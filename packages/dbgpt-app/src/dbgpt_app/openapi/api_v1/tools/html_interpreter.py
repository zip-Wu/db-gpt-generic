"""html_interpreter tool — render HTML as an interactive report."""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dbgpt.agent.resource.tool.base import tool

logger = logging.getLogger(__name__)

# ── 离线资源本地化 ──────────────────────────────────────────────────────────
# 报告 HTML 由模型生成，可能引用外部 CDN 脚本或样式表；目标环境是内网离线部署，
# 浏览器取不到这些资源，图表会渲染成空白或被压成一条，页面还会卡在等待请求
# 超时。这里在返回前统一处理：已知资源改写到本地 /images/vendor/ 路径（由
# /images 静态挂载直接服务），清单外的外部引用一律剥离。
VENDOR_URL_PREFIX = "/images/vendor"
CHARTJS_LOCAL_URL = f"{VENDOR_URL_PREFIX}/chart.umd.min.js"

# ── 渲染熔断（2026-09-26 新增）──────────────────────────────────────────────
# 报告类任务中，模型一旦写出超过单轮输出上限的 HTML 就会被拦腰截断；而截断发生
# 在【生成阶段】，模型看不到自己已经写了多少字符，工具回它"压到 8000 字符以内"
# 对它而言不可执行 → 只能反复重写。实测连续 39 次调用
# html_interpreter、每次入参都被截断，直到把 max_retry_count=50 烧完、用户什么
# 都拿不到。这里给它一个硬边界：确凿截断第 1 次仍按原逻辑渲染残页并给指引
# （保留一次改正机会），第 _HTML_TRUNCATION_LIMIT 次起拒绝渲染、要求改用 markdown
# 交付，不再空烧步数。只作用于"确凿截断"分支，成功路径完全不受影响。
_HTML_TRUNCATION_LIMIT = 2

_OFFLINE_REWRITE_RULES = [
    (
        re.compile(
            r"""https?://cdn\.jsdelivr\.net/npm/chart\.js@[^/"'<>\s]+/dist/chart\.umd(?:\.min)?\.js""",
            re.IGNORECASE,
        ),
        CHARTJS_LOCAL_URL,
    ),
    (
        re.compile(
            r"""https?://unpkg\.com/chart\.js@[^/"'<>\s]+/dist/chart\.umd(?:\.min)?\.js""",
            re.IGNORECASE,
        ),
        CHARTJS_LOCAL_URL,
    ),
    (
        re.compile(
            r"""https?://cdn\.jsdelivr\.net/npm/chart\.js(?:@[^/"'<>\s]+)?/dist/chart\.js""",
            re.IGNORECASE,
        ),
        CHARTJS_LOCAL_URL,
    ),
]

_EXTERNAL_SCRIPT_RE = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*["']https?://[^"']+["'][^>]*>\s*</script\s*>""",
    re.IGNORECASE,
)
_EXTERNAL_LINK_RE = re.compile(
    r"""<link\b[^>]*\bhref\s*=\s*["']https?://[^"']+["'][^>]*?/?>""",
    re.IGNORECASE,
)
_CHART_USAGE_RE = re.compile(
    r"new\s+Chart\s*\(|Chart\.register|Chart\.defaults", re.IGNORECASE
)
_CHART_INCLUDED_RE = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*["'][^"']*chart[^"']*\.js["']""", re.IGNORECASE
)


