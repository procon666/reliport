# -*- coding: utf-8 -*-
"""可信订制报告 · 本地应用壳（产品封装）

一条命令跑起来，浏览器打开 http://127.0.0.1:5055 即可用：
  1. 首页：输入任意主题（如 "2025年中国新能源汽车行业"）→ 生成报告（约1-3分钟）
  2. 报告页：阅读地图 + 正文 ⓘ 证据下钻 + 单源数字示弱措辞（出厂对账自动跑）
  3. 定制工作台：报告里的真实口径分歧 → 点"以后按 X 口径看" → 偏好沉淀
  4. 按我的口径重新生成：报告标题/摘要/叙事随你的选择切换（偏好跨报告累积）

运行：python3 app.py
密钥：项目根 .env 配置 DEEPSEEK_API_KEY（LLM）与 TAVILY_API_KEY（联网检索）。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

from flask import Flask, jsonify, redirect, request, url_for

ROOT = os.path.dirname(os.path.abspath(__file__))
PIPES = os.path.join(ROOT, "outputs", "pipes")
PY = sys.executable

app = Flask(__name__)
_jobs = {}
_lock = threading.Lock()


def _safe_tag(tag):
    """URL/文件名安全的标识。中文主题会被清洗成空——回退成字母+短哈希，
    避免文件落到 outputs/pipes 错误层级（2026 中文主题空 tag bug）。"""
    t = re.sub(r"[^a-zA-Z0-9_-]", "", tag or "")[:24]
    if t:
        return t
    import hashlib
    return "r" + hashlib.md5((tag or "topic").encode("utf-8")).hexdigest()[:8]


def _list_domains():
    out = []
    if not os.path.isdir(PIPES):
        return out
    for name in sorted(os.listdir(PIPES)):
        d = os.path.join(PIPES, name)
        md = os.path.join(d, f"{name}_report.md")
        if not os.path.isfile(md):
            continue
        stat = os.path.getmtime(md)
        n_anch = 0
        enh = os.path.join(d, f"{name}_enh.html")
        if os.path.exists(enh):
            n_anch = len(re.findall(r"sup class=\"ed",
                                    open(enh, encoding="utf-8").read()))
        dec = os.path.join(d, "decisions.json")
        n_dec = len(json.load(open(dec, encoding="utf-8"))) if os.path.exists(dec) else 0
        pf = os.path.join(d, "pref.json")
        n_pf = len(json.load(open(pf, encoding="utf-8"))) if os.path.exists(pf) else 0
        out.append({"tag": name,
                    "mtime": time.strftime("%m-%d %H:%M", time.localtime(stat)),
                    "anchors": n_anch, "decisions": n_dec, "prefs": n_pf})
    return out


def _run_job(tag, topic, focus, regen=False):
    """后台跑生成（theme_run 全链：报告+对账+示弱门禁+拍板点+增强页）。"""
    with _lock:
        _jobs[tag] = {"state": "running", "at": time.time()}

    def _work():
        cmd = [PY, os.path.join(ROOT, "scripts", "theme_run.py"),
               topic, focus, tag]
        if regen:
            cmd.append("--no-fetch")    # 重生成复用已抓素材
        try:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                               timeout=1800)
            ok = (r.returncode == 0 and os.path.exists(
                os.path.join(PIPES, tag, f"{tag}_report.md")))
            with _lock:
                _jobs[tag] = {"state": "done" if ok else "error",
                              "at": time.time(),
                              "msg": (r.stdout[-300:] if ok
                                      else (r.stdout + r.stderr)[-700:])}
        except Exception as e:      # noqa
            with _lock:
                _jobs[tag] = {"state": "error", "at": time.time(),
                              "msg": str(e)[:400]}

    threading.Thread(target=_work, daemon=True).start()


def _page(title, body_html):
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>{title}</title><style>
 body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f2f5fa;
 color:#1a2332;line-height:1.7;padding:28px 14px}}
 .w{{max-width:820px;margin:0 auto}}
 h1{{font-size:21px;border-left:5px solid #2f6fed;padding-left:12px}}
 .sub{{color:#5a6782;font-size:13px;margin:4px 0 18px 17px}}
 .box{{background:#fff;border:1px solid #dfe6f2;border-radius:14px;padding:20px 22px;margin-bottom:16px}}
 .note{{font-size:12.5px;color:#7a5a1a;background:#fff8e8;border:1px dashed #e6cf8f;
 border-radius:9px;padding:8px 12px;margin-top:10px}}
 a.d{{display:block;background:#fff;border:1px solid #dfe6f2;border-radius:12px;padding:12px 16px;
 margin-bottom:10px;text-decoration:none;color:inherit}}
 a.d:hover{{border-color:#2f6fed;background:#f4f7ff}}
 a.d b{{font-size:15px}} a.d span{{display:block;color:#5a6782;font-size:12.5px}}
 .empty{{color:#8a93a6;font-size:13.5px;padding:6px 0}}
 form input[type=text]{{width:100%;box-sizing:border-box;font-size:14px;padding:10px 12px;
 border:1px solid #c9d4ea;border-radius:9px;margin:4px 0;font-family:inherit}}
 .btn{{background:#2f6fed;color:#fff;border:none;border-radius:9px;padding:10px 18px;font-size:14px;
 cursor:pointer;font-family:inherit}}
 .btn:hover{{background:#2658c9}}
</style></head><body><div class="w">{body_html}</div></body></html>"""


