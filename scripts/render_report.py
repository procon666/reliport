# -*- coding: utf-8 -*-
"""产品呈现层 v2：置信度 = 让强证据与弱证据在阅读里看起来不一样。

用法：python3 scripts/render_report.py <tag> [--pref <pref.json> --domain <域>]
读 outputs/pipes/<tag>/src + <tag>_report.md → <tag>_enh.html

v2 相对 v1 的重构（用户 2026-09 定调）：
- 取消"给每个数盖章"的 8 列置信大表（信息过载、盖章感）；
- 第一现场=正文：句子语气已由生成层按强度分级（见 STRENGTH_RULE），
  弱证据句敢示弱；数字旁的 ⓘ 锚点可下钻看完整证据链；
- 顶部=阅读地图（导航式导读：哪些可放心引用、存疑集中在哪，替代统计条）；
- 保留"多口径并陈"与"个性偏好"（偏好命中话题在锚点内标注"你的采信"）。

引擎零改动。
"""
import glob
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.strength_layer import key_evs, _match_ev, _norm, load_docs
from research_agent.analyzer import extract_evidence

KIND_CN = {"一致": "多源一致", "单源": "单一来源", "修正": "官方修正(最新)",
           "键失真": "口径混杂", "分歧-真冲突": "多源分歧·真冲突",
           "分歧-无主导": "多源分歧·无主导", "分开呈现": "无年份相对值·分别呈现"}


_REPOST_ORGS = ("counterpoint research", "counterpoint", "canalys", "omdia", "idc数据",
                "idc", "gartner", "trendforce", "cino", "cinnno", "strategy analytics",
                "中国信通院", "信通院", "中汽协", "乘联会", "艾媒咨询", "艾媒", "沙利文")
_ORG_DISPLAY = {"idc": "IDC", "idc数据": "IDC", "counterpoint": "Counterpoint",
                "counterpoint research": "Counterpoint Research", "canalys": "Canalys",
                "omdia": "Omdia", "gartner": "Gartner", "trendforce": "TrendForce",
                "cino": "CINNO", "cinnno": "CINNO", "strategy analytics": "Strategy Analytics",
                "信通院": "信通院", "艾媒咨询": "艾媒咨询", "艾媒": "艾媒咨询"}


def _org_show(w):
    return _ORG_DISPLAY.get(w, w.title())


def _repost_orgs(ev):
    """统计某 evidence 的"署名机构"集合——按句内出现的机构名（据IDC/IDC称…）。
    用于区分：多家媒体转载同一机构数据（转载扩散）vs 多家机构独立统计。"""
    orgs = set()
    for cl in ev["clusters"]:
        for c in cl["claims"]:
            hay = ((c.get("sentence") or "") + " " + (c.get("doc_title") or "")).lower()
            for w in _REPOST_ORGS:
                if w in hay:
                    orgs.add(w)
    return orgs


def _repost_note(ev):
    """转载透明度说明（不改置信等级，仅澄清）：
    '多源一致·高置信'若其实来自同一机构数据被多页传播，读者应知道它不是独立复测。
    """
    pages = set()
    for cl in ev["clusters"]:
        for c in cl["claims"]:
            raw = (c.get("url") or c.get("url_key")
                   or c.get("origin_id") or "").strip()
            if raw:
                pages.add(raw.split("?")[0].rstrip("/"))
    orgs = _repost_orgs(ev)
    if len(pages) >= 2 and len(orgs) == 1:
        org = _org_show(next(iter(orgs)))
        return (f"<div class='repost'>该口径源自同一机构（{org}），"
                f"经 {len(pages)} 个页面传播——属转载扩散，不构成独立复测。</div>")
    return ""


def _region_note(ev):
    """地区提示：同一证据涉及 ≥2 个地区/范围 → 数值不宜直接比较（假冲突澄清）。
    与对齐层的"地区轴"（spec 拼接地区）互补：引擎层的证据合并封板不可改，
    但呈现层必须如实告知"这些数不是同一范围"。"""
    regs = set()
    for cl in ev["clusters"]:
        for c in cl["claims"]:
            m = _REGION_RE.search(c.get("sentence") or "")
            if m:
                regs.add(m.group(0))
    if len(regs) >= 2:
        return (f"<div class='repost'>该证据涉及不同地区/范围（{'/'.join(sorted(regs))}）"
                f"——数值不宜直接比较。</div>")
    return ""


