"""P1 回归测试：口径年份维度 + 时效进入裁决。

覆盖：
  A. 跨年份数值（2024 实际 vs 2030 预测）不应被误报为"冲突/分歧"；
  B. 显式年份主张与"无年份"主张应能归并、交叉验证；
  C. reason 透出"数据较新（YYYY-MM）"的时效信息；
  D. 同指标、同年份桶被拆开时各自带年份标签。
"""
import sys
sys.path.insert(0, '.')
from research_agent.searcher import SourceDoc
from research_agent.analyzer import extract_evidence, detect_conflicts

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")

def mk(url, title, content, cred=0.9, fetched="2024-06-01"):
    return SourceDoc(url=url, title=title, content=content,
                     credibility=cred, fetched_at=fetched)

# ---- A. 跨年份不算冲突 ----
docs = [
    mk("https://a.com/1", "甲", "2024年全球AI市场规模达2500亿美元。", cred=0.9, fetched="2024-06"),
    mk("https://b.com/2", "乙", "预计2030年全球AI市场规模将增长至12000亿美元。", cred=0.9, fetched="2024-06"),
]
conf = detect_conflicts(docs)
check("2024实际 vs 2030预测：不报冲突", len(conf) == 0, f"conflicts={len(conf)}")
evs = extract_evidence(docs)
labels = {e["label"] for e in evs}
check("跨年份拆成两个独立证据项",
      any("2024年" in l for l in labels) and any("2030年" in l for l in labels),
      f"labels={labels}")

# ---- B. 显式年份 + 无年份 可交叉验证 ----
docs = [
    mk("https://a.com/1", "甲", "2024年全球AI市场规模达2500亿美元。", cred=0.92, fetched="2024-06"),
    mk("https://b.com/2", "乙", "统计显示全球AI市场规模为2500亿美元。", cred=0.9, fetched="2024-07"),
]
evs = extract_evidence(docs)
best = max(evs, key=lambda e: e["n_sources"])
check("无年份归并到2024桶→2来源high", best["n_sources"] == 2 and best["level"] == "high",
      f"n_sources={best['n_sources']} level={best['level']}")

# ---- C. reason 透出时效 ----
check("reason 含'数据较新(2024-07)'", "2024-07" in best["reason"], f"reason={best['reason']}")

# ---- D. 单一来源、显式年份的标签携带年份 ----
docs = [mk("https://a.com/1", "甲", "2024年全球AI市场规模达2500亿美元。", cred=0.9)]
evs = extract_evidence(docs)
check("单源显式年份标签带(2024年)", any("2024年" in e["label"] for e in evs),
      f"labels={[e['label'] for e in evs]}")

# ---- E. 一句跨年份("该市场24亿…预计2030增至100亿")：都归"规模"且各自带年份 ----
from research_agent.analyzer import _sentence_claims
cs = _sentence_claims("报告称2024年该市场约24亿美元，预计2030年将增至100亿美元。", "u")
by_val = {c["value"]: c for c in cs}
check("24亿/100亿都判为规模", by_val.get("24亿美元", {}).get("indicator") == "规模"
      and by_val.get("100亿美元", {}).get("indicator") == "规模", f"claims={cs}")
check("24亿→2024、100亿→2030",
      by_val.get("24亿美元", {}).get("year") == 2024
      and by_val.get("100亿美元", {}).get("year") == 2030, f"claims={cs}")
check("不把'预计'当指标", all(c["indicator"] != "预计" for c in cs), f"claims={cs}")

print(f"\n========== P1 回归：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
