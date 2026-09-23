"""撰写层 Writer：把 Brief + 检索文档 综合成结构化 Markdown 报告。
强制"每条关键结论带来源引用"，并附来源清单。
"""
import logging
import time
from typing import List

from . import llm
from .analyzer import analyze, summarize_conflicts
from .topic_style import is_narrative_topic

logger = logging.getLogger(__name__)

# ---- 行业/市场体裁的撰写提示（原模板，一字不改）----
_MARKET_SYS = (
    "你是一名资深行业调研分析师。基于提供的来源资料，撰写一份结构化调研报告。\n"
    "硬性要求：\n"
    "1. 严格基于提供的资料，不得编造来源中没有的事实或数据\n"
    "2. 每条关键结论/数据必须标注来源编号，格式（来源①）\n"
    "3. 区分事实与推断，推断前加\"（推断）\"\n"
    "4. 按 Markdown 结构化输出：执行摘要 / 背景与定义 / 关键发现 / "
    "数据速览(如无数据可注明) / 争议与不确定性 / 结论与建议 / 来源清单\n"
    "5. 篇幅适中、重点突出，贴合简报的受众与视角\n"
    "6. **置信度分级**：根据多个来源是否一致给出明确置信标记，不要模棱两可：\n"
    "   - 多来源一致、可信度高 → 用确定句式 + 标注【高置信】\n"
    "   - 来源存在分歧或可信度一般 → 标注【中置信】\n"
    "   - 仅单一来源、可信度低或无法核实 → 标注【存疑】，并说明依据不足\n"
    "7. **冲突裁决**：当不同来源对同一事实说法冲突时，按「可信度加权 + 时效优先」"
    "采信更可信、更新近的来源，并在文中明示该分歧；若无法裁决，明确写出"
    "「该数据存在分歧，建议人工核实」，绝不硬编一个数字。"
)

# ---- 史/叙事体裁的撰写提示：以时间脉络为主线（保留普适内核 1-3/8-9）----
_NARRATIVE_SYS = (
    "你是一名资深发展史（编年史）撰稿人。基于提供的来源资料，撰写一份以"
    "**时间脉络为主线**的发展史报告。\n"
    "硬性要求：\n"
    "1. 严格基于提供的资料，不得编造来源中没有的事实或数据\n"
    "2. 每条关键结论/数据必须标注来源编号，格式（来源①）\n"
    "3. 区分事实与推断，推断前加\"（推断）\"\n"
    "4. 结构按时间脉络组织：执行摘要（点明演进主线与分期）/ 起源与萌芽"
    "（时代背景、雏形、早期代表）/ 发展阶段（**按时期分 3~5 节逐期展开**，"
    "每期写：时代背景、代表性产品/公司/事件、当时的关键量化数据、以及从上期"
    "转入本期的**转折原因**）/ 演进规律与驱动因素 / 关键数据时间轴（用表格把"
    "历年关键数据串成一条时间轴）/ 启示与展望 / 来源清单\n"
    "5. 用数字锚定历史：每个时期尽量给出当时可查的量化指标"
    "（用户量/收入/下载量/销量/规模等）\n"
    "6. **不要**写成'市场规模+竞争格局+份额'式的行业市场分析——那是另一种体裁\n"
    "7. 若不同来源对同一历史事实说法分歧：只写**真正的史料分歧或孤证**，标注"
    "【存疑】并说明依据；不要把不同年份/不同统计口径的数据反复当冲突罗列；"
    "主线一致则如实说明即可\n"
    "8. **置信度分级**：多来源一致→【高置信】；来源分歧或可信度一般→【中置信】；"
    "仅单一来源或无法核实→【存疑】并说明依据不足\n"
    "9. **冲突裁决**：真冲突时按「可信度加权 + 时效优先」采信更可信、更新近的"
    "来源并在文中明示分歧；无法裁决则明确写「该数据存在分歧，建议人工核实」，"
    "绝不硬编一个数字。"
)


