# -*- coding: utf-8 -*-
"""主题一键流水（复用层）：任意数据型主题 → 完整可订制报告。

一条命令跑完定制模块所需全部产物：
  1. product_pipeline（检索/复用素材 → 分歧清单 → 强度账 → 分析型报告 → 出厂对账 → 示弱门禁）
  2. decision_layer   （AI 对齐 → 拍板点 decisions.json —— "哪里值得你表态"自动检出）
  3. render_report    （增强 HTML：阅读地图 + 正文ⓘ证据下钻）

之后用户只需在定制工作台点口径 → customize_cli pick → regen 即完成订制。

用法：
  python3 scripts/theme_run.py "<主题>" "<关注>" <tag> [--no-fetch] [--domain 业务域]
  python3 scripts/theme_run.py "2025年中国半导体产业" "市场规模、国产化" chip --no-fetch --domain 半导体
"""
import os
import subprocess
import sys
import json

PIPE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    args = sys.argv[1:]
    if len(args) < 3:
        print(__doc__)
        return
    topic, focus, tag = args[0], args[1], args[2]
    no_fetch = "--no-fetch" in args
    domain = ""
    for i, a in enumerate(args):
        if a == "--domain" and i + 1 < len(args):
            domain = args[i + 1]

    py = sys.executable
    root = os.path.join(PIPE, "outputs", "pipes", tag)

    print(f"== 1/3 product_pipeline ({tag}) ==")
    cmd = [py, os.path.join(PIPE, "scripts", "product_pipeline.py"),
           topic, focus, tag] + (["--no-fetch"] if no_fetch else [])
    r = subprocess.run(cmd, cwd=PIPE, capture_output=True, text=True, timeout=1500)
    out = r.stdout
    if r.returncode != 0:
        print(out[-1200:]); print("pipeline 失败"); return
    for ln in out.splitlines():
        if any(k in ln for k in ("分歧项", "强度账", "对账:", "弱证据", "裸断言",
                                 "完成:", "疑不符")):
            print("   " + ln[:140])

    print(f"== 2/3 decision_layer（拍板点自动检出）==")
    r2 = subprocess.run([py, os.path.join(PIPE, "scripts", "decision_layer.py"), tag],
                        cwd=PIPE, capture_output=True, text=True, timeout=600)
    print("   " + (r2.stdout.strip() or r2.stderr.strip()).splitlines()[-1])

    print("== 3/3 render_report（增强 HTML）==")
    cmd3 = [py, os.path.join(PIPE, "scripts", "render_report.py"), tag]
    if domain:
        pf = os.path.join(root, "pref.json")
        if os.path.exists(pf):
            cmd3 += ["--pref", pf, "--domain", domain]
    r3 = subprocess.run(cmd3, cwd=PIPE, capture_output=True, text=True, timeout=300)
    print("   " + (r3.stdout.strip() or r3.stderr.strip()).splitlines()[-1])
    # 落主题元信息（供 app 的"按偏好重新生成"复用）
    root_dir = os.path.join(PIPE, "outputs", "pipes", tag)
    try:
        os.makedirs(root_dir, exist_ok=True)
        json.dump({"topic": topic, "focus": focus, "domain": domain},
                  open(os.path.join(root_dir, "meta.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
    except Exception:
        pass
    print(f"\n主题 '{topic}' 完成。产物在 outputs/pipes/{tag}/（report.md/html · decisions.json · pref.json）")


if __name__ == "__main__":
    main()