@app.route("/")
def index():
    cards = ""
    for d in _list_domains():
        cards += (f"<a class='d' href='/report/{d['tag']}'>"
                  f"<b>{d['tag']}</b>"
                  f"<span>生成于 {d['mtime']} · {d['anchors']} 证据锚点 · "
                  f"{d['decisions']} 可拍板点 · {d['prefs']} 条偏好</span></a>")
    if not cards:
        cards = "<div class='empty'>还没有报告——下面输入主题生成第一份。</div>"
    body = f"""<h1>可信订制报告</h1>
<div class="sub">多源对账 · 口径透明 · 证据下钻 · 按你的口径呈现</div>
<div class="box"><b>生成一份新主题报告</b>
<form action="/run" method="post" onsubmit="var b=this.querySelector('.btn');b.disabled=true;b.textContent='已提交，正在生成…';">
 <input name="topic" placeholder="主题，如：2025年中国新能源汽车行业" required>
 <input name="focus" placeholder="关注点（可选），如：全年产销、渗透率、头部公司">
 <input name="tag" placeholder="标识（可选，英文），如：nev">
 <button class="btn">开始生成</button>
</form>
<div class="note">首次会自动联网抓取资料，完成：出厂对账（每个数字可溯源）、示弱核查（弱证据降语气）、口径分歧检出与拍板点生成。约 1-3 分钟，期间勿关页面。</div>
</div>
<div class="box"><b>已有报告（点开 · 阅读 · 拍板 · 订制）</b>{cards}</div>"""
    return _page("可信订制报告", body)


@app.route("/run", methods=["POST"])
def run():
    topic = (request.form.get("topic") or "").strip()
    focus = (request.form.get("focus") or "").strip()
    tag = _safe_tag(request.form.get("tag") or re.sub(r"[^\w]", "_", topic)[:12])
    if not topic:
        return redirect("/")
    _run_job(tag, topic, focus)
    return redirect(url_for("wait", tag=tag))


@app.route("/wait/<tag>")
def wait(tag):
    tag = _safe_tag(tag)
    with _lock:
        st = _jobs.get(tag)
    if st and st["state"] == "done":
        return redirect(url_for("report", tag=tag))
    if st and st["state"] == "error":
        body = f"<h1>生成失败</h1><pre style='white-space:pre-wrap'>{st['msg']}</pre><p><a href='/'>返回</a></p>"
        return _page("生成失败", body)
    body = f"""<h1>正在生成「{tag}」…</h1>
<p>检索资料 → 抽取 → 出厂对账 → 示弱核查 → 报告排版</p>
<p style="color:#5a6782">首次约 1-3 分钟，本页将自动跳转；期间勿关闭。</p>
<script>setTimeout(function(){{location.reload();}},3000);</script>"""
    return _page("生成中", body)