# 地区正则：来自领域知识层（单一事实源）——曾与 ai_alignment_demo 各持一份副本
from research_agent.domain_knowledge import REGION_RE as _REGION_RE


def _indep_pubs(ev):
    """独立稿件数：按 URL/文件 归一化去重。doc_title 现在可能已是报道标题，
    不能按标题文本做键（含特殊字符/路径）；URL 才是稳定的稿件指纹。
    旧版 file://s01.txt 与 s01.txt 不同 → 归一 basename；同稿多句 URL 相同。"""
    pubs = set()
    for cl in ev["clusters"]:
        for c in cl["claims"]:
            raw = (c.get("url") or c.get("url_key")
                   or c.get("origin_id") or "").strip()
            if raw.startswith("http"):
                key = raw.split("?")[0].rstrip("/")   # 网络原文链
            elif raw:
                key = raw.replace("file://", "").split("?")[0].rstrip("/").split("/")[-1]
            else:
                t = (c.get("doc_title") or "").strip()
                key = t if t else ""
            if key and key.lower() not in ("unknown", "none", "n/a", "file"):
                pubs.add(key)
    return max(len(pubs), 1)


def _near_year(ev):
    """年份就近识别兜底（产品层，不改引擎）：引擎未标年份但原句确实含年份时，
    仅在"该值所在句/邻域出现唯一 20xx 年份"时回填——不猜不伪造；
    句子同时含多年度（对比句/时间序列）则保持 None，宁可标"未标注"也不标错。
    """
    target = ev["adopted"]["value"]
    cand = []
    for cl in ev["clusters"]:
        for c in cl["claims"]:
            sent = c.get("sentence") or ""
            hit = None
            if target:
                for m in re.finditer(re.escape(target), sent):
                    seg = sent[max(0, m.start() - 45):m.end() + 45]
                    yrs = re.findall(r"(20\d{2})\s*年?", seg)
                    if len(set(yrs)) == 1:
                        hit = yrs[0]
                    break
            if hit is None:
                # 值未能精确定位（写法不同如 部/台）→ 整句只有一个年份才采纳
                yrs = re.findall(r"(20\d{2})\s*年?", sent)
                if len(set(yrs)) == 1:
                    hit = yrs[0]
            if hit:
                cand.append(hit)
    uniq = {y for y in cand}
    return next(iter(uniq)) if len(uniq) == 1 else None


def level_note(ev):
    """给一个 evidence 写"人话强度说明"（浮层第一行）。

    关键纠正：旧版只看 adopted.n_sources，未按稿件去重——同一篇 s01.txt 内
    多句话会被算成"2源"，假冒"多源一致·可放心引用"。修复后按独立稿件数判定：
    n>1 但 indep==1 → 实质单源，应示弱而非 hi（2026-09 用户实测发现）。
    """
    n = ev["adopted"]["n_sources"]
    nd = len(ev["clusters"]) - 1
    indep = _indep_pubs(ev)
    if nd > 0:
        return (f"{KIND_CN.get(ev['verdict']['kind'], ev['verdict']['kind'])} · "
                f"{n} 条来源 / {indep} 家独立稿件，另有 {nd} 种口径/说法并存", "dv")
    if n > 1 and indep == 1:
        # 伪多源：同一稿件内多次提及同一值——降为单源语气
        return (f"实为单一稿件 · {n} 条表述都来自同一份素材，不构成交叉验证，"
                f"正文应降低确定性引用", "lo")
    if ev["level"] == "high" and indep >= 2:
        return (f"多源一致 · {indep} 家独立稿件交叉验证，可放心引用", "hi")
    if ev["level"] == "medium" and indep >= 2:
        return (f"中等证据 · {indep} 家独立稿件，{n} 条表述", "md")
    if ev["verdict"]["kind"] == "单源" or indep == 1:
        return ("单一来源 · 未获独立交叉验证，正文应降低确定性引用", "lo")
    return (f"中等证据 · {indep} 家独立稿件", "md")


def headings_of(md):
    """行号→最近章节标题。"""
    cur = "（开头）"
    got = []
    for i, line in enumerate(md.split("\n")):
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            cur = m.group(2).strip()[:30]
        got.append(cur)
    return got


