# -*- coding: utf-8 -*-
"""产品一体化管线（冲刺版）：任意主题 → 可信订制报告。

用法：
  python3 scripts/product_pipeline.py "<主题>" "<关注点>" --tag <标识> [--dir outputs/pipes]
  python3 scripts/product_pipeline.py "2025年中国医药行业" "营收利润、创新药" --tag pharma

步骤：
 A 检索：tavily 多查询取候选 URL
 B 抓取：bs4 正文提取，数据密集过滤（中文>1000 且 数字>55）
 C 抽取：_sentence_claims 抽候选；AI 对齐 topic/spec（复用 ai_alignment_demo 的
    collect_claims/ai_align/build_clusters）→ 规则剥口径词归键 → 自动分歧清单
 D 生成：分析型报告（默认专家口径；若偏好库有命中则注入偏好块——作用域树回退）
 E 出厂对账：报告数字 vs 来源全文 逐条核对（OK/概算/疑不符/无出处）
 F 呈现：报告 markdown → HTML（含来源清单）

引擎零改动（analyzer 只读调用）；LLM 用于 AI 对齐与成文。
"""
import glob
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research_agent.analyzer import _sentence_claims
from research_agent.searcher import Searcher
from research_agent import llm
# 领域知识层（单一事实源）：机构/地区/英文词表集中定义，避免"改一处漏一处"
from research_agent.domain_knowledge import (
    ORG_RE as _ORG_SPOT_RE, ORG_DOMAIN as _ORG_DOMAIN,
    AUTHORITY_DOMAINS as _AUTHORITY_DOMAINS, EN_MAP as _EN_MAP,
    FALLBACK_ORGS as _FALLBACK_ORGS,
)

PIPE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"}

SYS = ("你是一名严谨的行业研究分析师，负责撰写 Markdown 调研报告。\n"
       "写作纪律：\n"
       "1. 所有数字必须与给定材料一致（绝不编造、绝不改写）；数字引用处注明口径/来源。\n"
       "   **任何素材中找不到原词的数字，一律不写**——宁可跳过该指标，也绝不用记忆/常识补数；\n"
       "   若一段叙事确需年份节点而素材未给数字，只作定性表述（'上世纪九十年代前后'），不写精确数字。\n"
       "2. 趋势判断与归因只可使用材料中出现的表述，禁止自行编造原因或数据。\n"
       "3. 报告结构需具备分析层次，不止陈列数据：\n"
       "   摘要(3-5条核心结论) → 总量与结构(含表格) → 趋势解读(驱动因素、与往年/上期对比"
       "说明原因) → 主要公司/细分表现 → 风险与不确定性(材料提到的) → 口径差异说明 → 来源清单。\n"
       "4. 解读层使用材料内事实支撑，给读者'怎么看'而非只给'是什么'。\n"
       "5. 报告篇幅服从可用素材：若素材数据稀疏，宁要短而全可溯源，不要长而夹带无源数字。")

# 措辞分级规范（产品红线：让强证据与弱证据在句子里看起来不一样）
STRENGTH_RULE = (
    "=== 证据强度与措辞规范（必须逐条遵守，这是报告的置信度表达）===\n"
    "以下『强度账』给每条关键数据标了证据强度。正文引用时，**句子的语气必须随强度分层**：\n"
    "· [多源一致·高置信]：可正常断言，不加限定词（例：\"2024年新能源销量1649万辆\"）。\n"
    "· [中等证据]：正常陈述，但注明来源口径（例：\"据中汽协口径，……1649万辆\"）。\n"
    "· [单一来源·低置信]：句子**必须降低确定性**并指明仅一源，禁止斩钉截铁。写法示例：\n"
    "   \"据乘联会单一来源，比亚迪2024年零售销量约为348.5万辆（仅此一源，未获交叉验证）\"。\n"
    "· 标［另有口径 N 种］或含其他口径：不得二选一隐瞒，按『口径差异说明』规则并陈交代。\n"
    "强度账之外未列出的数字照常按来源资料引用。\n"
    "自检：写完全文后回头读一遍——如果每个数字句都像在发誓，说明没有执行本规范。\n"
)


# ---------- A/B 检索与抓取 ----------
def search_urls(topic, focus, max_urls=10):
    s = Searcher(provider="tavily")
    qs = [f"{topic} 数据 报告", f"{topic} 全年 增长 亿元", f"{topic} 规模 同比 {focus}"]
    urls = []
    for q in qs:
        try:
            for r in s.search(q, max_results=5):
                if r.url and r.url not in urls:
                    urls.append(r.url)
        except Exception as e:
            print(f"  [search] {q[:20]} ERR {e}")
        time.sleep(0.4)
    return urls


def _readable_ratio(txt: str) -> float:
    """正文可读性：中英文/数字/标点占比（防二进制乱码文件混入素材）。"""
    if not txt:
        return 0.0
    n = len(txt)
    good = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9，。、；：""''（）%％·…\-—0-9]", txt))
    return good / n


