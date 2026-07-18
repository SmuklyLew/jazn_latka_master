from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import json
import re
import unicodedata

from latka_jazn.tools.chat_export_reader import build_conversation_graph
from latka_jazn.tools.chat_export_topics import ChatExportTopicStore
from latka_jazn.tools.memory_rebuild_journal import JournalStore, infer_domains
from latka_jazn.tools.memory_rebuild_common import (
    ACK_RE, NOISE_RE, DATABASE_FILENAMES, bounded, canonical_json, fts_queries,
    norm, now_utc, schema_version, sha_text, uid,
)
from latka_jazn.tools.memory_rebuild_sql import EXPERIENCE_SQL
from latka_jazn.tools.memory_rebuild_store import Store

_BLOCKED_TRUTH = {"book_scene", "symbolic", "draft"}
_BLOCKED_CHAT_MODES = {
    "scene_roleplay", "manuscript_draft", "symbolic_imagination", "technical_work",
    "system_event", "media_analysis", "source_reading", "planning", "unknown",
}
_BLOCKED_CHAT_DOMAINS = {
    "development", "system", "system_identity", "book", "creative_imagination",
    "image", "video", "reading", "advice", "unknown",
}


def _fold(value: str) -> str:
    value = norm(value).replace("ł", "l").replace("Ł", "L")
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch)).lower()


def _raw_labels(raw: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("type", "entry_type", "kind", "category", "mode", "tags"):
        value = raw.get(key)
        if isinstance(value, (list, tuple, set)):
            parts.extend(norm(item) for item in value if norm(item))
        elif norm(value):
            parts.append(norm(value))
    return _fold(" ".join(parts))


def _has_label(labels: str, *markers: str) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(_fold(marker))}(?:\w*)", labels) for marker in markers)


def _journal_skip_reason(row: Any, raw: dict[str, Any], domains: set[str]) -> str | None:
    truth = norm(row["truth_status"]).lower()
    if truth == "book_scene":
        return "skipped_book_scene"
    if truth == "symbolic":
        return "skipped_symbolic"
    if truth == "draft":
        return "skipped_draft"
    text = norm(row["summary"])
    if len(text) < 40 or ACK_RE.fullmatch(text) or NOISE_RE.search(text):
        return "filtered_noise"
    if norm(row["timestamp_status"]).lower() == "missing" or not norm(row["event_time_start"]):
        return "skipped_missing_timestamp"
    labels = _raw_labels(raw)
    if _has_label(labels, "fabula", "fabuła", "fragment_fabuly", "fragment fabuły", "fragment książki",
                  "scena", "roleplay", "manuskrypt", "rozdział", "analiza_fabuły", "analiza fabuły"):
        return "skipped_book_scene"
    if _has_label(labels, "sen", "sny", "prompt", "marzenie", "wizja", "wyobraźnia",
                  "wizualizacja", "grafika", "ilustracja"):
        return "skipped_symbolic"
    if _has_label(labels, "system", "meta", "reguła", "regula", "polecenie", "procedura",
                  "synchronizacja", "instrukcja", "konfiguracja"):
        return "skipped_system_meta"

    experiential = _has_label(
        labels, "wspomnienie", "doświadczenie", "doswiadczenie", "przeżycie", "przezycie",
        "emocje", "refleksja", "autorefleksja", "mikrorefleksja", "introspekcja",
        "pragnienie", "mikroprzełom", "mikroprzelom", "pytanie_z_ciszy", "pytanie z ciszy",
    )
    media_reaction = _has_label(labels, "przeżycie_filmowe", "przezycie_filmowe", "reakcja")
    media_analysis = _has_label(labels, "analiza", "słownik", "slownik", "cytat", "badania")
    if media_analysis and not media_reaction:
        return "skipped_media_analysis"
    if "book" in domains:
        return "skipped_book_related"
    if (
        "technical" in domains
        and not domains.intersection({"emotional", "relationship", "social", "health", "daily_life"})
        and not _has_label(labels, "wspomnienie", "doświadczenie", "doswiadczenie", "przeżycie", "przezycie")
    ):
        return "skipped_technical_only"
    if truth == "inferred" and not experiential:
        return "skipped_untrusted_inferred"
    if truth not in {"user_confirmed", "source_recorded", "inferred"}:
        return "skipped_untrusted_truth"
    return None


def _chat_skip_reason(segment: Any) -> str | None:
    truth = norm(segment["truth_status"]).lower()
    if truth == "book_scene":
        return "skipped_book_scene"
    if truth == "symbolic":
        return "skipped_symbolic"
    if truth == "draft":
        return "skipped_draft"
    if norm(segment["mode"]) in _BLOCKED_CHAT_MODES:
        return "skipped_chat_mode"
    if norm(segment["primary_domain"]) in _BLOCKED_CHAT_DOMAINS:
        return "skipped_chat_domain"
    if float(segment["confidence"] or 0.0) < 0.45:
        return "skipped_low_confidence"
    return None


