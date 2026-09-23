"""分析层 Analyzer：多源去重、可信度评分、冲突检测与裁决参考。
由于 MVP 阶段检索源数量有限，这里提供轻量评分与去重逻辑，
并新增"主张抽取 + 冲突检测"，为 Writer 提供"可信度加权 + 时效"的裁决依据。
"""
import hashlib
import logging
import os
import re
from typing import Dict, List
from datetime import datetime
from urllib.parse import urlparse, parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 功能开关：主语抽取是否走"年份/量纲锚点"结构切分（无词典、可组装主体）。
# - True ：用结构切分器（对未知主体/新句式普适性更好）；
# - False：回退到旧的 _pick_subject/_subject_for 正则切分。
# 可用环境变量 ANALYZER_CARVER_STRUCTURE=0 一键禁用，实现不改代码的快速回退。
# ---------------------------------------------------------------------------
_CARVER_STRUCTURE_ENABLED = os.environ.get("ANALYZER_CARVER_STRUCTURE", "1") != "0"

# ---------------------------------------------------------------------------
# 功能开关：转载/镜像"来源归一"（origin 身份，防"虚假独立来源"）。
# - True ：同一正文(逐字转载/镜像)被多个域名转发时，被折叠为**单一来源原点**，
#           不把一堆低可信度镜像当成多个独立来源，从而"刷出" high 置信。
# - False：回退为"按原始 URL 计数"的旧行为（单页镜像仍视为独立来源）。
# 用环境变量 ANALYZER_ORIGIN_MERGE=0 一键禁用，不改动核心计分代码即可回退。
#
# 说明：本开关只影响"来源身份识别"（哪些 URL 算作同一 origin 参与计数），
# 完全不触碰 extract_evidence 里的硬阈值打分逻辑。合并后 origin 的计分仍走
# 原有的 n_sources / top_cred 规则——只是 n_sources 现在数的是 origin 而非裸 URL。
# 落地口径：本轮仅折叠"逐字转载"（精确整段指纹，零误伤）；"轻改写/洗稿"式转载的
# 近重复合并默认不启用，留待看线上数据后再评估是否放开（届时需相似度判据 + 门控）。
# ---------------------------------------------------------------------------
_ORIGIN_MERGE_ENABLED = os.environ.get("ANALYZER_ORIGIN_MERGE", "1") != "0"

# ---------------------------------------------------------------------------
# 功能开关：同句内同一 (指标, 主题, 口径年份) 是否保留"多个实质不同的数值"。
# - True（默认）：同一键下允许多个 `_values_close` 为假（非近重复）的数值都保留，
#   只对"数值近似"(同说法/口径噪声)去重，取距离指标词更近的一条。这样
#   "…零售158万辆，新能源约104万辆" 里 158万 与 104万 能同时存在，不再因
#   主题分离失败/率值并列而被"只留最近"顶掉。
# - False：回退旧行为——同一键只保留离指标词最近的一条。
# 用环境变量 ANALYZER_KEEP_DISTINCT=0 一键禁用。
#
# 说明：条件放开用 `_values_close`（±15%，与证据计分/冲突簇一致）判"实质不同"，
# 因此 12.0 亿 vs 12.1 亿（口径噪声）仍只留一条，不放大"单源数值膨胀"；
# 下游 `_extract_claims(dedup_by_url=True)` 仍按 (subject,indicator,year,value)
# 折叠同源同值，跨来源计数不受影响。
# ---------------------------------------------------------------------------
_KEEP_DISTINCT_CLAUSE = os.environ.get("ANALYZER_KEEP_DISTINCT", "1") != "0"

# 单句内允许产出的最大主张条数（防空句/病态长句因"条件放开"而撑爆上下文）。
_CLAUSE_MAX_CLAIMS = 8

# 句内"变化端点"的到达动词：'从X提升至Y/由38%降到36%/增长至/升至/降至'。
# 这类句式里，X 与 Y 是**同一指标的两个真实观测端点**（如份额从45.8%涨到47.5%），
# 即便两者数值 _values_close（如 38 vs 36 只差5.6%），也**不是**"同口径舍入噪声"
# （12.0亿 vs 12.1亿），而是应保留的两条独立数据点。簇A 句内去重时据此豁免。
_CHANGE_ARRIVE_RE = re.compile(
    r"(提升至|增长至|下降至|升至|降至|降到|攀升至|回落到|"
    r"下滑至|跌至|扩大到|收窄至|增长到|降低到|回落到)$"
)
# 到达动词可能带副词/助词前缀："由38%降到36%"里'降到'前的'由'；"也从45.8%提升至"
# 里'提升至'前的'也/从'。抓 b 前一段含"到/至"终点词即可（见 _is_change_endpoint）。


# 被视作"纯追踪/转发"而可安全剔除的查询参数（规范化 URL 用）。
# 保守白名单：只剔除确定无害的追踪/站内跳转参数，绝不乱删会改变语义的参数。
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "from", "ref", "refer", "referrer", "source", "src", "scm",
    "clicktime", "clickid", "gclid", "fbclid", "dclid", "yclid",
    "wechat", "scene", "subscene", "share_from", "share_to", "page_id",
}

# 归一"域名前导"（子域名在可信度与转载判定里多属同一运营实体或镜像关系）：
# 如 m.foo.com / mobile.foo.com / www.foo.com / wap.foo.com → foo.com。
# 保留 x.y.foo.com 中靠前的业务子域（如 finance.qq.com），只降级"纯设备/访问类"前导。
_MOBILE_HOST_PREFIX = ("www.", "m.", "mobile.", "wap.", "3g.", "touch.")

# 域名可信度初值（0~1），可在后续扩展
DOMAIN_CREDIBILITY = {
    "reuters.com": 0.95, "bloomberg.com": 0.95, "ft.com": 0.95,
    "wsj.com": 0.95, "economist.com": 0.93, "apnews.com": 0.92,
    "gov.cn": 0.9, "moe.gov.cn": 0.9, "stats.gov.cn": 0.9,
    "nature.com": 0.95, "science.org": 0.95, "arxiv.org": 0.9,
    "wikipedia.org": 0.8, "zhihu.com": 0.6, "csdn.net": 0.55,
    "medium.com": 0.5, "weixin.qq.com": 0.5,
}


def score_credibility(doc) -> float:
    """给单个 SourceDoc 打可信度分。

    （域名可信度口径按落地决定：未知主机默认维持 0.5，不做额外降权；后续如需对
    未知/镜像类主机做输入层边界收紧，可在此处扩展，不影响 extract_evidence 计分。）
    """
    from urllib.parse import urlparse
    host = urlparse(doc.url).netloc.lower().lstrip("www.")
    base = DOMAIN_CREDIBILITY.get(host, 0.5)
    # 抓取到正文比只有摘要更可信
    if not doc.from_fetch:
        base -= 0.2
    # 内容长度加成
    if doc.content and len(doc.content) > 500:
        base += 0.05
    doc.credibility = max(0.05, min(0.98, round(base, 2)))
    return doc.credibility


def dedup_docs(docs: List) -> List:
    """按 URL 去重，保留抓取成功且更长的一版。"""
    seen = {}
    for d in docs:
        key = d.url.split("#")[0]
        if key not in seen or len(d.content) > len(seen[key].content):
            seen[key] = d
    return list(seen.values())


def analyze(docs: List, min_content: int = 200) -> Dict:
    """对文档集合做整理：打分、去重、按可信度排序。
    丢弃正文过短的低价值源（如纯导航站）。
    """
    docs = dedup_docs(docs)
    kept = []
    for d in docs:
        score_credibility(d)
        if d.from_fetch and len(d.content) < min_content:
            continue  # 过滤过短正文
        kept.append(d)
    kept.sort(key=lambda d: d.credibility, reverse=True)
    conflicts = detect_conflicts(kept)
    return {"docs": kept, "count": len(kept), "conflicts": conflicts}


# ---------------------------------------------------------------------------
# 冲突检测：主张抽取 + 跨源比对，为 Writer 提供"可信度加权 + 时效"裁决依据
# ---------------------------------------------------------------------------

# 数字 + 单位/指标 的常见组合，用于定位"数值型主张"
_NUM_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*[%％万亿]?\s*(?:美元|元|亿|万|%|％|亿美元|元|吨|辆)?\s*"
    r"(?:亿美元|亿元|万元|%|％|辆|人|台|家|GWh|MWh|kWh|GW|MW|GWh|部|万吨"
    r"|千克|公斤|千瓦时|千瓦|桶|分钟|小时|点|TWh|平方米|)?)",
    re.IGNORECASE,
)
# "亿元人民币/万元人民币"被 _NUM_RE 截断成 'X亿元人/X万元人' 的残尾清洗正则。
# 见 _numeric_tokens：仅去掉"人民币"漏进的尾'人'；不含'元'的计数("5万人/亿人")不匹配。
_RMB_TAIL_RE = re.compile(r"(亿元|万元)人$")
# 常见"主张信号词"：这些词往往引出可冲突的量化结论
_CLAIM_KEYWORDS = [
    "市场份额", "渗透率", "规模", "增长率", "增速", "预计", "达到", "超过",
    "占比", "增长至", "下降至", "市值", "营收", "销售额", "销量", "用户数",
    "下载量", "安装量", "融资", "估值",
    # P0 补充的高频名词性指标：独立成指标，避免把常见量化句子误判为"无主张"
    "出货量", "装机量", "门店数", "毛利率", "净利率", "净利润", "市占率",
    "复合增长率", "研发投入", "研发费用",
    # 交付量/国产化率等：垂直行业高频量纲，归一到销量/国产化率
    "交付量", "国产化率", "自给率",
    # "市场占有率"是"市场份额/市占率"的长式（IDC/Counterpoint 报告高频）。
    # 缺它时"三星以19%的市场占有率排名榜首"里 19% 就近绑到更早的"同比增长"而
    # 错标成增长率；补上并归一到'市场份额'（%的比率词，量纲兼容）。
    "市场占有率",
    # 上市公司财报高频财务词：'营业总收入/营业收入'是'营收'的长式，业界通用。
    # 缺它们会让含此词的句子（"实现营业总收入1157.80亿元，净利润84.64亿元"）
    # 里'1157.80亿元'就近命中到同句的'净利润'而错绑成净利；补上并归一到'营收'。
    "营业总收入", "营业收入",
    # P2 补充的增速/量纲修饰词（就近配对后归一到增长率等）
    "同比增长", "环比增长", "同比增长率", "年增长", "市占",
    "净利",
    # 换数据验证补充：医药/价格/涨跌类（降价/降幅/跌幅/上涨/下滑/下跌）
    "降幅", "跌幅", "降价", "涨价", "上涨", "上浮", "下滑", "下降", "下跌",
    "同比下滑", "同比下跌", "同比下降", "下跌幅", "中选率", "出货",
    # "同比增加/同比提高"与"同比增长"同义（能源/工业产量报告高频）。缺它时这类 % 会
    # 落 fallback、被后文"约占"的占比信号窗口误伤成"占比"（"…同比增加19%，约占…35%"）。
    "同比增加", "同比提高",
    # "约占/占到/占"开头的占比表述（"约占全部发电量的35%"）——"占"单字太宽不收，
    # 收"约占"作比率指标，使 35% 就近绑"占比"而非远处"同比增加"（19% vs 35% 分家）。
    "约占",
    # "份额"短式（Counterpoint 报告："苹果以43%的份额领跑"）。缺它时 43% 无就近
    # 名词指标、被更远的"同比下降"劫持成跌幅。"市场份额/市占率"长式不受影响。
    "份额",
    # ---- 实测轮4：内容平台/服务业量纲 ----
    # "用户"裸词（"7亿用户/新增507万用户"）：量值直接跟"用户"结尾时归用户数域；
    # "用户数/日活/月活"等长式不受影响（同一指标，distance 取近）。
    "用户",
    # "使用时长/人均时长"（"人均使用时长126分钟"）：时间消费量纲，值带"分钟"。
    "时长",
    # "占总收入/占总规模"的占比表述（"广告收入4200亿元，占总收入46.6%"）——46.6%
    # 是占比，若不收录会被更远的"同比增长"劫持成增长率。
    "占总",
    # "零售"（"国内狭义乘用车零售158万辆"）——乘用车/汽车的零售口径销量指标。
    # 缺它时 158万辆 只能落笼统"规模"，无法与"销量/批发"等口径区分交叉。
    "零售",
    # "流水"（游戏/直播/内容平台核心收入口径："全年流水约200亿元"）。此前仅靠量纲词尾
    # fallback 兜底；"用户"关键词收录后，远距离量值会先 bind 到"用户"而跳过流水兜底，
    # 须把"流水"提为一级名词指标（与营收/销售额同级），使 200亿 就近绑流水。
    "流水",
    # "装机"短式：与"出货→出货量"对称（"太阳能发电新增装机59.59GW"里省略'量'字）。
    # 缺它会让此类句子 _match_keywords 命中不到、数值只能靠 fallback 兜底且受距离阈值
    # 限制（"5月单月仅8.68GW"距'装机'超窗→丢）。补上后装机体量能继承装机量指标。
    "装机",
    # P2-1 装机词族："装机容量"是**累计存量**指标（"全国光伏发电装机容量12.86亿千瓦"），
    # 与"装机量/新增装机"（增量）不同桶。缺它时 12.86亿 会被"装机"短式归一成装机量，
    # 与"59.59GW新增装机"同桶污染交叉验证。收为独立一级指标。
    "装机容量",
    # P2-1 "总装机"是"总装机容量"的口语短式（"全国40.8亿千瓦的发电总装机"），归装机容量。
    "总装机",
    # P2-3a OLED"出货面积"独立指标（"OLED面板出货面积720万平方米"，面积型出货，
    # 与"出货量"（件数/台数）不同桶）。"出货"子串会让 720万 归一成出货量，错桶。
    "出货面积",
    # P2-3b "稼动率"（面板/半导体行业设备利用率："行业稼动率降至80%"）。缺它时 80%
    # 落默认"增长率"，与"稼动率/产能利用率"口径无法交叉。
    "稼动率",
    # P2-3c "投资"（"AMOLED生产线总投资630亿元"）——水平量金额指标，缺它落笼统规模。
    "投资",
    # "下载"短式（游戏/App 下载量高频省略'量'字），与"装机/出货"对称补全。
    "下载",
    # ---- 真实压力盲测补充：医药/金融/体育之外的"名词计数"指标词 ----
    # 医药：'纳入389例患者/入组211位患者' 的"患者/入组"是**名词型计数指标**（裸整数
    # 389/211 无物理单位，只有名词可绑）。缺它时句中无任何指标词 → 患者数整体漏抽。
    "患者", "入组", "受试者",
    # 医药临床终点：'中位总生存期27.4个月/无进展生存期PFS为8.2个月'。"生存期/PFS/
    # 无进展生存期"是疗效终点的名词指标（'个月'是时长单位，裸小数 27.4 靠它绑定）。
    "生存期", "无进展生存期", "PFS",
    # 金融汇率变动：'中间价累计升值66个基点' 里"基点"是升贬幅度的计量名词指标。
    "基点",
    # 重工业量纲："表观消费量/消费量"为名词水平量指标（值带 亿吨/万吨/千克）。
    "消费量",
    # 内容平台量纲："人均使用时长126分钟" 的时长（分钟级）为水平量指标。
    "时长",
    # "流水"为水平量指标（游戏/直播收入额）。
    "流水",
    # 宏观经济景气：'制造业PMI从50.9上升至51.5' 的"PMI/pmi"是**指数点**名词指标
    # （指数点位不带物理单位，靠名词绑定；大小写两种都收，正文写法不定）。
    "PMI", "pmi",
    # 体育技术统计：'场均出手11.2次命中402记三分/命中率高达45.4%'。"出手/命中"是
    # 名词计数指标（次数），"命中率"是比率指标（%）。三者各自成指标名，避免
    # 出手次数/命中次数/命中率互相错绑（11.2次≠命中数，402记≠出手数）。
    # '命中'字面会同时出现在"命中率"里，靠"最近的指标词"与量纲(%→命中率)区分。
    "出手", "命中率", "命中",
]
_YEAR_RE = re.compile(r"^20\d{2}$")
# 句子里的年份标记，形如"2024年""到2030年""较2023年""2024财年"(财=财年/财政)、
# "2026年上半年/H1/2026H1/2026上半年/2026年度"。E2(P3评审)扩展写法补全：
# 除"年/财年/年度"外，接受"上半年/下半年/H1/H2"作为年份后缀（"2026H1的123.6GWh"）。
# 仍要求紧跟后缀，避免把"2000亿元"里的 2000 误当年份；不带后缀的裸 4 位数字不认。
_YEAR_IN_RE = re.compile(
    r"(?:^|[^\d])(20\d{2})\s*"
    r"(?:财年|年度|年(?:\s*(?:上半年|下半年|H1|H2|上|下))?|上半年|下半年|H1|H2)"
)
# E2 最小锚点排除：紧邻这些介词的年份是"基期/终点锚点年"（较2023年/比2024年/
# 至2023年/相较2025年），只作比较参照，不得抢占邻近水平量/状态量/相对值的年份。
# 注意区分：仅收比较介词"较/比/至"族——"到2025年/截至2026年"是预测目标/报告期
# 口径年（"预计到2030年将达X"的 2030 恰是该值的年份，语料 60% 渗透率句依赖它），
# 不能排除。5284 型区间终点由"至"覆盖（"2018年至2023年"）。
_ANCHOR_PRE_WORDS = ("相较于", "相比较", "相比", "较之", "相较", "较", "比", "至")

# 比率**状态量**桶（canon 后名）：占比/渗透率/份额等是时点状态，与"相对值"
# （增长率/跌幅/涨幅，E1 置 None）不同——状态量挂句内显式年。E3b 存量规则中
# "截至X年底/月底"的时点存量水平量 → None，但状态量不受影响（31.5%→2026）。
_STATE_RATIO_BUCKET = {
    "占比", "渗透率", "市场份额", "稼动率", "市占率", "占有率", "覆盖率",
    "国产化率", "自给率", "中选率", "毛利率", "净利率", "良率", "集中度",
}


def _is_state_ratio(indicator: str) -> bool:
    return indicator in _STATE_RATIO_BUCKET


def _is_anchor_year(sent: str, year_pos: int) -> bool:
    """年份是否基期/终点锚点（其前紧邻锚介词，如 '较2023年'/'至2023年'）。

    E2 最小版：只查紧邻前缀。完整规则（含 '去年同期/较上年' 等无显式年锚、
    '从X年…至Y年' 区间段）留 E3。
    """
    prev = sent[:year_pos]
    # 截至/截止X年 = 报告期口径年（"截至2026年7月底…存量/占比"的 2026），
    # 不是比较基期锚——但其尾字是"至"，须先豁免再查锚介词表（E2 实测暴露）。
    if prev.endswith("截至") or prev.endswith("截止"):
        return False
    return any(prev.endswith(w) for w in _ANCHOR_PRE_WORDS)