def fetch_text(url, max_bytes=600_000):
    """返回 (页面标题, 正文文本)。标题供证据链展示（提升来源可信度）。"""
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    title = ""
    t = soup.find("title")
    if t and t.string:
        title = " ".join(t.string.split())[:180]
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    body = "\n".join(l.strip() for l in soup.get_text("\n").split("\n")
                     if len(l.strip()) > 40
                     or (len(l.strip()) > 20 and re.search(r"\d|。|\.|%|％", l)))
    if len(body) > max_bytes:          # 异常超大文件多半混入乱码/冗余
        return title, ""
    if _readable_ratio(body) < 0.55:   # 二进制/乱码拒收
        return title, ""
    return title, body[:max_bytes]


# 统计/研究机构词（第二轮聚焦抓取用）：长词在前避免子串吞并
# 机构识别正则：来自领域知识层（单一事实源）domain_knowledge.ORG_RE
# （原此处本地定义已迁移——曾与 decision_layer._ORG_RE 不同步，导致补机构"漏一处"）


def _top_orgs(texts, top_n=3):
    """从已抓文本统计出现最多的机构（"对立机构"二次检索的候选）。"""
    cnt = {}
    for t in texts:
        for m in _ORG_SPOT_RE.finditer(t):
            w = m.group(0).strip()
            if not w:
                continue
            cnt[w] = cnt.get(w, 0) + 1
    return [w for w, _ in sorted(cnt.items(), key=lambda x: -x[1])][:top_n]


# 机构→官网域名 / 权威域：来自领域知识层（单一事实源）domain_knowledge
# 指标词（用于把"机构+主题"的检索词从硬编码"出货量"改为按主题派生）
_METRIC_WORDS = ("出货量", "销量", "市场份额", "份额", "市场规模", "规模", "营收",
                 "收入", "净利润", "渗透率", "产量", "均价", "出口", "装机",
                 "用户数", "增长率", "同比", "占比")


def _metric_hint(topic, focus):
    """从主题/关注点派生指标词，替代硬编码的"出货量 报告"（跨行业通用）。"""
    hay = (focus or "") + " " + (topic or "")
    hits = []
    for w in _METRIC_WORDS:
        if w in hay and w not in hits:
            hits.append(w)
    return " ".join(hits[:3]) if hits else "数据 报告"


def _discover_orgs(topic, focus="", n=8):
    """动态机构发现：用 LLM 从主题推断该行业的主要数据统计机构。

    动机（2026-09 光伏实测）：固定的 _FALLBACK_ORGS 是"已测行业"累积的
    （科技/汽车/消费），对未覆盖的行业（光伏）完全不对口——pass2 拿兜底名单里的
    IDC 去搜光伏，抓回一堆 IDC 手机/打印稿，污染整条管线。改为按主题动态发现
    该行业的权威机构（如光伏 → InfoLink / 中国光伏行业协会 / Enerdata），
    新行业不再依赖预置词表。失败时返回空列表，由调用方回退固定名单。
    """
    try:
        from research_agent.llm import chat
        sys_p = ("你是行业研究专家。给定一个研究主题，请列出该行业/领域**最权威的"
                 "数据统计机构、市场研究机构或行业协会**（用于检索行业数据报告）。"
                 "中英文名均可，优先**行业专有**机构（而非通用科技机构）。"
                 "只输出一个 JSON 数组，如 [\"机构A\",\"机构B\"]，不要任何解释。")
        usr = (f"主题：{topic}\n关注点：{focus or '（无）'}\n"
               f"列出 6-8 个该领域最权威、最可能发布相关数据的机构。")
        txt = chat(sys_p, usr, temperature=0.2, max_tokens=400)
        m = re.search(r"\[.*?\]", txt, re.S)
        if m:
            arr = json.loads(m.group(0))
            out = [str(x).strip() for x in arr if str(x).strip()]
            return out[:n]
    except Exception as e:
        print(f"  [机构发现] 失败（{type(e).__name__}: {e}）→ 回退固定名单")
    return []


# 中文指标词 → 英文（英文检索式用）
_EN_METRIC = {
    "出货量": "shipments", "销量": "sales", "市场份额": "market share", "份额": "share",
    "市场规模": "market size", "规模": "market size", "营收": "revenue", "收入": "revenue",
    "净利润": "net profit", "利润": "profit", "渗透率": "penetration", "产量": "production",
    "均价": "average selling price", "单价": "price", "出口": "exports", "进口": "imports",
    "装机": "installations", "用户数": "users", "增长率": "growth rate",
    "同比": "year-over-year", "占比": "share",
}
_EN_TOPIC_CACHE = {}


