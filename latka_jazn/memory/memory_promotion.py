from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
import hashlib
import json

from latka_jazn.memory.memory_tiers import (
    LongTermMemoryRecord,
    MemoryKind,
    MemoryTier,
    MemoryTruthStatus,
    PromotionStatus,
    ShortTermMemoryRecord,
    ensure_utc,
    utc_now,
)
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_promotion")


class PromotionOutcome(StrEnum):
    NEEDS_REVIEW = "needs_review"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"
    REJECTED = "rejected"
    APPROVED = "approved"


@dataclass(slots=True, frozen=True)
class PromotionRequest:
    request_id: str
    source_memory_id: str
    target_tier: MemoryTier
    requested_by: str
    requested_at_utc: datetime
    explicit_user_approval: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        ensure_utc(self.requested_at_utc)
        if self.target_tier is not MemoryTier.LONG_TERM:
            raise ValueError("current promotion policy only authorizes target long_term")
        if not self.reason.strip():
            raise ValueError("promotion request requires a reason")


@dataclass(slots=True, frozen=True)
class PromotionDecision:
    decision_id: str
    request_id: str
    source_memory_id: str
    outcome: PromotionOutcome
    target_tier: MemoryTier
    decided_at_utc: datetime
    decided_by: str
    reasons: tuple[str, ...]
    policy_version: str = SCHEMA_VERSION
    automatic_commit_allowed: bool = False
    truth_boundary: str = (
        "Decyzja promocji jest audytowalną autoryzacją zapisu. Sama ważność, liczba "
        "powtórzeń ani klasyfikacja tematu nigdy nie powodują automatycznej pamięci L3."
    )

    def __post_init__(self) -> None:
        ensure_utc(self.decided_at_utc)
        if self.automatic_commit_allowed:
            raise ValueError("automatic L3 commit is forbidden by this policy")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["outcome"] = self.outcome.value
        data["target_tier"] = self.target_tier.value
        data["decided_at_utc"] = self.decided_at_utc.isoformat()
        return data


