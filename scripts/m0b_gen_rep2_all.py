# -*- coding: utf-8 -*-
"""为 M0b widget 一次性预生成三份 rep2（三种口径），避免每点击都跑一次 LLM。
生成到 outputs/m0b/rep2_<slug>.md。
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research_agent import llm
from research_agent.searcher import SourceDoc
from scripts.pref_store import PreferenceStore, build_pref_block

SRC = "outputs/e2e_nev/nev_0[015].txt"
KEY = "比亚迪/比亚迪销量/全年销量"
PREF_JSON = "outputs/m0b/pref.json"
TOPIC = "2025年中国汽车市场全景与新能源转型"
FOCUS = ("全年总产销、新能源汽车销量与渗透率、总量口径差异(中汽协vs乘联会)、"
         "头部车企表现（重点看比亚迪全年销量）")
SYS = ("你是一名严谨的行业研究分析师，负责撰写 Markdown 调研报告。"
       "要求：引用数字必须与给定材料一致（绝不编造）；来源之间有差异时如实说明；"
       "文末附来源清单。")

CHOICES = [
    ("乘联会零售口径", "348.5 万辆", "retail", "零售（终端上牌）"),
    ("乘联会批发口径", "454.5 万辆", "wholesale", "批发（厂家批发）"),
    ("中汽协全口径", "460.2 万辆", "full", "含商用车全口径"),
]


def gen(st, dim, val):
    p = st.get(KEY)
    docs = []
    for fp in sorted(glob.glob(SRC)):
        txt = open(fp, encoding="utf-8").read()
        if len(txt) > 2000:
            txt = txt[:2000] + "…"
        docs.append(SourceDoc(url=f"file://{fp}", title=fp, content=txt,
                              from_fetch=True, credibility=0.85))
    src = "\n\n".join(f"[来源{i}] {d.title}\n{d.content}" for i, d in enumerate(docs, 1))
    pref = build_pref_block("比亚迪销量", dim, val, p["hits"])
    user = (f"=== 研究简报 ===\n主题：{TOPIC}\n关注：{FOCUS}\n\n"
            f"=== 来源资料 ===\n{src}\n\n{pref}"
            f"\n请撰写 Markdown 报告（1200-2000字），文末附来源清单。")
    return llm.chat(SYS, user, temperature=0.3, max_tokens=4500)


def main():
    os.makedirs("outputs/m0b", exist_ok=True)
    st = PreferenceStore(PREF_JSON)
    for dim, val, slug, note in CHOICES:
        out = f"outputs/m0b/rep2_{slug}.md"
        if os.path.exists(out):
            print(f"[skip] {out} 已存在")
            continue
        # 写真首触发：先设此口径为偏好
        st.set_user_choice(KEY, dim=dim, value=val, origin="user_pick")
        md = gen(st, dim, val)
        open(out, "w", encoding="utf-8").write(md)
        print(f"[ok]  {out}  ({len(md)} 字)")
    # 清理 pref：让真实被测者首触发（widget 模拟点击会重写）
    if os.path.exists(PREF_JSON):
        os.remove(PREF_JSON)
        print("[clean] pref.json 已清理")


if __name__ == "__main__":
    main()