def _en_metric_hint(topic, focus):
    hay = (focus or "") + " " + (topic or "")
    out = []
    for zh, en in _EN_METRIC.items():
        if zh in hay and en not in out:
            out.append(en)
    return " ".join(out[:3])


def _en_topic(topic, focus):
    """主题 → 英文检索词（LLM 一次翻译，失败回退到行业词映射）。

    中英双语检索的入口：中文 query 抓中文报道，英文 query 抓机构官网/英文媒体原稿。
    """
    key = (topic or "")[:80]
    if key in _EN_TOPIC_CACHE:
        return _EN_TOPIC_CACHE[key]
    en = ""
    try:
        out = llm.chat(
            "你是检索词翻译助手。把用户给的中文行业主题翻译成简洁的英文检索词："
            "3~6 个单词，保留年份与核心行业词，不要引号、不要解释、只输出这一行。",
            f"{topic} {focus}".strip(), temperature=0, max_tokens=60)
        en = " ".join((out or "").split())[:80]
    except Exception:
        en = ""
    if not en:
        pats = _en_patterns(topic)
        en = " ".join(re.sub(r"[\\()|]", " ", p) for p in pats[:2])
        en = " ".join(en.split())[:80]
    _EN_TOPIC_CACHE[key] = en
    return en


# 主题词提取的停用词（年份/地域/通用词，去掉后剩下行业实词）
_TOPIC_STOP = ("中国", "全球", "全国", "行业", "市场", "产业", "发展", "现状", "趋势",
               "报告", "数据", "分析", "研究", "情况", "前景", "格局", "变化", "2026",
               "2025", "2024", "2023", "年")


def _topic_terms(topic):
    """从主题提取"行业核心词"（2~4 字滑窗），用于素材相关性校验。"""
    t = re.sub(r"(19|20)\d{2}\s*年?", "", topic or "")
    for w in _TOPIC_STOP:
        t = t.replace(w, " ")
    terms = set()
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", t):
        for n in (2, 3, 4):
            for i in range(len(seg) - n + 1):
                terms.add(seg[i:i + n])
    return terms


# 主题中文词 → 英文关键词（英文素材的相关性校验用）
# 主题中文词 → 英文关键词：来自领域知识层（单一事实源）domain_knowledge.EN_MAP


def _en_patterns(topic):
    return [en for zh, en in _EN_MAP.items() if zh in (topic or "")]


# 标题相关性判定用的"泛词"——出现在标题里不足以证明稿件主题就是它。
# 2026-09 宠物实测：艾媒《新消费趋势白皮书》正文顺带提"宠物经济"赛道，被正文
# 命中判定放行；但其标题不含"宠物" → 用标题闸门可精准拒收。
_GENERIC_TERMS = ("经济", "消费", "规模", "增长", "同比", "环比", "市场", "行业",
                  "产业", "趋势", "发展", "数据", "报告", "分析", "研究", "情况",
                  "前景", "格局", "变化", "中国", "全球", "全国", "调查", "洞察")


def _core_terms(topic):
    """主题的"专有词"（去泛词、去纯数字）——标题相关性判定用。
    区分"稿件主题就是它"（标题含"宠物"）与"顺带提及"（"新消费白皮书"提宠物）。"""
    out = set()
    for w in _topic_terms(topic):
        if re.search(r"\d", w) or w in _GENERIC_TERMS:
            continue
        if len(w) >= 2:
            out.add(w)
    return out


def _title_ok(title, core, en_pats):
    """标题必须命中主题专有词（中文核心词或英文关键词）。无标题返回 True。"""
    if not title:
        return True
    if core and any(w in title for w in core):
        return True
    if en_pats and any(re.search(p, title, re.I) for p in en_pats):
        return True
    return False


