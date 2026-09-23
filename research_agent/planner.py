"""规划层 Planner：根据 Brief + 主题，拆解出需要调研的子问题与检索查询。

体裁感知：主题命中"史/叙事"体裁（见 topic_style.is_narrative_topic）时，
按**时间分期/发展阶段**拆解查询（起源 → 各时期 → 里程碑 → 现状），
而不是行业通用模板（现状/玩家/份额/趋势）——否则"手游发展史"这类主题会被
规划成"手游市场现状"，检索回来的资料也全是现状，报告必然跑偏。

行业主题走原模板一字不改；确定性部分（体裁判断、退化查询选择）可单测。
"""
import logging
from typing import List

from . import llm
from .topic_style import is_narrative_topic

logger = logging.getLogger(__name__)


# ---- 行业/市场体裁的规划提示（原模板，一字不改）----
_MARKET_PLAN_SYS = (
    "你是检索规划助手。根据研究简报，拆解出检索要用的搜索查询语句。\n"
    "要求：\n"
    "1. 每个查询是一句能在搜索引擎中使用的自然语言关键词组合\n"
    "2. 查询之间要覆盖不同侧面（现状、玩家、数据、趋势、风险、案例等）\n"
    "3. 输出最多 {max_queries} 条，中文查询\n"
    "4. 必须考虑简报里的受众/时间/市场/视角/必含项"
)

# ---- 史/叙事体裁的规划提示：强制按时间分期拆解 ----
_NARRATIVE_PLAN_SYS = (
    "你是检索规划助手。该研究主题属于「历史/演变」类，读者要的是**时间脉络**："
    "这个事物是如何一步步演变成今天的。\n"
    "要求：\n"
    "1. 查询必须按**时间分期/发展阶段**拆解，覆盖从起源到当下的完整时间轴；"
    "每个查询锚定一个时期（可用年份区间、代际或阶段名命名）\n"
    "2. 每个时期查询要能同时带回：该时期的代表性产品/事件/公司、关键量化数据"
    "（用户量/收入/下载量/销量/规模等）、以及转折原因\n"
    "3. 另安排查询覆盖：起源与早期雏形、关键转折与里程碑事件、技术与平台换代、"
    "当下现状与最新数据\n"
    "4. 输出最多 {max_queries} 条中文查询，可直接用于搜索引擎\n"
    "5. 不要规划'现状/竞争格局/市场份额'这类纯行业现状查询——本主题要的是历史脉络"
)


def plan_queries(brief: dict, max_queries: int = 6) -> List[str]:
    """根据 Brief 生成一组待检索的查询语句。

    返回去重后的查询列表。失败时退化为基于主题的基础查询
    （退化查询也按体裁选择，不会把史类主题退回行业三件套）。
    """
    topic = brief.get("topic", "")
    narrative = is_narrative_topic(topic)
    sys_tpl = _NARRATIVE_PLAN_SYS if narrative else _MARKET_PLAN_SYS
    sys = sys_tpl.format(max_queries=max_queries)
    user = f"研究简报：\n{topic}\n请输出 JSON：{{\"queries\": [\"...\", \"...\"]}}"
    try:
        data = llm.chat_json(sys, user, temperature=0.3)
        qs = [q for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
        qs = qs[:max_queries]
        if qs:
            return _dedup(qs)
    except llm.LLMError as e:
        logger.warning("规划查询失败，使用基础查询: %s", e)

    return _dedup(_fallback_queries(topic))


def _fallback_queries(topic: str) -> List[str]:
    """LLM 规划失败时的退化查询，按体裁选择。

    史/叙事 → 分期导向（起源/里程碑/代表作/历年数据/现状）；
    行业/市场 → 原"现状/玩家/对比"三件套（一字不改）。
    """
    if is_narrative_topic(topic):
        return [
            f"{topic} 起源 早期 雏形 背景",
            f"{topic} 发展阶段 分期 里程碑事件 代表作",
            f"{topic} 关键转折点 技术换代 平台迁移",
            f"{topic} 历年关键数据 用户 收入 规模 变化",
            f"{topic} 现状 最新进展 未来趋势",
        ]
    return [
        f"{topic} 现状 市场规模 趋势",
        f"{topic} 主要玩家 对比",
        f"{topic} 优劣势 挑战",
    ]


def _dedup(items: List[str]) -> List[str]:
    seen, out = set(), []
    for i in items:
        key = i.strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(i.strip())
    return out
