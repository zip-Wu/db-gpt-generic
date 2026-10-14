"""Tests for dialect-aware schema lookup (2026-10-14).

Covers:
- sql_query's dialect gate: Oracle dictionary views (all_tab_columns etc.)
  are deterministically blocked on non-Oracle databases instead of failing
  at the database and burning agent rounds.
- sql_query's invalid-column rescue now uses connector.get_columns so it
  works on every dialect (old path queried all_tab_columns, Oracle-only).
- the new show_table_schema tool: real columns, unknown-table recovery
  (returns the real table list), repeat cap, malformed names.
"""

import json

import pytest

from dbgpt_app.openapi.api_v1.tools.show_table_schema import make_show_table_schema
from dbgpt_app.openapi.api_v1.tools.sql_query import (
    connector_db_type,
    make_sql_query,
    ora_dict_view_in,
    schema_lookup_hint,
)


def _chunks(result):
    return json.loads(result)["chunks"]


def _text(result):
    return "\n".join(
        c["content"] for c in _chunks(result) if isinstance(c.get("content"), str)
    )


class FakeConnector:
    """Minimal connector double: db_type + metadata, never touches a DB."""

    def __init__(self, db_type="postgresql", columns=None, tables=None, run_error=None):
        self.db_type = db_type
        self._columns = columns or []
        self._tables = tables or []
        self._run_error = run_error
        self.run_calls = []

    def get_columns(self, table_name):
        # behaves like real connectors: unknown table -> empty (or raise)
        if self._columns and table_name.lower() in (
            str(c.get("name", "")).lower() for c in self._columns
        ):
            return self._columns
        if table_name.lower() == getattr(self, "_known_table", "").lower():
            return self._columns
        return []

    def get_table_names(self):
        return list(self._tables)

    def run(self, sql):
        self.run_calls.append(sql)
        if self._run_error:
            raise Exception(self._run_error)
        return [["ok"], [1]]


# ---------------------------------------------------------------------------
# helpers: view detection, dialect resolution, hint generation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("SELECT column_name FROM all_tab_columns WHERE table_name='t'", "all_tab_columns"),
        ("SELECT table_name FROM all_tables", "all_tables"),
        ("SELECT comments FROM all_col_comments", "all_col_comments"),
        ("SELECT column_name FROM user_tab_columns", "user_tab_columns"),
        ("SELECT * from ALL_TABLES", "ALL_TABLES"),
        ("SELECT user_id, all_modified_at FROM t", None),
        ("SELECT count(*) FROM users", None),
        ("SELECT column_name FROM information_schema.columns", None),
    ],
)
def test_ora_dict_view_detection(sql, expected):
    assert ora_dict_view_in(sql) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("postgresql", "postgresql"), ("PostgreSQL", "postgresql")],
)
def test_connector_db_type_normalizes(raw, expected):
    class C:
        db_type = raw

    assert connector_db_type(C()) == expected


def test_connector_db_type_accepts_enum_like_value():
    class DbType:
        def value(self):
            return "oracle"

    class C:
        db_type = DbType()

    assert connector_db_type(C()) == "oracle"


def test_schema_lookup_hint_by_dialect():
    pg_hint = schema_lookup_hint("postgresql")
    assert "show_table_schema" in pg_hint
    assert "information_schema.columns" in pg_hint
    # the hint must not send the model back into the Oracle dictionary
    assert "没有 all_tab_columns" in pg_hint

    oracle_hint = schema_lookup_hint("oracle")
    assert "show_table_schema" in oracle_hint
    assert "all_tab_columns" in oracle_hint  # legitimate on Oracle

    unknown_hint = schema_lookup_hint("")
    assert "show_table_schema" in unknown_hint
    assert "all_tab_columns" not in unknown_hint


# ---------------------------------------------------------------------------
# sql_query: dialect gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sql_query_blocks_ora_dict_view_on_postgresql():
    conn = FakeConnector("postgresql")
    state = {}
    tool = make_sql_query(state, conn)

    result = await tool(sql="SELECT column_name FROM all_tab_columns")
    text = _text(result)

    assert "已直接拦截" in text
    assert "show_table_schema" in text
    assert "information_schema.columns" in text
    # the query never reached the database
    assert conn.run_calls == []
    # and the failure is counted for the breaker
    assert state["sql_query_fail_streak"] == 1


