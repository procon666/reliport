# -*- coding: utf-8 -*-
"""M0b 最小点击入口（flask 本地服务）：真人亲手触发纠错 → rep2。

页面流：
1. GET  /        → 展示 rep1（默认专家口径版）+ 比亚迪口径选择卡
                  （被测者读到此处，亲手点"以后按这个口径看"）；
2. POST /choose  → 写偏好（PreferenceStore.set_user_choice，与 M0a 同一事件接口）
                  → 调用 LLM 生成 rep2（带偏好指令）→ 302 到 /rep2；
3. GET  /rep2    → 展示 rep2（**无系统高亮**——B 假设要求被测者自己指出变化）。

M0b 观测（主持人，见页面底部折叠区）：
B：被测者能否指出"哪里因我选过而变了"；
C：指出来是"哦改个数"还是"这玩意懂我"。
引擎零改动；纯产品层。
"""
import glob
import os

from flask import Flask, redirect, request, render_template_string
import markdown

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.pref_store import PreferenceStore, build_pref_block
from research_agent import llm
from research_agent.searcher import SourceDoc

app = Flask(__name__)

SRC = "outputs/e2e_nev/nev_0[015].txt"
KEY = "比亚迪/比亚迪销量/全年销量"
PREF_JSON = "outputs/m0b/pref.json"
REP1 = "outputs/m0/rep1_no_pref.md"
REP2 = "outputs/m0b/rep2.md"
TOPIC = "2025年中国汽车市场全景与新能源转型"
FOCUS = ("全年总产销、新能源汽车销量与渗透率、总量口径差异(中汽协vs乘联会)、"
         "头部车企表现（重点看比亚迪全年销量）")
SYS = ("你是一名严谨的行业研究分析师，负责撰写 Markdown 调研报告。"
       "要求：引用数字必须与给定材料一致（绝不编造）；来源之间有差异时如实说明；"
       "文末附来源清单。")

CHOICES = [
    ("乘联会零售口径", "348.5 万辆", "零售口径（终端上牌）"),
    ("乘联会批发口径", "454.5 万辆", "批发口径（厂家批发）"),
    ("中汽协全口径", "460.2 万辆", "含商用车全口径"),
]

PAGE = """
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>M0b 最小点击入口</title><style>
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1a2332;
 background:#f6f8fb;line-height:1.8;padding:20px 12px}
.wrap{max-width:760px;margin:0 auto}
.banner{background:#eef2fb;border:1px solid #d4def5;border-radius:10px;padding:10px 14px;
 font-size:13px;color:#33415e;margin-bottom:14px}
.card{background:#fff;border:1px solid #e3e8ef;border-radius:14px;padding:22px 26px;
 box-shadow:0 1px 4px rgba(20,30,60,.05);margin-bottom:16px}
h1{font-size:19px;margin:0 0 4px}.sub{color:#6b7686;font-size:12.5px;border-bottom:1px solid #e3e8ef;padding-bottom:10px;margin-bottom:12px}
h2,h3{border-left:4px solid #2f6fed;padding-left:10px}
table{border-collapse:collapse;margin:10px 0}td,th{border:1px solid #dbe2ee;padding:6px 12px;font-size:13.5px}
.qz{margin:14px 0;border:2px dashed #2f6fed;border-radius:12px;background:#f4f7ff;padding:14px 16px}
.qz .tt{font-weight:700;color:#16233f;margin-bottom:6px;font-size:14px}
.opts{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
.opts form{margin:0}
button{font-size:14px;padding:8px 14px;border-radius:9px;border:1px solid #b9c6dd;background:#fff;
 color:#22314f;cursor:pointer}
button:hover{border-color:#2f6fed;color:#2f6fed;background:#f4f7ff}
.wait{color:#2f6fed;font-size:13px}
.meta{color:#6b7686;font-size:12px;margin-top:8px}
.obs{background:#f3f5f9;border:1px dashed #c6cede;border-radius:10px;padding:10px 14px;
 font-size:13px;color:#45506a;margin-top:14px}
</style></head><body><div class="wrap">
<div class="banner"><b>内部测试</b> · 模拟你的"行业跟踪报告"：第一版先按默认口径生成，
你读到比亚迪销量处，可以亲手把口径改成你习惯的那个。生成第二版后对比看看。</div>
<div class="card">{% block body %}{% endblock %}</div>
</div></body></html>
"""


