"""阶段1(簇C) + 阶段2(簇B) 专项回归：句尾数值主体还原 + 元数据数字杂音清理。

覆盖两类此前导致召回损失的缺陷：
  簇C - 元数据数字被当数值主张：月份(7月)、半/季度标记(H1/Q2)的 1/2 会单独成
        token，误占 (指标,年份) 键、把真正的量值(123.6GWh/219.2GWh)顶掉；
  簇B - "实体主体+虚词"句尾数值(新能源约104万辆 / 其中燃油车只剩约54万辆)
        切不出主题，回退整句 → 不同主体的值撞同一脏主题键被句内去重顶掉。
"""
import sys
sys.path.insert(0, '.')
from research_agent.analyzer import _sentence_claims, _numeric_tokens

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")

def claims(sent):
    return _sentence_claims(sent, "u")

def sigs(sent):
    return {(c["value"], c["indicator"], c["year"], c["subject"]) for c in _sentence_claims(sent, "u")}

# ---------- 簇C: 元数据数字杂音清理 ----------
vals = [t["value"] for t in _numeric_tokens("2025年H1的123.6GWh增长至2026年H1的219.2GWh")]
check("H1的1不作为数值token", "1" not in vals, f"vals={vals}")
check("123.6GWh/219.2GWh整体保留", "123.6GWh" in vals and "219.2GWh" in vals, f"vals={vals}")

vals = [t["value"] for t in _numeric_tokens("比亚迪7月41.9万辆，同比增21.8%")]
check("月份7不作为数值token", "7" not in vals, f"vals={vals}")

vals = [t["value"] for t in _numeric_tokens("2025年Q2的45.8%提升至47.5%")]
check("Q2的2不作为数值token", "2" not in vals, f"vals={vals}")

# 月份区间起点数字：'1-5月'/'1-6月'/'1-12月' 的起始 '1' 是时间坐标非量值。
# 若不剔除，句内出现"装机/出货"等水平量指标时该 1 会就近绑上、多抽一条"装机量|1"。
vals = [t["value"] for t in _numeric_tokens("2026年1-5月全国太阳能发电新增装机59.59GW")]
check("1-5月的起始1不作为数值token", "1" not in vals, f"vals={vals}")
got = claims("2026年1-5月全国太阳能发电新增装机59.59GW，同比下降69.9%，其中5月单月仅8.68GW")
check("59.59GW/8.68GW照常抽出且无脏'装机量|1'",
      all(c["value"] not in ("1", "1GW") for c in got)
      and any(c["value"] == "59.59GW" for c in got)
      and any(c["value"] == "8.68GW" for c in got),
      f"got={[(c['value'], c['indicator']) for c in got]}")

# 型号数字不应被误删(宽松器H/S/Q白名单,不含型号场景)
vals = [t["value"] for t in _numeric_tokens("iPhone15销量达2400万台")]
check("iPhone15的15不被误删", "15" in vals or "2400万台" in vals, f"vals={vals}")

# 簇C: 电学单位判为水平量 → 219.2GWh不就近错绑成'增长率'
got = claims("这三家企业合计出货量从2025年H1的123.6GWh增长至2026年H1的219.2GWh，同比增长77.3%")
check("123.6GWh就近绑出货量(非增长率)", any(c["value"] == "123.6GWh" and c["indicator"] == "出货量" for c in got),
      f"got={[(c['value'], c['indicator']) for c in got]}")

# ---------- 簇B: 句尾数值主体还原 ----------
got = claims("2026年8月国内狭义乘用车零售158万辆，新能源约104万辆，渗透率有望达到65.8%")
check("104万辆主体切为'新能源'", any(c["value"] == "104万辆" and c["subject"] == "新能源" for c in got),
      f"got={[(c['value'], c['subject']) for c in got]}")
check("158万辆与104万辆同存(主题分离不被顶掉)",
      any(c["value"] == "158万辆" for c in got) and any(c["value"] == "104万辆" for c in got),
      f"got={[c['value'] for c in got]}")

got = claims("158万辆同比下滑21.7%，其中燃油车只剩约54万辆，降幅超过40%")
check("54万辆主体切为'燃油车'", any(c["value"] == "54万辆" and c["subject"] == "燃油车" for c in got),
      f"got={[(c['value'], c['subject']) for c in got]}")

got = claims("2026上半年全球储能电池出货量，宁德时代以125GWh位居第一，是排名第二的亿纬锂能(48GWh)的2.6倍")
subj_map = {c["value"]: c["subject"] for c in got}
check("125GWh主题=宁德时代", subj_map.get("125GWh") == "宁德时代", f"subj_map={subj_map}")
check("48GWh主题=亿纬锂能(排名修饰被剥)", subj_map.get("48GWh") == "亿纬锂能", f"subj_map={subj_map}")

# 承前省略的'较去年同期'应返回空主体(不产生假主题)
got = claims("2026年6月光伏新增装机规模12.48GW，较去年同期的14.36GW，同比下降13.09%")
check("承前省略不产生'去年'类假主体", all(c["subject"] != "去年" for c in got),
      f"got={[(c['value'], c['subject']) for c in got]}")

# ---------- 方向1(水平量专用整句指标): 句尾比率词不劫持分句水平量值 ----------
# "市场份额也从45.8%→47.5%"里句尾'市场份额'会抢走整句指标；水平量值 219.2GWh 应
# 仍继承句首'出货量'(靠 _pick_level_indicator),而非被丢弃/错绑。
got = claims("这三家企业合计出货量从2025年H1的123.6GWh增长至2026年H1的219.2GWh，同比增长77.3%；市场份额也从45.8%提升至47.5%")
val_ind = {(c["value"], c["indicator"]) for c in got}
check("219.2GWh就近绑出货量(水平量整句指标不被市场份额劫持)",
      ("219.2GWh", "出货量") in val_ind, f"got={sorted(val_ind)}")
check("45.8%仍归市场份额(比率值不受水平量整句指标影响)",
      ("45.8%", "市场份额") in val_ind, f"got={sorted(val_ind)}")

# ---------- 方向2: 装机短式收录,累计/单月装机体量并列 ----------
got = claims("2026年1-5月全国太阳能发电新增装机59.59GW，同比下降69.9%，其中5月单月仅8.68GW")
check("8.68GW(5月单月装机)被抽出", any(c["value"] == "8.68GW" for c in got),
      f"got={[(c['value'], c['indicator']) for c in got]}")
check("59.59GW与8.68GW并存(累计+单月)",
      any(c["value"] == "59.59GW" for c in got) and any(c["value"] == "8.68GW" for c in got),
      f"got={[c['value'] for c in got]}")

# ---------- 阶段2假阳性防线: 指标词/时间词不被当主体 ----------
for sent in ["渗透率约80%，环比提升2个百分点",
             "同比增长70%，其中欧洲贡献12万辆",
             "新能源车渗透率有望达到50%，其中纯电占35%"]:
    for c in claims(sent):
        bad = c["subject"] in ("渗透率", "增长率", "同比增长", "占比")
        if bad:
            check(f"主体不是指标词: {sent[:14]}", False, f"subject={c['subject']}")
            break
    else:
        check(f"主体非指标词({sent[:14]}…)", True)

print(f"\n========== 阶段1/2 专项回归：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