def _detect_year(sent: str, num_start: int, window: int = 30):
    """给某个数值找它的'口径年份'（如 2024 规模、2030 预测）。

    E2(P3评审簇A修复)：分句级回溯——前置年份的可见域从"≤30字符"放宽为
    "句号/换行/段落后最近一个分句块内、距值 ≤80 字符"，取最近前置年；
    锚点年（较/至/比X年，_is_anchor_year）不进候选，避免基期/终点锚点抢占
    水平量/状态量的年份（1557 挂 2023、5284 挂 2023 型）。
    相对值(增长率/跌幅/涨幅)的 year 已由 E1 统一置 None，本函数放宽不影响。

    - 只认句子中**显式出现**的年份，且需与数值足够近，否则返回 None；
    - None 表示"无显式年份"，用于与明确年份的主张区分，避免跨年误判为冲突，
      也不把无年份主张强行归属到某一年而过度拆分。
    """
    # 值自身被锚结构修饰（"较2021年的800亿元"/"至2026年H1的219.2GWh"）→ 该值就
    # 属于锚年（基期量挂基期年、变化终点挂终点年），直接返回。
    # 与"锚+年 与值分离"（5284 前的"至2023年"隔"中国医疗器械总体市场规模从"）
    # 不同：后者是参照锚，须排除让 5284 回填区间起点 2018。
    _pre = sent[max(0, num_start - 18):num_start]
    _bm = re.search(
        r"(?:较|比|相较于|相比|相较|至|到)\s*(20\d{2})\s*年?\s*"
        r"(?:上半年|下半年|H1|H2|上|下)?\s*的?\s*$", _pre)
    if _bm:
        return int(_bm.group(1))
    # E3a 相对期词值：值前紧邻"去年同期/去年/上年"（"较去年同期的14.36GW"）——
    # 该值是上期对比量，无本句报告年语义，不得被"2026年6月"等句首年吸走 → None。
    _last = sent[max(0, num_start - 12):num_start]
    if re.search(r"(?:去年同期|去年|上年)\s*的?\s*$", _last):
        return None
    years = []                      # [(pos, year)]
    for m in _YEAR_IN_RE.finditer(sent):
        if _is_anchor_year(sent, m.start(1)):
            continue
        years.append((m.start(1), int(m.group(1))))
    if not years:
        return None
    # E3c 区间终点：值前紧邻终点动词（"增至9731亿元"）且近 25 字符内无任何可用
    # 年份时，句内含"X年至Y年"的终点年 Y 即该值年份（9731→2023）。若近处已有
    # 可用年（16275 前 8 字符有"到2030年"预测目标年）→ 走下方正常回溯取 2030。
    if re.search(r"(?:增至|增长至|提升至|升至|突破|达到|超过|达|提高到)\s*$", _last):
        _win_has = any(num_start - 25 <= p < num_start for p, _y in years)
        if not _win_has:
            _seg = re.search(r"(?<!截)至(20\d{2})年", sent)
            # 分号保护：区间终点年与值之间跨分号则不适用——47.5%(分号后"也从45.8
            # 提升至47.5")不得借用分号前 219.2 结构的"至2026年H1"终点年。
            if _seg and "；" not in sent[_seg.start():num_start] \
                    and ";" not in sent[_seg.start():num_start]:
                return int(_seg.group(1))
    # E2 前置可见域：句号/换行/段落阻断后，从最近句块起点回溯（≤80 字符）。
    _left = max(sent.rfind("。", 0, num_start), sent.rfind("！", 0, num_start),
                sent.rfind("？", 0, num_start), sent.rfind("\n", 0, num_start)) + 1
    # 跨分号阻断：值前有分号且本分句是"从A提升至B"型变化端点（45.8/47.5% 的
    # "也从45.8%提升至47.5%"）→ 不跨分号继承段首年（评审:无显式相邻年→None）；
    # 平铺并列分句（"…1.08亿人；初中在校生5243.69万"）仍放行跨分号。
    _sc = max(sent.rfind("；", 0, num_start), sent.rfind(";", 0, num_start))
    _blocked = False
    if _sc > _left:
        _clause = sent[_sc + 1:num_start]
        if re.search(r"[从由].{0,8}?[至到]|[从由]|(?:提升|增长|下降|降至|增至|回落)[至到]",
                     _clause):
            _blocked = True

    def _near_ok(p):
        if _blocked and p < _sc:
            return False
        return _left <= p < num_start and num_start - p <= 80

    near = [y for p, y in years if _near_ok(p)]
    if near:
        return near[-1]             # 最靠近数值的前置年份
    ahead = [y for p, y in years if p >= num_start and p - num_start <= window]
    if ahead:
        return ahead[0]             # 数值后最近的年份（如"预计2030年…增至X"）
    return None
# 真正代表"指标"的词（优先用于聚类）；动词类只作信号，不当作指标
_INDICATOR_PRIORITY = [
    "市场份额", "渗透率", "增长率", "营收", "销售额", "市值", "估值",
    "销量", "用户数", "规模", "增速", "融资", "占比", "下载量", "安装量",
    "毛利率", "净利率", "净利润", "出货量", "装机量", "门店数", "复合增长率",
    "交付量", "国产化率", "自给率", "研发投入", "研发费用",
]
# 近义指标 → 唯一"规范名"：避免同一量纲因措辞不同而被拆成不同桶、破坏交叉验证。
# 仅在抽取落桶前做局部归一（轻量，不引入语义理解）。
_INDICATOR_CANON = {
    "复合增长率": "增长率", "增速": "增长率", "同比增长": "增长率",
    "同比增长率": "增长率", "环比增长": "增长率", "年增长": "增长率",
    "市占率": "市场份额", "市占": "市场份额", "市场占有率": "市场份额",
    "净利": "净利润", "净收益": "净利润",
    "出货": "出货量",
    "装机": "装机量",
    # "装机容量"（累计存量）归一到自身，与"装机量/新增装机"（增量）区分。
    "装机容量": "装机容量",
    # "总装机"（=总装机容量，口语短式）同桶。
    "总装机": "装机容量",
    # "出货面积"（面积型出货）归一到自身。
    "出货面积": "出货面积",
    # "稼动率"归一到自身（比率%指标，与产能利用率口径同类）。
    "稼动率": "稼动率",
    # "投资/总投资"（金额型水平量）归一到自身。
    "投资": "投资", "总投资": "投资",
    # "下载"是"下载量"的短式（同"出货/装机"先例）——缺它时"全球累计下载突破10亿次"
    # 无量纲词命中、落笼统"规模"，无法与"下载量"口径交叉验证。
    "下载": "下载量",
    "交付量": "销量", "交付": "销量",
    "自给率": "国产化率",
    # 自识别量纲词 → 规范指标名（仅同义归一，不做语义换算）
    "门店": "门店数", "日活": "用户数", "月活": "用户数",
    "营收": "营收", "营业总收入": "营收", "营业收入": "营收",
    "订单量": "订单量",
    "降幅": "跌幅", "跌幅": "跌幅", "降价": "跌幅", "下滑幅": "跌幅",
    "下跌幅": "跌幅",
    "同比下滑": "跌幅", "同比下跌": "跌幅", "同比下降": "跌幅",
    "下滑": "跌幅", "下降": "跌幅", "下跌": "跌幅",
    "涨价": "涨幅", "上涨": "涨幅", "上调": "涨幅", "上浮": "涨幅",
    # 真实压力盲测：名词计数指标域（医药/金融/宏观）归一到规范名
    "患者": "患者数", "入组": "患者数", "受试者": "患者数",
    "无进展生存期": "生存期", "PFS": "生存期",
    "pmi": "PMI",
    # "基点"是汇率/利率升贬的计量名词指标，归一到自身（值即"多少基点"）。
    "基点": "基点",
    # 体育技术统计归一到规范名
    "出手": "出手数", "命中": "命中数", "命中率": "命中率",
    # 重工业量纲：'粗钢表观消费量8.92亿吨'/'人均消费量601.1千克'——"消费量"名词指标
    # （产量/消费量/进口量/出口量在重工业报告高频，与既有 出货/销量 同构）。
    "消费量": "消费量",
    # 内容平台量纲："使用时长/人均时长"（分钟级时间消费）归一到时长。
    "时长": "时长",
    # "流水"归一到自身（与营收/销售额同级：游戏与直播平台收入口径）。
    "流水": "流水",
    # "零售"归一到自身（乘用车/汽车零售口径销量指标）。
    "零售": "零售",
    # '同比增加19%'/'同比提高11.8%'——"同比增加/提高"与"同比增长"同义但未收录，
    # 缺它时 19% 会落 fallback、被后文'约占'的占比信号窗口误伤成"占比"。
    "同比增加": "增长率", "同比提高": "增长率",
    # "约占"是"占比"的引导式写法（"约占全部发电量的35%"），归一到占比。
    "约占": "占比",
    # "份额"是"市场份额"的短式（Counterpoint 报告高频写法），归一后与长式同桶交叉。
    "份额": "市场份额",
    # 内容平台量纲归一
    "用户": "用户数", "时长": "时长", "占总": "占比",
}
# 水平量(level)指标：描述"存量/体量/绝对数量"（销量/规模/用户数…）。
# 当数值带 %（同比/增幅/占比…）时，绝不能把 % 错挂到水平量指标上——
# 例如"PHEV销量同比下滑28%"里的 28% 是跌幅，不是销量。
_LEVEL_INDICATORS = {
    "销量", "规模", "出货量", "出货", "装机量", "用户数", "营收", "销售额", "市值",
    "估值", "门店数", "下载量", "安装量", "融资", "净利润", "净利",
    "研发投入", "研发费用",
    # 名词计数/计量型水平量指标：值本身无物理单位（389/27.4/66），靠名词绑定。
    "患者数", "生存期", "基点", "出手数", "命中数",
    # 口径水平量：消费量/时长/流水/零售 等一级名词指标。
    "消费量", "时长", "流水", "零售",
    # P2-1 装机容量（累计存量水平量，与装机量/新增装机增量区分）。
    "装机容量",
    # P2-3a 出货面积（面积型水平量，与出货量/件数区分）。
    "出货面积",
}
# 比率型(rate)指标的**规范名**集合：描述"占比/变动幅度/相对量"（增长率/跌幅/
# 渗透率/毛利率…）。对称规则的另一半：带计数单位的水平量数值（辆/台/亿/元…）
# 不能就近绑到这些比率指标上，否则"…同比增长70%，其中欧洲贡献12万辆"的
# 12万辆 会被远处一个"同比增长"错标成增长率。
_RATE_INDICATOR_CANON = {
    "增长率", "跌幅", "涨幅", "渗透率", "占比", "市场份额", "中选率",
    "毛利率", "净利率", "国产化率", "自给率",
    # 体育命中率（%的比率指标，与出手数/命中数两个水平量区分）
    "命中率",
    # 稼动率/产能利用率（%比率指标）。
    "稼动率", "产能利用率",
}
# 仅作"信号"、不作为指标标签的动词（就近配对与最终标签都要排除）。
# 注意："下滑/下降/上涨"等涨跌词既能当动词（"下滑至50万辆"里是修饰水平量），
# 又能当跌幅/涨幅的**比率型指标**（"同比下滑28%"里 28% 就是跌幅）。
# 它们统一在 _nearest_indicator 里按数值类型区分：值为 % 时作比率指标，
# 值为水平量（万辆/亿元等）时按动词跳过，避免把"下滑至50万辆"错标成跌幅。
_VERB_KEYWORDS = {"达到", "超过", "预计", "增长至", "下降至", "增至", "回落", "跌破"}
# 涨跌类：值为 % 时它代表"跌幅/涨幅"（比率指标），否则视为动词
_DELTA_WORDS = {"下滑", "下降", "下跌", "同比下滑", "同比下跌", "同比下降",
                "下跌幅", "上涨", "上升", "上调", "上浮"}
# 数值单位可推断的默认指标：金额类→规模，百分比→增长率
_AMOUNT_UNIT_RE = re.compile(r"亿|万|美元|元|欧元|英镑|日元")
# 计数/计数类单位：出现即代表"水平量"（绝对数量），而非比率。
# 补充了能量/功率类绝对量单位（GWh/MWh/kWh/GW/MW/W/千瓦/千瓦时…）与面积
# （平方米/公顷/亩）。这类单位此前不在表中，导致带它们的量值被误判成"非水平量"
# （比率），从而绕开"量值不绑比率词"的对称规则：如"…219.2GWh，同比增长77.3%"
# 里的 219.2GWh 会被误就近绑成"增长率"。补全后量值才能正确挂到出货量/装机量等
# 名词指标上。注意：这些绝对量单位与百分比(%)无关，不会误伤比率判定。
_COUNT_UNIT_RE = re.compile(
    r"辆|台|人|家|部|座|件|个|套|吨|万吨|册|平方公里|亩|"
    r"平方米|公顷|亩|千克|公斤|桶|分钟|小时|"
    r"GWh|MWh|kWh|Wh|GW|MW|kW|TWh|千瓦时|千瓦|"
    r"台套|件套"
)
# 通用"计量维度词尾"：形如"XX量/XX数/XX单/日活/资本开支/保费收入/海外门店"中
# 表达"某个可量化的维度"的收尾字。靠它可在**未收录任何具体词**的情况下，把
# 数值附近一个带此类词尾的词自动识别为维度名（订单量/日活/资本开支…均可）。
# 这是"主体/量纲可组装而非写死"的关键：不枚举每个新词，只识别"词尾模式"。
_MEASURE_TAIL_RE = re.compile(
    r"(?:[\u4e00-\u9fa5A-Za-z]{1,6}?)(量|数|单|单量|活|收入|支出|开支|投入|"
    r"费用|流水|成交额|销售额|保费|门店|装机|出货|客单|订单)$"
)
# 能作为"量纲收尾锚"的最小可识别词尾（用于从句子里抓"量纲词尾词"）
_MEASURE_SUFFIXES = [
    "量", "数", "单量", "日活", "月活", "活", "收入", "支出", "开支", "投入",
    "费用", "流水", "成交额", "保费", "门店", "装机", "出货", "客单价", "订单",
    "销售额", "营收",
]


def _looks_level_value(value: str) -> bool:
    """判断一个数值是否属于'水平量'（带计数或金额单位），而非比率。

    - 带金额单位（亿元/亿美元/元/万…）或计数单位（辆/台/人/部…）→ 水平量；
    - 纯数字、带 % 等 → 不是水平量。
    用于就近配对的对称量纲规则：水平量数值不挂比率型指标（增长率/跌幅…）。
    """
    v = value.replace(" ", "")
    if "%" in v or "％" in v:
        return False
    return bool(_AMOUNT_UNIT_RE.search(v) or _COUNT_UNIT_RE.search(v))


def _default_indicator_by_unit(value: str) -> str:
    """当句子没有可用的名词指标时，用数值单位做最保守的指标兜底。

    - 带 % → 增长率（同比/增速类多为百分比）
    - 带金额单位（亿/万/元/美元）→ 规模（"达X亿美元"多为规模/市场规模口径）
    返回 "" 表示无法推断（该主张宁缺毋滥，避免污染指标标签）。
    """
    v = value.replace(" ", "")
    if "%" in v or "％" in v:
        return "增长率"
    if _AMOUNT_UNIT_RE.search(v):
        return "规模"
    return ""


def _canonical_indicator(kw: str) -> str:
    """把近义词归一到规范指标名；若无映射则原样返回。"""
    return _INDICATOR_CANON.get(kw, kw)


def _pick_indicator(hit_kw):
    """从命中词中挑出最像'指标'的那个：优先核心指标词，排除动词。

    若无任何名词型指标命中，返回 ""（宁缺毋滥，避免把"预计/达到"当指标）。
    """
    for kw in _INDICATOR_PRIORITY:
        if kw in hit_kw:
            return kw
    cand = [k for k in hit_kw if k not in _VERB_KEYWORDS]
    return max(cand, key=len) if cand else ""


def _pick_level_indicator(hit_kw):
    """从命中词中挑一个"水平量型"指标（规范名 ∈ _LEVEL_INDICATORS）作整句候选。

    背景：_pick_indicator 的全局优先级把市场份额/渗透率等**比率指标**放在最前，
    长句（"出货量从2025年的X增长至2026年的Y，同比增Z；市场份额也从P提升至Q"）
    里句尾的"市场份额"会抢走整句指标，导致句首/分句内的**水平量值**（X/Y，属于
    出货量）在就近超窗时无水平量整句指标可继承、被丢弃（如 219.2GWh 案例）。
    本函数专门服务**水平量值**：从命中词里挑一个规范名是水平量的指标（出货量/
    营收/规模/装机量…），使水平量值即使在就近指标超窗时也能继承到正确的量纲。
    比率值(%)仍走全局 _pick_indicator，不受影响。
    """
    for kw in _INDICATOR_PRIORITY:
        if kw in hit_kw and _canonical_indicator(kw) in _LEVEL_INDICATORS:
            return kw
    cand = [k for k in hit_kw if k not in _VERB_KEYWORDS]
    for kw in cand:
        if _canonical_indicator(kw) in _LEVEL_INDICATORS:
            return kw
    return ""


# 主题词定位：匹配"X市场 / X行业 / 苹果市占率 / 智能手机出货量 / 新能源渗透率"中的 X。
# 边界词既包含"市场/行业/产业/领域"这类常见后缀，也覆盖换数据验证里遇到的
# "市占/份额/出货量/规模/渗透率/营收/销量/销售额"等直接以指标收尾的实体主体
# （如"苹果市占率约19%"→主题"苹果"、"智能手机出货量达X"→主题"智能手机"）。
_SUBJECT_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z]{2,8}?)"
    r"(?:市场|行业|产业|领域|赛道|市占率|市占|市场份额|份额|"
    r"出货量|出货|装机量|规模|渗透率|营收|销售额|销量|用户数)"
)
# 常见的无实际意义的前缀，过滤掉
_SUBJECT_STOP = {"全球", "中国", "全国", "国内", "我国", "海外", "今年", "去年",
                 "整个", "这个", "相关", "主要", "新兴", "传统", "整个",
                 "中国人工智能", "国内主要"}