def _is_relevant(text, terms, url="", en_pats=None, need=2, title="", core=None):
    """主题相关性校验：素材开头命中主题词才算相关，否则拒收。

    动机（2026-09 实测）：对"机构词表不覆盖"的主题（如城市轨道交通——数据源是
    交通部/协会），pass2 兜底名单会抓来 IDC 博客归档页、半导体研讨会、运动营养
    食品等无关素材，甚至被对撞体检误判成"多机构对撞"。这道闸门把抓取入库的素材
    全过一遍，无关的直接拒收——宁少勿错。

    英文素材分支：英文稿无法用中文词校验，**必须命中主题的英文关键词**（_EN_MAP）
    才放行——只看域名会把"权威站的研讨会日程页/博客归档页"也放进来（实测：
    TrendForce 半导体页、IDC 博客归档页），所以域名不再作为放行条件。
    """
    # 标题闸门（2026-09 宠物实测）：标题不含主题专有词 → 拒收。艾媒《新消费趋势
    # 白皮书》正文顺带提"宠物经济"会被正文命中判定放行，但其标题不含"宠物"——
    # 用标题把"主题就是它"与"顺带提及"精准区分开。
    # 灰区（2026-09 量化）：标题泛化（如"IDC发Q2全球报告，最大的赢家是苹果三星"
    # 讲的是手机）会误伤。判据：正文**高频**提及主题专有词（≥8 次）→ 交 LLM 仲裁。
    if title and core:
        if _title_ok(title, core, en_pats):
            return True        # 标题含主题专有词 → 直接放行
        # 标题不含专有词：正文高频提及主题专有词 → 灰区（交 LLM）；否则拒收
        # （2026-09 白酒实测：标题含"白酒"的稿子曾被随后的正文判定误拒——正文
        #  判定用的"产量与/量与营"是滑窗碎片，真实文本几乎不出现 → 命中不足）
        cnt = sum(text.count(w) for w in core)
        return "grey" if cnt >= 8 else False
    head = text[:8000]
    cn = len(re.findall(r"[\u4e00-\u9fff]", head))
    en = len(re.findall(r"[A-Za-z]{3,}", head))
    if cn < 200 and en > 100:                     # 英文为主的素材
        if en_pats and any(re.search(p, head, re.I) for p in en_pats):
            return True
        return False
    if not terms:
        return True
    # 长词（3~4 字）判别力强、2 字词过泛（"城市/交通/规模"各行各业都有）——
    # 要求命中 ≥2 个长词，或 1 个长词 + ≥3 个短词；避免"半导体稿含'城市'"
    # 这类误放（2026-09 城轨实测：AI半导体稿/自动驾驶稿曾被误收）。
    strong = [w for w in terms if len(w) >= 3]
    weak = [w for w in terms if len(w) == 2]
    sh = sum(1 for w in strong if w in head)
    wh = sum(1 for w in weak if w in head)
    if strong:
        return sh >= 2 or (sh >= 1 and wh >= 3)
    return wh >= 3


def _llm_relevance(title, text, topic):
    """灰区仲裁：规则拿不准时，用 LLM 判断稿件主题是否与 topic 一致。

    仅在"标题不含专有词但正文高频提及主题"的少数稿上调用（成本可控）。
    动机（2026-09 量化）：标题闸门对"泛标题"稿（如"…最大的赢家是苹果三星"
    讲的是手机）会误伤，实测误伤率约 15%。LLM 只兜这一层灰区，**不参与数值抽取**。
    """
    try:
        from research_agent.llm import chat
        out = chat(
            "你是检索质量审核员。判断给定文章的标题与开头，是否**以给定主题为核心"
            "内容**（而非顺带提及）。只回答『是』或『否』，不要任何解释。",
            f"主题：{topic}\n标题：{title}\n开头：{text[:600]}",
            temperature=0, max_tokens=4)
        return "是" in (out or "")
    except Exception:
        return False          # LLM 失败 → 保守拒收（宁少勿错）


def _collision_probe(srcdir):
    """对撞体检（轻量规则，不烧 LLM）：统计"同一指标词下出现的机构数"。

    返回 (ok, multi_items, gaps)：
      multi_items = [(指标, [机构...])]  被 ≥2 家机构提到的指标 → 有对撞
      gaps        = [指标]               只有 1 家机构提到的指标 → 缺对撞
    动机：两轮抓取"抓到多机构名"不等于"抓到多口径对撞"（phone4 实测 4 家机构
    仍 0 拍板点）。抓完必须体检，不达标再定向补抓，才能从"靠运气"变"有保障"。
    """
    pair = {}
    for fp in sorted(glob.glob(os.path.join(srcdir, "*.txt"))):
        try:
            txt = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        for sent in re.split(r"[。！？\n]", txt):
            orgs = [m.group(0).strip() for m in _ORG_SPOT_RE.finditer(sent)]
            if not orgs:
                continue
            for w in _METRIC_WORDS:
                if w in sent:
                    pair.setdefault(w, set()).update(orgs)
    multi = [(m, sorted(o)) for m, o in pair.items() if len(o) >= 2]
    gaps = [m for m, o in pair.items() if len(o) == 1]
    multi.sort(key=lambda x: -len(x[1]))
    return (len(multi) > 0), multi, gaps


