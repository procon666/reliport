# -*- coding: utf-8 -*-
"""本地自检：确认代码版本 + 查看素材与拍板点状态。
在 research-agent-app 目录里运行： python selfcheck.py
把完整输出粘贴回对话即可。无需联网、不烧任何额度。
"""
import glob, json, os, re, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
OK, BAD = "✓", "✗"

def has(path, *needles):
    try:
        s = open(path, encoding="utf-8").read()
    except Exception:
        return False
    return all(n in s for n in needles)

print("=" * 60)
print("1) 代码版本检查（三项修复是否真的在这个目录里）")
ver = {
    "渲染坏链接修复(⟦ev占位)": ("scripts/render_report.py", "⟦ev:"),
    "对齐缓存指纹(_material_fp)": ("scripts/decision_layer.py", "_material_fp"),
    "英文稿+兜底机构(_FALLBACK_ORGS)": ("scripts/product_pipeline.py", "_FALLBACK_ORGS"),
    "抓取前清空素材目录": ("scripts/product_pipeline.py", "os.remove(f)"),
}
allok = True
for name, (fp, needle) in ver.items():
    ok = has(os.path.join(ROOT, fp), needle)
    allok = allok and ok
    print(f"  [{OK if ok else BAD}] {name}")
if not allok:
    print("  >>> 至少一项缺失：说明这个目录不是最新代码，覆盖没生效或覆盖错位置。")
else:
    print("  >>> 代码为最新版，覆盖成功。继续看下面产物。")

print()
print("2) outputs/pipes 下各主题产物（时间 + 拍板点数）")
pp = os.path.join(ROOT, "outputs", "pipes")
if not os.path.isdir(pp):
    print("  目录不存在:", pp)
else:
    for d in sorted(os.listdir(pp)):
        md = os.path.join(pp, d, f"{d}_report.md")
        if not os.path.isfile(md):
            continue
        dec = os.path.join(pp, d, "decisions.json")
        try:
            n_dec = len(json.load(open(dec, encoding="utf-8")))
        except Exception:
            n_dec = -1
        mt = time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(md)))
        print(f"  [{d}] 报告生成于 {mt} | decisions.json 拍板点 = {n_dec}")
        # alignment 缓存指纹
        al = os.path.join(pp, d, "alignment.json")
        if os.path.exists(al):
            a = json.load(open(al, encoding="utf-8"))
            n_src = len(glob.glob(os.path.join(pp, d, "src", "*.txt")))
            print(f"       alignment缓存: 素材{n_src}份 | 缓存指纹前40字: {(a.get('fp') or '')[:40]}")

print()
print("3) 各主题素材里的机构命中（判定抓取广度的直接证据）")
ORG = re.compile(r"Counterpoint|Canalys|Omdia|IDC|Gartner|TrendForce|CINNO|"
                 r"Strategy Analytics|中国信通院|信通院|中汽协|乘联会|艾媒|沙利文")
for d in sorted(os.listdir(pp)):
    src = os.path.join(pp, d, "src")
    fps = sorted(glob.glob(os.path.join(src, "*.txt")))
    if not fps:
        continue
    hit = {}
    for f in fps[:10]:
        t = open(f, encoding="utf-8").read()[:4000]
        for m in ORG.finditer(t):
            w = m.group(0)
            hit[w] = hit.get(w, 0) + 1
    tops = [w for w, c in sorted(hit.items(), key=lambda x: -x[1]) if c >= 1][:6]
    flag = OK if len(set(tops)) >= 2 else BAD
    print(f"  [{flag}] {d}: 素材{len(fps)}篇 | 机构命中: {tops}")

print()
print("4) 增强 HTML 是否有旧版坏链接痕迹（应为0）")
for d in sorted(os.listdir(pp)):
    enh = os.path.join(pp, d, f"{d}_enh.html")
    if not os.path.exists(enh):
        continue
    s = open(enh, encoding="utf-8").read()
    bad = s.count('href="同比') + s.count("⟦ev:") + s.count("[[ev:")
    print(f"  [{OK if bad == 0 else BAD}] {d}_enh.html 坏链接/残留 = {bad}")

print()
print("5) 运行环境")
try:
    import flask, markdown, requests
    print(f"  [✓] flask/markdown/requests 均已安装")
except Exception as e:
    print(f"  [✗] 缺依赖: {e}")
print("  目录:", ROOT)
print("=" * 60)
if allok:
    print("结论：代码已是最新版。若拍板点仍为 0，把上面 2) 和 3) 的输出发我——")
    print("       拍板点数量 = 素材里有没有 ≥2 家机构对同一指标各自报数。")
