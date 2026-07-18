from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3

import pytest

from latka_jazn.memory.memory_promotion import LongTermPromotionPolicy, new_promotion_request
from latka_jazn.memory.memory_tier_store import MemoryTierStore, WorkingMemoryBudget
from latka_jazn.memory.memory_tiers import (
    MemoryKind,
    MemoryTier,
    MemoryTruthStatus,
    ShortTermMemoryPolicy,
    SourceEvidence,
    WorkingMemoryRecord,
    deterministic_memory_id,
)
from latka_jazn.memory.turn_memory_transaction import TurnMemoryTransaction

BASE = datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)


def evidence(name: str) -> SourceEvidence:
    return SourceEvidence(
        source_type="conversation_segment",
        source_id=name,
        source_sha256="a" * 64,
        conversation_id="conv-1",
        node_ids=(f"node-{name}",),
        segment_id=name,
        exact_excerpt_sha256="b" * 64,
        timestamp_status="exact",
    )


def short_record(content: str = "Ważne zdarzenie"):
    policy = ShortTermMemoryPolicy(default_ttl=timedelta(days=7))
    record = policy.create(
        kind=MemoryKind.EPISODIC,
        content=content,
        domain="daily_life",
        mode="factual_conversation",
        truth_status=MemoryTruthStatus.USER_CONFIRMED,
        confidence=0.92,
        importance=0.89,
        evidence=[evidence("source")],
        created_at_utc=BASE,
    )
    return policy.reinforce(record, evidence=evidence("confirmation"), at_utc=BASE + timedelta(minutes=1))


def working(content: str, *, session: str = "session-1", importance: float = 0.5, minute: int = 0) -> WorkingMemoryRecord:
    created = BASE + timedelta(minutes=minute)
    memory_id = deterministic_memory_id(
        tier=MemoryTier.WORKING,
        kind=MemoryKind.CONVERSATION_CONTEXT,
        content=content,
        domain="development",
        mode="technical_work",
        evidence=(),
    )
    return WorkingMemoryRecord(
        memory_id=memory_id,
        tier=MemoryTier.WORKING,
        kind=MemoryKind.CONVERSATION_CONTEXT,
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        domain="development",
        mode="technical_work",
        truth_status=MemoryTruthStatus.SOURCE_RECORDED,
        confidence=0.9,
        importance=importance,
        created_at_utc=created,
        updated_at_utc=created,
        evidence=(),
        session_id=session,
        turn_id=f"turn-{minute}",
        active_goal="test",
    )


def test_short_term_round_trip_and_integrity(tmp_path) -> None:
    database = tmp_path / "tiers.sqlite3"
    record = short_record()
    with MemoryTierStore(database) as store:
        summary = store.save_record(record)
        assert summary.records_written == 1
        restored = store.get_record(record.memory_id)
        assert restored == record
        assert store.stats()["memory_evidence"] == 2
        assert store.validate()["ok"] is True


def test_working_budget_evicts_old_low_importance_records(tmp_path) -> None:
    with MemoryTierStore(tmp_path / "tiers.sqlite3") as store:
        budget = WorkingMemoryBudget(max_records_per_session=2, max_total_chars_per_session=100, max_record_chars=60)
        store.save_record(working("low", importance=0.1, minute=0), working_budget=budget)
        store.save_record(working("medium", importance=0.5, minute=1), working_budget=budget)
        result = store.save_record(working("high", importance=0.9, minute=2), working_budget=budget)
        assert result.working_records_evicted == 1
        records = store.list_records(session_id="session-1")
        assert [item.content for item in records] == ["medium", "high"]


def test_turn_transaction_rolls_back_everything_after_late_failure(tmp_path) -> None:
    with MemoryTierStore(tmp_path / "tiers.sqlite3") as store:
        tx = TurnMemoryTransaction(store)
        tx.stage_record(short_record())
        tx.stage_outbox(
            event_type="memory.test",
            aggregate_id="turn-1",
            payload={"value": 1},
            idempotency_key="turn-1:test",
        )
        tx.stage_before_commit_hook(lambda: (_ for _ in ()).throw(RuntimeError("late failure")))
        with pytest.raises(RuntimeError, match="late failure"):
            tx.commit()
        stats = store.stats()
        assert stats["memory_records"] == 0
        assert stats["memory_outbox"] == 0
        assert store.validate()["ok"] is True