def _truncation_confidence(html: str) -> str:
    """区分「确凿被截断」与「只是没写结束标签」。

    `</html>` 缺失只说明标签不全，模型本身也常顺手省略；若把它一律当成截断，
    会反过来引发"被判截断 → 重写 → 又省略 → 又被判截断"的自激循环。所以这里
    再看有没有旁证，只有旁证齐全才当作确凿截断（也才计入熔断）：

      - 引号不配平：停在属性值 / 字符串中间；
      - `{` 多于 `}`：停在未闭合的 <style>/<script> 块里；
      - 尾部停在半个标签里（最后一段有 `<` 却无配对的 `>`）。

    返回 "none"（有结束标签）/ "hard"（确凿截断）/ "soft"（仅缺结束标签）。
    """
    if "</html>" in html.lower():
        return "none"
    tail = html.rstrip()
    if not tail:
        return "hard"
    if tail.count('"') % 2 or tail.count("'") % 2:
        return "hard"
    if tail.count("{") > tail.count("}"):
        return "hard"
    seg = tail[-60:]
    if "<" in seg and seg.rfind("<") > seg.rfind(">"):
        return "hard"
    return "soft"


def _esc(value: Any) -> str:
    """HTML 文本转义（报告数据来自模型，必须转义后再拼进文档）。"""
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_REPORT_CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px;background:#f5f6f8;color:#1f2937;
  font:15px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
  "Hiragino Sans GB","Microsoft YaHei",sans-serif}
.report{max-width:960px;margin:0 auto;background:#fff;border-radius:12px;
  padding:40px 44px;box-shadow:0 2px 12px rgba(0,0,0,.06)}