def build_records(md, srcdir, pref_topic_hit=None):
    """正文关键数字 → 下钻记录。md 渲染前调用，行号对应 headings。"""
    evs = key_evs(srcdir, max_ev=800)
    heads = headings_of(md)
    rows = []
    for li, line in enumerate(md.split("\n")):
        s = line.strip()
        if not s or s.startswith("|") or s.startswith("```") or s.startswith("#"):
            continue
        if re.match(r"^[-*+]\s", s):       # 列表行照常扫（数字可能关键）
            pass
        for m in re.finditer(r"\d+(?:\.\d+)?\s*(?:万辆|万台|亿辆|亿元|万亿元|亿美元|万元|万千升|GWh|GW|MW|%|％|万辆|万亿)?", s):
            num = m.group(0).strip()
            if not num or not re.search(r"\d", num):
                continue
            ev = _match_ev(num, evs)
            if not ev:
                continue
            note, cls = level_note(ev)
            rows.append({
                "line": li, "sec": heads[li] if li < len(heads) else "（正文）",
                "num": num,
                "ev": ev, "cls": cls, "note": note,
            })
    return rows


def row_sources(ev, cap=3):
    """证据链来源原句（最多 cap 条）。展示三要素：
      ① 报道标题（doc_title，抓取时已落盘 _meta.json）——来源可信度肉眼可核；
      ② 原链（http）可新窗口打开核对；
      ③ 本地素材文件名作小字对照。
    按 (稿件URL, 句前30字) 去重——保证多稿件各占一行（用户实测
    "说2家独立稿件但只见一行"的视觉错觉来自纯句去重）。
    """
    seen = set()
    out = []
    idx = 0
    for cl in ev["clusters"]:
        for c in cl["claims"]:
            sent = (c.get("sentence") or "").strip()
            if not sent:
                continue
            raw_url = (c.get("url") or c.get("url_key")
                       or c.get("origin_id") or "").strip()
            is_http = bool(re.match(r"https?://", raw_url))
            if is_http:
                src_key = raw_url.split("?")[0]
            elif raw_url:
                src_key = raw_url.replace("file://", "").split("?")[0].rstrip("/").split("/")[-1]
            else:
                src_key = (c.get("doc_title") or "").strip()
            key = (src_key, sent[:30])
            if key in seen:
                continue
            seen.add(key)
            idx += 1
            title = (c.get("doc_title") or "").strip()
            disp_title = title[:42] + ("…" if len(title) > 42 else "") if title else (src_key or f"来源{idx}")
            file_n = ""
            if raw_url and not is_http:
                file_n = raw_url.replace("file://", "").rsplit("/", 1)[-1]
            elif raw_url and is_http:
                file_n = ""
            out.append({"title": disp_title, "url": raw_url[:60],
                        "file": file_n, "http": is_http,
                        "sent": sent[:120]})
            if len(out) >= cap:
                return out
    return out


def _src_cell(x):
    """来源格：报道标题为主行，素材文件名小字；http 原链可点开核对（target=_blank），
    本地文件不产生假跳转。"""
    head = x["title"] or x["url"]
    esc = lambda s: (s.replace("&", "&amp;").replace('"', "&quot;")
                     .replace("<", "&lt;").replace(">", "&gt;"))
    file_note = ""
    if x.get("file") and x["file"] and x["file"] != head:
        file_note = f" <i class='su-f'>[素材 {esc(x['file'])}]</i>"
    if x["http"] and x.get("url"):
        u = esc(x["url"])
        host = re.sub(r"^https?://([^/]+).*$", r"\1", x["url"])
        return (f"<span class='su'><a href='{u}' target='_blank' "
                f"rel='noopener'>{esc(head)} ↗</a>"
                f"{file_note}<i class='su-f'>{host}</i></span>")
    return f"<span class='su'>{esc(head)}{file_note}</span>"


