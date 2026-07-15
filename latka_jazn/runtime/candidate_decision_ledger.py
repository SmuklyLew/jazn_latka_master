from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from latka_jazn.memory.sqlite.runtime_audit_schema import connect_runtime_audit, ensure_runtime_audit_schema
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("candidate_decision_ledger")


def text_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class CandidateRecord:
    candidate_id: str
    text: str
    score: float | None = None
    accepted: bool = False
    validation: dict[str, Any] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)

    def to_safe_dict(self, *, include_raw_text: bool = False) -> dict[str, Any]:
        payload = {
            "candidate_id": self.candidate_id,
            "text_hash": text_hash(self.text),
            "score": self.score,
            "accepted": self.accepted,
            "validation": self.validation,
            "rejection_reasons": list(self.rejection_reasons),
        }
        if include_raw_text:
            payload["text"] = self.text
        return payload


@dataclass(slots=True)
class CandidateDecision:
    turn_id: str
    trace_id: str
    candidates: list[CandidateRecord]
    selected_candidate_id: str | None
    final_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    decision_id: str = ""
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate_id values must be unique")
        if self.selected_candidate_id is not None and self.selected_candidate_id not in ids:
            raise ValueError("selected_candidate_id is not present")
        if not self.decision_id:
            raw = json.dumps(
                {
                    "turn_id": self.turn_id,
                    "trace_id": self.trace_id,
                    "candidate_hashes": [text_hash(candidate.text) for candidate in self.candidates],
                    "selected_candidate_id": self.selected_candidate_id,
                    "created_at_utc": self.created_at_utc,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            self.decision_id = "decision-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def to_safe_dict(self, *, include_raw_text: bool = False) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "turn_id": self.turn_id,
            "trace_id": self.trace_id,
            "selected_candidate_id": self.selected_candidate_id,
            "final_hash": text_hash(self.final_text or "") if self.final_text is not None else None,
            "metadata": self.metadata,
            "created_at_utc": self.created_at_utc,
            "candidates": [item.to_safe_dict(include_raw_text=include_raw_text) for item in self.candidates],
        }


class CandidateDecisionLedger:
    def __init__(self, path: Path | str, *, retain_raw_text: bool = False) -> None:
        self.path = Path(path)
        self.retain_raw_text = retain_raw_text

    def append(self, decision: CandidateDecision) -> str:
        connection = connect_runtime_audit(self.path)
        try:
            ensure_runtime_audit_schema(connection)
            with connection:
                connection.execute(
                    """
                    INSERT INTO candidate_decisions(
                      decision_id,turn_id,trace_id,selected_candidate_id,final_hash,metadata_json,created_at_utc
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        decision.decision_id,
                        decision.turn_id,
                        decision.trace_id,
                        decision.selected_candidate_id,
                        text_hash(decision.final_text or "") if decision.final_text is not None else None,
                        json.dumps(decision.metadata, ensure_ascii=False, sort_keys=True),
                        decision.created_at_utc,
                    ),
                )
                for ordinal, candidate in enumerate(decision.candidates):
                    connection.execute(
                        """
                        INSERT INTO response_candidates(
                          decision_id,candidate_id,ordinal,text_hash,score,accepted,
                          validation_json,rejection_reasons_json,raw_text
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            decision.decision_id,
                            candidate.candidate_id,
                            ordinal,
                            text_hash(candidate.text),
                            candidate.score,
                            int(candidate.accepted),
                            json.dumps(candidate.validation, ensure_ascii=False, sort_keys=True),
                            json.dumps(candidate.rejection_reasons, ensure_ascii=False),
                            candidate.text if self.retain_raw_text else None,
                        ),
                    )
            return decision.decision_id
        finally:
            connection.close()

    def get(self, decision_id: str, *, include_raw_text: bool = False) -> dict[str, Any] | None:
        connection = connect_runtime_audit(self.path)
        try:
            ensure_runtime_audit_schema(connection)
            row = connection.execute(
                "SELECT * FROM candidate_decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
            if row is None:
                return None
            candidates = connection.execute(
                "SELECT * FROM response_candidates WHERE decision_id=? ORDER BY ordinal", (decision_id,)
            ).fetchall()
            payload = dict(row)
            payload["metadata"] = json.loads(payload.pop("metadata_json"))
            payload["candidates"] = []
            for candidate in candidates:
                item = dict(candidate)
                item["accepted"] = bool(item["accepted"])
                item["validation"] = json.loads(item.pop("validation_json"))
                item["rejection_reasons"] = json.loads(item.pop("rejection_reasons_json"))
                if not include_raw_text:
                    item.pop("raw_text", None)
                payload["candidates"].append(item)
            return payload
        finally:
            connection.close()
