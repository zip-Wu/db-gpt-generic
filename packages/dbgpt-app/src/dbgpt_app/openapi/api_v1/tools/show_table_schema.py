"""show_table_schema tool — dialect-agnostic table structure lookup.

背景：提示词与报错指引曾把模型指到 Oracle 字典视图（all_tables /
all_tab_columns 等）。这些视图只有 Oracle（及 ORA 兼容语义）才有，
PostgreSQL/MySQL 上查询必然失败（relation does not exist），模型拿不到
真实结构就开始幻觉表名/列名，反复试错烧光步数（2026-10-14 同事海关
PG 库实测复现：模型查 all_tab_columns 失败后写出不存在的表名）。

本工具走 connector.get_columns（任何关系库方言都可用），并在表名不存在
时回带库里真实存在的表名清单，从根上打断"猜表名→失败→再猜"循环。
"""

import asyncio
import json
import re
from typing import Any, Dict, Optional

from dbgpt.agent.resource.tool.base import tool

# 单次返回最多列出的列数（宽表保护；超出部分截断并注明）
_MAX_COLS_SHOWN = 60


def make_show_table_schema(react_state: Dict[str, Any], database_connector: Optional[Any]):
    @tool(
        description=(
            "查看指定表的完整结构（列名、类型、注释）。需要确认表名或字段名时一律用本工具，"
            "不要查 all_tab_columns 等 Oracle 字典视图（PostgreSQL/MySQL 等库没有这些视图）。"
            '参数: {"table_name": "表名"}。'
        )
    )
    async def show_table_schema(table_name: str) -> str:
        """Return the real columns (name/type/comment) of one table."""

        def _blocked(message: str) -> str:
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": message}]},
                ensure_ascii=False,
            )

        def _mark_call(key: str) -> int:
            counts = react_state.setdefault("show_table_schema_calls", {})
            n = int(counts.get(key, 0)) + 1
            counts[key] = n
            return n

        if database_connector is None:
            return _blocked("未选择数据库，请先在左侧面板选择一个数据源。")

        raw = (table_name or "").strip().strip('"').strip("`").strip("'").strip()
        if not raw:
            return _blocked(
                '参数 table_name 不能为空，请传入要查看的表名（如 "fact_decl_header"）。'
            )
        if not re.fullmatch(r"[\w.$#]+", raw):
            return _blocked(
                f"表名不合法：{raw!r}。请只传单个表名（字母/数字/下划线/点），"
                "不要传 SQL 片段。"
            )

        # 支持 schema 限定名（如 customs_ecom.fact_decl_header）：取最后的表名段，
        # 交给连接器按其配置的默认 schema 解析。
        bare = raw.rsplit(".", 1)[-1]

        # 同一表名的结构查询每次问答最多 2 次（与维度指纹闸门口径一致），
        # 防止模型反复"再看一眼表结构"烧步数。
        key = bare.lower()
        n = _mark_call(key)
        if n > 2:
            return _blocked(
                f"表 {bare} 的结构本次问答已返回过 2 次，不再重复查询。"
                "请直接使用已获得的列名信息继续作答。"
            )

        try:
            cols = await asyncio.to_thread(database_connector.get_columns, bare)
        except Exception:
            cols = []

        if not cols:
            try:
                tables = sorted(
                    str(t) for t in await asyncio.to_thread(database_connector.get_table_names)
                )
            except Exception:
                tables = []
            shown = "、".join(tables[:40]) + ("…" if len(tables) > 40 else "")
            return _blocked(
                f"表 {bare} 不存在或没有列信息。请从下面这些真实存在的表里核对表名，"
                f"不要凭空猜表名：{shown or '（表清单读取失败，请检查数据源配置）'}"
            )

        lines = [f"表 {bare} 共 {len(cols)} 列：", ""]
        for c in cols[:_MAX_COLS_SHOWN]:
            cname = str(c.get("name", "") or "")
            ctype = str(c.get("type", "") or "")
            ccom = str(c.get("comment", "") or "").strip().replace("|", "/")
            if len(ctype) > 40:
                ctype = ctype[:37] + "..."
            lines.append(f"- {cname}（{ctype}）: {ccom}" if ccom else f"- {cname}（{ctype}）")
        if len(cols) > _MAX_COLS_SHOWN:
            lines.append(f"…（其余 {len(cols) - _MAX_COLS_SHOWN} 列略，需要时在 SQL 里只选相关字段）")
        lines.append("")
        lines.append(
            "写 SQL 只允许使用上面真实存在的列名；注释里写明的口径"
            "（如「统计金额一律用本字段」「统计须排除某状态」）必须遵守。"
        )
        return json.dumps(
            {"chunks": [{"output_type": "markdown", "content": "\n".join(lines)}]},
            ensure_ascii=False,
        )

    return show_table_schema
