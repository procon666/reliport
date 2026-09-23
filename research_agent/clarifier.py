"""需求澄清模块 Clarifier（动态个性化版）。
设计：
1. LLM 根据初始需求，现场挑选 2~4 个最该澄清的维度；
2. 为每个选中维度**现场生成贴合主题的个性化选项** + 引导语（而非固定死选项）；
3. 一问一答，用户可选选项字母/文字，或自由补充；
4. 回答完后，LLM 根据已收集的信息做**动态追问**（至多 1 轮），进一步锚定导向；
5. 产出结构化 Brief。
"""
import logging
import re
from typing import List, Optional

from . import llm
from .config import CLARIFY_DIMENSIONS

logger = logging.getLogger(__name__)

# 各维度的"含义"描述，供 LLM 参考生成（只描述含义，不固定选项内容）
DIMENSION_META = {
    "audience": "目标受众（谁看这份报告，决定语言深度与关注点）",
    "purpose": "用途/决策（选型、分享、立项、了解）",
    "time_range": "时间范围（近期/三年/全周期）",
    "market": "市场范围（全球/中国/欧美等）",
    "depth": "深度（快览/标准/深度）",
    "angle": "视角/立场（机会/风险/并重/竞品）",
    "must_include": "必须覆盖的玩家/观点/数据",
    "must_exclude": "必须排除或弱化的内容",
    "format": "导出格式（pdf/md/both）",
}

DEFAULT_BRIEF = {
    "audience": "通用读者",
    "purpose": "了解概况",
    "time_range": "近一年",
    "market": "全球 + 中国",
    "depth": "标准",
    "angle": "机会与风险并重",
    "must_include": [],
    "must_exclude": [],
    "format": ["pdf", "md"],
}

_FALLBACK_DIMS = [
    {"key": "audience", "question": "这份报告主要给谁看？",
     "options": ["技术团队", "管理层/决策者", "投资人", "我自己了解"],
     "hint": "补充：具体给谁看 / 需要的语言深度"},
    {"key": "purpose", "question": "你最终要用它做什么？",
     "options": ["选型决策", "写文章/分享", "立项评估", "单纯了解"],
     "hint": "补充：具体应用场景"},
    {"key": "time_range", "question": "重点关注哪个时间段？",
     "options": ["近一年", "近三年", "全周期/历史"],
     "hint": "补充：具体起止时间"},
]


def _plan_clarification(topic: str, max_n: int = 4) -> List[dict]:
    """LLM 现场生成澄清计划：挑维度 + 每个维度的个性化选项 + 引导语。"""
    sys = (
        "你是资深需求澄清专家。用户会给出一个研究主题。你的任务是为它设计一套"
        "短小精悍的澄清问卷（2~4 题），把模糊需求锚定成可执行的检索简报。\n"
        "可选维度（key: 含义）：\n"
        + "\n".join(f"- {k}: {v}" for k, v in DIMENSION_META.items()) +
        "\n\n要求：\n"
        "1. 只选 2~4 个对当前主题最关键、最能避免跑偏的维度；主题已很清楚可少问。\n"
        "2. **每个维度的选项必须贴合这个具体主题现场生成**，不要用泛泛的空话选项。"
        "例如主题是「AI 编程助手」，受众题选项应是「前端/后端开发者 / 技术管理者(CTO) / "
        "投资人/招聘方 / 产品/运营」这类，而不是「技术团队/管理层」。\n"
        "3. 每个维度给 3~4 个选项 + 1 条自由补充提示（hint）。\n"
        "4. question 用一句简洁的引导语。\n"
        "只输出 JSON：{\"dimensions\": [{\"key\": \"...\", \"question\": \"...\", "
        "\"options\": [\"...\"], \"hint\": \"...\"}]}"
    )
    user = f"研究主题：{topic}"
    try:
        data = llm.chat_json(sys, user, temperature=0.6, max_tokens=1500)
        dims = []
        for d in data.get("dimensions", [])[:max_n]:
            key = d.get("key")
            if key not in DIMENSION_META:
                continue
            opts = [str(o).strip() for o in d.get("options", []) if str(o).strip()]
            if not opts:
                continue
            dims.append({
                "key": key,
                "question": str(d.get("question", "")).strip() or DIMENSION_META[key],
                "options": opts[:4],
                "hint": str(d.get("hint", "")).strip() or "补充：其他想法",
            })
        # 去重保序
        seen, out = set(), []
        for d in dims:
            if d["key"] not in seen:
                seen.add(d["key"])
                out.append(d)
        return out or _FALLBACK_DIMS
    except llm.LLMError as e:
        logger.warning("澄清计划生成失败，使用默认维度: %s", e)
        return _FALLBACK_DIMS