def test_approved_promotion_persists_ledger_l3_and_one_outbox_event(tmp_path) -> None:
    source = short_record()
    request = new_promotion_request(
        source,
        requested_by="Krzysztof",
        reason="Jawne zatwierdzenie.",
        requested_at_utc=BASE + timedelta(minutes=2),
    )
    policy = LongTermPromotionPolicy()
    decision = policy.evaluate(source, request, decided_at_utc=BASE + timedelta(minutes=3))
    long_term = policy.materialize(
        source,
        request,
        decision,
        approved_by="Krzysztof",
        promoted_at_utc=BASE + timedelta(minutes=4),
    )
    with MemoryTierStore(tmp_path / "tiers.sqlite3") as store:
        tx = TurnMemoryTransaction(store)
        tx.stage_promotion(source, request, decision, long_term)
        result = tx.commit()
        assert result.committed is True
        assert result.promotions_written == 1
        stats = store.stats()
        assert stats["short_term_memory_index"] == 1
        assert stats["long_term_memory_index"] == 1
        assert stats["promotion_requests"] == 1
        assert stats["promotion_decisions"] == 1
        assert stats["promotion_ledger"] == 1
        assert stats["memory_outbox"] == 1
        assert store.get_record(long_term.memory_id) == long_term
        assert store.validate()["ok"] is True


def test_outbox_is_idempotent_claimable_and_completable(tmp_path) -> None:
    with MemoryTierStore(tmp_path / "tiers.sqlite3") as store:
        for _ in range(2):
            with store.transaction():
                store.write_outbox(
                    event_type="memory.refresh",
                    aggregate_id="memory-1",
                    payload={"memory_id": "memory-1"},
                    idempotency_key="refresh:memory-1",
                )
        assert store.stats()["memory_outbox"] == 1
        claimed = store.claim_outbox(limit=10, now=BASE + timedelta(days=1))
        assert len(claimed) == 1
        event_id = claimed[0]["event_id"]
        store.complete_outbox(event_id)
        row = store.con.execute("SELECT status,attempts FROM memory_outbox WHERE event_id=?", (event_id,)).fetchone()
        assert tuple(row) == ("processed", 1)


def test_checkpoint_round_trip_retention_and_session_end(tmp_path) -> None:
    with MemoryTierStore(tmp_path / "tiers.sqlite3") as store:
        store.save_record(working("context one", minute=0))
        store.save_record(working("context two", minute=1))
        first = store.checkpoint_session("session-1", state={"route": "book"}, keep_latest=1)
        second = store.checkpoint_session("session-1", state={"route": "development"}, keep_latest=1)
        assert first != second
        assert store.stats()["session_checkpoints"] == 1
        payload = store.load_latest_checkpoint("session-1")
        assert payload["state"] == {"route": "development"}
        assert len(payload["records"]) == 2
        assert store.end_session("session-1") == 2
        assert store.list_records(session_id="session-1") == []
        assert store.stats()["session_checkpoints"] == 1


def test_database_constraints_reject_automatic_l3_commit_flag(tmp_path) -> None:
    with MemoryTierStore(tmp_path / "tiers.sqlite3") as store:
        source = short_record()
        with store.transaction():
            store.write_record(source)
            store.con.execute(
                """INSERT INTO promotion_requests(
                   request_id,source_memory_id,target_tier,requested_by,requested_at_utc,
                   explicit_user_approval,reason,request_json) VALUES(?,?,?,?,?,?,?,?)""",
                ("r", source.memory_id, "long_term", "test", BASE.isoformat(), 0, "test", "{}"),
            )
            with pytest.raises(sqlite3.IntegrityError):
                store.con.execute(
                    """INSERT INTO promotion_decisions(
                       decision_id,request_id,source_memory_id,outcome,target_tier,decided_at_utc,
                       decided_by,reasons_json,policy_version,automatic_commit_allowed,decision_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    ("d", "r", source.memory_id, "approved", "long_term", BASE.isoformat(), "test", "[]", "v", 1, "{}"),
                )