# 常见的"报道引导词/统计来源词"：是主题的噪音，会被剔除，
# 否则不同文章("报告显示" vs "数据显示" vs "机构则估算")会因措辞差异被判成不同主题、
# 破坏交叉验证。既出现在句首，也可能夹在主题前，故既做句首剥离也做子串剔除。
_LEADING_NOISE = [
    "报告显示", "报告称", "报告预计", "数据显示", "统计显示", "统计表明",
    "调查显示", "调查发现", "研究显示", "研究指出", "研究表明", "据估计",
    "据测算", "据调查", "预计", "有报告称", "有数据显示", "分析认为",
    "分析师表示", "分析师称", "机构预计", "机构报告", "机构认为",
    "机构则估算", "则估算", "另一机构则估算", "据媒体报道", "据称",
]
# 主语前的时间状语（上半年/一季度/全年…），属报道口径而非实体，应剥离，
# 避免"上半年中国新能源…"被切出"上半"这种截断式假主语。
_LEADING_PERIOD = (
    "前三季度", "前两个季度", "上半年", "下半年", "一季度", "二季度",
    "三季度", "四季度", "本季度", "上季度", "上财年", "全年", "财年", "当季",
    # "第X季度"写法（"2026年第二季度全球半导体市场规模"）——年份后的"第二季度"
    # 若只收"二季度"，主体切割会留下"第二季度全球半导体"这类时间词+实体粘连的假主语。
    "第一季度", "第二季度", "第三季度", "第四季度",
    "今年", "去年", "上年", "本年度", "当年度",
    # 期首/单期修饰：游戏/影视/新品史里"首月/首季/首年/上线当月"高频（"《原神》
    # 首月全球收入2.5亿美元"），缺它会被当主体切出"首月…"假主语。与季/半年同构。
    "首年", "首季度", "首月", "首季", "上线首月", "上架首月", "当月", "单月",
)
# 语义上等同于"计量/估算/比较口径"的动词，若紧贴主题前也不应进入主题词
_SUBJECT_VERB_NOISE = {"估算", "预计", "估计", "预测", "达到", "约为", "约", "则",
                       "仅为", "高达", "达到约", "接近"}


def _strip_leading_noise(text: str) -> str:
    """反复剥掉句首的报道引导词/限定词噪音，保留真正的主题名词。"""
    prev = None
    while text != prev:
        prev = text
        for nz in sorted(_LEADING_NOISE, key=len, reverse=True):
            if text.startswith(nz):
                text = text[len(nz):]
                break
    return text


def _strip_leading_period(text: str) -> str:
    """剥掉主语前的周期状语（上半年/一季度/全年…），可能跟在年份后：
    '2024年上半年中国…'→'中国…'、'公司上半年…'→'公司…'。"""
    prev = None
    while text != prev:
        prev = text
        text = re.sub(r"^(?:20\d{2}|19\d{2})\s*年?", "", text).lstrip("年月日")
        for p in sorted(_LEADING_PERIOD, key=len, reverse=True):
            if text.startswith(p):
                text = text[len(p):]
                break
    return text


def _pick_subject(sent: str) -> str:
    """从句段中提取主题词（用于精细聚类，避免不同主体/行业误判冲突）。

    先剥掉"报告显示/数据显示/机构则估算"等引导噪音与开头的年份锚点，再取
    "市场/行业/市占率/出货量/规模/渗透率"等边界词前的名词作为主题，使同一
    主体/行业的不同报道（不同措辞）落到同一主题、从而正确交叉验证。

    仅返回主语名词，不含边界词。例如：
      "全球AI市场规模达X"         → "AI"
      "苹果市占率约19%"           → "苹果"
      "智能手机出货量达12.1亿部"  → "智能手机"
      "中国新能源乘用车渗透率…"    → "新能源乘用车"
    """
    cleaned = _strip_leading_noise(sent)
    # 去掉句首/边界前的年份锚点（"2025年全球智能手机出货量…"里的 2025年）
    cleaned = re.sub(r"^(?:20\d{2}\s*年?|[年月日]+|[，,。])+", "", cleaned)
    cleaned = _strip_leading_period(cleaned)
    m = _SUBJECT_RE.search(cleaned)
    if not m:
        return ""
    subj = m.group(1)
    # 去掉残留年份/日期头
    subj = re.sub(r"^\d+年?", "", subj).lstrip("年月日")
    # 取逗号/连接词后最靠后的名词作为主题（如"中国新能源乘用车"→"新能源乘用车"）
    parts = [s for s in re.split(r"[的及与和、,，]", subj) if s]
    subj = parts[-1] if parts else subj
    # 去掉紧贴主题前的计量/估算动词
    for v in sorted(_SUBJECT_VERB_NOISE, key=len, reverse=True):
        if subj.startswith(v) and len(subj) > len(v):
            subj = subj[len(v):]
    # 去掉全局性前缀（全球/中国/国内/今年…）
    for s in _SUBJECT_STOP:
        if subj.startswith(s) and len(subj) > len(s):
            subj = subj[len(s):]
    return subj[:12]


def _extract_date(doc) -> str:
    """从文档时间字段或标题里提取粗略日期，用于"时效优先"。"""
    raw = getattr(doc, "fetched_at", None) or getattr(doc, "date", None) or ""
    m = re.search(r"(20\d{2})[-年/._]?(\d{1,2})?", str(raw))
    if m:
        year = m.group(1)
        month = m.group(2) or "01"
        return f"{year}-{int(month):02d}"
    return ""


def _canonical_host(host: str) -> str:
    """归一主机名：小写 + 去掉 www./m./mobile./wap. 等纯访问前导。

    注意：只在判定"是否同一域名/镜像"时用于比对，返回的是"可比较的规范化主机"。
    它不覆盖靠前的业务子域（finance.qq.com 保留 finance），以免把腾讯财经与腾讯科技
    混为一谈——那会错误地归并真正的独立编辑部来源。
    """
    h = (host or "").lower().strip()
    for p in _MOBILE_HOST_PREFIX:
        if h.startswith(p):
            h = h[len(p):]
            break
    return h


def _tracking_clean(qs: str) -> str:
    """剔除纯追踪参数，其余查询参数按 key 排序后重编码，让'语义相同'的 URL 归一。"""
    keep = [(k, v) for k, v in parse_qsl(qs, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS]
    if not keep:
        return ""
    keep.sort()
    return urlencode(keep)


def _canonical_url(url: str) -> str:
    """把一条 URL 规约为'用于跨域转载比对'的稳定身份。

    处理：去 fragment；剔除纯追踪参数；主机去掉 www./m. 等前导；路径去尾斜杠；
    默认端口去除；查询参数重排。返回 "" 当输入无效。该函数本身**不改变文档**，
    只是产生一个可比较的身份串。
    """
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "http://" + u
    try:
        parts = urlsplit(u)
    except Exception:
        return u.split("#")[0].rstrip("/")
    scheme = parts.scheme.lower()
    host = _canonical_host(parts.netloc)
    # 去掉 netloc 里由 urlsplit 拆出的 userinfo 与端口差异：netloc 已含，直接重排
    path = parts.path.rstrip("/") or "/"
    query = _tracking_clean(parts.query)
    if parts.fragment:
        pass  # fragment 不参与比对
    return urlunsplit((scheme, host, path, query, ""))


def _normalize_text(s: str) -> str:
    """把正文压成纯字母数字串，便于做跨源转载的近重复指纹。"""
    if not s:
        return ""
    s = s.lower()
    # 保留中日韩字形 + 拉丁字母 + 数字；去掉空格/标点/换行，避免排版差异造成漏判
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s, flags=re.UNICODE)
    return s


def _content_fingerprint(content: str, span: int = 200, min_len: int = 40) -> str:
    """取正文一段规整文本的短指纹，用于判断"是否同一份正文的转载/镜像"。

    用整段哈希对"站点外壳不同、但正文一模一样"的长转载最稳；对会被各自站点头尾
    /广告污染的中短转载，退化用"中段 200 字"哈希，且要求正文足够长才启用转载合并
    （太短如搜索摘要易出现"两句巧合撞指纹"，宁可少合并也不误合并真来源）。
    返回稳定前缀，输入过短返回空（不参与转载归并）。
    """
    n = _normalize_text(content)
    if len(n) < min_len:
        return ""
    # 全段指纹作为主指纹
    return hashlib.sha1(n.encode("utf-8")).hexdigest()[:16]


def _origin_key(doc) -> str:
    """单文档的**基础来源原点身份**：用规约后的 canonical URL 充当。

    它本身只负责"同一链接的各种写法（m./www.、追踪参数、#锚点）→ 同一 origin"。
    真正跨主机、内容近乎相同的**转载/镜像折叠**需要同时看到多篇文档做比对，
    因此由文档级的 `_assign_origins` 在其之上补一层内容指纹碰撞，把合并后的稳定
    origin_id 写回各 doc。返回的 canonical URL 保证确定性：同一链接必然同一 key。
    """
    if not _ORIGIN_MERGE_ENABLED:
        return _url_key(getattr(doc, "url", ""))
    cu = _canonical_url(getattr(doc, "url", "") or "")
    return cu or _url_key(getattr(doc, "url", "") or "")


def _assign_origins(docs: List) -> Dict[str, dict]:
    """把一组文档折叠成"独立编辑来源原点(origin)"，核心是防"虚假独立来源"。

    真正的交叉验证该数"独立编辑实体"，而非裸 URL。否则同一篇通稿被十家低可信度
    站镜像转载，会被误当成"十个独立来源"而刷出 high 置信。判据：
      1. canonical URL 相同（m./www./追踪参数差异）→ 同一 origin（基础身份）；
      2. 正文**逐字转载**指纹命中（不同域名/URL 却正文完全一致）→ 同一 origin（镜像/转载）；
      3. 否则各自独立。

    **范围说明（落地口径）**：本轮只做"逐字转载/镜像"的折叠（精确整段指纹，零误伤），
    **不做**"轻改写/洗稿"式的近重复合并（那需要相似度判据 + 可信度门控，权衡后默认不
    启用，留待后续看线上数据再评估）。因此仅轻微改写的转载在本次版本里仍视为独立来源。

    实现要点：
    - 采用并查集做**可传递**归并（A 同 B、B 同 C ⇒ A/B/C 同一 origin）；
    - 内容指纹碰撞索引而非两两全比，避免 O(n²)；
    - 每个 origin 选一位"代表文档"（优先抓取正文、综合可信度高、正文长），该 origin
      参与计分时以其成员可信度为候选集合。因此"一堆低可信度镜像"折叠后最高只能取到
      较可信的那一份，不会因镜像数量在 top_cred / 来源数上虚高。

    副作用：把 doc.origin_id / doc.origin_kind 写回每篇文档（供观测）。返回
    {origin_id: meta} 诊断表，供 writer 等展示"谁被折叠成了谁"。
    """
    if not docs:
        return {}
    if not _ORIGIN_MERGE_ENABLED:
        # 回退：每篇按其 raw-url 身份单独成 origin，不折叠任何转载。
        metas = {}
        for d in docs:
            _ensure_credibility(d)
            k = _url_key(getattr(d, "url", "") or "")
            d.origin_id = k
            d.origin_kind = "same_url"
            metas.setdefault(k, {"id": k, "kind": "same_url",
                                 "members": [], "n_docs": 0,
                                 "urls": set()})["members"].append(d)
            metas[k]["urls"].add(getattr(d, "url", ""))
            metas[k]["n_docs"] = len(metas[k]["members"])
            metas[k]["representative"] = d
        for m in metas.values():
            m["members"] = sorted(m["members"], key=lambda x: -x.credibility)
        return metas

    # ---- 规约 URL 作为每篇的基础身份 ----
    canon_of = {id(d): _origin_key(d) for d in docs}      # id(d) -> canonical key

    # ---- 正文转载指纹碰撞索引（同指纹 → 疑似转载/镜像）----
    fp_to_ids = {}                                         # 内容指纹 -> [doc ids]
    for d in docs:
        fh = _content_fingerprint(getattr(d, "content", "") or "")
        if not fh:
            continue
        fp_to_ids.setdefault(fh, []).append(id(d))

    # ---- 稳定实现（并查集，doc_list 下标为节点）----
    doc_list = list(docs)
    pos = {id(d): i for i, d in enumerate(doc_list)}
    uf = list(range(len(doc_list)))
    def _find(x):
        while uf[x] != x:
            uf[x] = uf[uf[x]]
            x = uf[x]
        return x
    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            uf[rb] = ra
    # 正文指纹相同的 doc 归并（同一份正文的转载/镜像）
    for fh, ids in fp_to_ids.items():
        base = ids[0]
        for other in ids[1:]:
            _union(pos[base], pos[other])
    # canonical URL 相同亦归并（同链接多写法、同链接必然同正文）
    canon_group = {}
    for i, d in enumerate(doc_list):
        ck = canon_of[id(d)]
        canon_group.setdefault(ck, []).append(i)
    for g in canon_group.values():
        base = g[0]
        for other in g[1:]:
            _union(base, other)

    # ---- 汇总最终 origin ----
    final = {}                                              # root index -> meta
    for i, d in enumerate(doc_list):
        root = _find(i)
        m = final.setdefault(root, {"id": None, "members": [], "urls": set()})
        m["members"].append(d)
        m["urls"].add(getattr(d, "url", ""))
    metas = {}
    for root, m in final.items():
        members = m["members"]
        canons = {canon_of[id(x)] for x in members} - {""}
        kind = "mirror" if len(canons) > 1 else "same_url"
        # 代表：优先 抓取正文 & 综合可信度高(已按域名+抓取+长度打分) & 正文长
        def rep_rank(x):
            _ensure_credibility(x)
            return (int(bool(getattr(x, "from_fetch", False))),
                    getattr(x, "credibility", 0.0) or 0.0,
                    len(getattr(x, "content", "") or ""))
        rep = max(members, key=rep_rank)
        oid = _origin_key(rep) or (m["urls"] and sorted(m["urls"])[0]) or f"oid:{root}"
        # 写回每篇文档
        for x in members:
            x.origin_id = oid
            x.origin_kind = kind
            x.origin_rep = rep
        metas[oid] = {
            "id": oid,
            "kind": kind,
            "n_docs": len(members),
            "representative": rep,
            "members": sorted(members, key=lambda x: -x.credibility),
            "urls": sorted(m["urls"]),
        }
    return metas


def _url_key(url: str) -> str:
    """来源指纹：去 #fragment，用于判断两条主张是否出自同一 URL。

    关键：真正的"交叉验证"依赖**不同来源（URL）**，而非句子条数。
    若同一 URL 被重复抓取/去重遗漏，绝不能被当成多个独立来源计数。
    """
    return (url or "").split("#")[0].strip().rstrip("/")


def _ensure_credibility(doc) -> None:
    """确保 doc.credibility 已打分（未打分则补一个默认分），便于独立入口调用。"""
    if not getattr(doc, "credibility", 0.0):
        score_credibility(doc)


def _find_fallback_dimension(sent: str, num_start: int, num_end: int) -> str:
    """当常规关键词没命中时，为数值找一个'量纲词尾词'作为维度名（自识别）。

    仅在主关键词引擎命中为空时兜底启用，避免干扰 _match_keywords 里已能正确
    归一(出货量/装机量…)的词，杜绝之前"手机出货"抢走"出货量"式回归。
    返回值即它自己的词（门店/日活/资本开支/GMV/订单量…），不并入任何粗粒度桶。

    识别方式：扫描数值前文里**出现的已知维度词干**（长词优先），取其最贴近数值、
    且前文确有它的那个作为维度名。这些是"量纲/维度名"白名单（不涉及实体主体的
    穷举——主体仍由结构切分器可组装地识别），避免用脆弱的"向后截串"误伤
    "即时配送订单量"这类把业务线当词干、或"海外日活"里日/月与日期混淆的句子。
    """
    region = sent[:num_start]                # 只看向数值之前的文本，减少误抓
    # 维度词干（按长度降序，长词优先命中），均能独立作"指标标签"用。
    tails = [
        "活跃用户数", "成交额", "销售额", "出货量", "装机量", "订单量",
        "门店数", "净利润", "资本开支", "保费收入", "客单量", "毛利率",
        "净利率", "月活", "日活", "门店", "GMV", "保费", "装机", "出货",
        "订单", "流水", "收入", "开支", "投入",
    ]
    best = None
    best_dist = None
    for tail in tails:
        idx = region.rfind(tail)
        if idx < 0:
            continue
        # 尽量向后取更多实质词（"资本开支"已整词收录则保持）；此处直接采用整词干
        d = num_end - (idx + len(tail))        # 词干越贴近数值越好
        if best_dist is None or d < best_dist:
            best_dist = d
            best = tail
    # 距离阈值：量纲词不该离数值太远（一般隔"达/约/突破"等少量字）
    if best is not None and best_dist <= 14:
        return best
    return ""


def _match_keywords(sent: str) -> List[tuple]:
    """返回句子中所有命中关键词的 (起点, 关键词)。允许一个词命中多次。"""
    hits = []
    for kw in _CLAIM_KEYWORDS:
        idx = 0
        while True:
            i = sent.find(kw, idx)
            if i < 0:
                break
            hits.append((i, kw))
            idx = i + max(1, len(kw))
    return sorted(hits)


# ---- 中文比例/成数（"近九成/超半数/三分之二"）----
# 数字字符（含"两"）：用于把"X成""Y分之Z""半数"这类纯中文比例转成百分比，
# 让它们与"70%""50%"在主张级可比（_value_to_number / _values_close 统一口径）。
_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000}
_CN_DIGITS_RE = re.compile(r"[零一二三四五六七八九十百两千]+")


def _cn_to_int(s: str) -> int:
    """把中文数字串（支持个/十/百/千位组合，如'十二''二十一''五十四'）转 int。

    仅处理常见的十进制写法，不覆盖"壹贰叁"繁体大写。无法解析返回 -1。
    """
    if not s:
        return -1
    if s in _CN_NUM:
        return _CN_NUM[s]
    # 逐位解析，识别"X十Y""X百Y""X千Y"组合
    total = 0
    cur = 0
    for ch in s:
        n = _CN_NUM.get(ch)
        if n is None:
            return -1
        if n in (10, 100, 1000):
            # 十/百/千作为位权：前面紧跟单数则相乘，否则补 1*位权
            total += (cur or 1) * n
            cur = 0
        else:
            cur = n
    return total + cur


# 中文比例短语正则：可带前缀修饰（近/约/超/超过/达/仅/占/其中），
# 主体为"X成/半数/一半/过半/Y分之Z"，归一为百分比。末尾需是 成/半/分 等结尾字，
# 避免误抓"今年"里的"今"之类。
_CN_PROP_RE = re.compile(
    r"(近|约|超|超过|达|仅|占|高于|低于|其中)?"
    r"((?:[零一二三四五六七八九十百两千]+分之[零一二三四五六七八九十百两千]+)"
    r"|(?:[零一二三四五六七八九两]+成)"
    r"|(?:半数|过半|一半))"
)


