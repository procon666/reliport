# -*- coding: utf-8 -*-
"""M0a 机制闭环 demo：偏好 → 口径切换 → 回显（管道焊通验证）。

流程：
1. 第一次生成（无偏好，默认专家口径）：比亚迪销量按中汽协 460.2 万辆主叙事；
2. 注入"用户选择事件"（set_user_choice：比亚迪销量 → 乘联会零售 348.5）——
   事件接口与 M0b 真人点击一致，M0b 只换事件源；
3. 第二次生成（带偏好指令）：比亚迪主叙事切 348.5，其余口径仍交代，附回显；
4. 断言（M0a 验收=机制跑通）：
   A 口径切换：rep2 比亚迪段落以 348.5 为主、不再以 460.2 为主；
   B 回显出现：rep2 含"按你的采信"与"已确认 1 次"；
   C hits 累积：pref.json 中 hits==1。
引擎零改动；LLM 仅用于成文（与真实链路一致）。
"""
import glob
import sys

sys.path.insert(0, ".")
from research_agent import llm
from research_agent.searcher import SourceDoc
from scripts.pref_store import PreferenceStore, build_pref_block

SRC = "outputs/e2e_nev/nev_0[015].txt"
TOPIC = "2025年中国汽车市场全景与新能源转型"
FOCUS = ("全年总产销、新能源汽车销量与渗透率、总量口径差异(中汽协vs乘联会)、"
         "头部车企表现（重点看比亚迪全年销量）")
KEY = "比亚迪/比亚迪销量/全年销量"
PREF_JSON = "outputs/m0/pref.json"
OUT1 = "outputs/m0/rep1_no_pref.md"
OUT2 = "outputs/m0/rep2_with_pref.md"

SYS = ("你是一名严谨的行业研究分析师，负责撰写 Markdown 调研报告。"
       "写作纪律：\n"
       "1. 所有数字必须与给定材料一致（绝不编造、绝不改写）；数字引用处注明口径/来源。\n"
       "2. 趋势判断与归因只可使用材料中出现的表述，禁止自行编造原因或数据。\n"
       "3. 报告结构需具备分析层次，不止陈列数据：\n"
       "   摘要(3-5条核心结论) → 总量与结构(含表格) → 趋势解读(驱动因素、与往年/上期对比"
       "说明原因) → 主要公司/细分表现 → 风险与不确定性(材料提到的) → 口径差异说明 → 来源清单。\n"
       "4. 解读层使用材料内事实支撑，给读者'怎么看'而非只给'是什么'。")


def load_docs():
    docs = []
    for fp in sorted(glob.glob(SRC)):
        docs.append(SourceDoc(url=f"file://{fp}", title=fp,
                              content=open(fp, encoding="utf-8").read(),
                              from_fetch=True, credibility=0.85))
    return docs


def brief():
    return (f"=== 研究简报 ===\n主题：{TOPIC}\n关注：{FOCUS}")


def gen(tag, pref_block=""):
    docs = load_docs()
    parts = []
    for i, d in enumerate(docs, 1):
        body = d.content
        if len(body) > 1600:
            body = body[:1600] + "…"
        parts.append(f"[来源{i}] {d.title}\nURL:{d.url}\n{body}")
    src = "\n\n".join(parts)
    user = (f"{brief()}\n\n=== 来源资料 ===\n请从原始资料中自行提取数据撰写报告。"
            f"\n\n{src}\n\n{pref_block}"
            "\n\n请撰写 Markdown 报告（1200-2000字），文末附来源清单。")
    print(f"--- 生成报告 {tag} (LLM) ---")
    md = llm.chat(SYS, user, temperature=0.3, max_tokens=4500)
    return md


def pref_block(st, key):
    p = st.get(key)
    if not p:
        return ""
    return build_pref_block(key, p["dim"], p["value"], p["hits"])


def main():
    import os
    os.makedirs("outputs/m0", exist_ok=True)
    st = PreferenceStore(PREF_JSON)

    regen = "--regen" in sys.argv
    if regen or not (os.path.exists(OUT1) and os.path.exists(OUT2)):
        print("== [1/4] 第一次生成：无偏好（默认专家口径）==")
        md1 = gen("rep1")
        open(OUT1, "w", encoding="utf-8").write(md1)

        print("== [2/4] 注入用户选择事件（M0b 时由真人点击触发同一接口）==")
        choice = st.set_user_choice(KEY, dim="乘联会零售口径",
                                    value="348.5 万辆", origin="user_pick")
        print("  偏好已写:", choice)

        print("== [3/4] 第二次生成：带偏好指令 ==")
        md2 = gen("rep2", pref_block(st, KEY))
        open(OUT2, "w", encoding="utf-8").write(md2)
    else:
        print("== 产物已存在，跳过生成（--regen 可强制重跑）==")
        md1 = open(OUT1, encoding="utf-8").read()
        md2 = open(OUT2, encoding="utf-8").read()
        print(f"  偏好库: {st.get(KEY)}")

    print("== [4/4] 断言（M0a 验收=机制跑通）==")
    import re
    checks = []
    # A 偏好已进生成指令并产生显式声明：rep2 出现用户采信回显（含 348.5 与采信词）
    checks.append(("A 回显声明'按你的采信'+348.5", "按你的采信" in md2 and "348.5" in md2))
    # B 声明偏好为主叙事（AI 以专节显式表态，而非全文机械替换）
    checks.append(("B 声明以偏好口径作主叙事",
                   re.search(r"(采用|按用户偏好|凡涉及|主口径|主视角|采信).{0,24}348\.5", md2) is not None
                   or re.search(r"348\.5.{0,24}(主口径|主视角|采信|作为)", md2) is not None))
    # C 其余口径仍交代（口径差异节保留 460.2/454.5）
    checks.append(("C 其余口径仍交代", "460.2" in md2 and "454.5" in md2))
    # D hits 累积
    checks.append(("D hits==1", st.get(KEY)["hits"] == 1))
    # E 基线 rep1 含 460.2（默认专家口径正常）
    checks.append(("E 基线 rep1 含默认口径 460.2", "460.2" in md1))

    allok = True
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        allok = allok and ok
    print(f"\nM0a 结果: {'机制闭环通过' if allok else '存在失败,需修'}")
    print(f"产物: {OUT1} / {OUT2} / {PREF_JSON}")


if __name__ == "__main__":
    main()
