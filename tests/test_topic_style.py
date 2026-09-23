# -*- coding: utf-8 -*-
"""主题体裁感知回归测试（史/叙事 vs 行业/市场）。

背景：引擎默认链路（planner/writer 提示词）面向"行业市场调研"，会把"手游发展史"
这类**史/叙事**主题跑成"行业市场分析"（历史只剩一张小表）。修复 = 体裁感知：
- topic_style.is_narrative_topic：确定性识别（无 LLM），供 planner/writer 选择模板；
- planner._fallback_queries：LLM 规划失败时的退化查询也按体裁选择，
  史类不再退回行业"现状/玩家/对比"三件套。

固化内容（防隐式回退）：
  1. 体裁识别正例：手游发展史/中国汽车工业发展史/华为创业历程/人工智能的演进/
     诺基亚兴衰/柯达百年沉浮/苹果公司发展史（公司传记） → True
  2. 体裁识别负例：AI编程助手市场/新能源汽车行业/低代码平台市场/半导体竞争格局
     → False（行业主题绝不能误判成编年叙事）
  3. 史类退化查询 → 分期导向（起源/发展阶段/里程碑/历年数据）
  4. 行业退化查询 → 原"现状/市场规模/玩家"三件套（一字不改，回归护栏）
"""
import sys
sys.path.insert(0, '.')
from research_agent.planner import _fallback_queries
from research_agent.topic_style import is_narrative_topic

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

# ---- 1. 体裁识别：史/叙事正例 ----
# 含公司传记（单一主体时间脉络=编年结构天然适用，缺口验证已覆盖）
for t in ["手游发展史", "中国汽车工业发展史", "华为创业历程", "人工智能的演进",
          "诺基亚兴衰史", "柯达的百年沉浮", "共享单车兴衰", "小米发家史",
          "苹果公司发展史", "任天堂百年兴衰史"]:
    check(f"史类识别:{t}", is_narrative_topic(t))

# ---- 2. 体裁识别：行业/市场负例（绝不误判）----
# 注：'技术综述/研报'类主题经实测由行业模板承接（技术+市场+应用结构本就重叠，
# 如'大语言模型技术综述'产出含技术架构/演进方向/行业应用章节），不应判成编年史。
for t in ["AI编程助手市场调研", "新能源汽车行业分析", "低代码平台市场",
          "2025年中国咖啡市场", "半导体行业竞争格局", "智能手机出货量预测",
          "大语言模型技术综述", "固态电池技术研报"]:
    check(f"行业不误判:{t}", not is_narrative_topic(t))

# ---- 3. 史类退化查询：分期导向 ----
fb = _fallback_queries("手游发展史")
check("史类退化查询为分期导向", len(fb) == 5 and
      any("起源" in q for q in fb) and any("发展阶段" in q for q in fb) and
      any("里程碑" in q for q in fb) and any("历年" in q for q in fb),
      f"got={fb}")
check("史类退化不含行业三件套",
      not any("市场份额" in q or "主要玩家" in q for q in fb), f"got={fb}")

# ---- 4. 行业退化查询：原三件套一字不改（回归护栏）----
fb_m = _fallback_queries("AI编程助手市场")
check("行业退化查询保持原样",
      fb_m == ["AI编程助手市场 现状 市场规模 趋势",
               "AI编程助手市场 主要玩家 对比",
               "AI编程助手市场 优劣势 挑战"], f"got={fb_m}")

print(f"\n========== 主题体裁感知回归：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
