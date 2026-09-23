# -*- coding: utf-8 -*-
"""偏好存储（M0a 最小实现）：PreferenceStore.

作用域：key 采用 "domain/topic/metric" 分层路径；M0 只实现**精确键命中**，
领域级回退（子级覆盖父级）留 TODO（M1 做）。
字段对齐 docs/product_skeleton.md §2.1：dim/value/origin/hits/updated_at。
引擎零改动；纯产品层。
"""
import json
import os
import time


class PreferenceStore:
    def __init__(self, path: str):
        self.path = path
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                return json.load(open(self.path, encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get(self, key: str):
        """精确键命中。M1 补作用域树回退（先查具体→逐级上溯→专家默认）。"""
        return self.data.get(key)

    def decide(self, key: str):
        """返回 (有偏好?, 偏好值)。无偏好时由上层用专家默认。"""
        p = self.get(key)
        return (True, p) if p else (False, None)

    def set_user_choice(self, key: str, dim: str, value: str,
                        origin: str = "user_pick"):
        """用户选择事件入口——M0a 由模拟源调用，M0b 由真人点击事件调用，
        接口不变，闭环代码零改动。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        old = self.get(key)
        hits = (old.get("hits", 0) if old else 0) + 1
        self.data[key] = {
            "dim": dim, "value": value, "origin": origin,
            "hits": hits, "updated_at": now,
        }
        self.save()
        return self.data[key]

    def echo_phrase(self, key: str) -> str:
        """回显（订制感+合规留痕双职责）文案生成。"""
        p = self.get(key)
        if not p:
            return ""
        return (f"按你的采信：{p['value']}（{p['dim']}，"
                f"已确认 {p['hits']} 次 · 非系统推荐）")


def build_pref_block(key: str, dim: str, val: str, hits: int) -> str:
    """偏好驱动生成的强约束指令（M0 后修正版）：所选口径决定叙述主轴。

    v1 只写"以偏好口径为主叙事"，AI 仍把来源最强的未选口径（如总销量榜首
    460.2）当 headline——订制成了"标注"而非"视角"。v2 明确：
    ① 标题/摘要/结论/首句一律按所选口径表述；② 未选口径禁止进入主叙事，
    只许在"口径差异说明"节交代；③ 附正例。
    """
    return (f"=== 你的用户口径偏好（必须遵守，决定叙述主轴）===\n"
            f"- 话题「{key}」：用户主口径 = {val}（{dim}）"
            f"【用户采信 · 已确认 {hits} 次 · 非系统推荐】\n"
            "执行规则（严格）：\n"
            f"1. **所选口径决定视角**：凡该话题的标题、摘要、结论、正文首句及"
            f"排名/份额表述，一律按所选口径 {dim} 组织（例：选零售口径则写"
            f"'比亚迪2025年零售销量{val}，居国内乘用车零售市场首位'）。\n"
            "2. **未选口径禁止进入主叙事**：其它口径数值不得出现在该话题的标题、"
            "摘要、结论或正文主句中；只允许集中出现在独立的『口径差异说明』小节"
            "作交代。\n"
            f"3. 所选口径主句后随文标注『按你的采信：{val}（{dim}，"
            f"已确认 {hits} 次 · 非系统推荐）』。\n"
            "4. 其余无偏好话题照默认专家口径。\n")


def choose_prefs(store, query_key: str):
    """作用域树回退：返回适用于 query_key 的偏好（最具体优先）。

    query_key 形如 "领域/主题/指标"；data 中任意已存偏好 key p 若满足
    query_key == p 或 p 是 query_key 的祖先（按 '/' 边界）即适用。
    返回 [(适用长度, key, pref)] 按最长匹配排序；无 → []。
    例：存 "新能源"(零售口径) → query "新能源/比亚迪销量/全年销量" 命中领域级；
       存 "新能源/比亚迪销量" → 同话题命中话题级（更长→优先）。
    """
    q = (query_key or "").strip("/")
    hits = []
    for p, pref in store.data.items():
        p = (p or "").strip("/")
        if not p or not q:
            continue
        if q == p or q.startswith(p + "/"):
            hits.append((len(p.split("/")), p, pref))
        elif p.startswith(q + "/"):
            # 偏好比查询更具体（存了指标级，查询落在话题级）——不向下应用，避免错套
            continue
    hits.sort(key=lambda x: -x[0])
    return [(k, v) for _n, k, v in hits]
