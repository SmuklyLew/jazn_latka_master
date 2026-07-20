from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import sqlite3

from latka_jazn.config import JaznConfig
from latka_jazn.memory.memory_tier_store import MemoryTierStore, WorkingMemoryBudget
from latka_jazn.memory.memory_tiers import (
    MemoryKind,
    MemoryTier,
    MemoryTruthStatus,
    SourceEvidence,
    WorkingMemoryRecord,
    deterministic_memory_id,
)
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("wake_state_runtime")
TRUTH_BOUNDARY = (
    "Wake state is a verified, bounded continuity packet. It may seed L1 and the model/host context, "
    "but it does not itself promote L2/L3 records or prove biological consciousness."
)


@dataclass(slots=True)
class WakeStateRuntimeStatus:
    schema_version: str
    status: str
    sidecar_db_path: str
    snapshot_id: str | None
    snapshot_sha256: str | None
    source_run_id: str | None
    validation_status: str | None
    context: dict[str, Any] | None
    l1_memory_id: str | None
    errors: list[str]
    truth_boundary: str = TRUTH_BOUNDARY

    @property
    def ok(self) -> bool:
        return self.status in {"ready", "hydrated"}

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    recent = snapshot.get("recent_events") if isinstance(snapshot.get("recent_events"), list) else []
    threads = snapshot.get("open_threads") if isinstance(snapshot.get("open_threads"), list) else []
    relationship = snapshot.get("relationship_digest") if isinstance(snapshot.get("relationship_digest"), dict) else {}
    truth = snapshot.get("truth_boundary_digest") if isinstance(snapshot.get("truth_boundary_digest"), dict) else {}
    policy = snapshot.get("namespace_policy") if isinstance(snapshot.get("namespace_policy"), dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "wake_state_schema_version": snapshot.get("schema_version"),
        "created_at_utc": snapshot.get("created_at_utc"),
        "identity_snapshot": snapshot.get("identity_snapshot") if isinstance(snapshot.get("identity_snapshot"), dict) else {},
        "relationship_digest": {
            "krzysztof_candidate_present": bool(relationship.get("krzysztof_candidate_present")),
            "krzysztof_private_namespace_allowed": bool(relationship.get("krzysztof_private_namespace_allowed")),
            "rule": relationship.get("rule"),
        },
        "truth_boundary_digest": truth,
        "namespace_policy": {
            "default_for_unknown_interlocutor": policy.get("default_for_unknown_interlocutor"),
            "private_namespace_requires_confirmed_actor": bool(policy.get("private_namespace_requires_confirmed_actor", True)),
            "namespace_counts": policy.get("namespace_counts") if isinstance(policy.get("namespace_counts"), dict) else {},
        },
        "recent_events": recent[:8],
        "open_threads": [str(item)[:320] for item in threads[:8]],
        "source_counts": snapshot.get("source_counts") if isinstance(snapshot.get("source_counts"), dict) else {},
        "source_run_id": snapshot.get("source_run_id"),
        "validation_status": snapshot.get("validation_status"),
        "truth_boundary": TRUTH_BOUNDARY,
    }