def _choice_card_html():
    opts = "".join(
        f'<form method="post" action="/choose">'
        f'<input type="hidden" name="dim" value="{d}">'
        f'<input type="hidden" name="val" value="{v}">'
        f'<button type="submit">以后按「{d}」({v.split()[0]})</button></form>'
        for d, v, _ in CHOICES)
    return (f'<div class="qz"><div class="tt">（系统标注）比亚迪全年销量有几个口径'
            f'都权威——你习惯按哪个看？点一下，第二版报告会按你的口径来。</div>'
            f'<div class="opts">{opts}</div></div>')


@app.route("/")
def index():
    md = open(REP1, encoding="utf-8").read()
    body = markdown.markdown(md, extensions=["tables"])
    i = body.find("<h3>4.3")
    if i < 0:
        i = len(body)
    body = body[:i] + _choice_card_html() + body[i:]
    return _html_wrap("报告 · 第一版（默认口径）", body)


def _html_wrap(title, body):
    return PAGE.replace('<div class="card">{% block body %}{% endblock %}</div>',
                        '<div class="card">' + body + '</div>').replace(
        "<title>M0b 最小点击入口</title>", f"<title>{title}</title>")


@app.route("/choose", methods=["POST"])
def choose():
    dim = request.form.get("dim", "")
    val = request.form.get("val", "")
    if not dim:
        return "缺少口径参数", 400
    os.makedirs("outputs/m0b", exist_ok=True)
    st = PreferenceStore(PREF_JSON)
    st.set_user_choice(KEY, dim=dim, value=val, origin="user_pick")
    md2 = _gen_rep2(st)
    open(REP2, "w", encoding="utf-8").write(md2)
    return redirect("/rep2")


@app.route("/rep2")
def rep2():
    md = open(REP2, encoding="utf-8").read()
    body = markdown.markdown(md, extensions=["tables"])
    obs = ('<div class="obs"><b>主持人提问清单（勿让被测者先看）：</b><br>'
           '① 这版和你刚才看的第一版相比，哪里不一样？<br>'
           '② 为什么会有这个变化？（期望答出"因为我刚才选了 X 口径"）<br>'
           '③ 感觉是"哦改了个数"还是"这玩意懂我"？</div>')
    return _html_wrap("报告 · 第二版（按你的口径）", body + obs)


def _gen_rep2(st):
    p = st.get(KEY)
    docs = []
    for fp in sorted(glob.glob(SRC)):
        txt = open(fp, encoding="utf-8").read()
        if len(txt) > 2000:
            txt = txt[:2000] + "…"
        docs.append(SourceDoc(url=f"file://{fp}", title=fp, content=txt,
                              from_fetch=True, credibility=0.85))
    src = "\n\n".join(f"[来源{i}] {d.title}\n{d.content}" for i, d in enumerate(docs, 1))
    pref = build_pref_block(KEY, p["dim"], p["value"], p["hits"])
    user = (f"=== 研究简报 ===\n主题：{TOPIC}\n关注：{FOCUS}\n\n"
            f"=== 来源资料 ===\n{src}\n\n{pref}"
            f"\n请撰写 Markdown 报告（1200-2000字），文末附来源清单。")
    print("[M0b] 生成 rep2 (LLM, 约30-60s)...")
    return llm.chat(SYS, user, temperature=0.3, max_tokens=4500)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8788, debug=False)
