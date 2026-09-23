# 归档交付清单（下游接入团队）

> 交付对象：证据库/跨源可信/端到端验证的下游接入方。
> 交付基线：`baseline-post-E3` + CI 门禁 29/29（抽取层收口 `d650a13`；schema v2
> 聚合层扩展 `ee322a9`/`c670346`/`194067b`）。
> 性质：本文档是"交付包目录"——所有资产均在仓库内，按表索取即用。

---

## 1. 引擎调用 API（三个入口，纯确定性/无 LLM）

| 入口 | 签名 | 用途 | 返回 |
|---|---|---|---|
| **句子级抽取** | `analyzer._sentence_claims(sent, url_key)` | 单句 → 四元组 claims | `[{start,value,indicator,subject,year,dist}]`（subject 互含判定用） |
| **文档级分析** | `analyzer.analyze(docs, min_content=200)` | 整批文档分析（报告生成链路） | 聚合分析 dict |
| **证据聚合** | `analyzer.extract_evidence(docs)` | **证据库接入点**：多来源量化主张 → 证据项（schema v2 裁决层） | `[{subject,indicator,label,n_sources,claims,clusters,adopted,level,score,reason,verdict:{kind,detail}}]` |

> `extract_evidence` 已内建"来源级计数"（以不同 URL 为准的交叉验证）、四门归簇
> （值 close+量纲+锚一致+None 锚不抬）、`adopted` 裁决（含修正优先）、确定性置信
> 等级 high/medium/low（单一来源恒 low），以及 **schema v2 三步扩展**（锚字段/
> 实体归一/verdict 结构化——抽取层零改动，全部收敛聚合层）。

**最小接入示例**：
```python
from research_agent.analyzer import extract_evidence
# docs: [SourceDoc(url=..., title=..., content=...)]（正文/摘录）
evidence = extract_evidence(docs)  # 证据项列表,含 adopted 裁决 + verdict{kind,detail}
```

## 2. 治理资产交付物表

| 资产 | 位置 | 内容 | 给谁看 |
|---|---|---|---|
| **封板文档** | `docs/phase_closeout_2026.md` | 口径纪律表/审计护栏/KNOWN 22 归组/收敛曲线/风险提醒/存档链/**附录 A schema v2 扩展** | 所有人（先读这个） |
| **schema v2 议题包+拍板** | `docs/schema_v2_agenda.md` | 4 缺口 6 议题 + 评审拍板（结构化锚/实体归一/议题3-4-5） | schema v2 维护者 |
| **schema v2 跑分收集** | `docs/e2e_harvest.md` | 6 报告 373 claims 真实 case（锚/实体拆分/假冲突） | 议题设计输入 |
| **口径归因材料** | `docs/p3_year_schema_review.md` | 年份错 4 簇（A 超窗/B 写法/C 相对值/D 存量端点）逐例归因 | 下游标注/复核 |
| **评审决策稿 + schema v2 备忘** | `docs/p3_review_agenda.md` | 评审拍板记录、待决项、schema v2 侧信道锚备忘 | 下游标注/复核 |
| **观测笔记（5 轮）** | `docs/obs_round2_note.md` | 23 领域观测史、子型成簇/达线记录、批次结论 | 想了解缺陷如何被发现的人 |
| **KNOWN 边界清单** | `tests/test_known_boundaries.py` | 22 条 subject xfail（docstring 含全部治理史） | 下游（遇到即知情不报 bug） |
| **修复固化断言** | `tests/test_label_fixes.py` | 63 条 = 抽取层能力清单 | 想知道引擎会什么的人 |
| **schema v2 测试** | `tests/test_schema_v2_anchor.py` / `_norm.py` / `_verdict.py` | 9+22+11=42 断言固化三步 | schema v2 维护者 |
| **口径审计** | `tests/test_year_schema_audit.py` | 相对值 year=None 机器约束 | 新标注必跑 |
| **CI 门禁** | `scripts/ci_gate.sh` | 29 项统一闸门（语料/KNOWN/审计/断言/label 防回归/专项/盲测/schema v2 测试） | 任何改动人 |
| **端到端跑分工具** | `scripts/e2e_pilot.py` / `scripts/e2e_harvest.py` | 单报告代理指标 / 多报告聚合观测（可复跑） | 端到端验证 |
| **抽取+聚合入口** | `research_agent/analyzer.py` | `_sentence_claims`/`analyze`/`extract_evidence`/`detect_conflicts` | 接入方 |

## 3. 运行与验证

```bash
bash scripts/ci_gate.sh        # 29 项门禁,全绿才允许合并(封板纪律)
python3 -m research_agent.eval --expect-only   # 语料 79/79
```

## 4. schema v2 状态（已落地三步，抽取层零改动）

- **锚字段**：相对值 claim 侧信道锚 `{report_year, base_year}` + 锚一致门（`ee322a9`）；
- **实体归一**：`_norm_evidence_subject` 残词清洗收敛跨文实体（`c670346`）；
- **议题 3/4/5**：None 锚相对值不互抬 · 量纲门 · 修正识别 · `verdict.kind`
  结构化（`194067b`）；
- 输出字段与 kind 枚举（一致/单源/修正/键失真/分歧-真冲突/分歧-无主导/分开呈现）
  见封板文档附录 A；决策文档见上表；全程确定性（无 LLM）。

## 5. 版本与基线

- 抽取层交付基线：`baseline-post-E3`（`38cf2da`）；观测封板：`12ab825`；CI 收口：
  `d650a13`；schema v2 聚合层扩展：`ee322a9`/`c670346`/`194067b`；归档更新：
  `851cdc2`。
- 全历史基线 `baseline-pre/post-*` 成对保留，任何阶段可 `git reset --hard`
  单独回退（见封板文档 §8）。
- 抽取层引擎零改动冻结；聚合层（schema v2）扩展按"先跑分收集真实 case → 独立
  测试提交"纪律推进。

## 6. 接手必读顺序

封板文档 `docs/phase_closeout_2026.md`（含附录 A schema v2 扩展）→
`docs/schema_v2_agenda.md` + `docs/e2e_harvest.md`（schema v2 决策与真实 case）→
`docs/obs_round2_note.md`（观测史）→ `docs/p3_year_schema_review.md` +
`docs/p3_review_agenda.md`（年份归因/评审决策）→ `tests/test_known_boundaries.py`
（22 条边界）→ `tests/test_label_fixes.py`（63 断言）+ `tests/test_schema_v2_*.py`
（42 断言，抽取层 + 聚合层能力清单）。
