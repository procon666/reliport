"""导出渲染层 Renderer：把 Markdown 渲染成 PDF / HTML，并落盘。
默认产出 PDF（普通用户友好）+ 保留 .md。
"""
import logging
import re
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")
    return s[:40] or "report"


def _write_md(md_text: str, out_dir: Path, topic: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_slugify(topic)}.md"
    path.write_text(md_text, encoding="utf-8")
    return path


def _md_to_html(md_text: str) -> str:
    import markdown
    extensions = ["tables", "fenced_code", "nl2br"]
    body = markdown.markdown(md_text, extensions=extensions)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>调研报告</title>
<style>
body {{ font-family: "PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
       max-width: 860px; margin: 40px auto; padding: 0 24px;
       color: #222; line-height: 1.7; font-size: 15px; }}
h1 {{ font-size: 26px; border-bottom: 3px solid #2b6cb0; padding-bottom: 10px; }}
h2 {{ font-size: 20px; color: #1a5276; border-left: 4px solid #2b6cb0; padding-left: 10px; margin-top: 30px; }}
h3 {{ font-size: 17px; }}
blockquote {{ color: #666; border-left: 3px solid #ccc; margin: 8px 0; padding-left: 12px; background:#f8f8f8; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
th,td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: left; }}
th {{ background: #f0f4f8; }}
a {{ color: #2b6cb0; text-decoration: none; }}
hr {{ border: none; border-top: 1px solid #eee; margin: 24px 0; }}
</style></head><body>
{body}
</body></html>"""
    return html


# ---------------------------------------------------------------------------
# ReportLab PDF 渲染（纯 Python，Windows 零系统依赖）
# ---------------------------------------------------------------------------

# 中文字体自动探测：按优先级尝试，找不到则退回内置 Helvetica
# 说明：TTC 需用 subfontIndex=0 指定第一个子字体（TrueType 轮廓的 TTC 均支持）。
# 第一项指向项目自带字体（用户把字体放入 fonts/ 目录即可，打包 exe 时随包分发）。
from .config import BASE_DIR

_CJK_FONT_CANDIDATES = [
    # 项目自带字体（可选，用户放入 fonts/ 目录即可，最省心）
    ("EmbeddedCJK", str(BASE_DIR / "fonts" / "wqy-microhei.ttc")),
    # Windows（微软雅黑 / 宋体，均为 TrueType 轮廓 TTC，可加载）
    ("Microsoft YaHei", r"C:\Windows\Fonts\msyh.ttc"),
    ("SimSun", r"C:\Windows\Fonts\simsun.ttc"),
    ("SimHei", r"C:\Windows\Fonts\simhei.ttf"),
    # macOS
    ("PingFang SC", "/System/Library/Fonts/PingFang.ttc"),
    ("Hiragino Sans GB", "/System/Library/Fonts/Hiragino Sans GB.ttc"),
    # Linux
    ("WenQuanYi Micro Hei", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    ("WenQuanYi Zen Hei", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ("Noto Sans CJK SC", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ("Droid Sans Fallback", "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
]

_reportlab_fonts_ready = False
_reportlab_font_family = None


def _resolve_cjk_font():
    """找一套可用中文字体，注册到 ReportLab。找不到中文字体时退回 Helvetica，
    并在日志中明确提示（中文可能显示为方块）。"""
    global _reportlab_fonts_ready, _reportlab_font_family
    if _reportlab_fonts_ready:
        return _reportlab_font_family

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    family = "Helvetica"  # 兜底
    for name, path in _CJK_FONT_CANDIDATES:
        p = Path(path)
        if not p.exists():
            continue
        try:
            # TTC 加载第一个子字体
            pdfmetrics.registerFont(TTFont(name, str(p), subfontIndex=0))
            family = name
            logger.info("PDF 使用中文字体: %s", path)
            break
        except Exception as e:
            logger.warning("字体注册失败 %s: %s", path, e)

    if family == "Helvetica":
        logger.warning("未找到可用的中文字体，PDF 中文可能显示为方块。"
                       "建议将 wqy-microhei.ttc 放入项目 fonts/ 目录。")

    # 注册字体族映射，让 Paragraph 中的 <b>/<i> 能识别 (即使没有真粗体，
    # 也避免 ps2tt 报错导致整段丢失)
    try:
        from reportlab.pdfbase.pdfmetrics import registerFontFamily
        registerFontFamily(family, normal=family, bold=family,
                           italic=family, boldItalic=family)
    except Exception:
        pass

    _reportlab_font_family = family
    _reportlab_fonts_ready = True
    return family


def _md_inline(s: str) -> str:
    """Markdown 行内解析 + HTML 安全合并：
    1) 把 **bold**/*italic*/`code`/[text](url) 转 ReportLab 标签；
    2) 规整 LLM 偶尔输出的 <br/>；
    3) 对所有"非白名单"标签做 HTML escape，避免 ReportLab ps2tt 报错；
    4) 【高置信】/【中置信】/【存疑】 → 带背景色色块。
    """
    # 1) 置信度色块（最先处理，因为包含 <font> 标签）
    s = s.replace("【高置信】", "<font backColor='#c8e6c9' color='#1b5e20'><b> 高置信 </b></font>")
    s = s.replace("【中置信】", "<font backColor='#fff3cd' color='#7a5c00'><b> 中置信 </b></font>")
    s = s.replace("【存疑】", "<font backColor='#ffcdd2' color='#b71c1c'><b> 存疑 </b></font>")
    # 2) 规整 <br/>
    s = re.sub(r"<br\s*/?>", "<br/>", s, flags=re.IGNORECASE)
    # 3) 链接 [text](url)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               lambda m: f"<link href='{m.group(2)}' color='#2b6cb0'><u>{m.group(1)}</u></link>",
               s)
    # 4) **bold** / *italic* / `code`
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r"<font color='#c7254e'>\1</font>", s)
    # 5) 保护 ReportLab 合法标签不被 escape 转义
    tags = []
    def _stash(m):
        tags.append(m.group(0))
        return f"\x00T{len(tags)-1}\x00"
    # 匹配所有 ReportLab 支持的标签（含属性）。白名单包括：
    # b/i/u/font/link/br（基础格式）、super/sub（上标下标）
    s = re.sub(r"<(/?(?:b|i|u|font|link|br|super|sub)\b)[^<>]*>", _stash, s, flags=re.IGNORECASE)
    # 6) escape 剩余字面 < > &
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 7) 还原 ReportLab 标签
    for i, t in enumerate(tags):
        s = s.replace(f"\x00T{i}\x00", t)
    return s


def _md_line_to_flowables(md_text: str, style_h1, style_h2, style_h3, style_body,
                          style_li, style_code, style_quote, font_family):
    """把 Markdown 文本转成 ReportLab 的 flowable 列表（标题/段落/表格/引用/代码）。"""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, Preformatted
    from reportlab.lib.styles import ParagraphStyle

    lines = md_text.split("\n")
    flows = []
    i = 0
    table_buf = []          # 当前正在累积的表格行
    in_code = False         # 是否在 ``` 代码块里
    code_buf = []
    para_buf = []           # 普通段落（多行合并成一个 <br/> 段落）

    def flush_para():
        if para_buf:
            text = "<br/>".join(x.strip() for x in para_buf if x.strip())
            if text:
                flows.append(Paragraph(escape(text), style_body))
            para_buf.clear()

    def flush_table():
        if not table_buf:
            return
        # 用 Paragraph 包裹 cell，让 ReportLab 按真实字体度量计算列宽（关键！）
        # 这样中文/英文混合时列宽才不会被错误估算成 0
        from reportlab.platypus import Paragraph as _P
        cell_style = ParagraphStyle("cell", parent=style_body,
                                    fontSize=9, leading=12,
                                    alignment=TA_LEFT, spaceAfter=0)
        head_style = ParagraphStyle("head", parent=cell_style,
                                    textColor="#1a5276", fontName=font_family)

        rows = []
        for row in table_buf:
            cells = [c.strip() for c in row.strip("|").split("|")]
            rows.append(cells)
        if rows:
            ncol = max(len(r) for r in rows)
            for r in rows:
                while len(r) < ncol:
                    r.append("")
            # 包裹 Paragraph
            wrapped = []
            for i, r in enumerate(rows):
                s = head_style if i == 0 else cell_style
                wrapped.append([_P(escape(c) if c else "", s) for c in r])
            t = Table(wrapped, repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            flows.append(t)
            flows.append(Spacer(1, 8))
        table_buf.clear()

    def escape(s):
        # escape 已与 _md_inline 合并：同时处理 markdown 行内格式与 HTML escape
        return _md_inline(s)

    for line in lines:
        s = line.rstrip()
        if in_code:
            if s.startswith("```"):
                in_code = False
                code_text = "\n".join(code_buf)
                flows.append(Preformatted(escape(code_text), style_code))
                flows.append(Spacer(1, 6))
                code_buf = []
            else:
                code_buf.append(s)
            continue

        # 代码块开始
        if s.startswith("```"):
            flush_para(); flush_table()
            in_code = True
            code_buf = []
            continue

        # 表格分隔行：|---|---|
        if re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]+\|", s) and "-" in s and not s.startswith("#"):
            continue

        # 表格行
        if s.startswith("|"):
            flush_para()
            table_buf.append(s)
            continue

        flush_table()

        # 引用
        if s.startswith(">"):
            flush_para()
            text = s.lstrip(">").strip()
            flows.append(Paragraph(escape(text), style_quote))
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            flush_para()
            level = len(m.group(1))
            content = escape(m.group(2))
            if level == 1:
                flows.append(Paragraph(content, style_h1))
            elif level == 2:
                flows.append(Paragraph(content, style_h2))
            elif level == 3:
                flows.append(Paragraph(content, style_h3))
            else:
                flows.append(Paragraph(content, style_h3))
            continue

        # 列表项
        m = re.match(r"^\s*([-*+]|\d+[.、)])\s+(.*)", s)
        if m:
            flush_para()
            flows.append(Paragraph(escape(m.group(2)), style_li))
            continue

        # 分隔线
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", s):
            flush_para()
            flows.append(Spacer(1, 6))
            continue

        # 空行 -> 结束当前段落
        if not s.strip():
            flush_para()
            continue

        para_buf.append(s)

    flush_para()
    flush_table()
    if in_code and code_buf:
        flows.append(Preformatted(escape("\n".join(code_buf)), style_code))
    return flows


def _write_pdf(md_text: str, out_dir: Path, topic: str) -> Path:
    """ReportLab 渲染 PDF（纯 Python，Windows/Linux/macOS 通用，无系统依赖）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer)

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"{_slugify(topic)}.pdf"

    font_family = _resolve_cjk_font()

    # 样式（从零构建，替换字体）
    def _st(base, **kw):
        style = ParagraphStyle(name=base)
        style.fontName = font_family
        for k, v in kw.items():
            setattr(style, k, v)
        return style

    title = topic or "调研报告"
    first_line = md_text.strip().split("\n")[0] if md_text.strip() else ""

    style_title = _st("title", fontSize=20, leading=26, spaceAfter=6,
                      alignment=TA_LEFT, textColor="#1a5276")
    style_h1 = _st("h1", fontSize=17, leading=22, spaceBefore=14, spaceAfter=6,
                   textColor="#1a5276")
    style_h2 = _st("h2", fontSize=14.5, leading=19, spaceBefore=12, spaceAfter=5,
                   textColor="#2b6cb0")
    style_h3 = _st("h3", fontSize=12.5, leading=17, spaceBefore=8, spaceAfter=3,
                   textColor="#333333")
    style_body = _st("body", fontSize=10.5, leading=16, spaceAfter=4,
                     alignment=TA_JUSTIFY)
    style_li = _st("li", fontSize=10.5, leading=15.5, spaceAfter=2, leftIndent=16)
    style_quote = _st("quote", fontSize=10, leading=15, textColor="#666666",
                      leftIndent=12, spaceAfter=4)
    style_code = _st("code", fontSize=8.5, leading=12, textColor="#333333",
                     backColor="#f4f4f4", borderPadding=4, leftIndent=4)

    story = []
    # 封面标题块
    if first_line and first_line.startswith("#"):
        story.append(Paragraph(escape_md(first_line.lstrip("#").strip()), style_title))
        body_start = md_text.strip().split("\n", 1)[1] if "\n" in md_text.strip() else ""
    else:
        story.append(Paragraph(escape_md(title), style_title))
        body_start = md_text
    story.append(Spacer(1, 4))

    story += _md_line_to_flowables(body_start, style_h1, style_h2, style_h3,
                                   style_body, style_li, style_code, style_quote,
                                   font_family)

    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4,
                            leftMargin=2.2*cm, rightMargin=2.2*cm,
                            topMargin=2.0*cm, bottomMargin=2.0*cm,
                            title=topic, author="AI Research Agent")
    doc.build(story)
    logger.info("PDF 已生成: %s", pdf_path)
    return pdf_path


def escape_md(s: str) -> str:
    # _md_inline 已统一处理 markdown 行内格式 + HTML escape，
    # 不再先做 escape（否则会破坏 <br/> 等合法标签）
    return _md_inline(s)


def render(md_text: str, topic: str, fmt: str = "pdf", out_dir: Path = None) -> dict:
    """渲染并落盘。fmt: pdf | md | both。返回 {path, type} 信息。"""
    out_dir = out_dir or settings.output_dir
    produced = []

    md_path = _write_md(md_text, out_dir, topic)
    produced.append(("markdown", md_path))

    fmt = fmt.lower()
    if fmt in ("pdf", "both"):
        p = _write_pdf(md_text, out_dir, topic)
        produced.append(("pdf" if p.suffix == ".pdf" else "html", p))

    return {"md_path": md_path, "produced": produced, "out_dir": out_dir}
