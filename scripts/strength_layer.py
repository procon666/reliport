# -*- coding: utf-8 -*-
"""证据强度层：把"证据强弱"变成报告里看得见、可核查的分层。

产品原则（用户 2026 定义）：置信度不是给每个数盖章，而是让强证据与弱证据在
报告里**看起来不一样**——强证据正常断言；弱证据句子敢示弱（据X单一来源、
未获交叉验证）。本模块服务两层：
1. 生成前：strength_lines() 产出"证据强度账"注入生成指令（约束式措辞）；
2. 生成后：strength_audit() 核查弱证据句是否真的示弱了（规则纠错闸门）。

引擎零改动（只读 extract_evidence / _sentence_claims）。
"""
import glob
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research_agent.analyzer import extract_evidence, _value_to_number
from research_agent.searcher import SourceDoc

from scripts.e2e_audit_demo import _NUM_RE, _UNIT_DIM, _DEFAULT_DIM

# 示弱标记词：句中出现即说明作者降低了确定性并给了可复核语境。
_WEAK_MARK = ("据", "发布", "显示", "披露", "报告称", "称", "约", "近", "左右",
              "单一来源", "单源", "一源", "未获", "未经", "未", "尚无", "仅",
              "或为", "估计", "逾", "略超", "报道", "援引", "记载", "注明",
              "上赛季", "当赛季", "对应赛季", "当季")
# 可复核信号：①年份锚(年/赛季/财年/季度) ②来源标号(来源3/s01…) ③时序/对比结构
_ANCHOR_RE = re.compile(
    r"(?:19|20)\d{2}\s*(?:年|赛季|财年|H1|H2|上半年|下半年|季度)?|"
    r"(?:来源|援引)\s*\d+|s\d{2}|"
    r"(?:由|从)\S{0,10}?(?:至|提升|升至|到|下降|回落)|"
    r"比(?:19|20)\d{2}\s*(?:年|赛季)?|同比|环比")
_STRONG_UGLY = ("仅此一源", "单一来源", "未获交叉验证", "只此一家")

_LEVEL_CN = {"high": "高", "medium": "中", "low": "低"}


def load_docs(srcdir):
    """读素材（*.txt）+ 配套 _meta.json（{basename:{title,url}}）。
    meta 是抓取时落盘的报道标题/原链——让证据链显示"报道标题"而非干文件名，
    来源可信度肉眼可核（用户需求：把引用的报道标题带出来）。
    """
    meta = {}
    mp = os.path.join(srcdir, "_meta.json")
    if os.path.exists(mp):
        try:
            meta = json.load(open(mp, encoding="utf-8"))
        except Exception:
            meta = {}
    docs = []
    for fp in sorted(glob.glob(os.path.join(srcdir, "*.txt"))):
        bn = os.path.basename(fp)
        m = meta.get(bn, {})
        title = (m.get("title") or bn).strip() or bn
        url = m.get("url") or f"file://{bn}"
        docs.append(SourceDoc(url=url, title=title,
                              content=open(fp, encoding="utf-8").read(),
                              from_fetch=True, credibility=0.85))
    return docs


def evs_of(srcdir, max_ev=500):
    return extract_evidence(load_docs(srcdir), max_evidence=max_ev)


def _num_eq(a, b):
    na, nb = _value_to_number(a), _value_to_number(b)
    if na and nb:
        return na[1] == nb[1] and abs(na[0] - nb[0]) < 1e-9
    return False


def _close(a, b):
    """同量纲 3% 内近似（用于'约700万'与'709.8万'类同源表达，仅作兜底）。"""
    na, nb = _value_to_number(a), _value_to_number(b)
    if na and nb and na[1] == nb[1]:
        return abs(na[0] - nb[0]) / max(na[0], nb[0], 1) < 0.03
    return False


def _match_ev(value, evs):
    """报告里的一个数值串 → 命中的 evidence（精确优先，close 兜底）。"""
    for e in evs:
        vals = [e["adopted"]["value"]] + [c["value"] for c in e["clusters"]]
        if any(_num_eq(value, x) for x in vals):
            return e
    for e in evs:
        vals = [e["adopted"]["value"]] + [c["value"] for c in e["clusters"]]
        if any(_close(value, x) for x in vals):
            return e
    return None


