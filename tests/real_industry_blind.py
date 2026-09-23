# -*- coding: utf-8 -*-
"""真实检索盲测·重工业量纲版（钢铁/原油/煤炭/电力/化工）。

目的（普适性矩阵·抽取层轮1）：既有盲测覆盖"消费电子/新能源"量纲，重工业带来
亿吨/万吨/千克/美元每桶/亿千瓦时/亿千瓦/万吨每年 等大宗量纲与产量/消费量/进口量
名词指标。句子逐字摘录自 Tavily 真联网返回（钢铁协会/国家统计局/能源局/化工媒体）。

运行：python3 tests/real_industry_blind.py
"""
import re
import sys
sys.path.insert(0, '.')
from research_agent.analyzer import _sentence_claims

REAL = [
    # ============ 钢铁 ============
    ("钢铁", "csteel", "2024年，全国累计生产粗钢10.05亿吨，同比下降1.7%。",
     ["10.05", "1.7"], [], "ok_产量跌幅"),
    ("钢铁", "people", "2024年粗钢表观消费量下降至8.92亿吨，较2020年的高点下降1.56亿吨。",
     ["8.92", "1.56"], [], "ok_消费量"),
    ("钢铁", "worldsteel", "2024年12月，中国粗钢产量为7597万吨，同比提高11.8%。",
     ["7597", "11.8"], [], "ok_产量增长"),
    ("钢铁", "custeel", "2024年中国人均钢材表观消费量降至601.1千克，排名全球第三。",
     ["601.1"], [], "unit_千克"),
    # ============ 原油 ============
    ("原油", "ndrc", "5日WTI和布伦特原油期货价格分别升至71.99美元/桶和75.53美元/桶。",
     ["71.99", "75.53"], [], "并列豁免_WTI布伦特并存"),
    ("原油", "tradingec", "布伦特原油期货上涨至95.25美元/桶，比前一天上涨5.26%。",
     ["95.25", "5.26"], [], "ok_油价"),
    ("原油", "stats", "12月份，规上工业原油产量1790万吨，同比增长1.4%。",
     ["1790", "1.4"], [], "ok_产量"),
    # ============ 煤炭 ============
    ("煤炭", "stats", "2024年全国原煤产量47.8亿吨，同比增长1.2%。",
     ["47.8", "1.2"], [], "ok_产量"),
    ("煤炭", "stats", "全国累计进口煤炭5.4亿吨，同比增加6828万吨、增长14.4%。",
     ["5.4", "6828", "14.4"], [], "multi_进口"),
    ("煤炭", "energy", "2024年内蒙古原煤产量12.97亿吨，居全国第一，同比增长5.4%。",
     ["12.97", "5.4"], [], "ok_排名无关"),
    # ============ 电力 ============
    ("电力", "stats", "2024年，全社会用电量98521亿千瓦时，同比增长6.8%。",
     ["98521", "6.8"], [], "unit_亿千瓦时"),
    ("电力", "nea", "2024年，全国累计发电装机容量达33.49亿千瓦，新增发电装机容量4.29亿千瓦。",
     ["33.49", "4.29"], [], "multi_装机"),
    ("电力", "nea", "2024年，全国可再生能源发电量达3.46万亿千瓦时，同比增加19%，约占全部发电量的35%。",
     ["3.46", "19", "35"], [], "multi_增长占比分工"),
    # ============ 化工 ============
    ("化工", "ndrc", "2024年3月份，硫酸产量890.0万吨、烧碱368.4万吨、纯碱322.9万吨，同比增长7.8%。",
     ["890.0", "368.4", "322.9", "7.8"], [], "multi_多产品"),
    ("化工", "zhiyan", "2024年，中国聚乙烯产能为3431万吨/年。",
     ["3431"], [], "unit_万吨每年"),
    ("化工", "zhongji", "2024年世界广义POE产能316.2万吨/年，消费量约254万吨。",
     ["316.2", "254"], [], "multi_产能消费"),
]


def nums_in(s):
    return re.findall(r"\d+(?:\.\d+)?", s)


def value_hit(got_vals, exp):
    return any(nums_in(exp) and nums_in(g) and nums_in(exp)[0] == nums_in(g)[0]
               for g in got_vals)


print("=" * 80)
print("真实检索盲测·重工业量纲版（钢铁/原油/煤炭/电力/化工）")
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
        miss_log.append((field, sent, m, got_vals))
    for n in bad_noise:
        noise_log.append((field, sent, n, got_vals))
    mark = "OK " if not missing and not bad_noise else "PART"
    print(f"[{mark}|{field}] {sent[:32]}…")
    if missing or bad_noise:
        print(f"      漏抽 {missing} 误抽 {bad_noise}  产出 {got_vals}")

print("\n" + "=" * 80)
print(f"■ 总体：期望数值召回 {hit}/{tot} = {hit/tot*100:.0f}% ｜ 误抽探针 {noise_bad} 处")
for f, (t, h, nb) in sorted(field_stat.items(), key=lambda x: -x[1][0]):
    print(f"   {f:<4} 召回 {h}/{t} ({h/t*100:4.0f}%)  误抽 {nb}")
if miss_log:
    print("\n■ 漏抽明细：")
    for (f, s, m, g) in miss_log:
        print(f"   [{f}] 期望 {m} 缺于「{s[:28]}…」产出 {g}")
if noise_log:
    print("\n■ 误抽明细：")
    for (f, s, n, g) in noise_log:
        print(f"   [{f}] {n} 本不应出现 于「{s[:28]}…」产出 {g}")
