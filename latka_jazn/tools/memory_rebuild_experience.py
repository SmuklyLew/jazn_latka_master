from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import json

from latka_jazn.tools.chat_export_reader import build_conversation_graph
from latka_jazn.tools.chat_export_topics import ChatExportTopicStore
from latka_jazn.tools.memory_rebuild_journal import JournalStore, infer_domains
from latka_jazn.tools.memory_rebuild_common import (
    ACK_RE, NOISE_RE, DATABASE_FILENAMES, bounded, canonical_json, fts_queries,
    norm, now_utc, schema_version, sha_text, uid,
)
from latka_jazn.tools.memory_rebuild_sql import EXPERIENCE_SQL
from latka_jazn.tools.memory_rebuild_store import Store


class ExperienceStore(Store):
    def __init__(self, path: Path) -> None:
        super().__init__(path, EXPERIENCE_SQL, "experience_meta", schema_version("live_experience"))

    def candidate(self, source_db: str, source_type: str, source_id: str, source_hash: str | None,
                  title: str, summary: str, truth: str, confidence: float, importance: float,
                  domains: Iterable[str], score: dict[str, Any]) -> tuple[str | None, str]:
        text = norm(summary)
        reasons = []
        if len(text) < 40:
            reasons.append("too_short")
        if ACK_RE.fullmatch(text):
            reasons.append("acknowledgement_only")
        if NOISE_RE.search(text):
            reasons.append("technical_noise")
        if reasons:
            return None, "filtered_noise"
        identity = sha_text(canonical_json([source_db, source_type, source_id, sha_text(text.lower())]))
        candidate_id = uid("experience-candidate", identity)
        payload = {**score, "noise_reasons": reasons, "length": len(text)}
        with self.transaction():
            cursor = self.con.execute(
                """INSERT OR IGNORE INTO candidates(candidate_id,identity_key,source_database,source_type,
                   source_record_id,source_sha256,title,summary,truth_status,confidence,importance,domains_json,
                   score_json,status,created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'pending_review',?)""",
                (candidate_id, identity, source_db, source_type, source_id, source_hash, title, summary,
                 truth, bounded(confidence, 0.55), bounded(importance, 0.6), canonical_json(sorted(set(domains))),
                 canonical_json(payload), now_utc()),
            )
            if cursor.rowcount:
                doc = self.con.execute(
                    "INSERT INTO experience_fts_docs(record_type,record_id,title,truth_status) VALUES('candidate',?,?,?)",
                    (candidate_id, title, truth),
                )
                self.con.execute("INSERT INTO experience_fts(rowid,text) VALUES(?,?)",
                                 (doc.lastrowid, f"{title}\n{summary}\n{' '.join(domains)}"))
                return candidate_id, "inserted"
        return candidate_id, "duplicate"

    def from_journal(self, journal: JournalStore, limit: int | None = None) -> dict[str, Any]:
        sql = "SELECT * FROM journal_entries WHERE status='active' ORDER BY COALESCE(event_time_start,updated_at_utc),entry_id"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = journal.con.execute(sql, params).fetchall()
        counters = {"inserted_candidates": 0, "duplicates": 0, "filtered_noise": 0}
        ids = []
        for row in rows:
            candidate_id, status = self.candidate(
                DATABASE_FILENAMES["journal"], "journal_entry", row["entry_id"], row["content_sha256"],
                row["title"], row["summary"], row["truth_status"],
                0.75 if row["truth_status"] in {"source_recorded", "user_confirmed"} else 0.55,
                row["importance"], infer_domains(f"{row['title']} {row['summary']} {row['content']}"),
                {"source": "journal", "suspected_fanout": bool(row["suspected_fanout"])},
            )
            counters[{"inserted": "inserted_candidates", "duplicate": "duplicates"}.get(status, "filtered_noise")] += 1
            if candidate_id and status == "inserted":
                ids.append(candidate_id)
        return {"ok": True, "source": DATABASE_FILENAMES["journal"], "rows_seen": len(rows),
                **counters, "candidate_ids": ids, "automatic_experience": False,
                "automatic_l2": False, "automatic_l3": False}

    def from_chats(self, archive: Path, limit: int | None = None) -> dict[str, Any]:
        with ChatExportTopicStore(archive) as topics:
            topics.analyse_all(force=False, limit=limit)
            sql = "SELECT * FROM conversation_segments ORDER BY conversation_id,ordinal"
            params: tuple[Any, ...] = ()
            if limit is not None:
                sql += " LIMIT ?"
                params = (limit,)
            segments = topics.con.execute(sql, params).fetchall()
            cache: dict[str, dict[str, Any]] = {}
            counters = {"inserted_candidates": 0, "duplicates": 0, "filtered_noise": 0}
            ids = []
            for segment in segments:
                conversation_id = segment["conversation_id"]
                payload = cache.get(conversation_id) or topics.archive.conversation_payload(conversation_id)
                if payload is None:
                    continue
                cache[conversation_id] = payload
                graph = build_conversation_graph(payload)
                positions = {node.node_id: index for index, node in enumerate(graph.nodes)}
                start = positions.get(segment["start_node_id"], 0)
                end = positions.get(segment["end_node_id"], start)
                if end < start:
                    start, end = end, start
                text = "\n".join(f"{node.role}: {node.text}" for node in graph.nodes[start:end + 1] if node.text.strip())
                importance = min(1.0, max(0.05, 0.25 + segment["char_count"] / 4000.0))
                candidate_id, status = self.candidate(
                    DATABASE_FILENAMES["archive_chats"], "conversation_segment", segment["segment_id"],
                    graph.semantic_tree_sha256, graph.title or "Segment rozmowy", norm(text)[:4000],
                    segment["truth_status"], segment["confidence"], importance,
                    [segment["primary_domain"]], {"source": "chat_segment", "mode": segment["mode"]},
                )
                counters[{"inserted": "inserted_candidates", "duplicate": "duplicates"}.get(status, "filtered_noise")] += 1
                if candidate_id and status == "inserted":
                    ids.append(candidate_id)
        return {"ok": True, "source": DATABASE_FILENAMES["archive_chats"], "segments_seen": len(segments),
                **counters, "candidate_ids": ids, "automatic_experience": False,
                "automatic_l2": False, "automatic_l3": False}

    def list_candidates(self, status: str = "pending_review", limit: int = 100) -> list[dict[str, Any]]:
        rows = self.con.execute(
            "SELECT * FROM candidates WHERE status=? ORDER BY importance DESC,confidence DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["domains"] = json.loads(item.pop("domains_json"))
            item["score"] = json.loads(item.pop("score_json"))
            result.append(item)
        return result

    def approve(self, candidate_id: str, confirm_id: str, approved_by: str, reason: str) -> dict[str, Any]:
        if candidate_id != confirm_id:
            raise ValueError("confirm-candidate-id must exactly match candidate-id")
        if not approved_by.strip() or not reason.strip():
            raise ValueError("approved-by and reason are required")
        row = self.con.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        experience_id, current = uid("experience", row["identity_key"]), now_utc()
        domains = json.loads(row["domains_json"])
        with self.transaction():
            self.con.execute(
                """INSERT OR IGNORE INTO experiences(experience_id,identity_key,candidate_id,title,summary,
                   truth_status,confidence,importance,status,revision,approved_by,approval_reason,
                   created_at_utc,updated_at_utc) VALUES(?,?,?,?,?,?,?,?,'active',1,?,?,?,?)""",
                (experience_id, row["identity_key"], candidate_id, row["title"], row["summary"],
                 row["truth_status"], row["confidence"], row["importance"], approved_by, reason, current, current),
            )
            self.con.executemany("INSERT OR IGNORE INTO experience_domains(experience_id,domain) VALUES(?,?)",
                                 [(experience_id, domain) for domain in domains])
            self.con.execute(
                """INSERT OR IGNORE INTO experience_sources(experience_id,source_database,source_type,
                   source_record_id,source_sha256,evidence_json) VALUES(?,?,?,?,?,?)""",
                (experience_id, row["source_database"], row["source_type"], row["source_record_id"],
                 row["source_sha256"], canonical_json({"candidate_id": candidate_id})),
            )
            self.con.execute("UPDATE candidates SET status='approved',reviewed_at_utc=?,reviewed_by=?,review_reason=? WHERE candidate_id=?",
                             (current, approved_by, reason, candidate_id))
        return {"ok": True, "candidate_id": candidate_id, "experience_id": experience_id,
                "approved_by": approved_by, "automatic_l2": False, "automatic_l3": False}

    def counts(self) -> dict[str, int]:
        return {
            "candidates": self.con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],
            "pending_review": self.con.execute("SELECT COUNT(*) FROM candidates WHERE status='pending_review'").fetchone()[0],
            "experiences": self.con.execute("SELECT COUNT(*) FROM experiences").fetchone()[0],
            "source_links": self.con.execute("SELECT COUNT(*) FROM experience_sources").fetchone()[0],
        }

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        for fts_query in fts_queries(query):
            rows = self.con.execute(
                """SELECT d.record_type,d.record_id,d.title,d.truth_status,bm25(experience_fts) rank
                   FROM experience_fts JOIN experience_fts_docs d ON d.rowid=experience_fts.rowid
                   WHERE experience_fts MATCH ? ORDER BY rank LIMIT ?""", (fts_query, limit)).fetchall()
            if rows:
                return [dict(row) for row in rows]
        return []
