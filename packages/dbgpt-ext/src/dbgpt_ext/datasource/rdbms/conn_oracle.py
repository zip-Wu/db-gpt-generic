"""Oracle connector using python-oracledb."""

import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Type

from sqlalchemy import text
from sqlalchemy.engine import URL

from dbgpt.core.awel.flow import (
    TAGS_ORDER_HIGH,
    ResourceCategory,
    auto_register_resource,
)
from dbgpt.datasource.rdbms.base import RDBMSConnector, RDBMSDatasourceParameters
from dbgpt.util.i18n_utils import _

_ORACLE_INIT_LOCK = threading.Lock()


def initialize_oracle_client():
    """Enable optional Thick mode before SQLAlchemy makes its first connection."""
    if os.getenv("ORACLE_THICK_MODE", "false").lower() != "true":
        return
    import oracledb

    with _ORACLE_INIT_LOCK:
        if oracledb.is_thin_mode():
            # Linux resolves Instant Client from ldconfig/LD_LIBRARY_PATH.
            oracledb.init_oracle_client()


@auto_register_resource(
    label=_("Oracle datasource"),
    category=ResourceCategory.DATABASE,
    tags={"order": TAGS_ORDER_HIGH},
    description=_(
        "Enterprise-grade relational database with oracledb driver (python-oracledb)."
    ),
)
@dataclass
class OracleParameters(RDBMSDatasourceParameters):
    """Oracle connection parameters."""

    __type__ = "oracle"

    driver: str = field(
        default="oracle+oracledb",  # ✅ 使用 python-oracledb 驱动
        metadata={
            "help": _("Driver name for Oracle, default is oracle+oracledb."),
        },
    )

    service_name: Optional[str] = field(
        default=None,
        metadata={
            "help": _("Oracle service name (alternative to SID)."),
        },
    )

    sid: Optional[str] = field(
        default=None,
        metadata={
            "help": _("Oracle SID (System ID, alternative to service name)."),
        },
    )

    def db_url(self, ssl: bool = False, charset: Optional[str] = None) -> str:
        if not self.service_name and not self.sid:
            raise ValueError("Either service_name or sid must be provided for Oracle.")
        return URL.create(
            self.driver,
            username=self.user,
            password=self.password,
            host=self.host,
            port=int(self.port),
            database=None if self.service_name else self.sid,
            query={"service_name": self.service_name} if self.service_name else {},
        ).render_as_string(hide_password=False)

    def create_connector(self) -> "OracleConnector":
        # 【根因修复】必须走 from_uri_db 路径以复用 CURRENT_SCHEMA(ORACLE_SCHEMA) 适配：
        # from_parameters 参数直连会反射到登录账号的默认 schema，
        # 业务表 owner 为其他 schema 时 get_table_names() 返回空 →
        # 概要向量 embedding 空转写入 0 条（db summary embedding success 但 chroma 0 向量）。
        return OracleConnector.from_uri_db(
            host=self.host,
            port=self.port,
            user=self.user,
            pwd=self.password,
            sid=self.sid,
            service_name=self.service_name,
        )


