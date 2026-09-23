# -*- coding: utf-8 -*-
"""P3 口径审计：相对值（增长率/跌幅/涨幅）year 必须为 None —— 防口径回潮。

背景：E1 之后"相对值 year 一律 None"是 P3 评审定稿口径（Y 分级）。但口径共识
存在于人脑与文档中，观测归集/标注新样本时仍可能按旧直觉写回"增长率挂报告年"
（E1 收口语料 8 处旧口径断言就是教训）。本测试把该口径变成**机器可执行约束**：

审计规则（与引擎 E1 判定严格一致——canon 后 indicator ∈ {增长率, 跌幅, 涨幅}）：
  三源期望（data/claim_corpus.py / tests/test_known_boundaries.py / 与
  tests/label_strict_eval.py 同构的 LABELED 表）中，凡 indicator 落在相对值
  三桶且 year 非 None → 违规，exit 1。

范围说明（单向审计）：
- 只审计"相对值不得挂年"这一硬约束。反向（状态量/水平量"该挂未挂"）取决于句内
  是否存在显式年，属引擎行为，无法在标注期望层静态判定，故不在此约束。
- 状态量词（占比/渗透率/份额/稼动率/国产化率/毛利率…）不在三桶，天然放行——
  它们挂显式年是 Y 口径允许的。

用法：python3 tests/test_year_schema_audit.py   （0 违规 exit 0）
"""
import ast
import contextlib
import io
import importlib.util
import sys
from collections import Counter

sys.path.insert(0, ".")
sys.path.insert(0, "..")

RELATIVE_BUCKET = {"增长率", "跌幅", "涨幅"}
SOURCES = [
    ("data/claim_corpus.py", "CASES/claim_cases"),
    ("tests/test_known_boundaries.py", "KNOWN"),
    ("tests/label_strict_eval.py", "LABELED"),
]


def _exps_of(item):
    """KNOWN/LABELED 条目 → [(句子, (主体, 指标, 值, 年份))]"""
    try:
        if (
            len(item) >= 4
            and isinstance(item[3], (tuple, list))
            and item[3]
            and isinstance(item[3][0], (tuple, list))
        ):
            return [(item[1], e) for e in item[3]]
        return [(item[1], item[3])]
    except Exception:
        return []


def _load_known_like(path, name):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    return []


def _load_corpus(path):
    spec = importlib.util.spec_from_file_location("claim_corpus_mod", path)
    mod = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(mod)
    rows = mod.claim_cases() if hasattr(mod, "claim_cases") else mod.CASES
    return rows


def _iter_expectations():
    """产出 (来源, 句子, indicator, year)。"""
    rows = _load_corpus("data/claim_corpus.py")
    for r in rows:
        if len(r) >= 2 and isinstance(r[1], (tuple, list)):
            for e in r[1]:
                if len(e) >= 4:
                    yield ("corpus", r[0], e[1], e[3])
    for path, name in [
        ("tests/test_known_boundaries.py", "KNOWN"),
        ("tests/label_strict_eval.py", "LABELED"),
    ]:
        for item in _load_known_like(path, name):
            for sent, e in _exps_of(item):
                if len(e) >= 4:
                    yield (name, sent, e[1], e[3])


def audit():
    violations = []
    total = 0
    bucket = Counter()
    for src, sent, indicator, year in _iter_expectations():
        if not isinstance(indicator, str):
            continue
        bucket[indicator] += 1
        total += 1
        if indicator in RELATIVE_BUCKET and year is not None:
            violations.append((src, sent, indicator, year))
    return total, bucket, violations


def main():
    total, bucket, violations = audit()
    rel_total = sum(bucket[k] for k in RELATIVE_BUCKET)
    print(f"口径审计：三源期望共 {total} 条，其中相对值桶 {rel_total} 条")
    print(f"相对值桶分布：增长率 x{bucket['增长率']} / 跌幅 x{bucket['跌幅']} / "
          f"涨幅 x{bucket['涨幅']}")
    if violations:
        print(f"\n✗ 口径回潮违规 {len(violations)} 处（相对值 year 非 None）：")
        for src, sent, ind, year in violations:
            print(f"  [{src}] {ind}={year} | {sent[:44]}…")
        print("\n结论：FAIL —— 相对值 claim 不得挂显式年（P3 Y 口径，E1 后）")
        sys.exit(1)
    print("\n结论：PASS —— 0 违规，相对值 year=None 口径已被三源约束")
    sys.exit(0)


if __name__ == "__main__":
    main()
