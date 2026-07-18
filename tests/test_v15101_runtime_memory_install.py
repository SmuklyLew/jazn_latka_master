from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from latka_jazn.memory.memory_tier_store import MemoryTierStore
from latka_jazn.memory.runtime_memory_v151 import RuntimeMemoryV151Coordinator, RuntimeMemoryWriteContext
from latka_jazn.memory.runtime_memory_v151_install import (
    LegacyLayeredMemoryReadOnlyAdapter,
    install_runtime_memory_v151,
)
from latka_jazn.memory.runtime_persistence import RuntimeMemoryCandidate


class FakeClassifier:
    def build_candidate_from_runtime_turn(self, **kwargs) -> RuntimeMemoryCandidate:
        return RuntimeMemoryCandidate(
            kind="runtime_wspomnienie",
            title="Test",
            content=kwargs["user_text"],
            importance=0.9,
            confidence=0.9,
            grounding="recognized",
            raw_excerpt=kwargs["user_text"],
        )

    def should_persist(self, candidate: RuntimeMemoryCandidate) -> tuple[bool, str]:
        return True, "accepted"

    def candidate_fingerprint(self, candidate: RuntimeMemoryCandidate) -> str:
        return "e" * 64


class FakeLayeredMemory:
    def continuity_snapshot(self) -> dict:
        return {"read": "still available"}

    def consolidate_from_plan(self, *args, **kwargs):
        raise AssertionError("legacy fanout must not be called")


def fake_engine(root: Path):
    return SimpleNamespace(
        config=SimpleNamespace(root=root),
        runtime_memory=FakeClassifier(),
        layered_memory=FakeLayeredMemory(),
    )


def test_install_is_idempotent_and_blocks_only_legacy_write(tmp_path: Path) -> None:
    engine = fake_engine(tmp_path)
    first = install_runtime_memory_v151(engine)
    second = install_runtime_memory_v151(engine)
    assert first.installed is True
    assert second.installed is False
    assert isinstance(engine.runtime_memory, RuntimeMemoryV151Coordinator)
    assert isinstance(engine.layered_memory, LegacyLayeredMemoryReadOnlyAdapter)
    assert engine.layered_memory.continuity_snapshot() == {"read": "still available"}
    blocked = engine.layered_memory.consolidate_from_plan(text="x")
    assert blocked["status"] == "blocked_legacy_fanout"
    assert engine.layered_memory.blocked_write_count == 1


def test_bound_session_context_is_used_until_reset(tmp_path: Path) -> None:
    engine = fake_engine(tmp_path)
    status = install_runtime_memory_v151(engine)
    coordinator = engine.runtime_memory
    context = RuntimeMemoryWriteContext(session_id="session-real", turn_id="turn-real", actor="user")
    token = coordinator.bind_context(context)
    try:
        candidate = RuntimeMemoryCandidate(
            kind="runtime_wspomnienie",
            title="Tura",
            content="Treść zatwierdzonej tury.",
            importance=0.9,
            confidence=0.9,
            grounding="recognized",
        )
        coordinator.persist_candidate(candidate)
    finally:
        coordinator.reset_context(token)
    assert coordinator.current_context() is None

    with MemoryTierStore(status.database_path) as store:
        row = store.con.execute(
            """SELECT w.session_id,w.turn_id,e.source_id,e.conversation_id
                 FROM working_memory_index w
                 JOIN memory_evidence e ON e.memory_id=w.memory_id"""
        ).fetchone()
        assert tuple(row) == ("session-real", "turn-real", "turn-real", "session-real")
        assert store.stats()["long_term_memory_index"] == 0
        assert store.validate()["ok"] is True