class OracleConnector(RDBMSConnector):
    db_type: str = "oracle"
    db_dialect: str = "oracle"
    driver: str = "oracle+oracledb"

    def get_usable_table_names(self) -> list:
        # 【根因修复】SQLAlchemy Oracle 反射枚举表走 user_tables（登录账号名下），
        # 业务表 owner 为其他 schema 时返回空（即使 CURRENT_SCHEMA 已切）。改为按
        # CURRENT_SCHEMA 查 all_tables，与 ALTER SESSION SET CURRENT_SCHEMA 配套，
        # 否则概要向量 embedding / 表结构注入都拿不到表清单。
        with self.session_scope() as session:
            rows = session.execute(
                text(
                    "SELECT table_name FROM all_tables "
                    "WHERE owner = sys_context('USERENV','CURRENT_SCHEMA') "
                    "AND table_name NOT LIKE '%$%' ORDER BY table_name"
                )
            ).fetchall()
            return [r[0] for r in rows]

    def get_table_names(self) -> list:
        return self.get_usable_table_names()

    def get_columns(self, table_name) -> List[Dict]:
        # 【适配说明】三件事：① DB-GPT 各流程(如 RdbmsSummary)常把表名转小写传递，
        # Oracle 字典按大写存储，统一转大写；② SQLAlchemy Oracle 反射的"默认 schema"
        # 取登录用户而非 CURRENT_SCHEMA，必须显式传 schema=业务 owner，
        # 否则 get_columns 查 all_tab_columns owner=登录用户 返回空 → NoSuchTableError。
        schema = os.environ.get("ORACLE_SCHEMA")
        kw = {"schema": schema} if schema else {}
        return self._inspector.get_columns(str(table_name).upper(), **kw)

    def get_indexes(self, table_name, db_name=None) -> List:
        # 【适配说明】与 get_columns 同因：显式 schema + 大写，RdbmsSummary 依赖。
        schema = os.environ.get("ORACLE_SCHEMA")
        kw = {"schema": schema} if schema else {}
        return self._inspector.get_indexes(str(table_name).upper(), **kw)

    @classmethod
    def from_uri(cls, database_uri, engine_args=None, **kwargs):
        initialize_oracle_client()
        return super().from_uri(database_uri, engine_args=engine_args, **kwargs)

    @classmethod
    def param_class(cls) -> Type[RDBMSDatasourceParameters]:
        return OracleParameters

    @classmethod
    def from_uri_db(
        cls,
        host: str,
        port: int,
        user: str,
        pwd: str,
        sid: Optional[str] = None,
        service_name: Optional[str] = None,
        engine_args: Optional[dict] = None,
        **kwargs,
    ) -> "OracleConnector":
        # 【适配说明】ORACLE_THICK_MODE=true 时先切厚客户端再建引擎：
        # 该路径（ORACLE_SCHEMA 适配走 from_uri_db）不经过 from_uri，须在此初始化。
        initialize_oracle_client()

        if not sid and not service_name:
            raise ValueError("Must provide either sid or service_name")

        db_url = URL.create(
            cls.driver,
            username=user,
            password=pwd,
            host=host,
            port=int(port),
            database=None if service_name else sid,
            query={"service_name": service_name} if service_name else {},
        )

        # 【补丁】登录账号默认 schema 可能不是业务 schema。设置环境变量 ORACLE_SCHEMA 时，连接后统一切
        # CURRENT_SCHEMA，让 SQLAlchemy 的 Oracle 方言以业务 schema 解析表清单/列，
        # 否则自动寻表（db summary）检索出来的是空表。
        schema = os.environ.get("ORACLE_SCHEMA")
        if schema:
            from sqlalchemy import create_engine, event

            engine = create_engine(db_url, **(engine_args or {}))
            if not isinstance(schema, str) or not schema.strip():
                schema = None
            if schema:
                @event.listens_for(engine, "connect")
                def _set_current_schema(dbapi_conn, _record):
                    cursor = dbapi_conn.cursor()
                    try:
                        cursor.execute(f'ALTER SESSION SET CURRENT_SCHEMA = "{schema.strip()}"')
                    finally:
                        cursor.close()

            return cls(engine, **kwargs)

        return cls.from_uri(db_url, engine_args=engine_args, **kwargs)

    def get_simple_fields(self, table_name):
        """Get column fields about specified table."""
        return self.get_fields(table_name)

    def get_fields(self, table_name: str, db_name=None) -> List[Tuple]:
        # 【适配说明】与 get_columns 同因：user_tab_columns/user_col_comments 只认
        # 登录账号默认 schema，业务表 owner 为其他 schema 时返回空。改用 all_* 视图 + owner 过滤。
        with self.session_scope() as session:
            query = f"""
                SELECT col.column_name,
                       col.data_type,
                       col.data_default,
                       col.nullable,
                       comm.comments
                FROM all_tab_columns col
                LEFT JOIN all_col_comments comm
                ON col.owner = comm.owner
                AND col.table_name = comm.table_name
                AND col.column_name = comm.column_name
                WHERE col.owner = sys_context('USERENV','CURRENT_SCHEMA')
                AND col.table_name = '{table_name.upper()}'
            """
            result = session.execute(text(query))
            return result.fetchall()

    def get_charset(self) -> str:
        with self.session_scope() as session:
            cursor = session.execute(
                text(
                    "SELECT VALUE FROM NLS_DATABASE_PARAMETERS "
                    "WHERE PARAMETER = 'NLS_CHARACTERSET'"
                )
            )
            return cursor.fetchone()[0]

    def get_grants(self):
        with self.session_scope() as session:
            cursor = session.execute(text("SELECT privilege FROM user_sys_privs"))
            return cursor.fetchall()

    def get_users(self) -> List[Tuple[str, None]]:
        with self.session_scope() as session:
            cursor = session.execute(text("SELECT username FROM all_users"))
            return [(row[0], None) for row in cursor.fetchall()]

    def get_database_names(self) -> List[str]:
        with self.session_scope() as session:
            if self._engine.dialect.server_version_info < (12,):
                return [
                    session.execute(
                        text("SELECT sys_context('USERENV', 'DB_NAME') FROM dual")
                    ).scalar()
                ]
            is_cdb = session.execute(text("SELECT CDB FROM V$DATABASE")).fetchone()[0]
            if is_cdb == "YES":
                pdbs = session.execute(
                    text("SELECT NAME FROM V$PDBS WHERE OPEN_MODE = 'READ WRITE'")
                ).fetchall()
                return [name[0] for name in pdbs]
            else:
                return [
                    session.execute(
                        text("SELECT sys_context('USERENV', 'CON_NAME') FROM dual")
                    ).fetchone()[0]
                ]

    def get_table_comments(self, db_name: str) -> List[Tuple[str, str]]:
        # user_tab_comments 只认登录账号默认 schema；业务表 owner 为其他 schema 时为空。
        with self.session_scope() as session:
            result = session.execute(
                text(
                    "SELECT table_name, comments FROM all_tab_comments "
                    "WHERE owner = sys_context('USERENV','CURRENT_SCHEMA')"
                )
            )
            return [(row[0], row[1]) for row in result.fetchall()]

    def get_table_comment(self, table_name: str) -> Dict:
        # 【适配说明】与 get_table_comments 同因，统一走 all_* + owner 过滤。
        with self.session_scope() as session:
            cursor = session.execute(
                text(
                    f"SELECT comments FROM all_tab_comments "
                    f"WHERE owner = sys_context('USERENV','CURRENT_SCHEMA') "
                    f"AND table_name = '{table_name.upper()}'"
                )
            )
            row = cursor.fetchone()
            return {"text": row[0] if row else ""}

    def get_column_comments(
        self, db_name: str, table_name: str
    ) -> List[Tuple[str, str]]:
        # 【适配说明】user_col_comments 同因，改用 all_col_comments + owner 过滤。
        with self.session_scope() as session:
            cursor = session.execute(
                text(f"""
                    SELECT column_name, comments
                    FROM all_col_comments
                    WHERE owner = sys_context('USERENV','CURRENT_SCHEMA')
                    AND table_name = '{table_name.upper()}'
                """)
            )
            return [(row[0], row[1]) for row in cursor.fetchall()]

    def get_collation(self) -> str:
        with self.session_scope() as session:
            cursor = session.execute(
                text(
                    "SELECT value FROM NLS_DATABASE_PARAMETERS "
                    "WHERE parameter = 'NLS_SORT'"
                )
            )
            return cursor.fetchone()[0]
