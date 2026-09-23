# -*- coding: utf-8 -*-
"""schema v2 步骤3：议题3/4/5 落地（None 锚门 + 量纲门与修正识别 + verdict kind）。

拍板结论（跑分真实 case 驱动）：
- 议题3：None 锚相对值（无年份增长率）比较期未知 → 不互证不判冲突（分开呈现/低置信）；
- 议题4：_values_close 加量纲类门（'10万吨' vs '1706.86亿元' 不同类不并，异物同桶
  残留判'键失真'而非真分歧）；'上修至/修正为'簇修正优先（采纳最新不报冲突）；
- 议题5：裁决 reason 结构化 —— evidence["verdict"]={kind, detail}。
"""
import sys

sys.path.insert(0, ".")
from research_agent.analyzer import extract_evidence
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


def make(url, title, content, cred=0.9, d="2026-06-01"):
    return SourceDoc(url=url, title=title, content=content,
                     from_fetch=True, credibility=cred, fetched_at=d)


# 1) None 锚相对值 → 分开呈现 / low（不互证不判冲突）
evs = extract_evidence([
    make("https://a.com/1", "A", "公司营收同比增长10%。", 0.9),
    make("https://b.com/2", "B", "公司营收同比增长10%。", 0.9),
])
e = next((x for x in evs if x["indicator"] == "增长率"), None)
check("None锚:level=low", e is not None and e["level"] == "low", f"got={e['level'] if e else None}")
check("None锚:kind=分开呈现",
      e is not None and e["verdict"]["kind"] == "分开呈现",
      f"got={e['verdict']['kind'] if e else None}")
check("None锚:不互抬(adopted单源)",
      e is not None and e["adopted"]["n_sources"] == 1,
      f"got={e['adopted']['n_sources'] if e else None}")

# 2) 量纲键失真: 同桶 10万吨 vs 1706.86亿元 → 不并,kind=键失真
evs2 = extract_evidence([
    make("https://a.com/1", "A", "2024年生产量为10万吨。", 0.9),
    make("https://b.com/2", "B", "2024年规模达1706.86亿元。", 0.9),
])
e2 = next((x for x in evs2 if x["indicator"] == "规模"), None)
check("量纲门:两值不并簇",
      e2 is not None and len(e2["clusters"]) == 2,
      f"got={len(e2['clusters']) if e2 else None}")
check("量纲门:kind=键失真(异物同桶非真分歧)",
      e2 is not None and e2["verdict"]["kind"] == "键失真",
      f"got={e2['verdict']['kind'] if e2 else None}")

# 3) 修正优先: 上修至 → 采纳修正值,kind=修正
evs3 = extract_evidence([
    make("https://a.com/1", "A", "2024年公司营收为100亿元。", 0.9, "2025-01-01"),
    make("https://b.com/2", "B", "公司2024年营收上修至120亿元。", 0.9, "2026-06-01"),
])
e3 = next((x for x in evs3 if x["indicator"] == "营收"), None)
check("修正:adopted=修正值120亿",
      e3 is not None and e3["adopted"]["value"] == "120亿元",
      f"got={e3['adopted']['value'] if e3 else None}")
check("修正:kind=修正",
      e3 is not None and e3["verdict"]["kind"] == "修正",
      f"got={e3['verdict']['kind'] if e3 else None}")

# 4) 真冲突: 同量纲同年两值 → 分歧类(不并、无主导)
evs4 = extract_evidence([
    make("https://a.com/1", "A", "2024年公司营收为100亿元。", 0.9),
    make("https://b.com/2", "B", "2024年公司营收达130亿元。", 0.9),
])
e4 = next((x for x in evs4 if x["indicator"] == "营收"), None)
check("真冲突:两值不并簇",
      e4 is not None and len(e4["clusters"]) == 2,
      f"got={len(e4['clusters']) if e4 else None}")
check("真冲突:kind含分歧(非键失真/非修正)",
      e4 is not None and "分歧" in e4["verdict"]["kind"],
      f"got={e4['verdict']['kind'] if e4 else None}")

# 5) verdict 字段存在性 + reason 兼容保留
check("verdict结构:{kind,detail}",
      e4 is not None and isinstance(e4["verdict"], dict)
      and "kind" in e4["verdict"] and "detail" in e4["verdict"])
check("reason 字符串仍保留(向后兼容)",
      e4 is not None and isinstance(e4["reason"], str) and len(e4["reason"]) > 0)

print(f"\n========== schema v2 裁决 verdict：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
