from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from latka_jazn.memory.memory_promotion import (
    LongTermPromotionPolicy,
    PromotionOutcome,
    new_promotion_request,
)
from latka_jazn.memory.memory_tiers import (
    MemoryKind,
    MemoryTruthStatus,
    PromotionStatus,
    ShortTermMemoryPolicy,
    SourceEvidence,
)

BASE_TIME = datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc)


def evidence(source_id: str = "segment-1") -> SourceEvidence:
    return SourceEvidence(
        source_type="conversation_segment",
        source_id=source_id,
        source_sha256="a" * 64,
        conversation_id="conv-1",
        node_ids=("n1", "n2"),
        segment_id=source_id,
        exact_excerpt_sha256="b" * 64,
        timestamp_status="exact",
    )


def make_short_term(*, kind: MemoryKind = MemoryKind.EPISODIC, truth: MemoryTruthStatus = MemoryTruthStatus.USER_CONFIRMED):
    return ShortTermMemoryPolicy().create(
        kind=kind,
        content="Krzysztof potwierdził ważne wydarzenie.",
        domain="daily_life",
        mode="factual_conversation",
        truth_status=truth,
        confidence=0.91,
        importance=0.88,
        evidence=[evidence()],
        created_at_utc=BASE_TIME,
    )


def test_short_term_ids_are_deterministic_and_require_evidence() -> None:
    policy = ShortTermMemoryPolicy()
    first = make_short_term()
    second = make_short_term()
    assert first.memory_id == second.memory_id
    with pytest.raises(ValueError, match="source evidence"):
        policy.create(
            kind=MemoryKind.EPISODIC,
            content="Brak źródła.",
            domain="daily_life",
            mode="factual_conversation",
            truth_status=MemoryTruthStatus.INFERRED,
            confidence=0.5,
            importance=0.5,
            evidence=[],
            created_at_utc=BASE_TIME,
        )


def test_reinforcement_is_idempotent_and_bounded() -> None:
    policy = ShortTermMemoryPolicy(
        default_ttl=timedelta(days=2),
        reinforcement_extension=timedelta(days=3),
        max_lifetime=timedelta(days=6),
    )
    record = policy.create(
        kind=MemoryKind.OPEN_TASK,
        content="Dokończyć importer rozmów.",
        domain="development",
        mode="technical_work",
        truth_status=MemoryTruthStatus.SOURCE_RECORDED,
        confidence=0.9,
        importance=0.8,
        evidence=[evidence("segment-a")],
        created_at_utc=BASE_TIME,
    )
    reinforced = policy.reinforce(record, evidence=evidence("segment-b"), at_utc=BASE_TIME + timedelta(hours=1))
    duplicate = policy.reinforce(reinforced, evidence=evidence("segment-b"), at_utc=BASE_TIME + timedelta(hours=2))
    capped = policy.reinforce(reinforced, evidence=evidence("segment-c"), at_utc=BASE_TIME + timedelta(hours=3))
    assert reinforced.reinforcement_count == 1
    assert duplicate == reinforced
    assert capped.expires_at_utc == BASE_TIME + timedelta(days=6)


def test_expired_short_term_record_cannot_be_reinforced() -> None:
    policy = ShortTermMemoryPolicy(default_ttl=timedelta(hours=1))
    record = policy.create(
        kind=MemoryKind.HYPOTHESIS,
        content="Hipoteza oczekuje na sprawdzenie.",
        domain="system",
        mode="planning",
        truth_status=MemoryTruthStatus.INFERRED,
        confidence=0.5,
        importance=0.5,
        evidence=[evidence()],
        created_at_utc=BASE_TIME,
    )
    result = policy.reinforce(record, evidence=evidence("new"), at_utc=BASE_TIME + timedelta(hours=2))
    assert result.promotion_status is PromotionStatus.EXPIRED
    assert result.reinforcement_count == 0


def test_long_term_policy_never_auto_commits_and_requires_evidence_strength() -> None:
    record = make_short_term()
    policy = ShortTermMemoryPolicy()
    record = policy.reinforce(record, evidence=evidence("segment-2"), at_utc=BASE_TIME + timedelta(hours=1))
    request = new_promotion_request(
        record,
        requested_by="Krzysztof",
        reason="Jawne zatwierdzenie ważnego wspomnienia.",
        requested_at_utc=BASE_TIME + timedelta(hours=2),
    )
    decision = LongTermPromotionPolicy().evaluate(record, request, decided_at_utc=BASE_TIME + timedelta(hours=3))
    assert decision.outcome is PromotionOutcome.APPROVED
    assert decision.automatic_commit_allowed is False
    long_term = LongTermPromotionPolicy().materialize(
        record,
        request,
        decision,
        approved_by="Krzysztof",
        promoted_at_utc=BASE_TIME + timedelta(hours=4),
    )
    assert long_term.active
    assert long_term.promoted_from_memory_id == record.memory_id
    assert long_term.promotion_decision_id == decision.decision_id


def test_book_scene_and_book_canon_require_distinct_handling() -> None:
    draft = make_short_term(kind=MemoryKind.BOOK_DRAFT, truth=MemoryTruthStatus.BOOK_SCENE)
    draft = ShortTermMemoryPolicy().reinforce(draft, evidence=evidence("segment-2"), at_utc=BASE_TIME + timedelta(hours=1))
    draft_request = new_promotion_request(
        draft,
        requested_by="runtime",
        reason="Zachowaj szkic jako materiał książkowy.",
        requested_at_utc=BASE_TIME + timedelta(hours=2),
    )
    draft_decision = LongTermPromotionPolicy().evaluate(draft, draft_request, decided_at_utc=BASE_TIME + timedelta(hours=3))
    assert draft_decision.outcome is PromotionOutcome.APPROVED

    canon = ShortTermMemoryPolicy().create(
        kind=MemoryKind.BOOK_CANON,
        content="Zatwierdzona scena rozdziału.",
        domain="book",
        mode="manuscript_draft",
        truth_status=MemoryTruthStatus.CANONICAL,
        confidence=0.95,
        importance=0.9,
        evidence=[evidence("canon-segment")],
        created_at_utc=BASE_TIME,
    )
    canon = ShortTermMemoryPolicy().reinforce(canon, evidence=evidence("canon-confirmation"), at_utc=BASE_TIME + timedelta(hours=1))
    no_approval = new_promotion_request(
        canon,
        requested_by="runtime",
        reason="Kandydat kanonu.",
        explicit_user_approval=False,
        requested_at_utc=BASE_TIME + timedelta(hours=2),
    )
    decision = LongTermPromotionPolicy().evaluate(canon, no_approval, decided_at_utc=BASE_TIME + timedelta(hours=3))
    assert decision.outcome is PromotionOutcome.NEEDS_REVIEW
    with_approval = new_promotion_request(
        canon,
        requested_by="Krzysztof",
        reason="Krzysztof zatwierdził scenę jako kanon.",
        explicit_user_approval=True,
        requested_at_utc=BASE_TIME + timedelta(hours=4),
    )
    approved = LongTermPromotionPolicy().evaluate(canon, with_approval, decided_at_utc=BASE_TIME + timedelta(hours=5))
    assert approved.outcome is PromotionOutcome.APPROVED
