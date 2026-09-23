# -*- coding: utf-8 -*-
"""端到端效果验证（A/B）：裁决表是否让 LLM 报告更可信。

A = 现有 writer 链路（只喂来源原文，LLM 自行读数字）
B = A + schema v2 裁决表（多源一致/单源/存疑标注，禁止引用存疑数字）
同 brief、同来源，对照输出 outputs/e2e_report_demo_A.md / _B.md。
"""
import glob
import os
import sys

sys.path.insert(0, ".")
from research_agent import llm
from research_agent.analyzer import extract_evidence
from research_agent.searcher import SourceDoc

import sys as _sys

if len(_sys.argv) > 2:
    _DIR, TOPIC = _sys.argv[1], _sys.argv[2]
else:
    _DIR, TOPIC = "outputs/e2e_bench/baijiu_*.txt", "2025年中国白酒行业概况与主要上市公司业绩"
_TAG = _sys.argv[4] if len(_sys.argv) > 4 else "demo"
_ONLY = _sys.argv[5] if len(_sys.argv) > 5 else ""

BRIEF = {"topic": TOPIC,
         "focus": _sys.argv[3] if len(_sys.argv) > 3 else "行业整体数据与头部公司表现"}

SYS = ("你是一名严谨的行业研究分析师，负责撰写 Markdown 调研报告。"
       "要求：结构清晰、引用数字必须与给定材料一致（绝不编造或改写数字）、"
       "来源之间有差异时如实说明。文末附来源清单（标题+URL）。")

SRC_INST_A = ("=== 来源资料 ===\n请从以下原始资料中自行提取数据撰写报告。"
              "每个来源正文可能含多个年份/口径数据，注意区分。")
SRC_INST_B = ("=== 来源资料 ===\n以下是原始资料，请仅作背景参考。\n\n"
              "=== 已核实数字表（系统裁决）===\n"
              "写作时必须采用下表数字（这是从多来源交叉核实后的结果），规则：\n"
              "· [多源一致]：直接采用，可写'多家来源一致显示'；\n"
              "· [单源]：可采用但需用'据XX数据'这类限定；\n"
              "· [存疑]：禁止作为事实引用，如涉及需写'各来源数据存在差异'；\n"
              "· 年份为空表示该数值无明确对应年份，写作时不要硬加年份。\n")

# 约束式 C：规则只"出题"（检出口径分歧点），判断与表达全部留给 AI。
# 清单由规则层确定性产出（此处为演示素材的人工核对项，可被 argv[6] 文件覆盖）。
SRC_INST_C_HEAD = ("=== 来源资料 ===\n请从原始资料中自行提取数据撰写报告。\n\n"
                   "=== 规则检出的口径分歧提示（硬约束，必须逐条处理）===\n"
                   "以下指标在多个来源中存在**统计口径差异**，规则已检出但不下结论。\n"
                   "报告**必须**：\n"
                   "· 单列一节（如'口径与数据差异说明'）逐条交代每一项的口径差异；\n"
                   "· 引用这类数字时必须随文注明口径与来源（如'中汽协口径''乘联会零售口径'）；\n"
                   "· 禁止把不同口径的数值直接并列比较、混用成一个数，或只取其一而不说明依据；\n"
                   "· 允许以某一口径为主叙事，但其余口径须交代清楚，不得回避。\n"
                   "检出的分歧指标清单如下（每条含分歧点，供你核对原文后自行处理）：\n")
_CONSTRAINT_DEFAULT = [
 "新能源车2025年销量：中汽协口径1649万辆(产销)；乘联会口径零售1280.9万辆、批发1531.9万辆——三者关系需向读者交代",
 "2025全年汽车销量总量：中汽协3440万辆(全口径含商用车)；乘联会国产狭义乘用车零售2374.4万辆/批发2955.4万辆",
 "2025汽车出口：中汽协709.8万辆(全口径含商用车)；乘联会国产乘用车出口573.9万辆",
 "比亚迪2025销量：中汽协发布460.2万辆(全年)；乘联会批发榜454.5万辆；乘联会零售十强348.5万辆——口径差异须注明",
]


def load_docs():
    docs = []
    for fp in sorted(glob.glob(_DIR)):
        txt = open(fp, encoding="utf-8").read()
        docs.append(SourceDoc(url=f"file://{os.path.basename(fp)}",
                              title=os.path.basename(fp), content=txt,
                              from_fetch=True, credibility=0.85))
    return docs


def build_table(evs):
    lines = []
    n = 0
    for e in evs:
        subj = e["subject"] or "(全局)"
        if not e["subject"]:
            continue                      # 跳过空主体项（键失真源）
        year = f"{e['year']}年" if e["year"] else "年份不明"
        val = e["adopted"]["value"]
        ns = e["adopted"]["n_sources"]
        kind = e["verdict"]["kind"]
        if kind == "一致" and ns >= 2:
            tag = "多源一致"
        elif kind == "单源":
            tag = "单源"
        elif kind == "分开呈现":
            continue
        else:
            tag = "存疑"
        lines.append(f"[{tag}] {subj} | {e['indicator']} | {year} | {val}"
                     f" | {ns}个独立来源")
        n += 1
        if n >= 40:
            break
    return "\n".join(lines)


def source_ctx(docs):
    parts = []
    for i, d in enumerate(docs, 1):
        body = d.content
        if len(body) > 1600:
            body = body[:1600] + "…"
        parts.append(f"[来源{i}] {d.title}\nURL:{d.url}\n{body}")
    return "\n\n".join(parts)


def run():
    docs = load_docs()
    evs = extract_evidence(docs, max_evidence=500)
    table = build_table(evs)
    print(f"证据项 {len(evs)} | 裁决表条目 {table.count(chr(10))+1}")
    src = source_ctx(docs)
    brief_txt = "\n".join(f"{k}: {v}" for k, v in BRIEF.items())

    items = _CONSTRAINT_DEFAULT
    if len(_sys.argv) > 6:
        items = [ln.strip() for ln in open(_sys.argv[6], encoding="utf-8")
                 if ln.strip()]
    insts = {"A": SRC_INST_A, "B": SRC_INST_B,
             "C": SRC_INST_C_HEAD + "\n".join(f"- {it}" for it in items)}
    for tag, inst in insts.items():
        if _ONLY and tag != _ONLY:
            continue
        extra = table if tag == "B" else ""
        user = (f"=== 研究简报 ===\n{brief_txt}\n\n"
                f"{inst}\n{src}"
                + (f"\n\n已核实数字表：\n{extra}" if extra else "")
                + "\n\n请撰写 Markdown 报告（1500-2500字），文末附来源清单。")
        print(f"--- 生成报告 {tag} (LLM调用) ---")
        md = llm.chat(SYS, user, temperature=0.4, max_tokens=6000)
        out = f"outputs/e2e_report_{_TAG}_{tag}.md"
        open(out, "w", encoding="utf-8").write(md)
        print(f"落盘 {out} ({len(md)} 字)")


if __name__ == "__main__":
    run()
