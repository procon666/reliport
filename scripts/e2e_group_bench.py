# -*- coding: utf-8 -*-
"""同主题多源分组跑分（第③项）：白酒5+汽车5+手机5 → 各组内同批 ingest。

指标（用户定稿）：
A 全局：evidence/src 条目、单源 vs 多源占比、verdict.kind 全分布；
B schema v2 专项：①锚——相对值 claim 数/anchor 有效数/跨年假冲突拦截 case；
  ②实体归一——norm 归并 case（同 norm subject 下不同 subject_raw 源数）/误漏归并
  样本（打印供人工判）；③裁决质量——键失真/修正数（校验 adopted）+ 抽样多源簇；
C 边界 case 样本打印（抽取层命中 KNOWN 由人工对照，不改 analyzer）。

用法：python3 scripts/e2e_group_bench.py [报告根目录]   （默认 outputs/e2e_bench）
"""
import glob
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
from research_agent.analyzer import extract_evidence, _REL_BUCKET
from research_agent.evidence_store import EvidenceStore
from research_agent.searcher import SourceDoc

GROUPS = ("baijiu", "auto", "mobile")


def load_docs(grp_dir, prefix=None):
    docs, metas = [], []
    pat = grp_dir + f"/{prefix}_*.txt" if prefix else grp_dir + "/*.txt"
    for fp in sorted(glob.glob(pat)):
        txt = open(fp, encoding="utf-8").read()
        docs.append(SourceDoc(url=f"file://{os.path.basename(fp)}",
                              title=fp, content=txt, from_fetch=True,
                              credibility=0.85))
        metas.append((fp, len(txt)))
    return docs, metas


def run_group(name, grp_dir, db_path, prefix=None):
    docs, metas = load_docs(grp_dir, prefix)
    print(f"\n{'='*66}\n主题组 [{name}]  {len(docs)} 文档 "
          f"(总字数 {sum(m for _, m in metas)})")
    evs = extract_evidence(docs, max_evidence=500)
    st = EvidenceStore(db_path)
    st.ingest_docs(docs)
    db = st.conn
    # ---- A. 全局 ----
    n_src = db.execute("SELECT COUNT(*) FROM src").fetchone()[0]
    rows = db.execute(
        "SELECT adopted_value, n_sources, level, verdict_kind FROM evidence"
    ).fetchall()
    n_multi = sum(1 for r in rows if r[1] >= 2)
    vd = Counter(r[3] for r in rows)
    print(f"A. evidence {len(rows)} | src {n_src} | 多源(adopted n>=2) "
          f"{n_multi} ({n_multi/max(len(rows),1):.0%}) | 单源 "
          f"{len(rows)-n_multi}")
    print(f"   verdict分布: {dict(vd)}")

    # ---- B1. 锚字段 ----
    rel = db.execute("SELECT COUNT(*) FROM src WHERE is_rel=1").fetchone()[0]
    rel_anchor = db.execute(
        "SELECT COUNT(*) FROM src WHERE is_rel=1 "
        "AND (anchor_report IS NOT NULL OR anchor_base IS NOT NULL)"
    ).fetchone()[0]
    print(f"B1. 锚: 相对值 src {rel} | anchor有效(有report或base) {rel_anchor} "
          f"({rel_anchor/max(rel,1):.0%})")
    # 假冲突拦截: 相对值 evidence 内多簇且簇anchor不同 → 拆开互证
    def _ak(a):
        return (a.get("report_year"), a.get("base_year")) if a else None
    intercept = 0
    for e in evs:
        if e["indicator"] in _REL_BUCKET and len(e["clusters"]) >= 2:
            anc = {_ak(c.get("anchor")) for c in e["clusters"]}
            if len(anc) > 1:
                intercept += 1
    print(f"   相对值evidence中簇anchor相异被拆开(跨年假冲突拦截): {intercept}")

    # ---- B2. 实体归一 ----
    rows2 = db.execute(
        "SELECT subject, indicator, subject_raw FROM src "
        "WHERE subject!='' GROUP BY subject, indicator, subject_raw"
    ).fetchall()
    grp = defaultdict(list)
    for subj, ind, raw in rows2:
        grp[(subj, ind)].append(raw)
    merged = [(k, v) for k, v in grp.items() if len(set(v)) > 1]
    print(f"B2. 实体归一: 归一后同(subject,indicator)多原文形态组 {len(merged)}")
    for (subj, ind), raws in merged[:6]:
        print(f"     [{subj}|{ind}] ← {sorted(set(raws))[:3]}")

    # ---- B3. 裁决质量 ----
    kd = sum(1 for r in rows if r[3] == "键失真")
    fix = [r for r in rows if r[3] == "修正"]
    print(f"B3. 裁决: 键失真 {kd} | 修正 {len(fix)}")
    for r in fix[:3]:
        print(f"     [修正] {r[2]} adopted={r[0]}")
    return evs, st


def main(root: str):
    for name in GROUPS:
        grp_dir = root   # 文件平铺命名 {name}_NN.txt
        files = glob.glob(os.path.join(root, f"{name}_*.txt"))
        if not files:
            print(f"缺组 {name}"); continue
        db_path = os.path.join(root, f"{name}.db")
        if os.path.exists(db_path):
            os.remove(db_path)
        run_group(name, root, db_path, prefix=name)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "outputs/e2e_bench")