@pytest.mark.asyncio
async def test_sql_query_blocks_user_dict_view_on_mysql():
    conn = FakeConnector("mysql")
    tool = make_sql_query({}, conn)

    text = _text(await tool(sql="select column_name from USER_TAB_COLUMNS where table_name='t'"))

    assert "已直接拦截" in text
    assert conn.run_calls == []


@pytest.mark.asyncio
async def test_sql_query_does_not_block_ora_dict_view_on_oracle():
    conn = FakeConnector("oracle", run_error=None)
    tool = make_sql_query({}, conn)

    text = _text(await tool(sql="SELECT column_name FROM all_tab_columns WHERE table_name='T1'"))

    assert "已直接拦截" not in text
    assert len(conn.run_calls) == 1  # executed (ROWNUM-wrapped or not)


@pytest.mark.asyncio
async def test_sql_query_does_not_block_normal_sql_on_postgresql():
    conn = FakeConnector("postgresql")
    tool = make_sql_query({}, conn)

    text = _text(await tool(sql="SELECT count(*) FROM fact_decl_header"))

    assert "已直接拦截" not in text
    assert len(conn.run_calls) == 1


# ---------------------------------------------------------------------------
# sql_query: invalid-column rescue via get_columns (all dialects)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sql_query_invalid_column_on_pg_returns_real_columns():
    cols = [
        {"name": "decl_no", "type": "character", "comment": "清单编号"},
        {"name": "decl_date", "type": "date", "comment": "申报日期"},
    ]
    conn = FakeConnector("postgresql", columns=cols, run_error='column "wrong_col" does not exist')
    conn._known_table = "fact_decl_header"
    tool = make_sql_query({}, conn)

    text = _text(await tool(sql="SELECT wrong_col FROM fact_decl_header"))

    assert "【列名纠偏】" in text
    assert "decl_no" in text
    assert "申报日期" in text
    assert "show_table_schema" in text


@pytest.mark.asyncio
async def test_sql_query_unknown_table_on_pg_says_table_name_is_wrong():
    conn = FakeConnector("postgresql", run_error='relation "fact_e_decl_header" does not exist')
    conn._tables = ["fact_decl_header", "dim_enterprise"]
    tool = make_sql_query({}, conn)

    text = _text(await tool(sql="SELECT decl_no FROM fact_e_decl_header"))

    assert "【列名纠偏】" in text
    assert "表名很可能写错" in text
    assert "fact_decl_header" in text


# ---------------------------------------------------------------------------
# show_table_schema tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_show_table_schema_returns_columns_with_comments():
    cols = [
        {"name": "decl_no", "type": "character", "comment": "清单编号(前2位57为拱北关区)"},
        {"name": "total_value_rmb", "type": "numeric", "comment": "统计金额一律用本字段"},
    ]
    conn = FakeConnector("postgresql", columns=cols, tables=["fact_decl_header"])
    conn._known_table = "fact_decl_header"
    tool = make_show_table_schema({}, conn)

    result = await tool(table_name="fact_decl_header")
    chunk = _chunks(result)[0]

    assert chunk["output_type"] == "markdown"
    content = chunk["content"]
    assert "decl_no" in content
    assert "清单编号" in content
    assert "total_value_rmb" in content
    assert "统计金额一律用本字段" in content
    assert "真实存在的列名" in content


@pytest.mark.asyncio
async def test_show_table_schema_unknown_table_lists_real_tables():
    conn = FakeConnector("postgresql", tables=["fact_decl_header", "dim_enterprise"])
    tool = make_show_table_schema({}, conn)

    text = _text(await tool(table_name="fact_e_decl_header"))

    assert "不存在" in text
    assert "fact_decl_header" in text
    assert "dim_enterprise" in text
    assert "不要凭空猜表名" in text


