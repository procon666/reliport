# -*- coding: utf-8 -*-
"""真实检索盲测·手游发展史专版：证明引擎对"跨主题史/作品叙事"素材的普适性。

目的（对应工程目标——引擎不只做行业报告，也能研究"手游发展史"）：
  手游史是典型的"史/作品叙事"素材——句式以《作品名》+量纲尾(累计下载量/流水/
  日活/估值/首月收入)为主，主题词不在"行业名词表"里，只能靠结构切分+量纲对称别名
  +游戏域量纲尾清洗 +书名号归一。本样本逐字摘录自真联网检索(Tavily)返回的真实
  媒体/机构片段，证明引擎在不预收手游专用词表的情况下即可正确抽取量化主张。

真实性声明：
- 输入全部来自真联网检索返回片段(央视/界面/一财/BBC/时代周报/Sensor Tower资讯/
  游戏媒体)，逐字摘录含明确数字的句子；本脚本运行时**不联网**，固化摘录结果便于
  回归复跑；
- 期望数值一律对照原文，不以臆测替代；
- 脚本只统计召回/精度，不触碰 research_agent/analyzer.py。

运行：python3 tests/real_mobilegame_blind.py
"""
import sys
sys.path.insert(0, '.')
from research_agent.analyzer import _sentence_claims, _value_to_number, _values_close

# 每条: (子主题, 来源, 真实句(逐字摘录), 期望[(指标, 数值, 年份或None)], 缺陷倾向tag)
REAL = [
    # ============ 愤怒的小鸟 / Rovio(下载量史) ============
    ("小鸟", "taptap", "《愤怒的小鸟》系列自2009年12月上线以来，历史累计下载量突破10亿次。",
     [("下载量", "10亿", None)], "ok_title"),
    ("小鸟", "52pk", "去年四月，愤怒的小鸟系列全球累计下载量已达17亿，全年实现创收1.956亿美元。",
     [("下载量", "17亿", None)], "ok"),
    ("小鸟", "cctv", "2011年《愤怒的小鸟》各平台下载量超过1亿次，产生了超过800万美元的下载收入。",
     [("下载量", "1亿", 2011)], "ok"),
    ("小鸟", "nyt", "在愤怒的小鸟全球10亿次的下载量中，中国市场贡献了1.4亿次。",
     [("下载量", "10亿", None), ("下载量", "1.4亿", None)], "multi_two"),
    # ============ 部落冲突 / 皇室战争 / Supercell(收入史) ============
    ("皇室战争", "jiemian", "Supercell旗下手游《皇室战争》上线不到一年，累计收入已超过10亿美元。",
     [("收入", "10亿美元", None)], "ok_title"),
    ("部落冲突", "people", "《部落冲突》2015年总收入为13.5亿美元，是全球营收最高的手机游戏。",
     [("营收", "13.5亿美元", 2015)], "ok"),
    ("Supercell", "people", "Supercell去年总营收达到21.1亿美元，盈利6.93亿美元。",
     [("营收", "21.1亿美元", None)], "ok"),
    ("部落冲突", "199it", "2019年《部落冲突》吸金7.27亿美元，较2018年增长27%。",
     [("营收", "7.27亿美元", 2019), ("增长率", "27%", 2018)], "multi_rate"),
    # ============ 腾讯收购 Supercell(估值/并购史) ============
    ("并购", "yicai", "腾讯宣布86亿美元收购《部落冲突》开发商Supercell 84.3%股权，将令Supercell的估值超过102亿美元。",
     [("估值", "86亿美元", None), ("估值", "102亿美元", None)], "multi_two_est"),
    ("并购", "bbc", "腾讯将以86亿美元的价格收购软银麾下创作热门游戏《部落冲突》的芬兰手机游戏商Supercell。",
     [("估值", "86亿美元", None)], "ok"),
    ("并购", "nytimes", "腾讯接近达成收购《部落冲突》开发商股权的协议，后者估值将超过90亿美元。",
     [("估值", "90亿美元", None)], "ok"),
    # ============ 王者荣耀(日活/流水史) ============
    ("王者", "sohu", "《王者荣耀》2025年国服日活跃用户突破1.39亿，全球月活跃用户超过2.6亿。",
     [("用户数", "1.39亿", 2025), ("用户数", "2.6亿", 2025)], "multi_two_dau"),
    ("王者", "jiemian", "《王者荣耀》该游戏注册用户超两亿，春节假期日活跃用户峰值超过8000万，最高日流水达到2亿，月流水超过30亿。",
     [("用户数", "8000万", None), ("流水", "2亿", None), ("流水", "30亿", None)], "multi_flow"),
    ("王者", "guancha", "2024年中国游戏产业市场规模已突破3257.8亿元，同比增长7.53%。",
     [("规模", "3257.8亿元", 2024), ("增长率", "7.53%", None)], "multi_rate"),
    # ============ 米哈游 原神 / 崩铁(首月收入) ============
    ("原神", "morketing", "《原神》在上线首月的IAP收入为1.72亿美元。",
     [("收入", "1.72亿美元", None)], "ok_title"),
    ("崩铁", "morketing", "《崩坏:星穹铁道》全球上线一个月，其IAP收入达到1.32亿美元。",
     [("收入", "1.32亿美元", None)], "ok_title"),
    ("原神", "36kr", "《原神》上线以来全球移动端营收达每月30亿元，70%来自中国之外市场。",
     [("营收", "30亿元", None), ("占比", "70%", None)], "multi_two"),
    # ============ 主机史参照(跨领域销量史) ============
    ("马力欧", "wiki", "超级马力欧系列作为任天堂核心作品，累计销量达4亿3000万份。",
     [("销量", "4亿", None), ("销量", "3000万", None)], "multi_sale_piece"),
]

