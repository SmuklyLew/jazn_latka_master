from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
import json
import sqlite3

from latka_jazn.memory.memory_tier_schema import SCHEMA_SQL
from latka_jazn.memory.memory_tier_support import (
    WorkingMemoryBudget,
    WriteSummary,
    iso,
    json_text,
    record_from_dict,
)
from latka_jazn.memory.memory_tiers import (
    LongTermMemoryRecord,
    MemoryRecord,
    MemoryTier,
    ShortTermMemoryRecord,
    WorkingMemoryRecord,
)
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_tier_store")


class MemoryTierCoreStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 30_000, synchronous: str = "FULL") -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path, timeout=max(1.0, busy_timeout_ms / 1000), isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.execute(f"PRAGMA busy_timeout={max(1000, int(busy_timeout_ms))}")
        self.con.execute("PRAGMA journal_mode=WAL")
        selected_sync = synchronous.upper()
        if selected_sync not in {"FULL", "NORMAL"}:
            raise ValueError("synchronous must be FULL or NORMAL")
        self.con.execute(f"PRAGMA synchronous={selected_sync}")
        self.con.execute("PRAGMA temp_store=FILE")
        self.con.execute("PRAGMA cache_size=-16384")
        self.con.executescript(SCHEMA_SQL)
        self.con.execute("INSERT OR REPLACE INTO memory_store_meta(key,value) VALUES('schema_version',?)", (SCHEMA_VERSION,))
        self.con.execute(
            "INSERT OR REPLACE INTO memory_store_meta(key,value) VALUES('truth_boundary',?)",
            ("L1/L2/L3 są rozdzielone. L2 nie jest promocją L3, a outbox nie dowodzi wykonania efektu.",),
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self.con.in_transaction:
            raise RuntimeError("nested memory transactions are forbidden")
        self.con.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.con.rollback()
            raise
        else:
            self.con.commit()

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def save_record(self, record: MemoryRecord, *, working_budget: WorkingMemoryBudget | None = None) -> WriteSummary:
        with self.transaction():
            summary = self.write_record(record)
            evicted = 0
            if isinstance(record, WorkingMemoryRecord):
                evicted = self.enforce_working_budget(record.session_id, working_budget or WorkingMemoryBudget())
            return WriteSummary(records_written=summary.records_written,
                                evidence_written=summary.evidence_written,
                                working_records_evicted=evicted)

    def write_record(self, record: MemoryRecord) -> WriteSummary:
        self._require_transaction()
        payload = record.to_dict()
        existing = self.con.execute(
            "SELECT tier,content_sha256 FROM memory_records WHERE memory_id=?", (record.memory_id,),
        ).fetchone()
        if existing and (str(existing["tier"]) != record.tier.value
                         or str(existing["content_sha256"]) != record.content_sha256):
            raise ValueError("memory_id collision with different tier or content")
        self.con.execute(
            """INSERT INTO memory_records(
               memory_id,tier,kind,content,content_sha256,domain,mode,truth_status,
               confidence,importance,created_at_utc,updated_at_utc,tags_json,record_json,active)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(memory_id) DO UPDATE SET
                 confidence=excluded.confidence,importance=excluded.importance,
                 updated_at_utc=excluded.updated_at_utc,tags_json=excluded.tags_json,
                 record_json=excluded.record_json,active=excluded.active""",
            (record.memory_id, record.tier.value, record.kind.value, record.content,
             record.content_sha256, record.domain, record.mode, record.truth_status.value,
             record.confidence, record.importance, iso(record.created_at_utc),
             iso(record.updated_at_utc), json_text(record.tags), json_text(payload),
             int(not isinstance(record, LongTermMemoryRecord) or record.active)),
        )
        evidence_written = 0
        for item in record.evidence:
            cursor = self.con.execute(
                """INSERT OR IGNORE INTO memory_evidence(
                   memory_id,evidence_key,source_type,source_id,evidence_json) VALUES(?,?,?,?,?)""",
                (record.memory_id, item.evidence_key, item.source_type, item.source_id,
                 json_text(item.to_dict())),
            )
            evidence_written += max(0, cursor.rowcount)
        if isinstance(record, WorkingMemoryRecord):
            self.con.execute(
                """INSERT INTO working_memory_index(
                   memory_id,session_id,turn_id,active_goal,expires_on_session_end,checkpoint_allowed)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(memory_id) DO UPDATE SET
                   session_id=excluded.session_id,turn_id=excluded.turn_id,
                   active_goal=excluded.active_goal,expires_on_session_end=excluded.expires_on_session_end,
                   checkpoint_allowed=excluded.checkpoint_allowed""",
                (record.memory_id, record.session_id, record.turn_id, record.active_goal,
                 int(record.expires_on_session_end), int(record.checkpoint_allowed)),
            )
        elif isinstance(record, ShortTermMemoryRecord):
            self.con.execute(
                """INSERT INTO short_term_memory_index(
                   memory_id,expires_at_utc,reinforcement_count,last_reinforced_at_utc,
                   reinforcement_evidence_keys_json,promotion_status) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(memory_id) DO UPDATE SET expires_at_utc=excluded.expires_at_utc,
                   reinforcement_count=excluded.reinforcement_count,
                   last_reinforced_at_utc=excluded.last_reinforced_at_utc,
                   reinforcement_evidence_keys_json=excluded.reinforcement_evidence_keys_json,
                   promotion_status=excluded.promotion_status""",
                (record.memory_id, iso(record.expires_at_utc), record.reinforcement_count,
                 iso(record.last_reinforced_at_utc) if record.last_reinforced_at_utc else None,
                 json_text(record.reinforcement_evidence_keys), record.promotion_status.value),
            )
        elif isinstance(record, LongTermMemoryRecord):
            self.con.execute(
                """INSERT INTO long_term_memory_index(
                   memory_id,promoted_at_utc,promoted_from_memory_id,promotion_decision_id,
                   approved_by,promotion_reason,revision,invalidated_at_utc,invalidation_reason)
                   VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(memory_id) DO UPDATE SET
                   revision=excluded.revision,invalidated_at_utc=excluded.invalidated_at_utc,
                   invalidation_reason=excluded.invalidation_reason""",
                (record.memory_id, iso(record.promoted_at_utc), record.promoted_from_memory_id,
                 record.promotion_decision_id, record.approved_by, record.promotion_reason,
                 record.revision, iso(record.invalidated_at_utc) if record.invalidated_at_utc else None,
                 record.invalidation_reason),
            )
        else:
            raise TypeError(f"unsupported memory record type: {type(record).__name__}")
        return WriteSummary(records_written=1, evidence_written=evidence_written)

    def enforce_working_budget(self, session_id: str, budget: WorkingMemoryBudget) -> int:
        self._require_transaction()
        oversized = self.con.execute(
            """SELECT r.memory_id FROM memory_records r JOIN working_memory_index w ON w.memory_id=r.memory_id
               WHERE w.session_id=? AND length(r.content)>?""",
            (session_id, budget.max_record_chars),
        ).fetchall()
        if oversized:
            raise ValueError("working-memory record exceeds max_record_chars")
        rows = self.con.execute(
            """SELECT r.memory_id,length(r.content) AS chars,r.importance,r.updated_at_utc
               FROM memory_records r JOIN working_memory_index w ON w.memory_id=r.memory_id
               WHERE w.session_id=? AND r.active=1
               ORDER BY r.importance ASC,r.updated_at_utc ASC,r.memory_id ASC""",
            (session_id,),
        ).fetchall()
        count = len(rows)
        total_chars = sum(int(row["chars"]) for row in rows)
        evict: list[str] = []
        for row in rows:
            if count <= budget.max_records_per_session and total_chars <= budget.max_total_chars_per_session:
                break
            evict.append(str(row["memory_id"]))
            count -= 1
            total_chars -= int(row["chars"])
        if evict:
            placeholders = ",".join("?" for _ in evict)
            self.con.execute(f"DELETE FROM memory_records WHERE memory_id IN ({placeholders})", evict)
        return len(evict)

    def end_session(self, session_id: str) -> int:
        with self.transaction():
            rows = self.con.execute(
                "SELECT memory_id FROM working_memory_index WHERE session_id=? AND expires_on_session_end=1",
                (session_id,),
            ).fetchall()
            ids = [str(row["memory_id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self.con.execute(f"DELETE FROM memory_records WHERE memory_id IN ({placeholders})", ids)
            return len(ids)

    def get_record(self, memory_id: str) -> MemoryRecord | None:
        row = self.con.execute("SELECT record_json FROM memory_records WHERE memory_id=?", (memory_id,)).fetchone()
        return record_from_dict(json.loads(row["record_json"])) if row else None

    def list_records(self, *, tier: MemoryTier | None = None, session_id: str | None = None) -> list[MemoryRecord]:
        if session_id is not None:
            rows = self.con.execute(
                """SELECT r.record_json FROM memory_records r JOIN working_memory_index w ON w.memory_id=r.memory_id
                   WHERE w.session_id=? ORDER BY r.updated_at_utc,r.memory_id""", (session_id,),
            ).fetchall()
        elif tier is not None:
            rows = self.con.execute(
                "SELECT record_json FROM memory_records WHERE tier=? ORDER BY updated_at_utc,memory_id",
                (tier.value,),
            ).fetchall()
        else:
            rows = self.con.execute(
                "SELECT record_json FROM memory_records ORDER BY tier,updated_at_utc,memory_id"
            ).fetchall()
        return [record_from_dict(json.loads(row["record_json"])) for row in rows]

    def stats(self) -> dict[str, int]:
        tables = ("memory_records", "memory_evidence", "working_memory_index",
                  "short_term_memory_index", "long_term_memory_index", "promotion_requests",
                  "promotion_decisions", "promotion_ledger", "memory_outbox", "session_checkpoints")
        return {name: int(self.con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in tables}

    def validate(self, *, full: bool = True) -> dict[str, Any]:
        pragma = "integrity_check" if full else "quick_check"
        integrity = str(self.con.execute(f"PRAGMA {pragma}").fetchone()[0])
        foreign_keys = list(self.con.execute("PRAGMA foreign_key_check"))
        auto_commit = int(self.con.execute(
            "SELECT COUNT(*) FROM promotion_decisions WHERE automatic_commit_allowed<>0"
        ).fetchone()[0])
        return {"ok": integrity == "ok" and not foreign_keys and auto_commit == 0,
                "integrity_check": integrity,
                "foreign_key_error_count": len(foreign_keys),
                "automatic_commit_violation_count": auto_commit,
                "stats": self.stats(), "path": str(self.path)}

    def _require_transaction(self) -> None:
        if not self.con.in_transaction:
            raise RuntimeError("write operation requires an active memory transaction")
