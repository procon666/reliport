# -*- coding: utf-8 -*-
"""排名/份额/CAGR 句式专项回归（Counterpoint/IDC/市场报告 —— 真联网检索修复固化）。

背景（Tavily 真联网检索手机/服务器/机器人市场报告，句子逐字摘录）：
  排名句式（'三星以19%的份额位居第二'）与 CAGR 预测句（'2025年X亿美元，2030年
  将达Y亿美元，复合年增长率Z%'）是市场报告高频结构。修复：
  1. "份额"短式收录（→市场份额）——'苹果以43%的份额领跑'原被远处'同比下降'
     劫持成跌幅（43% 应为市场份额）；
  2. 序数（第二/第三/第九）不得误抽为量值——中文序数天然不落入量值提取；
  3. CAGR/复合年增长率多值句：规模(水平量)+增长率(%) 分家并存。
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

def vals(sent):
    return [v for v, _ in rows(sent)]

# ---- 1. 份额短式（Counterpoint）----
g = rows("苹果以43%的份额领跑智能手机市场收入，但其收入同比下降11%。")
check("份额短式:43%→市场份额(非跌幅)", ("43%", "市场份额") in g and ("11%", "跌幅") in g, f"got={g}")
g = rows("苹果公司占据了整个高端智能手机市场67%的份额，相比2023年的72%略有下降。")
check("份额长式:67%/72%并存", ("67%", "市场份额") in g and "72%" in vals("苹果公司占据了整个高端智能手机市场67%的份额，相比2023年的72%略有下降。"), f"got={g}")
g = rows("三星在出货量方面继续领先市场，份额为19%。")
check("份额:19%→市场份额", ("19%", "市场份额") in g, f"got={g}")

# ---- 2. 排名句式不抽序数 ----
for s in ["苹果位居第二，其出货量同比增长5%。",
          "小米排名第三，出货量同比增长3%，得益于中国市场的强劲表现。",
          "排名第九的是华为，它的市场份额为4%，出货量约为4880万台，同比增长36%。"]:
    vs = vals(s)
    check(f"序数不抽:{s[:10]}…", not any(v in ("第二", "第三", "第九", "2", "3", "9") for v in vs), f"got={vs}")

# ---- 3. CAGR 预测多值句 ----
g = rows("IDC预测，2025年中国具身智能机器人用户支出规模预计超过14亿美元，到2030年将飙升至770亿美元，年均复合增长率高达94%。")
check("CAGR:14亿/770亿/94%三值并存",
      ("14亿美元", "规模") in g and ("770亿美元", "规模") in g and ("94%", "增长率") in g, f"got={g}")
g = rows("2024年服务器市场估值为1031.2亿美元，预计到2032年将达到2131.355亿美元，复合年增长率为9.5%。")
check("CAGR:1031.2/2131.355/9.5%三值",
      ("1031.2亿美元", "估值") in g and ("2131.355亿美元", "估值") in g and ("9.5%", "增长率") in g, f"got={g}")
g = rows("2024年北美以36.8%的份额领先市场，其次是亚太地区的28.7%。")
check("份额并列:36.8%/28.7%分主体并存",
      ("36.8%", "市场份额") in g and ("28.7%", "市场份额") in g, f"got={g}")

# ---- 4. 回归护栏 ----
g = rows("vivo以3%的同比增长率位居第四，市场份额为8%。")
check("增长率与份额分家:3%/8%", ("3%", "增长率") in g and ("8%", "市场份额") in g, f"got={g}")

print(f"\n========== 排名/CAGR 专项回归：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
