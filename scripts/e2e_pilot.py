# -*- coding: utf-8 -*-
"""端到端跑分 pilot（可复用）：真实报告全文 → 召回/质量/一致性代理指标 + 异常探测。

用法：python3 scripts/e2e_pilot.py [报告.txt 路径]   （默认 outputs/e2e_report_pilot.txt）

指标口径（封板文档 §7.2：线上主指标 = 端到端真实文档跑分）：
- 覆盖代理：含 claim 句数/句子数、claim 总数、报告数值 token 上界（年份/编号为噪音，作参考）；
- 质量代理：空主体率、动词/碎词尾主体率、相对值 year=None 率（应 100%）、状态量挂年率；
- 一致性：同 (subject,indicator) 跨句多值组（候选人工复查，=0 表示无内部冲突）。
输出异常样例供人工抽查；发现的高频句式记入被动归集（KNOWN 或专项候选）。

2026-07 pilot 基线（智研·2025 家电零售报告 5386 字）：44 claims / 相对值 None 20/20 /
空主体 2 / 碎词尾 3 / 同键多值 0。暴露真实句式：钢铁并列"数据显示"引导空主体、
"规模为X亿元，同比降Y%"的 Y subject 带"为"残词、指代主语"这一数据"。
"""
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
from research_agent.analyzer import _sentence_claims, _numeric_tokens

VERB_TAIL_RE = re.compile(
    r"(?:为|达|占|增长|同比|较|突破|超过|预计|有望|实现|约|的)$")
STAT_BUCKET = {"占比", "渗透率", "市场份额", "毛利率", "净利率", "稼动率"}
REL_BUCKET = {"增长率", "跌幅", "涨幅"}


def run(path: str):
    txt = open(path, encoding="utf-8").read()
    sents = [s.strip() for s in re.split(r"(?<=[。！？])", txt)
             if s.strip() and re.search(r"[\u4e00-\u9fff]", s)]
    claims = []
    for s in sents:
        for c in _sentence_claims(s, "e2e"):
            claims.append((s, c))
    empty = sum(1 for _, c in claims if not (c["subject"] or "").strip())
    verb = sum(1 for _, c in claims if (c["subject"] or "").strip()
               and VERB_TAIL_RE.search((c["subject"] or "").strip()))
    rel_t = sum(1 for _, c in claims if c["indicator"] in REL_BUCKET)
    rel_n = sum(1 for _, c in claims
                if c["indicator"] in REL_BUCKET and c["year"] is None)
    st_t = sum(1 for _, c in claims if c["indicator"] in STAT_BUCKET)
    st_y = sum(1 for _, c in claims
               if c["indicator"] in STAT_BUCKET and c["year"] is not None)
    nnum = sum(len(_numeric_tokens(s)) for s in sents)
    num_s = sum(1 for s in sents if _sentence_claims(s, "e2e"))
    grp = defaultdict(list)
    for _, c in claims:
        grp[(c["subject"], c["indicator"])].append(c["value"])
    dup = [(k, sorted(set(v))) for k, v in grp.items()
           if len(set(v)) > 1 and k[0]]

    print(f"报告: {len(txt)}字 | 句子 {len(sents)} | 含claim句 {num_s}")
    print(f"claims {len(claims)} | 数值token上界≈{nnum}")
    print(f"质量: 空主体 {empty} | 动词/碎词尾主体 {verb}")
    print(f"相对值 {rel_t} 条 None率 {rel_n}/{rel_t} | 状态量 {st_t} 条 挂年 {st_y}/{st_t}")
    ind = Counter(c["indicator"] for _, c in claims)
    print("indicator top:", ind.most_common(10))
    print(f"同键多值组(复查): {len(dup)}")
    for k, v in dup[:10]:
        print("   ", k, "→", v[:6])
    n = 0
    print("--- 空/碎词主体样例 ---")
    for s, c in claims:
        subj = (c["subject"] or "").strip()
        if (not subj or VERB_TAIL_RE.search(subj)) and n < 10:
            print(f'   [{c["value"]}|{c["indicator"]}|subj={subj!r}] {s[:64]}')
            n += 1
    return len(claims), empty, verb, rel_n, rel_t


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "outputs/e2e_report_pilot.txt")
