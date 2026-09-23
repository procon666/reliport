# -*- coding: utf-8 -*-
"""错误驱动自迭代评估脚手架（eval）。

这是把"抽取质量"变成可度量、可回归的工具：
- 加载跨领域量化主张语料 data/claim_corpus.py；
- 用 analyzer._sentence_claims 对每条句子抽取主张；
- 与期望 (主体/指标/数值/年份) 比对，产出"主张命中率 / 句子级全对率"；
- 把**失败样本落盘**到 eval/errors.jsonl，供下一轮迭代针对改进，防止回归。

用法：
    python -m research_agent.eval                 # 跑一次，落盘错误样本
    python -m research_agent.eval --verbose       # 打印每条失败明细
    python -m research_agent.eval --expect-only   # 只看结果，不覆盖错误文件
"""
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_agent.analyzer import (  # noqa: E402
    _sentence_claims,
    _value_to_number,
    _values_close,
)
from data.claim_corpus import claim_cases  # noqa: E402


def _num_eq(a: str, b: str) -> bool:
    """数值实质相等：容忍单位差异与舍入噪声。"""
    na = _value_to_number(a)
    nb = _value_to_number(b)
    if na and nb:
        if na[1] != nb[1]:
            return False
        return _values_close(a, b)
    return a.replace(" ", "") == b.replace(" ", "")


def _claims_got(got):
    return [(c["value"], c["indicator"], c["subject"], c["year"]) for c in got]


def _subj_ok(exp_subj: str, got_subj: str) -> bool:
    """主体是否算命中：期望为空则放行（未标注主体不强求）；
    否则引擎主体与期望主体须互相包含（容忍"中国新能源"vs"新能源"等粒度差）。"""
    if not exp_subj:
        return True
    g = (got_subj or "").strip()
    if not g:
        return False
    if exp_subj in g or g in exp_subj:
        return True
    # 期望"华为" vs 产出"华为服务/华为全球"等情况——取较短者是否出现在较长者中已覆盖
    return False


def run(verbose: bool = False):
    """评估语料并返回 (汇总dict, 错误样本list)。

    评分口径：
    - 主张命中 = 期望的 (取值, 口径年份) 在产出里能找到（指标名以"就近归一"为准，
      不强求与期望一字不差——指标归一是否合理另行通过句子级全对校验）；
    - 句子级全对 = 该句所有期望主张都命中，且**指标名、主体**也与期望一致。
    """
    from research_agent.analyzer import _sentence_claims as _sc
    cases = claim_cases()
    total_claims = 0
    hit_claims = 0
    total_sent = 0
    ok_sent = 0
    errors = []
    for sent, expects in cases:
        got = _sc(sent, "u")
        total_sent += 1
        sent_ok = True
        for (exp_subj, exp_ind, exp_val, exp_year) in expects:
            total_claims += 1
            # 取值+年份命中（不强制指标/主体）——主张级
            val_hit = any(
                _num_eq(c["value"], exp_val) and c["year"] == exp_year
                for c in got
            )
            # 指标名+主体也一致——句子级（主体用互含判定，粒度可略差）
            full_hit = any(
                _num_eq(c["value"], exp_val) and c["year"] == exp_year
                and c["indicator"] == exp_ind
                and _subj_ok(exp_subj, c["subject"])
                for c in got
            )
            if val_hit:
                hit_claims += 1
            else:
                sent_ok = False
                errors.append({
                    "sentence": sent,
                    "expected": list(expects),
                    "got": _claims_got(got),
                    "miss": (exp_subj, exp_ind, exp_val, exp_year),
                    "reason": "value_missing",
                })
                if verbose:
                    print(f"  [value_miss] {sent}")
                    print(f"          缺: {(exp_subj, exp_ind, exp_val, exp_year)}")
                    print(f"          得: {_claims_got(got)}")
            if not full_hit:
                sent_ok = False  # 取值在但指标/主体不对 → 句子级不算全对
                if verbose and val_hit:
                    print(f"  [label_miss] {sent}")
                    print(f"          期望(指标={exp_ind!r},主体={exp_subj!r})")
                    print(f"          产出: {_claims_got(got)}")
        if sent_ok:
            ok_sent += 1
    summary = {
        "claims_hit": hit_claims,
        "claims_total": total_claims,
        "sent_ok": ok_sent,
        "sent_total": total_sent,
    }
    return summary, errors


def persist_errors(errors, out_path):
    """把失败样本以 JSONL 落盘，供错误驱动迭代比对。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8") as f:
        for e in errors:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return out_path


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="主张抽取错误驱动评估")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--expect-only", action="store_true",
                   help="只打印结果，不写入错误样本文件")
    args = p.parse_args(argv)

    base = Path(__file__).resolve().parent.parent
    summary, errors = run(verbose=args.verbose)

    ch, ct = summary["claims_hit"], summary["claims_total"]
    so, st = summary["sent_ok"], summary["sent_total"]
    print(f"\n主张命中(取值为准): {ch}/{ct}  ({ch/ct*100:.1f}%)")
    print(f"句子级全对(含指标): {so}/{st}  ({so/st*100:.1f}%)")

    if not args.expect_only:
        out = base / "eval" / "errors.jsonl"
        pth = persist_errors(errors, out)
        tag = f"，已落盘 {len(errors)} 条失败样本 → {pth}" if errors else "，全部通过"
        print(f"评估完成{tag}")


if __name__ == "__main__":
    main()
