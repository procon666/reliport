"""确定性计分修复后的回归测试。

覆盖：
  1. 3 个不同来源给出同一数值  → 期望 high
  2. 3 个来源，2 一致 + 1 分歧 → 期望 medium
  3. 同 URL 重复 3 次           → 期望 low（单一来源，修复前误判 high）
  4. 单 URL 正文内同一说法多次  → 期望 low（修复前可能误判）
  5. 2 个不同来源一致           → 期望 high/medium（取决于可信度）
"""
import sys
sys.path.insert(0, '.')
from research_agent.searcher import SourceDoc
from research_agent.analyzer import extract_evidence

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    tag = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{tag}] {name}" + (f"  {detail}" if detail else ""))

def make(url, title, content, cred=0.9, fetched="2024-06-01"):
    return SourceDoc(url=url, title=title, content=content,
                     from_fetch=True, fetched_at=fetched, credibility=cred)

def find_ev(evs, indicator):
    for e in evs:
        if e["indicator"] == indicator and e["subject"]:
            return e
    for e in evs:
        if e["indicator"] == indicator:
            return e
    return None

# ---------- 用例1：3 个不同来源、口径一致 → high ----------
c1 = ("2024年全球AI市场规模达到2500亿美元。")
c2 = ("报告显示全球AI市场规模2024年为2500亿美元。")
c3 = ("数据显示全球AI市场2024年规模达2500亿美元。")
docs = [
    make("https://a.com/r1", "报告A", c1, cred=0.9),
    make("https://b.com/r2", "报告B", c2, cred=0.85),
    make("https://c.com/r3", "报告C", c3, cred=0.8),
]
ev = find_ev(extract_evidence(docs), "规模")
print("\n=== 用例1：3独立来源一致（AI市场 2500亿）===")
print("  ", ev["label"], ev["adopted"]["value"], ev["level"], ev["score"], "| n_sources=", ev["n_sources"])
check("3独立来源一致→high", ev["level"] == "high" and ev["n_sources"] == 3, f"level={ev['level']} src={ev['n_sources']}")
check("reason 提到独立来源", "独立来源" in ev["reason"])

# ---------- 用例2：3 来源 2 一致 + 1 分歧 → medium ----------
c_a = "全球AI编程工具市场规模2024年约50亿美元，未来快速增长。"
c_b = "数据显示AI编程工具市场2024年规模约为50亿美元。"
c_c = "另一机构则估算AI编程工具市场2024年规模仅20亿美元。"
docs = [
    make("https://a.com/x", "源A", c_a, cred=0.9),
    make("https://b.com/y", "源B", c_b, cred=0.85),
    make("https://c.com/z", "源C", c_c, cred=0.9),
]
ev = find_ev(extract_evidence(docs), "规模")
print("\n=== 用例2：3来源 2一致+1分歧 ===")
print("  ", ev["label"], "裁决=", ev["adopted"]["value"], ev["level"], ev["score"], "| 簇数=", len(ev["clusters"]))
check("2一致+1分歧→medium", ev["level"] == "medium" and ev["n_sources"] == 3,
      f"level={ev['level']} src={ev['n_sources']}")
check("裁决采纳主导簇(50亿)", "50亿美元" in ev["adopted"]["value"] or "50亿" in ev["adopted"]["value"])

# ---------- 用例3：同 URL 重复 3 次 → low（本 bug 核心）----------
c_single = "2024年全球低代码市场规模达50亿美元，行业快速发展。"
docs = [make("https://low.com/one", "同一份报告", c_single, cred=0.9) for _ in range(3)]
ev = find_ev(extract_evidence(docs), "规模")
print("\n=== 用例3：同URL重复3次 ===")
print("  ", ev["label"], ev["adopted"]["value"], ev["level"], ev["score"], "| n_sources=", ev["n_sources"])
check("同URL重复3次→low（单一来源）", ev["level"] == "low" and ev["n_sources"] == 1,
      f"level={ev['level']} src={ev['n_sources']}")

# ---------- 用例4：单 URL 正文同一说法多次 → low ----------
c_rep = ("市场规模达50亿美元。" * 5) + "2024年全球市场规模50亿美元。"
docs = [make("https://rep.com/x", "重复正文", c_rep, cred=0.9)]
ev = find_ev(extract_evidence(docs), "规模")
print("\n=== 用例4：单URL正文内重复说法 ===")
print("  ", ev["label"], ev["adopted"]["value"], ev["level"], ev["score"], "| n_sources=", ev["n_sources"])
check("单URL正文重复→low", ev["level"] == "low" and ev["n_sources"] == 1,
      f"level={ev['level']} src={ev['n_sources']}")

# ---------- 用例5：2 个独立来源一致（高可信）→ high ----------
docs = [
    make("https://m.com/1", "来源甲", "全球AI市场规模2024年达3000亿美元。", cred=0.92),
    make("https://n.com/2", "来源乙", "统计显示全球AI市场规模为3000亿美元。", cred=0.9),
]
ev = find_ev(extract_evidence(docs), "规模")
print("\n=== 用例5：2独立来源一致+高可信 ===")
print("  ", ev["label"], ev["adopted"]["value"], ev["level"], ev["score"], "| n_sources=", ev["n_sources"])
check("2独立来源一致→high(可信≥0.85)", ev["level"] == "high" and ev["n_sources"] == 2,
      f"level={ev['level']} src={ev['n_sources']}")

# ---------- 用例6：2 独立来源但同 URL fragment 不同 → 视作1来源 ----------
docs = [
    make("https://x.com/a#sec1", "同页锚1", "规模2024年达120亿美元。", cred=0.9),
    make("https://x.com/a#sec2", "同页锚2", "规模为120亿美元。", cred=0.9),
]
ev = find_ev(extract_evidence(docs), "规模")
print("\n=== 用例6：同URL不同fragment ===")
print("  ", ev["label"], ev["adopted"]["value"], ev["level"], ev["score"], "| n_sources=", ev["n_sources"])
check("同URL不同fragment→1来源→low", ev["level"] == "low" and ev["n_sources"] == 1,
      f"level={ev['level']} src={ev['n_sources']}")

print(f"\n========== 结果：{PASS} 通过 / {FAIL} 失败 ==========")
sys.exit(1 if FAIL else 0)