def _cn_prop_to_percent(expr: str):
    """把'X成/Y分之Z/半数/一半/过半'解析成 (百分比数值, True)；失败返回 None。"""
    m = re.match(r"([零一二三四五六七八九十百两千]+)分之"
                 r"([零一二三四五六七八九十百两千]+)", expr)
    if m:
        a = _cn_to_int(m.group(1))
        b = _cn_to_int(m.group(2))
        if a > 0:
            return (round(b / a * 100, 3), True)
        return None
    m = re.match(r"([零一二三四五六七八九两]+)成", expr)
    if m:
        n = _cn_to_int(m.group(1))
        if 0 <= n <= 10:
            return (n * 10.0, True)
        return None
    if expr in ("半数", "一半", "过半"):
        return (50.0, True)
    return None


def _cn_prop_tokens(sent: str) -> List[dict]:
    """在 ASCII 数字之外，识别中文比例短语并返回 {start,end,raw,value} token。

    value 直接给出可比较的百分比形态，如 '九成'→'90%'（便于冲突/交叉比对）。
    纯年份/日期（如"今年""去年"不含 成/分 结尾）不会被误抓。
    """
    toks = []
    for m in _CN_PROP_RE.finditer(sent):
        prefix = m.group(1) or ""
        expr = m.group(2)
        pct = _cn_prop_to_percent(expr)
        if pct is None:
            continue
        start = m.start(2)
        value = f"{pct[0]:g}%"
        toks.append({"start": start, "end": m.end(2),
                     "raw": m.group(0).strip(), "value": value,
                     "cn": prefix + expr})
    return toks


def _numeric_tokens(sent: str) -> List[dict]:
    """返回句子中每个'数值 token'：{start, end, raw, value}。

    value 为去掉空白的原始串；纯年份（如 2024 单独出现）会被剔除，
    因为它通常是时间锚点而非可冲突的量化结论。
    除阿拉伯数字外，还会识别"近九成/超半数/三分之二"等中文比例（归一为百分比）。
    """
    toks = []
    for m in _NUM_RE.finditer(sent):
        raw = m.group(0).strip()
        value = raw.replace(" ", "").strip()
        if not value:
            continue
        if _YEAR_RE.match(value):
            continue  # 纯 4 位年份，非主张值
        # 展示清洗：'5044亿元人民币' 被单位表截成 '5044亿元人'（"人民币"的"人"被
        # 计数单位吞入）。数值解析已正确(与'亿元'同值)，这里去掉展示层多余的尾'人'，
        # 使期望值 '1200亿元' 能精确匹配。仅匹配 '亿元人/万元人'（"X万元人"里没有
        # '元'，计数如"5万人/亿人"不受影响），不碰主正则、零抽取风险。
        value = _RMB_TAIL_RE.sub(lambda m: m.group(1), value)
        tok = {"start": m.start(), "end": m.end(),
               "raw": raw, "value": value}
        # 剔除"非主张"的元数据数字杂音：
        #  (a) 月份数字：'7月' 的 7（月份是时间坐标，不是可冲突的量值）；
        #  (b) 半/季度标记里的数字：'H1'/'Q2'/'S1' 的 1/2/3/4（前导是 H/S/Q 字母），
        #      这类 1/2 会被单独当数值 token 抓出，误占 (指标,年份) 键、顶掉真正量值。
        if _is_metadata_digit(tok, sent):
            continue
        toks.append(tok)
    toks.extend(_cn_prop_tokens(sent))
    # 去掉阿拉伯与中文可能重复覆盖的区域（保守按 end 排序防乱序）
    toks.sort(key=lambda t: t["start"])
    return toks


def _is_metadata_digit(tok: dict, sent: str) -> bool:
    """判断一个数值 token 是否只是"时间元数据数字"（月份/半季标记），非主张量值。"""
    v = tok["value"]
    end = tok["end"]
    start = tok["start"]
    # 产品/化学/型号代号：'PD-1'/'IL-6'/'CE-5' 里数字前是 '<拉丁字母>-'（代号连字符）。
    # 数字是代号的一部分（PD-1 的 1），不是可交叉的量化值；一旦句内出现"销售额/收入"
    # 等量纲词，它会被就近误绑、多抽一条假的"销售额|1"。拉丁字母+'-'+数字是通用命名
    # 形态（药品/蛋白/型号），须剔除。范围连字符（5-3）前是数字、负号前是中文，均不受影响。
    if (start >= 2 and sent[start - 1] == "-"
            and sent[start - 2].isascii() and sent[start - 2].isalpha()):
        return True
    # 代号后缀字母：'5G用户'/'4K电视'/'5nm工艺'/'3D打印' 里数字后紧跟拉丁字母——数字是
    # 代号的一部分（5G 的 5），不是可交叉量值；一旦句内有"用户/规模"等词会被就近误抽
    # （'5G用户合计突破10亿'→多抽'用户数|5'）。汉字单位(亿/人/部/月)不受影响。
    if v.isdigit() and sent[end:end + 1].isascii() and sent[end:end + 1].isalpha():
        return True
    # 跨年度/赛季区间后段：'2015-16赛季/2018-19财年' 的 16/19 是年份区间的后一年
    # （2015-16 = 2015 至 2016），是时间坐标而非量值。判别：'-' 前是数字（年份末位）、
    # v<=99（两位年份后段）且后跟"赛季/年度/财年"等跨年词。缺它会把 16 就近误绑成
    # 出手数/营收等错指标、多抽假主张。注意不误伤真实区间量值：'3-5万吨' 的 5 后跟
    # '万吨'（量纲）不在触发词内，'5%-3%' 的 3 后跟 '%' 也不在触发词内。
    if (start >= 2 and sent[start - 1] == "-" and sent[start - 2].isdigit()
            and len(v) <= 2
            and re.match(r"(?:赛季|年度|财年)", sent[end:])):
        return True
    # 汇率比价基准：'1美元对人民币6.78元'/'1欧元兑…' 里 '1美元/1欧元' 是"一单位外币
    # 值多少本币"的比价基准（比例式的分母），不是可交叉的量值主张；真正的汇率值是
    # 后面的 '6.78元'。判别：token 恰为 '1美元/1欧元/1日元/1英镑' 且后紧跟 '对/兑'。
    # '1亿美元'（巨额货币量）不受影响——正则锚定 ^1美元$ 不匹配带'亿'的长式。
    if re.match(r"^1(?:美元|欧元|日元|英镑)$", v) and sent[end:end + 1] in ("对", "兑"):
        return True
    # 世代/代号序号："第8.6代AMOLED生产线/第3代半导体/第4代移动通信" 的 8.6/3/4 是
    # 技术世代号，非量值。须在纯整数判定前处理（含小数 8.6）。
    if re.match(r"^\d+(?:\.\d+)?$", v) and sent[end:end + 1] == "代":
        return True
    if not v.isdigit():
        return False
    # 数量定语：'385个药品/262家企业/12360只基金/370只货币基金/50个品种' 里，纯整数+
    # 通用量词(个/种/家/只/款/批/项) 是对实体**个数**的计数（口径描述），不是可交叉的
    # 量化指标（规模/收入/降幅/份额）。一旦句内出现降幅/规模等指标词，这种计数会被就近
    # 误绑、多抽一条假主张（'超50个药品…降幅超90%'→50 被抽成跌幅）。
    # 人/患者类量词(例/位/名/人)属于"患者数/人数"指标域，不在此剔除（留给指标域扩展）；
    # 带单位的真实量值(2000亿/万台/59.59GW)不是纯整数，天然不受影响。
    if v.isdigit() and sent[end:end + 1] in "个种家只款批项场":
        # 例外：'66个基点'/'提升3个百分点' 的"个基点/个百分点"是**计量单位复合**
        # （汇率升贬、幅度差的实际量值），不是实体个数计数，不得剔除。
        if re.match(r"个(?:基点|百分点|千分点|标准点|位点|患者|病例|样本|站点|网点)", sent[end:]):
            pass
        else:
            return True
    # 半年/季度/阶段标记：'2025年H1' 里 H1 的 1，前导为 H/S/Q 字母 → 剔除。
    # 限定前导恰是 H/S/Q 且紧跟其后（'H1'），避免误删 iPhone15/A100 这类"型号+数字"。
    if start > 0 and sent[start - 1] in "HSQhqs":
        return True
    # 孤立月份：'7月'/'2026年6月'/'，5月'，token 后紧跟'月'字且前面不是小数
    if sent[end:end + 1] == "月":
        # 排除"1.5月"这类带小数（极少，且视为量值更合理）；纯整数字才是月份
        if start - 1 < 0 or not sent[start - 1].isdigit():
            return True
    # 时间跨度/时长：'上线3年累计下载量超10亿'/'运行5年后用户达2亿'/'上市35天后
    # 下载量达1000万'里的裸整数（3/5/35）是"多少年/天/周"的时间跨度，不是可交叉的
    # 量化指标。一旦句内出现下载量/营收等量纲词，这种跨度数会被就近误绑、多抽一条
    # 假的"下载量|3"。真实年份(20xx)已被 _numeric_tokens 的 _YEAR_RE 提前剔除，落到
    # 这里的'N年/天/周/旬'只可能是非年份时间跨度（3年/35天/6周），剔掉安全。
    if v.isdigit() and re.match(r"(?:年|年后|年来|天|日后|周|旬|个月|个月后|小时后)", sent[end:]):
        return True
    # 月份区间起点：'1-5月'/'1-6月'/'1-12月' 的起始 '1'（时间坐标而非量值）。
    # 形如 <数字>-<1..12>月，且本 token 是该区间的起点月号（1-12、<=31）。
    # 例如 "2026年1-5月全国…新增装机59.59GW"——'1-5月' 是时间范围，起始 1 若不剔除，
    # 一旦句内出现"装机/出货"等水平量指标，就会就近绑上、多出一条假的"装机量|1"。
    if int(v) <= 31 and sent[end:end + 1] == "-":
        m = re.match(r"-(\d{1,2})月", sent[end:])
        if m and 1 <= int(m.group(1)) <= 12:
            return True
    # 覆盖地域/范围计数：'玩家覆盖200多个国家'/'遍布全球100多个地区' 里的裸整数
    # （200/100）是"覆盖了多少个地区"的范围数，不是可交叉的量化指标（下载量/用户数）。
    # 一旦句内出现下载量等水平量词，这种裸数会被就近误绑、多抽一条假的"下载量|200"。
    # 判别：裸纯整数（无单位）且紧跟 '多?个' + 地域类名词 → 判为范围计数剔除。
    # 真实量值永远带单位（2亿/万台/%），不带单位的地域数不属于本函数过滤对象。
    if v.isdigit() and re.match(
            r"多?(?:个|种)(?:国家|地区|城市|市场|省份|州|成员国|国|语言|语种|货币|语)",
            sent[end:]):
        return True
    return False


def _is_parallel_value(sent: str, num_start: int, win: int = 6) -> bool:
    """判断某数值 token 是否处于**并列列举**位置（"分别增长10.4%和9.4%"的 9.4）。

    并列连词（和/与/及/、/分别/以及）引导的多个数值，是"不同对象各自的值"
    （产量增10.4% vs 销量增9.4%、WTI 71.99 vs 布伦特 75.53），不是同口径舍入噪声
    （12.0亿 vs 12.1亿）。close 去重遇到并列值时须跳过合并、整组保留。
    """
    lo = max(0, num_start - win)
    return bool(re.search(r"和|与|及|、|分别|以及|跟", sent[lo:num_start]))


def _is_change_endpoint(sent: str, num_start: int, win: int = 16) -> bool:
    """判断某数值 token 是否是一段"变化句式"的到达端点（终点）。

    '从X提升至Y/由38%降到36%/增长至…' 里，紧跟在到达动词(提升至/增长至/
    降至/降到/升至…)后的 Y 是**变化终点**——它与起点 X 是同一指标的两个真实
    观测点（份额从45.8%涨到47.5%、占比从38%降到36%），不是同口径舍入噪声。
    返回 True 表示该数值是到达端，应在句内去重时保留（见簇A 豁免）。
    """
    lo = max(0, num_start - win)
    local = sent[lo:num_start]
    return bool(_CHANGE_ARRIVE_RE.search(local))


def _has_negative_sign(sent: str, tok: dict) -> bool:
    """判断某数值 token 是否带**负号前导**（"-3%"里的 '-'），用于识别负增长。

    背景："_NUM_RE" 不吞前导负号，token.value 恒为去掉符号的 '3%'，方向因此丢失。
    但 token.start 之前的字符可还原符号。本函数只认"负号 + 数值"的紧邻形态，即
    '-3%'/'－3.5%'（含全角减号 '－'）。范围/区间连字符必须排除：
      '5%-3%' 里中间那个 '-' 前是 '%'（区间分隔符），前后两个数都是正常正值，
      不可误判为负；'下降-3%' 里的 '-' 前是 '降' 字，是动词与负号，仍应识别。
    """
    s = tok["start"]
    if s <= 0:
        return False
    prev = sent[s - 1]
    if prev not in ("-", "－"):
        return False
    # 排除区间连字符：'5%-3%'/'3-5%' 的连字符前紧邻 %/％ 或数字 → 是范围而非负号。
    if s - 2 >= 0 and sent[s - 2] in "%％0123456789":
        return False
    return True


# P2-B 双量纲并列缩写："产销" = 产量+销量。判别模式：'产销…分别(完成/实现/为)A和B'。
# 命中时第一个水平量值指"产量"、第二个指"销量"（汽车/工业产量报告高频写法）。
_PRODUCT_SALES_DUAL_RE = re.compile(
    r"产销[^，。；;]{0,10}?分别(?:完成|实现|为|达)?\s*"
    r"(\d+(?:\.\d+)?\s*万?\s*(?:辆|台)?)\s*和\s*(\d+(?:\.\d+)?\s*万?\s*(?:辆|台)?)"
)


def _dual_metric_override(sent: str, tok: dict) -> str:
    """命中'产销分别完成A和B'时，按 tok 是 A 还是 B 返回 '产量'/'销量'，否则 ""。"""
    m = _PRODUCT_SALES_DUAL_RE.search(sent)
    if not m:
        return ""
    a_raw = m.group(1).replace(" ", "")
    b_raw = m.group(2).replace(" ", "")
    v = tok["value"].replace(" ", "")
    if v == a_raw:
        return "产量"
    if v == b_raw:
        return "销量"
    return ""


def _nearest_indicator(num_start: int, kw_hits: List[tuple], value: str = "",
                       window: int = 26, sent: str = ""):
    """给一个数值位置找'就近指标词'，返回 (指标词, 距离) 或 (\"\", None)。

    策略（关键是**量纲类型兼容**，而非针对个别句子的枚举）：
    - 只把**真正的名词型指标**（规模/营收/市场份额等，非动词）作为就近配对对象，
      避免把"达到/增长至/超过"当指标；
    - 一个数值只能绑到与它**同类量纲**的指标上：
      * **比率值(%)**（同比/渗透率/占比/跌幅/毛利率…）只挂比率型指标，
        不挂水平量指标——"PHEV销量同比下滑28%"的 28% 是跌幅，不是销量；
      * **水平量值**(辆/台/人/家/部/亿/元等计数)只挂水平量指标，
        不挂比率型指标——"…同比增长70%，其中欧洲贡献12万辆"的 12万辆
        是数量，不能被远处一个"同比增长"错标成增长率；
    - 优先取数字**紧前**的指标，前后都可，窗口内取距离最近的。
    """
    is_percent = "%" in value or "％" in value
    is_level = (not is_percent) and _looks_level_value(value)
    verbs = _VERB_KEYWORDS
    best_kw, best_d = None, None
    for kw_start, kw in kw_hits:
        if kw in verbs:
            continue  # 纯动词不做就近指标
        canon = _canonical_indicator(kw)
        # 量纲类型兼容（对称规则，非针对个别句子的枚举）：
        # - 比率值(%) 只允许挂"比率型"词（其规范名在 _RATE_INDICATOR_CANON 或
        #   本身就是涨跌词）；水平量指标(销量/规模/订单量/资本开支…)一律排除，
        #   使任何未收录的量纲词也抢不走 % 值——跌幅不会被标成销量/订单量。
        if is_percent:
            if kw in _DELTA_WORDS or canon in _RATE_INDICATOR_CANON:
                pass
            else:
                continue
        # - 水平量值(辆/台/亿/元…) 不挂比率型指标（增长率/跌幅/渗透率）——
        #   "…同比增长70%，其中欧洲贡献12万辆"的 12万辆 不能被远处
        #   的"同比增长"错标成增长率。
        if is_level and (kw in _RATE_INDICATOR_CANON or canon in _RATE_INDICATOR_CANON):
            continue
        if not is_percent and kw in _DELTA_WORDS:
            continue  # 水平量数值前的涨跌词当作动词，不作为比率指标
        # P2-1 "装机规模"复合抢注：'光伏新增装机规模12.48GW' 里数字前的"规模"比"装机"
        # 更近（距离优先），会抢走指标并归成笼统"规模"，而语义是"装机规模=新增装机体量"
        # 应归装机量。判别：'规模' 若紧前紧跟强量纲指标词（装机/出货/交付/产能…），
        # 是"X规模"的冗余量词尾，不作为独立指标，让更早的强指标词接管。
        if kw == "规模" and kw_start >= 2 and sent[kw_start - 2:kw_start] in (
                "装机", "出货", "交付", "产能", "销量", "产量", "营收", "销售额", "下载量"):
            continue
        kw_end = kw_start + len(kw)
        if kw_end <= num_start:
            dist = num_start - kw_end        # 关键词在数字前
        else:
            dist = kw_start - num_start      # 关键词在数字后
        if best_d is None or dist < best_d:
            best_d, best_kw = dist, kw
    if best_d is not None and best_d <= window:
        return best_kw, best_d
    return "", None


