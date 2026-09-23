# -*- coding: utf-8 -*-
"""AI 对齐 + 规则归一化闸门：最小验证。

分工：AI 管语义同指（每个数值属于哪个实体 + 什么统计口径），
     规则管结构确定（键的清洗规范化、同 entity 归簇、分歧清单自动生成）。

流程：
1. 规则 `_sentence_claims` 抽候选（销量/规模/渗透率/份额/产量/零售类 claim）；
2. 按"句"去重，把句文本 + 句内数值表交给 LLM，AI 对每个数值判
   {entity: 规范实体名(同实体必须同名), gauge: 口径(批发/零售/产销/出口/全口径…)}；
3. 规则归一化：清洗 entity（空格/引号/全半角）→ 同 entity 聚簇，
   value 单位/数值规范化，year 取规则侧解析；
4. 规则自动产出"分歧清单"：同一 entity 下口径集合 ≥2 → 逐口径列值；
5. 输出约束清单文件，供 e2e_report_demo.py 约束式 C 档使用。

用法：python3 scripts/ai_alignment_demo.py <源glob> <输出清单>
      [--topic-limit N]  每条 entity 最多保留 N 个来源值（去噪，默认 8）
"""
import glob
import json
import re
import sys

sys.path.insert(0, ".")
from research_agent import llm
from research_agent.analyzer import _sentence_claims, _value_to_number

SYS = ("你是严谨的数据核对员。我给你一段话和其中出现的数值清单，"
       "你的任务是对每个数值做两级判定：\n"
       "topic = 该数值所属的**话题**（主体+指标，如 '中国新能源汽车销量'、"
       "'比亚迪销量'、'中国汽车出口'）。同一话题必须用完全相同的名字，跨句一致；"
       "不同话题不许同名。**机构名与统计体系绝不写进 topic**，放进 spec。\n"
       "spec = **以统计机构/来源开头** + 期间，≤10字。机构优先取原文明示的"
       "（如 '据IDC'/'Canalys称'/'乘联会'/'中汽协'/'信通院'/'Counterpoint'/'Omdia'/'"
       "艾媒'/'Frost' 等，英文保留原样）；原文没机构则用 '未注明'。之后接期间："
       "全年/单季/单月/预测/出口 等。例：'IDC全年'、'Canalys单季'、'乘联会零售'、"
       "'信通院全年'、'未注明单季'。\n"
       "**同话题不同机构=不同 spec，是拍板分歧点，必须区分开**"
       "（'IDC单季' 与 'Canalys单季' 绝不能合并成同一个 spec）。"
       "同一 spec 内数值近似重复（'约700万' 与 '709.8万'）则可同 spec。\n"
       "**中英双语规则**：英文句子（如 \"Apple's share reached 21% in Q1\"）同样要处理，"
       "topic 与 spec 一律用**中文**输出；中英文若指同一话题必须**同名**"
       "（中文'苹果全球市场份额'与英文 'Apple global market share' 都输出为 '苹果全球市场份额'）。\n"
       "**地区/范围规则**：topic 必须带**地区或范围限定**（全球/中国/亚太/美国/欧洲/印度/海外…），"
       "不同地区是不同话题，绝不能合并——'全球智能手机出货量'与'中国智能手机出货量'是两个 topic，"
       "不允许因为指标相同就写成一个。\n"
       "只输出 JSON 数组，每项 {\"sid\":\"<句子id>\",\"value\":\"<原数值>\","
       "\"topic\":\"...\",\"spec\":\"...\"}，不要输出其它文字。"
       "无法判断话题时 topic 填 '未知'。")


_METRIC_IND = ("销量", "规模", "渗透率", "市场份额", "产量", "零售", "出货量",
               "出货", "营收", "收入", "净利润", "销售额", "市值", "装机",
               "用户数", "出口", "进口", "利润", "产能", "份额", "投资",
               "补贴", "成本", "均价", "单价", "库存", "增加值", "增长率",
               "增幅", "跌幅", "占比")


