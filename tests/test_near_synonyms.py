"""P2 回归测试：增速句识别 + 指标近义归一。

覆盖：
  1. "营收同比增长30%" → 同时抽出 营收=… 与 增长率=30%（此前 30% 会丢）
  2. "同比/复合增长率/增速" 等近义归一到一个"增长率"桶（跨来源可交叉验证）
  3. "市占率/市占" → 市场份额
  4. 净利润/净利 近义归一
"""
import sys
sys.path.insert(0, '.')
from research_agent.searcher import SourceDoc
from research_agent.analyzer import _sentence_claims, extract_evidence

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")

def pairs(sent):
    return {(c["value"], c["indicator"]) for c in _sentence_claims(sent, "u")}

# ---- 1. 增速句不再漏抽 ----
got = pairs("2024年公司营收120亿元，同比增长30%。")
check("营收句抽到 营收=120亿 与 增长率=30%",
      ("120亿元", "营收") in got and ("30%", "增长率") in got, f"got={got}")

got = pairs("该公司营收同比增长30%，净利润下滑10%。")
check("净利润下滑10% 抽出且标为跌幅(而非错挂到净利润水平量)",
      ("30%", "增长率") in got and ("10%", "跌幅") in got, f"got={got}")

# ---- 2. 近义增速词归一到"增长率"（跨来源交叉）----
docs = [
    SourceDoc(url="https://a.com/1", title="甲", content="该市场年复合增长率为25%。", credibility=0.9),
    SourceDoc(url="https://b.com/2", title="乙", content="数据显示该市场增速约25%。", credibility=0.85),
]
evs = extract_evidence(docs)
growth = [e for e in evs if e["indicator"] == "增长率"]
# schema v2(议题3)后:无年份增长率(None锚)比较期未知,不互抬置信→同一桶2源保留
# 但 adopted 单源(level 不再 high)。本断言只验证"指标归一到同一桶"。
check("复合增长率/增速 归一到同一增长率桶(同桶2源)",
      bool(growth) and len(growth) == 1 and growth[0]["n_sources"] == 2,
      f"n_sources={growth[0]['n_sources'] if growth else None}")
check("复合增长率/增速 None锚不互抬(adopted单源,schema v2议题3)",
      bool(growth) and growth[0]["adopted"]["n_sources"] == 1
      and growth[0]["level"] == "low",
      f"adopted_n={growth[0]['adopted']['n_sources'] if growth else None}")

# ---- 3. 市占率→市场份额 ----
got = pairs("该公司市占率达到32%。")
check("市占率归一为市场份额", ("32%", "市场份额") in got, f"got={got}")

# ---- 4. 净利润近义 ----
got = pairs("该公司净利达20亿元。")
check("净利归一为净利润", ("20亿元", "净利润") in got, f"got={got}")

# ---- 5. 超长句（无句号>200字）里的主张不再被整句丢弃 ----
long = "全球人工智能市场规模近年来持续扩大，各国政策与资本共同推动其演进，" * 8
d = SourceDoc(url="https://x.com", title="x", content=long + "2024年全球AI市场规模达到2500亿美元")
cl = []
from research_agent.analyzer import _extract_claims
cl = _extract_claims(d, dedup_by_url=True)
check("超长句主张不被丢弃", any(c["value"] == "2500亿美元" for c in cl), f"claims={cl}")

print(f"\n========== P2 回归：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