def _strip_year_scope_head(seg: str) -> str:
    """清洗从年份/量纲前切出的候选主语：剥掉年份残渣、范围词、虚词、估测动词。

    注意不去剥单字介动字(到/从/比/达)——"比"可能属于"比亚迪"等实体名；
    且只对多字引导词做句首剥离，避免误伤实体。
    """
    seg = re.sub(r"^(?:20\d{2}|19\d{2})\s*年?", "", seg).lstrip("年月日")
    # 剥"报告显示/数据显示/预计到/据估计"等多字句首引导
    seg = _strip_leading_noise(seg)
    seg = re.sub(r"^(?:预计到|达到约|增长至|截至)\s*", "", seg)
    # 剥承前连接词"其中/其余"（"…2200亿元，其中出海收入约150亿美元"里主体是"出海"，
    # "其中"只是把子句挂在前面同一大主体下的连接词，非实体，不可进主体）。
    # 这里放句首剥词层，比只在 _tail_level_subject 剥更彻底（carve 命中锚点会短路后者）。
    for conn in ("其余", "其中"):
        if seg.startswith(conn) and len(seg) > len(conn):
            seg = seg[len(conn):]
    # 剥周期状语（上半年/一季度/全年…，可能紧跟年份后："2024年上半年"）
    seg = _strip_leading_period(seg)
    # 剥"第X批/首批/上批"这类批次序数（"第七批国家药品…"→"国家药品…"）
    seg = re.sub(r"^(?:第[零一二三四五六七八九十百千两]+批|首批|上批|本批|上一批)\s*", "", seg)
    # 剥范围词（全球/中国/全国/国内/某…）
    for s in sorted(_SUBJECT_STOP, key=len, reverse=True):
        if seg.startswith(s) and len(seg) > len(s):
            seg = seg[len(s):]
    # 去掉尾部可能残留的"市场规模/行业/份额"等量纲尾，只留主体实体名
    for tail in ("市场规模", "行业规模", "市场份额", "市场", "行业", "产业",
                 "领域", "赛道", "规模", "份额", "市占率", "渗透率"):
        if seg.endswith(tail) and len(seg) > len(tail):
            seg = seg[: -len(tail)]
            break
    # 去掉尾部"平均/合计/累计/总/综合"等修饰（量纲前的口径词，"咖啡豆平均"→"咖啡豆"）
    for tail in ("平均", "合计", "累计", "累计平均", "总体", "综合"):
        if seg.endswith(tail) and len(seg) > len(tail):
            seg = seg[: -len(tail)]
            break
    # 去掉尾部残留的周期词（"公司一季度"→"公司"）：周期是报道口径，非实体。
    # 仅在整词收尾时剥（不剥"公司"这类名本身）。
    for p in sorted(_LEADING_PERIOD, key=len, reverse=True):
        if seg.endswith(p) and len(seg) > len(p):
            seg = seg[: -len(p)]
            break
    return seg.strip(" ，,、")[:16]


# 结构切分用的"量纲/时间边界"锚：这些词前通常是主语实体。
# 注意：不用裸单字"年"作锚——年份边界(20xx年)由 _carve_subject_clause 的
# _YEAR_IN_RE/年份分支专门处理，完整期词(全年/今年/去年/年度)已单独列出。
# 裸"年"作锚会抢走"首年/财年"等期词内部的'年'，把"《原神》首年营收"错切出"《原神》首"。
_SUBJECT_ANCHOR_RE = re.compile(
    r"(?:全年|今年|去年|年度|市场|行业|产业|领域|赛道|市占率|市占|"
    r"市场份额|份额|出货量|出货|装机量|规模|渗透率|国产化率|营收|销售额|"
    r"销量|用户数|收入|毛利率|净利率|净利润|市值|增速|增长率|日活|月活|"
    r"门店|订单|装机|融资|资本开支|保费|装机|价格"
    r"|销售面积|施工面积|成交面积|面积"
    r"|收购价|零售价|均价|出厂价|售价|报价|挂牌价|股价|楼面价"
    r"|降价幅度|涨价幅度|降幅|涨幅|幅度"
    r"|产量|出栏量|存栏量|消费量|进口量|出口量|采购量|交易量|货运量|周转量"
    r"|客运量|引种量|投放量|发行量|装载量|回收量|发电量|售电量"
    r"|票房|收入|营收|流水|成交量|网点数|家数|门店数|客户数|客流量)"
)


def _carve_subject_clause(local: str) -> str:
    """无词典的结构式主语切分：主体 = '年份/量纲锚点前紧邻的名词性成分'。

    之所以普适，是因为绝大多数行业报告的量化句都遵循：
        [主体实体] 2024年 [量纲词]…达X
    或  [主体实体][市场/规模/市占率]…X
    主体不依赖是否在词典中，只靠结构位置（紧邻年份前/锚点词前）切出。
    对"奈雪/苹果/比亚迪/某新品"这类词典外主体同样成立。

    处理两类结构：
    - 年份前置式："比亚迪2024年…"→年份前"比亚迪"即主体；"预计到2025年中国新能源
      汽车…"→主体在年份之后（年份前只剩引导词），回退用年份后的量纲锚点。
    - 无年份式："苹果市占率约19%"→取"市占率"前的"苹果"。
    """
    s = _strip_leading_noise(local)
    # 剥句首周期状语（上半年/一季度/全年…），避免无年份分支把"上半年"里的
    # "年"误当锚点，从而切出"上半"这种假主语；也处理"2024年上半年…"紧跟年份。
    s = _strip_leading_period(s)
    ym = re.search(r"(?:20\d{2}|19\d{2})\s*年?", s)
    if ym:
        before = s[:ym.start()]
        # 年份前若无实质名词（只剩引导/预计/到），则主体在年份之后、量纲锚点之前
        bclean = _strip_year_scope_head(before)
        if bclean and not _looks_verb_only(bclean):
            return bclean
        after = s[ym.end():]
        am = _SUBJECT_ANCHOR_RE.search(after)
        if am:
            head = _strip_year_scope_head(after[:am.start()])
            if head and not _looks_verb_only(head):
                return head
    # 无年份：取第一个量纲/时间锚点前的名词性片断
    am = _SUBJECT_ANCHOR_RE.search(s)
    if am and am.start() > 0:
        head = _strip_year_scope_head(s[:am.start()])
        if head and not _looks_verb_only(head):
            return head
    return ""


# 分句里的"纯实体主体 + 虚词"收尾模式（供"句尾数值"结构兜底用）。
# 形如 "新能源约104万辆" / "宁德时代以125GWh" / "其中燃油车只剩约54万辆"：
# local 只有实体名词 + 尾随虚词(约/以/只剩…)，既无年份锚也无量纲锚，
# _carve_subject_clause 因此切不出主题、回退整句 → 不同主体的值撞到同一脏主题键、
# 被句内去重顶掉。本函数专治此类：从"数值前的分句文本"剥虚词与承前连词，
# 尽量还原出真正的实体主体。仅服务**水平量值**（比率 % 的主题绑定走就近指标，
# 不做此宽松，避免把"降幅""同比增长"等比率语境错当主体）。
# 结果走黑名单过滤：剥出的若是指标词尾/纯时间，宁缺毋滥返回空。
# 主体出现承前省略（"较去年同期的X"）时也返回空（主体其实在前面小句）。
# 前导主语序列：可作动词状语的量词/时间/连词。
_TAIL_LEAD_NOISE = sorted(
    ["其中", "其余", "较", "比", "其", "该", "同期", "同比", "环比", "较去年同期"],
    key=len, reverse=True)
# 承前省略的信号：这些作为**句首**说明主体在前面，本句无新主体。
# 不用单字"其/该"（会误伤"其中/其余"等引出新主体的词）；"较/比/同期/同比/环比"
# 是明确的承接比较信号，主体几乎总在前句。
_TAIL_LEAD_FALLBACK = sorted(["较同期", "较去年", "较", "比", "同期", "同比", "环比"],
                             key=len, reverse=True)
_TAIL_TRAIL_NOISE = sorted(
    ["只剩约", "剩约", "约为", "约计", "仅剩", "还剩", "达到约", "增长至", "下降至",
     "约有", "约", "达", "剩", "仅", "位居", "突破", "超过", "为", "是", "有望",
     "左右", "至", "到", "以", "仍", "还有", "达约", "约达"],
    key=len, reverse=True)
# 剥成"指标词/量纲名"收尾 → 这不是实体主体，拒绝。
_TAIL_BAD_END = re.compile(
    r"(渗透率|占比|增长率|增速|市场份额|毛利率|净利率|降幅|涨幅|规模|营收|"
    r"销售额|销量|出货量|装机量|门店数|产量|渗透|市占|装机|出货|净利)$")
# 剥剩"纯虚词/无实体"短语 → 拒绝（宁缺毋滥）。宽松器只在标准切分失败时兜底，
# 不产出"占总/约占"这类假主体。仅拦确定无实体的纯虚词短语；"欧洲贡献/出口"这类
# 可能含实体的保留（宁偏主题不纯，也不误伤真实量纲方向）。
_TAIL_BAD_PHRASE = {
    "占总", "约占", "占", "其中", "约", "达", "剩", "位居", "其中约",
    "占大头", "主要是", "为", "是",
}


def _tail_level_subject(local: str) -> str:
    """从'数值前分句文本'还原实体主体（仅水平量语境调用）。失败返回 ""。"""
    s = local.strip("，,、 ；; ")
    if not s:
        return ""
    # 剥承前省略的句首信号：整句较长时主体在前句，本句"较去年同期/其/其中…"仅承接
    for h in _TAIL_LEAD_FALLBACK:
        if s.startswith(h) and len(s) > len(h):
            return ""
    # 剥句首连词/范围语
    for h in _TAIL_LEAD_NOISE:
        if s.startswith(h) and len(s) > len(h):
            s = s[len(h):]
            break
    # 剥"是排名第二的亿纬锂能("这类排名修饰 → "亿纬锂能"
    m = re.match(r"^(?:是|为)?排名第[一二三四五六七八九十百]+的", s)
    if m:
        s = s[m.end():]
    # 反复剥尾随虚词直到稳定
    prev = None
    while s and s != prev:
        prev = s
        for t in _TAIL_TRAIL_NOISE:
            if s.endswith(t):
                s = s[:-len(t)]
                break
    if not s:
        return ""
    # 残渣含数字/时间词收尾 → 非实体，拒绝
    if re.search(r"[0-9]", s):
        return ""
    if _TAIL_BAD_END.search(s):
        return ""
    if re.search(r"(年|月|日)$", s):
        return ""
    if s in _TAIL_BAD_PHRASE or any(s.startswith(p) for p in _TAIL_BAD_PHRASE if len(p) >= 2):
        return ""
    # 收尾再清一遍残留的"的/是/有"
    s = s.rstrip("的与及和、,， (（是约")
    if _looks_verb_only(s):
        return ""
    return s[:12]


def _looks_verb_only(text: str) -> bool:
    """粗略判断切出的片段是否'只剩虚词/动词'（无名词主体）。"""
    t = text.strip("，,、 ")
    if not t:
        return True
    if re.fullmatch(r"[到从较比于至在向与和及]", t):
        return True
    return len(t) <= 1 and t in "年近本这该"


# ---- 游戏/影视作品域主体修饰裁剪（限定业务域，非全局规则）----
# 背景：手游史等作品史句子常是"《书名》+[地域/口径/累计/单期]+量纲词"（如"《王者
# 荣耀》全球累计下载量"），carve 会把书名后的"全球/累计/下载量"整串残留进主体。
# 但这类"书名号作品 + 量纲尾修饰"只出现在游戏/影视等**作品域**（财报/行业报告里
# 书名号很少），故按用户决策**限定游戏实体域触发**：仅当句含游戏/手游/《作品名》
# 等域信号时才做尾裁剪，非游戏域句子完全不动（对通用语料零波及）。
_GAME_SUBJECT_SIGNAL = re.compile(r"游戏|手游|《|电玩|主机游戏")
# 作品名《…》之后常见的"量纲/修饰尾"（裁剪对象）：地域/口径/期次 + 下载/流水/营收等。
_GAME_TAIL_WORDS = [
    "下载量", "下载次数", "下载", "流水", "营收", "收入", "销售额", "销量",
    "用户数", "玩家数", "日活", "月活", "累计", "合计", "全球", "国内",
    "海外", "首月", "首季", "首年", "单月", "当月", "全年", "累计下载量",
    "活跃用户数", "总流水", "旗下", "贡献",
]


def _is_game_subject_sentence(sent: str) -> bool:
    return bool(_GAME_SUBJECT_SIGNAL.search(sent))


def _trim_game_subject_tail(subj: str) -> str:
    """把'《书名》+地域/累计/量纲尾'里书名后的修饰串反复剥掉，只留作品/实体名。
    若剥到只剩纯期次/修饰词（如'全年''累计'），返回空串（调用方回退整句取真主体）。
    仅应由游戏域调用方使用（见 _subject_for），不作用于通用句。"""
    s = subj.strip(" ，,、；;")
    if not s:
        return s
    prev = None
    while s and s != prev:
        prev = s
        for t in sorted(_GAME_TAIL_WORDS, key=len, reverse=True):
            if s.endswith(t):
                s = s[: -len(t)].rstrip(" 的，,、")
                break
    # 剥后只剩纯期次/地域修饰词 → 无实体，返回空（让 _subject_for 回退整句）
    if s and s in ("全年", "单月", "当月", "累计", "首月", "首年", "首季",
                   "全球", "国内", "海外", "合计", "平均", "综合"):
        return ""
    return s.strip(" ，,、；;")


def _normalize_subject(subj: str) -> str:
    """主体归一：去掉《》书名号等纯装饰标记，使同一作品/实体跨来源不同写法
    （"《王者荣耀》" vs "王者荣耀"）落到同一主题键、能被交叉验证合并成 high。
    《》只是排版强调标记，非实体名的一部分；去掉不影响"书籍名作为事实"的语义
    （《三体》销量 与 三体销量 本就该归并交叉）。
    """
    s = subj.strip() if subj else subj
    if not s:
        return s
    s = s.replace("《", "").replace("》", "")
    return s.strip(" ，,、")


# P1：占比/率值分句无实体时的"残词主体"头判定——"占汽车总销量"被 carve 出
# "占汽车总"（'占'是引导动词残留，非实体），"约占全部发电量"被 carve 出"约占全部"
# （'约'头同残），此类主体视为无效继续回溯。
_BAD_SUBJ_HEAD_RE = re.compile(r"^(?:占|约|较|比|以及|同时|同比|环比)")
# P1：回溯结果里残留的"数字尾巴"（"新能源约104万辆"→裁"约104万辆"留"新能源"、
# "燃油车只剩约54万辆"→裁"约54万辆"）。仅对率值回溯主体应用。
_TAIL_DIGIT_RE = re.compile(r"(?:约|近|超|达|剩)?\d[\d.,]*[%％万]?(?:辆|台|元|人|户|亿|GWh|MWh|GW|MW|TWh)?$")

# P4b(专项1)：归属前缀结构——"归母净利润/归属于该行股东的净利润/归属母公司股东的净
# 利润"等。净利润归属前缀是**定语成分**（归母=归属于母公司股东），不是主体实体；
# 但因"净利润"在锚表里，carve 会把前缀切出来当主体（"归母"、"归属于该行股东的"），
# 造成 823.20亿/127.42亿 等值主体为残词。正则覆盖真实报告高频写法：
#   归母净利润 / 归母净利
#   归属于<该行/母公司/上市公司/本公司>股东的(?)净利润
#   归属母公司股东的净利润
_ATTRIB_PREFIX_RE = re.compile(
    r"(?:归母净利|归母净利润|归属于[^，。；、，]{0,12}?股东的?净利润|"
    r"归属母公司(?:股东)?的?净利润|归母)"
)
_ATTRIB_RESIDUAL_RE = re.compile(
    r"^(?:归母|归属于[^净]{0,18}?$|归属母公司[^净]{0,18}?$)"
)

# P5(专项2)：并列多主体错位的两类修复的共享常量。
# F1 裸承接：值所在分句是"裸实体+变化动词"或纯裸实体（无指标词锚）时，该实体即本值
# 主体——"内窥镜增长31.9%"（应归内窥镜而非回退到前主体）、"火山引擎14.8%"（裸实体
# 直接跟值）。剥尾动词表：承接省略句的谓语变化词（增长/下滑/亏损…），按长词优先剥。
_VERB_TAIL_CLEAN = (
    "同比增长", "同比下滑", "同比减少", "同比上涨", "同比下跌", "同比微增",
    "环比增长", "环比下滑", "增长", "下滑", "下跌", "下降", "上升", "上涨",
    "亏损", "减少", "增加", "微增", "提高", "降低",
)
# 剥动词后再剥的尾字（"达/至/约/为/以…"等粘着成分）。
_BARE_SUBJ_END_NOISE = "的了约达至为以并至"


def _clean_bare_subject(cand: str) -> str:
    """把候选"裸实体"段清理成可用主体；不干净返回空串。

    1) 剥尾部变化动词（内窥镜增长→内窥镜）；2) 剥尾字粘着成分；3) 拒绝含数字/
    顿号连词/句内指标词/引导残词/纯虚词者（那些不是"裸实体承接"，交给主链路）。
    """
    if not cand:
        return ""
    s = cand.strip(" ，,、；;：:")
    if not s:
        return ""
    for vt in _VERB_TAIL_CLEAN:
        if s.endswith(vt) and len(s) > len(vt):
            s = s[: -len(vt)]
            break
    s = s.rstrip(_BARE_SUBJ_END_NOISE).strip(" ，,、；;")
    # 剥承前引导词（其中/其余…）——"其中东南亚占约"→"东南亚占"
    for _lead in ("其中", "其余", "另外", "此外", "分别", "比上年", "较上年", "同比", "环比"):
        if s.startswith(_lead) and len(s) > len(_lead):
            s = s[len(_lead):]
            break
    if len(s) < 2 or len(s) > 16:
        return ""
    if re.search(r"[0-9%％、，,和与及/．]", s):
        return ""
    # 剥动词后只剩动词本身（"增长5.2%"的 local='增长'）→ 非实体，放弃
    if s in _VERB_TAIL_CLEAN or s in _DELTA_WORDS:
        return ""
    # 承接副词残留（"增幅更是高达104%"的 local 剥到"增幅更是高"）→ 非纯实体
    if any(_adv in s for _adv in ("更是", "高达", "大幅", "明显", "快速", "持续",
                                   "稳步", "显著", "强劲", "较快", "有望", "同比", "环比",
                                   "仅", "小幅", "微幅")):
        return ""
    # 动词词根开头（"增长高/下滑明显"是承接省略的动词+副词，非"实体"）→ 放弃
    if s.startswith(("增长", "下滑", "下跌", "下降", "上升", "上涨", "亏损",
                     "减少", "增加", "提高", "降低", "微增")):
        return ""
    if _looks_verb_only(s):
        return ""
    if _BAD_SUBJ_HEAD_RE.match(s):
        return ""
    # 尾留引导动词残（"东南亚占"的"占"= 占/占比的动词引导）→ 非纯实体，放弃
    if s.endswith(("占", "同比", "环比", "分别", "较上年", "占比", "依次")):
        return ""
    for kw in _CLAIM_KEYWORDS:
        if kw in s:
            return ""
    return s


def _is_attrib_residual(subj: str) -> bool:
    """判定某主体是否归属前缀残词（"归母"/"归属于该行股东的"这类，非公司实体）。"""
    if not subj:
        return False
    s = subj.strip(" ，,、；;")
    if not s:
        return False
    if s == "归母":
        return True
    # "归属于…的" / "归属母公司…" 且不含"净利"（含净利=整体未剥离，作正常主体）
    if (s.startswith("归属于") or s.startswith("归属母公司")) and "净利" not in s:
        return True
    return False


