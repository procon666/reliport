# -*- coding: utf-8 -*-
"""端到端跑分扩充收集（第一阶段）：多份真实报告 → 聚合观测 + 三类专项 case。

用法：python3 scripts/e2e_harvest.py [报告目录]
收集目标（供第二阶段实体归一 + 第三阶段议题3/4/5 评审输入）：
  A. 质量代理聚合（各报告 claims/相对值None率/空主体/碎词尾）
  B. 实体拆分缺口C频率：subject 去残词尾后同词干不同原文的组数（同实体拆桶量）
  C. 锚字段生效/失效：相对值 claim 锚解析分布 + 可疑(有同比无report)样例
  D. 跨文档 extract_evidence：被锚门拆开的组 / detect_conflicts 冲突 / reason 样例
禁止：仅观测分析，不改 analyzer 任何代码。
"""
import glob
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
from research_agent.analyzer import _sentence_claims, extract_evidence, \
    detect_conflicts, _resolve_anchor, _REL_BUCKET
from research_agent.searcher import SourceDoc

VERB_TAIL_RE = re.compile(r"(?:为|达|占|增长|同比|较|突破|超过|预计|有望|实现|约|的|总计|合计)$")
REL_BUCKET = _REL_BUCKET
STAT_BUCKET = {"占比", "渗透率", "市场份额", "毛利率", "净利率", "稼动率"}


def subj_stem(subj: str) -> str:
    """残词尾剥离后的 subject 词干(探测用,非最终归并规则)。"""
    s = (subj or "").strip()
    while True:
        m = VERB_TAIL_RE.search(s)
        if m and len(s) > len(m.group(0)):
            s = s[: m.start()].rstrip(" ，,、的")
        else:
            break
    return s


def run(report_dir: str):
    files = sorted(glob.glob(report_dir + "/*.txt"))
    all_claims = []
    per_report = []
    for fp in files:
        txt = open(fp, encoding="utf-8").read()
        sents = [x.strip() for x in re.split(r"(?<=[。！？])", txt)
                 if x.strip() and re.search(r"[\u4e00-\u9fff]", x)]
        claims = []
        for s in sents:
            for c in _sentence_claims(s, fp):
                claims.append(c)
        rel = sum(1 for c in claims if c["indicator"] in REL_BUCKET)
        rel_n = sum(1 for c in claims
                    if c["indicator"] in REL_BUCKET and c["year"] is None)
        empty = sum(1 for c in claims if not (c["subject"] or "").strip())
        verb = sum(1 for c in claims if (c["subject"] or "").strip()
                   and VERB_TAIL_RE.search((c["subject"] or "").strip()))
        per_report.append((fp, len(txt), len(sents), len(claims), rel, rel_n,
                           empty, verb))
        all_claims.extend(claims)

    print("=== A. 各报告质量聚合 ===")
    t_c = t_r = t_rn = t_e = t_v = 0
    for fp, ln, sn, cn, rn_t, rn_n, em, vb in per_report:
        t_c += cn; t_r += rn_t; t_rn += rn_n; t_e += em; t_v += vb
        print(f"  {fp.split('/')[-1]}: {ln}字/{sn}句 | claims {cn} | "
              f"相对值None {rn_n}/{rn_t} | 空主体 {em} | 碎词尾 {vb}")
    print(f"  合计: claims {t_c} | 相对值None {t_rn}/{t_r} | 空主体 {t_e} | 碎词尾 {t_v}")

    print("\n=== B. 实体拆分(缺口C): 同indicator+同词干不同subject原文 ===")
    grp = defaultdict(set)
    for c in all_claims:
        st = subj_stem(c["subject"])
        if st:
            grp[(st, c["indicator"])].add(c["subject"])
    splits = [(k, v) for k, v in grp.items() if len(v) > 1]
    print(f"  同词干多原文组: {len(splits)}")
    for (st, ind), vals in sorted(splits, key=lambda x: -len(x[1]))[:14]:
        print(f"    词干[{st}|{ind}] ← {sorted(vals)[:4]}")

    print("\n=== C. 锚字段生效/失效 ===")
    anchors = Counter()
    suspect = []
    for c in all_claims:
        if c["indicator"] in REL_BUCKET:
            a = _resolve_anchor(c.get("claim") or "")
            key = (a["report_year"], a["base_year"]) if a else None
            anchors[key] += 1
    print(f"  相对值锚分布(top): {anchors.most_common(8)}")
    for c in all_claims:
        if c["indicator"] in REL_BUCKET and "同比" in (c.get("claim") or ""):
            a = _resolve_anchor(c.get("claim") or "")
            if a and a["report_year"] is None:
                suspect.append(c["value"])
    print(f"  有同比但report缺(锚失效候选) {len(suspect)} 例")

    print("\n=== D. 跨文档 extract_evidence: 锚门/冲突/reason ===")
    docs = []
    for i, fp in enumerate(files):
        txt = open(fp, encoding="utf-8").read()
        docs.append(SourceDoc(url=f"file://r{i:02d}", title=fp,
                              content=txt, from_fetch=True,
                              fetched_at="2026-01-15", credibility=0.85))
    evs = extract_evidence(docs)
    rel_ev = [e for e in evs if e["indicator"] in REL_BUCKET]
    print(f"  证据项 {len(evs)} | 其中相对值 {len(rel_ev)}")
    # 锚门拆开的: 同indicator 2+簇 且簇anchor不同
    for e in rel_ev:
        if len(e["clusters"]) >= 2:
            a0 = e["clusters"][0].get("anchor")
            a1 = e["clusters"][1].get("anchor")
            if a0 and a1 and a0 != a1:
                print(f"    [锚门拆开] {e['subject'][:16]}|{e['indicator']}: "
                      f"簇1={e['clusters'][0]['value']}({a0}) vs "
                      f"簇2={e['clusters'][1]['value']}({a1}) level={e['level']}")
    confs = detect_conflicts(docs)
    print(f"  detect_conflicts: {len(confs)} 处冲突")
    for c_ in confs[:8]:
        vs = sorted({cl_["value"] for cl_ in c_["claims"]})
        print(f"    [{c_['topic'][:30]}] 值={vs[:5]}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "outputs/e2e_reports")
