# -*- coding: utf-8 -*-
"""新主题盲测自检（引入此前语料/测试未覆盖的领域与句式）。

目的：验证"转载镜像修复 + 主张抽取"对不同主题是否依然成立，避免只会在已见过的
语料上表现好。本脚本引入 16+ 个此前语料(claim_corpus)/既有测试均未覆盖的新领域，
并**如实汇报**：能对多少、错在哪、属于可接受还是暴露真短板。

新主题（刻意挑选，规避语料里已有的 半导体/咖啡/新能源/机器人/游戏/电池/芯片）：
军工、航天卫星、冷链物流、宠物经济、数据中心、智算、院线票房、医美、
露营装备、跨境电商物流、特种钢、在线问诊、珠宝零售、储能系统出货、
运动鞋服、考研/职业教育。

每类给一句"值不同、句式不同"的话，期望抽取 (主体, 指标, 数值, 年份)。
评分沿用 eval 口径：主张级(数值+年份)命中 / 句子级全对(再加指标名+主体)。
此外补两块镜像置信自检：逐字镜像折叠(防刷High)、独立源不误并，用新主题复验。
"""
import sys
sys.path.insert(0, '.')
from research_agent.analyzer import _sentence_claims, extract_evidence, _assign_origins
from research_agent.searcher import SourceDoc

# (句子, 期望[(主体, 指标, 数值, 年份)])
NEW_CASES = [
    # —— 军工/航天卫星 ——
    ("2024年全球航天发射市场规模达到80亿美元，可回收火箭推动成本下降。",
     [("航天发射", "规模", "80亿美元", 2024)]),
    ("我国商业航天市场预计2027年规模将突破5000亿元。",
     [("商业航天", "规模", "5000亿元", 2027)]),
    # —— 冷链物流 ——
    ("2023年中国冷链物流市场规模约5170亿元，冷库容量持续扩容。",
     [("冷链物流", "规模", "5170亿元", 2023)]),
    # —— 宠物经济 ——
    ("2024年国内宠物食品市场规模达到1500亿元，猫粮占比最高。",
     [("宠物食品", "规模", "1500亿元", 2024)]),
    # —— 数据中心/智算 ——
    ("全国智算中心总算力2025年预计超过300EFLOPS。",
     [("智算中心", "总算力", "300EFLOPS", 2025)]),
    # —— 院线票房 ——
    ("2024年国庆档电影总票房约28亿元，观影人次创近年新高。",
     [("电影", "总票房", "28亿元", 2024)]),
    # —— 医美 ——
    ("2023年中国医美市场规模达2700亿元，光电类项目增速最快。",
     [("医美", "规模", "2700亿元", 2023)]),
    # —— 露营装备 ——
    ("2024年露营装备线上销售额同比下滑了12%。",
     [("露营装备", "跌幅", "12%", 2024)]),
    # —— 跨境电商物流 ——
    ("2024年跨境电商物流市场规模约1.2万亿元，海外仓数量突破500个。",
     [("跨境电商物流", "规模", "1.2万亿元", 2024)]),
    # —— 特种钢/新材料 ——
    ("2024年全球高温合金市场规模约450亿美元，航空航天为主要需求。",
     [("高温合金", "规模", "450亿美元", 2024)]),
    # —— 在线问诊/互联网医疗 ——
    ("2023年中国在线问诊市场规模达到900亿元，渗透率逐步提升。",
     [("在线问诊", "规模", "900亿元", 2023)]),
    # —— 珠宝零售 ——
    ("2024年黄金珠宝消费额同比增长了18%。",
     [("黄金珠宝", "增长率", "18%", 2024)]),
    # —— 储能系统出货 ——
    ("2024年全球储能系统出货量达230GWh。",
     [("储能系统", "出货量", "230GWh", 2024)]),
    # —— 运动鞋服 ——
    ("2025年中国运动鞋服市场规模预计达4600亿元。",
     [("运动鞋服", "规模", "4600亿元", 2025)]),
    # —— 考研/职业教育 ——
    ("2025年考研报名人数为388万人，连续两年下降。",
     [("考研", "报名人数", "388万", 2025)]),
    # —— 咖啡以外：现制茶饮+新式早点 ——
    ("2024年中国预包装食品赛道整体规模约1200亿元，健康零食占比提升。",
     [("预包装食品", "规模", "1200亿元", 2024)]),
]

def _num_eq(a, b):
    from research_agent.analyzer import _value_to_number, _values_close
    na = _value_to_number(a); nb = _value_to_number(b)
    if na and nb:
        if na[1] != nb[1]:
            return False
        return _values_close(a, b)
    return a.replace(" ", "") == b.replace(" ", "")

def _subj_ok(exp, got):
    if not exp:
        return True
    g = (got or "").strip()
    return bool(g) and (exp in g or g in exp)