@dataclass(slots=True, frozen=True)
class LongTermPromotionPolicy:
    min_confidence: float = 0.72
    min_importance: float = 0.62
    min_reinforcement_count: int = 1
    require_user_approval_for_book_canon: bool = True
    require_user_approval_for_preference: bool = True
    schema_version: str = SCHEMA_VERSION

    def evaluate(
        self,
        record: ShortTermMemoryRecord,
        request: PromotionRequest,
        *,
        decided_at_utc: datetime | None = None,
    ) -> PromotionDecision:
        when = ensure_utc(decided_at_utc or utc_now())
        reasons: list[str] = []
        outcome = PromotionOutcome.APPROVED

        if record.memory_id != request.source_memory_id:
            raise ValueError("promotion request references a different memory record")
        if record.promotion_status in {PromotionStatus.REJECTED, PromotionStatus.INVALIDATED, PromotionStatus.EXPIRED}:
            outcome = PromotionOutcome.REJECTED
            reasons.append(f"source_status={record.promotion_status.value}")
        if record.is_expired(now=when):
            outcome = PromotionOutcome.REJECTED
            reasons.append("short_term_record_expired")
        if record.truth_status is MemoryTruthStatus.REJECTED:
            outcome = PromotionOutcome.REJECTED
            reasons.append("truth_status_rejected")
        elif record.truth_status in {
            MemoryTruthStatus.INFERRED,
            MemoryTruthStatus.SYMBOLIC,
            MemoryTruthStatus.BOOK_SCENE,
            MemoryTruthStatus.DRAFT,
        }:
            if record.kind is not MemoryKind.BOOK_DRAFT:
                outcome = PromotionOutcome.NEEDS_REVIEW
                reasons.append(f"non_factual_truth_status={record.truth_status.value}")
        if record.confidence < self.min_confidence:
            outcome = _weaker(outcome, PromotionOutcome.NEEDS_MORE_EVIDENCE)
            reasons.append(f"confidence_below_{self.min_confidence}")
        if record.importance < self.min_importance:
            outcome = _weaker(outcome, PromotionOutcome.NEEDS_MORE_EVIDENCE)
            reasons.append(f"importance_below_{self.min_importance}")
        if record.reinforcement_count < self.min_reinforcement_count:
            outcome = _weaker(outcome, PromotionOutcome.NEEDS_MORE_EVIDENCE)
            reasons.append(f"reinforcement_below_{self.min_reinforcement_count}")
        if not record.evidence:
            outcome = PromotionOutcome.REJECTED
            reasons.append("source_evidence_missing")

        approval_required = (
            (record.kind is MemoryKind.BOOK_CANON and self.require_user_approval_for_book_canon)
            or (record.kind is MemoryKind.PREFERENCE and self.require_user_approval_for_preference)
            or record.truth_status is MemoryTruthStatus.CANONICAL
        )
        if approval_required and not request.explicit_user_approval:
            outcome = _weaker(outcome, PromotionOutcome.NEEDS_REVIEW)
            reasons.append("explicit_user_approval_required")

        if not reasons:
            reasons.append("all_long_term_policy_requirements_satisfied")
        decision_id = _decision_id(request, record, outcome, reasons)
        return PromotionDecision(
            decision_id=decision_id,
            request_id=request.request_id,
            source_memory_id=record.memory_id,
            outcome=outcome,
            target_tier=MemoryTier.LONG_TERM,
            decided_at_utc=when,
            decided_by="LongTermPromotionPolicy",
            reasons=tuple(reasons),
            automatic_commit_allowed=False,
        )

    def materialize(
        self,
        record: ShortTermMemoryRecord,
        request: PromotionRequest,
        decision: PromotionDecision,
        *,
        approved_by: str,
        promoted_at_utc: datetime | None = None,
    ) -> LongTermMemoryRecord:
        """Create an L3 record only after a separately persisted approved decision."""
        if decision.request_id != request.request_id or decision.source_memory_id != record.memory_id:
            raise ValueError("promotion request, decision and source record do not match")
        if decision.outcome is not PromotionOutcome.APPROVED:
            raise ValueError(f"cannot materialize non-approved promotion: {decision.outcome.value}")
        if not approved_by.strip():
            raise ValueError("approved_by is required")
        when = ensure_utc(promoted_at_utc or utc_now())
        return LongTermMemoryRecord(
            memory_id=_long_term_id(record, decision),
            tier=MemoryTier.LONG_TERM,
            kind=record.kind,
            content=record.content,
            content_sha256=record.content_sha256,
            domain=record.domain,
            mode=record.mode,
            truth_status=record.truth_status,
            confidence=record.confidence,
            importance=record.importance,
            created_at_utc=record.created_at_utc,
            updated_at_utc=when,
            evidence=record.evidence,
            tags=record.tags,
            promoted_at_utc=when,
            promoted_from_memory_id=record.memory_id,
            promotion_decision_id=decision.decision_id,
            approved_by=approved_by,
            promotion_reason=request.reason,
        )


def new_promotion_request(
    record: ShortTermMemoryRecord,
    *,
    requested_by: str,
    reason: str,
    explicit_user_approval: bool = False,
    requested_at_utc: datetime | None = None,
) -> PromotionRequest:
    when = ensure_utc(requested_at_utc or utc_now())
    raw = json.dumps(
        {
            "memory_id": record.memory_id,
            "requested_by": requested_by,
            "reason": reason,
            "explicit_user_approval": explicit_user_approval,
            "requested_at_utc": when.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return PromotionRequest(
        request_id=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        source_memory_id=record.memory_id,
        target_tier=MemoryTier.LONG_TERM,
        requested_by=requested_by,
        requested_at_utc=when,
        explicit_user_approval=explicit_user_approval,
        reason=reason,
    )


def _weaker(current: PromotionOutcome, candidate: PromotionOutcome) -> PromotionOutcome:
    order = {
        PromotionOutcome.APPROVED: 0,
        PromotionOutcome.NEEDS_MORE_EVIDENCE: 1,
        PromotionOutcome.NEEDS_REVIEW: 2,
        PromotionOutcome.REJECTED: 3,
    }
    return candidate if order[candidate] > order[current] else current


def _decision_id(
    request: PromotionRequest,
    record: ShortTermMemoryRecord,
    outcome: PromotionOutcome,
    reasons: list[str],
) -> str:
    raw = json.dumps(
        {
            "request_id": request.request_id,
            "source_memory_id": record.memory_id,
            "outcome": outcome.value,
            "reasons": reasons,
            "policy_version": SCHEMA_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _long_term_id(record: ShortTermMemoryRecord, decision: PromotionDecision) -> str:
    raw = f"long_term|{record.memory_id}|{decision.decision_id}|{record.content_sha256}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
