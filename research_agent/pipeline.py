"""主流程 pipeline：串联 澄清 → 规划 → 检索 → 分析 → 撰写 → 导出。
"""
import logging
from typing import Optional

from . import clarifier, planner, renderer, searcher, writer
from .config import settings

logger = logging.getLogger(__name__)


def run(
    topic: str,
    interactive: bool = True,
    auto_brief: Optional[dict] = None,
    output_format: str = None,
    max_queries: int = 6,
) -> dict:
    """执行一次完整调研，返回结果信息。"""
    fmt = output_format or settings.default_format

    # 1. 澄清
    brief = clarifier.clarify(
        topic, interactive=interactive, auto_brief=auto_brief
    )
    print(f"\n[1/5] 澄清完成 → 开始规划检索方案")

    # 2. 规划
    queries = planner.plan_queries(brief, max_queries=max_queries)
    print(f"[2/5] 规划检索 {len(queries)} 个查询")

    # 3. 检索 + 抓取
    pipeline = searcher.SearchPipeline(
        searcher.Searcher(provider=settings.search_provider)
    )
    all_docs = []
    for q in queries:
        print(f"   · 检索: {q}")
        # 每个查询控制抓取数量，平衡质量与耗时/token
        docs = pipeline.collect(q, max_results=3, min_content=100)
        all_docs.extend(docs)
    print(f"[3/5] 检索+抓取完成，共收集 {len(all_docs)} 条来源")

    # 4. 撰写
    print("[4/5] 综合撰写报告（调用大模型）…")
    md = writer.write_report(brief, all_docs)

    # 5. 导出
    print(f"[5/5] 渲染导出（{fmt}）…")
    result = renderer.render(md, brief.get("topic", topic), fmt=fmt)
    print("\n完成！产出文件：")
    for kind, p in result["produced"]:
        print(f"   · {kind}: {p}")

    return {"brief": brief, "md": md, "result": result}
