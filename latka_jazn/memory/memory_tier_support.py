from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
import json

from latka_jazn.memory.memory_tiers import (
    LongTermMemoryRecord,
    MemoryKind,
    MemoryRecord,
    MemoryTier,
    MemoryTruthStatus,
    PromotionStatus,
    ShortTermMemoryRecord,
    SourceEvidence,
    WorkingMemoryRecord,
    ensure_utc,
)


def iso(value: datetime) -> str:
    return ensure_utc(value).isoformat()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(slots=True, frozen=True)
class WorkingMemoryBudget:
    max_records_per_session: int = 32
    max_total_chars_per_session: int = 32_000
    max_record_chars: int = 8_000

    def __post_init__(self) -> None:
        if min(self.max_records_per_session, self.max_total_chars_per_session, self.max_record_chars) < 1:
            raise ValueError("working-memory budget values must be positive")


@dataclass(slots=True, frozen=True)
class WriteSummary:
    records_written: int = 0
    evidence_written: int = 0
    promotions_written: int = 0
    outbox_written: int = 0
    working_records_evicted: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def evidence_from_dict(data: dict[str, Any]) -> SourceEvidence:
    return SourceEvidence(
        source_type=str(data["source_type"]),
        source_id=str(data["source_id"]),
        source_sha256=data.get("source_sha256"),
        conversation_id=data.get("conversation_id"),
        node_ids=tuple(data.get("node_ids") or ()),
        segment_id=data.get("segment_id"),
        exact_excerpt_sha256=data.get("exact_excerpt_sha256"),
        timestamp_status=data.get("timestamp_status"),
        metadata=dict(data.get("metadata") or {}),
    )


def record_from_dict(data: dict[str, Any]) -> MemoryRecord:
    common = dict(
        memory_id=str(data["memory_id"]),
        tier=MemoryTier(data["tier"]),
        kind=MemoryKind(data["kind"]),
        content=str(data["content"]),
        content_sha256=str(data["content_sha256"]),
        domain=str(data["domain"]),
        mode=str(data["mode"]),
        truth_status=MemoryTruthStatus(data["truth_status"]),
        confidence=float(data["confidence"]),
        importance=float(data["importance"]),
        created_at_utc=datetime.fromisoformat(data["created_at_utc"]),
        updated_at_utc=datetime.fromisoformat(data["updated_at_utc"]),
        evidence=tuple(evidence_from_dict(item) for item in data.get("evidence") or ()),
        tags=tuple(data.get("tags") or ()),
    )
    tier = common["tier"]
    if tier is MemoryTier.WORKING:
        return WorkingMemoryRecord(
            **common,
            session_id=str(data["session_id"]),
            turn_id=data.get("turn_id"),
            active_goal=data.get("active_goal"),
            expires_on_session_end=bool(data.get("expires_on_session_end", True)),
            checkpoint_allowed=bool(data.get("checkpoint_allowed", True)),
        )
    if tier is MemoryTier.SHORT_TERM:
        return ShortTermMemoryRecord(
            **common,
            expires_at_utc=datetime.fromisoformat(data["expires_at_utc"]),
            reinforcement_count=int(data.get("reinforcement_count", 0)),
            last_reinforced_at_utc=parse_time(data.get("last_reinforced_at_utc")),
            reinforcement_evidence_keys=tuple(data.get("reinforcement_evidence_keys") or ()),
            promotion_status=PromotionStatus(data.get("promotion_status", PromotionStatus.PENDING_REVIEW.value)),
        )
    if tier is MemoryTier.LONG_TERM:
        return LongTermMemoryRecord(
            **common,
            promoted_at_utc=datetime.fromisoformat(data["promoted_at_utc"]),
            promoted_from_memory_id=data.get("promoted_from_memory_id"),
            promotion_decision_id=data.get("promotion_decision_id"),
            approved_by=str(data.get("approved_by") or "runtime_policy"),
            promotion_reason=str(data.get("promotion_reason") or ""),
            revision=int(data.get("revision", 1)),
            invalidated_at_utc=parse_time(data.get("invalidated_at_utc")),
            invalidation_reason=data.get("invalidation_reason"),
        )
    raise ValueError(f"unsupported tier: {tier}")