def _subject_backtrack(sent: str, num_start: int, game: bool, max_back: int = 4) -> str:
    """向左逐分句回溯最近实体主体（P1，仅率值/裸数启用）。

    占比/率值所在分句常只含"同比增长/全球占比/降至"等，切不到实体主体；旧逻辑
    直接回退**整句**结构切分，会把主句主体强加给归属别处的率值
    （"…中国出货1888.6GWh，同比增长55.5%，全球占比82.8%"：55.5%/82.8% 属"中国"，
    旧逻辑却归"锂离子电池"）。本函数从该值所在分句开始**逐级向左合并分句文本**
    重试结构切分，返回最近一个能切出干净主体的分句主体；全部失败返回 ""。
    水平量值不走此路径（它们另有 _tail_level_subject → 整句的既有逻辑）。
    """
    seps = [i for i in range(num_start) if sent[i] in "，,、；;"]
    lo = max(0, len(seps) - max_back)
    for k in range(len(seps), lo - 1, -1):
        seg_start = seps[k - 1] + 1 if k > 0 else 0
        seg = sent[seg_start:num_start]
        subj = _carve_subject_clause(seg)
        if game and subj:
            subj = _trim_game_subject_tail(subj)
        if subj and not _BAD_SUBJ_HEAD_RE.match(subj):
            # 裁掉回溯段带入的数字尾巴（"新能源约104万辆"→"新能源"）
            return _TAIL_DIGIT_RE.sub("", subj).strip(" ，,、")
    return ""


def _subject_for(sent: str, num_start: int, *, is_level: bool = False) -> str:
    """给某个数值找它所属小句的主题词（clause-local）。

    一句里若并列多个主体（"智能手机出货量…，苹果市占率…"），各数值的主题
    必须取自各自所在的逗号分句，否则会把"苹果市占率"当成"智能手机出货量"
    的主题、破坏交叉验证与冲突判定。

    受 _CARVER_STRUCTURE_ENABLED 开关控制：
    - 开启（默认）：用无词典的结构式切分 _carve_subject_clause；
    - 关闭：回退到旧的 _pick_subject 正则切分。

    is_level=True 表示该数值是水平量（带计数/金额/功率等绝对单位，非 %）。
    对水平量值，若结构切分抓不到（如"新能源约104万辆"这类句尾数值、分句里
    只有实体主体+虚词、无年份/量纲锚），再尝试 _tail_level_subject 从分句文本
    还原主体；比率值(%)不触发此宽松，避免把比率语境(降幅/同比增长)错当主体。
    """
    # 找出该数值所在的子句区间：[start, next 分隔符)
    start = 0
    for m in re.finditer(r"[，,、；;。]", sent):
        if m.start() >= num_start:
            break
        start = m.end()
    local = sent[start:num_start]              # 数值之前的小句文本
    game = _is_game_subject_sentence(sent)      # 仅游戏/作品域才裁剪书名后的量纲修饰尾
    if _CARVER_STRUCTURE_ENABLED:
        subj = _carve_subject_clause(local)
        # 残词主体（"占汽车总销量"→"占汽车总"）不算有效，视为未切到 → 继续回溯。
        if subj and _BAD_SUBJ_HEAD_RE.match(subj):
            subj = ""
        if subj:
            subj = _trim_game_subject_tail(subj) if game else subj
            if subj:
                return subj
            # 游戏域裁剪把"全年流水"这类剥成空 → 回退整句取真主体（书名/实体）
            subj = _trim_game_subject_tail(_carve_subject_clause(sent)) if game \
                else _carve_subject_clause(sent)
            if subj:
                return subj
        # P1 率值向左回溯：占比/率值所在分句常无实体（"同比增长55.5%"/"全球占比82.8%"/
        # "占汽车总销量的49.6%"），旧逻辑直接回退整句取主句主体（"锂离子电池"/长句尾）。
        # 应向左跨分句找最近实体主体分句（"其中中国出货1888.6GWh"→"中国"、
        # "新能源约104万辆"→"新能源"）。仅对非水平量(率/裸数)启用；水平量保持既有
        # _tail_level_subject→整句路径，避免扰动 158万辆/104万辆 等既有主体归属。
        if not is_level:
            bsubj = _subject_backtrack(sent, num_start, game)
            if bsubj:
                return bsubj
        # 句尾数值结构（实体主体+虚词，无锚点）：仅水平量值启用宽松主体还原，
        # 避免不同主体的水平量值因主题撞脏键而互相顶掉。
        if is_level:
            tsubj = _tail_level_subject(local)
            if tsubj:
                tsubj = _trim_game_subject_tail(tsubj) if game else tsubj
            if tsubj:
                return tsubj
            # 游戏域兜底：从句尾剥词仍为空（"全年流水"等只有量纲/期词）→ 回退整句取主体
            if game:
                whole = _trim_game_subject_tail(_carve_subject_clause(sent))
                if whole:
                    return whole
        # 结构切分没抓到（如分句里只有量词/数值），回退整句结构切分
        return _trim_game_subject_tail(_carve_subject_clause(sent)) if game \
            else _carve_subject_clause(sent)
    # 旧逻辑
    subj = _pick_subject(local)
    if subj:
        return subj
    return _pick_subject(sent)


def _sentence_claims(sent: str, url_key: str) -> List[dict]:
    """把一个句段解析成多条主张（一句支持多个'指标-数值'对）。

    相比旧版"一句一条"，本实现：
    - 用**就近配对**让每个数值绑定到离它最近的指标词，避免张冠李戴；
    - 为每个数值识别**口径年份**（显式 2024/2030 等），把同一指标但不同年份的
      数值区分开（如 2024 实际规模 vs 2030 预测），不互相顶替；
    - 同一指标**同一口径年份**只在句内保留离它最近的那条数值（防单源数值膨胀）；
    - 找不到就近指标时才回退到整句统一指标。
    """
    kw_hits = _match_keywords(sent)
    sent_indicator = _pick_indicator([k for _, k in kw_hits])
    level_indicator = _pick_level_indicator([k for _, k in kw_hits])
    toks = _numeric_tokens(sent)
    if not toks:
        return []

    # 每个数值 → (指标, 年份, 主题, 绑定距离)。指标优先级：
    #   就近名词指标 > 整句名词指标 > 量纲词尾兜底(订单量/日活/资本开支…) >
    #   按数值单位兜底(规模/增长率) > 丢弃
    bound = []                       # [{start, value, indicator, year, subject, dist}]
    for tok in toks:
        kw, dist = _nearest_indicator(tok["start"], kw_hits, value=tok["value"],
                                      sent=sent)
        is_percent = "%" in tok["value"] or "％" in tok["value"]
        # 就近名词指标（已按量纲类型过滤）> 整句指标(需量纲类型兼容) > 维度词尾兜底
        # > 按单位兜底。比率值(%) 不继承水平量整句指标（门店数/销量/规模…），
        # 否则"…其中东南亚占约六成"会被句首"门店数"吞并、与"4000家"同桶丢失。
        #
        # 整句指标选择：比率值(%) 用全局 _pick_indicator（市场份额/渗透率等比率优先）；
        # **水平量值**（非%）用 _pick_level_indicator——从命中词里挑"水平量型"指标
        # （出货量/营收/规模…）。这样长句句尾若出现"市场份额/占比"等比率词，不会抢走
        # 句首/分句里水平量值的量纲归属（"…出货量从X增长至Y，…；市场份额也从P→Q"，
        # X/Y 属出货量而非被市场份额吞掉）。
        cand_si = level_indicator if not is_percent else sent_indicator
        si_canon = (_canonical_indicator(cand_si)
                    if cand_si and cand_si not in _VERB_KEYWORDS
                    else "")
        level_si = bool(si_canon) and si_canon in _LEVEL_INDICATORS
        rate_si = bool(si_canon) and si_canon in _RATE_INDICATOR_CANON
        indicator = ""
        if kw:
            indicator = _canonical_indicator(kw)
        elif si_canon and not (is_percent and level_si) and not (not is_percent and rate_si):
            indicator = si_canon
        if not indicator:
            # 量纲词尾兜底先于"按单位估规模"：即便无词典词，也能识别
            # "订单量/日活/资本开支/GMV…"这样更具体的维度名，而非笼统归"规模"
            indicator = _find_fallback_dimension(sent, tok["start"], tok["end"])
            if indicator:
                # 兜底抓出的量纲词（门店/日活/订单量…）归一到规范指标名
                indicator = _canonical_indicator(indicator)
            if not indicator:
                indicator = _default_indicator_by_unit(tok["value"])
                # 纯 % 且无任何比率信号时，默认增长率其实指向"占比/份额"场景更贴切：
                # 真正的同比/增速几乎总带"增长/下滑"等词（已是关键词），能就近命中；
                # 落进这里(无关键词)的 % 多为"X成受访者/其中来自/占…"的比例表述。
                if is_percent and indicator == "增长率":
                    lo = max(0, tok["start"] - 12)
                    local = sent[lo: tok["end"] + 6]
                    if re.search(r"占|占比|来自|受访|其中|份额|比重|贡献", local):
                        indicator = "占比"
        if not indicator:
            continue
        year = _detect_year(sent, tok["start"])
        subj = _subject_for(sent, tok["start"],
                            is_level=_looks_level_value(tok["value"]))
        # 负增速方向归一：解析为'增长率'的值若带**负号前导**（'录得-3%的增速'、
        # '同比-5.5%增速'），语义是"该增速为下跌"，应归入'跌幅'口径（与
        # '下滑3%/下降3%'同桶同值），而非留在'增长率'。值保持量值('3%')——
        # 跌幅词本身自带负方向，负号不进入值（与既有 下滑3%→跌幅|3% 呈现一致）。
        # 只作用于'增长率'桶：净利率/毛利率/市场份额等"比率真实为负"(-3%净利率，
        # 语义≠下滑3%)的值本就不进'增长率'，天然不受影响；范围连字符('5%-3%')
        # 由 _has_negative_sign 排除，不会被误判为负。
        if indicator == "增长率" and _has_negative_sign(sent, tok):
            indicator = "跌幅"
        # P0 倍数误抽止血：数值后紧跟"倍"是**倍率关系**（'是亿纬锂能的2.6倍'、
        # '增长5倍'），不是出货量/规模等水平量。缺此规则 2.6 会被就近绑成
        # '出货量|2.6'、5 绑成'营收|5'，污染交叉验证。独立成'倍数'桶——
        # 不与'增长率'混（5倍=+400%，与 5% 不同量纲不可 close），也不与水平量混。
        if tok["end"] < len(sent) and sent[tok["end"]] == "倍":
            indicator = "倍数"
        # P2-B 产销并列展开：'产销分别完成257.3万辆和258.4万辆' → 前值产量/后值销量。
        dual = _dual_metric_override(sent, tok)
        if dual:
            indicator = dual
        # E1(P3评审): 相对值(增长率/跌幅/涨幅)年份口径 = None。相对变化的时间是
        # "基期→报告期"二元组,挂单一绝对年必然歧义(挂报告年≠挂基期年,跨源合并会
        # 假冲突)。与占比/渗透率/稼动率等比率**状态量**区分(状态量仍挂显式年)。
        # 负增速翻转(-3%→跌幅)与'倍数'桶天然不在此集合外;非相对值 claim 年份零变化。
        if indicator in ("增长率", "跌幅", "涨幅"):
            year = None
        # E3b(P3评审存量口径,时段/时点区分): "截至X年…"后的**水平量存量**若为
        # 时点（X年底/末/X月底/月末/某日）→ 不挂年度（12.86亿千瓦/40.8亿千瓦
        # 装机容量 → None）——时点存量与"2026全年"口径不等价，挂年会在跨源合并
        # 时产生假冲突。区分（用户拍板口径）：
        #   - 时段累计（截至2026上半年/全年/1-5月）→ 挂显式年（744.6万辆→2026）；
        #   - 比率状态量（_is_state_ratio，占比/渗透率…）→ 无论时点仍挂显式年；
        #   - 相对值/倍数桶由 E1/E3a 处理，不在此列。
        if year is not None and not _is_state_ratio(indicator) \
                and indicator not in ("增长率", "跌幅", "涨幅", "倍数"):
            _iz = max(sent.rfind("截至", 0, tok["start"]),
                      sent.rfind("截止", 0, tok["start"]))
            if _iz >= 0:
                _span = sent[_iz: tok["start"]]
                _period_word = re.search(
                    r"(?:上半年|下半年|全年|年度|\d{1,2}月(?:份)?(?:至今|累计)|"
                    r"\d{1,2}个月)", _span)
                _point_word = re.search(r"(?:年底|年末|月底|月末|季度末|\d{1,2}日|底|末)", _span)
                if _point_word and not _period_word:
                    year = None
        bound.append({"start": tok["start"], "value": tok["value"],
                      "indicator": indicator, "year": year,
                      "subject": subj, "dist": dist})

    # 空主体受控继承（P4，仅在 subject 严格为空时触发）：承前省略的**承接分句**
    # 常切不出自己的主体——"出游人次65.22亿，比上年同期增加9.07亿，同比增长16.2%"
    # 的 16.2%（所在分句只有'同比增长'，_subject_backtrack 左遇数字/连词段也切不出）；
    # "产销分别完成257.3万辆和258.4万辆" 的 258.4（前值后值同句并列）。这类空主体值
    # 与其左侧最近值共享主语（并列/承前省略），把左侧最近**非空**主体继承给空值。
    # 触发必须满足（防无边界继承、防跨句/跨新比价句污染）：
    #   1) 本值 strict 空主体（上面的 else 分支天然满足）；
    #   2) 本值所在小句是**承接句**：以承前连词/比率词开头（同比/环比/较/分别/增长/
    #      下滑/约/达…），或**并列尾**（"257.3万辆和"以 和/与/及/、 收尾）——即它承接
    #      前文主体，不是"1欧元兑7.2元"（独立比价新句）或"iPhone均价800美元"（新实体句）；
    #   3) 继承源是句内左侧最近非空主体，且与其距离 ≤50 字符、同句（不跨；。\n）；
    #   4) 继承源本身为水平量值(非%比率)——比率值的主体链不带单位实体，不作继承源。
    # 任一条不满足则保持空（宁缺毋滥，交给下游 treat-as-null）。
    # 承接小句判定：(a) 句首是承接词（主句主体在前）；(b) 句尾并列连词（"A和B"后位值）。
    _inher_subj, _inher_start, _inher_level = "", -10**9, False
    for _b in bound:
        _seg_start = 0
        for _sep in ("，", "；", "。", "；", ",", "、", ":", "：", "\n"):
            _i = sent.rfind(_sep, 0, _b["start"])
            if _i > _seg_start:
                _seg_start = _i + 1
        _local = sent[_seg_start:_b["start"]]
        _lead = _local[:14]
        _is_inherit_clause = bool(
            re.match(r"^(?:同比|环比|较上年|较去年|较|分别|其中|和|与|及|增长|下滑|"
                     r"下降|上涨|下跌|约|达|剩|还|又|同比分别|环比分别|年增长)", _lead)
            or re.search(r"(?:和|与|及|、)\s*$", _local)
            or _local.startswith(("较同期", "比上年", "较上一年")))
        if _b["subject"]:
            _inher_subj, _inher_start = _b["subject"], _b["start"]
            _inher_level = "%" not in _b["value"] and "％" not in _b["value"]
        elif _inher_subj and _inher_level and _is_inherit_clause \
                and 0 <= _b["start"] - _inher_start <= 50:
            _gap = sent[_inher_start:_b["start"]]
            if "；" not in _gap and "。" not in _gap and "\n" not in _gap \
                    and "；" not in _gap:
                _b["subject"] = _inher_subj
                # 链式继承：被赋值者同样作为后续承接分句的继承源（同主语链），
                # 使"…出游65.22亿，…增加9.07亿，同比增长16.2%" 的 16.2 能沿链取到主体。
                _inher_subj, _inher_start = _b["subject"], _b["start"]

    # 归属前缀剥离（P4b 专项1）：净利润归属前缀（归母/归属于X股东的）是定语非主体，
    # 因"净利润"锚切分把前缀当主体（白酒 823.20 的"归母"、民生 127.42 的"归属于
    # 该行股东的"）。句子命中归属结构时，残词主体的 claim 继承**句首主导实体**（首个
    # 非空非归属残词主体，即主句公司/行业实体：春秋航空/国防军工/民生银行…）。用
    # 首个而非"最近"——"最近"会串到同指标域残主体（823 前紧邻 1147.55 的"利润总额"
    # 亦非实体，只是锚切残留）。仅当：
    #   1) 整句命中归属前缀结构（_ATTRIB_PREFIX_RE）；
    #   2) 该 claim 主体是归属残词（_is_attrib_residual）；
    #   3) 句首存在有效主体（首个非空、非归属残词主体），且与残词 claim ≤80 字符、同句。
    # 非归属 claim 与无残词主体一律不动（宁缺毋滥）。
    if _ATTRIB_PREFIX_RE.search(sent):
        _att_lead, _att_lead_start = "", -10**9
        for _b in bound:
            if not _b["subject"] or _is_attrib_residual(_b["subject"]):
                continue
            _att_lead, _att_lead_start = _b["subject"], _b["start"]
            break
        if _att_lead:
            for _b in bound:
                if not _b["subject"]:
                    continue
                if _is_attrib_residual(_b["subject"]) \
                        and 0 <= _b["start"] - _att_lead_start <= 120:
                    # 归属前缀(归母/归属于X股东的)语义上强锚定句首公司——即便隔分号
                    # （财报并列行"营收…；归母净利…"）仍是同一主体，故只禁跨句号/换行。
                    _gap = sent[_att_lead_start:_b["start"]]
                    if "。" not in _gap and "\n" not in _gap:
                        _b["subject"] = _att_lead


    # ---- P5(专项2)：并列多主体错位修复（F2 列表对位 → F1 裸承接）----
    # 形态两类（KNOWN 并列错位族 ×4 实测归因）：
    #   A. 枚举并列（句首顿号实体列表 + 并列标记"分别" + 等量值列表）：
    #      "保利发展、绿城中国的销售额为2530亿元和2519亿元，分别位居冠军和亚军"
    #      → 两值都归"绿城中国的"；"航天发展、ST新研、ST炼石上半年分别亏损
    #      4.25亿、1.77亿、1.01亿" → 归到最末主体/空。应**按序对位**：X1→v1、X2→v2。
    #   B. 逐主体承接（值所在分句 = "裸实体 + 变化动词/裸实体"直接跟值）：
    #      "手术机器人…368.1%，内窥镜增长31.9%，肾脏透析设备增长33%" → 31.9/33 都回退
    #      成前主体"手术机器人出口"（且 31.9 与 33 同键被 close 吞 33）。
    #      "火山引擎14.8%、华为云13.1%" → 都归第一分句"阿里云"。应取各值分句内的
    #      裸实体作主体。
    # 两机制都只改 subject、不动年份/指标/值，且要求候选实体与现有主体语义不同才改。
    # ---- F2：句首顿号列表 + 并列标记 + 值列表 按序对位 ----
    # 触发（全满足才尝试，宁缺毋滥）：
    #   1) 句内含"分别|依次"并列标记 或 值之间以顿号/和分隔；
    #   2) 第一个量化值之前存在由"、"连接的多实体列表段；
    #   3) 列表段在句中主句位置（到第一值的文本里可剥出 N≥2 实体）。
    if re.search(r"分别|依次", sent):
        _vfirst = min((_b["start"] for _b in bound), default=-1)
        if _vfirst > 0:
            _pre = sent[: _vfirst]
            # 找列表段：主句动词指标区（"销售额为/分别亏损/达/为…"）之前的连续列表
            _vm = re.search(
                r"(?:的)?(?:销售额|销量|销售|份额|营收|收入|出货|规模|装机)"
                r"(?:为|达)?$|"
                r"(?:分别|依次|共|合计)?(?:亏损|位居|排名|完成|实现|达|为|"
                r"降至|增至|达至|升至|下滑|下降|增长)\s*$",
                _pre)
            _seg = _pre[:_vm.start()] if _vm else _pre
            # 剥尾粘着（的/中国…的）与期词年份，再按顿号/和拆分
            _seg = re.sub(r"(?:截至)?20\d{2}年?(?:上半|下半)?|上半年|下半年|全年|当年$", "", _seg)
            _seg = _seg.rstrip(" ，,、；;的")
            _ents = [e.strip(" ，,、；;的等") for e in re.split(r"[、和与及]", _seg)
                     if len(e.strip(" ，,、；;的等")) >= 2]
            if len(_ents) >= 2:
                _vlist = [_b for _b in bound if _b["start"] < _vfirst + 200]
                if len(_vlist) == len(_ents):
                    for _e, _vb in zip(_ents, _vlist):
                        if not _vb["subject"] or _vb["subject"] != _e:
                            _vb["subject"] = _e
    # ---- F1：裸承接主语（值分句 = 裸实体[+变化动词] 或 裸实体直接跟值）----
    for _b in bound:
        _bs = 0
        for _sep in ("；", "。", "，", "、", ",", "；", ";", "\n", ":", "和", "与", "及"):
            _i = sent.rfind(_sep, 0, _b["start"])
            if _i > _bs:
                _bs = _i + 1
        _loc = sent[_bs:_b["start"]].strip(" ，,、；;：:")
        if not _loc or len(_loc) > 16:
            continue
        _cand = _clean_bare_subject(_loc)
        if not _cand:
            continue
        _cur = (_b["subject"] or "").strip()
        if _cur and (_cur in _cand or _cand in _cur):
            continue  # 现有主体已是该实体或其上下文，不动
        if _cur != _cand:
            _b["subject"] = _cand


    # 每 (指标, 主题, 口径年份) 聚合数值。主题并入键：并列句里同一指标但不同主体
    # （"X出货量…，Y出货量…"）不互相顶替。
    # 默认（_KEEP_DISTINCT_CLAUSE）同一键允许多个"实质不同"的数值并存：
    #   - 新值与已保留者 `_values_close` 为假（数值真不一样）→ 追加保留；
    #   - 数值近似（同说法/四舍五入口径噪声，如 12.0 亿 vs 12.1 亿）→ 只留
    #     距离指标词更近的那条，防"单源数值膨胀"。
    # 关闭开关则退回旧行为：同一键只保留离指标词最近的一条。
    best_by_ind = {}                # key -> [bound_record]
    for b in bound:
        key = (b["indicator"], b["subject"], b["year"])
        lst = best_by_ind.setdefault(key, [])
        placed = False
        if _KEEP_DISTINCT_CLAUSE:
            for i, cur in enumerate(lst):
                if _values_close(cur["value"], b["value"]):
                    # 变化端点豁免：b 若是被"提升至/增长至/降至/降到…"引导的终点
                    # （份额45.8%→47.5%、占比38%→36%），它与已保留的起点是同指标
                    # 的**两个真实观测点**，而非同口径舍入噪声(12.0亿vs12.1亿)。
                    # 是终点则跳过 close 合并，走末尾 append 保留；否则仍按噪声去重。
                    if _is_change_endpoint(sent, b["start"]):
                        continue
                    # 并列列举豁免：b 若被并列连词（和/与/及/分别…）引导
                    # （"同比分别增长10.4%和9.4%"的 9.4、"WTI 71.99 和 布伦特 75.53"），
                    # 是**不同对象各自的值**，也不是舍入噪声，须整组保留。
                    if _is_parallel_value(sent, b["start"]):
                        continue
                    # 实质同值(舍入噪声)：保留距离指标词更近的一条（dist 越小越贴指标）
                    cur_d = cur["dist"] if cur["dist"] is not None else float("inf")
                    new_d = b["dist"] if b["dist"] is not None else float("inf")
                    if new_d < cur_d:
                        lst[i] = b
                    placed = True
                    break
            if not placed:
                lst.append(b)
        else:
            cur = lst[0] if lst else None
            if cur is None:
                lst.append(b)
            else:
                cur_d = cur["dist"] if cur["dist"] is not None else float("inf")
                new_d = b["dist"] if b["dist"] is not None else float("inf")
                if new_d < cur_d:
                    lst[0] = b

    # 展平并施加单句条数上限保护（防空句/病态长句爆炸）
    flat = [r for lst in best_by_ind.values() for r in lst]
    flat.sort(key=lambda r: r["start"])
    flat = flat[:_CLAUSE_MAX_CLAIMS]
    return [{
        "claim": sent[:120],
        "value": r["value"],
        "indicator": r["indicator"],
        "year": r["year"],
        "subject": _normalize_subject(r["subject"]),
        "url_key": url_key,
    } for r in flat]


