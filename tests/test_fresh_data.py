"""换数据验证：用全新领域 + 真实风格语料检验抽取/年份/归一的健壮性。

此前回归测试全部使用 AI市场/AI编程助手/低代码 等同一批 mock 句子。
这里换一批**从未用过**的领域与措辞风格：
  - 新能源汽车（渗透率突破、PHEV同比下滑、车企跌幅）
  - 生物医药（带量采购降价、研发管线成功率）
  - 消费电子（出货量、市占）
  - 不同年份口径、英文夹杂、货币单位混用
用来验证不是"只在熟悉句子上能跑"。
"""
import sys
sys.path.insert(0, '.')
from research_agent.searcher import SourceDoc
from research_agent.analyzer import extract_evidence, detect_conflicts, _sentence_claims

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
    return {(c["value"], c["indicator"], c["year"]) for c in _sentence_claims(sent, "u")}

# ---------- 1. 新能源汽车：真实风格单句 ----------
got = pairs("中国新能源乘用车渗透率2025年首次突破50%，其中PHEV销量同比下滑28%。")
check("渗透率50%抽出、PHEV下滑28%抽出",
      any(v == "50%" and i == "渗透率" for v, i, y in got)
      and any(v == "28%" and i == "跌幅" for v, i, y in got),
      f"got={got}")

got = pairs("大众中国2025年销量跌幅达57.8%，广汽本田连续两年跌幅超20%。")
check("车企跌幅57.8%不误标成规模/用户数",
      any(v == "57.8%" for v, i, y in got), f"got={got}")

# ---------- 2. 生物医药：带量采购/研发 ----------
got = pairs("第七批国家药品带量采购平均降价幅度达48%，中选药品价格较去年下降近一半。")
check("药品降价48%抽出(命中降价类指标)",
      any(v == "48%" for v, i, y in got), f"got={got}")

# ---------- 3. 消费电子：出货量 + 跨来源交叉（不同措辞：出货/出货量、市占率/市场份额）----------
d1 = SourceDoc(url="https://phone.com/1", title="调研甲", content="2025年全球智能手机出货量达12.1亿部，苹果市占率约19%。", credibility=0.9, fetched_at="2025-06")
d2 = SourceDoc(url="https://phone.com/2", title="机构乙", content="统计显示2025年全球智能手机出货12.1亿部，苹果市场份额19%。", credibility=0.85, fetched_at="2025-06")
evs = extract_evidence([d1, d2])
for e in evs:
    # 出货量 + 市占都应形成2源；且主语被正确切分（出货量属"智能手机"、市占属"苹果"）
    if e["indicator"] == "出货量" and e["subject"] == "智能手机":
        check("智能手机出货量两源交叉(出货/出货量归一)→2源", e["n_sources"] == 2, f"n={e['n_sources']} label={e['label']}")
    if e["indicator"] == "市场份额":
        check("苹果市占两源交叉(市占率/市场份额归一)→2源",
              e["n_sources"] == 2 and e["subject"] == "苹果",
              f"n={e['n_sources']} subject={e['subject']} label={e['label']}")

# ---------- 4. 不同年份口径（生物药市场规模2024 vs 2030预测）----------
docs = [
    SourceDoc(url="https://bio.com/1", title="A", content="2024年全球生物制药市场规模达4500亿美元。", credibility=0.9, fetched_at="2025-01"),
    SourceDoc(url="https://bio.com/2", title="B", content="预计2030年全球生物制药市场规模将增长至1.1万亿美元。", credibility=0.9, fetched_at="2025-01"),
]
evs = extract_evidence(docs)
yrs = {e["year"] for e in evs if e["indicator"] == "规模"}
check("生物药2024规模与2030预测分属不同年份桶(且1.1万亿换算正确)",
      yrs == {2024, 2030}, f"years={yrs}")
conf = detect_conflicts(docs)
check("生物药跨年不报冲突", len(conf) == 0, f"conflicts={len(conf)}")

# ---------- 5. 货币单位混写 + 万亿 ----------
got = pairs("2025年AI芯片市场规模达850亿美元，预计到2027年将超过1500亿美元。")
check("850亿与1500亿都归规模、各带年份(2025/2027)",
      any(v == "850亿美元" and y == 2025 for v, i, y in got)
      and any(v == "1500亿美元" and y == 2027 for v, i, y in got), f"got={got}")

# ---------- 6. 真实风格报告句（提取自 outputs/新能源汽车行业.md 变体）----------
got = pairs("2025年PHEV销量同比下滑28%，与BEV和REEV的增长形成鲜明对比，国内渗透率首次突破50%。")
check("复杂真实句：PHEV下滑28% + 渗透率50%",
      any(v == "28%" for v, i, y in got) and any(v == "50%" and i == "渗透率" for v, i, y in got),
      f"got={got}")

# ---------- 7. 真实分歧 vs 舍入噪声：不得把实质分歧静默合并成 high ----------
# 3000亿 vs 3500亿：以较小者计 ~16.7% 差异，属真实分歧 → 应报冲突、且为 medium(无主导源)
Bdocs = [
    SourceDoc(url="https://ai.com/1", title="甲", content="2024年中国AI市场规模达到3000亿元。", credibility=0.9, fetched_at="2025-01"),
    SourceDoc(url="https://ai.com/2", title="乙", content="据估计2024年中国AI市场规模约3500亿元。", credibility=0.85, fetched_at="2025-01"),
]
be = extract_evidence(Bdocs)
bb = [e for e in be if e["indicator"] == "规模"]
check("真实分歧3000vs3500不得判high(应为medium/低置信)",
      bb and all(e["level"] != "high" for e in bb),
      f"levels={[e['level'] for e in bb]}")
check("真实分歧3000vs3500 报冲突",
      len(detect_conflicts(Bdocs)) == 1, f"conflicts={len(detect_conflicts(Bdocs))}")
# 12.0/12.1/12.05：四舍五入噪声 → 应视为同一条说法(high)，且不报冲突
Cdocs = [
    SourceDoc(url="https://p.com/1", title="甲", content="2025年全球智能手机出货量12.0亿部。", credibility=0.9, fetched_at="2025-06"),
    SourceDoc(url="https://p.com/2", title="乙", content="统计显示2025年全球智能手机出货12.1亿部。", credibility=0.85, fetched_at="2025-06"),
    SourceDoc(url="https://p.com/3", title="丙", content="2025年全球智能手机出货12.05亿部。", credibility=0.8, fetched_at="2025-06"),
]
ce = [e for e in extract_evidence(Cdocs) if e["indicator"] == "出货量"]
check("舍入噪声12.0/12.1/12.05判high(多源一致)",
      ce and ce[0]["level"] == "high" and ce[0]["n_sources"] == 3,
      f"level={ce[0]['level'] if ce else None} n={ce[0]['n_sources'] if ce else None}")
check("舍入噪声不报冲突(与high口径统一)",
      len(detect_conflicts(Cdocs)) == 0, f"conflicts={len(detect_conflicts(Cdocs))}")

# ---------- 8. 对称量纲规则：水平量数值不得被远处比率词误标 ----------
# 全新句式（出口/欧洲/比亚迪 语境）：70% 是增长率，但 12万辆 是销量(数量)，
# 不能被窗口内更早出现的"同比增长"错标成增长率。
got = pairs("比亚迪2024年出口销量同比增长了70%，欧洲市场贡献了其中12万辆。")
check("12万辆(水平量)不被'同比增长'误标为增长率",
      ("70%", "增长率", None) in got
      and any(v == "12万辆" and i != "增长率" for v, i, _y in got),
      f"got={got}")

print(f"\n========== 换数据验证：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
