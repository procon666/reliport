# -*- coding: utf-8 -*-
"""领域知识层（单一事实源）：集中定义所有"行业词表"——机构 / 地区 / 英文关键词。

动机（2026-09 多主题压测暴露的共性问题）：
    同一类领域知识曾分散在多个文件**重复定义**，导致"改一处漏一处"：
      - 机构词表散在 5 处（product_pipeline._ORG_SPOT_RE / decision_layer._ORG_RE /
        _FALLBACK_ORGS / _ORG_DOMAIN / _AUTHORITY_DOMAINS）——补行业机构时补了
        _ORG_SPOT_RE 却忘了 _ORG_RE，直到游戏主题真对撞被误剔才暴露；
      - 地区正则有两份完全相同的副本（ai_alignment_demo / render_report）。
    本模块作为**单一事实源**：所有词表在此定义，各模块 import 引用（用别名保持
    调用处零改动）。**新增一个行业机构只需在这里加一行，全链路自动生效。**

分工不变：LLM 只做语义；规则负责数值与确定性判定；本模块只放"规则用的词表"。
"""
import re as _re

# ============ 1. 机构 ============
# 具体机构名（跨行业）—— 识别正则、拍板点判定、域名映射都由它派生。
ORG_NAMES = (
    # 科技 / 电子 / 半导体
    "IDC", "IDC数据", "Canalys", "Counterpoint Research", "Counterpoint",
    "Omdia", "Gartner", "TrendForce", "Strategy Analytics", "CINNO", "GFK", "GfK",
    # 官方 / 协会（跨行业）
    "中国信通院", "信通院", "国家统计局", "统计局", "工信部", "药监局", "海关",
    "中汽协产销", "乘联会批发", "乘联会零售", "中汽协", "乘联会",
    "中国光伏行业协会", "CPIA", "中国酒业协会", "中国音数协",
    "中国畜牧业协会宠物产业分会", "中国宠物行业协会",
    # 咨询 / 研究（跨行业）
    "艾媒咨询", "艾媒", "沙利文", "Frost & Sullivan", "弗若斯特",
    "中商产业研究院", "中商产业", "中商情报网", "前瞻产业研究院", "前瞻",
    "欧睿", "Euromonitor", "凯度", "Kantar", "尼尔森", "Nielsen",
    "麦肯锡", "贝恩", "毕马威", "Statista", "Mordor Intelligence",
    "FutureMarketInsights", "消费者报道",
    # 游戏 / 数字娱乐
    "Niko Partners", "Newzoo", "伽马数据", "CADPA",
    # 消费 / 零售 / 餐饮
    "窄门餐眼", "窄门", "CBNData", "辰智", "NCBD", "勤策", "红餐", "久谦",
    "魔镜", "蝉妈妈", "星图数据",
    # 家电
    "奥维云网", "AVC", "中怡康", "产业在线",
    # 光伏 / 新能源
    "InfoLink", "SolarZoom", "SMM", "Enerdata", "PVInfoLink",
    # 医疗
    "医械数据云",
)

# 泛机构词（无具体名，但也表示"有机构口径"）
ORG_GENERIC_WORDS = ("协会", "研究院", "机构估算", "机构预测")


def _build_alt(names):
    """构造正则分支：按长度降序，避免"信通院"截断"中国信通院"。"""
    pats = sorted({n for n in names if n}, key=len, reverse=True)
    return "|".join(_re.escape(p) for p in pats)


# 机构识别 / 判定正则（具体名 + 泛词）
ORG_RE = _re.compile(_build_alt(ORG_NAMES) + "|" + _build_alt(ORG_GENERIC_WORDS))

# 机构 → 官网域名（定向抓一手稿）
ORG_DOMAIN = {
    "IDC": "idc.com", "IDC数据": "idc.com",
    "Canalys": "canalys.com",
    "Counterpoint": "counterpointresearch.com",
    "Counterpoint Research": "counterpointresearch.com",
    "Omdia": "omdia.com", "Gartner": "gartner.com",
    "TrendForce": "trendforce.com",
    "Strategy Analytics": "strategyanalytics.com",
    "CINNO": "cinno.com",
    "中国信通院": "caict.ac.cn", "信通院": "caict.ac.cn",
    "中汽协": "caam.org.cn", "乘联会": "cpcaauto.com",
    "艾媒咨询": "iimedia.cn", "艾媒": "iimedia.cn",
    "沙利文": "frostchina.com",
    "中国光伏行业协会": "chinapv.org.cn",
    "中国酒业协会": "cada.org.cn",
    "欧睿": "euromonitor.com",
}