def _extract_claims(doc, *, dedup_by_url: bool = False) -> List[dict]:
    """从正文抽取'含数字的量化主张'句段。
    返回 [{claim, value, indicator, subject, url_key}]，其中：
    - value 是主张对应的数值；
    - indicator 是与该数值**就近配对**的指标词；
    - subject 是主张的"主题词"（如"全球AI市场"→"AI市场"）；
    - url_key 是本条主张的**来源计数身份**（供来源级去重/计数）。

    注意：当 `_assign_origins` 已给 doc 写好 doc.origin_id 时（转载/镜像已折叠），
    url_key 取该 origin_id —— 同正文的镜像转载在后续"来源计数"里只算一次，从根源
    杜绝"一堆低可信度镜像被当成多个独立来源刷 high"。否则回退到 raw URL 指纹。

    一句话若含多个"指标-数值"对，会产出多条主张，不再只取第一个数值。

    当 dedup_by_url=True 时，对同一来源中**重复出现同一主张**的句子做去重
    （只保留第一条），从根源避免把"一句话被正文重复多次"误当多来源。
    """
    claims = []
    if not doc.content:
        return claims
    # 计数身份：优先用已折叠的 origin_id；未折叠（独立入口）则回退 raw URL 指纹。
    source_key = getattr(doc, "origin_id", None) or _url_key(getattr(doc, "url", ""))
    sentences = re.split(r"(?<=[。！？!?；;])|\n", doc.content)
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 8:
            continue
        # 过长句仅作上限保护（防病态输入），上限放宽到 2000；
        # 真实长句里的量化主张应被抽到，而不是整句静默丢弃。
        if len(sent) > 2000:
            # 对大段无标点文本做粗略切分，避免整段漏抽
            for piece in (sent[i:i + 400] for i in range(0, len(sent), 400)):
                claims.extend(_sentence_claims(piece, source_key))
            continue
        claims.extend(_sentence_claims(sent, source_key))

    if dedup_by_url:
        # 同一来源对"同一 (subject, indicator, 口径年份, 实质取值)"只保留一条，
        # 避免同源重复句子被误计为多个独立来源。
        unique = []
        seen = set()
        for c in claims:
            sig = (c["subject"], c["indicator"], c["year"], c["value"])
            if sig in seen:
                continue
            seen.add(sig)
            unique.append(c)
        claims = unique
    return claims


# schema v2（聚合层，抽取冻结）：相对值 claim 的侧信道锚。
# 背景：E1 后相对值（增长率/跌幅/涨幅）year 一律 None（口径正确），但跨源合并时
# "2025 收入同比+10%"与"2026 收入同比+8%"会进同一 None 桶，被 _values_close 判成
# 分歧或（值接近时）互相背书——实为两年不同增长率。锚字段从 claim 原文解析报告期/
# 基期年，仅用于聚合层跨源比较，**不触碰 _sentence_claims 的 year 口径**。
_REL_BUCKET = {"增长率", "跌幅", "涨幅"}


def _resolve_anchor(sentence: str):
    """解析相对值 claim 的 {report_year, base_year}；无任何显式年份 → None。

    确定性最小集（不做深度推理，宁缺毋滥）：
    - base_year：'较/比/相较于/相比 X年' 显式基期；
    - report_year：句内首个显式年份（通常为句首报告年）；
    - 报告句含'同比'且无显式基期锚 → base_year = report_year - 1（同比语义）。
    """
    if not sentence:
        return None
    m_base = re.search(r"(?:较|比|相较于|相比)\s*(20\d{2})年", sentence)
    base = int(m_base.group(1)) if m_base else None
    years = [int(y) for y in re.findall(r"(?:^|[^\d])(20\d{2})\s*(?:财)?年", sentence)]
    report = years[0] if years else None
    if report is None and base is None:
        return None
    if base is None and report is not None and re.search(r"同比", sentence):
        base = report - 1
    return {"report_year": report, "base_year": base}


def _anchor_compatible(a, b) -> bool:
    """锚一致门：两相对值 claim 仅当锚相同（或均无锚=现状）才可同簇互相背书。

    锚不同 → 分开呈现、不判冲突也不互相抬升置信（schema v2 拍板语义）。
    """
    if a is None and b is None:
        return True
    return a is not None and a == b


# schema v2 第二阶段：聚合层实体键归一（抽取层 subject 原样保留，只清洗残词）。
# 真实样本（白酒3文 83 subject）驱动：尾部动词/归属残（'五粮液实现'/'水井坊归母'/
# '五粮液实现归母'）剥尾得公司名；句首引导噪声（'记者梳理'/'国家统计局数据显示'/
# '在'/'余'）空化；正确实体（'定价'/'白酒'/'金徽酒'/'实际产能'/'产能利用率'等）
# 不受影响。**不做互含合并**（'白酒上市公司' vs '20家A股白酒上市公司' 语义可能不同，
# 宁保留碎片也不误并）。
_SUBJ_TAIL_VERBS = (
    "实现归母", "归母", "实现", "超过", "突破", "达到", "达",
    "降至", "增至", "录得", "创下",
)
_SUBJ_HEAD_NOISE = (
    "记者梳理", "国家统计局数据显示", "数据显示", "报告显示", "数据来源",
    "展望", "预计", "截至", "进入", "其中", "从", "受", "在", "据", "随",
    "较上年", "同比",
)


def _norm_evidence_subject(subj: str) -> str:
    """聚合层证据实体键：剥尾动词/归属残 + 剥句首引导噪声 + 噪声键空化。

    保守设计（宁缺毋滥）：只清确定性残词；清洗后为空/单字/无中文 → ""（无实体键，
    防 '在'/'余'/'19家' 这类计数假值污染桶）。
    """
    s = (subj or "").strip().strip(" ，,、:：的")
    if not s:
        return ""
    for _ in range(4):
        t = s
        for w in _SUBJ_HEAD_NOISE:
            # >=:整词即噪声('国家统计局数据显示')也剥除→空化;留s非空继续
            if s.startswith(w) and len(s) >= len(w):
                s = s[len(w):].lstrip(" ，,、")
                break
        for w in _SUBJ_TAIL_VERBS:
            if s.endswith(w) and len(s) > len(w):
                s = s[:-len(w)].rstrip(" ，,、")
                break
        if s == t:
            break
    s = s.strip(" ，,、:：的")
    if len(s) <= 1 or not re.search(r"[\u4e00-\u9fffA-Za-z]", s):
        return ""
    return s


# schema v2 第三阶段：量纲类门 + 修正识别 + 裁决 kind（聚合层）。
# 真实 case：'10万吨(钢铁)' vs '1706.86亿元' 在空 subject 桶里互报冲突——_values_close
# 只比数值不看单位。按拍板加量纲类门（同量纲才可 close 并簇）；'部/台/辆'同属计数
# 类不拆合法互证（避免 2.5万台 vs 2.5万部 被拆）。
_REVISION_WORDS = ("上修至", "上修到", "上调至", "上调到", "修正为", "更正为",
                   "调整为", "更新为")


def _dim_class(value: str) -> str:
    s = (value or "").strip()
    if not s:
        return "count"
    if "%" in s or "％" in s:
        return "pct"
    if re.search(r"元|美元|港元|人民币|日元|欧元", s):
        return "money"
    if re.search(r"吨|千克|克|斤|担", s):
        return "weight"
    if re.search(r"Wh|千瓦时|度电|立方米|万方|亿方|升", s):
        return "energy"
    return "count"          # 台/辆/部/人次/个/家/套… 计数(不拆合法互证)


def _same_dimension(v1: str, v2: str) -> bool:
    return _dim_class(v1) == _dim_class(v2)


def _has_revision(sentence: str) -> bool:
    return bool(sentence) and any(w in sentence for w in _REVISION_WORDS)


def _evidence_merge_ok(cl, cluster) -> bool:
    """聚合层并簇四门：值 close + 量纲类同 + 锚一致 + None 锚相对值不互抬。

    - 水平量：量纲同 + close 即可（现状+量纲门，'10万吨' vs '1706.86亿元'不同类不并）；
    - 相对值：锚不同不并（2025同比 vs 2026同比）；None 锚（比较期未知）不互抬，
      各自单源，防 '50% vs 11.32%' 假冲突（议题3 拍板）。
    """
    if not _values_close(cluster["value"], cl["value"]):
        return False
    if not _same_dimension(cluster["value"], cl["value"]):
        return False
    a, b = cluster.get("anchor"), cl.get("anchor")
    if cl.get("is_rel"):
        if a is None or b is None:
            return False
        return a == b
    return _anchor_compatible(a, b)


def _claims_to_buckets(docs) -> List[dict]:
    """把一组文档的量化主张整理成'最终聚类桶'，统一供冲突检测与证据计分复用。

    返回 [ {subject, indicator, year, claims:[...]} ]，其中 claims 已做过：
    - 同 (subject, indicator) 维度内按**口径年份**区分：显式年份不同则分开成桶，
      避免把"2024 实际规模"与"2030 预测规模"误当成分歧；
    - **无年份主张归并到同指标下、来源最多那个显式年份桶**（无年份通常指当前/
      发布当年），若全桶都无年份则归入 None 桶；
    - 每个 url_key 只保留一条最可信/最新记录，防同 URL 重复抓取被计为多来源。

    说明：跨年份的数值不是"冲突"，分桶后各自独立计分，不再互相拉低置信。
    """
    # 先把文档折叠成"独立编辑来源原点"(origin)：同一正文被多站转载/镜像 → 只算一份。
    # 该步会写回 doc.origin_id / origin_kind / origin_rep，_extract_claims 因此用
    # origin_id 作为计数身份，于是镜像在后续 by_url 去重与计数里自然合并。
    _assign_origins(docs)
    # 第一遍：按 (subject, indicator) 粗聚，记录每条的年份
    raw = {}                       # (subject, indicator) -> [record]
    for d in docs:
        _ensure_credibility(d)
        for c in _extract_claims(d, dedup_by_url=True):
            _subj_norm = _norm_evidence_subject(c["subject"])
            key = (_subj_norm, c["indicator"])
            raw.setdefault(key, []).append({
                "doc_title": getattr(d, "title", "")[:60],
                "url": getattr(d, "url", ""),
                "url_key": c["url_key"],
                "origin_id": getattr(d, "origin_id", None) or c["url_key"],
                "origin_kind": getattr(d, "origin_kind", ""),
                "credibility": getattr(d, "credibility", 0.5),
                "date": _extract_date(d),
                "value": c["value"],
                "sentence": c["claim"],
                "indicator": c["indicator"],
                "subject": _subj_norm,
                "subject_raw": c["subject"],
                "year": c["year"],
                # schema v2: 相对值附侧信道锚(聚合层比较用,不碰year口径)
                "anchor": (_resolve_anchor(c["claim"])
                           if c["indicator"] in _REL_BUCKET else None),
                "is_rel": c["indicator"] in _REL_BUCKET,
                "revision": _has_revision(c["claim"]),
            })

    buckets = []
    for (subject, indicator), recs in raw.items():
        # 按来源(origin)去重：同 origin 保留可信度最高 / 日期更新的。
        # 镜像转载因共享同一 origin_id，在这里被折叠为一条，避免"多站转载刷来源数"。
        by_url = {}
        for r in recs:
            prev = by_url.get(r["url_key"])
            if prev is None or _compare_claim_key(r) > _compare_claim_key(prev):
                by_url[r["url_key"]] = r
        recs = list(by_url.values())

        # 按年份分桶（显式年份）；None 先单独放
        by_year = {}
        none_recs = []
        for r in recs:
            y = r["year"]
            if y is None:
                none_recs.append(r)
            else:
                by_year.setdefault(y, []).append(r)
        if not by_year:
            # 全部无年份 → 单桶
            buckets.append({"subject": subject, "indicator": indicator,
                            "year": None, "claims": recs})
            continue
        # 无年份主张归并到"来源最多"的显式年份桶（通常为发布当年/当前口径）
        biggest_year = max(by_year, key=lambda y: len(by_year[y]))
        by_year[biggest_year].extend(none_recs)
        for y, claims in by_year.items():
            buckets.append({"subject": subject, "indicator": indicator,
                            "year": y, "claims": claims})
    return buckets


