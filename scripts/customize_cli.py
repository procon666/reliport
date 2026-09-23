# -*- coding: utf-8 -*-
"""定制模块·执行端：拍板事件 → 偏好沉淀 → 按偏好重生成 → 重新渲染。

闭环（用户拍一次板，报告视角真的跟着变）：
  1. pick  写偏好库（PreferenceStore.set_user_choice，含 hits 累积）
  2. regen 调 product_pipeline（--no-fetch 复用素材，--pref/--domain 注入偏好）
     → 新报告以用户口径为主视角（标题/摘要/结论）；出厂对账 + 示弱门禁自动跑
  3. render_report 重新渲染增强 HTML（阅读地图 / 下钻锚点随报告更新）

用法：
  python3 scripts/customize_cli.py pick <tag> <话题key> <选项spec> <数值>
  python3 scripts/customize_cli.py regen <tag> <topic> <focus> [--domain X]

引擎零改动；全部收敛产品层。
"""
import json
import os
import subprocess
import sys

PIPE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pref_path(tag):
    return os.path.join(PIPE, "outputs", "pipes", tag, "pref.json")


def pick(tag, key, spec, value):
    from scripts.pref_store import PreferenceStore
    st = PreferenceStore(pref_path(tag))
    rec = st.set_user_choice(key, dim=spec, value=value, origin="user_pick")
    print(f"[pick] 已沉淀: {tag} | {key} → {spec} {value} | hits={rec['hits']}")
    return rec


def regen(tag, topic, focus, domain=""):
    root = os.path.join(PIPE, "outputs", "pipes", tag)
    pf = pref_path(tag)
    cmd = [sys.executable, os.path.join(PIPE, "scripts", "product_pipeline.py"),
           topic, focus, tag, "--no-fetch"]
    if os.path.exists(pf):
        cmd += [f"--pref={pf}"]
        if domain:
            cmd += [f"--domain={domain}"]
    r = subprocess.run(cmd, cwd=PIPE, capture_output=True, text=True, timeout=1200)
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    for ln in out.splitlines():
        if any(k in ln for k in ("== D", "== E", "== E2", "== F", "对账:", "弱证据",
                                 "重生成", "完成:", "疑不符", "裸断言")):
            print("   " + ln[:150])
    r2 = subprocess.run(
        [sys.executable, os.path.join(PIPE, "scripts", "render_report.py"), tag]
        + (["--pref", pf, "--domain", domain] if os.path.exists(pf) and domain else []),
        cwd=PIPE, capture_output=True, text=True, timeout=300)
    print("   " + (r2.stdout.strip() or r2.stderr.strip()).splitlines()[-1])


def main():
    args = sys.argv[1:]
    act = args[0] if args else "help"
    if act == "pick" and len(args) >= 5:
        pick(args[1], args[2], args[3], args[4])
    elif act == "regen" and len(args) >= 4:
        dom = ""
        rest = args[4:]
        for i, a in enumerate(rest):
            if a == "--domain" and i + 1 < len(rest):
                dom = rest[i + 1]
        regen(args[1], args[2], args[3], dom)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