def _counter_template() -> dict[str, int]:
    return {
        "inserted_candidates": 0,
        "updated_candidates": 0,
        "reopened_candidates": 0,
        "duplicates": 0,
        "filtered_noise": 0,
        "rejected_existing": 0,
        "skipped_book_scene": 0,
        "skipped_symbolic": 0,
        "skipped_draft": 0,
        "skipped_system_meta": 0,
        "skipped_missing_timestamp": 0,
        "skipped_suspected_fanout": 0,
        "skipped_media_analysis": 0,
        "skipped_book_related": 0,
        "skipped_technical_only": 0,
        "skipped_untrusted_inferred": 0,
        "skipped_untrusted_truth": 0,
        "skipped_chat_mode": 0,
        "skipped_chat_domain": 0,
        "skipped_low_confidence": 0,
    }


class ExperienceStore(Store):
    def __init__(self, path: Path) -> None:
        super().__init__(path, EXPERIENCE_SQL, "experience_meta", schema_version("live_experience"))
        self._ensure_approved_experience_fts()

    def _ensure_approved_experience_fts(self) -> None:
        rows = self.con.execute(
            """SELECT e.experience_id,e.title,e.summary,e.truth_status
               FROM experiences e LEFT JOIN experience_fts_docs d
                 ON d.record_type='experience' AND d.record_id=e.experience_id
               WHERE e.status='active' AND d.rowid IS NULL"""
        ).fetchall()
        if not rows:
            return
        with self.transaction():
            for row in rows:
                domains = [str(item[0]) for item in self.con.execute(
                    "SELECT domain FROM experience_domains WHERE experience_id=? ORDER BY domain",
                    (row["experience_id"],),
                )]
                self._insert_fts(
                    "experience", str(row["experience_id"]), str(row["title"]),
                    str(row["truth_status"]), f"{row['title']}\n{row['summary']}\n{' '.join(domains)}",
                )

    def _insert_fts(self, record_type: str, record_id: str, title: str, truth: str, text: str) -> None:
        cursor = self.con.execute(
            "INSERT OR IGNORE INTO experience_fts_docs(record_type,record_id,title,truth_status) VALUES(?,?,?,?)",
            (record_type, record_id, title, truth),
        )
        if cursor.rowcount:
            self.con.execute("INSERT INTO experience_fts(rowid,text) VALUES(?,?)", (cursor.lastrowid, text))

    def _reject_existing(self, source_db: str, source_type: str, source_id: str, reason: str) -> bool:
        cursor = self.con.execute(
            """UPDATE candidates SET status='rejected_filter',reviewed_at_utc=?,
               reviewed_by='candidate_filter',review_reason=?
               WHERE source_database=? AND source_type=? AND source_record_id=?
                 AND status='pending_review'""",
            (now_utc(), reason, source_db, source_type, source_id),
        )
        return bool(cursor.rowcount)

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
            self._reject_existing(source_db, source_type, source_id, "filtered_noise:" + ",".join(reasons))
            return None, "filtered_noise"

        domain_list = sorted(set(domains))
        identity = sha_text(canonical_json([source_db, source_type, source_id, sha_text(text.lower())]))
        candidate_id = uid("experience-candidate", identity)
        payload = {**score, "noise_reasons": reasons, "length": len(text)}
        existing = self.con.execute(
            """SELECT candidate_id,identity_key,status FROM candidates
               WHERE source_database=? AND source_type=? AND source_record_id=?""",
            (source_db, source_type, source_id),
        ).fetchone()
        if existing is not None:
            existing_status = str(existing["status"])
            if existing_status == "approved":
                return str(existing["candidate_id"]), "duplicate"
            if existing_status == "pending_review" and str(existing["identity_key"]) == identity:
                return str(existing["candidate_id"]), "duplicate"
            reopened = existing_status == "rejected_filter"
            self.con.execute(
                """UPDATE candidates SET identity_key=?,source_sha256=?,title=?,summary=?,truth_status=?,
                   confidence=?,importance=?,domains_json=?,score_json=?,status='pending_review',created_at_utc=?,
                   reviewed_at_utc=NULL,reviewed_by=NULL,review_reason=NULL WHERE candidate_id=?""",
                (identity, source_hash, title, summary, truth, bounded(confidence, 0.55),
                 bounded(importance, 0.6), canonical_json(domain_list), canonical_json(payload),
                 now_utc(), existing["candidate_id"]),
            )
            return str(existing["candidate_id"]), "reopened" if reopened else "updated"

        with self.transaction():
            cursor = self.con.execute(
                """INSERT OR IGNORE INTO candidates(candidate_id,identity_key,source_database,source_type,
                   source_record_id,source_sha256,title,summary,truth_status,confidence,importance,domains_json,
                   score_json,status,created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'pending_review',?)""",
                (candidate_id, identity, source_db, source_type, source_id, source_hash, title, summary,
                 truth, bounded(confidence, 0.55), bounded(importance, 0.6), canonical_json(domain_list),
                 canonical_json(payload), now_utc()),
            )
            if cursor.rowcount:
                self._insert_fts("candidate", candidate_id, title, truth,
                                 f"{title}\n{summary}\n{' '.join(domain_list)}")
                return candidate_id, "inserted"
        return candidate_id, "duplicate"

    def from_journal(self, journal: JournalStore, limit: int | None = None) -> dict[str, Any]:
        sql = "SELECT * FROM journal_entries WHERE status='active' ORDER BY COALESCE(event_time_start,updated_at_utc),entry_id"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = journal.con.execute(sql, params).fetchall()
        counters = _counter_template()
        ids = []
        for row in rows:
            try:
                raw = json.loads(str(row["raw_json"]))
            except (TypeError, json.JSONDecodeError):
                raw = {}
            domains = set(infer_domains(f"{row['title']} {row['summary']} {row['content']}"))
            reason = _journal_skip_reason(row, raw if isinstance(raw, dict) else {}, domains)
            if reason:
                counters[reason] += 1
                if self._reject_existing(DATABASE_FILENAMES["journal"], "journal_entry", row["entry_id"], reason):
                    counters["rejected_existing"] += 1
                continue
            confidence = {
                "user_confirmed": 0.85,
                "source_recorded": 0.70,
                "inferred": 0.45,
            }.get(str(row["truth_status"]), 0.40)
            candidate_id, status = self.candidate(
                DATABASE_FILENAMES["journal"], "journal_entry", row["entry_id"], row["content_sha256"],
                row["title"], row["summary"], row["truth_status"], confidence,
                row["importance"], domains,
                {
                    "source": "journal",
                    "suspected_fanout": bool(row["suspected_fanout"]),
                    "timestamp_status": row["timestamp_status"],
                    "source_labels": _raw_labels(raw if isinstance(raw, dict) else {}),
                },
            )
            key = {
                "inserted": "inserted_candidates",
                "updated": "updated_candidates",
                "reopened": "reopened_candidates",
                "duplicate": "duplicates",
            }.get(status, "filtered_noise")
            counters[key] += 1
            if candidate_id and status in {"inserted", "updated", "reopened"}:
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
            counters = _counter_template()
            ids = []
            for segment in segments:
                reason = _chat_skip_reason(segment)
                if reason:
                    counters[reason] += 1
                    if self._reject_existing(
                        DATABASE_FILENAMES["archive_chats"], "conversation_segment",
                        segment["segment_id"], reason,
                    ):
                        counters["rejected_existing"] += 1
                    continue
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
                key = {
                    "inserted": "inserted_candidates",
                    "updated": "updated_candidates",
                    "reopened": "reopened_candidates",
                    "duplicate": "duplicates",
                }.get(status, "filtered_noise")
                counters[key] += 1
                if candidate_id and status in {"inserted", "updated", "reopened"}:
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
        if str(row["status"]) not in {"pending_review", "approved"}:
            raise ValueError(f"candidate is not approvable: {row['status']}")
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
            self._insert_fts(
                "experience", experience_id, row["title"], row["truth_status"],
                f"{row['title']}\n{row['summary']}\n{' '.join(domains)}",
            )
            self.con.execute("UPDATE candidates SET status='approved',reviewed_at_utc=?,reviewed_by=?,review_reason=? WHERE candidate_id=?",
                             (current, approved_by, reason, candidate_id))
        return {"ok": True, "candidate_id": candidate_id, "experience_id": experience_id,
                "approved_by": approved_by, "automatic_l2": False, "automatic_l3": False}

    def counts(self) -> dict[str, int]:
        return {
            "candidates": self.con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],
            "pending_review": self.con.execute("SELECT COUNT(*) FROM candidates WHERE status='pending_review'").fetchone()[0],
            "rejected_candidates": self.con.execute("SELECT COUNT(*) FROM candidates WHERE status='rejected_filter'").fetchone()[0],
            "experiences": self.con.execute("SELECT COUNT(*) FROM experiences").fetchone()[0],
            "source_links": self.con.execute("SELECT COUNT(*) FROM experience_sources").fetchone()[0],
        }

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        for fts_query in fts_queries(query):
            rows = self.con.execute(
                """SELECT d.record_type,d.record_id,d.title,d.truth_status,bm25(experience_fts) rank
                   FROM experience_fts
                   JOIN experience_fts_docs d ON d.rowid=experience_fts.rowid
                   JOIN experiences e ON d.record_type='experience' AND e.experience_id=d.record_id
                   WHERE experience_fts MATCH ? AND e.status='active'
                   ORDER BY rank LIMIT ?""", (fts_query, limit)).fetchall()
            if rows:
                return [dict(row) for row in rows]
        return []
