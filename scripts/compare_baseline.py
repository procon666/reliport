# -*- coding: utf-8 -*-
"""对照实验：裸 LLM 直接写 vs 本系统管线（同一素材、同一对账器）。

动机：证明"走管线"比"直接把素材丢给 LLM"更可信——**不是自说自话**，而是用
     同一个对账器（factory_audit：数字回素材找原词）对两份报告做客观核验。

用法：
  python3 scripts/compare_baseline.py <tag> "<主题>"
  例：python3 scripts/compare_baseline.py phonev "2026年全球智能手机市场出货量与格局"

产出：
  outputs/pipes/<tag>/_baseline.md   裸 LLM 报告
  outputs/pipes/<tag>/_compare.json  对比结果
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research_agent import llm
from scripts.product_pipeline import factory_audit


def baseline_report(topic, srcdir, max_chars=60000):
    """裸 LLM：素材正文直接丢给 LLM，**不给**"无原词不写"的约束。

    这是"直接让 AI 写行业报告"的典型用法——只给资料，不给对账/示弱纪律。
    """
    files = sorted(glob.glob(os.path.join(srcdir, "*.txt")))
    docs = "\n\n".join(open(f, encoding="utf-8").read() for f in files)
    SYS = ("你是资深行业分析师。请根据用户提供的资料，写一份**详实**的行业分析报告，"
           "包含：摘要、市场规模与增长、竞争格局、趋势判断、风险提示。"
           "要求数据丰富、论述专业、有明确结论。")
    usr = f"研究主题：{topic}\n\n参考资料：\n{docs[:max_chars]}"
    return llm.chat(SYS, usr, temperature=0.4, max_tokens=6000)


def baseline_no_docs(topic):
    """裸 LLM **不给素材**（靠模型记忆）——"直接问 AI 写行业报告"的另一种常见用法。
    这条最能体现"编数"：数字来自模型记忆，很可能在素材里找不到原词。"""
    SYS = ("你是资深行业分析师。请根据你的知识，写一份详实的行业分析报告，"
           "包含：摘要、市场规模与增长、竞争格局、趋势判断、风险提示。")
    usr = f"研究主题：{topic}"
    return llm.chat(SYS, usr, temperature=0.4, max_tokens=6000)


def audit_stats(md_path, srcdir):
    """对账：数字回素材找原词。返回统计（与出厂对账同口径）。"""
    stat, rows = factory_audit(md_path, srcdir)
    total = len(rows)
    ok = stat.get("OK", 0)
    calc = stat.get("概算", 0)
    mismatch = stat.get("疑不符", 0)
    nocite = stat.get("无出处", 0)
    return {
        "数字总数": total, "OK": ok, "概算": calc,
        "疑不符": mismatch, "无出处": nocite,
        "可核对数": ok + calc,
        "可核对率": round((ok + calc) / total, 3) if total else 0.0,
        "问题数": mismatch + nocite,
    }


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "phonev"
    topic = sys.argv[2] if len(sys.argv) > 2 else "2026年全球智能手机市场出货量与格局"
    srcdir = f"outputs/pipes/{tag}/src"
    n = len(glob.glob(srcdir + "/*.txt"))
    print(f"主题：{topic}\n素材：{srcdir}（{n} 篇）\n")

    # 组 1：裸 LLM（有素材）
    print("== 组 1：裸 LLM + 素材（无约束 prompt）==")
    base_md = baseline_report(topic, srcdir)
    base_p = f"outputs/pipes/{tag}/_baseline.md"
    open(base_p, "w", encoding="utf-8").write(base_md)
    print(f"   报告 {len(base_md)} 字 → {base_p}")

    # 组 2：裸 LLM（无素材，靠记忆）
    print("== 组 2：裸 LLM 无素材（靠模型记忆）==")
    nod_md = baseline_no_docs(topic)
    nod_p = f"outputs/pipes/{tag}/_baseline_nodocs.md"
    open(nod_p, "w", encoding="utf-8").write(nod_md)
    print(f"   报告 {len(nod_md)} 字 → {nod_p}")

    # 组 3：本系统
    sys_p = f"outputs/pipes/{tag}/{tag}_report.md"
    print(f"== 组 3：本系统管线 ==\n   {sys_p}")

    # 同一对账器核验三份（都用同一批素材做基准）
    b = audit_stats(base_p, srcdir)
    nd = audit_stats(nod_p, srcdir)
    s = audit_stats(sys_p, srcdir)

    print("\n== 对账结果（同一对账器：数字回素材找原词）==")
    print(f"{'指标':<10}{'裸LLM+素材':>12}{'裸LLM无素材':>12}{'本系统':>12}")
    for k in ["数字总数", "OK", "概算", "疑不符", "无出处", "可核对率", "问题数"]:
        print(f"{k:<10}{str(b[k]):>12}{str(nd[k]):>12}{str(s[k]):>12}")

    json.dump({"topic": topic, "baseline_with_docs": b,
               "baseline_no_docs": nd, "system": s},
              open(f"outputs/pipes/{tag}/_compare.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n结果已存 outputs/pipes/{tag}/_compare.json")


if __name__ == "__main__":
    main()