def _build_source_context(docs) -> str:
    """把检索文档压缩成喂给 LLM 的上下文（含编号，便于引用）。"""
    parts = []
    for i, d in enumerate(docs, 1):
        body = d.content
        if len(body) > 1500:
            body = body[:1500] + "…"
        parts.append(
            f"[来源{i}] 标题：{d.title}\nURL：{d.url}\n可信度：{d.credibility}\n"
            f"正文：{body}"
        )
    return "\n\n".join(parts)


def write_report(brief: dict, docs: List) -> str:
    """生成 Markdown 报告，返回 md 文本。"""
    result = analyze(docs)
    docs = result["docs"]
    if not docs:
        return _empty_report(brief)

    source_ctx = _build_source_context(docs)
    conflict_ctx = summarize_conflicts(result.get("conflicts", []))

    sys = _NARRATIVE_SYS if is_narrative_topic(brief.get("topic", "")) else _MARKET_SYS
    brief_txt = "\n".join(f"{k}: {v}" for k, v in brief.items())
    user_parts = [
        f"=== 研究简报 ===\n{brief_txt}",
        f"=== 来源资料 ===\n{source_ctx}",
    ]
    if conflict_ctx:
        user_parts.append(f"=== 冲突提示（请按规则裁决） ===\n{conflict_ctx}")
    user_parts.append(
        "请撰写报告，最后附上'来源清单'（编号+标题+URL+可信度）。"
    )
    user = "\n\n".join(user_parts)
    try:
        md = llm.chat(sys, user, temperature=0.4, max_tokens=6000)
    except llm.LLMError as e:
        logger.error("报告生成失败: %s", e)
        return _error_report(brief, docs, str(e))

    md = _append_header(brief, md)
    # 若正文已自带"来源清单"，不再重复追加
    if "来源清单" not in md:
        md = _append_sources(md, docs)
    return md


def _append_header(brief: dict, md: str) -> str:
    now = time.strftime("%Y-%m-%d %H:%M")
    topic = brief.get("topic", "调研报告")
    audience = brief.get("audience", "")
    purpose = brief.get("purpose", "")
    angle = brief.get("angle", "")
    # 只在报告最上方加"元信息"小标题行，避免与正文大标题重复
    meta = (
        f"> 生成时间：{now} ｜ 信息源：联网检索+抓取\n"
        f"> 简报：受众={audience} ｜ 用途={purpose} ｜ 视角={angle}\n"
    )
    # 若正文已有 H1，就把元信息放在其下；否则补一个 H1
    if md.lstrip().startswith("# "):
        # 找到第一个 H1 行，在其后插入元信息
        lines = md.split("\n")
        idx = 0
        for i, ln in enumerate(lines):
            if ln.startswith("# "):
                idx = i
                break
        lines.insert(idx + 1, "\n" + meta.strip())
        return "\n".join(lines)
    return f"# {topic} 调研报告\n\n{meta.strip()}\n\n" + md


def _append_sources(md: str, docs) -> str:
    lines = ["\n\n---\n\n## 来源清单\n"]
    for i, d in enumerate(docs, 1):
        lines.append(f"{i}. **{d.title}** — {d.url} — 抓取时间 {d.fetched_at} — 可信度 {d.credibility}")
    return md + "\n".join(lines)


def _empty_report(brief: dict) -> str:
    now = time.strftime("%Y-%m-%d %H:%M")
    return (
        f"# {brief.get('topic', '调研报告')} 调研报告\n\n"
        f"> 生成时间：{now}\n\n"
        "本次未能检索到有效来源，报告未生成。请检查网络/搜索配置后重试。\n"
    )


def _error_report(brief: dict, docs, err: str) -> str:
    now = time.strftime("%Y-%m-%d %H:%M")
    head = (
        f"# {brief.get('topic', '调研报告')} 调研报告\n\n"
        f"> 生成时间：{now}\n\n报告生成遇到错误：{err}\n\n"
    )
    if docs:
        head += "已检索到以下来源（可人工查看）：\n"
        for d in docs:
            head += f"- {d.title} — {d.url}\n"
    return head
