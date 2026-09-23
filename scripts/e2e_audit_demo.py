# -*- coding: utf-8 -*-
"""对账校验器 demo v2（质检员试用·量纲修正版）：报告数字 vs 来源逐条对账 + 篡改抓取。

用法：python3 scripts/e2e_audit_demo.py [报告md] [来源glob] [篡改"原串|改串"]
v2 修正（2026 新能源主题真实场景暴露）：
1. 数字单位用**长单位优先**匹配（"359.8万辆"整取，不再截成 359.8+万）；
2. close 比较加**量纲门**：百分比/金额/数量分桶，跨桶不比（359.8万辆 vs 387.9%
   不再互判近值——v1 无量纲门致 14 处假"疑不符"）；
3. 源侧收集**全文数字**（不只 claim 值），报告数字在源文本逐字存在即 OK——
   消除 claim 抽取覆盖不全导致的假"无出处"；
4. 删除残留的苹果硬编码篡改块（v1 在换主题后空转造成假"被抓"）。
"""
import glob
import re
import sys
import unicodedata

sys.path.insert(0, ".")
from research_agent.analyzer import _sentence_claims

# 长单位优先（Python 正则从左到右尝试，"万辆"须在"万"前）
_NUM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(万辆|万台|万部|万人|万套|万人次|万千升|亿辆|亿台|亿部|万亿元|"
    r"亿美元|亿元|万元|GWh|GW|MW|个百分点|%|％|万|亿|元|美元|台|部|点)")

_APPROX = ("约", "近", "左右", "上下", "逾", "略超", "高达")

_UNIT_DIM = {
    "%": "pct", "％": "pct", "个百分点": "pct",
    "亿元": "money", "万元": "money", "万亿元": "money", "美元": "money",
    "亿美元": "money", "元": "money",
    "万千升": "volume", "GWh": "energy", "GW": "energy", "MW": "energy",
}
_DEFAULT_DIM = "count"      # 万台/万辆/亿台/台/部/点 等计数


def _dim(unit: str) -> str:
    return _UNIT_DIM.get(unit, _DEFAULT_DIM)


def collect_numbers(text: str):
    """抽 (num, unit) 集合 → {(num,unit)} 精确集 + {dim:[num]}。"""
    exact = set()
    dims = {}
    for m in _NUM_RE.finditer(text):
        num, unit = m.group(1), m.group(2)
        exact.add((num, unit))
        d = _dim(unit)
        dims.setdefault(d, []).append(float(num))
    return exact, dims


def source_index(glob_pat: str):
    """来源全文数字索引（NFKC 归一全角数字）+ claim 值并入。"""
    raw = ""
    fps = sorted(glob.glob(glob_pat))
    for fp in fps:
        raw += unicodedata.normalize("NFKC", open(fp, encoding="utf-8").read()) + "\n"
    exact, dims = collect_numbers(raw)
    for fp in fps:
        for line in open(fp, encoding="utf-8"):
            for s in re.split(r"(?<=[。！？])", line):
                if not re.search(r"[\u4e00-\u9fff]", s):
                    continue
                for c in _sentence_claims(s, fp):
                    m = _NUM_RE.search(c["value"])
                    if m:
                        exact.add((m.group(1), m.group(2)))
    return exact, dims


def _report_rows(md: str):
    out = []
    for m in _NUM_RE.finditer(md):
        out.append((m.group(1), m.group(2),
                    md[max(0, m.start() - 22):m.end() + 6].replace("\n", " ")))
    return out


def audit(md_path: str, src_pat: str, tamper_mark: str = ""):
    md = unicodedata.normalize("NFKC", open(md_path, encoding="utf-8").read())
    s_exact, s_dims = source_index(src_pat)
    if tamper_mark:
        md = md.replace(tamper_mark[0], tamper_mark[1], 1)
    rows = []
    for num, unit, ctx in _report_rows(md):
        fnum = float(num)
        d = _dim(unit)
        if (num, unit) in s_exact:
            rows.append(("OK", num, unit, ctx))
            continue
        # 概算修饰放行（"约/近/左右…"）——须先于 close，否则"约1个百分点"
        # 会被近值判成疑不符（2026 全角修复后暴露的顺序 bug）
        if any(w in ctx for w in _APPROX):
            rows.append(("概算", num, unit, ctx))
            continue
        # 同量纲 close：源无此数但有很接近的 → 高度疑似被改
        cand = [n for n in s_dims.get(d, [])]
        best = None
        for n0 in cand:
            if best is None or abs(fnum - n0) < abs(fnum - best):
                best = n0
        if best is not None and abs(fnum - best) / max(best, 1) < 0.05:
            rows.append(("疑不符", num, unit, f"源近值 {best:g} | {ctx}"))
            continue
        rows.append(("无出处", num, unit, ctx))
    return rows, md


def main():
    md_path = sys.argv[1] if len(sys.argv) > 1 else "outputs/e2e_report_demo_B.md"
    src_pat = sys.argv[2] if len(sys.argv) > 2 else "outputs/e2e_conflict/cf_*.txt"
    tam = sys.argv[3].split("|") if len(sys.argv) > 3 and "|" in sys.argv[3] else None
    print(f"=== 对账: {md_path} vs {src_pat} ===")
    rows, _ = audit(md_path, src_pat)
    for tag, num, unit, ctx in rows:
        print(f"[{tag:>4}] {num}{unit:<6} | {ctx[:52]}")
    import collections
    print("统计:", dict(collections.Counter(r[0] for r in rows)))

    if tam:
        a, b = tam
        print(f"\n=== 篡改对照: {a} → {b} ===")
        rows2, _ = audit(md_path, src_pat, tamper_mark=(a, b))
        bnum = re.search(r"(\d+(?:\.\d+)?)", b)
        bn = bnum.group(1) if bnum else "__none__"
        caught = [r for r in rows2 if r[0] == "疑不符" and r[1] == bn]
        print(f"篡改数字 {bn} 被抓: {len(caught) > 0}")
        for tag, num, unit, ctx in rows2:
            if num == bn:
                print(f"  [{tag}] {num}{unit} | {ctx[:66]}")
        if not any(r[1] == bn for r in rows2):
            print("  提示: 篡改串未命中报告原文(替换未生效), 对照无效")


if __name__ == "__main__":
    main()
