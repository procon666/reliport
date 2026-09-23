# -*- coding: utf-8 -*-
"""证据库（evidence store）：extract_evidence 裁决结果入库（sqlite，标准库，确定性无 LLM）。

数据流：多文档 → `extract_evidence`（schema v2 裁决层：锚/实体归一/verdict）→
`ingest_docs` 写入。

Schema（两张表，轻量规范）：
  evidence —— 一个裁决证据项（按归一 subject + indicator 分年，唯一键入库）
    id, subject, indicator, year, label, adopted_value, level, score,
    verdict_kind, verdict_detail, n_sources, clusters_json, source_hash,
    updated_at
    唯一键 (subject, indicator, year)：重新 ingest 时按裁决层结果整体覆盖
    （修正优先/时效裁决已在 extract_evidence 完成，库层不做二次裁决）。
  src —— 证据项下的来源 claim
    evidence_id FK, url, url_key, doc_title, value, indicator, subject,
    subject_raw, year, anchor_report, anchor_base, is_rel, revision,
    credibility, date, sentence

设计要点：
- 归一 subject 作为键 → 跨文档异称（'五粮液实现归母'→'五粮液'）自动落同键；
- 相对值锚（anchor_report/base）随 claim 存，供跨源/跨年追溯；
- revision 标记保留，'上修至/修正为'来源可回溯；
- 全部确定性、无外部依赖（sqlite3 标准库）。
"""
import json
import sqlite3
from typing import Dict, List, Optional

from research_agent.analyzer import extract_evidence

DDL = """
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    indicator TEXT NOT NULL,
    year INTEGER,
    label TEXT,
    adopted_value TEXT,
    level TEXT,
    score REAL,
    verdict_kind TEXT,
    verdict_detail TEXT,
    n_sources INTEGER,
    clusters_json TEXT,
    updated_at TEXT,
    UNIQUE (subject, indicator, year)
);
CREATE TABLE IF NOT EXISTS src (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id INTEGER NOT NULL REFERENCES evidence(id),
    url TEXT,
    url_key TEXT,
    doc_title TEXT,
    value TEXT,
    indicator TEXT,
    subject TEXT,
    subject_raw TEXT,
    year INTEGER,
    anchor_report INTEGER,
    anchor_base INTEGER,
    is_rel INTEGER DEFAULT 0,
    revision INTEGER DEFAULT 0,
    credibility REAL,
    date TEXT,
    sentence TEXT
);
CREATE INDEX IF NOT EXISTS idx_ev_key ON evidence (subject, indicator, year);
CREATE INDEX IF NOT EXISTS idx_src_ev ON src (evidence_id);
"""


class EvidenceStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(DDL)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def ingest_docs(self, docs: List) -> int:
        """多文档 → extract_evidence → upsert（按裁决层结果整体覆盖）。"""
        evidences = extract_evidence(docs, max_evidence=500)  # 证据库入库不截断
        n = 0
        for ev in evidences:
            cur = self.conn.execute(
                "SELECT id FROM evidence WHERE subject=? AND indicator=? "
                "AND year IS ?",
                (ev["subject"], ev["indicator"], ev["year"]))
            row = cur.fetchone()
            clusters_json = json.dumps(
                [{k: c.get(k) for k in ("value", "revision")}
                 for c in ev["clusters"]], ensure_ascii=False)
            verdict = ev.get("verdict", {})
            if row:
                eid = row[0]
                self.conn.execute(
                    "UPDATE evidence SET label=?, adopted_value=?, level=?, "
                    "score=?, verdict_kind=?, verdict_detail=?, n_sources=?, "
                    "clusters_json=?, updated_at=datetime('now') WHERE id=?",
                    (ev["label"], ev["adopted"]["value"], ev["level"],
                     ev["score"], verdict.get("kind"), verdict.get("detail"),
                     ev["adopted"]["n_sources"], clusters_json, eid))
                self.conn.execute("DELETE FROM src WHERE evidence_id=?", (eid,))
            else:
                cur = self.conn.execute(
                    "INSERT INTO evidence(subject,indicator,year,label,"
                    "adopted_value,level,score,verdict_kind,verdict_detail,"
                    "n_sources,clusters_json,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                    (ev["subject"], ev["indicator"], ev["year"], ev["label"],
                     ev["adopted"]["value"], ev["level"], ev["score"],
                     verdict.get("kind"), verdict.get("detail"),
                     ev["adopted"]["n_sources"], clusters_json))
                eid = cur.lastrowid
            for cl in ev["claims"]:
                anchor = cl.get("anchor") or {}
                self.conn.execute(
                    "INSERT INTO src(evidence_id,url,url_key,doc_title,value,"
                    "indicator,subject,subject_raw,year,anchor_report,"
                    "anchor_base,is_rel,revision,credibility,date,sentence) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, cl.get("url"), cl.get("url_key"),
                     cl.get("doc_title"), cl["value"], cl["indicator"],
                     cl.get("subject"), cl.get("subject_raw"),
                     cl.get("year"), anchor.get("report_year"),
                     anchor.get("base_year"),
                     1 if cl.get("is_rel") else 0,
                     1 if cl.get("revision") else 0,
                     cl.get("credibility"), cl.get("date"),
                     cl.get("sentence")))
            n += 1
        self.conn.commit()
        return n

    def query(self, subject: Optional[str] = None,
              indicator: Optional[str] = None,
              year: Optional[int] = None) -> List[Dict]:
        """跨源查询：按归一 subject/indicator/year 过滤。"""
        sql = ("SELECT subject,indicator,year,adopted_value,level,score,"
               "verdict_kind,verdict_detail,n_sources FROM evidence WHERE 1=1")
        args = []
        if subject:
            sql += " AND subject=?"; args.append(subject)
        if indicator:
            sql += " AND indicator=?"; args.append(indicator)
        if year is not None:
            sql += " AND year=?"; args.append(year)
        rows = self.conn.execute(sql, args).fetchall()
        return [{"subject": r[0], "indicator": r[1], "year": r[2],
                 "adopted_value": r[3], "level": r[4], "score": r[5],
                 "verdict_kind": r[6], "verdict_detail": r[7],
                 "n_sources": r[8]} for r in rows]

    def sources(self, subject: str, indicator: str,
                year: Optional[int] = None) -> List[Dict]:
        """证据项下的来源明细。"""
        cur = self.conn.execute(
            "SELECT id FROM evidence WHERE subject=? AND indicator=? "
            "AND year IS ?", (subject, indicator, year))
        row = cur.fetchone()
        if not row:
            return []
        rows = self.conn.execute(
            "SELECT url,doc_title,value,subject_raw,anchor_report,"
            "anchor_base,revision,credibility,date,sentence "
            "FROM src WHERE evidence_id=? ORDER BY credibility DESC",
            (row[0],)).fetchall()
        return [{"url": r[0], "doc_title": r[1], "value": r[2],
                 "subject_raw": r[3], "anchor_report": r[4],
                 "anchor_base": r[5], "revision": bool(r[6]),
                 "credibility": r[7], "date": r[8], "sentence": r[9]}
                for r in rows]

    def stats(self) -> Dict:
        ev = self.conn.execute(
            "SELECT verdict_kind, COUNT(*) FROM evidence GROUP BY verdict_kind"
        ).fetchall()
        n = self.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        return {"n_evidence": n,
                "verdict_dist": {k: c for k, c in ev}}