# 兜底机构名单（动态机构发现失败时用）
FALLBACK_ORGS = ORG_NAMES

# 权威域（无机构命中时的定向兜底）
AUTHORITY_DOMAINS = tuple(sorted(set(ORG_DOMAIN.values()) | {
    "statista.com", "trendforce.com", "gartner.com", "iimedia.cn",
}))


# ============ 2. 地区 / 范围 ============
# 别名 → 规范名（中英双语统一归一）。改这里，全链路（spec 归一/渲染提示）生效。
REGION_ALIASES = {
    "全球": "全球", "世界": "全球", "worldwide": "全球", "global": "全球",
    "中国": "中国", "中国大陆": "中国", "国内市场": "中国", "国内": "中国",
    "全国": "中国", "china": "中国",
    "亚太": "亚太", "亚太地区": "亚太", "asia": "亚太",
    "asia pacific": "亚太", "asia-pacific": "亚太",
    "北美": "北美", "north america": "北美", "美国": "美国",
    "欧洲": "欧洲", "西欧": "欧洲", "东欧": "欧洲", "europe": "欧洲",
    "印度": "印度", "india": "印度",
    "日本": "日本", "japan": "日本",
    "韩国": "韩国", "korea": "韩国",
    "东南亚": "东南亚",
    "拉美": "拉美", "拉丁美洲": "拉美", "latin america": "拉美",
    "中东": "中东", "middle east": "中东",
    "非洲": "非洲", "africa": "非洲",
    "海外": "海外", "海外市场": "海外",
    "新兴市场": "新兴市场", "发达市场": "发达市场",
}
REGION_RE = _re.compile("|".join(
    sorted((_re.escape(k) for k in REGION_ALIASES), key=len, reverse=True)), _re.I)


def region_cn(r):
    """把地区别名归一为规范名（中英统一）。"""
    return REGION_ALIASES.get((r or "").strip().lower(), r)


# ============ 3. 主题中文词 → 英文关键词 ============
EN_MAP = {
    "宠物": r"\bpet\b|\bpets\b|pet\s*(?:market|industry|food|economy|care)",
    "光伏": r"solar|photovoltaic|\bPV\b",
    "硅料": r"polysilicon|\bsilicon\b",
    "组件": r"\bmodules?\b|\bpanels?\b|component",
    "储能": r"energy\s*storage|battery",
    "风电": r"wind\s*(?:power|turbine|energy)",
    "手机": r"smartphone|mobile\s*phone|handset",
    "智能手机": r"smartphone",
    "半导体": r"semiconductor|chip|wafer",
    "芯片": r"chip|semiconductor",
    "新能源": r"electric\s*vehicle|EV|battery|renewable",
    "汽车": r"automobile|automotive|\bcar\b|vehicle",
    "医药": r"pharma|drug|medicine|biotech",
    "医疗": r"medical|healthcare",
    "游戏": r"game|gaming",
    "电商": r"e-?commerce|online\s*retail",
    "零售": r"retail",
    "消费": r"consumer",
    "家电": r"home\s*appliance|consumer\s*electronics",
    "咖啡": r"\bcoffee\b",
    "白酒": r"baijiu|chinese\s*spirits|liquor",
    "云": r"\bcloud\b",
    "人工智能": r"\bAI\b|artificial\s*intelligence",
    "服务器": r"server|datacenter",
    "面板": r"display|panel|LCD|OLED",
    "轨道交通": r"rail\s*transit|metro|urban\s*rail|subway",
    "地铁": r"metro|subway|urban\s*rail",
    "能源": r"\benergy\b|\bpower\b|oil|gas",
    "电力": r"electricity|power\s*grid",
    "金融": r"finance|banking|insurance",
    "银行": r"bank|banking",
    "教育": r"education|edtech",
    "房地产": r"real\s*estate|property|housing",
    "食品": r"\bfood\b|beverage",
    "饮料": r"beverage|drink",
    "旅游": r"tourism|travel",
    "物流": r"logistics|freight|shipping",
    "航空": r"airline|aviation",
    "机器人": r"robot|robotics",
}