def _norm(s):
    return unicodedata.normalize("NFKC", s or "")


def key_evs(srcdir, max_ev=500):
    """关键 evidence：subject 可读（非纯指标残）、adopted 值含可解析数值。"""
    evs = evs_of(srcdir, max_ev)
    out = []
    for e in evs:
        if not _NUM_RE.search(e["adopted"]["value"] or ""):
            continue
        subj = (e["subject"] or "").strip()
        if not subj:
            continue
        out.append(e)
    out.sort(key=lambda e: (-len(e["clusters"]), -e["adopted"]["n_sources"]))
    return out


def strength_lines(srcdir, cap=40):
    """证据强度账 → markdown 行（注入生成指令，约束措辞）。"""
    evs = key_evs(srcdir)
    if not evs:
        return []
    lines = []
    for e in evs[:cap]:
        v = e["adopted"]["value"]
        n = e["adopted"]["n_sources"]
        kind = e["verdict"]["kind"]
        subj = (e["subject"] or "全局")[:26]
        y = f"{e['year']}年" if e["year"] else "年?"
        if e["level"] == "high" and len(e["clusters"]) == 1:
            tag = "[多源一致·高置信]"
        elif e["level"] == "medium":
            tag = "[中等证据]"
        elif kind in ("单源",) or n == 1:
            tag = "[单一来源·低置信]"
        else:
            tag = f"[{kind}·需并陈]"
        lines.append(f"- 强度账 {tag} {subj}｜{e['indicator']}（{y}）：{v}"
                     f"（{n} 家独立来源"
                     + (f"；另有口径 {len(e['clusters']) - 1} 种" if len(e["clusters"]) > 1 else "")
                     + "）")
    return lines


def sentence_matches(md, srcdir):
    """报告 md → [(num, unit, sent, ev)]：句子粒度匹配证据（供核查/渲染）。"""
    evs = key_evs(srcdir, max_ev=800)
    md = _norm(md)
    out = []
    for line in md.split("\n"):
        if not line.strip() or line.lstrip().startswith("|") or line.strip().startswith("```"):
            continue
        if re.match(r"^#{1,6}\s", line) or re.match(r"^[-*+]\s", line):
            continue
        for seg in re.split(r"(?<=[。！？])", line):
            if not re.search(r"\d", seg):
                continue
            for m in _NUM_RE.finditer(seg):
                num, unit = m.group(1), m.group(2)
                ev = _match_ev(f"{num}{unit}", evs)
                if ev:
                    out.append((num, unit, seg.strip(), ev))
    # 去重：同一句同一证据只记一次
    seen = set()
    uniq = []
    for num, unit, sent, ev in out:
        k = (id(ev), num)
        if k in seen:
            continue
        seen.add(k)
        uniq.append((num, unit, sent, ev))
    return uniq


def strength_audit(md_path, srcdir, verbose=False):
    """核查"句子敢示弱"：低置信/单源/分歧的数字，其所在句是否带降格措辞。

    返回 (stat, misses)。misses 每项 = (num, unit, sent, ev) 弱证据句未示弱。
    这是规则纠错闸门——LLM 措辞执行度不稳定，须可测。
    """
    md = open(md_path, encoding="utf-8").read()
    rows = sentence_matches(md, srcdir)
    n_weak = 0
    n_ok = 0
    misses = []
    for num, unit, sent, ev in rows:
        weak = (ev["level"] == "low") or len(ev["clusters"]) > 1
        if not weak:
            continue
        n_weak += 1
        # 可复核判定（整句信号）：降格词 / 年份锚 / 来源标号 / 时序对比结构。
        # 时序锚定句（"由1978年的1.8%升至2017年15%"）自带可复核语境，不算裸断言。
        if any(w in sent for w in _WEAK_MARK) or _ANCHOR_RE.search(sent):
            n_ok += 1
        else:
            misses.append((num, unit, sent, ev))
    stat = {"weak_total": n_weak, "weak_marked": n_ok,
            "weak_unmarked": len(misses)}
    return stat, misses