h1{margin:0 0 6px;font-size:26px;line-height:1.4;color:#111827}
.subtitle{margin:0 0 28px;color:#6b7280;font-size:14px}
section{margin:0 0 32px}
h2{margin:0 0 12px;font-size:18px;color:#1f2937;
  border-left:4px solid #2563eb;padding-left:10px;line-height:1.5}
p{margin:0 0 12px;white-space:pre-wrap;word-break:break-word}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:14px}
th,td{border:1px solid #e5e7eb;padding:8px 12px;text-align:left}
th{background:#f3f4f6;font-weight:600;color:#374151}
tbody tr:nth-child(even){background:#fafafa}
.chart{margin:16px 0;height:340px}
.footnote{margin-top:32px;padding-top:16px;border-top:1px solid #e5e7eb;
  color:#6b7280;font-size:13px;white-space:pre-wrap}
"""


def render_report_html(report: Any) -> str:
    """把结构化报告数据渲染成完整 HTML。

    存在的理由：让模型不必再吐出上万字符的 HTML。报告类任务里，模型单轮输出
    （思考 + 工具入参）共享同一个 max_new_tokens 预算，一份手写 HTML 报告动辄
    需要数千 token，撞上限后会被拦腰截断、且模型感知不到自己写了多少 —— 只会
    反复重写直到步数烧完（实测连续 39 次）。改为"模型只传数据、
    HTML 由这里生成"后，入参从数千 token 降到几百 token，对输出上限免疫。

    期望结构（字段都可缺省，缺省即不渲染该部分）::

        {"title": "...", "subtitle": "...",
         "sections": [
            {"heading": "...", "text": "...",
             "table": {"columns": [...], "rows": [[...], ...]},
             "chart": {"type": "bar|line|pie", "labels": [...],
                       "values": [...], "label": "人数"}},
            ...],
         "footnote": "..."}
    """
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except Exception:
            report = {}
    if not isinstance(report, dict):
        report = {}

    title = _esc(report.get("title") or "数据报告")
    subtitle = report.get("subtitle")
    parts: List[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{title}</title>",
        f"<style>{_REPORT_CSS}</style>",
        f'<script src="{CHARTJS_LOCAL_URL}"></script>',
        "</head><body><div class='report'>",
        f"<h1>{title}</h1>",
    ]
    if subtitle:
        parts.append(f"<div class='subtitle'>{_esc(subtitle)}</div>")

    sections = report.get("sections")
    if not isinstance(sections, list):
        sections = []
    for idx, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        parts.append("<section>")
        if sec.get("heading"):
            parts.append(f"<h2>{_esc(sec['heading'])}</h2>")
        if sec.get("text"):
            parts.append(f"<p>{_esc(sec['text'])}</p>")

        table = sec.get("table")
        if isinstance(table, dict):
            cols = table.get("columns") or []
            rows = table.get("rows") or []
            if cols:
                head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
                body = "".join(
                    "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>"
                    for row in rows
                    if isinstance(row, (list, tuple))
                )
                parts.append(
                    f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
                )

        chart = sec.get("chart")
        if isinstance(chart, dict) and chart.get("labels") and chart.get("values"):
            canvas_id = f"chart_{idx}"
            ctype = str(chart.get("type") or "bar").lower()
            if ctype not in ("bar", "line", "pie", "doughnut"):
                ctype = "bar"
            labels = json.dumps(chart["labels"], ensure_ascii=False)
            values = json.dumps(chart["values"], ensure_ascii=False)
            series = _esc(chart.get("label") or "数值")
            parts.append(f"<div class='chart'><canvas id='{canvas_id}'></canvas></div>")
            parts.append(
                "<script>(function(){var el=document.getElementById("
                f"'{canvas_id}');if(!el||typeof Chart==='undefined')return;"
                f"new Chart(el,{{type:'{ctype}',data:{{labels:{labels},"
                f"datasets:[{{label:'{series}',data:{values},"
                "backgroundColor:['#2563eb','#10b981','#f59e0b','#ef4444',"
                "'#8b5cf6','#06b6d4','#84cc16','#f97316']}]}},"
                "options:{responsive:true,maintainAspectRatio:false,"
                "plugins:{legend:{display:true}}}});})();</script>"
            )
        parts.append("</section>")

    if report.get("footnote"):
        parts.append(f"<div class='footnote'>{_esc(report['footnote'])}</div>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def localize_offline_assets(html: str) -> Tuple[str, List[str]]:
    """把报告 HTML 里的外部资源改写成本地可用形态，返回 (html, notes)。

    notes 是处理说明，会写进工具 observation 供模型参考，便于模型下一轮直接
    采用离线写法（不再引用外部 CDN）。
    """
    notes: List[str] = []

    for pattern, local_url in _OFFLINE_REWRITE_RULES:
        html, count = pattern.subn(local_url, html)
        if count:
            notes.append(f"{count} 处外部资源已改用本地副本")

    stripped = 0
    html, count = _EXTERNAL_SCRIPT_RE.subn("", html)
    stripped += count
    html, count = _EXTERNAL_LINK_RE.subn("", html)
    stripped += count
    if stripped:
        notes.append(f"{stripped} 处外部资源引用已移除（离线环境不可用）")

    if _CHART_USAGE_RE.search(html) and not _CHART_INCLUDED_RE.search(html):
        tag = f'<script src="{CHARTJS_LOCAL_URL}"></script>'
        if re.search(r"</head>", html, re.IGNORECASE):
            html = re.sub(
                r"</head>", tag + "</head>", html, count=1, flags=re.IGNORECASE
            )
        else:
            html = tag + html
        notes.append("已自动引入本地图表库")

    lowered = html.lower()
    if "</html>" not in lowered:
        html = html.rstrip() + ("\n" if "</body>" in lowered else "\n</body>\n") + "</html>\n"
        notes.append("报告 HTML 疑似被截断（缺少 </html> 结束标签），已补齐结束标签兜底渲染")

    if notes:
        logger.info("html_interpreter: offline asset localization — %s", "; ".join(notes))

    return html, notes


def make_html_interpreter(react_state: Dict[str, Any], skills_dir: str):
    @tool(
        description=(
            "将报告渲染为可交互的网页，这是向用户展示网页报告的唯一方式。"
            "【首选·report 模式】只传结构化数据，HTML 由系统生成——"
            "不会被单轮输出长度截断，强烈建议优先使用："
            '{"report": {"title": "报告标题", "subtitle": "数据截至 2026-09-26",'
            ' "sections": [{"heading": "一、总体情况", "text": "结论文字……",'
            '   "table": {"columns": ["类目", "数量"], "rows": [["类目A", 8025],'
            ' ["类目B", 9473]]},'
            '   "chart": {"type": "bar", "labels": ["类目A", "类目B"],'
            ' "values": [8025, 9473], "label": "人数"}}],'
            ' "footnote": "口径说明：……"}}。'
            "sections 可给多段；table 与 chart 都可以省略；chart.type 取 "
            "bar/line/pie；数字直接写数值（不要加引号、不要写千分位）。"
            "【一次性】同一份报告禁止重复调用本工具；渲染成功后若目标已达成"
            "请直接调用 terminate。"
            "【html 模式·仅在确实需要自定义版式时用】直接传完整 HTML 字符串："
            '{"html": "<html>...</html>", "title": "标题"}，'
            "需自己写全 HTML（含 <!DOCTYPE html>、<html>、<head>、<body>），"
            "总长务必控制在 8000 字符以内（单轮输出超长约 1 万字符会被截断）；"
            "内容多时合并进同一份 HTML，不要分多次调用。"
            "【禁止】不要用 code_interpreter 生成 HTML 再 print 或写入文件。"
            "【技能模式 - 仅在使用技能时可选】可用模板模式："
            '{"template_path": "技能名/templates/模板.html", '
            '"data": {"KEY": "值"}, "title": "标题"}；'
            '或用文件模式：{"file_path": "/path/to/report.html"}。'
        )
    )
    async def html_interpreter(
        html: str = "",
        title: str = "Report",
        file_path: str = "",
        template_path: str = "",
        data: dict | str = None,
        report: dict | str = None,
    ) -> str:
        """Render HTML as an interactive web report."""
        from dbgpt.configs.model_config import STATIC_MESSAGE_IMG_PATH

        skills_path = Path(skills_dir).expanduser().resolve()

        # ── Mode 0: report（结构化数据 → 代码生成 HTML）────────────────────
        # 放在最前面：这条路径的入参只有几百字符，不受单轮输出上限影响，
        # 是报告类任务应当优先走的路径（详 render_report_html 的说明）。
        if report and not template_path and not file_path:
            _report_obj = report
            if isinstance(_report_obj, str):
                try:
                    _report_obj = json.loads(_report_obj)
                except Exception:
                    _report_obj = None
            if not isinstance(_report_obj, dict):
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    "report 参数不是合法的 JSON 对象（可能是生成时被"
                                    "截断，或格式写错）。请按 "
                                    '{"title": "...", "sections": [{"heading": "...", '
                                    '"text": "...", "table": {"columns": [...], '
                                    '"rows": [[...]]}}]} 的结构重发一次；'
                                    "若数据本身很短仍失败，可改用 html 参数传入"
                                    "完整 HTML。"
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            html = render_report_html(_report_obj)
            if not title or title == "Report":
                title = str(_report_obj.get("title") or "数据报告")

        # ── Mode 1: template_path + data ──
        if template_path and template_path.strip():
            tp = template_path.strip()
            target = (skills_path / tp).resolve()
            try:
                target.relative_to(skills_path)
            except ValueError:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Invalid template_path: {tp}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if not target.is_file():
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"Template not found: {tp}. "
                                    "Please retry using the `html` parameter "
                                    "directly — "
                                    "generate complete HTML yourself and pass it via "
                                    '{"html": "<html>...</html>", "title": "title"}.'
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            try:
                raw_template = target.read_text(encoding="utf-8")
            except Exception as e:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Error reading template: {e}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

            replacements = data
            if isinstance(replacements, str):
                try:
                    replacements = json.loads(replacements)
                except Exception:
                    try:
                        fixed = str(replacements).rstrip()
                        if not fixed.endswith("}"):
                            fixed += '"}' if not fixed.endswith('"') else "}"
                        replacements = json.loads(fixed)
                    except Exception:
                        replacements = {}
            if not isinstance(replacements, dict):
                replacements = {}

            auto_data = react_state.get("auto_data", {})
            if isinstance(auto_data, dict):
                replacements = {**auto_data, **replacements}

            ratio_data = react_state.get("ratio_data", {})
            if isinstance(ratio_data, dict):
                replacements = {**ratio_data, **replacements}

            image_url_map = react_state.get("image_url_map", {})
            if isinstance(image_url_map, dict):
                for stem, url in image_url_map.items():
                    chart_key = f"CHART_{stem.upper()}"
                    if chart_key not in replacements:
                        replacements[chart_key] = url

            def _replace_placeholder(m):
                key = m.group(1)
                return str(replacements.get(key, ""))

            html = re.sub(r"\{\{([A-Z_0-9]+)\}\}", _replace_placeholder, raw_template)
            if not title or title == "Report":
                title = target.stem

        # ── Mode 2: file_path ──
        elif file_path and file_path.strip():
            fp = file_path.strip()
            if not os.path.isfile(fp):
                cid = react_state.get("conv_id") or "default"
                from dbgpt.configs.model_config import PILOT_PATH

                alt = os.path.join(PILOT_PATH, "data", cid, os.path.basename(fp))
                if os.path.isfile(alt):
                    fp = alt
                else:
                    return json.dumps(
                        {
                            "chunks": [
                                {
                                    "output_type": "text",
                                    "content": f"File not found: {file_path}",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    html = f.read()
                if not title or title == "Report":
                    title = os.path.splitext(os.path.basename(fp))[0]
            except Exception as e:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Error reading file: {e}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        # ── Mode 3: inline html ──
        if html and isinstance(html, str) and not template_path and not file_path:
            if "\\n" in html:
                html = html.replace("\\n", "\n")
            if "\\t" in html:
                html = html.replace("\\t", "\t")

        if not html or not html.strip():
            return json.dumps(
                {
                    "chunks": [
                        {"output_type": "text", "content": "No HTML content provided"}
                    ]
                },
                ensure_ascii=False,
            )

        # Post-process: fix image URLs
        fixed_html = html.strip()
        try:
            IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
            name_to_served: Dict[str, str] = {}
            if os.path.isdir(STATIC_MESSAGE_IMG_PATH):
                for fname in os.listdir(STATIC_MESSAGE_IMG_PATH):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in IMAGE_EXTS:
                        continue
                    m = re.match(r"^[0-9a-f]{8}_(.+)$", fname, re.IGNORECASE)
                    if m:
                        name_to_served[m.group(1).lower()] = f"/images/{fname}"

            if name_to_served:

                def _fix_img_src(match: re.Match) -> str:
                    prefix = match.group(1)
                    raw_path = match.group(2)
                    quote = match.group(3)
                    filename = raw_path.rsplit("/", 1)[-1].lower()
                    if re.match(r"^[0-9a-f]{8}_.+$", filename, re.IGNORECASE):
                        return match.group(0)
                    if filename in name_to_served:
                        return f"{prefix}{name_to_served[filename]}{quote}"
                    return match.group(0)

                fixed_html = re.sub(
                    r"""(src\s*=\s*["'])([^"']+\.(?:png|jpg|jpeg|gif|svg|webp))(["'])""",
                    _fix_img_src,
                    fixed_html,
                    flags=re.IGNORECASE,
                )
        except Exception:
            pass

        # Auto-append missing images
        try:
            gen_images = react_state.get("generated_images", [])
            if gen_images:
                html_img_stems = set(
                    re.sub(r"^[0-9a-f]+_", "", os.path.basename(src))
                    for src in re.findall(
                        r'<img[^>]+src=["\']([^"\']+)["\']', fixed_html, re.IGNORECASE
                    )
                )

                def _img_stem(url):
                    return re.sub(r"^[0-9a-f]+_", "", os.path.basename(url))

                missing = [
                    url
                    for url in gen_images
                    if url not in fixed_html and _img_stem(url) not in html_img_stems
                ]
                if missing:
                    imgs_html = "".join(
                        f'<div style="margin:16px 0">'
                        f'<img src="{url}" '
                        f'style="max-width:100%;height:auto;border-radius:8px">'
                        f"</div>"
                        for url in missing
                    )
                    section = (
                        '<div style="margin-top:32px"><h2>📊 分析图表</h2>'
                        f"{imgs_html}</div>"
                    )
                    if "</body>" in fixed_html.lower():
                        fixed_html = re.sub(
                            r"(</body>)",
                            section + r"\1",
                            fixed_html,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                    else:
                        fixed_html += section
        except Exception:
            pass

        # 【截断判定必须放在本地化之前】localize_offline_assets 会给缺 </html> 的
        # 残页补齐结束标签，补完就再也分不出"本来就没写"还是"被截断"。
        _truncation_level = _truncation_confidence(fixed_html)

        # 离线资源本地化：必须在图片 URL 修正之后、返回给前端之前执行，
        # 否则模型引用的外部 CDN 会在无外网环境加载失败。
        fixed_html, offline_notes = localize_offline_assets(fixed_html)

        # ── 渲染熔断：确凿截断累计达上限后拒绝再渲染 ──
        if _truncation_level == "hard":
            _trunc_count = int(react_state.get("html_render_truncations", 0)) + 1
            react_state["html_render_truncations"] = _trunc_count
            if _trunc_count >= _HTML_TRUNCATION_LIMIT:
                logger.warning(
                    "html_interpreter: 输入 HTML 连续 %d 次在生成阶段被截断，"
                    "已熔断、不再渲染残页",
                    _trunc_count,
                )
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"【本工具已熔断】你传入的 HTML 已连续 {_trunc_count} 次"
                                    "在生成阶段被截断（每次都是写到一半就断），"
                                    "重复调用不可能成功——这不是写法问题，而是单轮输出量"
                                    "超过了模型上限。请立刻停止调用 html_interpreter"
                                    f"【不要再尝试第 {_trunc_count + 1} 次】，改为在最终答复里"
                                    "用 Markdown 表格把已取得的数据交付给用户"
                                    "（先给核心数据表，再给一段要点结论），"
                                    "并在答复开头说明「网页版报告因篇幅超限未能生成」。"
                                    "然后立即调用 terminate 结束本轮。"
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        if any("截断" in n for n in offline_notes):
            # 输入 HTML 本身就不完整（模型输出被上游截断）——绝不能按"成功"汇报，
            # 否则模型会拿着残页继续 terminate，用户看到的就是丢图/断版的报告。
            summary = (
                "⚠️ 你传入的 HTML 参数在生成阶段就被截断（缺少结束标签，通常是单轮"
                "输出超过约 1 万字符上限导致）。系统已对残页做兜底修复并渲染，但报告"
                "尾部内容与图表脚本很可能已丢失。"
                "【必须重写】重新生成一份更精简的报告并重新调用本工具：整个 HTML "
                "控制在 8000 字符以内 —— 单个 <style> 块、元素不带内联 style、"
                "Chart.js 配置精简、最多 4 张图和 15 行表格；写不下的部分用一段文字"
                "说明省略了什么。不要重发刚才那份长报告。"
                "⚠️ 这是最后一次重试机会：若重写后仍被截断，本工具会自动熔断并"
                "禁止再次调用，届时必须改用 Markdown 表格交付数据。"
            )
        else:
            summary = (
                "✅ HTML 报告已成功渲染并展示给用户。报告任务已完成，"
                "请勿重复调用 html_interpreter 生成报告。"
                "若全部目标已达成，请直接调用 terminate 结束。"
            )
        if offline_notes:
            summary += (
                "【离线资源处理】本环境无外网，外部资源不可用："
                + "；".join(offline_notes)
                + "。图表请使用本地 Chart.js（/images/vendor/chart.umd.min.js）"
                "或内联 SVG 绘制，不要在 HTML 中引用任何外部地址。"
            )

        chunks: List[Dict[str, Any]] = [
            {"output_type": "text", "content": summary},
            {"output_type": "html", "content": fixed_html, "title": title},
        ]
        return json.dumps({"chunks": chunks}, ensure_ascii=False)

    return html_interpreter