def _report_body(tag):
    root = os.path.join(PIPES, tag)
    enh = os.path.join(root, f"{tag}_enh.html")
    plain = os.path.join(root, f"{tag}_report.html")
    fp = enh if os.path.exists(enh) else plain
    if os.path.exists(fp):
        return open(fp, encoding="utf-8").read()
    return None


def _esc(s):
    return (s.replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


@app.route("/report/<tag>")
def report(tag):
    tag = _safe_tag(tag)
    root = os.path.join(PIPES, tag)
    rbody = _report_body(tag)
    if rbody is None:
        return _page("无报告", "<h1>报告不存在</h1><p><a href='/'>返回首页生成一份</a></p>")
    decs, prefs = [], {}
    dp = os.path.join(root, "decisions.json")
    if os.path.exists(dp):
        decs = json.load(open(dp, encoding="utf-8"))
    pp = os.path.join(root, "pref.json")
    if os.path.exists(pp):
        prefs = json.load(open(pp, encoding="utf-8"))

    dec_html = ""
    for d in decs[:6]:
        cur = next((v["value"] for k, v in prefs.items() if d["topic"] in k), None)
        curtag = f"<span style='color:#0b6b46'>(你已选：{cur})</span>" if cur else ""
        opts = "".join(
            f"<button class='op' data-t='{_esc(d['topic'])}' "
            f"data-spec='{_esc(o['spec'])}' data-val='{_esc(o['value'])}'>"
            f"<span class='ch'>{_esc(o['spec'].replace('/全年',''))}</span>"
            f"<span class='vl'>{_esc(o['value'])}</span></button>"
            for o in d["options"])
        dec_html += (f"<div class='dc'><div class='dt'>{_esc(d['topic'])} {curtag}</div>"
                     f"<div class='ops'>{opts}</div>"
                     f"<div class='st' id='st-{_esc(d['topic'])}'></div></div>")
    if not dec_html:
        # 区分两种"无拍板卡"：正文有分歧标注但不可拍板 vs 素材确实没有冲突
        enh_p = os.path.join(root, f"{tag}_enh.html")
        n_dv = 0
        if os.path.exists(enh_p):
            n_dv = len(re.findall(r"class=\"ed dv\"",
                                   open(enh_p, encoding="utf-8").read()))
        if n_dv > 0:
            dec_html = (f"<div style='color:#6d4bb0;font-size:13px'>本报告正文有 "
                        f"<b>{n_dv}</b> 处标注『多口径分歧』（紫色 ⓘ，点开可看证据链）"
                        "——但多因统计口径/时间粒度不同（如 IDC 单季 vs Counterpoint 全年），"
                        "或某一方只有定性表述，未构成『同一可比口径下二选一』的可拍板选项。<br>"
                        "要看到可点选的拍板卡：素材需同一指标、同一时期出现 "
                        "≥2 家机构的可比数值。</div>")
        else:
            dec_html = ("<div style='color:#5a6782;font-size:13px'>本报告素材未检出"
                        "“多渠道口径分歧”——拍板点数量取决于素材的多源碰撞度；"
                        "证据链仍可点正文 ⓘ 查看。</div>")
    # 无论 strict 拍板卡是否为空，都追加"事实分歧（非可拍）"列表——
    # 用户视角：所有多 spec 项都应可见，列出后便于他了解所有数据维度差异
    # （2026-09 用户反馈："为啥没口径选择"）。strict 拍板卡里的 base 不重复展示。
    _shown_topics = {d["topic"] for d in decs} if decs else set()
    div_rows = []
    align_p = os.path.join(root, "alignment.json")
    if os.path.exists(align_p):
        try:
            a = json.load(open(align_p, encoding="utf-8")).get("clusters", {})
            for base, ys in a.items():
                if base in _shown_topics:
                    continue
                for y, gs in ys.items():
                    real = {s: vs for s, vs in gs.items()
                            if s != "__noise__" and len(vs) == 1}
                    if len(real) >= 2:
                        spec_items = " · ".join(
                            f"<code>{_esc(s)}</code> = {_esc(v[0])}"
                            for s, v in real.items())
                        ytag = f"({y}年)" if y != "年?" else ""
                        div_rows.append(
                            f"<li><b>{_esc(base)}</b> {ytag}：{spec_items}</li>")
        except Exception:
            pass
    if div_rows:
        div_list = ("<ul style='margin:6px 0 0 18px;padding:0;font-size:12.5px;"
                    "color:#6d4bb0;line-height:1.7'>"
                    + "".join(div_rows[:8]) + "</ul>")
        dec_html += ("<div style='margin-top:10px;padding-top:8px;border-top:1px dashed #e6ebf3'>"
                     "<div style='font-size:12.5px;color:#6d4bb0;font-weight:700'>"
                     "事实分歧（暂未构成 strict 拍板，多因口径/时间/地区不同）：</div>"
                     + div_list +
                     "<div style='color:#5a6782;font-size:12px;margin-top:6px'>"
                     "以上数据展示了所有 multi-spec 项，便于你了解全部口径差异。"
                     "它们暂不构成可拍板的'二选一'选项——但你仍可在后续报告里通过"
                     "『按我的口径重新生成』基于你的偏好按其中某一口径叙事。"
                     "</div></div>")
    pref_html = ""
    if prefs:
        _picks = {k: v for k, v in prefs.items() if v.get("origin", "user_pick") == "user_pick"}
        _trust = {k: v for k, v in prefs.items() if v.get("origin") == "user_trust"}
        rows = []
        if _picks:
            rows.append("<b>口径偏好</b>" + "".join(
                f"<br>· 「{_esc(k)}」→ {_esc(v['value'])}（{_esc(v['dim'])}，已确认 {v['hits']} 次）"
                for k, v in _picks.items()))
        if _trust:
            rows.append("<b>信任标记</b>" + "".join(
                f"<br>· {_esc(k.replace('·采信',''))} → 采信 {_esc(v['value'])}（{v['hits']} 次）"
                for k, v in _trust.items()))
        pref_html = "<div class='pc'>" + "<br>".join(rows) + "</div>"

    body = f"""<div class="top"><a href="/">← 全部报告</a><span class="t">{tag}</span>
<span style="flex:1"></span><button class="rbtn" id="rbtn">按我的口径重新生成</button></div>
<div class="wrap">
<div class="panel"><h3>定制工作台 · 报告里的真实口径分歧，选一下你的口径</h3>
<div class="hint">同一公司/市场不同机构口径数值不同（不是谁错了）。点一个：偏好沉淀，再点
“按我的口径重新生成”即按你的口径重写标题/摘要/叙事。同一领域后续报告自动套用。</div>
{dec_html}
{pref_html}</div>
<iframe id="rp" src="/frame/{tag}" style="width:100%;min-height:1700px;border:1px solid #dfe6f2;border-radius:12px;background:#fff"></iframe>
</div>
<script>
function esc(s){{return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}
document.querySelectorAll('button.op').forEach(function(b){{
 b.onclick=function(){{
  var t=b.getAttribute('data-t'),sp=b.getAttribute('data-spec'),v=b.getAttribute('data-val');
  var st=document.getElementById('st-'+t); if(!st)return;
  st.innerHTML='已记住…';
  fetch('/api/pick',{{method:'POST',headers:{{'Content-Type':'application/json'}},
   body:JSON.stringify({{tag:'{tag}',topic:t,spec:sp,value:v}})}})
   .then(function(r){{return r.json();}}).then(function(j){{
     st.innerHTML=j.ok ? ('<b>已沉淀（确认 '+j.hits+' 次）</b>：「'+t+'」→ '
        +esc(sp.replace('/全年',''))+'（'+esc(v)+'）· 点右上按钮立即生效')
        : ('失败：'+(j.err||'?')); }});
 }};}});
document.getElementById('rbtn').onclick=function(){{
 var b=this;b.disabled=true;b.textContent='重生成中(约1-3分钟)…';
 fetch('/api/regen',{{method:'POST',headers:{{'Content-Type':'application/json'}},
  body:JSON.stringify({{tag:'{tag}'}})}})
  .then(function(r){{return r.json();}}).then(function(j){{location.href=j.wait;}});
}};
</script>
<style>
 body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#eef1f6;
 color:#1a2332;margin:0}}
 .top{{position:sticky;top:0;background:#0f1c33;color:#fff;padding:10px 18px;font-size:13px;
 display:flex;gap:16px;align-items:center;z-index:9}}
 .top a{{color:#aecbff;text-decoration:none}} .top .t{{font-weight:800;font-size:15px;color:#fff}}
 .wrap{{max-width:1000px;margin:0 auto;padding:14px}}
 .panel{{background:#fff;border:1px solid #dfe6f2;border-radius:12px;padding:14px 18px;margin-bottom:12px}}
 .panel h3{{margin:0 0 8px;font-size:15px;border-left:4px solid #2f6fed;padding-left:9px}}
 .hint{{color:#5a6782;font-size:12.5px;margin-bottom:10px}}
 .dc{{margin-bottom:10px;padding-bottom:8px;border-bottom:1px dashed #e6ebf3}}
 .dc .dt{{font-weight:700;font-size:13.5px;margin-bottom:6px}}
 .ops{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}}
 button.op{{border:1px solid #c9d4ea;background:#f7f9fc;border-radius:10px;padding:8px;cursor:pointer;font-family:inherit}}
 button.op:hover{{border-color:#2f6fed;background:#eef4ff}}
 .op .ch{{display:block;font-size:12px;color:#33415e}}
 .op .vl{{display:block;font-weight:800;font-size:14px;color:#2f6fed}}
 .st{{min-height:20px;font-size:12.5px;color:#0b6b46;margin-top:6px}}
 .pc{{background:#e9f7f1;border:1px solid #bfe6d4;border-radius:10px;padding:9px 14px;font-size:12.8px;color:#0b6b46}}
 .rbtn{{background:#12a06f;color:#fff;border:none;border-radius:8px;padding:7px 14px;cursor:pointer;font-family:inherit}}
</style>"""
    return _page(f"报告 · {tag}", body)


@app.route("/frame/<tag>")
def frame(tag):
    """报告正文（iframe 独立文档，避免样式污染壳层）。"""
    rbody = _report_body(_safe_tag(tag))
    if rbody is None:
        return "报告不存在", 404
    return rbody


@app.route("/api/pick", methods=["POST"])
def api_pick():
    d = request.get_json(force=True)
    tag = _safe_tag(d.get("tag", ""))
    topic, spec, value = (d.get("topic") or "").strip(), (d.get("spec") or "").strip(), (d.get("value") or "").strip()
    trust = bool(d.get("trust"))
    if not (tag and topic and spec and value):
        return jsonify({"ok": False, "err": "参数缺失"})
    sys.path.insert(0, ROOT)
    from scripts.pref_store import PreferenceStore
    key = topic + "·采信" if trust else topic
    origin = "user_trust" if trust else "user_pick"
    rec = PreferenceStore(os.path.join(PIPES, tag, "pref.json")).set_user_choice(
        key, dim=spec, value=value, origin=origin)
    return jsonify({"ok": True, "hits": rec["hits"]})


@app.route("/api/regen", methods=["POST"])
def api_regen():
    d = request.get_json(force=True)
    tag = _safe_tag(d.get("tag", ""))
    meta_p = os.path.join(PIPES, tag, "meta.json")
    if os.path.exists(meta_p):
        meta = json.load(open(meta_p, encoding="utf-8"))
        topic = meta.get("topic") or tag
        focus = meta.get("focus") or ""
    else:
        md = os.path.join(PIPES, tag, f"{tag}_report.md")
        if not os.path.exists(md):
            return jsonify({"ok": False, "err": "无该报告"})
        first = open(md, encoding="utf-8").read(600)
        m = re.search(r"主题[:：]\s*(.{2,80})", first)
        topic = m.group(1).strip() if m else tag
        focus = ""
    _run_job(tag, topic, focus, regen=True)
    return jsonify({"ok": True, "wait": f"/wait/{tag}"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5055"))
    print("=" * 52)
    print("可信订制报告 · 本地应用")
    print(f"打开: http://127.0.0.1:{port}")
    print("密钥: 见项目根 .env（DEEPSEEK_API_KEY / TAVILY_API_KEY）")
    print("=" * 52)
    app.run(host="0.0.0.0", port=port, threaded=True)