@pytest.mark.asyncio
async def test_show_table_schema_accepts_schema_qualified_name():
    cols = [{"name": "id", "type": "bigint", "comment": ""}]
    conn = FakeConnector("postgresql", columns=cols, tables=["t1"])
    conn._known_table = "t1"
    tool = make_show_table_schema({}, conn)

    text = _text(await tool(table_name="customs_ecom.t1"))

    assert "id" in text
    assert "不存在" not in text


@pytest.mark.asyncio
async def test_show_table_schema_repeat_is_capped_at_two():
    cols = [{"name": "id", "type": "bigint", "comment": ""}]
    conn = FakeConnector("postgresql", columns=cols, tables=["t1"])
    conn._known_table = "t1"
    state = {}
    tool = make_show_table_schema(state, conn)

    assert "不存在" not in _text(await tool(table_name="t1"))
    assert "不存在" not in _text(await tool(table_name="t1"))
    third = _text(await tool(table_name="t1"))
    assert "不再重复查询" in third


@pytest.mark.asyncio
async def test_show_table_schema_rejects_malformed_names():
    conn = FakeConnector("postgresql", tables=["t1"])
    tool = make_show_table_schema({}, conn)

    for bad in ("", "t1; DROP TABLE t2", "t1 t2"):
        text = _text(await tool(table_name=bad))
        assert "不合法" in text or "不能为空" in text
    assert conn.run_calls == []


@pytest.mark.asyncio
async def test_show_table_schema_without_connector():
    tool = make_show_table_schema({}, None)

    text = _text(await tool(table_name="t1"))

    assert "未选择数据库" in text


# ---------------------------------------------------------------------------
# PostgreSQL connector: configured schema must reach get_columns and search_path
# ---------------------------------------------------------------------------


def test_pg_parameters_engine_args_pins_search_path():
    from dbgpt_ext.datasource.rdbms.conn_postgresql import PostgreSQLParameters

    p = PostgreSQLParameters(
        host="h", port=9790, user="u", password="p",
        database="testdb", schema="customs_ecom",
    )
    args = p.engine_args()
    assert args["connect_args"]["options"] == "-c search_path=customs_ecom"
    # pool settings from the base class survive
    assert args["pool_size"] == 5

    p2 = PostgreSQLParameters(host="h", port=9790, user="u", password="p", database="d")
    assert "connect_args" not in (p2.engine_args() or {})


def test_pg_connector_get_columns_uses_configured_schema():
    from dbgpt_ext.datasource.rdbms.conn_postgresql import PostgreSQLConnector

    class FakeInspector:
        def __init__(self):
            self.calls = []

        def get_columns(self, table, schema=None):
            self.calls.append((table, schema))
            return [{"name": "id", "comment": ""}]

    conn = object.__new__(PostgreSQLConnector)
    conn._schema = "customs_ecom"
    conn._inspector = FakeInspector()

    cols = conn.get_columns("dim_enterprise")

    assert cols == [{"name": "id", "comment": ""}]
    assert conn._inspector.calls == [("dim_enterprise", "customs_ecom")]


def test_connector_manager_derives_engine_args_from_param_class():
    from dbgpt_ext.datasource.rdbms.conn_postgresql import (
        PostgreSQLConnector,
        PostgreSQLParameters,
    )
    from dbgpt_serve.datasource.manages.connector_manager import ConnectorManager

    args = ConnectorManager._derive_engine_args(
        PostgreSQLConnector, "119.91.254.205", 9790, "u", "p", "testdb", "customs_ecom"
    )
    assert args["connect_args"]["options"] == "-c search_path=customs_ecom"

    assert (
        ConnectorManager._derive_engine_args(
            PostgreSQLConnector, "h", 5432, "u", "p", "d", None
        )
        is None
    )

    class NoParamConnector:
        db_type = "weird"

        @classmethod
        def param_class(cls):
            class Broken:
                @classmethod
                def from_dict(cls, d, ignore_extra_fields=False):
                    raise RuntimeError("boom")

            return Broken

    assert (
        ConnectorManager._derive_engine_args(
            NoParamConnector, "h", 5432, "u", "p", "d", "s"
        )
        is None
    )
    # the real param class used above must be the PG one
    assert PostgreSQLConnector.param_class() is PostgreSQLParameters
