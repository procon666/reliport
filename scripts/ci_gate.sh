#!/usr/bin/env bash
# ============================================================
# CI 门禁收口 —— 引擎/测试/标注任何改动后必跑（阶段封板基线 baseline-post-E3）
# 全部门禁任一失败 → exit 1。
# 用途：封板后禁止单点补丁；若业务复现新簇重启迭代，本脚本是回归闸门。
# ============================================================
set -u
cd "$(dirname "$0")/.."
PASS=0; FAIL=0
ok(){ echo "[PASS] $1"; PASS=$((PASS+1)); }
bad(){ echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

# 1) 语料回归 79/79（P0 起保持）
out=$(python3 -m research_agent.eval --expect-only 2>&1 | tail -1)
echo "$out" | grep -q "79/79" && ok "语料 $out" || bad "语料 $out"

# 2) KNOWN 22 条全仍在错（无意外已修 REVISIT → exit 1）
if python3 tests/test_known_boundaries.py >/dev/null 2>&1; then
  ok "KNOWN 全仍在错(无REVISIT)"
else
  bad "KNOWN 存在 REVISIT 或异常(需人工确认迁移)"
fi

# 3) 口径审计（相对值 year=None 防回潮）
if python3 tests/test_year_schema_audit.py >/dev/null 2>&1; then
  ok "口径审计 PASS"
else
  bad "口径审计 FAIL(相对值挂年回潮)"
fi

# 4) 修复固化断言 63 条全过
out=$(python3 tests/test_label_fixes.py 2>&1 | tail -1)
echo "$out" | grep -q "0 失败" && ok "label_fixes $out" || bad "label_fixes $out"

# 5) label 严格三元组防回归（≥53/61，历史集不追高只防掉）
out=$(python3 tests/label_strict_eval.py 2>&1 | grep "三元组全对")
echo "$out" | grep -qE "5[3-9]/61|6[0-1]/61" && ok "label 防回归 $out" || bad "label 掉分 $out"

# 6) 其余专项测试全绿
for t in tests/test_*.py; do
  case "$t" in
    *known_boundaries*|*year_schema_audit*|*label_fixes*|*label_strict*) continue;;
  esac
  out=$(python3 "$t" 2>&1 | tail -1)
  if echo "$out" | grep -qE "0 失败|0失败|0 条|PASS|14/14"; then
    ok "专项 $(basename "$t")"
  else
    bad "专项 $(basename "$t") => $out"
  fi
done

# 7) real_* 四靶场 100%（盲测主指标之一）
for r in real_scale real_mobilegame real_pressure real_industry; do
  out=$(python3 tests/${r}_blind.py 2>&1 | grep "■ 总体")
  echo "$out" | grep -q "100%" && ok "$r $out" || bad "$r $out"
done

echo "----------------------------------------"
echo "CI 门禁汇总: $PASS 通过 / $FAIL 失败"
[ "$FAIL" -eq 0 ] || { echo "门禁失败——封板纪律:禁止绕过,修复或走评审重启流程"; exit 1; }
exit 0
