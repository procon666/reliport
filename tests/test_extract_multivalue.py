"""P0 抽取重构回归测试：单句多值主张 + 数值↔就近指标配对。

旧版"一句一条"的缺陷：
  A. 一句话含多个"指标-数值"对时只抽 1 条，其余数据丢失；
  B. 数值与指标可能错配（"规模2500亿…年增25%"被抽成 增长率=2500亿）。

本测试锁定 P0 重构后的正确行为。
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
    """取 (value, indicator) 集合便于断言。"""
    return {(c["value"], c["indicator"]) for c in _sentence_claims(sent, "u")}

# ---------- A. 单句多值：不再丢数据 ----------
got = pairs("2024年公司营收为120亿元，同比增长30%，毛利率达到45%。")
check("营收句含多个指标值(营收120亿/毛利率45%被抽出)",
      ("120亿元", "营收") in got and ("45%", "毛利率") in got, f"got={got}")

got = pairs("该公司毛利率为45%，净利率为15%，净利润达10亿元。")
check("利润三兄弟各自成条(45/15/10亿)",
      ("45%", "毛利率") in got and ("15%", "净利率") in got and ("10亿元", "净利润") in got,
      f"got={got}")

got = pairs("市场份额为32%，较去年上升了5个百分点，用户数突破8000万。")
check("份额与用户数双抽出",
      ("32%", "市场份额") in got and ("8000万", "用户数") in got, f"got={got}")

# ---------- B. 数值↔就近指标：不再张冠李戴 ----------
got = pairs("全球AI市场2024年规模2500亿美元，到2030年预计增至12000亿美元，年复合增长率25%。")
check("规模=2500亿、增长率=25%（不把12000错配给增长率）",
      ("2500亿美元", "规模") in got and ("25%", "增长率") in got and ("12000亿美元", "增长率") not in got,
      f"got={got}")

got = pairs("2024年全球AI市场规模达2500亿美元，同比增长20%。")
check("规模句只抽规模=2500亿（20%无近指标不误配）",
      ("2500亿美元", "规模") in got, f"got={got}")

# ---------- C. 单值句行为不回退（与旧版一致）----------
got = pairs("全球低代码市场规模2024年达50亿美元，行业快速发展。")
check("单值规模句仍正确", ("50亿美元", "规模") in got, f"got={got}")

got = pairs("北美市场占比最高达到45%。")
check("占比句正确", ("45%", "占比") in got, f"got={got}")

# ---------- D. 端到端：多值抽取不虚增"独立来源" ----------
# 同来源一句话含规模+占比两条，不应让 extract_evidence 的来源数虚高
d = SourceDoc(url="https://x.com/1", title="X", content="2024年全球AI市场规模达2500亿美元，北美占比45%。",
              credibility=0.9)
ev = extract_evidence([d])
scale = [e for e in ev if e["indicator"] == "规模" and e["subject"] == "AI"]
if scale:
    check("同源多主张不虚增规模来源数", scale[0]["n_sources"] == 1 and scale[0]["level"] == "low",
          f"n_sources={scale[0]['n_sources']}")
else:
    check("同源多主张不虚增规模来源数", False, "未抽到规模证据")

print(f"\n========== P0 重构回归：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