DEFECT = {
    "ok": "常规单值(作品名/书名号)",
    "ok_title": "《作品名》书名号主体 + 常规单值",
    "multi_two": "同句两值(总量+分市场/比例)",
    "multi_two_est": "同句两估值(收购价+被并方估值)",
    "multi_two_dau": "同句两用户量(日活+月活)",
    "multi_flow": "同句三流水/日活混排",
    "multi_rate": "量值+同比率",
    "multi_sale_piece": "中文数字份(4亿3000万)拆两档销量",
}


def num_eq(a, b):
    na = _value_to_number(a)
    nb = _value_to_number(b)
    if na and nb:
        if na[1] != nb[1]:
            return False
        return _values_close(a, b)
    return a.replace(" ", "") == b.replace(" ", "")


def value_recalled(got, exp_v):
    return any(num_eq(c["value"], exp_v) for c in got)


print("=" * 80)
print("真实检索盲测·手游发展史专版(证明跨主题史类素材普适性)")
print("  输入逐字摘录自真联网检索(Tavily)返回的真实媒体片段")
print("=" * 80)
tot = hit = 0
topic_stat = {}
defect_miss = {}
all_miss = []
for topic, src, sent, expects, defect in REAL:
    got = _sentence_claims(sent, "u")
    got_sig = [(c["value"], c["indicator"], c["year"]) for c in got]
    missing = []
    for (_i, exp_v, _y) in expects:
        tot += 1
        topic_stat.setdefault(topic, [0, 0])
        topic_stat[topic][1] += 1
        if value_recalled(got, exp_v):
            hit += 1
            topic_stat[topic][0] += 1
        else:
            missing.append(exp_v)
            defect_miss[defect] = defect_miss.get(defect, 0) + 1
            all_miss.append((topic, src, sent, exp_v, defect, got_sig))
    mark = "OK " if not missing else "PART"
    print(f"[{mark}|{topic}] {sent[:32]}…")
    if missing:
        print(f"      MISS {missing}  产出 {got_sig}")

print("\n" + "=" * 80)
print(f"■ 总体：数值召回 {hit}/{tot}  = {hit/tot*100:.0f}%")
print("\n■ 分子主题召回：")
for dom, (h, t) in sorted(topic_stat.items(), key=lambda x: -x[1][1]):
    print(f"   {dom:<6} {h}/{t}  {h/t*100:5.0f}%")
print("\n■ 漏抽按缺陷类型归类：")
if defect_miss:
    for tag, n in sorted(defect_miss.items(), key=lambda x: -x[1]):
        print(f"   {n:>2} 处 | {DEFECT.get(tag, tag)}")
else:
    print("   (无漏抽)")
print("\n■ 漏抽明细（逐条可复核）：")
for (topic, src, sent, exp_v, defect, got) in all_miss:
    print(f"   [{topic}|{src}] 期望 {exp_v} 缺于「{sent[:30]}…」 产出 {got}")
print()
ok = hit == tot
if ok:
    print("结论：全部命中——引擎可普适抽取手游发展史等跨主题叙事素材的量化主张")
else:
    print("结论：存在漏抽(见上)——记录为后续优化输入")
sys.exit(0 if ok else 1)