def render(tag, pref_file=None, domain=None):
    root = os.path.join("outputs", "pipes", tag)
    srcdir = os.path.join(root, "src")
    md_path = os.path.join(root, f"{tag}_report.md")
    raw_md = open(md_path, encoding="utf-8").read()
    md = _norm(raw_md)

    # 偏好（作用域命中）→ 供顶部卡与浮层标注
    prefs = []
    if pref_file and os.path.exists(pref_file):
        data = json.load(open(pref_file, encoding="utf-8"))
        # pref 文件即本报告域专属，全部显示（domain 过滤会误伤中英混合话题）
        for k, v in data.items():
            prefs.append((k, v))
    pref_hits = [k for k, _ in prefs]

    def _topic_hit(t):
        if not t:
            return False
        for k in pref_hits:
            if k in t or t in k or any(w and w in t for w in k.split("/")):
                return True
        return False

    # ---- 记录：报告数字 → evidence（含章节定位） ----
    evs_all = key_evs(srcdir, max_ev=800)
    records = []
    heads = headings_of(md)
    for li, line in enumerate(md.split("\n")):
        s = line.strip()
        if not s or s.startswith("|") or s.startswith("```") or s.startswith("#"):
            continue
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(万辆|万台|亿辆|亿台|亿元|万亿元|亿美元|万元|万千升|GWh|GW|MW|%|％|万|亿|台|辆|倍|点)", s):
            num, unit = m.group(1), m.group(2)
            ev = _match_ev(f"{num}{unit}", evs_all)
            if not ev:
                continue
            # 去重（同句同值）
            if any(r["num"] == f"{num}{unit}" and r["_li"] == li and r["_ev"] is ev
                   for r in records):
                continue
            note, cls = level_note(ev)
            records.append({
                "_li": li, "_ev": ev, "num": f"{num}{unit}",
                "sec": heads[li] if li < len(heads) else "（正文）",
                "cls": cls, "note": note,
                "mine": _topic_hit(ev["subject"]),
            })
    used = []
    seen = set()
    for r in records:
        if id(r["_ev"]) in seen:
            continue
        seen.add(id(r["_ev"]))
        used.append(r["_ev"])
    n_hi = sum(1 for e in used if e["level"] == "high")
    n_lo = sum(1 for e in used if e["level"] == "low")
    n_dv = sum(1 for e in used if len(e["clusters"]) > 1)
    n_md = len(used) - n_hi - n_lo

    # ---- 阅读地图卡片 ----
    lo_div = [r for r in records if r["cls"] in ("lo", "dv")]
    hi = [r for r in records if r["cls"] == "hi"]
    by_sec = {}
    for r in lo_div:
        by_sec.setdefault(r["sec"], []).append(r)
    map_list = ""
    for sec, rs in sorted(by_sec.items(), key=lambda x: -len(x[1])):
        map_list += (f"<li><b>{sec}</b>："
                     + "、".join(f"{r['num']}" for r in rs[:5])
                     + ("…" if len(rs) > 5 else "") + "</li>")
    if not map_list:
        map_list = "<li>本报告未见单一来源或口径分歧的关键数</li>"
    hi_list = "".join(f"<span class='chip hi'>{r['num']}</span>" for r in hi[:14])
    if not hi_list:
        hi_list = "<span class='muted'>（暂无——本报告关键数多为单源综述）</span>"

    w_pct = lambda a, b: round(a * 100 / b) if b else 0
    bar = (f"<div class='bar'><i class='b-hi' style='width:{w_pct(n_hi, len(used))}%'></i>"
           f"<i class='b-md' style='width:{w_pct(n_md, len(used))}%'></i>"
           f"<i class='b-lo' style='width:{w_pct(n_lo + n_dv, len(used))}%'></i></div>")

    pref_html = ""
    _picks = [(k, v) for k, v in prefs if v.get("origin", "user_pick") == "user_pick"]
    _trusts = [(k, v) for k, v in prefs if v.get("origin") == "user_trust"]
    if _picks or _trusts:
        parts = []
        if _picks:
            parts.append("<b>口径偏好</b>（按你的口径呈现，随确认累积）：<br>" + "<br>".join(
                f"· 话题「{k}」→ 你按 <b>{v['value']}</b>（{v['dim']}，已确认 {v['hits']} 次 · 非系统推荐）"
                for k, v in _picks))
        if _trusts:
            parts.append("<b>信任标记</b>（你采信过的单源口径，报告将标注）：<br>" + "<br>".join(
                f"· {k.replace('·采信','')} → 采信 <b>{v['value']}</b>（已确认 {v['hits']} 次）"
                for k, v in _trusts))
        pref_html = ("<div class='pref'>" + "<br>".join(parts) +
                     "<br><i>口径偏好决定叙述主轴；信任标记累积你的确认，不改变事实判断。</i></div>")
    else:
        pref_html = ("<div class='pref off'><b>你的偏好</b>：尚未设定。<br>"
                     "<i>遇到口径分歧处点选；单一来源数字点 ⓘ 里的『我采信』——"
                     "每一次确认都会让后续报告更贴你（个性化随交互累积）。</i></div>")

    map_html = f"""
<div class="map">
<div class="m-tt">阅读地图 · 这份报告哪里可信、哪里该警惕</div>
<p class="m-lead">本报告有 <b>{len(used)}</b> 个关键数据点：<span class="c-hi">{n_hi}</span> 个由多家独立来源互证
（可放心引用），<span class="c-md">{n_md}</span> 个中等证据，<span class="c-lo">{n_lo + n_dv}</span> 个仅单一来源或
存在口径分歧——<b>正文对这些已降低确定性语气并标 ⓘ</b>，引用前建议点开核对。</p>
{bar}
<div class="m-cols">
 <div class="m-col"><div class="m-h">🔍 建议带警惕读（集中在：）</div><ul class="m-ul">{map_list}</ul></div>
 <div class="m-col"><div class="m-h">✅ 可放心引用（高置信）</div><div class="chips">{hi_list}</div></div>
</div>
{pref_html}
</div>"""

    # ---- 正文：关键数字句注入 ⓘ 锚点 ----
    rec = []
    lines_out = []
    for li, line in enumerate(md.split("\n")):
        s = line.strip()
        if not s or s.startswith("|") or s.startswith("```") or s.startswith("#"):
            lines_out.append(line)
            continue
        line_used = False
        # 先按原行迭代收集 (end, 占位符)，再统一拼接——
        # 不能边 finditer 边改 line：连续密集数字(54%、60%、40%…)时
        # m.end() 相对旧行错位 → 占位符嵌套错乱（2026 实测漏渲 bug）。
        injects = []
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(万辆|万台|亿辆|亿台|亿元|万亿元|亿美元|万元|万千升|GWh|GW|MW|%|％|万|亿|台|辆|倍|点)", line):
            num, unit = m.group(1), m.group(2)
            ev = _match_ev(f"{num}{unit}", evs_all)
            if not ev:
                continue
            hit = next((r for r in records if r["num"] == f"{num}{unit}" and r["_li"] == li), None)
            if hit is None:
                continue
            k = len(rec)
            rec.append(hit)     # 完整复用 records 条目（含 note/cls/mine）
            # 注：占位符用 ⟦⟧ 而非 [[ ]]——方括号对会被 python-markdown 的
            # 链接识别器解析成畸形 <a href>（实测生成 <a href="同比<sup…">[ev:5]</a>），
            # 点击即跳 404「报告不存在」。⟦ev:N⟧ 对 markdown 透明，转换后再替换。
            injects.append((m.end(), f"⟦ev:{k}⟧"))
            line_used = True
        if injects:
            parts, last = [], 0
            for pos, mark in injects:
                parts.append(line[last:pos])
                parts.append(mark)
                last = pos
            parts.append(line[last:])
            line = "".join(parts)
        lines_out.append(line)
    md2 = "\n".join(lines_out)
    import markdown
    body = markdown.markdown(md2, extensions=["tables"])

    # ---- ⓘ 锚与浮层数据 ----
    ev_payload = []
    for k, r in enumerate(rec):
        ev = r["_ev"]
        cl_rows = "".join(
            f"<tr><td class='cv'>{c['value']}</td><td>{len(c['claims'])} 条来源</td></tr>"
            for c in ev["clusters"][:5])
        srcs = row_sources(ev)
        src_html = "".join(
            f"<div class='src'>{_src_cell(x)}"
            f"<span class='st'>{x['sent']}</span></div>" for x in srcs)
        if not src_html:
            src_html = "<div class='muted'>（来源句未入 claim 库）</div>"
        kind = KIND_CN.get(ev["verdict"]["kind"], ev["verdict"]["kind"])
        y_near = None
        if ev["year"]:
            y_disp = f"{ev['year']} 年"
        else:
            y_near = _near_year(ev)
            y_disp = (f"{y_near} 年*" if y_near else "年份未标注")
        label = f"{(ev['subject'] or '全局')[:24]}｜{ev['indicator']}（{y_disp}）"
        ev_payload.append({
            "i": k, "num": r["num"], "cls": r["cls"],
            "label": label, "note": r["note"], "kind": kind,
            "adopted": ev["adopted"]["value"], "n": ev["adopted"]["n_sources"],
            "indep_n": _indep_pubs(ev),
            "clusters": cl_rows, "sources": src_html, "mine": r["mine"],
            "repost": _repost_note(ev),
            "region": _region_note(ev),
            "y_near": bool(y_near),
            "trust": r["cls"] == "lo",      # 单一来源/伪多源可"我采信"
            "key": (ev["subject"] or "").strip(),
            "val": ev["adopted"]["value"],
        })
    body = re.sub(r"⟦ev:(\d+)⟧",
                  lambda mm: f'<sup class="ed {ev_payload[int(mm.group(1))]["cls"]}" '
                             f'data-k="{mm.group(1)}">ⓘ</sup>', body)

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>可信订制报告 · {tag}</title><style>
 body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1a2332;
 background:#f6f8fb;line-height:1.9;padding:24px 12px}}
 .wrap{{max-width:880px;margin:0 auto}}
 .card{{background:#fff;border:1px solid #e3e8ef;border-radius:14px;padding:22px 26px;margin-bottom:16px}}
 .map{{background:#fff;border:1px solid #dfe6f2;border-radius:14px;padding:18px 22px;margin-bottom:16px}}
 .m-tt{{font-size:16px;font-weight:800;color:#0f1c33}}
 .m-lead{{font-size:13.5px;color:#3b4863;margin:8px 0 10px}}
 .c-hi{{color:#0b7a46;font-weight:800}} .c-md{{color:#9a6b00;font-weight:800}} .c-lo{{color:#c0392b;font-weight:800}}
 .bar{{height:10px;border-radius:6px;overflow:hidden;background:#eef1f6;display:flex;margin:6px 0 14px}}
 .bar i{{display:block;height:100%}} .b-hi{{background:#1fa06a}} .b-md{{background:#d9a024}} .b-lo{{background:#d9754a}}
 .m-cols{{display:grid;grid-template-columns:1.25fr .75fr;gap:16px}}
 @media(max-width:760px){{.m-cols{{grid-template-columns:1fr}}}}
 .m-h{{font-size:13px;font-weight:700;color:#45506a;margin-bottom:6px}}
 .m-ul{{margin:0;padding-left:18px;font-size:12.8px;color:#7a4a2b}}
 .m-ul li{{margin:3px 0}}
 .chips{{line-height:2}} .chip{{display:inline-block;font-size:11.5px;background:#e9f7f1;
  color:#0b6b46;border:1px solid #bfe6d4;border-radius:10px;padding:0 8px;margin:1px 2px}}
 .muted{{color:#a3adc0;font-size:12px}}
 .pref{{background:#e9f7f1;border:1px solid #bfe6d4;border-radius:10px;padding:10px 14px;
  font-size:12.8px;color:#0b6b46;margin-top:12px}}
 .pref.off{{background:#f4f6fa;border-color:#dbe2ee;color:#45506a}}
 h1,h2,h3{{border-left:4px solid #2f6fed;padding-left:10px}}
 table{{border-collapse:collapse;margin:10px 0}} td,th{{border:1px solid #dbe2ee;padding:6px 10px;font-size:13px}}
 sup.ed{{cursor:pointer;font-size:10px;padding:0 3px;border-radius:8px;position:relative;top:-1px;user-select:none}}
 sup.ed.hi{{background:#e9f7f1;color:#0b7a46}} sup.ed.md{{background:#fdf3dd;color:#9a6b00}}
 sup.ed.lo{{background:#fdeee6;color:#c0392b}} sup.ed.dv{{background:#f0ecfb;color:#6d4bb0}}
 .evbox{{display:none;position:fixed;right:18px;top:14px;width:400px;max-height:86vh;overflow:auto;
  background:#fff;border:1.5px solid #c9d4ea;border-radius:12px;box-shadow:0 10px 34px rgba(20,35,60,.22);
  z-index:99;padding:16px 18px;font-size:13px}}
 .evbox .x{{float:right;cursor:pointer;color:#8a93a6}}
 .evbox .eb-tt{{font-size:14px;font-weight:800;margin-bottom:2px}}
 .evbox .eb-num{{font-size:17px;font-weight:800;color:#2f6fed}}
 .note{{margin:6px 0;padding:6px 10px;border-radius:8px;font-size:12.5px}}
 .note.hi{{background:#e9f7f1;color:#0b6b46}} .note.md{{background:#fdf3dd;color:#9a6b00}}
 .note.lo{{background:#fdeee6;color:#c0392b}} .note.dv{{background:#f0ecfb;color:#6d4bb0}}
 .yourpick{{color:#0b7a46;font-weight:700}}
 .src{{border-top:1px dashed #e2e7ef;padding:6px 0}}
 .repost{{background:#fff8e6;border:1px dashed #e0c878;border-radius:7px;color:#8a6416;
  font-size:11.8px;padding:5px 9px;margin:4px 0 6px}}
 .yhint{{color:#8a93a6;font-size:11.5px;margin:2px 0 4px}}
 .su{{display:block;color:#8a93a6;font-size:11px;margin-bottom:2px}}
 .su a{{color:#2f6fed;text-decoration:none;font-weight:600}}
 .su a:hover{{text-decoration:underline}}
 .su-f{{display:block;color:#b3bdcd;font-size:10.5px;word-break:break-all;margin-top:1px}}
 .st{{color:#33415e;font-size:12.3px}}
 table.evtd td{{font-size:12.3px;padding:4px 8px}} .cv{{font-weight:700}}
 .k{{display:inline-block;font-size:11px;background:#eef2fb;border-radius:6px;padding:1px 8px;color:#45506a}}
 .evbox .k{{display:inline-block;font-size:11px;background:#eef2fb;border-radius:6px;padding:1px 8px;color:#45506a}}
 .tb{{margin-top:10px;background:#fff;border:1.5px solid #12a06f;color:#0b6b46;border-radius:9px;
  padding:7px 12px;font-size:12.5px;cursor:pointer;font-family:inherit}}
 .tb:hover{{background:#e9f7f1}}
 .tst{{display:block;font-size:12px;color:#0b6b46;margin-top:5px}}
</style></head><body><div class="wrap">
{map_html}
<div class="card">{body}</div>
<div class="evbox" id="evbox">
 <span class="x" onclick="hideBox()">✕</span>
 <div class="eb-tt" id="eb-tt"></div>
 <div><span class="eb-num" id="eb-num"></span> <span class="k" id="eb-kind"></span></div>
 <div class="note" id="eb-note"></div>
 <div class="yhint" id="eb-yhint"></div>
 <div style="font-weight:700;margin:8px 0 2px;font-size:12.5px">支撑它的证据链：</div>
 <div id="eb-repost"></div>
 <div id="eb-srcs"></div>
 <div style="font-weight:700;margin:10px 0 2px;font-size:12.5px">有没有别的口径在打架：</div>
 <table class="evtd" id="eb-cl"></table>
 <button class="tb" id="eb-tb" style="display:none">✓ 我采信此来源（记住我的信任）</button>
 <span class="tst" id="eb-tst"></span>
</div>
<script>
var EV={json.dumps(ev_payload, ensure_ascii=False)};
var CURK=null;
function tagFromPath(){{var m=(location.pathname||'').match(/\/frame\/([\w-]+)/);return m?m[1]:'';}}
function hideBox(){{var box=document.getElementById('evbox');box.style.display='none';CURK=null;}}
function placeBox(rect){{
 // 定位策略：浮层"贴着锚点展开、不盖住正在读的位置"——
 // 纵向优先锚点正下方(读上文不中断)；下方放不下且上方够 → 翻上方；
 // 横向左缘对齐锚点，超右缘内收；任何情况不出视口。
 var box=document.getElementById('evbox');
 if(!box||!rect)return;
 var vw=window.innerWidth||document.documentElement.clientWidth;
 var vh=window.innerHeight||document.documentElement.clientHeight;
 var W=Math.min(430, vw-16);
 box.style.width=W+'px';
 box.style.maxHeight=(vh-16)+'px';
 box.style.display='block';            // 先显示再量高：display:none 时 offsetHeight=0，
                                        // 用 300 兜底会算错翻转位置(实测贴底锚上偏)
 var boxH=Math.min(vh-16, box.offsetHeight||300);
 var left=Math.max(8, Math.min(rect.left, vw-W-8));
 var gap=10, top;
 if(rect.bottom+gap+boxH<=vh-8){{top=rect.bottom+gap;}}
 else if(rect.top-gap-boxH>=8){{top=rect.top-gap-boxH;}}
 else{{top=Math.max(8, vh-boxH-8);}}
 box.style.left=left+'px'; box.style.top=top+'px'; box.style.right='auto';
}}
function reposCur(){{
 // 滚动/缩放时让浮层跟随锚点；锚点滚出视口则收起
 if(CURK==null)return;
 var s=document.querySelector('sup.ed[data-k="'+CURK+'"]');
 if(!s)return;
 var r=s.getBoundingClientRect();
 if(r.bottom<-40||r.top>window.innerHeight+40){{
  var b=document.getElementById('evbox'); b.style.display='none'; return;}}
 placeBox(r);
}}
window.addEventListener('scroll',reposCur,{{passive:true}});
window.addEventListener('resize',reposCur);
function showEv(k, rect){{var d=EV[k];if(!d)return; CURK=k;
 var box=document.getElementById('evbox');
 document.getElementById('eb-tt').textContent=(d.mine?'【你的采信】':'')+d.label;
 document.getElementById('eb-num').textContent=d.num+'（系统采用 '+d.adopted+'，'+d.n+' 源 / '+d.indep_n+' 家独立稿件）';
 document.getElementById('eb-kind').textContent='判定：'+d.kind;
 var nn=document.getElementById('eb-note'); nn.className='note '+d.cls; nn.textContent=d.note;
 document.getElementById('eb-yhint').textContent = d.y_near
   ? '＊ 年份为就近识别（素材原句含该年份，引擎未自动标注），请以证据原句为准。'
   : '';
 document.getElementById('eb-srcs').innerHTML=d.sources;
 document.getElementById('eb-repost').innerHTML=(d.repost||'')+(d.region||'');
 document.getElementById('eb-cl').innerHTML=d.clusters||'<tr><td class="muted">无其他口径</td></tr>';
 var tb=document.getElementById('eb-tb'), ts=document.getElementById('eb-tst');
 if(d.trust){{tb.style.display='inline-block'; ts.textContent='';}}
 else{{tb.style.display='none'; ts.textContent='';}}
 // 内容填充完成后再量高度定位（否则 offsetHeight 是空盒）
 if(rect){{placeBox(rect);}}
 else{{box.style.left='';box.style.top='';box.style.right='18px';box.style.top='14px';box.style.display='block';}}
}}
document.getElementById('eb-tb').onclick=function(){{
 var d=EV[CURK], tb=this, ts=document.getElementById('eb-tst');
 var tag=tagFromPath();
 if(!tag){{ts.textContent='无法保存：请在本地应用页面中使用（地址应为 http://127.0.0.1:5055/...，不要直接双击打开 HTML 文件）';return;}}
 tb.disabled=true;
 ts.textContent='保存中…';
 fetch('/api/pick',{{method:'POST',headers:{{'Content-Type':'application/json'}},
  body:JSON.stringify({{tag:tag, topic:d.key, spec:'单源采信', value:d.val, trust:true}})}})
 .then(function(r){{
   return r.json().then(function(j){{return {{ok:r.ok, status:r.status, j:j}};}})
            .catch(function(){{return {{ok:false, status:r.status, j:null}};}});
 }})
 .then(function(x){{
  tb.disabled=false;
  if(x.ok&&x.j&&x.j.ok){{ts.textContent='已记住：你确认过这个来源口径 '+x.j.hits+' 次。后续同话题报告将标注你的信任。';}}
  else{{ts.textContent='保存失败：'+(x.j&&x.j.err?x.j.err:('HTTP '+x.status));}}
 }}).catch(function(e){{
  tb.disabled=false;
  var why=(e&&e.message)?('('+e.message+')'):'';
  ts.textContent='请求失败'+why+'——请确认页面来自 python app.py（http://127.0.0.1:5055）而非本地文件；若服务未启动请先启动。';
 }});
}};
document.addEventListener('click',function(e){{
 var s=e.target.closest('sup.ed');
 if(s){{showEv(parseInt(s.getAttribute('data-k'),10), s.getBoundingClientRect());}}
 else if(!e.target.closest('#evbox')){{hideBox();}}
}});
</script>
</div></body></html>"""
    out = os.path.join(root, f"{tag}_enh.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"增强报告已生成: {out}")
    print(f"地图: 关键数{len(used)} = 高{n_hi}/中{n_md}/低+分歧{n_lo + n_dv} | 正文锚点 {len(rec)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    _tag = args[0] if args else "reform"
    _pf = None; _dom = None
    for a in args[1:]:
        if a.startswith("--pref="):
            _pf = a.split("=", 1)[1]
        elif a.startswith("--domain="):
            _dom = a.split("=", 1)[1]
    render(_tag, _pf, _dom)
