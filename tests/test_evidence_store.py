# -*- coding: utf-8 -*-
"""证据库（EvidenceStore）回归：入库 → 查询 → 修正覆盖 → 来源追溯。

- ingest：多文档 → extract_evidence → evidence/src 两表（按归一 subject 键）；
- 实体归一生效：'五粮液实现归母' 与 '五粮液归母' 落同一 evidence 行（互证）；
- 修正覆盖：同键再 ingest（含'上修至'源）→ adopted_value 更新为修正值；
- query/sources/stats 可用。
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")
from research_agent.evidence_store import EvidenceStore
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


tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "ev.db")

# 1) 实体归一入库: 两文异称('实现归母'/'归母') → 同 evidence 行 2 源
st = EvidenceStore(db)
n1 = st.ingest_docs([
    make("https://a.com/1", "A", "2025年五粮液实现归母净利润89.54亿元。", 0.9),
    make("https://b.com/2", "B", "2025年五粮液归母净利润为89.54亿元。", 0.9),
])
check("入库1:产生evidence", n1 >= 1, f"n={n1}")
rows = st.query(subject="五粮液", indicator="净利润", year=2025)
check("实体归一:异称落同键(1行2源)",
      len(rows) == 1 and rows[0]["n_sources"] == 2
      and rows[0]["level"] == "high",
      f"got={rows}")
srcs = st.sources("五粮液", "净利润", 2025)
check("来源追溯:2源含subject_raw", len(srcs) == 2 and srcs[0]["subject_raw"], f"n={len(srcs)}")

# 2) 修正识别(批内): 同批旧值源 + '上修至'源 → 修正簇优先(议题4语义)
st2 = EvidenceStore(os.path.join(tmp, "ev2.db"))
st2.ingest_docs([
    make("https://a.com/1", "A", "2024年公司营收为100亿元。", 0.9, "2025-01-01"),
    make("https://c.com/3", "C", "公司2024年营收上修至120亿元。", 0.9, "2026-07-01"),
])
rows2 = st2.query(subject="公司", indicator="营收", year=2024)
check("修正覆盖:adopted=上修值且kind=修正",
      rows2 and rows2[0]["adopted_value"] == "120亿元"
      and rows2[0]["verdict_kind"] == "修正",
      f"got={rows2}")

# 3) 不同 subject 不混
rows3 = st.query(subject="贵州茅台", indicator="净利润")
check("隔离:茅台键无记录(未混)", len(rows3) == 0, f"got={rows3}")

# 4) stats + 多源一致
st3 = EvidenceStore(os.path.join(tmp, "ev3.db"))
st3.ingest_docs([
    make("https://a.com/1", "A", "2024年全球AI市场规模达2500亿美元。", 0.9),
    make("https://b.com/2", "B", "2024年全球AI市场规模达到2500亿美元。", 0.9),
    make("https://d.com/4", "D", "2025年全球AI市场规模达3200亿美元。", 0.9),
])
s = st3.stats()
check("stats:一致+单源分布", s["n_evidence"] == 2 and "一致" in s["verdict_dist"],
      f"got={s}")

print(f"\n========== 证据库 EvidenceStore：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
