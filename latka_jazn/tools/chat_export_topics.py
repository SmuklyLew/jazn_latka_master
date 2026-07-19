from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
import json
import uuid

from latka_jazn.memory.conversation_domains import ConversationDomainClassifier
from latka_jazn.tools.chat_export_reader import build_conversation_graph
from latka_jazn.tools.chat_export_store import ChatExportArchiveStore
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("chat_export_topics")

TOPIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_topic_profiles(
  conversation_id TEXT PRIMARY KEY,
  source_semantic_tree_sha256 TEXT NOT NULL,
  classifier_version TEXT NOT NULL,
  primary_domain TEXT NOT NULL,
  secondary_domains_json TEXT NOT NULL DEFAULT '[]',
  primary_mode TEXT NOT NULL,
  confidence REAL NOT NULL,
  segment_count INTEGER NOT NULL,
  analysed_at_utc TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  scores_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);
CREATE TABLE IF NOT EXISTS conversation_segments(
  segment_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  start_node_id TEXT NOT NULL,
  end_node_id TEXT NOT NULL,
  primary_domain TEXT NOT NULL,
  mode TEXT NOT NULL,
  truth_status TEXT NOT NULL,
  confidence REAL NOT NULL,
  message_count INTEGER NOT NULL,
  char_count INTEGER NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  source_branch TEXT NOT NULL DEFAULT 'current_path',
  classifier_version TEXT NOT NULL,
  manual_override INTEGER NOT NULL DEFAULT 0,
  created_at_utc TEXT NOT NULL,
  UNIQUE(conversation_id,ordinal,source_branch),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_conversation_segments_topic
  ON conversation_segments(primary_domain,mode,truth_status);
CREATE TABLE IF NOT EXISTS memory_review_queue(
  candidate_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  candidate_type TEXT NOT NULL,
  requested_domain TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending_review',
  requested_at_utc TEXT NOT NULL,
  reviewed_by TEXT,
  reviewed_at_utc TEXT,
  promotion_target TEXT,
  decision_note TEXT,
  source_refs_json TEXT NOT NULL,
  UNIQUE(segment_id,candidate_type),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_review_queue_status
  ON memory_review_queue(status,requested_domain,candidate_type);
"""


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _truth_status(mode: str) -> str:
    return {
        "scene_roleplay": "book_scene",
        "manuscript_draft": "draft",
        "symbolic_imagination": "symbolic",
    }.get(mode, "source_recorded")


@dataclass(slots=True, frozen=True)
class TopicSegment:
    segment_id: str
    conversation_id: str
    ordinal: int
    start_node_id: str
    end_node_id: str
    primary_domain: str
    mode: str
    truth_status: str
    confidence: float
    message_count: int
    char_count: int
    evidence: tuple[str, ...]
    source_branch: str = "current_path"
    classifier_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class TopicAnalysis:
    conversation_id: str
    source_semantic_tree_sha256: str
    primary_domain: str
    secondary_domains: tuple[str, ...]
    primary_mode: str
    confidence: float
    segments: tuple[TopicSegment, ...]
    evidence: tuple[str, ...]
    scores: dict[str, float]
    classifier_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConversationTopicSegmenter:
    """Segment a visible conversation path without changing archive truth status."""

    def __init__(self, *, max_segment_chars: int = 16_000) -> None:
        self.classifier = ConversationDomainClassifier()
        self.max_segment_chars = max(1000, int(max_segment_chars))

    def analyse_payload(self, payload: dict[str, Any]) -> TopicAnalysis:
        graph = build_conversation_graph(payload)
        index = graph.node_index()
        current_nodes = [index[node_id] for node_id in graph.current_path if node_id in index]
        message_nodes = [node for node in current_nodes if node.role in {"user", "assistant"} and node.text]
        segments: list[TopicSegment] = []
        current: list[tuple[Any, Any]] = []
        current_chars = 0

        def flush() -> None:
            nonlocal current, current_chars
            if not current:
                return
            first_node, first_report = current[0]
            last_node = current[-1][0]
            confidence = round(sum(item[1].confidence for item in current) / len(current), 3)
            evidence = tuple(sorted({entry for _, report in current for entry in report.evidence})[:32])
            ordinal = len(segments)
            seed = f"{graph.conversation_id}|{first_node.node_id}|{last_node.node_id}|{SCHEMA_VERSION}"
            segment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
            segments.append(TopicSegment(
                segment_id=segment_id,
                conversation_id=graph.conversation_id,
                ordinal=ordinal,
                start_node_id=first_node.node_id,
                end_node_id=last_node.node_id,
                primary_domain=first_report.primary_domain,
                mode=first_report.mode,
                truth_status=_truth_status(first_report.mode),
                confidence=confidence,
                message_count=len(current),
                char_count=current_chars,
                evidence=evidence,
            ))
            current = []
            current_chars = 0

        previous_user_text: str | None = None
        for node in message_nodes:
            report = self.classifier.classify(
                node.text,
                role=node.role or "unknown",
                title=graph.title,
                metadata={"content_type": node.content_type},
                context=previous_user_text if node.role == "assistant" else None,
            )
            if node.role == "user":
                previous_user_text = node.text
            if current:
                prior = current[0][1]
                changed = report.primary_domain != prior.primary_domain or report.mode != prior.mode
                too_large = current_chars + len(node.text) > self.max_segment_chars
                if changed or too_large:
                    flush()
            current.append((node, report))
            current_chars += len(node.text)
        flush()

        domain_weights: dict[str, float] = {}
        mode_weights: dict[str, float] = {}
        evidence: set[str] = set()
        for segment in segments:
            weight = max(1.0, float(segment.char_count)) * max(0.2, segment.confidence)
            domain_weights[segment.primary_domain] = domain_weights.get(segment.primary_domain, 0.0) + weight
            mode_weights[segment.mode] = mode_weights.get(segment.mode, 0.0) + weight
            evidence.update(segment.evidence)
        if domain_weights:
            domains = sorted(domain_weights, key=lambda key: (-domain_weights[key], key))
            modes = sorted(mode_weights, key=lambda key: (-mode_weights[key], key))
            primary_domain = domains[0]
            secondary = tuple(domains[1:4])
            primary_mode = modes[0]
            total = sum(domain_weights.values())
            confidence = round(domain_weights[primary_domain] / total if total else 0.0, 3)
            scores = {key: round(value / total, 4) for key, value in domain_weights.items()} if total else {}
        else:
            primary_domain = "unknown"
            secondary = ()
            primary_mode = "unknown"
            confidence = 0.0
            scores = {"unknown": 1.0}
        return TopicAnalysis(
            conversation_id=graph.conversation_id,
            source_semantic_tree_sha256=graph.semantic_tree_sha256,
            primary_domain=primary_domain,
            secondary_domains=secondary,
            primary_mode=primary_mode,
            confidence=confidence,
            segments=tuple(segments),
            evidence=tuple(sorted(evidence)[:64]),
            scores=scores,
        )


class ChatExportTopicStore:
    """Derived topic index and manual review queue for an archive database."""

    def __init__(self, database: str | Path) -> None:
        self.archive = ChatExportArchiveStore(database)
        self.con = self.archive.con
        self.con.executescript(TOPIC_SCHEMA)
        self.segmenter = ConversationTopicSegmenter()

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> "ChatExportTopicStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def analyse_conversation(self, conversation_id: str, *, force: bool = False) -> TopicAnalysis | None:
        row = self.con.execute(
            "SELECT semantic_tree_sha256 FROM conversations WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        existing = self.con.execute(
            "SELECT source_semantic_tree_sha256,classifier_version FROM conversation_topic_profiles WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if (
            not force
            and existing is not None
            and str(existing["source_semantic_tree_sha256"]) == str(row["semantic_tree_sha256"])
            and str(existing["classifier_version"]) == SCHEMA_VERSION
        ):
            return None
        payload = self.archive.conversation_payload(conversation_id)
        if payload is None:
            return None
        analysis = self.segmenter.analyse_payload(payload)
        with self.archive.transaction():
            self._write_analysis(analysis)
        return analysis

    def analyse_all(self, *, force: bool = False, limit: int | None = None) -> dict[str, Any]:
        sql = "SELECT conversation_id FROM conversations ORDER BY COALESCE(update_time,create_time),conversation_id"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (max(0, int(limit)),)
        conversation_ids = [str(row[0]) for row in self.con.execute(sql, params)]
        analysed = skipped = segment_count = 0
        for conversation_id in conversation_ids:
            result = self.analyse_conversation(conversation_id, force=force)
            if result is None:
                skipped += 1
            else:
                analysed += 1
                segment_count += len(result.segments)
        return {
            "ok": True,
            "classifier_version": SCHEMA_VERSION,
            "conversation_count": len(conversation_ids),
            "analysed": analysed,
            "skipped_fresh": skipped,
            "segment_count": segment_count,
            "truth_boundary": "Analiza tematów nie promuje segmentów do pamięci długotrwałej.",
        }

    def _write_analysis(self, analysis: TopicAnalysis) -> None:
        now = _utc_now()
        self.con.execute(
            """UPDATE memory_review_queue SET status='stale_reanalysis',reviewed_at_utc=?
                 WHERE segment_id IN (SELECT segment_id FROM conversation_segments WHERE conversation_id=?)
                   AND status='pending_review'""",
            (now, analysis.conversation_id),
        )
        self.con.execute("DELETE FROM conversation_segments WHERE conversation_id=?", (analysis.conversation_id,))
        self.con.execute(
            """INSERT INTO conversation_topic_profiles(
               conversation_id,source_semantic_tree_sha256,classifier_version,primary_domain,
               secondary_domains_json,primary_mode,confidence,segment_count,analysed_at_utc,
               evidence_json,scores_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(conversation_id) DO UPDATE SET
               source_semantic_tree_sha256=excluded.source_semantic_tree_sha256,
               classifier_version=excluded.classifier_version,primary_domain=excluded.primary_domain,
               secondary_domains_json=excluded.secondary_domains_json,primary_mode=excluded.primary_mode,
               confidence=excluded.confidence,segment_count=excluded.segment_count,
               analysed_at_utc=excluded.analysed_at_utc,evidence_json=excluded.evidence_json,
               scores_json=excluded.scores_json""",
            (
                analysis.conversation_id, analysis.source_semantic_tree_sha256, SCHEMA_VERSION,
                analysis.primary_domain, json.dumps(list(analysis.secondary_domains), ensure_ascii=False),
                analysis.primary_mode, analysis.confidence, len(analysis.segments), now,
                json.dumps(list(analysis.evidence), ensure_ascii=False),
                json.dumps(analysis.scores, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.con.executemany(
            """INSERT INTO conversation_segments(
               segment_id,conversation_id,ordinal,start_node_id,end_node_id,primary_domain,mode,
               truth_status,confidence,message_count,char_count,evidence_json,source_branch,
               classifier_version,manual_override,created_at_utc)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
            [
                (
                    segment.segment_id, segment.conversation_id, segment.ordinal,
                    segment.start_node_id, segment.end_node_id, segment.primary_domain,
                    segment.mode, segment.truth_status, segment.confidence,
                    segment.message_count, segment.char_count,
                    json.dumps(list(segment.evidence), ensure_ascii=False),
                    segment.source_branch, SCHEMA_VERSION, now,
                )
                for segment in analysis.segments
            ],
        )

    def summary(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.con.execute(
            """SELECT primary_domain,mode,truth_status,COUNT(*) AS segments,
                      SUM(message_count) AS messages,SUM(char_count) AS characters,
                      ROUND(AVG(confidence),3) AS avg_confidence
                 FROM conversation_segments
                GROUP BY primary_domain,mode,truth_status
                ORDER BY segments DESC,primary_domain,mode"""
        )]

    def queue_domains(self, domains: Iterable[str], *, reason: str, candidate_type: str = "long_term_review") -> int:
        selected = {str(domain).strip() for domain in domains if str(domain).strip()}
        if not selected:
            return 0
        placeholders = ",".join("?" for _ in selected)
        rows = self.con.execute(
            f"""SELECT segment_id,conversation_id,primary_domain,start_node_id,end_node_id,
                       truth_status,classifier_version
                  FROM conversation_segments WHERE primary_domain IN ({placeholders})""",
            tuple(sorted(selected)),
        ).fetchall()
        inserted = 0
        with self.archive.transaction():
            for row in rows:
                candidate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{row['segment_id']}|{candidate_type}"))
                source_refs = {
                    "conversation_id": row["conversation_id"],
                    "segment_id": row["segment_id"],
                    "start_node_id": row["start_node_id"],
                    "end_node_id": row["end_node_id"],
                    "truth_status": row["truth_status"],
                    "classifier_version": row["classifier_version"],
                }
                cursor = self.con.execute(
                    """INSERT OR IGNORE INTO memory_review_queue(
                       candidate_id,conversation_id,segment_id,candidate_type,requested_domain,
                       reason,status,requested_at_utc,source_refs_json)
                       VALUES(?,?,?,?,?,?,'pending_review',?,?)""",
                    (
                        candidate_id, row["conversation_id"], row["segment_id"], candidate_type,
                        row["primary_domain"], reason, _utc_now(),
                        json.dumps(source_refs, ensure_ascii=False, sort_keys=True),
                    ),
                )
                inserted += max(0, cursor.rowcount)
        return inserted

    def review_queue(self, *, status: str | None = "pending_review", limit: int = 200) -> list[dict[str, Any]]:
        if status:
            rows = self.con.execute(
                "SELECT * FROM memory_review_queue WHERE status=? ORDER BY requested_at_utc,candidate_id LIMIT ?",
                (status, max(1, int(limit))),
            )
        else:
            rows = self.con.execute(
                "SELECT * FROM memory_review_queue ORDER BY requested_at_utc,candidate_id LIMIT ?",
                (max(1, int(limit)),),
            )
        return [dict(row) for row in rows]
