"""转载/镜像"虚假独立来源"修复的回归测试。

落地口径（本修复不改动 extract_evidence 的核心硬阈值，只改"来源身份"）：
- 本轮只折叠**逐字转载/镜像**（精确整段指纹，零误伤）；
- "轻改写/洗稿"式转载的**近重复合并默认不启用**（需相似度判据+可信度门控，
  权衡后留待看线上数据再评估是否放开），故轻微改写的多站转载在本版本仍按独立来源计。

覆盖用例：
  1. 同一通稿被 N 个低可信度镜像站**原样**转载 → 折叠为 1 origin → low，绝不 high；
  2. 同一通稿被**轻改写**(换词/截断)转载 → 近重复合并未启用 → 保持独立（记录现口径）；
  3. 3 个**独立原创**长文报道同一数值 → 保持 3 origin（不误并）；
  4. 高可信源仅措辞相近(数值四舍五入噪声) → 保持独立；
  5. 同 URL 带 m./www./追踪参数变体 → 归并为 1 origin；
  6. 关闭 ANALYZER_ORIGIN_MERGE=0 时回退为按原始 URL 计数（镜像不再折叠）。
"""
import os
import sys
sys.path.insert(0, '.')
from research_agent.searcher import SourceDoc
import research_agent.analyzer as A

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")

def make(url, content, cred=0.5, title=""):
    return SourceDoc(url=url, title=title or url, content=content,
                     from_fetch=True, fetched_at="2024-06-01", credibility=cred)

def find_ev(evs, indicator="规模"):
    for e in evs:
        if e["indicator"] == indicator:
            return e
    return None

# 一段较长的"通稿"正文，作为被多站转载的原料
CORE = ("2024年全球新能源车市场规模达到2000亿美元，同比快速增长，行业景气度持续走高，"
        "各大车企纷纷加大投入，产业链上下游协同发展，市场前景被广泛看好。"
        "据行业研究机构统计，电动汽车渗透率进一步提升，推动整个产业链规模扩张。"
        "业内普遍认为，电池成本下降与充电设施完善是推动市场增长的关键因素。"
        "政策面上，多国陆续出台补贴与基础设施建设规划，为行业注入长期动力。")

print("\n=== 用例1：同通稿原样转载到 3 个低可信镜像站 → 折叠为 1 origin → low ===")
mirrors = [CORE, CORE, CORE]
docs = [make(f"https://m{i}-spam.info/p", t, cred=0.42 - i * 0.06) for i, t in enumerate(mirrors)]
groups = A._assign_origins(docs)
check("3 原样镜像折叠为 1 origin", len(groups) == 1, f"n_origin={len(groups)}")
ev = find_ev(A.extract_evidence(docs))
check("原样镜像计为 1 来源 → low", ev is not None and ev["level"] == "low" and ev["n_sources"] == 1,
      f"level={ev['level'] if ev else None} src={ev['n_sources'] if ev else None}")

print("\n=== 用例2：轻改写转载 → 近重复合并未启用，保持独立(记录落地现口径) ===")
core = CORE
light = ["据外媒报道：" + core,
         core.replace("快速", "明显").replace("市场前景被广泛看好", "行业普遍持乐观预期"),
         "编译自行业报告：" + core[:-12]]
docs = [make(f"https://agg{i}.info/x", t, cred=0.4 - i * 0.06) for i, t in enumerate(light)]
groups = A._assign_origins(docs)
check("轻改写(非逐字)镜像不折叠 → 保持 3 origin(现口径)", len(groups) == 3,
      f"n_origin={len(groups)}（逐字指纹未命中=非原样转载，本轮近重复不启用，故不折叠）")

print("\n=== 用例3：3 篇独立原创长文报道同一数值 → 保持 3 origin（不误并）===")
indep = [
    ("国际能源署发布年度报告指出，受新能源政策推动，全球清洁能源投资规模在2024年"
     "达到2.1万亿美元，其中光伏和风电占比超过六成，各国政府补贴与企业资本开支共同"
     "驱动了这一增长，行业分析师认为未来五年仍将保持双位数扩张。"),
    ("最新统计显示，全球范围内投向太阳能与风能等可再生能源的资金在2024年创下历史"
     "新高，总额约为2.1万亿美元，较上年增长明显。咨询机构测算，储能与电网配套投资"
     "也在同步放量，带动整条设备供应链景气。"),
    ("根据多份产业调研，2024年可再生能源领域获得的总融资达到2.1万亿美元量级。业内"
     "指出这一数字主要由大型风光电站项目构成，并预计随着各国碳中和目标推进，资本"
     "将持续涌入，行业热度有望维持高位。"),
]
docs = [make(f"https://org{i}.com/r", t, cred=0.85 - i * 0.05) for i, t in enumerate(indep)]
groups = A._assign_origins(docs)
check("3 独立长文保持 3 origin", len(groups) == 3, f"n_origin={len(groups)}")

print("\n=== 用例4：高可信源仅措辞相近(数值含舍入噪声) → 保持独立，不被逐字指纹误并 ===")
# 12.0 / 12.1 / 12.05：独立机构对同一量的四舍五入差异（正文并非逐字相同）
docs = [
    make("https://p.com/1", "2025年全球智能手机出货量12.0亿部。", cred=0.9),
    make("https://p.com/2", "统计显示2025年全球智能手机出货12.1亿部。", cred=0.85),
    make("https://p.com/3", "2025年全球智能手机出货12.05亿部。", cred=0.8),
]
groups = A._assign_origins(docs)
check("相近措辞独立源保持 3 origin(不误并)", len(groups) == 3, f"n_origin={len(groups)}")
ev = find_ev(A.extract_evidence(docs), "出货量")
if ev is None:
    ev = next((e for e in A.extract_evidence(docs) if "出货" in e["indicator"]), None)
check("高可信噪声差异仍按 3 来源计分", ev is not None and ev["n_sources"] == 3,
      f"src={ev['n_sources'] if ev else None}")

print("\n=== 用例5：同 URL 的 m./www./追踪参数变体 → 归并为 1 origin ===")
docs = [
    make("https://news.qq.com/article?id=123&utm_source=abc", "正文X：市场2024年规模达500亿元。", cred=0.7),
    make("https://www.news.qq.com/article?id=123", "正文X：市场2024年规模达500亿元。", cred=0.7),
    make("https://m.news.qq.com/article?id=123#frag", "正文X：市场2024年规模达500亿元。", cred=0.7),
]
groups = A._assign_origins(docs)
check("URL 变体(www/m/追踪参数)归并为 1 origin", len(groups) == 1, f"n_origin={len(groups)}")

print("\n=== 用例6：关闭 ANALYZER_ORIGIN_MERGE → 回退为按原始 URL 计数 ===")
os.environ["ANALYZER_ORIGIN_MERGE"] = "0"
# 需要重载模块级开关（直接改属性验证回退路径，不重启进程）
old_flag = A._ORIGIN_MERGE_ENABLED
A._ORIGIN_MERGE_ENABLED = False
try:
    docs = [make(f"https://m{i}-spam.info/p", CORE, cred=0.4) for i in range(3)]
    groups = A._assign_origins(docs)
    check("关闭时 3 镜像按原始 URL 各算一源(3 origin)", len(groups) == 3, f"n_origin={len(groups)}")
finally:
    A._ORIGIN_MERGE_ENABLED = old_flag
    os.environ.pop("ANALYZER_ORIGIN_MERGE", None)

print("\n========== 转载/镜像回归：{} 通过 / {} 失败 ==========".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