def harvest(topic, focus, srcdir, want=6):
    """两轮聚焦抓取：先泛抓几篇 → 统计文中机构 → 围绕机构二次补抓对立稿。

    动机（用户实测归因）：单轮泛抓每次只命中一家机构（如 IDC），Canalys/
    Counterpoint/Omdia 等对立数据明明存在却抓不到 → 拍板点天然偏少。
    第二轮用"主题 + 机构名"定向检索，把同指标的多家机构说法都收进来，
    拍板/定制才有地基。素材质量过滤不变（可读性 + 数据密度）。
    """
    os.makedirs(srcdir, exist_ok=True)
    # 抓取前清空素材目录：sXX.txt 编号从 0 重来，残留旧稿会造成
    # "新旧混料"——同一 tag 二次跑时旧素材还在，报告/对齐被稀释（用户实测）。
    for f in glob.glob(os.path.join(srcdir, "*.txt")):
        os.remove(f)
    meta_p = os.path.join(srcdir, "_meta.json")
    if os.path.exists(meta_p):
        os.remove(meta_p)
    saved, seen_urls, texts = [], set(), []
    meta = {}        # basename -> {title, url}（证据链展示报道标题用）
    topic_terms = _topic_terms(topic)     # 主题核心词（相关性闸门用）
    topic_core = _core_terms(topic)       # 主题专有词（标题闸门用）
    en_pats = _en_patterns(topic)         # 主题英文关键词（英文素材校验用）
    en_topic = _en_topic(topic, focus)    # 英文检索词（中英双语检索用）
    en_metric = _en_metric_hint(topic, focus)
    got = 0

    def _save(u):
        nonlocal got
        try:
            title, txt = fetch_text(u)
        except Exception:
            return False
        # 相关性闸门：与主题无关的素材直接拒收（防 pass2 兜底名单/权威域广搜跑偏）
        rel = _is_relevant(txt, topic_terms, u, en_pats, title=title, core=topic_core)
        if rel == "grey":                     # 规则拿不准 → LLM 仲裁（仅少数稿）
            rel = _llm_relevance(title, txt, topic)
            print(f"  [灰区仲裁] {'放行' if rel else '拒收'} {u[:44]}")
        if not rel:
            print(f"  [skip-rel] 与主题无关 {u[:46]}")
            return False
        cn = len(re.findall(r"[\u4e00-\u9fff]", txt))
        nn = len(re.findall(r"\d", txt))
        enw = len(re.findall(r"[A-Za-z]{3,}", txt))
        # 中英双轨放行：中文稿看中文字数；英文稿(Canalys/Counterpoint/Omdia 官网
        # 原稿、英文媒体)按英文词数+数据密度。旧判据只认中文 → 国际机构原稿全被
        # 滤掉，多机构对立素材结构性偏窄（用户实测手机主题只进中文转述）。
        if (cn > 900 and nn > 45) or (enw > 320 and nn > 30):
            fn = os.path.join(srcdir, f"s{got:02d}.txt")
            open(fn, "w", encoding="utf-8").write(txt)
            meta[f"s{got:02d}.txt"] = {"title": title, "url": u}
            texts.append(txt)
            saved.append(u)
            got += 1
            print(f"  [src] {len(txt)}B 中{cn}/英{enw}/数{nn} | {title[:40]} | {u[:40]}")
            return True
        print(f"  [skip] 中{cn}/英{enw}/数{nn} {u[:48]}")
        return False

    def _grab(urls, limit):
        for u in urls:
            if got >= limit:
                return
            if u in seen_urls:
                continue
            seen_urls.add(u)
            try:
                _save(u)
            except Exception as e:
                print(f"  [fetch-err] {u[:44]} {type(e).__name__}")
            time.sleep(0.3)

    # ---- 第一轮：泛抓（中文多角度 + 1 篇英文原稿）----
    first_want = min(3, want)
    print(f"  [pass1 泛抓 至 {first_want} 篇 | 英文检索词: {en_topic or '—'}]")
    urls1 = search_urls(topic, focus)
    _grab(urls1, max(first_want - 1, 1))
    if en_topic and got < first_want:
        s_en = Searcher(provider="tavily")
        for q in (f"{en_topic} {en_metric} report", f"{en_topic} market data"):
            if got >= first_want:
                break
            try:
                us = [r.url for r in s_en.search(q, max_results=4)
                      if r.url and r.url not in seen_urls]
            except Exception as e:
                print(f"  [search-en] ERR {e}")
                us = []
            _grab(us, first_want)

    # ---- 第二轮：围绕检出的机构补抓对立稿 ----
    orgs = _top_orgs(texts, top_n=min(3, want))
    # pass2 第一优先：稿内已检出的机构（对题）。
    # 第二优先：**动态机构发现**——按主题推断该行业权威机构（2026-09 新增）。
    #   替代固定兜底名单：光伏主题若用固定名单里的 IDC 去搜，会抓回 IDC 手机/
    #   打印稿污染管线。固定名单仅在动态发现失败时兜底。
    discovered = _discover_orgs(topic, focus)
    if discovered:
        extra = discovered
        print(f"  [机构发现] {' / '.join(discovered[:6])}")
    else:
        extra = list(_FALLBACK_ORGS)
    pool = []
    for o in list(orgs) + list(extra):
        if o not in pool:
            pool.append(o)
    if pool and got < want:
        print(f"  [pass2 机构聚焦: {' / '.join(pool[:6])}… → 补至 {want} 篇]")
        s = Searcher(provider="tavily")
        metric = _metric_hint(topic, focus)

        def _search_urls(q, n, domains=None):
            out = []
            try:
                for r in s.search(q, max_results=n, include_domains=domains):
                    if r.url and r.url not in seen_urls:
                        out.append(r.url)
            except Exception as e:
                print(f"  [search] {q[:26]} ERR {e}")
            return out

        for org in pool:
            if got >= want:
                break
            # ① 先在机构官网域内搜（拿一手稿）——优先英文检索词（官网多为英文站）
            dom = _ORG_DOMAIN.get(org)
            if dom:
                if en_topic:
                    _grab(_search_urls(f"{en_topic} {en_metric}", 3, [dom]), want)
                    if got >= want:
                        break
                _grab(_search_urls(f"{topic[:26]} {metric}", 3, [dom]), want)
                if got >= want:
                    break
            # ② 退回通用检索（机构名 + 主题 + 派生指标词；再补一次英文）
            _grab(_search_urls(f"{org} {topic[:22]} {metric}", 4), want)
            if got < want and en_topic:
                _grab(_search_urls(f"{org} {en_topic} {en_metric}", 3), want)
            time.sleep(0.4)
    print(f"  素材共 {got} 篇 | 检出机构: {orgs}")

    # ---- 第三轮：对撞闭环（体检 → 缺对撞则定向补抓）----
    # 动机：抓到"多机构名"≠抓到"多口径对撞"。抓完必须体检；不达标时围绕
    # 缺口指标做对撞导向检索（"各机构对比"、权威域广搜），把"靠运气"变"有保障"。
    try:
        ok, multi, gaps = _collision_probe(srcdir)
        print(f"  [对撞体检] 多机构指标 {len(multi)} 项"
              + (f"：{multi[0][0]}[{'/'.join(multi[0][1])}]" if multi else "")
              + f" | 单机构缺口 {len(gaps)} 项")
        if not ok and gaps:
            s3 = Searcher(provider="tavily")
            cap3 = want + 2                     # 第三轮最多多抓 2 篇
            # 对撞补抓的机构域/关键词：优先"动态发现机构"，避免硬编码科技机构
            # （光伏主题若硬搜 IDC 域会抓回手机稿）。无动态发现则退回通用检索。
            dyn_orgs = discovered or orgs
            dyn_doms = [_ORG_DOMAIN[o] for o in dyn_orgs if o in _ORG_DOMAIN]
            org_kw = " ".join(dyn_orgs[:3]) or "各机构"
            print(f"  [pass3 对撞补抓] 缺口指标: {'/'.join(gaps[:3])} | 机构: {org_kw} → 上限 {cap3} 篇")
            for gap in gaps[:2]:
                if got >= cap3:
                    break
                # ① 定向广搜该指标（有动态机构域则域内搜，否则通用）
                qs3 = [f"{topic[:26]} {gap} 2026"]
                if en_topic:
                    qs3.append(f"{en_topic} {en_metric}")
                for q3 in qs3:
                    if got >= cap3:
                        break
                    urls3 = []
                    try:
                        for r in s3.search(q3, max_results=4,
                                           include_domains=(dyn_doms or None)):
                            if r.url and r.url not in seen_urls:
                                urls3.append(r.url)
                    except Exception as e:
                        print(f"  [search] pass3 域内 ERR {e}")
                    _grab(urls3, cap3)
                if got >= cap3:
                    break
                # ② 对撞导向关键词（动态机构组合 / 口径对比）
                urls4 = []
                try:
                    for r in s3.search(f"{topic[:24]} {gap} {org_kw} 对比 差异",
                                       max_results=4):
                        if r.url and r.url not in seen_urls:
                            urls4.append(r.url)
                except Exception as e:
                    print(f"  [search] pass3 对比 ERR {e}")
                _grab(urls4, cap3)
                time.sleep(0.4)
            ok2, multi2, gaps2 = _collision_probe(srcdir)
            print(f"  [对撞体检·补抓后] 多机构指标 {len(multi2)} 项"
                  + (f"：{multi2[0][0]}[{'/'.join(multi2[0][1])}]" if multi2 else "")
                  + f" | 剩余缺口 {len(gaps2)} 项")
    except Exception as e:
        print(f"  [对撞体检] 跳过（{type(e).__name__}: {e}）")

    # 收尾：把标题/URL 元数据落盘（证据链显示报道标题）
    try:
        json.dump(meta, open(meta_p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass
    return got


# ---------- C 分歧清单（复用 ai_alignment_demo 组件） ----------
def divergence_list(srcdir):
    from scripts.ai_alignment_demo import collect_claims, ai_align, build_clusters
    glob_pat = os.path.join(srcdir, "*.txt")
    sents = collect_claims(glob_pat)
    if not sents:
        return [], {}
    aligned = ai_align(sents)
    clusters, lines, n = build_clusters(sents, aligned)
    return lines, clusters


# ---------- D 生成（分析型；可选偏好注入 + 强度账措辞） ----------
def gen_report(topic, focus, srcdir, pref_lines=None, strength_lines=None,
               retry_feedback=""):
    docs = []
    fps = sorted(glob.glob(os.path.join(srcdir, "*.txt")))
    for fp in fps:
        txt = open(fp, encoding="utf-8").read()
        docs.append(txt)
    src = "\n\n".join(f"[来源{i+1}] {os.path.basename(fp)}\n{txt[:2200]}"
                      for i, (fp, txt) in enumerate(zip(fps, docs)))
    user = (f"=== 研究简报 ===\n主题：{topic}\n关注：{focus}\n\n=== 来源资料 ===\n{src}\n\n")
    if pref_lines:
        user += ("=== 你的用户口径偏好（决定叙述主轴）===\n" + "\n".join(pref_lines) + "\n\n")
    user += STRENGTH_RULE
    if strength_lines:
        user += "\n".join(strength_lines) + "\n\n"
    if retry_feedback:
        user += ("=== 复核反馈（上次版本以下句子违反措辞规范，必须改写后融入本版）===\n"
                 + retry_feedback + "\n\n")
    user += "请撰写 Markdown 报告（1800-2800字），文末附来源清单（标题+URL）。"
    return llm.chat(SYS, user, temperature=0.3, max_tokens=5000)


# ---------- E 出厂对账 ----------
def factory_audit(md_path, srcdir):
    from scripts.e2e_audit_demo import audit
    rows, _ = audit(md_path, os.path.join(srcdir, "*.txt"))
    import collections
    stat = dict(collections.Counter(r[0] for r in rows))
    return stat, rows


# ---------- F HTML 呈现 ----------
def to_html(md_text, out_path):
    import markdown
    body = markdown.markdown(md_text, extensions=["tables"])
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>可信订制报告</title><style>
 body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1a2332;
 background:#f6f8fb;line-height:1.85;padding:26px 12px}}
 .wrap{{max-width:820px;margin:0 auto;background:#fff;border:1px solid #e3e8ef;
 border-radius:14px;padding:28px 34px}}
 h1,h2,h3{{border-left:4px solid #2f6fed;padding-left:10px}}
 table{{border-collapse:collapse;margin:10px 0}} td,th{{border:1px solid #dbe2ee;
 padding:6px 12px;font-size:13.5px}}
 .aq{{color:#8a5a1a}} blockquote{{border-left:3px solid #c9d4ea;margin-left:0;padding-left:12px;color:#33415e}}
</style></head><body><div class="wrap">{body}</div></body></html>"""
    open(out_path, "w", encoding="utf-8").write(html)
    return out_path


def main():
    args = sys.argv[1:]
    # --pref=<json> --domain=<领域> 传入偏好库（作用域注入）；--no-fetch 复用已有素材
    pref_file = None; domain = None; no_fetch = False
    keep = []
    for a in args:
        if a.startswith("--pref="):
            pref_file = a.split("=", 1)[1]
        elif a.startswith("--domain="):
            domain = a.split("=", 1)[1]
        elif a == "--no-fetch":
            no_fetch = True
        else:
            keep.append(a)
    topic = keep[0] if keep else "2025年中国新能源汽车行业"
    focus = keep[1] if len(keep) > 1 else "全年产销、渗透率、头部公司"
    tag = keep[2] if len(keep) > 2 else "p"
    root = os.path.join(PIPE_ROOT, "outputs", "pipes", tag)
    srcdir = os.path.join(root, "src")
    os.makedirs(srcdir, exist_ok=True)

    if not no_fetch:
        print(f"== A 检索+抓取（{tag}）==")
        n = harvest(topic, focus, srcdir)
        if n == 0:
            print("!! 无素材，退出"); return
        print(f"   素材 {n} 份")
    else:
        n = len(glob.glob(os.path.join(srcdir, "*.txt")))
        print(f"== A 跳过抓取（--no-fetch），复用素材 {n} 份 ==")

    print("== C 自动分歧清单 ==")
    div_lines, clusters = divergence_list(srcdir)
    print(f"   分歧项 {len(div_lines)}")
    for ln in div_lines[:12]:
        print("   ", ln)
    # 把"口径分歧需交代"作为报告呈现要求（并入偏好行，不给答案只出题）
    pref_lines = ([f"- 提示：{ln}" for ln in div_lines[:10]] if div_lines else [])

    print("== C2 证据强度账（注入措辞分级）==")
    from scripts.strength_layer import strength_lines as _sl
    slines = _sl(srcdir)
    print(f"   强度账 {len(slines)} 条")

    print("== D 生成分析型报告（默认专家口径 + 偏好注入 + 分歧提示 + 强度措辞）==")
    from scripts.pref_store import PreferenceStore, build_pref_block
    if pref_file and os.path.exists(pref_file):
        st = PreferenceStore(pref_file)
        dom = domain or topic.split("年")[-1].split("行业")[0] or topic[:2]
        user_prefs = []
        for k, v in st.data.items():
            if v.get("origin", "user_pick") != "user_pick":
                continue    # 只把"口径选择"注入生成主轴；单源信任标记不改变叙述
            head = (k or "").split("/", 1)[0]
            if dom and (dom in k or k in dom or dom in head or head in dom):
                user_prefs.append(build_pref_block(k, v["dim"], v["value"], v["hits"]))
        if user_prefs:
            pref_lines = user_prefs + pref_lines
            print(f"   注入用户偏好 {len(user_prefs)} 条（domain≈{dom}）")
    md = gen_report(topic, focus, srcdir, pref_lines, slines)
    md_path = os.path.join(root, f"{tag}_report.md")
    open(md_path, "w", encoding="utf-8").write(md)

    print("== E 出厂对账 + 示弱核查 + 语义对账（统一门禁）==")
    from scripts.strength_layer import strength_audit as _sa, semantic_audit as _sema
    stat, rows = factory_audit(md_path, srcdir)
    sstat, smisses = _sa(md_path, srcdir)
    semstat, semissues = _sema(md_path, srcdir)
    bad = [r for r in rows if r[0] in ("疑不符", "无出处")]
    print(f"   对账: OK{stat.get('OK',0)}/概算{stat.get('概算',0)}/"
          f"疑不符{stat.get('疑不符',0)}/无出处{stat.get('无出处',0)}")
    for r in bad[:8]:
        print(f"   [{r[0]}] {r[1]}{r[2]} | {r[3][:56]}")
    print(f"   示弱: 弱证据 {sstat['weak_total']}（可复核 {sstat['weak_marked']}"
          f"/裸断言 {sstat['weak_unmarked']}）")
    # 语义对账（2026-09 新增）：数字有出处，但单位/年份/主体与素材不一致
    print(f"   语义对账: " + ("/".join(f"{k}{v}" for k, v in semstat.items())
                              if semstat else "无问题"))
    for it in semissues[:6]:
        print(f"   [{it[0]}] {it[1]} | 报告句: {it[2][:46]} | {it[3][:38]}")

    # 门禁：①素材外数字过多 ②裸断言过多 ③单位/年份与素材不符 → 带反馈重生成一次
    def _audit_fail():
        bad = [r for r in rows if r[0] in ("疑不符", "无出处")]
        hard_sem = [i for i in semissues if i[0] in ("单位不一致", "年份不一致")]
        return (len([r for r in bad if r[0] == "无出处"]) > 4
                or sstat["weak_unmarked"] > 3
                or len(hard_sem) > 2)
    if _audit_fail():
        fb = []
        for num, unit, sent, ev in smisses[:6]:
            fb.append(f"- 「{sent[:110]}」中 {num}{unit} 为单一来源弱证据，原句语气过确定，请降确定性表述")
        for r in rows:
            if r[0] == "无出处" and len(fb) < 12:
                fb.append(f"- 「{r[3][:110]}」中 {r[1]}{r[2]} 在来源资料中找不到原词——"
                          f"若确属素材遗漏请删除该数字；不要用素材外记忆补数")
        for it in semissues:
            if it[0] in ("单位不一致", "年份不一致") and len(fb) < 14:
                fb.append(f"- 「{it[2][:100]}」中 {it[1]} 与素材不一致（{it[3][:40]}）"
                          f"——请按素材原文修正单位/年份")
        print("   !! 门禁未过，重生成一次（附无出处/裸断言/语义不符反馈）")
        md = gen_report(topic, focus, srcdir, pref_lines, slines,
                        retry_feedback="\n".join(fb))
        open(md_path, "w", encoding="utf-8").write(md)
        stat, rows = factory_audit(md_path, srcdir)
        sstat, smisses = _sa(md_path, srcdir)
        semstat, semissues = _sema(md_path, srcdir)
        bad = [r for r in rows if r[0] in ("疑不符", "无出处")]
        print(f"   重生成后对账: OK{stat.get('OK',0)}/概算{stat.get('概算',0)}/"
              f"疑不符{stat.get('疑不符',0)}/无出处{stat.get('无出处',0)}")
        print(f"   重生成后示弱: 可复核 {sstat['weak_marked']}/裸断言 {sstat['weak_unmarked']}")
        print(f"   重生成后语义对账: " + ("/".join(f"{k}{v}" for k, v in semstat.items())
                                        if semstat else "无问题"))

    print("== F HTML 呈现 ==")
    html_path = to_html(md, os.path.join(root, f"{tag}_report.html"))
    print(f"完成: {md_path}\n     {html_path}")
    print(f"对账: OK{stat.get('OK',0)}/概算{stat.get('概算',0)}/疑不符{stat.get('疑不符',0)}/无出处{stat.get('无出处',0)}")


if __name__ == "__main__":
    main()