print("=" * 72)
print("一、新主题主张抽取盲测")
print("=" * 72)
hit = miss = sent_ok_cnt = 0
miss_detail = []
for i, (sent, expects) in enumerate(NEW_CASES, 1):
    got = _sentence_claims(sent, "u")
    got_txt = [(c["value"], c["indicator"], c["subject"]) for c in got]
    sent_ok = True
    ok_flags = []
    for (exp_s, exp_i, exp_v, exp_y) in expects:
        val_hit = any(_num_eq(c["value"], exp_v) and c["year"] == exp_y for c in got)
        full_hit = any(_num_eq(c["value"], exp_v) and c["year"] == exp_y
                       and c["indicator"] == exp_i and _subj_ok(exp_s, c["subject"])
                       for c in got)
        if val_hit:
            hit += 1
        else:
            miss += 1
            sent_ok = False
            miss_detail.append((sent, exp_s, exp_i, exp_v, "值缺", got_txt))
        if not full_hit:
            sent_ok = False
            if val_hit:
                miss_detail.append((sent, exp_s, exp_i, exp_v, "指标/主体不一致", got_txt))
        ok_flags.append(("v" if val_hit else "×") + ("+" if full_hit else "-"))
    if sent_ok:
        sent_ok_cnt += 1
    mark = "OK " if sent_ok else "PART"
    print(f"[{mark}] 例{i:02d} {sent[:26]}… → {got_txt}  期望命中{''.join(ok_flags)}")

print(f"\n主张级命中: {hit}/{len(NEW_CASES)}   句子级全对: {sent_ok_cnt}/{len(NEW_CASES)}")
if miss_detail:
    print("\n—— 未命中/不一致明细（如实）——")
    for (sent, exp_s, exp_i, exp_v, why, got_txt) in miss_detail:
        print(f"  · {why} | 期望({exp_s}|{exp_i}|{exp_v}) | 句:{sent[:40]} | 得:{got_txt}")

print("\n" + "=" * 72)
print("二、新主题下的镜像/独立源置信复验")
print("=" * 72)
def mk(url, content, cred=0.5):
    return SourceDoc(url=url, title=url, content=content,
                     from_fetch=True, fetched_at="2024-06-01", credibility=cred)
# (a) 新主题逐字镜像刷量 → 应折1 origin → low
core = ("2024年中国军工电子市场规模达到620亿元，信息化装备占比持续提升，"
        "多家院所加大研发投入，行业整体保持较高景气度，市场前景被普遍看好。"
        "据行业协会统计，相关配套产业链规模也在稳步扩张，带动上游元器件需求。")
mirror_docs = [mk(f"https://m{i}-jun-aggregator.info/p", core, 0.4) for i in range(6)]
og = _assign_origins(mirror_docs)
evs = [e for e in extract_evidence(mirror_docs)]
mirror_ok = len(og) == 1
print(f"[{'OK' if mirror_ok else 'FAIL'}] 军工新主题：6 家逐字镜像 → origin数={len(og)}"
      f"（{'折叠为单一来源' if mirror_ok else '未折叠'}）")
ev0 = next((e for e in evs), None)
print(f"      → 该主题任一证据 level={ev0['level'] if ev0 else None} "
      f"n_sources={ev0['n_sources'] if ev0 else None}")

# (b) 独立源（不同措辞长文）不误并 —— 用"智算/宠物"新主题
indep = [
    ("2024年国内智算中心规模达300亿元，主要来自运营商与云厂商的资本开支，"
     "东部一线城市新建项目集中落地，带动服务器与交换机需求放量。"),
    ("据多方统计，国内面向AI训练的智能计算中心在2024年投入约300亿元，"
     "其中头部互联网平台自建为主，第三方IDC亦有参与。"),
    ("行业测算显示，2024年中国智能算力中心整体市场规模为300亿元量级，"
     "政府与国企主导的大型项目占比过半。"),
]
docs = [mk(f"https://ca{i}.com/r", t, 0.85 - i * 0.05) for i, t in enumerate(indep)]
og2 = _assign_origins(docs)
indep_ok = len(og2) == 3
print(f"[{'OK' if indep_ok else 'FAIL'}] 智算新主题：3 篇独立长文 → origin数={len(og2)}"
      f"（{'不误并' if indep_ok else '误并了！'}）")

print("\n" + "=" * 72)
total_checks = miss == 0 and mirror_ok and indep_ok
print(f"自检结论：{'全部通过 ✅' if total_checks else '存在未命中/待改进项 ⚠️'} "
      f"(主张漏抽 {miss} 句，镜像折叠{'✓' if mirror_ok else '✗'}，独立源误并{'无' if indep_ok else '有'})")
