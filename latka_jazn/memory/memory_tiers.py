from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Iterable
import hashlib
import json

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_tiers")


class MemoryTier(StrEnum):
    SOURCE_ARCHIVE = "source_archive"
    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class MemoryKind(StrEnum):
    CONVERSATION_CONTEXT = "conversation_context"
    OPEN_TASK = "open_task"
    HYPOTHESIS = "hypothesis"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    REFLECTION = "reflection"
    AFFECTIVE = "affective"
    PREFERENCE = "preference"
    BOOK_DRAFT = "book_draft"
    BOOK_CANON = "book_canon"
    MEDIA_EXPERIENCE = "media_experience"


class MemoryTruthStatus(StrEnum):
    SOURCE_RECORDED = "source_recorded"
    USER_CONFIRMED = "user_confirmed"
    INFERRED = "inferred"
    SYMBOLIC = "symbolic"
    BOOK_SCENE = "book_scene"
    DRAFT = "draft"
    CANONICAL = "canonical"
    REJECTED = "rejected"


class PromotionStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING_REVIEW = "pending_review"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("memory timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(slots=True, frozen=True)
class SourceEvidence:
    source_type: str
    source_id: str
    source_sha256: str | None = None
    conversation_id: str | None = None
    node_ids: tuple[str, ...] = ()
    segment_id: str | None = None
    exact_excerpt_sha256: str | None = None
    timestamp_status: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_type.strip():
            raise ValueError("source_type is required")
        if not self.source_id.strip():
            raise ValueError("source_id is required")

    @property
    def evidence_key(self) -> str:
        payload = {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "conversation_id": self.conversation_id,
            "node_ids": self.node_ids,
            "segment_id": self.segment_id,
            "exact_excerpt_sha256": self.exact_excerpt_sha256,
        }
        return _hash_text(_canonical_json(payload))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class MemoryRecord:
    memory_id: str
    tier: MemoryTier
    kind: MemoryKind
    content: str
    content_sha256: str
    domain: str
    mode: str
    truth_status: MemoryTruthStatus
    confidence: float
    importance: float
    created_at_utc: datetime
    updated_at_utc: datetime
    evidence: tuple[SourceEvidence, ...]
    tags: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = (
        "Rekord pamięci jest operacyjnym zapisem z proweniencją. Sam poziom ani etykieta "
        "nie dowodzą biologicznego przeżycia, faktu fizycznego ani kanonu książki."
    )

    def __post_init__(self) -> None:
        if not self.memory_id:
            raise ValueError("memory_id is required")
        if not self.content.strip():
            raise ValueError("memory content is required")
        if self.content_sha256 != _hash_text(self.content):
            raise ValueError("content_sha256 does not match content")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not 0.0 <= float(self.importance) <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        ensure_utc(self.created_at_utc)
        ensure_utc(self.updated_at_utc)
        if self.updated_at_utc < self.created_at_utc:
            raise ValueError("updated_at_utc cannot precede created_at_utc")
        if self.tier is not MemoryTier.WORKING and not self.evidence:
            raise ValueError("persistent memory tiers require source evidence")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tier"] = self.tier.value
        data["kind"] = self.kind.value
        data["truth_status"] = self.truth_status.value
        data["created_at_utc"] = self.created_at_utc.isoformat()
        data["updated_at_utc"] = self.updated_at_utc.isoformat()
        return data


@dataclass(slots=True, frozen=True)
class WorkingMemoryRecord(MemoryRecord):
    session_id: str = "runtime-session"
    turn_id: str | None = None
    active_goal: str | None = None
    expires_on_session_end: bool = True
    checkpoint_allowed: bool = True

    def __post_init__(self) -> None:
        MemoryRecord.__post_init__(self)
        if self.tier is not MemoryTier.WORKING:
            raise ValueError("WorkingMemoryRecord requires tier=working")
        if not self.session_id:
            raise ValueError("session_id is required")


@dataclass(slots=True, frozen=True)
class ShortTermMemoryRecord(MemoryRecord):
    expires_at_utc: datetime = field(default_factory=lambda: utc_now() + timedelta(days=7))
    reinforcement_count: int = 0
    last_reinforced_at_utc: datetime | None = None
    reinforcement_evidence_keys: tuple[str, ...] = ()
    promotion_status: PromotionStatus = PromotionStatus.PENDING_REVIEW

    def __post_init__(self) -> None:
        MemoryRecord.__post_init__(self)
        if self.tier is not MemoryTier.SHORT_TERM:
            raise ValueError("ShortTermMemoryRecord requires tier=short_term")
        ensure_utc(self.expires_at_utc)
        if self.expires_at_utc <= self.created_at_utc:
            raise ValueError("short-term expiry must follow creation")
        if self.reinforcement_count < 0:
            raise ValueError("reinforcement_count cannot be negative")
        if self.last_reinforced_at_utc is not None:
            ensure_utc(self.last_reinforced_at_utc)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current = ensure_utc(now or utc_now())
        return current >= self.expires_at_utc


@dataclass(slots=True, frozen=True)
class LongTermMemoryRecord(MemoryRecord):
    promoted_at_utc: datetime = field(default_factory=utc_now)
    promoted_from_memory_id: str | None = None
    promotion_decision_id: str | None = None
    approved_by: str = "runtime_policy"
    promotion_reason: str = ""
    revision: int = 1
    invalidated_at_utc: datetime | None = None
    invalidation_reason: str | None = None

    def __post_init__(self) -> None:
        MemoryRecord.__post_init__(self)
        if self.tier is not MemoryTier.LONG_TERM:
            raise ValueError("LongTermMemoryRecord requires tier=long_term")
        ensure_utc(self.promoted_at_utc)
        if not self.promotion_decision_id:
            raise ValueError("long-term memory requires a promotion decision")
        if not self.promotion_reason.strip():
            raise ValueError("long-term memory requires a promotion reason")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if self.invalidated_at_utc is not None:
            ensure_utc(self.invalidated_at_utc)

    @property
    def active(self) -> bool:
        return self.invalidated_at_utc is None


@dataclass(slots=True, frozen=True)
class ShortTermMemoryPolicy:
    default_ttl: timedelta = timedelta(days=7)
    reinforcement_extension: timedelta = timedelta(days=3)
    max_lifetime: timedelta = timedelta(days=90)
    max_reinforcement_count: int = 10_000
    schema_version: str = SCHEMA_VERSION

    def create(
        self,
        *,
        kind: MemoryKind,
        content: str,
        domain: str,
        mode: str,
        truth_status: MemoryTruthStatus,
        confidence: float,
        importance: float,
        evidence: Iterable[SourceEvidence],
        created_at_utc: datetime | None = None,
        tags: Iterable[str] = (),
    ) -> ShortTermMemoryRecord:
        created = ensure_utc(created_at_utc or utc_now())
        evidence_tuple = _unique_evidence(evidence)
        memory_id = deterministic_memory_id(
            tier=MemoryTier.SHORT_TERM,
            kind=kind,
            content=content,
            domain=domain,
            mode=mode,
            evidence=evidence_tuple,
        )
        return ShortTermMemoryRecord(
            memory_id=memory_id,
            tier=MemoryTier.SHORT_TERM,
            kind=kind,
            content=content,
            content_sha256=_hash_text(content),
            domain=domain,
            mode=mode,
            truth_status=truth_status,
            confidence=float(confidence),
            importance=float(importance),
            created_at_utc=created,
            updated_at_utc=created,
            evidence=evidence_tuple,
            tags=tuple(sorted(set(str(tag) for tag in tags if str(tag).strip()))),
            expires_at_utc=created + self.default_ttl,
            promotion_status=PromotionStatus.PENDING_REVIEW,
        )

    def reinforce(
        self,
        record: ShortTermMemoryRecord,
        *,
        evidence: SourceEvidence,
        at_utc: datetime | None = None,
    ) -> ShortTermMemoryRecord:
        when = ensure_utc(at_utc or utc_now())
        if record.is_expired(now=when):
            return replace(
                record,
                promotion_status=PromotionStatus.EXPIRED,
                updated_at_utc=when,
            )
        key = evidence.evidence_key
        if key in record.reinforcement_evidence_keys:
            return record
        evidence_tuple = _unique_evidence((*record.evidence, evidence))
        lifetime_cap = record.created_at_utc + self.max_lifetime
        extended = min(record.expires_at_utc + self.reinforcement_extension, lifetime_cap)
        return replace(
            record,
            evidence=evidence_tuple,
            reinforcement_count=min(record.reinforcement_count + 1, self.max_reinforcement_count),
            reinforcement_evidence_keys=tuple(sorted((*record.reinforcement_evidence_keys, key))),
            last_reinforced_at_utc=when,
            expires_at_utc=extended,
            updated_at_utc=when,
        )


@dataclass(slots=True, frozen=True)
class WorkingMemoryPolicy:
    max_items: int = 64
    max_total_chars: int = 48_000
    schema_version: str = SCHEMA_VERSION

    def trim(self, records: Iterable[WorkingMemoryRecord]) -> tuple[WorkingMemoryRecord, ...]:
        ordered = sorted(records, key=lambda record: (record.updated_at_utc, record.memory_id), reverse=True)
        selected: list[WorkingMemoryRecord] = []
        total_chars = 0
        for record in ordered:
            if len(selected) >= self.max_items:
                break
            if selected and total_chars + len(record.content) > self.max_total_chars:
                continue
            selected.append(record)
            total_chars += len(record.content)
        selected.sort(key=lambda record: (record.updated_at_utc, record.memory_id))
        return tuple(selected)


def deterministic_memory_id(
    *,
    tier: MemoryTier,
    kind: MemoryKind,
    content: str,
    domain: str,
    mode: str,
    evidence: Iterable[SourceEvidence],
) -> str:
    evidence_keys = sorted(item.evidence_key for item in evidence)
    payload = {
        "tier": tier.value,
        "kind": kind.value,
        "content_sha256": _hash_text(content),
        "domain": domain,
        "mode": mode,
        "evidence_keys": evidence_keys,
    }
    return _hash_text(_canonical_json(payload))


def _unique_evidence(evidence: Iterable[SourceEvidence]) -> tuple[SourceEvidence, ...]:
    by_key: dict[str, SourceEvidence] = {}
    for item in evidence:
        by_key[item.evidence_key] = item
    return tuple(by_key[key] for key in sorted(by_key))
