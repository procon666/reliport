# -*- coding: utf-8 -*-
"""定制模块·决策层：自动找出报告里的"可拍板分歧点"并缓存。

"定制"的第一步不是让用户随便点，而是系统先确定**哪里值得拍板**——
同一话题存在多个统计口径/来源口径（比亚迪 460.2 全口径 vs 454.5 批发 vs
348.5 零售），这些才是用户该表态的地方（他的判断语境决定选哪个口径）。

管道：素材 → collect_claims → AI 对齐 topic/spec（一次性，结果缓存）→
build_clusters → 规范化拍板点（跨年份桶合并同 spec）→ decisions.json。

产物（引擎零改动，LLM 对齐结果落盘供报告 UI / 偏好注入复用）：
  <tag>/alignment.json   AI 对齐原始结果
  <tag>/decisions.json   拍板点列表：[{topic, domain, options:[{spec,value,note}]}]
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_CHS = ("中汽协产销", "乘联会批发", "乘联会零售", "中汽协", "乘联会", "海关")
# 机构正则：来自领域知识层（单一事实源）——曾因与 product_pipeline._ORG_SPOT_RE
# 不同步，导致游戏主题真对撞被误剔（补了抓取词表却漏了判定词表）。
from research_agent.domain_knowledge import ORG_RE as _ORG_RE
_YR_RE = re.compile(r"(20\d{2})年?")   # spec 里的年份可能是裸数字（"艾媒咨询2024·全球"）


def _ch_of(spec):
    """渠道 = 机构（+地区），**不含年份/期间词**。

    年份是"时间序列"维度而非口径渠道——同一机构 2023 与 2024 的数值不该
    被当成两个可选口径（2026-09 咖啡主题实测：'艾媒咨询2024·全球' 与
    '艾媒咨询2023·中国' 曾被算成 2 个渠道 → 伪拍板点）。
    形如 '艾媒咨询2024·全球' → '艾媒咨询·全球'；'IDC单季·全球' → 'IDC·全球'。
    """
    for w in _CHS:
        if spec.startswith(w):
            return w
    s = re.sub(r"(19|20)\d{2}\s*年?", "", spec)                 # 去年份
    s = re.sub(r"(单季|全年|预测|单月|累计|季度|当期|年)", "", s)  # 去期间词
    s = s.strip("·/ ").strip()
    return s or (spec.split("/")[0] if spec else "")


def _tm_of(spec):
    """时间粒度：单月/年份序列 → 非拍板（同一渠道多时间点=时序不是口径）。"""
    if "单月" in spec or re.search(r"[0-9０-９]+\s*月", spec):
        return "单月"
    m = _YR_RE.search(spec)
    if m:
        return "年:" + m.group(1)
    return "全年" if "全年" in spec else "总"


def _merge_spec_values(clusters):
    """跨 year 桶合并同 spec（2025键 与 年? 键的同口径值合一），2025 年优先。"""
    merged = {}          # base -> spec -> {value, note}
    for base, ys in clusters.items():
        m = merged.setdefault(base, {})
        # year 排序: 有具体年份(key非"年?")排前,覆盖"年?"
        years = sorted(ys.keys(), key=lambda y: 0 if y != "年?" else 1)
        for y in years:
            for spec, vs in ys[y].items():
                if spec == "__noise__":
                    continue
                if len(vs) != 1:          # 单 spec 多值(时间序列/噪声)不是拍板点
                    m.pop(spec, None)
                    continue
                val = vs[0]
                # 去修饰值(门槛/约/区间不进选项)
                if any(w in spec or w in val for w in
                       ("门槛", "约", "近", "上限", "下限", "左右", "预测")):
                    continue
                m[spec] = val
    return merged


def _filter_real_decisions(merged):
    """只留真拍板点：全部选项同一年份、同时间粒度(全年/总)，且渠道 ≥2。

    滤掉四类噪声：①跨年份混展示（不同年份数值不可比——只保留选项最多的那一年）；
    ②同渠道多年份时间序列（燃油车 2019-2025）；③月度数据（单月 9/10/11 月）；
    ④单渠道/同源伪对撞（"未注明"的值与某具名机构相同 = 同源转述，不算独立口径）。
    """
    out = {}
    for base, specs in sorted(merged.items()):
        if len(specs) < 2:
            continue
        # ① 年份一致性：跨年份数值不可比 → 只保留选项最多的那一年
        by_year = {}
        for spec, val in specs.items():
            m = _YR_RE.search(spec)
            by_year.setdefault(m.group(1) if m else "?", {})[spec] = val
        if len(by_year) > 1:
            yk = sorted(by_year, key=lambda k: (len(by_year[k]), k), reverse=True)[0]
            specs = by_year[yk]
            if len(specs) < 2:
                continue
        # ② 同源伪对撞：'未注明' 渠道的值若与某具名机构相同 → 同源转述，丢弃
        named_vals = {v for s, v in specs.items()
                      if not _ch_of(s).startswith("未注")}
        if named_vals:
            specs = {s: v for s, v in specs.items()
                     if not (_ch_of(s).startswith("未注") and v in named_vals)}
            if len(specs) < 2:
                continue
        # ②b 机构多样性：拍板点 = **不同机构**对同一事实的口径分歧。
        #     同机构自说自话不算——如"艾媒·全球" vs "艾媒·中国"是同一家报的
        #     不同地区市场，不是可选口径（2026-09 咖啡实测：素材仅一家机构时报出
        #     的"分歧"全是伪对撞）。"未注明"来源合并为一类。
        org_set = set()
        for s in specs:
            org = _ch_of(s).split("·")[0]
            org_set.add("未注明" if (not org or org.startswith("未注")) else org)
        if len(org_set) < 2:
            continue
        # ③ 渠道分组（渠道 = 机构+地区，不含年份）
        ch_groups = {}
        seq_noise = False
        for spec, val in specs.items():
            ch = _ch_of(spec)
            tm = _tm_of(spec)
            if tm == "单月":
                continue                     # 月度不是全年口径,排除
            ch_groups.setdefault(ch, []).append((tm, spec, val))
        # 具名机构判定：至少一个渠道来自具名机构（非"未注明"、非空）。
        # 2026-09 游戏实测：CADPA/伽马数据等**新行业机构**不在固定 _ORG_RE 词表里，
        # 导致真对撞被整条误剔除 → 改为按"是否具名"判定（不依赖固定词表，动态
        # 发现的机构自动生效）。
        has_org = any(ch and not ch.startswith("未注") for ch in ch_groups)
        # 同渠道多时间点(年:2020 / 年:2024)=时间序列→排除整个话题
        for ch, items in ch_groups.items():
            tms = {t for t, _s, _v in items}
            if len(tms) > 1:
                seq_noise = True
                break
        # 无具名机构=时间序列/单源，不是口径可选
        if seq_noise or not has_org or len(ch_groups) < 2:
            continue
        options = []
        for ch, items in ch_groups.items():
            _t, spec, val = items[0]
            # 展示口径名（保留年份信息供人辨识）
            label = spec
            options.append({"spec": label, "value": val, "ch": ch})
        if len(options) >= 2:
            out[base] = options
    return out


def _is_decision_base(base):
    """拍板点候选：销量/份额类话题（同话题多口径才有业务拍板意义）。"""
    return ("销量" in base or "份额" in base or "规模" in base or
            "零售" in base or "出口" in base)


# 对齐缓存版本：collect_claims/对齐策略变了就必须重算（2026-09 phone4 bug；
# fair-v3 = 加入地区轴 + 中英双语对齐；fair-v4 = 地区同义归一 国内/国内市场→中国）
_COLLECT_V = "fair-v4"


def _material_fp(srcdir):
    """素材指纹：文件名+大小+修改时间。素材一变，alignment 缓存必须失效重算。

    背景（用户实测）：同一 tag 二次抓取补进 Counterpoint/Omdia 后，旧
    alignment.json 仍在 → decisions.json 停留在无冲突的旧结果。
    """
    fps = sorted(glob.glob(os.path.join(srcdir, "*.txt")))
    parts = [str(len(fps))]
    for fp in fps:
        try:
            st = os.stat(fp)
            parts.append(f"{os.path.basename(fp)}:{st.st_size}:{int(st.st_mtime)}")
        except OSError:
            parts.append(os.path.basename(fp))
    return "|".join(parts)


def build_decisions(srcdir, tag, domain_hint=""):
    """对齐 → 拍板点列表。alignment 缓存仅在素材指纹+collect版本均未变时复用。"""
    root = os.path.dirname(srcdir)
    align_path = os.path.join(root, "alignment.json")
    fp_now = _material_fp(srcdir)
    data = None
    if os.path.exists(align_path):
        d = json.load(open(align_path, encoding="utf-8"))
        if d.get("fp") == fp_now and d.get("collect_v") == _COLLECT_V:
            data = d
        else:
            print(f"  [alignment] 素材/抽取版本已变化，重算对齐（collect_v={d.get('collect_v')}→{_COLLECT_V}）")
    if data is None:
        from scripts.ai_alignment_demo import collect_claims, ai_align, build_clusters
        sents = collect_claims(os.path.join(srcdir, "*.txt"))
        aligned = ai_align(sents)
        clusters, _lines, _n = build_clusters(sents, aligned)
        json.dump({"collect_v": _COLLECT_V, "fp": fp_now,
                   "sents_count": len(sents), "clusters": clusters},
                  open(align_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    else:
        clusters = data["clusters"]
    merged = _merge_spec_values(clusters)
    # 统一领域前缀（比亚迪销量 → 汽车/比亚迪销量，供作用域回退）
    dom = domain_hint or tag
    out = []
    for base, options in _filter_real_decisions(merged).items():
        out.append({"topic": base, "key": base,
                    "domain": dom, "options": options})
    # 落盘
    dec_path = os.path.join(root, "decisions.json")
    json.dump(out, open(dec_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return out


def load_decisions(tag, root=None):
    root = root or os.path.join("outputs", "pipes", tag)
    p = os.path.join(root, "decisions.json")
    if not os.path.exists(p):
        return []
    return json.load(open(p, encoding="utf-8"))


if __name__ == "__main__":
    _tag = sys.argv[1] if len(sys.argv) > 1 else "nev"
    _root = os.path.join("outputs", "pipes", _tag)
    _srcdir = os.path.join(_root, "src")
    decs = build_decisions(_srcdir, _tag, domain_hint=_tag)
    print(f"拍板点 {len(decs)} 个:")
    for d in decs:
        print(f"  [{d['topic']}] 选项: "
              + "; ".join(f"{o['spec']} {o['value']}" for o in d["options"]))
