# -*- coding: utf-8 -*-
"""真实检索盲测·新量纲压力版：生物医药 / 金融行情 / 体育 三大 analyzer 未验证领域。

目的（普适性矩阵 · 抽取层压力维度）：
  既有语料/盲测覆盖的是"行业报告式"量纲（亿元/GWh/万台/份额/%），这三类真实领域
  带来完全不同的量纲与句式压力：
  - 生物医药：患者"例/位"、临床期数、生存期"月"、集采降幅、药企销售额；
  - 金融行情：指数"点位/涨跌幅"、成交额、基金规模/只数、汇率"元/基点"、PMI 指数点；
  - 体育数据：命中率、命中数"记/球"、出手"次"、转会费"欧元/身价"。

真实性声明：
- 输入全部来自 Tavily 真联网检索返回的真实片段（药企公告/21财经/证券时报/新浪/
  医保局/维基/NBA 数据站等），逐字摘录含明确数字的句子；
- 期望数值与"不应误抽"均对照原文，不以臆测替代；
- 脚本只统计召回与误抽，不触碰 research_agent/analyzer.py。

运行：python3 tests/real_pressure_blind.py
"""
import re
import sys
sys.path.insert(0, '.')
from research_agent.analyzer import _sentence_claims

# 每条: (领域, 来源, 真实句, 期望数值[], 不应误抽的数值[], 缺陷倾向)
REAL = [
    # ============ 生物医药 ============
    ("医药", "hutchmed", "SACHI研究共有211位患者被随机分配接受治疗。",
     ["211"], [], "unit_位患者"),
    ("医药", "hengrui", "该III期研究共纳入389例患者，随机分配并接受治疗。",
     ["389"], [], "unit_例"),
    ("医药", "hengrui", "Cam-chemo组193例，PBO-chemo组196例。",
     [], [], "edge_两臂对照无指标名词"),
    ("医药", "hengrui", "与对照组相比，Cam-chemo显著延长了中位总生存期，达到27.4个月对比15.5个月。",
     ["27.4", "15.5"], [], "month_生存期"),
    ("医药", "hunan", "默沙东公布的业绩显示，PD-1当年为其贡献了110亿美元收入，同比增幅达54.6%。",
     ["110", "54.6"], [], "收入_增幅"),
    ("医药", "21jingji", "礼来与信达生物的合作产品PD-1信迪利单抗2024年全年销售额达5.26亿美元，约合38亿元人民币。",
     ["5.26", "38"], ["1"], "代号数字误抽"),
    ("医药", "pharnex", "从现场流传的价格信息来看，降幅最大的为石药欧意的美金刚，降幅达到98.7%。",
     ["98.7"], [], "ok_降幅"),
    ("医药", "wuhan", "第九次集采共有262家企业的382个产品参与投标，205家企业的266个产品获中选资格。",
     [], ["262", "382", "205", "266"], "集采企业产品数(计数口径剔除)"),
    ("医药", "cnpharm", "拟中选的385个药品中，超50个药品拟中选价格降幅超过90%。",
     ["90"], ["50", "385"], "超N个数量误抽"),
    # ============ 金融行情 ============
    ("金融", "stcn", "截至午间收盘，A股市场成交额突破1.7万亿元。",
     ["1.7"], [], "ok_成交额"),
    ("金融", "stcn", "上证指数涨1.18%，深证成指涨2.25%，创业板指数涨3.63%。",
     ["1.18", "2.25", "3.63"], [], "multi_index"),
    ("金融", "oeeee", "截至2024年末，全市场存续12360只公募基金，净值规模合计31.9万亿元。",
     ["31.9"], ["12360"], "只数误抽"),
    ("金融", "oeeee", "货币型基金370只，合计规模达13万亿元，占总规模40.8%。",
     ["13", "40.8"], ["370"], "只数误抽"),
    ("金融", "cls", "基金总净值为32.83万亿元，较2023年末增长18.93%。",
     ["32.83", "18.93"], [], "ok_规模增长"),
    ("金融", "sina", "8月人民币对美元中间价累计升值66个基点。",
     ["66"], [], "unit_基点"),
    ("金融", "sina", "当日人民币汇率中间价为1美元对人民币6.7828元。",
     ["6.7828"], ["1"], "汇率_1误抽"),
    ("金融", "tradingecon", "8月份制造业pmi从7月份的四个月低点50.9上升至51.5。",
     ["50.9", "51.5"], [], "pmi指数"),
    # ============ 体育数据 ============
    ("体育", "sohu", "2015-16赛季库里场均出手11.2次，三分命中率高达45.4%，总计命中402记三分球。",
     ["11.2", "45.4", "402"], [], "体育出手命中"),
    ("体育", "sohu", "2018-19赛季哈登在78场比赛中以场均13.2次出手命中378记三分。",
     ["13.2", "378"], ["78"], "体育出手命中_场次剔"),
    ("体育", "wiki", "2017年8月内马尔以2.22亿欧元由巴塞隆拿转会至巴黎圣日耳门。",
     ["2.22"], [], "ok_转会费"),
    ("体育", "zhihu", "德布劳内22岁以2500万欧元身价加盟沃尔夫斯堡。",
     ["2500"], ["22"], "ok_身价"),
]

