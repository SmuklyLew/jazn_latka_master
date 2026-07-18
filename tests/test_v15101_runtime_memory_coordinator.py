from __future__ import annotations

from pathlib import Path

from latka_jazn.memory.memory_tier_store import MemoryTierStore
from latka_jazn.memory.runtime_memory_v151 import RuntimeMemoryV151Coordinator, RuntimeMemoryWriteContext
from latka_jazn.memory.runtime_persistence import RuntimeMemoryCandidate


class FakeClassifier:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted

    def build_candidate_from_runtime_turn(self, **kwargs) -> RuntimeMemoryCandidate:
        return RuntimeMemoryCandidate(
            kind="runtime_wspomnienie",
            title="Test",
            content=kwargs["user_text"],
            importance=float(kwargs["importance"]),
            confidence=float(kwargs.get("confidence", 0.8)),
            source=str(kwargs.get("source", "runtime")),
            grounding=str(kwargs.get("grounding", "recognized")),
            raw_excerpt=str(kwargs.get("raw_excerpt") or kwargs["user_text"]),
        )

    def should_persist(self, candidate: RuntimeMemoryCandidate) -> tuple[bool, str]:
        return self.accepted, "accepted_for_test" if self.accepted else "below_threshold"

    def candidate_fingerprint(self, candidate: RuntimeMemoryCandidate) -> str:
        return "f" * 64


def test_rejected_candidate_does_not_create_database(tmp_path: Path) -> None:
    database = tmp_path / "runtime-v151.sqlite3"
    coordinator = RuntimeMemoryV151Coordinator(database, classifier=FakeClassifier(accepted=False))
    candidate = RuntimeMemoryCandidate(kind="runtime_wspomnienie", title="Nie zapisuj", content="krótki ślad")
    result = coordinator.persist_candidate(candidate)
    assert result.accepted is False
    assert result.records == []
    assert database.exists() is False


def test_accepted_turn_writes_only_l1_l2_and_outbox(tmp_path: Path) -> None:
    database = tmp_path / "runtime-v151.sqlite3"
    coordinator = RuntimeMemoryV151Coordinator(database, classifier=FakeClassifier())
    candidate = RuntimeMemoryCandidate(
        kind="runtime_wspomnienie",
        title="Rozmowa",
        content="To jest ważny ślad rozmowy.",
        importance=0.82,
        confidence=0.9,
        grounding="recognized",
        raw_excerpt="To jest ważny ślad rozmowy.",
    )
    context = RuntimeMemoryWriteContext(session_id="session-1", turn_id="turn-1", actor="user")
    result = coordinator.persist_candidate(candidate, context=context)
    assert result.accepted is True
    assert [record.layer for record in result.records] == ["working", "short_term", "outbox"]

    with MemoryTierStore(database) as store:
        stats = store.stats()
        assert stats["memory_records"] == 2
        assert stats["working_memory_index"] == 1
        assert stats["short_term_memory_index"] == 1
        assert stats["long_term_memory_index"] == 0
        assert stats["promotion_requests"] == 0
        assert stats["promotion_decisions"] == 0
        assert stats["promotion_ledger"] == 0
        assert stats["memory_outbox"] == 1
        assert store.validate()["ok"] is True


def test_same_turn_is_idempotent_and_procedural_candidate_is_l2_procedural(tmp_path: Path) -> None:
    database = tmp_path / "runtime-v151.sqlite3"
    coordinator = RuntimeMemoryV151Coordinator(database, classifier=FakeClassifier())
    candidate = RuntimeMemoryCandidate(
        kind="reguła_proceduralna",
        title="Reguła",
        content="Zachowaj pełne źródło rozmowy.",
        importance=0.9,
        confidence=0.88,
        grounding="user_confirmed",
        procedural_trigger="import eksportu",
        procedural_action="zachować drzewo",
        procedural_reason="kontekst",
    )
    context = RuntimeMemoryWriteContext(session_id="session-2", turn_id="turn-2", actor="user")
    coordinator.persist_candidate(candidate, context=context)
    coordinator.persist_candidate(candidate, context=context)

    with MemoryTierStore(database) as store:
        stats = store.stats()
        assert stats["memory_records"] == 2
        assert stats["memory_outbox"] == 1
        short = store.con.execute(
            """SELECT r.kind,r.truth_status FROM memory_records r
               JOIN short_term_memory_index s ON s.memory_id=r.memory_id"""
        ).fetchone()
        assert tuple(short) == ("procedural", "user_confirmed")
        assert store.validate()["ok"] is True
