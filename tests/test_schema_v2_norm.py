# -*- coding: utf-8 -*-
"""schema v2 步骤2：聚合层证据实体归一（subject 残词清洗，抽取层不动）。

背景：跨文档同实体因抽取层 subject 带动词/归属残词尾（'五粮液实现归母'/
'水井坊归母'/'五粮液实现'）或句首引导噪声（'记者梳理'/'在'/'国家统计局数据显示'）
而拆桶，跨源互证失败。聚合层 _norm_evidence_subject 清洗后作桶键，使残词形态
收敛为实体（'五粮液实现归母'→'五粮液'）。

保守边界（宁缺毋滥，真实白酒样本驱动）：
- 只清确定性残词尾/句首噪声，不做互含合并（'白酒上市公司' vs '20家A股白酒上市
  公司' 语义可能不同，不误并）；
- 清洗后为空/单字/无中文 → ""（噪声键空化：'在'/'余'/'19家' 计数假值不入桶）；
- 正确实体不受影响（'定价'/'白酒'/'贵州茅台'/'金徽酒'/'实际产能'/'产能利用率'…）。
"""
import sys

sys.path.insert(0, ".")
from research_agent.analyzer import _norm_evidence_subject, extract_evidence
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
                     from_fetch=True, credibility=cred)


def find(evs, indicator, subj=""):
    for e in evs:
        if e["indicator"] == indicator and (not subj or e["subject"] == subj):
            return e
    return None


# ---- norm 单元: 剥尾动词/归属残 ----
for raw, exp in [("五粮液实现", "五粮液"), ("洋河股份实现", "洋河股份"),
                 ("水井坊实现", "水井坊"), ("五粮液实现归母", "五粮液"),
                 ("水井坊归母", "水井坊")]:
    check(f"norm剥尾:{raw}→{exp}", _norm_evidence_subject(raw) == exp,
          f"got={_norm_evidence_subject(raw)!r}")

# ---- norm 单元: 句首噪声空化 ----
for raw in ("在", "余", "记者梳理", "国家统计局数据显示"):
    check(f"norm噪声空化:{raw}→''", _norm_evidence_subject(raw) == "",
          f"got={_norm_evidence_subject(raw)!r}")

# ---- norm 单元: 正确实体不误伤 ----
for raw in ("定价", "白酒", "金徽酒", "今世缘", "皇台酒业", "实际产能",
            "产能利用率", "贵州茅台", "青岛啤酒", "五粮液", "第三季度"):
    check(f"norm不误伤:{raw}保持不变",
          _norm_evidence_subject(raw) == raw,
          f"got={_norm_evidence_subject(raw)!r}")

# ---- 互证: 残词形态跨文收敛同桶 → 2源high ----
evs = extract_evidence([
    make("https://a.com/1", "A", "2025年五粮液实现归母净利润89.54亿元。", 0.9),
    make("https://b.com/2", "B", "2025年五粮液归母净利润为89.54亿元。", 0.9),
])
e = find(evs, "净利润", "五粮液")
check("互证:残词归母两源收敛'五粮液'桶",
      e is not None and e["adopted"]["n_sources"] == 2 and e["level"] == "high",
      f"got={[(x['subject'], x['level']) for x in evs if x['indicator']=='净利润']}")

# ---- 隔离: 不同公司不并 ----
evs2 = extract_evidence([
    make("https://a.com/1", "A", "2025年五粮液实现归母净利润89.54亿元。", 0.9),
    make("https://b.com/2", "B", "2025年贵州茅台实现归母净利润862亿元。", 0.9),
])
nj = [x for x in evs2 if x["indicator"] == "净利润"]
check("隔离:茅台/五粮液分桶",
      len(nj) >= 2 and {x["subject"] for x in nj} >= {"五粮液", "贵州茅台"},
      f"got={[(x['subject'], x['adopted']['n_sources']) for x in nj]}")

print(f"\n========== schema v2 实体归一：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
