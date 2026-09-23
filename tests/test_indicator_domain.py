# -*- coding: utf-8 -*-
"""新量纲指标域专项回归（医药/金融宏观/体育 —— 真实压力盲测修复固化）。

背景（真联网检索医药/金融/体育三领域，21 句逐字摘录，见 real_pressure_blind.py）：
  analyzer 既有量纲体系是"行业报告式"（亿元/GWh/万台/份额/%）。这些真实领域用
  **名词计数/计量**表达量化事实，数值本身无物理单位，靠名词指标绑定：
  - 医药：'纳入389例患者'→患者数；'中位总生存期27.4个月'→生存期（'个月'时长）；
  - 金融宏观：'升值66个基点'→基点（'个基点'是计量复合，不得被数量规则剔）；
    'PMI从50.9上升至51.5'→PMI（指数点，两值并存）；
  - 体育：'场均出手11.2次'→出手数、'命中率45.4%'→命中率、'命中402记三分'→命中数；
    出场'78场'、跨年区间'2015-16赛季'的 16 均非量值须剔除。

修复：_CLAIM_KEYWORDS/_INDICATOR_CANON/_LEVEL_INDICATORS/_RATE_INDICATOR_CANON
收录 患者/入组/受试者→患者数、生存期/PFS→生存期、基点、PMI/pmi→PMI、
出手→出手数、命中→命中数、命中率；_is_metadata_digit 补"跨年度区间后段"
（2015-16赛季的16）剔除、'场'量词剔除、'个基点/个百分点'计量例外。
"""
import sys
sys.path.insert(0, '.')
from research_agent.analyzer import _sentence_claims

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    tag = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{tag}] {name}" + (f"  {detail}" if detail else ""))

def rows(sent):
    return [(c["value"], c["indicator"]) for c in _sentence_claims(sent, "x")]

# ---- 1. 医药：患者数（例/位）----
g = rows("该III期研究共纳入389例患者，随机分配并接受治疗。")
check("患者数:389例→患者数|389", ("389", "患者数") in g, f"got={g}")
g = rows("SACHI研究共有211位患者被随机分配接受治疗。")
check("患者数:211位→患者数|211", ("211", "患者数") in g, f"got={g}")

# ---- 2. 医药：临床终点生存期（月）----
g = rows("与对照组相比，Cam-chemo显著延长了中位总生存期，达到27.4个月对比15.5个月。")
check("生存期:27.4/15.5并存", ("27.4", "生存期") in g and ("15.5", "生存期") in g, f"got={g}")
g = rows("联合疗法组的中位PFS为8.2个月，化疗组则为4.5个月。")
check("生存期:PFS 8.2/4.5并存", ("8.2", "生存期") in g and ("4.5", "生存期") in g, f"got={g}")

# ---- 3. 金融宏观：基点 / PMI ----
g = rows("8月人民币对美元中间价累计升值66个基点。")
check("基点:66个基点→基点|66", ("66", "基点") in g, f"got={g}")
g = rows("8月份制造业pmi从7月份的四个月低点50.9上升至51.5。")
check("PMI:50.9/51.5并存→PMI", ("50.9", "PMI") in g and ("51.5", "PMI") in g, f"got={g}")

# ---- 4. 体育：出手/命中率/命中数 分工，场次与跨年区间剔除 ----
g = rows("2015-16赛季库里场均出手11.2次，三分命中率高达45.4%，总计命中402记三分球。")
check("体育:11.2出手数/45.4%命中率/402命中数",
      ("11.2", "出手数") in g and ("45.4%", "命中率") in g and ("402", "命中数") in g, f"got={g}")
check("体育:跨年区间'2015-16'的16不抽", "16" not in [v for v, _ in g], f"got={g}")
g = rows("2018-19赛季哈登在78场比赛中以场均13.2次出手命中378记三分。")
check("体育:13.2出手数/378命中数",
      ("13.2", "出手数") in g and ("378", "命中数") in g, f"got={g}")
check("体育:出场'78场'不抽", "78" not in [v for v, _ in g], f"got={g}")

# ---- 5. 回归护栏：行业句式不受新指标词影响 ----
g = rows("2024年中国游戏产业市场规模已突破3257.8亿元，同比增长7.53%。")
check("行业规模/增长不受影响", ("3257.8亿元", "规模") in g and ("7.53%", "增长率") in g, f"got={g}")
g = rows("比亚迪2024年销量达160万辆，其中出口占比约20%。")
check("汽车销量/占比不受影响", ("160万辆", "销量") in g, f"got={g}")
g = rows("该产品临床试验显示有效率较高，未披露具体人数。")
check("无数字句子不产生主张", g == [], f"got={g}")

print(f"\n========== 新量纲指标域专项回归：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
