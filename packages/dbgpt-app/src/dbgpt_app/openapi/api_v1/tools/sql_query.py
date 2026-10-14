"""sql_query tool — read-only SQL query against the selected database."""

import asyncio
import datetime
import json
import re
from typing import Any, Dict, Optional

from dbgpt.agent.resource.tool.base import tool


# ── 维度指纹闸门（2026-09-16 新增）──────────────────────────────────
# 背景：报告类任务中模型会在【已统计过的维度之间交替重查】（实测出现
# "按年份分布" ↔ "按类别分布" 来回十几次），因为每条 SQL 的文本不同（尤其模型
# 生成的语句常被截断或微调），上面那道"完全相同 SQL 只执行一次"的闸门永远不命中
# → 实测把 50 轮全部耗尽、用户什么也拿不到。这里按 (主表, GROUP BY 维度键) 生成
# "维度指纹"，同一指纹超过 _DIM_QUERY_LIMIT 次即拦截，直接打断交替循环。
# 只对含 GROUP BY 的聚合查询生效——那才是"按某个维度做分布统计"。
_DIM_QUERY_LIMIT = 2


def _split_top_level(text: str):
    """按顶层逗号切分，括号内的逗号不切（TO_CHAR(x,'YYYY') 算一项）。"""
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [x.strip() for x in parts if x.strip()]


def _dimension_fingerprint(sql_text: str) -> Optional[str]:
    """维度指纹 = "<主表>|<归一化并排序后的 GROUP BY 键集合>"；非聚合查询返回 None。

    归一化目的：让"同一维度的不同写法"（列顺序不同、带表别名 t.col1、
    大小写与空格差异）落到同一个指纹上，从而识别出重复统计。
    """
    # 先去一层括号，避免 EXTRACT(YEAR FROM AAE044) 里的 FROM 被误当成主表
    flat = re.sub(r"\([^()]*\)", " ", sql_text)
    m_tbl = re.search(r"\bFROM\s+([A-Za-z_][\w$#.]*)", flat, re.I)
    if not m_tbl:
        return None
    table = m_tbl.group(1).split(".")[-1].upper()

    m_grp = re.search(
        r"\bGROUP\s+BY\s+(.+?)(?:\bORDER\s+BY\b|\bHAVING\b|\bFETCH\b|\bOFFSET\b|$)",
        sql_text,
        re.I | re.S,
    )
    if not m_grp:
        return None

    keys = []
    for part in _split_top_level(m_grp.group(1)):
        part = re.sub(r"\b\w+\.(\w+)", r"\1", part)      # 去掉表别名前缀
        part = re.sub(r"\s+", " ", part).strip().lower()      # 归一化空白与大小写
        if part:
            keys.append(part)
    if not keys:
        return None
    return "%s|%s" % (table, ",".join(sorted(keys)))




# ── 口径自动回述 ────────────────────────────────────────────────────────────
# 背景：实测子任务返回里【数值正确、文字口径却漏写了过滤条件】，后续叙述被带偏。
# 让模型"自己总结口径"不可靠，这里改成由工具**直接从 SQL 提取**过滤条件并附在
# 结果末尾，模型照抄即可。
_TOP_CLAUSE_KEYWORDS = ("WHERE", "GROUP BY", "ORDER BY", "HAVING", "UNION", "CONNECT BY")


def _top_level_clauses(sql: str) -> Dict[str, str]:
    """取出括号深度 0、且不在字符串字面量里的 WHERE / GROUP BY / HAVING 原文。"""
    out: Dict[str, str] = {}
    depth = 0
    in_str = False
    cur = None
    start = 0
    i = 0
    n = len(sql)
    up = sql.upper()
    while i < n:
        ch = sql[i]
        if in_str:
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0:
            hit = None
            for kw in _TOP_CLAUSE_KEYWORDS:
                if up.startswith(kw, i):
                    before_ok = i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] in "_.")
                    after = i + len(kw)
                    after_ok = after >= n or not (
                        sql[after].isalnum() or sql[after] in "_."
                    )
                    if before_ok and after_ok:
                        hit = kw
                        break
            if hit:
                if cur:
                    out[cur] = sql[start:i].strip()
                cur = hit
                i += len(hit)
                start = i
                continue
        i += 1
    if cur:
        out[cur] = sql[start:].strip()
    return out