def _extract_selection(user_input: str, options: List[str]) -> str:
    """从用户输入解析选择：匹配选项字母/数字或直接匹配文本。"""
    u = user_input.strip()
    m = re.match(r"^([A-Za-z]|\d+)$", u)
    if m:
        token = m.group(1)
        if token.isalpha():
            idx = ord(token.upper()) - 65
        else:
            idx = int(token) - 1
        if 0 <= idx < len(options):
            return options[idx]
    if u in options:
        return u
    # 含选项关键词（选最长的命中，避免歧义）；支持按分隔符拆词匹配
    best = None
    for opt in options:
        if not opt:
            continue
        if opt in u:
            if best is None or len(opt) > len(best):
                best = opt
            continue
        # 拆词匹配（如 "投资人/招聘方" 拆成 投资人、招聘方）
        for kw in re.split(r"[/、,，| ]", opt):
            kw = kw.strip()
            if len(kw) >= 2 and kw in u:
                if best is None or len(kw) > len(best):
                    best = opt
                break
    return best if best is not None else u  # 自由文本


def _render_question(q: dict, idx: int) -> str:
    """把一题渲染成可读文本（含选项字母与自由补充提示）。"""
    lines = [f"\nQ{idx}（{DIMENSION_META.get(q['key'], q['key'])}）{q['question']}"]
    for i, opt in enumerate(q["options"], 1):
        lines.append(f"  [{chr(64 + i)}] {opt}")
    lines.append(f"  [自由填] {q['hint']}")
    return "\n".join(lines)


def _ask_followup(topic: str, brief: dict, dims: List[dict]) -> Optional[dict]:
    """LLM 根据已收集的回答，判断是否需要追加 1 个针对性问题以锚定导向。"""
    sys = (
        "你是需求澄清专家。用户刚回答完一系列澄清问题。请判断：为了把研究方向"
        "锚定得更准，是否还有 1 个**针对性、具体**的问题值得追问？\n"
        "只在该问题能显著提升检索精度时才给；否则给 null。\n"
        "只输出 JSON：{\"question\": \"...\", \"options\": [\"...\"], \"hint\": \"...\", "
        "\"ask\": true/false}"
    )
    brief_txt = "\n".join(f"- {k}: {v}" for k, v in brief.items() if k != "topic")
    user = (
        f"研究主题：{topic}\n已收集的澄清结果：\n{brief_txt}\n\n"
        "如需要追问，请给出 1 个贴合主题的个性化问题。"
    )
    try:
        data = llm.chat_json(sys, user, temperature=0.4, max_tokens=600)
        if not data.get("ask"):
            return None
        opts = [str(o).strip() for o in data.get("options", []) if str(o).strip()]
        return {
            "question": str(data.get("question", "")).strip(),
            "options": opts[:4] or ["是", "否"],
            "hint": str(data.get("hint", "")).strip() or "补充说明",
        }
    except llm.LLMError as e:
        logger.info("动态追问跳过: %s", e)
        return None


def clarify(topic: str, interactive: bool = True, auto_brief: Optional[dict] = None):
    """执行澄清，返回结构化 Brief 字典。

    交互模式：动态生成个性化问题 → 逐题提问（含选项+自由补充）→ 动态追问。
    auto_brief 提供时跳过提问，直接使用（供非交互/测试用）。
    """
    if auto_brief:
        brief = dict(DEFAULT_BRIEF)
        brief.update(auto_brief)
        brief.setdefault("topic", topic)
        return brief

    dims = _plan_clarification(topic)
    brief = dict(DEFAULT_BRIEF)
    brief["topic"] = topic

    print(f"\n好题。让我先针对「{topic}」问几个关键问题，好让报告更对路"
          f"（可输选项字母/文字，或自由补充）👇\n")

    for idx, q in enumerate(dims, 1):
        print(_render_question(q, idx))
        while True:
            try:
                ans = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans:
                break
            print("  请回答一下这个问题（可输字母/文字，或直接敲回车跳过）：")
            try:
                ans = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                ans = ""

        choice = _extract_selection(ans, q["options"])
        key = q["key"]
        if key in ("must_include", "must_exclude"):
            brief[key] = [choice] if choice not in ("无", "无，自行判断", "自行判断") else []
        else:
            brief[key] = choice
        print()

    # 动态追问：根据全部回答再锚定一次导向
    fq = _ask_followup(topic, brief, dims)
    if fq and fq.get("question"):
        print(f"\n再确认一点，帮我锁定方向：")
        print(f"Q（追问）{fq['question']}")
        for i, opt in enumerate(fq["options"], 1):
            print(f"  [{chr(64 + i)}] {opt}")
        print(f"  [自由填] {fq['hint']}")
        try:
            ans = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans:
            fchoice = _extract_selection(ans, fq["options"])
            brief["followup"] = fchoice
            # 存回一个可被 planner/writer 消费的字段
            brief["angle_note"] = fchoice if fchoice not in ("是", "否") else None

    # 格式默认
    if "format" not in brief or not brief.get("format"):
        brief["format"] = ["pdf", "md"]

    print("\n=== Brief 摘要 ===")
    print(f"主题：{brief['topic']}")
    for q in dims:
        print(f"  · {DIMENSION_META.get(q['key'], q['key'])}：{brief.get(q['key'])}")
    if brief.get("followup"):
        print(f"  · 补充锚定：{brief['followup']}")
    print("  · 格式：pdf + md")
    print("输入【确认】开跑，或说出要修改的条目（例如：受众改成投资人）")
    try:
        confirm = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        confirm = "确认"
    if confirm and "确认" not in confirm and "开跑" not in confirm:
        print(f"(已收到修改意向：{confirm}，先按当前 Brief 继续，后续可再优化)")

    return brief