def semantic_audit(md_path, srcdir, verbose=False):
    """语义级对账（2026-09 新增）：数字在素材里能找到，但**单位/年份/主体**与素材句
    不一致 → 报问题。补出厂对账的盲区：原对账只查"数字有没有出处"，
    抓不住"2.9% 写成 3%""2.775亿部写成亿台""2025 写成 2026"这类语义篡改。

    返回 (stat, issues)；issues 每项 = (类型, 数字, 报告句, 素材句摘录)。
    """
    md = open(md_path, encoding="utf-8").read()
    rows = sentence_matches(md, srcdir)          # (num, unit, sent, ev)
    issues = []
    for num, unit, sent, ev in rows:
        src_sents = []
        for cl in ev["clusters"]:
            for c in cl["claims"]:
                s = (c.get("sentence") or "").strip()
                if s:
                    src_sents.append(s)
        if not src_sents:
            continue
        src = " ".join(src_sents[:3])
        # ① 单位一致性（比对数值后的完整单位串，如 亿台 vs 亿部 / %vs 万）
        u_rep = _unit_near(sent, num)
        u_src = _unit_near(src, num)
        if u_rep and u_src and u_rep != u_src:
            issues.append(("单位不一致", f"{num}{u_rep}", sent,
                           f"素材为 {num}{u_src}"))
        # ② 年份一致性（两边都有明确年份且无交集 → 年份不符）
        y_rep = set(re.findall(r"(20\d{2})", sent))
        y_src = set(re.findall(r"(20\d{2})", src))
        if y_rep and y_src and not (y_rep & y_src):
            issues.append(("年份不一致", f"{num}{unit}", sent,
                           f"素材年份 {'/'.join(sorted(y_src))}"))
        # ③ 主体一致性（仅提示，且只对"干净主体"校验）：
        # 引擎抽出的 subject 常是碎片（"苹果以"、"本榜排序主口径为…"），
        # 这类一律跳过，避免误报；只校验 2~6 字、无标点/动词残片的干净主体。
        subj = (ev.get("subject") or "").strip()
        _frag = ("最初", "最后", "目前", "当前", "本榜", "排序", "主口径", "注",
                 "数据", "报告", "显示", "披露", "预计", "约为", "以上", "以下")
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z]{2,8}", subj) and \
                not re.search(r"[以为的和：:，,。、]", subj) and \
                not any(w in subj for w in _frag):
            core = re.sub(r"(全球|中国|亚太|北美|美国|欧洲|印度|海外|智能手机|手机|"
                          r"市场|份额|出货量|销量|规模|同比|增长率|跌幅|占比)", "", subj)
            if len(core) >= 2 and core not in sent:
                issues.append(("主体待核", f"{num}{unit}", sent, f"证据主体「{subj}」"))
    stat = {}
    for k, *_ in issues:
        stat[k] = stat.get(k, 0) + 1
    return stat, issues


_UNIT_NEAR_RE = re.compile(
    r"(%|％|万辆|万台|亿辆|亿台|亿部|万部|亿元|万亿元|亿美元|万元|万千升|"
    r"亿|万|台|辆|部|美元|元|人|倍|点)")


def _unit_near(text, num):
    """取文本中某数值紧随其后的单位词（用于单位一致性比对）。"""
    i = text.find(num)
    if i < 0:
        return ""
    seg = text[i + len(num): i + len(num) + 6]
    m = _UNIT_NEAR_RE.match(seg)
    return m.group(0) if m else ""


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else "outputs/pipes/reform"
    d0 = os.path.join(p, "src")
    if os.path.isdir(d0):
        md = os.path.join(p, os.path.basename(p) + "_report.md")
        st, ms = strength_audit(md, d0)
        print("strength_audit:", st)
        for num, unit, sent, ev in ms[:8]:
            print(f"  [未示弱] {num}{unit} | {sent[:60]}")
    else:
        print("用法: strength_layer.py <tag>, 或 python3 strength_layer.py outputs/pipes/reform")
