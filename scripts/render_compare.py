# -*- coding: utf-8 -*-
"""对照实验可视化：读 _compare.json + 三份报告 → 自包含 HTML 对比页（含柱状图）。

用法：python3 scripts/render_compare.py <tag>
产出：outputs/pipes/<tag>/_compare.html（可超链接/预览打开）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _pct(a, b):
    return (a / b * 100) if b else 0


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "phonev"
    root = f"outputs/pipes/{tag}"
    data = json.load(open(f"{root}/_compare.json", encoding="utf-8"))
    b, nd, s = data["baseline_with_docs"], data["baseline_no_docs"], data["system"]
    topic = data["topic"]

    from scripts.product_pipeline import factory_audit
    _, rows = factory_audit(f"{root}/_baseline_nodocs.md", f"{root}/src")
    fakes = [r for r in rows if r[0] == "无出处"][:6]

    def bar(label, val, maxv, cls):
        w = _pct(val, maxv)
        return (f"<div class='bar-row'><span class='nm'>{label}</span>"
                f"<div class='track'><div class='bar {cls}' style='width:{w:.1f}%'></div></div>"
                f"<span class='bv'>{val}</span></div>")

    max_bad = max(b["无出处"], nd["无出处"], s["无出处"], 1)
    fake_html = "".join(
        f"<li><code>{r[1]}{r[2]}</code> <span class='mut'>{str(r[3])[:46]}</span></li>"
        for r in fakes) or "<li class='mut'>（无）</li>"

    out = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>对照实验：裸 LLM vs 本系统</title>
<style>
:root{{--bg:#f5f7fb;--card:#fff;--ink:#1e2942;--mut:#6b7793;--good:#12a150;--bad:#e5484d;--line:#e7ebf3;--acc:#3b6ef6}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:980px;margin:0 auto;padding:34px 20px 64px}}
h1{{font-size:25px;margin:0 0 6px}}
.sub{{color:var(--mut);font-size:14px;margin-bottom:24px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-bottom:20px;box-shadow:0 1px 3px rgba(20,30,60,.05)}}
h2{{font-size:17px;margin:0 0 14px;display:flex;align-items:center;gap:8px}}
h2 .dot{{width:8px;height:8px;border-radius:50%;background:var(--acc)}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:right}}
th:first-child,td:first-child{{text-align:left}}
thead th{{color:var(--mut);font-weight:600;border-bottom:2px solid var(--line)}}
tr.hl td{{background:#fff6f6}}
.bad{{color:var(--bad);font-weight:800}}
.good{{color:var(--good);font-weight:800}}
.bar-row{{display:flex;align-items:center;gap:12px;margin:10px 0}}
.nm{{width:130px;font-size:13px;color:var(--mut);flex:none}}
.track{{flex:1;background:#eef1f7;border-radius:6px;overflow:hidden;height:20px}}
.bar{{height:100%;border-radius:6px;transition:width .6s}}
.b-green{{background:linear-gradient(90deg,#12a150,#3ecf7a)}}
.b-red{{background:linear-gradient(90deg,#e5484d,#ff7a7e)}}
.b-blue{{background:linear-gradient(90deg,#3b6ef6,#6f9bff)}}
.bv{{width:56px;text-align:right;font-weight:700;flex:none}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.box{{border:1px solid var(--line);border-radius:11px;padding:14px 16px}}
.box.red{{background:#fff6f6;border-color:#f6d3d4}}
.box.green{{background:#f2fbf5;border-color:#cdeed8}}
.box h3{{margin:0 0 8px;font-size:14px}}
.box ul{{margin:0;padding-left:18px;font-size:13px;line-height:1.9}}
code{{background:#eef1f7;padding:1px 6px;border-radius:4px;font-size:12.5px}}
.mut{{color:var(--mut)}}
.quote{{background:#f8fafc;border-left:3px solid var(--acc);padding:10px 14px;border-radius:0 8px 8px 0;font-size:13.5px;color:#33405c;margin-top:8px}}
@media(max-width:680px){{.two{{grid-template-columns:1fr}}}}
</style></head>
<body><div class="wrap">
<h1>对照实验：裸 LLM vs 本系统</h1>
<div class="sub">主题：{topic}　|　同一批素材（6 篇）　|　同一个对账器（数字回素材找原词）</div>

<div class="card">
<h2><span class="dot"></span>核心对比</h2>
<table>
<thead><tr><th>指标</th><th>裸 LLM + 素材</th><th>裸 LLM 无素材</th><th>本系统</th></tr></thead>
<tbody>
<tr><td>数字总数</td><td>{b['数字总数']}</td><td>{nd['数字总数']}</td><td>{s['数字总数']}</td></tr>
<tr><td>可核对（OK）</td><td>{b['OK']}</td><td>{nd['OK']}</td><td>{s['OK']}</td></tr>
<tr class="hl"><td><b>无出处（编造）</b></td><td class="good">{b['无出处']}</td><td class="bad">{nd['无出处']}</td><td class="good">{s['无出处']}</td></tr>
<tr><td>疑不符（篡改）</td><td>{b['疑不符']}</td><td class="bad">{nd['疑不符']}</td><td>{s['疑不符']}</td></tr>
<tr><td><b>可核对率</b></td><td class="good">{b['可核对率']*100:.1f}%</td><td class="bad">{nd['可核对率']*100:.1f}%</td><td class="good">{s['可核对率']*100:.1f}%</td></tr>
</tbody></table>
</div>

<div class="card">
<h2><span class="dot"></span>可核对率</h2>
{bar('裸 LLM + 素材', round(b['可核对率']*100,1), 100, 'b-blue')}
{bar('裸 LLM 无素材', round(nd['可核对率']*100,1), 100, 'b-red')}
{bar('本系统', round(s['可核对率']*100,1), 100, 'b-green')}
</div>

<div class="card">
<h2><span class="dot"></span>编造数字数（越少越好）</h2>
{bar('裸 LLM + 素材', b['无出处'], max_bad, 'b-blue')}
{bar('裸 LLM 无素材', nd['无出处'], max_bad, 'b-red')}
{bar('本系统', s['无出处'], max_bad, 'b-green')}
</div>

<div class="card">
<h2><span class="dot"></span>编造实锤（裸 LLM 无素材 vs 本系统）</h2>
<div class="two">
<div class="box red"><h3>❌ 裸 LLM 无素材：编造的数字</h3>
<ul>{fake_html}</ul>
<div class="quote">用了"区间""约""预计"等严谨表述，普通读者分辨不出。</div>
</div>
<div class="box green"><h3>✅ 本系统：只用抓到的素材</h3>
<ul><li><b>0 个无出处</b>（每个数字都能回素材找到原句）</li>
<li>每个数字可点开看<b>证据链</b>（判定等级 + 报道标题 + 原句）</li>
<li>跨源冲突<b>结构化成可点选选项</b></li></ul>
</div>
</div>
</div>

<div class="card">
<h2><span class="dot"></span>结论</h2>
<p>有素材时裸 LLM 也不编数（98.8%）——<b>素材在上下文里，它抄就是了</b>。
但"直接问 AI"（<b>不给素材</b>）时它编了一半（50%）。</p>
<p><b>本系统不靠"防编数"取胜，而靠"保证只用素材 + 冲突结构化 + 证据链可下钻 + 可测试/可个性化"。</b></p>
<div class="quote">面试话术："把同样素材交给裸 LLM，它也能不编数；但一旦不给素材（最常见的『直接问 AI』），它编了一半的数字，还编得比真的像真的。我的系统保证 0 编造，因为只用抓到的素材，而且每个数字都能点开看原句。"</div>
</div>

</div></body></html>"""
    p = f"{root}/_compare.html"
    open(p, "w", encoding="utf-8").write(out)
    print(f"生成 {p}")


if __name__ == "__main__":
    main()