class WakeStateRuntimeBridge:
    def __init__(self, config: JaznConfig) -> None:
        self.config = config
        self.sidecar_path = config.normalization_sidecar_db_path
        self.tier_path = config.memory_tier_db_path

    def load(self) -> WakeStateRuntimeStatus:
        if not self.sidecar_path.is_file():
            return self._status("sidecar_missing", errors=[f"missing sidecar: {self.sidecar_path}"])
        try:
            con = sqlite3.connect(f"file:{self.sidecar_path.resolve().as_posix()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            try:
                integrity = str(con.execute("PRAGMA quick_check").fetchone()[0])
                fk_count = len(con.execute("PRAGMA foreign_key_check").fetchall())
                rows = con.execute(
                    "SELECT * FROM wake_state_snapshots WHERE active=1 ORDER BY created_at_utc DESC,rowid DESC"
                ).fetchall()
            finally:
                con.close()
            if integrity != "ok" or fk_count:
                return self._status("sidecar_invalid", errors=[f"integrity={integrity}; foreign_keys={fk_count}"])
            if len(rows) != 1:
                return self._status("active_snapshot_invalid", errors=[f"active_snapshot_count={len(rows)}"])
            row = rows[0]
            raw = str(row["snapshot_json"])
            digest = _sha256_text(raw)
            if digest != str(row["snapshot_sha256"]):
                return self._status("snapshot_hash_mismatch", errors=["snapshot_json sha256 mismatch"])
            snapshot = json.loads(raw)
            if not isinstance(snapshot, dict) or str(row["validation_status"]) != "valid":
                return self._status("snapshot_not_valid", errors=[f"validation_status={row['validation_status']}"])
            context = _bounded_snapshot(snapshot)
            return WakeStateRuntimeStatus(
                schema_version=SCHEMA_VERSION,
                status="ready",
                sidecar_db_path=str(self.sidecar_path),
                snapshot_id=str(row["snapshot_id"]),
                snapshot_sha256=str(row["snapshot_sha256"]),
                source_run_id=str(row["source_run_id"] or "") or None,
                validation_status=str(row["validation_status"]),
                context=context,
                l1_memory_id=None,
                errors=[],
            )
        except Exception as exc:
            return self._status("read_error", errors=[f"{type(exc).__name__}: {exc}"])

    def hydrate_l1(self, *, session_id: str, active_goal: str = "verified_wake_state") -> WakeStateRuntimeStatus:
        status = self.load()
        if not status.ok or not status.context or not status.snapshot_id or not status.snapshot_sha256:
            return status
        content = _canonical_json(status.context)
        now = datetime.now(timezone.utc)
        evidence = SourceEvidence(
            source_type="wake_state_snapshot",
            source_id=status.snapshot_id,
            source_sha256=status.snapshot_sha256,
            exact_excerpt_sha256=_sha256_text(content),
            timestamp_status="snapshot_recorded",
            metadata={
                "sidecar_db_path": str(self.sidecar_path),
                "source_run_id": status.source_run_id,
                "validation_status": status.validation_status,
                "schema_version": SCHEMA_VERSION,
            },
        )
        memory_id = deterministic_memory_id(
            tier=MemoryTier.WORKING,
            kind=MemoryKind.CONVERSATION_CONTEXT,
            content=content,
            domain="runtime_continuity",
            mode="wake_state",
            evidence=(evidence,),
        )
        record = WorkingMemoryRecord(
            memory_id=memory_id,
            tier=MemoryTier.WORKING,
            kind=MemoryKind.CONVERSATION_CONTEXT,
            content=content,
            content_sha256=_sha256_text(content),
            domain="runtime_continuity",
            mode="wake_state",
            truth_status=MemoryTruthStatus.SOURCE_RECORDED,
            confidence=0.9,
            importance=0.86,
            created_at_utc=now,
            updated_at_utc=now,
            evidence=(evidence,),
            tags=("wake_state", "verified", "l1", "runtime_v151"),
            session_id=session_id,
            turn_id=None,
            active_goal=active_goal,
            expires_on_session_end=True,
            checkpoint_allowed=True,
        )
        try:
            with MemoryTierStore(self.tier_path) as store:
                store.save_record(record, working_budget=WorkingMemoryBudget())
            status.status = "hydrated"
            status.l1_memory_id = memory_id
            return status
        except Exception as exc:
            status.status = "l1_hydration_failed"
            status.errors.append(f"{type(exc).__name__}: {exc}")
            return status

    def end_session(self, session_id: str) -> int:
        if not self.tier_path.is_file():
            return 0
        with MemoryTierStore(self.tier_path) as store:
            return store.end_session(session_id)

    def _status(self, status: str, *, errors: list[str]) -> WakeStateRuntimeStatus:
        return WakeStateRuntimeStatus(
            schema_version=SCHEMA_VERSION,
            status=status,
            sidecar_db_path=str(self.sidecar_path),
            snapshot_id=None,
            snapshot_sha256=None,
            source_run_id=None,
            validation_status=None,
            context=None,
            l1_memory_id=None,
            errors=errors,
        )
