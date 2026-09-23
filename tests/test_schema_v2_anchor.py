# -*- coding: utf-8 -*-
"""schema v2 步骤1：相对值侧信道锚（跨源合并不判假冲突/不假背书）。

背景：E1 后相对值（增长率/跌幅/涨幅）year 一律 None。跨源合并时若不同报告期的
增长率（"2025 同比+10%" vs "2026 同比+10.5%"）值接近，会被 _values_close 并成
一簇、互相背书抬到 high——实为两年不同数据。锚字段 {report_year, base_year}
从 claim 原文解析，仅用于聚合层比较（extract_evidence/detect_conflicts 聚簇锚门），
不触碰 _sentence_claims 的 year 口径（抽取层冻结）。

规则（评审拍板：结构化锚 + 锚不同不并簇）：
- 锚相同（或均无锚=现状）→ 允许 close 并簇；
- 锚不同 → 分开呈现，不判冲突、不互相抬升置信。
"""
import sys

sys.path.insert(0, ".")
from research_agent.analyzer import extract_evidence, _resolve_anchor
from research_agent.searcher import SourceDoc

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def make(url, title, content, cred=0.9):
    return SourceDoc(url=url, title=title, content=content,
                     from_fetch=True, fetched_at="2026-01-15", credibility=cred)


def growth_ev(evs):
    for e in evs:
        if e["indicator"] == "增长率":
            return e
    return None


# ---- 锚解析 ----
check("锚:2025同比→(2025,2024)",
      _resolve_anchor("2025年A公司营收同比增长10%") ==
      {"report_year": 2025, "base_year": 2024})
check("锚:2026同比→(2026,2025)",
      _resolve_anchor("2026年A公司营收同比增长10.5%") ==
      {"report_year": 2026, "base_year": 2025})
check("锚:较2023显式基期→(2025,2023)",
      _resolve_anchor("2025年A公司营收较2023年增长50%") ==
      {"report_year": 2025, "base_year": 2023})
check("锚:无年份句子→None", _resolve_anchor("公司营收同比增长10%") is None)

# ---- 用例A: 不同报告期、值接近(10% vs 10.5%)→ 锚门拆开,不假背书 high ----
evA = growth_ev(extract_evidence([
    make("https://a.com/1", "A1", "2025年A公司营收同比增长10%。", 0.9),
    make("https://b.com/2", "B2", "2026年A公司营收同比增长10.5%。", 0.9),
]))
check("A:不同锚拆2簇", evA is not None and len(evA["clusters"]) == 2,
      f"got={len(evA['clusters']) if evA else None}")
check("A:不因值接近并簇成high(adopted单源)",
      evA is not None and evA["adopted"]["n_sources"] == 1,
      f"got={evA['adopted']['n_sources'] if evA else None}")

# ---- 用例B: 同锚一致 → 仍并簇多源互证 ----
evB = growth_ev(extract_evidence([
    make("https://a.com/1", "A1", "2025年A公司营收同比增长10%。", 0.9),
    make("https://b.com/2", "B2", "2025年A公司营收同比增长10%。", 0.9),
]))
check("B:同锚并1簇", evB is not None and len(evB["clusters"]) == 1,
      f"got={len(evB['clusters']) if evB else None}")
check("B:多源互证 high",
      evB is not None and evB["level"] == "high" and evB["adopted"]["n_sources"] == 2,
      f"got={evB['level'] if evB else None}")

# ---- 用例C: 水平量(无锚)向后兼容 → 3源一致仍 high ----
evC = [e for e in extract_evidence([
    make("https://a.com/1", "A", "2024年全球AI市场规模达到2500亿美元。", 0.9),
    make("https://b.com/2", "B", "报告显示全球AI市场规模2024年为2500亿美元。", 0.9),
    make("https://c.com/3", "C", "数据显示全球AI市场2024年规模达2500亿美元。", 0.9),
]) if e["indicator"] == "规模"]
check("C:水平量3源仍high", evC and evC[0]["level"] == "high",
      f"got={evC[0]['level'] if evC else None}")

print(f"\n========== schema v2 锚字段：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