def _squash(text: str, limit: int = 400) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[:limit] + " …"


def _query_stamp() -> str:
    """本次取数的时刻（北京时间）。

    背景：业务库是**实时写入**的 —— 同一个统计问题在不同时刻返回的数字可能不同。
    答复里没有任何时间锚点，用户会以为"模型不稳定"。故把取数时刻一并回述：
    数字变化时能自证是数据在动，而不是模型在变。

    ⚠️ 容器时区通常是 UTC，所以先按 UTC 取当前时刻再 +8 小时显式换算成北京时间，
    不依赖容器 TZ 配置。
    """
    beijing = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    return beijing.strftime("%Y-%m-%d %H:%M")


def _extract_count_distinct(sql: str) -> list:
    """提取所有 COUNT(DISTINCT <expr>) 里的 <expr>（expr 可含嵌套括号）。

    为什么不能用旧正则 `count\\s*\\(\\s*distinct\\s+([^)]+?)\\s*\\)`：
    嵌套写法如 `COUNT(DISTINCT UPPER(TRIM(col)))` 的 expr 里带两层括号，
    `[^)]+?` 会在第一个 `)` 处截断，口径回述会变成残句。
    这里按括号配对扫描，平铺写法（COUNT(DISTINCT col)）行为不变。
    """
    out = []
    for m in re.finditer(r"count\s*\(", sql or "", re.IGNORECASE):
        i = m.end()
        m2 = re.match(r"\s*distinct\s+", sql[i:], re.IGNORECASE)
        if not m2:
            continue
        j = i + m2.end()
        depth = 1  # 已在 count( 里面
        start = j
        while j < len(sql) and depth:
            ch = sql[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(sql[start:j].strip())
    return out


def _describe_sql(sql: str) -> str:
    """从 SQL 里提取「这次到底按什么口径查的」，附在结果末尾供模型照抄。"""
    if not sql:
        return ""
    clauses = _top_level_clauses(sql)
    tables = re.findall(r"\bFROM\s+((?:[\w\"]+\.)?[\w\"]+)", sql, re.IGNORECASE)
    joins = re.findall(r"\bJOIN\s+((?:[\w\"]+\.)?[\w\"]+)", sql, re.IGNORECASE)
    where = _squash(clauses.get("WHERE", ""))
    group = _squash(clauses.get("GROUP BY", ""))
    having = _squash(clauses.get("HAVING", ""))
    dist = _extract_count_distinct(sql)
    if dist:
        stat = "COUNT(DISTINCT %s) —— 按该字段去重计人数" % "、".join(
            d.strip() for d in dist[:3]
        )
    elif re.search(r"count\s*\(\s*\*\s*\)", sql, re.IGNORECASE):
        stat = "COUNT(*) —— 记录条数口径（未去重）"
    else:
        stat = "无聚合（返回明细行）"
    src = "、".join(dict.fromkeys([t.strip() for t in tables + joins])) or "未识别"
    lines = [
        "",
        "【口径自动回述·工具直接从 SQL 提取。答复末尾的口径说明必须逐字包含下列每一条，"
        "不得省略、改写或另行总结】",
        "- 数据来源（FROM/JOIN）：" + src,
        "- 过滤条件（WHERE）：" + (where or "无"),
    ]
    if group:
        lines.append("- 分组维度（GROUP BY）：" + group)
    if having:
        lines.append("- 分组后过滤（HAVING）：" + having)
    lines.append("- 统计方式：" + stat)
    lines.append("- 数据查询时刻：" + _query_stamp() + "（北京时间）")
    return "\n".join(lines)


def make_sql_query(react_state: Dict[str, Any], database_connector: Optional[Any]):
    @tool(
        description=(
            "对用户选择的数据库执行 SQL 查询（仅支持 SELECT）。"
            '参数: {"sql": "SELECT 语句"}。'
            "工具最多返回 50 行数据，大数据量请用 GROUP BY/COUNT 等聚合在 SQL 内汇总，"
            "不要分页拉取全量明细。"
            "单条 SQL 最多选择 30 个字段、2000 字符：只 SELECT 回答问题所需的关键字段"
            "（建议 ≤10 列），禁止 SELECT * 或列出整表全部列。"
        )
    )
    async def sql_query(sql: str) -> str:
        """Execute a read-only SQL query against the selected database."""

        # ── 熔断计数（方案 C）：同一轮问答内 sql_query 连续失败过多时，
        # 阻止模型继续盲目重试烧步数。react_state 单请求内跨调用共享。──
        def _mark_fail() -> int:
            streak = int(react_state.get("sql_query_fail_streak", 0)) + 1
            react_state["sql_query_fail_streak"] = streak
            return streak

        def _mark_ok() -> None:
            react_state["sql_query_fail_streak"] = 0


        async def _run_sql(stmt: str):
            """在独立线程里执行同步驱动，避免长查询阻塞整个事件循环。
        
            框架 ToolPack 对同步工具是直接在事件循环里调的（core 里还留着
            「TODO: Execute in a separate executor」），单进程部署下一条慢查询会把
            所有并发用户的请求一起卡住；丢线程池后默认上限 = CPU+4，天然限流。
            """
            return await asyncio.to_thread(database_connector.run, stmt)

        def _breaker_suffix() -> str:
            if int(react_state.get("sql_query_fail_streak", 0)) >= 3:
                return (
                    "\n\n【查询策略调整】sql_query 已连续失败 3 次，说明当前写法有问题，"
                    "请换一种查询策略：① 先执行 SELECT column_name FROM all_tab_columns "
                    "WHERE table_name='表名' 拿到真实列名（或参考上方【列名纠偏】清单）；"
                    "② 用 COUNT(*) / GROUP BY 探数，确认数据规模；"
                    "③ 用 WHERE 加精确过滤（如姓名/编号/区划代码）缩小范围，只查少量字段。"
                    "换策略后可以继续查询——重点是先拿到正确的列名，不要凭空猜列名。"
                )
            return ""

        # ── 重复查询拦截 + 元数据探测上限（方案 D，2026-09-15 新增）──
        # 背景：模型遇到"库里根本没有的信息"（如编码含义无字典表）时，会反复
        # "换个表再找一遍"，实测把 50 轮全部耗尽、最后什么都没答出来（用户只看到
        # 被截断的半截 SQL）。规则约束在长循环里会被模型遗忘，这里加两道硬闸门：
        # ① 完全相同的 SQL 只执行一次；② 表结构/列注释探测超过上限即拦截。
        _META_LIMIT = 5
        _normalized = " ".join(sql.split()).lower()

        def _blocked(message: str) -> str:
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": message}]},
                ensure_ascii=False,
            )

        _seen = react_state.setdefault("sql_query_seen", set())
        if _normalized in _seen:
            return _blocked(
                "【重复查询已拦截】这条 SQL 本次问答中已经执行过，结果与上次完全相同，"
                "不再重复执行。请立刻停止试探：已拿到所需数据就直接汇总作答；"
                "若确认库中无法取得（例如缺少编码对照表），请说明缺口并结束本轮，"
                "不要再用同类查询反复尝试。"
            )

        if re.search(r"\ball_(?:tab|col)_(?:columns|comments)\b", sql, re.IGNORECASE):
            _probe = int(react_state.get("sql_query_meta_probe", 0)) + 1
            react_state["sql_query_meta_probe"] = _probe
            if _probe > _META_LIMIT:
                return _blocked(
                    "【元数据探测已超限】本次问答查询表结构/列注释已达 "
                    f"{_META_LIMIT} 次上限。本库的列注释只写字段名（如“学历”），"
                    "不含编码取值含义，继续查表结构不会得到新信息。请立刻改用已有"
                    "信息作答：编码含义无法确定时，直接以编码形式给出统计结果"
                    "（例：<列名>='<码>' 共 N 条），并注明「编码含义待业务方确认」。"
                )

        if database_connector is None:
            _mark_fail()
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": "未选择数据库，请先在左侧面板选择一个数据源。"
                            + _breaker_suffix(),
                        }
                    ]
                },
                ensure_ascii=False,
            )

        sql_stripped = sql.strip().rstrip(";")
        # 【残缺参数拦截】模型生成超长工具调用（如欲一次 SELECT
        # 数百列）时，参数在生成/传输过程被截断，sql 可能带 JSON 外壳
        # （{"sql": "SELECT ...}）且缺 FROM/缺闭合引号。若直接执行会报
        # ORA-00972/ORA-01740 等垃圾错误并把模型带进"再试一次"死循环。
        # 先尝试还原 JSON 外壳，还原失败或语句不完整则拦截并给出正确指引。
        if sql_stripped.lstrip().startswith("{"):
            try:
                _shell = json.loads(sql_stripped)
                if isinstance(_shell, dict) and _shell.get("sql"):
                    sql_stripped = str(_shell["sql"]).strip().rstrip(";")
            except Exception:
                pass  # 截断的 JSON 无法解析，保留原文交给下方完整性校验
        # 【超长SQL自动裁剪】模型常把"对象画像"类问题理解为 SELECT
        # 全列（宽表 100+ 列），且即使提示字段限制，模型仍会抄
        # 整表列名 → 超长 SQL 反复失败 → "简化→又全列"死循环直到步数耗尽。
        # 只提示限制不解决问题：模型管不住自己。改为【工具层自动裁剪】——
        # 超长 SELECT 直接由工具裁到前 _MAX_AUTO_COLS 列再执行，不让模型重写。
        # 简单 SELECT（无 GROUP BY/ORDER BY/子查询/聚合）才裁剪，避免改坏语义。
        try:
            import re as _re

            _sel_m = _re.search(
                r"\bSELECT\b(.*?)\bFROM\b", sql_stripped, _re.IGNORECASE | _re.DOTALL
            )
            _is_select = sql_stripped.lstrip().upper().startswith("SELECT")
            if not _is_select or _sel_m is None:
                # 语句不完整（缺 FROM / 非 SELECT 开头 / 被截断）：不执行，
                # 直接给模型正确列名获取路径，避免"残缺 SQL→Oracle 报错→再试"死循环。
                _mark_fail()
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    "SQL 不完整或无法解析（缺少完整的 SELECT...FROM 结构），"
                                    "已拦截不执行。请重新生成规范的 SELECT 语句："
                                    "只选择回答问题所需的少量字段（建议 ≤10 列），"
                                    "并确保含完整 FROM 表名与 WHERE 条件，不要列出整表全部列。"
                                    "不确定字段名时，先执行："
                                    "SELECT column_name FROM all_tab_columns "
                                    "WHERE table_name='<表名>' 查看该表真实字段，"
                                    "再针对性查询。"
                                    + _breaker_suffix()
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            # 【维度指纹闸门】同一 (主表, GROUP BY 列集合) 的统计维度最多执行
            # _DIM_QUERY_LIMIT 次；超出即拦截，打断"交替重查已查过维度"的循环。
            _dim_fp = _dimension_fingerprint(sql_stripped)
            if _dim_fp is not None:
                _dim_counts = react_state.setdefault("sql_query_dim_seen", {})
                _dim_counts[_dim_fp] = int(_dim_counts.get(_dim_fp, 0)) + 1
                if _dim_counts[_dim_fp] > _DIM_QUERY_LIMIT:
                    _dim_tbl, _, _dim_cols = _dim_fp.partition("|")
                    return _blocked(
                        "【同一维度重复统计已拦截】本次问答中已经统计过「表 %s 按 %s "
                        "分组」这个维度，结果与上次完全相同，不再重复执行。请立刻停止"
                        "收集更多维度：直接使用已经获得的数据渲染报告并结束本轮。"
                        % (_dim_tbl, _dim_cols)
                    )

            _col_count = _sel_m.group(1).count(",") + 1
            _MAX_AUTO_COLS = 12
            _untrimmed_upper = sql_stripped.upper()
            _too_long = len(sql_stripped) > 2000 or _col_count > 30
            _has_complex = any(
                kw in _untrimmed_upper
                for kw in (" GROUP ", " ORDER ", " OVER (", " UNION ", " DISTINCT ")
            ) or _untrimmed_upper.count("SELECT") > 1
            if _too_long:
                if _has_complex:
                    # 复杂查询不裁剪（改坏语义风险高），直接提示让模型重写
                    _mark_fail()
                    return json.dumps(
                        {
                            "chunks": [
                                {
                                    "output_type": "text",
                                    "content": (
                                        "SQL 过长：当前语句超过工具限制"
                                        "（单条 SQL 最多 30 个字段、2000 字符）。"
                                        "是【输入 SQL 本身超出长度限制】，不是数据库结果被截断——"
                                        "请务必先加 WHERE 精确过滤（如姓名/编号/区划代码）缩小范围，"
                                        "并且只 SELECT 回答问题所需的少量关键字段（建议 ≤10 列），"
                                        "不要列出整表全部列，也不要 SELECT *。"
                                        "不确定字段名时，可先执行："
                                        "SELECT column_name FROM all_tab_columns "
                                        "WHERE table_name='<表名>' 查看后再选字段。"
                                        + _breaker_suffix(),
                                    ),
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                # 简单 SELECT：自动裁剪到前 _MAX_AUTO_COLS 列，继续执行
                _col_text = _sel_m.group(1)
                _col_parts = [c.strip() for c in _col_text.split(",") if c.strip()]
                _keep = ", ".join(_col_parts[:_MAX_AUTO_COLS])
                sql_stripped = (
                    sql_stripped[:_sel_m.start(1)]
                    + _keep
                    + sql_stripped[_sel_m.end(1):]
                )
                sql_stripped = (
                    "/* 自动精简：原 SQL 字段过多，已裁剪至前 "
                    + str(_MAX_AUTO_COLS)
                    + " 列 */\n"
                    + sql_stripped
                )
        except Exception:
            pass

        sql_upper = sql_stripped.upper().lstrip()
        forbidden = [
            "INSERT",
            "UPDATE",
            "DELETE",
            "DROP",
            "ALTER",
            "TRUNCATE",
            "CREATE",
            "GRANT",
            "REVOKE",
        ]
        for kw in forbidden:
            if sql_upper.startswith(kw):
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"安全限制: 不允许执行 {kw} 语句，"
                                "仅支持 SELECT 查询。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        try:
            sql_stripped = sql_stripped or ""
            # 口径回述要用未经 ROWNUM 包裹的原句（下方会改写 sql_stripped）
            _sql_for_clause = sql_stripped
            # TODO(business): 可在此挂"SQL 口径闸门"——按你的业务规则对 SQL 做
            # 静态核对（如维度码是否在字典里、状态过滤是否符合口径），提示随结果
            # 回给模型。上游开发版实现了两个例子：区划码校验、特定表口径兜底
            # （策略：只提醒、不改写 SQL，误报不阻断查询本身）。
            # 11g 兜底：FETCH FIRST → ROWNUM 改写
            # 注意：re 已在模块顶部导入，此处不可再 import——函数内出现 import re
            # 会让 re 变成整个函数的局部变量，导致函数前段的 re.search 抛
            # UnboundLocalError（2026-09-15 实测踩到）。
            m = re.search(
                r"\s+FETCH\s+FIRST\s+(\d+)\s+ROWS\s+ONLY\s*$", sql_stripped, re.IGNORECASE
            )
            if m:
                sql_stripped = (
                    f"SELECT * FROM ({sql_stripped[: m.start()].strip()}) "
                    f"WHERE ROWNUM <= {m.group(1)}"
                )
            # Oracle 且未自带行数限制时，工具层加 ROWNUM <= 50 上限，
            # 避免模型拉全量明细后死循环分页。
            if (
                getattr(database_connector, "db_type", "") == "oracle"
                and "rownum" not in sql_stripped.lower()
                and "limit" not in sql_stripped.lower()
            ):
                sql_stripped = f"SELECT * FROM ({sql_stripped}) WHERE ROWNUM <= 50"

            result = await _run_sql(sql_stripped)
            if not result:
                return json.dumps(
                    {
                        "chunks": [
                            {"output_type": "text", "content": "查询返回空结果。"}
                        ]
                    },
                    ensure_ascii=False,
                )

            columns = result[0]
            col_names = [str(c[0]) if isinstance(c, tuple) else str(c) for c in columns]
            rows = result[1:]

            header = "| " + " | ".join(col_names) + " |"
            separator = "| " + " | ".join(["---"] * len(col_names)) + " |"
            md_rows = []
            for row in rows[:50]:
                md_rows.append("| " + " | ".join(str(v) for v in row) + " |")
            table = "\n".join([header, separator] + md_rows)
            if len(rows) > 50:
                table += (
                    "\n\n（结果超过 50 行，只显示前 50 行——这是【结果行数被截断】,"
                    "不是 SQL 被截断。请先用 WHERE 精确过滤（如姓名/编号）缩小范围，"
                    "或改用 GROUP BY/COUNT 在 SQL 内聚合汇总，不要试图拉全量。）"
                )

            # Cap total output size so a single wide query can't blow out the
            # LLM context window. The full result remains available via the
            # ToolResultStorage persistence layer if it exceeds the threshold.
            MAX_SQL_OUTPUT_CHARS = 20_000
            if len(table) > MAX_SQL_OUTPUT_CHARS:
                table = (
                    table[:MAX_SQL_OUTPUT_CHARS]
                    + "\n\n... [输出超过 20000 字符被截断——这是【结果体积被截断】，"
                    "不是 SQL 被截断。请加 WHERE 过滤或用 GROUP BY 聚合减少返回内容]"
                    f"(Total rows: {len(rows)})"
                )

            # 【口径自动回述】附在截断之后，保证这段不会被截掉。
            try:
                _clause = _describe_sql(_sql_for_clause)
                if _clause:
                    table += "\n" + _clause
            except Exception:
                pass  # 回述失败不能影响查询结果

            _seen.add(_normalized)
            _mark_ok()
            return json.dumps(
                {"chunks": [{"output_type": "markdown", "content": table}]},
                ensure_ascii=False,
            )
        except Exception as e:
            _mark_fail()
            err_str = str(e)
            # 【列名无效自动纠错】模型常无真实列清单可用 → 反复用幻觉列名
            # 失败（ORA-00904 标识符无效 / ORA-00972 标识符过长 / column not found）。
            # 此时不再只报错，直接把该表真实字段清单（列名: 注释）回给模型，
            # 让它下一步用正确列名，从根上打断"猜列名→失败→再猜"死循环。
            col_hint = ""
            if database_connector is not None and any(
                kw in err_str.upper()
                for kw in (
                    "ORA-00904",
                    "ORA-00972",
                    "INVALID IDENTIFIER",
                    "IDENTIFIER IS TOO LONG",
                    "标识符无效",
                    "标识符过长",
                    "IDENTIFIER TOO LONG",
                )
            ):
                try:
                    import re as _re2

                    _fm = _re2.search(
                        r"\bFROM\s+(?:[\w\"]+\.)*([A-Za-z0-9_\"$#]+)",
                        sql_stripped or "",
                        _re2.IGNORECASE,
                    )
                    if _fm:
                        _tbl = _fm.group(1).strip('"').upper()
                        # 与 conn_oracle [6] 一致：CURRENT_SCHEMA 取业务 schema，
                        # 不依赖 get_columns 的 ORACLE_SCHEMA 环境变量。
                        _chk = await _run_sql(
                            "SELECT c.column_name, "
                            "NVL((SELECT t.comments FROM all_col_comments t "
                            "WHERE t.owner=c.owner AND t.table_name=c.table_name "
                            "AND t.column_name=c.column_name), '') "
                            "FROM all_tab_columns c "
                            "WHERE c.table_name='%s' "
                            "AND c.owner=SYS_CONTEXT('USERENV','CURRENT_SCHEMA') "
                            "ORDER BY c.column_id" % _tbl
                        )
                        _cols = [list(r) for r in (_chk or [])[1:]]
                        if _cols:
                            _lines = []
                            for _row in _cols[1:41]:
                                _cname = str(_row[0] or "")
                                _ccom = str(_row[1] or "").strip()
                                _lines.append(
                                    f"{_cname}（{_ccom}）" if _ccom else _cname
                                )
                            _more = "…（其余略，" if len(_cols) > 40 else "（共 %d 列，" % len(_cols)
                            col_hint = (
                                f"\n\n【列名纠偏】SQL 因列名无效失败（Oracle {err_str.strip()[:120]}）。"
                                f"表 {_tbl} 实际字段{_more}"
                                "请只使用以上真实存在的列名重写 SQL，不要臆造列名，"
                                "也不要 SELECT * 或全量列。"
                                + ", ".join(_lines)
                                + "）"
                            )
                except Exception:
                    pass

            content = f"SQL 执行失败: {err_str}" + col_hint + _breaker_suffix()
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": content}]},
                ensure_ascii=False,
            )

    return sql_query
