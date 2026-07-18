from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
import hashlib
import json
import sqlite3

from latka_jazn.memory.memory_tier_store import MemoryTierStore
from latka_jazn.memory.memory_tiers import (
    MemoryKind,
    MemoryTruthStatus,
    ShortTermMemoryPolicy,
    ShortTermMemoryRecord,
    SourceEvidence,
)
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("legacy_fanout_migration")
MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS legacy_migration_runs(
  run_id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  scanned_at_utc TEXT NOT NULL,
  inventory_json TEXT NOT NULL,
  UNIQUE(source_sha256)
);
CREATE TABLE IF NOT EXISTS legacy_migration_candidates(
  candidate_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  legacy_table TEXT NOT NULL,
  legacy_record_id TEXT NOT NULL,
  related_group_id TEXT,
  memory_kind TEXT NOT NULL,
  content TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  truth_status TEXT NOT NULL,
  confidence REAL NOT NULL,
  importance REAL NOT NULL,
  suspected_fanout INTEGER NOT NULL,
  review_status TEXT NOT NULL CHECK(review_status IN ('pending_review','approved_for_l2','rejected','migrated_l2')),
  candidate_json TEXT NOT NULL,
  UNIQUE(run_id,legacy_table,legacy_record_id),
  FOREIGN KEY(run_id) REFERENCES legacy_migration_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_legacy_candidate_review
  ON legacy_migration_candidates(review_status,suspected_fanout,legacy_table);
"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True, frozen=True)
class LegacyMigrationCandidate:
    candidate_id: str
    legacy_table: str
    legacy_record_id: str
    related_group_id: str | None
    memory_kind: MemoryKind
    content: str
    truth_status: MemoryTruthStatus
    confidence: float
    importance: float
    suspected_fanout: bool
    source_path: str
    source_sha256: str
    raw_record: dict[str, Any]
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = (
        "Kandydat migracji jest zapisem do przeglądu. Nie dowodzi niezależnego wspomnienia "
        "i nie jest automatycznie L2 ani L3."
    )

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["memory_kind"] = self.memory_kind.value
        data["truth_status"] = self.truth_status.value
        data["content_sha256"] = self.content_sha256
        return data

    def to_short_term(self, *, approved_by: str, now: datetime | None = None) -> ShortTermMemoryRecord:
        if not approved_by.strip():
            raise ValueError("approved_by is required for migration to L2")
        journal_source = self.legacy_table == "dziennik_entries"
        source_type = "legacy_dziennik_json" if journal_source else "legacy_memory_sqlite"
        domain = "legacy_journal_migration" if journal_source else "legacy_migration"
        evidence = SourceEvidence(
            source_type=source_type,
            source_id=f"{self.legacy_table}:{self.legacy_record_id}",
            source_sha256=self.source_sha256,
            metadata={
                "source_path": self.source_path,
                "migration_candidate_id": self.candidate_id,
                "suspected_fanout": self.suspected_fanout,
                "approved_by": approved_by,
            },
        )
        policy = ShortTermMemoryPolicy(default_ttl=timedelta(days=30), max_lifetime=timedelta(days=180))
        return policy.create(
            kind=self.memory_kind,
            content=self.content,
            domain=domain,
            mode="source_review",
            truth_status=self.truth_status,
            confidence=self.confidence,
            importance=self.importance,
            evidence=(evidence,),
            created_at_utc=now or datetime.now(timezone.utc),
            tags=(
                "legacy_migration",
                self.legacy_table,
                "suspected_fanout" if self.suspected_fanout else "independent_candidate",
            ),
        )


class LegacyMemoryScanner:
    TABLES = (
        "episodic_memories", "reflection_entries", "semantic_facts",
        "procedural_rules", "truth_audits",
    )

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.source_sha256 = _sha256_file(self.path)
        self.con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        tables = {str(row[0]) for row in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [table for table in self.TABLES if table not in tables]
        if missing:
            raise sqlite3.DatabaseError(f"legacy memory tables are missing: {', '.join(missing)}")

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "LegacyMemoryScanner":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def inventory(self) -> dict[str, int]:
        return {table: int(self.con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in self.TABLES}

    def candidates(self) -> Iterator[LegacyMigrationCandidate]:
        for row in self.con.execute("SELECT * FROM episodic_memories ORDER BY created_at_utc,episode_id"):
            raw = dict(row)
            yield self._candidate(
                "episodic_memories", str(row["episode_id"]), str(row["scene"]), MemoryKind.EPISODIC,
                related_group_id=str(row["episode_id"]), confidence=float(row["confidence"]),
                importance=0.65, suspected_fanout=False, raw=raw,
                truth_status=self._truth_status(row["grounding"]),
            )
        for row in self.con.execute("SELECT * FROM reflection_entries ORDER BY created_at_utc,reflection_id"):
            raw = dict(row)
            text = str(row["meaning_for_latka"])
            templated = text.startswith("Ten zapis runtime jest ważny, bo:")
            yield self._candidate(
                "reflection_entries", str(row["reflection_id"]), text, MemoryKind.REFLECTION,
                related_group_id=str(row["episode_id"]) if row["episode_id"] else None,
                confidence=float(row["confidence"]), importance=0.5,
                suspected_fanout=bool(row["episode_id"]) or templated, raw=raw,
                truth_status=MemoryTruthStatus.INFERRED,
            )
        for row in self.con.execute("SELECT * FROM semantic_facts ORDER BY created_at_utc,fact_id"):
            raw = dict(row)
            content = f"{row['subject']} {row['predicate']} {row['value']}"
            yield self._candidate(
                "semantic_facts", str(row["fact_id"]), content, MemoryKind.SEMANTIC,
                related_group_id=None, confidence=float(row["confidence"]), importance=0.7,
                suspected_fanout=False, raw=raw, truth_status=MemoryTruthStatus.INFERRED,
            )
        for row in self.con.execute("SELECT * FROM procedural_rules ORDER BY created_at_utc,rule_id"):
            raw = dict(row)
            content = f"Gdy: {row['trigger']}\nZrób: {row['action']}\nPowód: {row['reason']}"
            yield self._candidate(
                "procedural_rules", str(row["rule_id"]), content, MemoryKind.PROCEDURAL,
                related_group_id=None, confidence=0.75, importance=min(1.0, float(row["priority"]) / 100),
                suspected_fanout=False, raw=raw, truth_status=MemoryTruthStatus.SOURCE_RECORDED,
            )

    def _candidate(
        self, table: str, record_id: str, content: str, kind: MemoryKind, *,
        related_group_id: str | None, confidence: float, importance: float,
        suspected_fanout: bool, raw: dict[str, Any], truth_status: MemoryTruthStatus,
    ) -> LegacyMigrationCandidate:
        candidate_id = hashlib.sha256(
            f"{self.source_sha256}|{table}|{record_id}|{hashlib.sha256(content.encode()).hexdigest()}".encode()
        ).hexdigest()
        return LegacyMigrationCandidate(
            candidate_id, table, record_id, related_group_id, kind, content, truth_status,
            max(0.0, min(1.0, confidence)), max(0.0, min(1.0, importance)),
            suspected_fanout, str(self.path), self.source_sha256, raw,
        )

    @staticmethod
    def _truth_status(grounding: Any) -> MemoryTruthStatus:
        value = str(grounding or "").lower()
        if value in {"verified", "user_confirmed"}:
            return MemoryTruthStatus.USER_CONFIRMED
        if value in {"recognized", "recovered", "source_recorded"}:
            return MemoryTruthStatus.SOURCE_RECORDED
        return MemoryTruthStatus.INFERRED


class LegacyFanoutMigrationStore:
    def __init__(self, store: MemoryTierStore) -> None:
        self.store = store
        self.store.con.executescript(MIGRATION_SQL)

    def stage_scan(self, scanner: Any) -> dict[str, int]:
        inventory = scanner.inventory()
        run_id = hashlib.sha256(f"{scanner.source_sha256}|{SCHEMA_VERSION}".encode()).hexdigest()
        inserted = fanout = 0
        with self.store.transaction():
            self.store.con.execute(
                """INSERT OR IGNORE INTO legacy_migration_runs(
                   run_id,source_path,source_sha256,scanned_at_utc,inventory_json) VALUES(?,?,?,?,?)""",
                (run_id, str(scanner.path), scanner.source_sha256, _utc_now(),
                 json.dumps(inventory, ensure_ascii=False, sort_keys=True)),
            )
            for candidate in scanner.candidates():
                cursor = self.store.con.execute(
                    """INSERT OR IGNORE INTO legacy_migration_candidates(
                       candidate_id,run_id,legacy_table,legacy_record_id,related_group_id,memory_kind,
                       content,content_sha256,truth_status,confidence,importance,suspected_fanout,
                       review_status,candidate_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'pending_review',?)""",
                    (candidate.candidate_id, run_id, candidate.legacy_table, candidate.legacy_record_id,
                     candidate.related_group_id, candidate.memory_kind.value, candidate.content,
                     candidate.content_sha256, candidate.truth_status.value, candidate.confidence,
                     candidate.importance, int(candidate.suspected_fanout),
                     json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True)),
                )
                inserted += max(0, cursor.rowcount)
                fanout += int(candidate.suspected_fanout and cursor.rowcount > 0)
        return {"inserted_candidates": inserted, "suspected_fanout": fanout, **inventory}

    def list_candidates(self, *, status: str = "pending_review") -> list[LegacyMigrationCandidate]:
        rows = self.store.con.execute(
            "SELECT candidate_json FROM legacy_migration_candidates WHERE review_status=? ORDER BY suspected_fanout,legacy_table,legacy_record_id",
            (status,),
        ).fetchall()
        return [self._from_dict(json.loads(row["candidate_json"])) for row in rows]

    def approve_to_l2(self, candidate_id: str, *, approved_by: str, now: datetime | None = None) -> ShortTermMemoryRecord:
        row = self.store.con.execute(
            "SELECT candidate_json,review_status FROM legacy_migration_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        if row["review_status"] not in {"pending_review", "approved_for_l2"}:
            raise ValueError(f"candidate status does not permit L2 migration: {row['review_status']}")
        candidate = self._from_dict(json.loads(row["candidate_json"]))
        record = candidate.to_short_term(approved_by=approved_by, now=now)
        with self.store.transaction():
            self.store.write_record(record)
            self.store.con.execute(
                "UPDATE legacy_migration_candidates SET review_status='migrated_l2' WHERE candidate_id=?",
                (candidate_id,),
            )
            self.store.write_outbox(
                event_type="memory.legacy_migrated_l2",
                aggregate_id=record.memory_id,
                payload={"candidate_id": candidate_id, "memory_id": record.memory_id, "approved_by": approved_by},
                idempotency_key=f"legacy-l2:{candidate_id}:{record.memory_id}",
            )
        return record

    @staticmethod
    def _from_dict(data: dict[str, Any]) -> LegacyMigrationCandidate:
        return LegacyMigrationCandidate(
            candidate_id=data["candidate_id"], legacy_table=data["legacy_table"],
            legacy_record_id=data["legacy_record_id"], related_group_id=data.get("related_group_id"),
            memory_kind=MemoryKind(data["memory_kind"]), content=data["content"],
            truth_status=MemoryTruthStatus(data["truth_status"]), confidence=float(data["confidence"]),
            importance=float(data["importance"]), suspected_fanout=bool(data["suspected_fanout"]),
            source_path=data["source_path"], source_sha256=data["source_sha256"],
            raw_record=dict(data.get("raw_record") or {}),
        )