def collect_claims(glob_pat, max_sents=60):
    """抽"疑似数据点"句子：指标属数值类 或 值带量纲单位（跨主题通用）。

    早年白名单只有销量/规模/渗透率等，手机"出货量"、消费"营收/市值"等核心
    指标全被滤掉 → 拍板点永远偏少（2026 手机主题对照实验暴露）。放宽为
    indicator 命中数值指标集合或值含明显量纲即收，交给 AI 对齐去归类。

    配额**按文件公平轮转**（v2 修复）：旧实现按文件序累计截断到 max_sents，
    首文件若为超大综述（如 18KB 行业报告）会把全部配额吃光，排在其后的
    IDC/Counterpoint 等机构稿件一句都进不来 → alignment 无机构 → 拍板点=0
    （2026-09 phone4 实测：60 句全来自 s00.txt，s01~s05 零进入）。
    轮转抽取保证每个文件都有句子进入对齐，机构稿不再被淹没。
    """
    sentences = {}
    sid = 0
    fps = sorted(glob.glob(glob_pat))
    pools = []
    for fp in fps:
        cur = []
        for line in open(fp, encoding="utf-8"):
            # 中英双轨切句：先按中文句末标点切，再对每段按英文句末（". " 后接大写）切
            # （避免把 "21.0%" 的小数点当句末）
            segs = []
            for seg0 in re.split(r"(?<=[。！？])", line):
                segs.extend(re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", seg0))
            for s in segs:
                # 语言过滤（2026-09 中英双语）：中文句 或 含英文实词的句子都收
                if not re.search(r"[\u4e00-\u9fff]", s) and not re.search(r"[A-Za-z]{3,}", s):
                    continue
                items = []
                for c in _sentence_claims(s, fp):
                    ind = c["indicator"] or ""
                    v = c["value"] or ""
                    if not (any(w in ind for w in _METRIC_IND)
                            or re.search(r"万|亿|%|％|辆|台|千瓦|GW|GWh|元|美元|万千升", v)
                            # 英文句：数值含数字即收（指标语义交给 LLM 对齐判断）
                            or (re.search(r"[A-Za-z]{3,}", s) and re.search(r"\d", v))):
                        continue
                    items.append((c["value"], c["indicator"], c["year"]))
                if items:
                    cur.append((s[:240], items))
                    # 不在此截断总量；每文件内也设软上限防单文件霸占轮转
                    if len(cur) >= 200:
                        break
        pools.append(cur)
    # 轮转：每轮每个文件取一句，直到总配额耗尽或全部抽完
    while len(sentences) < max_sents:
        advanced = 0
        for cur in pools:
            if len(sentences) >= max_sents:
                break
            if cur:
                s, items = cur.pop(0)
                sentences[f"s{sid}"] = {"text": s, "items": items}
                sid += 1
                advanced += 1
        if advanced == 0:
            break
    return sentences


def ai_align(sentences):
    """LLM 逐句输出 entity/gauge。返回 {sid: [(value,entity,gauge)]}。"""
    rows = []
    for sid, info in sentences.items():
        vals = "、".join(f"{v}" for v, _i, _y in info["items"])
        rows.append(f'"{sid}": "{info["text"]}" (数值: {vals})')
    prompt = "\n".join(rows)
    print(f"对齐输入: {len(sentences)} 句")
    out = llm.chat(SYS, prompt, temperature=0, max_tokens=8000)
    # 容错提取 JSON：先试完整数组；失败则逐个对象解析（容忍 max_tokens 截断）。
    # 2026-09 白酒实测：60 句对齐输出超过 4000 token 被截断 → 旧逻辑要求闭合 "]"
    # 找不到 → 返回空 → 整轮对齐失败。
    m = re.search(r"\[.*", out, re.S)
    if not m:
        print("!! LLM 输出非 JSON:", out[:300])
        return {}
    body = m.group(0)
    data = None
    if "]" in body:
        try:
            data = json.loads(body[:body.rfind("]") + 1])
        except Exception:
            data = None
    if data is None:                    # 截断容错：逐个完整对象解析
        data = []
        for obj in re.finditer(r"\{[^{}]*\}", body):
            try:
                data.append(json.loads(obj.group(0)))
            except Exception:
                pass
        if not data:
            print("!! JSON 解析失败（疑似截断）:", out[:200])
            return {}
        print(f"  [对齐] 输出被截断，容错解析出 {len(data)} 条")
    res = {}
    for d in data:
        res.setdefault(d.get("sid", ""), []).append(
            (str(d.get("value", "")), d.get("topic", "未知"), d.get("spec", "")))
    return res


def norm_entity(e):
    """规则层键清洗：只做确定性规范化，不做语义归并。"""
    e = (e or "").strip().strip("\"'“”‘’ ")
    e = re.sub(r"\s+", "", e)
    e = e.replace("（", "(").replace("）", ")").replace("，", ",")
    return e


def _close(a, b):
    """近似值判定(用于同 spec 去重: 700 vs 709.8 是同一表达)。"""
    na = _value_to_number(a); nb = _value_to_number(b)
    if na and nb and na[1] == nb[1]:
        return abs(na[0] - nb[0]) / max(na[0], nb[0], 1) < 0.03
    return False


# 纯统计环节/范围词：剥离它们不改变实体语义（"比亚迪零售销量"→"比亚迪销量"）。
# 词表**只收**统计环节词；新能源/纯电/乘用车/传统燃油等属性修饰词绝不在此表，
# 防止把"纯电销量"与"总销量"误并为同一话题。
_GAUGE_STRIP = ("零售", "批发", "产销", "上牌", "终端", "国内", "国产",
                "狭义", "广义", "内销", "出口", "进口", "含商用车")
# 修饰值标记：这些词说明该数值不是精确口径值（门槛/约/区间），不进分歧清单
_NOISE_SPEC = ("门槛", "约", "近", "上限", "下限", "左右", "近似", "区间")


# 地区/范围词（中英双语）：同一指标在不同地区不是同一话题/口径，
# 必须拆分——否则 "IDC Q2 全球 -6.7%" 与 "IDC Q1 中国 -3.3%" 会被并成假冲突。
# 地区正则/归一：来自领域知识层（单一事实源）——曾有两份完全相同的副本
# （本文件与 render_report），改一处漏一处。
from research_agent.domain_knowledge import REGION_RE as _REGION_RE, region_cn as _region_cn


def _region_of(text):
    """抽取句子中的地区/范围词（中英双语）；无则返回空串。"""
    m = _REGION_RE.search(text or "")
    return m.group(0) if m else ""


# （_region_cn 已从 domain_knowledge 导入，此处不再本地定义）


def _base_topic(topic: str) -> str:
    """规则层主体标识符归一：剥纯统计环节词（确定性，词表驱动）。"""
    t = topic
    for w in _GAUGE_STRIP:
        t = t.replace(w, "")
    return t


def _spec_year(spec: str, fallback):
    """口径内若有显式年份(如'乘联会零售/全年/2020年')优先作为期间键。"""
    m = re.search(r"(20\d{2})\s*年?", spec)
    return int(m.group(1)) if m else fallback


def build_clusters(sentences, aligned):
    """规则层：base topic(剥口径词)=键，期间=spec内年份或规则claim.year，
    spec=口径维(去修饰值)，close 去重。"""
    clusters = {}                       # base -> {year -> {spec -> [values]}}
    for sid, info in sentences.items():
        for v, _ind, yr in info["items"]:
            hits = [a for a in aligned.get(sid, []) if str(a[0]) == str(v)]
            if not hits:
                continue
            topic = norm_entity(hits[0][1])
            spec = (hits[0][2] or "").strip() or "未注口径"
            # 地区轴（2026-09）：句子里的地区/范围词并入 spec —— 同机构、同时间粒度
            # 但不同地区（全球 vs 中国）不再被当成"同一口径的冲突"（假冲突修复）。
            region = _region_cn(_region_of(info["text"]))
            if region and region not in spec:
                spec = f"{spec}·{region}"
            if not topic or topic == "未知":
                continue
            base = _base_topic(topic)
            y = _spec_year(spec, yr if yr else None)
            y = y if y else "年?"
            bucket = clusters.setdefault(base, {}).setdefault(y, {})
            # 修饰值(门槛/约/区间)单独收集,不参与分歧清单
            if any(w in spec or w in v for w in _NOISE_SPEC):
                bucket.setdefault("__noise__", [])
                if not any(_close(v, x) for x in bucket["__noise__"]):
                    bucket["__noise__"].append(v)
                continue
            cur = bucket.setdefault(spec, [])
            if not any(_close(v, x) for x in cur):     # close 去重
                cur.append(v)
    lines, n_div = [], 0
    for base in sorted(clusters):
        for y, gs in clusters[base].items():
            real = {sp: vs for sp, vs in gs.items() if sp != "__noise__"}
            if len(real) >= 2:                          # 同题同年多口径
                n_div += 1
                parts = [f"{sp}:{'/'.join(sorted(set(vs)))}"
                         for sp, vs in sorted(real.items())]
                ytag = f"({y}年)" if y != "年?" else ""
                lines.append(f"- {base}{ytag}：{'；'.join(parts)}——口径差异需交代")
    return clusters, lines, n_div


def main():
    glob_pat = sys.argv[1] if len(sys.argv) > 1 else "outputs/e2e_nev/nev_0[015].txt"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/constraints_auto.txt"
    sentences = collect_claims(glob_pat)
    aligned = ai_align(sentences)
    clusters, div_lines, n_div = build_clusters(sentences, aligned)
    print(f"对齐成功 topic 簇数: {len(clusters)} | 口径分歧项: {n_div}")
    print("\n=== 自动分歧清单(前20) ===")
    for ln in div_lines[:20]:
        print(" ", ln)
    # 同 entity 无分歧的大簇也展示(验证对齐质量)
    print("\n=== 单口径大簇抽查(对齐一致性) ===")
    big = sorted(((len({v for g in gs.values() for v in g}), ent)
                  for ent, gs in clusters.items()), reverse=True)[:10]
    for n, ent in big:
        if n > 1:
            print(f"  [{n}值] {ent}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(div_lines))
    print(f"\n清单已写入 {out_path} ({len(div_lines)} 行)")


if __name__ == "__main__":
    main()