def detect_conflicts(docs: List, min_conflict_value: int = 2) -> List[dict]:
    """检测跨来源的数值主张冲突。

    策略（轻量、不引入重 NER）：
    - 对每篇文档抽取含数字的量化主张，并识别其指标词（市场份额/规模/增速等）；
    - 按"指标词 + 口径年份"聚类：同一指标、同一口径年份下多个来源给出**不同数值**，
      才视为潜在冲突；跨年份数值（2024 实际 vs 2030 预测）不算冲突，各自独立；
    - 对每个冲突点，汇总各家说法、可信度、日期，供 Writer 加权裁决。

    返回 [{"topic", "claims": [...]}]
    """
    conflicts = []
    for b in _claims_to_buckets(docs):
        claims = b["claims"]
        if len(claims) < min_conflict_value:
            continue
        # 同一指标、同一口径年份下，只有出现**实质分歧**的取值才算冲突。
        # "实质分歧"定义与证据计分里的簇划分一致（_values_close）：像 12.0 与 12.1
        # 这类四舍五入/计量噪声差异不算冲突（它们本就是同一条说法被证实）；
        # 只有落入**不同取值簇**、彼此无法用口径噪声解释的说法才构成冲突。
        # 这样冲突检测与 extract_evidence 的"high=多源一致"判定口径统一，避免同一
        # 条证据既被标成 high 又被报成冲突的自相矛盾。
        clusters = []                       # [{value, claims:[...]}]，近似值归同一簇
        for cl in sorted(claims, key=lambda x: -x["credibility"]):
            placed = False
            for cluster in clusters:
                if _evidence_merge_ok(cl, cluster):
                    cluster["claims"].append(cl)
                    if cl.get("revision"):
                        cluster["revision"] = True
                    placed = True
                    break
            if not placed:
                clusters.append({"value": cl["value"], "claims": [cl],
                                 "anchor": cl.get("anchor"),
                                 "revision": cl.get("revision", False)})
        if all(cl.get("is_rel") and cl.get("anchor") is None for cl in claims):
            continue                        # 全 None 锚相对值:比较期未知,非同题冲突
        if len(clusters) < 2:
            continue                        # 所有来源实质一致 → 无冲突
        values = [c["value"] for c in clusters]
        best = max(clusters, key=lambda c: len(c["claims"]))
        # 主导说法取簇内最高可信度来源作为代表值描述
        rep = max(best["claims"], key=lambda c: c["credibility"])
        base = f"「{b['subject']}」的{b['indicator']}" if b["subject"] else f"「{b['indicator']}」"
        if b["year"]:
            base = f"{base}（{b['year']}年）"
        others = "、".join(v for v in values if v != best["value"])
        conflicts.append({
            "topic": f"关于{base}存在分歧说法（{best['value']} vs {others}）",
            "claims": claims,
        })
        if len(conflicts) >= 8:  # 上限，避免撑爆上下文
            break
    return conflicts


def summarize_conflicts(conflicts: List[dict]) -> str:
    """把冲突点压缩成喂给 Writer 的裁决指令文本。"""
    if not conflicts:
        return ""
    lines = [
        "【注意】以下信息在多个来源间存在**冲突/分歧**，请按规则裁决后再写入报告："
        "\n裁决规则：优先采信「可信度高 + 更近」的来源；仍分歧则明示并标注【存疑】。"
    ]
    for i, c in enumerate(conflicts, 1):
        lines.append(f"\n冲突{i}: {c['topic']}")
        for cl in sorted(c["claims"], key=lambda x: -x["credibility"]):
            lines.append(
                f"  - 说「{cl['value']}」来源「{cl['doc_title']}」"
                f"(可信度{cl['credibility']}，日期{cl['date'] or '未知'})"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 确定性置信度计分（替代"LLM 凭感觉标"，改为可复现的规则计算）
# ---------------------------------------------------------------------------

# 单位系数：把 "2000亿美元/2000 亿美元/2000亿" 统一归一化到"亿"为基准的数字。
# 覆盖复合货币单位："1.1万亿美元" 与 "1.1万亿" 在"亿"基准下都是 11000。
_UNIT_MULT = {
    "万亿": 10000, "千亿": 1000, "百亿": 100, "十亿": 10, "亿": 1,
    "亿元": 1, "亿美元": 1, "万亿元": 10000, "万亿美元": 10000,
    "万": 0.0001, "万元": 0.0001, "万美元": 0.0001,
    "%": None, "％": None,  # 百分比不归一（比较时特殊处理）
}


def _value_to_number(value_str: str):
    """把 '2000亿美元' / '38%' / '50万' / '1500万部' / '80GWh' 解析成可比较数值。

    返回 (num, is_percent)。解析失败返回 None。
    规则：
    - 金额/量级复合单位归一到"亿"基准（亿美元/亿元/万/万亿…）；
    - 允许量级词后跟计数名词（"1500万部/2.5万台/80万套"）：计数名词不改变量级，
      因此 '1500万部' 与 '1500万' 数值可比；
    - GWh/MWh 等无量级单位按原值（1 倍）比较。
    """
    s = value_str.replace(" ", "").strip()
    if not s:
        return None
    m = re.match(r"(\d+(?:\.\d+)?)\s*(%|％)?\s*(.*)", s)
    if not m:
        return None
    num = float(m.group(1))
    is_percent = bool(m.group(2))
    unit = m.group(3) or ""
    # 取 unit 中最长的"量级词"前缀（亿美元 > 亿 > 万…），剩余部分为计数名词(忽略)
    mult = 1.0
    for k in sorted(_UNIT_MULT, key=len, reverse=True):
        if k == "%" or k == "％":
            continue
        if unit.startswith(k):
            v = _UNIT_MULT[k]
            mult = 1.0 if v is None else v
            break
    return (num * mult, is_percent)


def _values_close(v1: str, v2: str, rel_tol: float = 0.15) -> bool:
    """判断两个取值字符串是否'实质接近'（容忍 ±15% 的口径差异）。

    参考基取两者**较小**的那个（对称、与放入顺序无关），使判定更严格也更稳定：
    - 12.0 亿 vs 12.1 亿（四舍五入/计量噪声）→ 0.8% 差异 → 视为同一条说法；
    - 3000 亿 vs 3500 亿（真实口径分歧）→ 以较小者计 16.7% 差异 → 不算同一说法，
      避免把实质分歧静默合并成"高置信"。该口径与 detect_conflicts 的分歧判定一致。
    """
    n1 = _value_to_number(v1)
    n2 = _value_to_number(v2)
    if not n1 or not n2:
        return v1 == v2
    # 百分比不与非百分比比较
    if n1[1] != n2[1]:
        return False
    a, b = n1[0], n2[0]
    if b == 0 and a == 0:
        return True
    base = min(abs(a), abs(b))
    if base == 0:
        return a == b
    return abs(a - b) <= rel_tol * base


def _freshest_date(claims: List[dict]) -> str:
    """返回一组主张中最新的来源日期（YYYY-MM），无有效日期返回 ''。"""
    best = ""
    for c in claims:
        d = c.get("date") or ""
        if d and d > best:
            best = d
    return best


def _compare_claim_key(c):
    """给单条主张排优先级用的 key：越可信越好，其次越新越好。

    供'同一来源多条候选'、以及冲突裁决时排序复用。
    """
    return (c["credibility"], c.get("date") or "")


def extract_evidence(docs: List, max_evidence: int = 12) -> List[dict]:
    """把多来源量化主张聚合成'证据项'，并给出**确定性**置信度与裁决。

    设计要点（关键防呆）：
    - **来源级计数**：真正的"交叉验证"以**不同 URL** 为准，而非句子/主张条数。
      同一 URL 被重复抓取、或同一文档内重复出现同一说法，都不会被计成多个来源，
      否则"单一来源"会被错误抬升为 high 置信。
    - 计数逻辑：
      * n_total_source = 该指标下**不同来源 URL** 的数量；
      * 簇的"支持来源数" = 该簇内**不同来源 URL** 的数量；
      * 只有多来源才可能 high；单一来源恒为 low。

    返回每个证据项：
    {
      subject, indicator, label,
      n_sources,                   # 不同来源总数
      claims: [ {doc_title,url,url_key,credibility,date,value,value_num,sentence} ],
      clusters: [ {value, claims:[...]} ],   # 按实质接近归组后的簇
      adopted: {value, credibility, n_sources, sources:[...]},  # 裁决后采纳的说法
      level: 'high'|'medium'|'low',   # 确定性置信等级
      score: 0~1,                     # 可复现的分数
      reason: str                     # 给 Writer/展示用的裁决说明
    }
    """
    evidences = []
    for b in _claims_to_buckets(docs):
        subject, indicator, year = b["subject"], b["indicator"], b["year"]
        claims = b["claims"]      # 已按 url 去重、按口径年份分桶

        # ---- 按"实质接近"的取值聚簇。
        clusters = []  # [{value, claims:[...]}]；每个来源只允许进入一个簇
        placed_urls = set()
        for cl in sorted(claims, key=lambda x: -x["credibility"]):
            placed = False
            for cluster in clusters:
                if (cl["url_key"] not in placed_urls
                        and _evidence_merge_ok(cl, cluster)):
                    cluster["claims"].append(cl)
                    if cl.get("revision"):
                        cluster["revision"] = True
                    placed_urls.add(cl["url_key"])
                    placed = True
                    break
            if not placed:
                clusters.append({"value": cl["value"], "claims": [cl],
                                 "anchor": cl.get("anchor"),
                                 "revision": cl.get("revision", False)})
                placed_urls.add(cl["url_key"])

        clusters.sort(key=lambda c: len(c["claims"]), reverse=True)
        # 主导说法 = 支持来源最多的簇；次之取最高可信度；再平局取来源更新者（时效优先）
        best_cluster = clusters[0]
        for cluster in clusters:
            if len(cluster["claims"]) > len(best_cluster["claims"]):
                best_cluster = cluster
            elif len(cluster["claims"]) == len(best_cluster["claims"]):
                bmax = max(x["credibility"] for x in best_cluster["claims"])
                cmax = max(x["credibility"] for x in cluster["claims"])
                if cmax > bmax:
                    best_cluster = cluster
                elif cmax == bmax:
                    # 可信度相同时，采信"数据更新"的说法簇（时效优先）
                    if _freshest_date(cluster["claims"]) > _freshest_date(best_cluster["claims"]):
                        best_cluster = cluster
        # schema v2 议题4: 修正优先——含"上修至/修正为"的簇=官方最新披露,
        # 覆盖支持数裁决(采纳最新不报冲突)。
        for cluster in clusters:
            if cluster.get("revision"):
                best_cluster = cluster
                break

        n_cluster = len(clusters)                      # 有几个分歧簇
        n_support = len(best_cluster["claims"])        # 主导说法：不同来源数
        top_cred = max(x["credibility"] for x in best_cluster["claims"])
        n_total = len(claims)                          # 不同来源总数

        # ---- 确定性连续计分（2026 工程评审重构；严格区分"来源数"与"句子数"）----
        # ① 数量不碾压质量：high 要求 主导源可信 tc≥0.6(≥3源) 或 tc≥0.85(2源)，
        #    3 个互相独立但都低可信的来源不再能冲到 high（旧 0.5+0.3n+0.2tc 可到 0.95）；
        # ② 单一连续函数 score=f(可信度,覆盖度,主导占比)，消除旧分档的边界倒挂，
        #    level 只做显示映射；
        # ③ 分歧簇(n_cluster>1) level 封顶 medium/low——有分歧不配 high（保守）；
        # ④ 单一来源恒 low、无年份相对值不互证——保留（防"一家之言/假背书"）。
        score, level, reason = 0.0, "low", ""
        _no_anchor_rel = all(cl.get("is_rel") and cl.get("anchor") is None
                             for cl in claims)
        if _no_anchor_rel:
            # 议题3:全 None 锚相对值——各源比较期未知,不互证不判冲突(分别呈现)
            score = 0.2 + top_cred * 0.15
            level = "low"
            reason = "无年份增长率各源比较期未知,不互证(分别呈现)"
        elif n_total == 1:
            # 单一来源（含同源折叠后）恒 low——最可信的源没旁证也不给高
            score = 0.2 + top_cred * 0.15
            level = "low"
            reason = "仅单一来源，缺乏交叉验证"
        elif n_cluster > 1:
            # 有分歧：分数由主导占比+可信度连续给出；level 按裁决映射
            agree = n_support / max(n_total, 1)
            score = round(0.22 + 0.30 * agree + 0.25 * top_cred, 3)
            if n_support == 1 and top_cred >= 0.85:
                level = "medium"
                reason = (f"关于{best_cluster['value']}，虽为单一高可信来源"
                          f"（{top_cred:.2f}），但与其他 {n_total - 1} 个来源说法分歧")
            elif n_support >= 2:
                level = "medium"
                reason = (f"存在 {n_cluster} 种说法，支持{best_cluster['value']}"
                          f"的来源最多（{n_support} 个），但未完全一致")
            else:
                level = "low"
                reason = f"关于该指标存在 {n_cluster} 种分歧说法且无主导来源"
        else:
            # 多源一致：连续函数（可信度 0.44 权重 + 覆盖度 0.26，数量有界不碾压）
            coverage = 1 - 1 / (n_support + 1)     # 1源.5 / 2源.667 / 3源.75 / 10源.91
            raw = 0.34 + 0.44 * top_cred + 0.26 * coverage
            score = min(0.95, round(raw, 3))
            if (n_support >= 3 and top_cred >= 0.6) \
                    or (n_support == 2 and top_cred >= 0.85):
                level = "high"                     # 质量门槛：数量不碾压质量
            elif score >= 0.55:
                level = "medium"
            reason = (f"{n_support} 个独立来源一致为{best_cluster['value']}"
                      f"（最高可信 {top_cred:.2f}），各源口径一致")

        # 给 reason 补一条时效说明（若主导说法有明确更新日期）
        freshest = _freshest_date(best_cluster["claims"])
        if freshest:
            reason = reason.rstrip() + f"；数据较新（{freshest}）"

        adopted = {
            "value": best_cluster["value"],
            "credibility": top_cred,
            "n_sources": n_support,
            "freshest": freshest,
            "sources": best_cluster["claims"],
        }
        # schema v2 议题5: 裁决 kind 结构化(区分一致/单源/修正/键失真/真分歧)。
        if n_cluster == 1:
            kind = "一致" if n_support >= 2 else "单源"
        else:
            if _no_anchor_rel:
                kind = "分开呈现"
            elif best_cluster.get("revision"):
                kind = "修正"
            elif len({_dim_class(c_["value"]) for c_ in clusters}) > 1:
                kind = "键失真"      # 量纲各异=空/宽subject异物同桶残留,非真分歧
            elif n_support >= 2:
                kind = "分歧-真冲突"
            else:
                kind = "分歧-无主导"
        verdict = {"kind": kind, "detail": reason}
        label = f"「{subject}」的{indicator}" if subject else f"「{indicator}」"
        if year:
            label = f"{label}（{year}年）"
        evidences.append({
            "subject": subject,
            "indicator": indicator,
            "year": year,
            "label": label,
            "n_sources": n_total,
            "claims": claims,
            "clusters": clusters,
            "adopted": adopted,
            "level": level,
            "score": round(score, 2),
            "reason": reason,
            "verdict": verdict,
        })
        if max_evidence is not None and len(evidences) >= max_evidence:
            break
    return evidences


def origin_groups_debug(docs: List) -> List[dict]:
    """观测/调试：返回该组文档折叠成"独立来源原点"的明细。

    每项含：origin_id、kind(same_url=同链接多写法 / mirror=跨站转载·已折叠)、
    折叠成员数 n_docs、代表来源 representative，以及原始成员 URL 列表 urls。
    供人工排查"为何某条证据来源数比文档数少"——多半是转载/镜像被正确折叠。
    """
    if not _ORIGIN_MERGE_ENABLED:
        return []
    groups = _assign_origins(list(docs))
    out = []
    for oid, m in groups.items():
        rep = m["representative"]
        members = m["members"]
        if m["kind"] == "mirror":
            by_url = sorted({getattr(x, "url", "") for x in members})
            desc = f"跨站转载：{m['n_docs']} 份正文折叠为 1 个独立来源"
        else:
            by_url = list(m["urls"])
            desc = f"同链接多写法：{m['n_docs']} 条记录归并为 1 个来源"
        out.append({
            "origin_id": oid,
            "kind": m["kind"],
            "n_docs": m["n_docs"],
            "representative_url": getattr(rep, "url", ""),
            "representative_cred": round(getattr(rep, "credibility", 0.0), 2),
            "urls": sorted(by_url)[:5],
            "desc": desc,
        })
    out.sort(key=lambda g: -g["n_docs"])
    return out


def evidence_debug(docs: List, evidences: List[dict]) -> dict:
    """观测/调试：把"来源折叠"与"每条证据的来源计数"对齐，便于排查。

    返回 { groups: [...], evidences: [{label, level, n_origin(去重后), n_docs(原始),
    collapsed(被折叠掉的转载份数), urls:[...]} ] }。
    collapsed = 原始供稿该指标的源文档数 - 去重后计入的 origin 数，>0 即发生了
    转载/镜像折叠（正是"虚假独立来源"被拦截之处）。
    """
    # 先用当前 docs 重新折叠一次，确保拿到最新的 origin 归属（幂等）
    groups = _assign_origins(list(docs))
    # origin_id -> 该 origin 的原始成员数（含跨 bucket，仅作计数参考）
    member_count = {oid: len(m["members"]) for oid, m in groups.items()}
    ev_dbg = []
    for ev in evidences:
        claims = ev.get("claims", [])
        seen_origins = set()
        urls = []
        for c in claims:
            oid = c.get("origin_id") or c.get("url_key")
            seen_origins.add(oid)
            if c.get("url") not in urls:
                urls.append(c["url"])
        n_origin = len(seen_origins)
        # 原始供稿该指标的有效源文档数 ≈ 每个已去重 claim 背后实际 member 之和
        n_docs_raw = 0
        for c in claims:
            oid = c.get("origin_id") or c.get("url_key")
            n_docs_raw += member_count.get(oid, 1)
        ev_dbg.append({
            "label": ev.get("label", ""),
            "level": ev.get("level", ""),
            "score": ev.get("score", 0),
            "n_origin": n_origin,          # 参与计分的独立来源数（即 n_sources 口径）
            "n_docs_raw": n_docs_raw,      # 原始被折叠前的有效源文档数
            "collapsed": max(0, n_docs_raw - n_origin),
            "urls": urls,
        })
    return {"groups": groups_debug_meta(groups), "evidences": ev_dbg}


def groups_debug_meta(groups: dict) -> List[dict]:
    """把 _assign_origins 的返回收敛成便于观测的紧凑列表。"""
    out = []
    for oid, m in groups.items():
        rep = m["representative"]
        out.append({
            "origin_id": oid,
            "kind": m["kind"],
            "n_docs": m["n_docs"],
            "representative_url": getattr(rep, "url", ""),
            "member_urls": sorted(m["urls"]),
        })
    out.sort(key=lambda g: -g["n_docs"])
    return out





def _evidence_level_label(level: str) -> str:
    return {"high": "高置信", "medium": "中置信", "low": "存疑"}.get(level, "存疑")


def summarize_evidence(evidences: List[dict]) -> str:
    """把确定性证据项压缩成喂给 Writer 的文本（含裁决后的取值与置信）。"""
    if not evidences:
        return ""
    lines = [
        "以下是**代码已确定性计分**的关键数据结论，请直接引用其『裁决值』并按『等级』标注，"
        "不要改动置信等级、不要改数值："
    ]
    for i, ev in enumerate(evidences, 1):
        lines.append(
            f"- {ev['label']}：裁决值={ev['adopted']['value']}"
            f"｜置信等级={_evidence_level_label(ev['level'])}"
            f"｜分数={ev['score']}｜依据={ev['reason']}"
        )
    return "\n".join(lines)