DEFECT = {
    "unit_位患者": "患者以'位'计量，analyzer 无此量纲",
    "unit_例": "患者以'例'计量，analyzer 无此量纲",
    "unit_例_two": "两臂各N例（对照臂结构）",
    "edge_两臂对照无指标名词": "边界:两臂'组193例/196例'无任何指标名词(无患者/入组),单句无法归属——设计中可接受不抽",
    "month_生存期": "临床终点以'月'计（PFS/OS），非市场量纲",
    "收入_增幅": "药企收入+同比增幅，收入词可能未绑定",
    "代号数字误抽": "PD-1 产品代号中的数字 1 被当独立数值误抽",
    "ok_降幅": "常规降幅句（应命中）",
    "集采企业产品数(计数口径剔除)": "企业数/产品数（家/个）为计数口径，analyzer 剔除不抽",
    "超N个数量误抽": "'超50个药品'的 50 被就近绑成降幅",
    "ok_成交额": "常规成交额句（应命中）",
    "multi_index": "多指数涨跌幅并列",
    "只数误抽": "基金'只数'（存续12360只）被当规模抽",
    "ok_规模增长": "常规规模+增长率句（应命中）",
    "unit_基点": "汇率升贬以'基点'计，analyzer 无此单位",
    "汇率_1误抽": "汇率表述'1美元对人民币X'把 1 误抽",
    "pmi指数": "PMI 指数点位变化（50.9→51.5）",
    "命中数_记": "命中'记'（三分球计数单位）",
    "体育出手命中": "体育技术统计:出手次数/命中率/命中数(2015-16跨年区间不得误抽)",
    "体育出手命中_场次剔": "体育:出手次数+命中数,出场'78场'须剔除",
    "ok_转会费": "转会费欧元（应命中）",
    "ok_身价": "身价欧元（应命中）",
}


def nums_in(s: str):
    return re.findall(r"\d+(?:\.\d+)?", s)


def value_hit(got_vals, exp):
    """期望值是否出现在产出值里（宽松数值匹配，忽略单位）。"""
    return any(nums_in(exp) and nums_in(got) and nums_in(exp)[0] == nums_in(got)[0]
               for got in got_vals)


print("=" * 80)
print("真实检索盲测·新量纲压力版（生物医药 / 金融行情 / 体育）")
print("  输入逐字摘录自 Tavily 真联网检索返回的真实片段")
print("=" * 80)
tot = hit = 0
noise_bad = 0
field_stat = {}
miss_log = []
noise_log = []
for field, src, sent, expects, noise, defect in REAL:
    got = _sentence_claims(sent, "u")
    got_vals = [c["value"] for c in got]
    missing = [e for e in expects if not value_hit(got_vals, e)]
    bad_noise = [n for n in noise if value_hit(got_vals, n)]
    tot += len(expects)
    hit += len(expects) - len(missing)
    noise_bad += len(bad_noise)
    field_stat.setdefault(field, [0, 0, 0])
    field_stat[field][0] += len(expects)
    field_stat[field][1] += len(expects) - len(missing)
    field_stat[field][2] += len(bad_noise)
    for m in missing:
        miss_log.append((field, sent, m, defect, got_vals))
    for n in bad_noise:
        noise_log.append((field, sent, n, defect, got_vals))
    mark = "OK " if not missing and not bad_noise else "PART"
    print(f"[{mark}|{field}] {sent[:36]}…")
    if missing or bad_noise:
        print(f"      期望漏抽 {missing}  误抽 {bad_noise}  产出 {got_vals}")

print("\n" + "=" * 80)
print(f"■ 总体：期望数值召回 {hit}/{tot} = {hit/tot*100:.0f}% ｜ 误抽探针 {noise_bad} 处")
print("\n■ 分领域（召回/期望/误抽）：")
for f, (t, h, nb) in sorted(field_stat.items(), key=lambda x: -x[1][0]):
    print(f"   {f:<4} 召回 {h}/{t} ({h/t*100:4.0f}%)  误抽 {nb}")
print("\n■ 漏抽按缺陷类型归类：")
if miss_log:
    dm = {}
    for (f, s, m, d, g) in miss_log:
        dm[d] = dm.get(d, 0) + 1
    for d, n in sorted(dm.items(), key=lambda x: -x[1]):
        print(f"   {n:>2} 处 | {DEFECT.get(d, d)}")
else:
    print("   (无漏抽)")
print("\n■ 误抽明细（应抽到却多出的值，膨胀风险）：")
for (f, s, n, d, g) in noise_log:
    print(f"   [{f}] {n} 本不应出现 于「{s[:30]}…」产出 {g}")
