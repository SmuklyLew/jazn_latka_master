from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
import time

import pytest

from latka_jazn.config import JaznConfig
from latka_jazn.core.runtime_session import JaznRuntimeSession
from latka_jazn.core.turn_execution import TurnExecutionContext
from latka_jazn.core.turn_timeout import RuntimeSessionWorker, RuntimeTurnTimeoutError
from latka_jazn.memory.layered_memory import LayeredMemory
from latka_jazn.memory.store import MemoryStore
from latka_jazn.nlp.dialogue_intent_classifier import DialogueIntentClassifier


def _successful_result() -> dict:
    return {
        "ok": True,
        "final_visible_text": "[czas] Działam.",
        "final_visible_integrity": {"valid": True, "consensus": True},
        "final_visible_integrity_consensus": {"valid": True, "mismatch": False},
        "runtime_truth_gate": {"ok": True, "normal_response_allowed": True},
        "normal_response_blocked": False,
    }


def _semantic_counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        }
        names = (
            "episodic_memories",
            "semantic_facts",
            "procedural_rules",
            "reflection_entries",
            "truth_audits",
        )
        return {
            name: int(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            for name in names
            if name in tables
        }
    finally:
        con.close()


def test_input_truth_audit_is_pure_and_does_not_touch_canonical_truth_audits(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory" / "sqlite" / "runtime_memory.sqlite3")
    layered = LayeredMemory(store, tmp_path)
    before = store.stats()["truth_audits"]

    audit = layered.evaluate_truth("Działasz?", source_count=0)

    assert audit
    assert store.stats()["truth_audits"] == before
    assert (tmp_path / "memory" / "layered" / "truth_audits.jsonl").read_text(encoding="utf-8") == ""
    store.close()


def test_turn_local_semantic_commit_requires_all_success_gates(tmp_path: Path) -> None:
    context = TurnExecutionContext.create(
        request_id="valid-commit",
        turn_id="turn-valid",
        session_id="session-valid",
        timeout_seconds=1.0,
        audit_db_path=tmp_path / "runtime_audit.sqlite3",
    )
    writes: list[str] = []
    context.stage_semantic_write(
        data_type="truth_audit",
        stage="candidate_persistence_staging",
        commit=lambda: writes.append("committed"),
    )

    outcome = context.commit_if_allowed(_successful_result(), job_status="completed")

    assert outcome["committed"] is True
    assert outcome["committed_count"] == 1
    assert writes == ["committed"]
    assert context.commit_if_allowed(_successful_result(), job_status="completed")["committed_count"] == 0
    assert writes == ["committed"]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda result: result["final_visible_integrity"].update(valid=False), "integrity_invalid"),
        (lambda result: result["final_visible_integrity_consensus"].update(mismatch=True), "consensus_mismatch"),
        (lambda result: result["runtime_truth_gate"].update(ok=False), "runtime_truth_gate_failed"),
        (lambda result: result.update(normal_response_blocked=True), "normal_response_blocked"),
        (lambda result: result.update(ok=False), "result_not_ok"),
    ],
)
def test_turn_local_semantic_staging_is_rejected_on_failed_gate(tmp_path: Path, mutation, reason: str) -> None:
    context = TurnExecutionContext.create(
        request_id=f"reject-{reason}",
        turn_id="turn-reject",
        session_id="session-reject",
        timeout_seconds=1.0,
        audit_db_path=tmp_path / "runtime_audit.sqlite3",
    )
    writes: list[str] = []
    context.stage_semantic_write(
        data_type="truth_audit",
        stage="candidate_persistence_staging",
        commit=lambda: writes.append("forbidden"),
    )
    result = _successful_result()
    mutation(result)

    outcome = context.commit_if_allowed(result, job_status="completed")

    assert outcome["committed"] is False
    assert outcome["reason"] == reason
    assert writes == []


class _LateWritingSession:
    writes: list[str] = []
    executions: dict[str, int] = {}
    slow_started = threading.Event()
    allow_finish = threading.Event()
    slow_finished = threading.Event()

    def __init__(self, _config, **kwargs) -> None:
        self.state = type("State", (), {"session_id": kwargs.get("session_id")})()

    def process_user_text(self, user_text: str, *, _turn_context: TurnExecutionContext, **_kwargs) -> dict:
        self.executions[user_text] = self.executions.get(user_text, 0) + 1
        if user_text == "slow":
            _turn_context.stage_semantic_write(
                data_type="truth_audit",
                stage="candidate_persistence_staging",
                commit=lambda: self.writes.append("late-write"),
            )
            self.slow_started.set()
            self.allow_finish.wait(2.0)
            _turn_context.commit_if_allowed(_successful_result(), job_status="completed")
            self.slow_finished.set()
        return _successful_result()

    def close(self) -> None:
        return


def test_execution_timeout_cancels_late_commit_and_retires_worker(tmp_path: Path) -> None:
    _LateWritingSession.writes = []
    _LateWritingSession.executions = {}
    _LateWritingSession.slow_started = threading.Event()
    _LateWritingSession.allow_finish = threading.Event()
    _LateWritingSession.slow_finished = threading.Event()
    worker = RuntimeSessionWorker(
        session_factory=_LateWritingSession,
        config=JaznConfig(root=tmp_path),
        session_id="same-worker",
        no_carryover=False,
        source_client="isolated-test",
        command="isolated-test",
        timeout_seconds=0.03,
    )
    try:
        with pytest.raises(RuntimeTurnTimeoutError):
            worker.process_user_text("slow", request_id="timeout-request")
        assert _LateWritingSession.slow_started.is_set()
        assert worker.timed_out is True
        assert worker.usable is False

        _LateWritingSession.allow_finish.set()
        assert _LateWritingSession.slow_finished.wait(1.0)
        assert _LateWritingSession.writes == []
        with pytest.raises(RuntimeError, match="retired after an execution timeout"):
            worker.process_user_text("fast", request_id="next-request")
        assert _LateWritingSession.executions == {"slow": 1}
    finally:
        _LateWritingSession.allow_finish.set()
        worker.close()



def test_technical_audit_failure_is_fail_soft(tmp_path: Path, monkeypatch) -> None:
    from latka_jazn.audit import audit_context_store

    class _BrokenAuditStore:
        def __init__(self, _path) -> None:
            raise OSError("audit store unavailable")

    monkeypatch.setattr(audit_context_store, "AuditContextStore", _BrokenAuditStore)
    context = TurnExecutionContext.create(
        request_id="audit-failure",
        turn_id="turn-audit-failure",
        session_id="session-audit-failure",
        timeout_seconds=1.0,
        audit_db_path=tmp_path / "runtime_audit.sqlite3",
    )
    context.record_technical_event("probe", {"value": 1})

    status = context.persist_audit()

    assert status["ok"] is False
    assert status["available"] is True
    assert status["error_code"] == "OSError"
    assert "audit store unavailable" in status["error"]
    assert context.snapshot()["stages"]["audit_persistence"]["status"] == "failed_non_blocking"


def test_timeout_is_not_masked_by_technical_audit_failure(tmp_path: Path, monkeypatch) -> None:
    from latka_jazn.audit import audit_context_store

    class _BrokenAuditStore:
        def __init__(self, _path) -> None:
            raise OSError("audit store unavailable")

    monkeypatch.setattr(audit_context_store, "AuditContextStore", _BrokenAuditStore)
    _LateWritingSession.writes = []
    _LateWritingSession.executions = {}
    _LateWritingSession.slow_started = threading.Event()
    _LateWritingSession.allow_finish = threading.Event()
    _LateWritingSession.slow_finished = threading.Event()
    worker = RuntimeSessionWorker(
        session_factory=_LateWritingSession,
        config=JaznConfig(root=tmp_path),
        session_id="audit-timeout",
        no_carryover=False,
        source_client="isolated-test",
        command="isolated-test",
        timeout_seconds=0.03,
    )
    try:
        with pytest.raises(RuntimeTurnTimeoutError):
            worker.process_user_text("slow", request_id="audit-timeout-request")
    finally:
        _LateWritingSession.allow_finish.set()
        worker.close()

def test_health_presence_phrases_are_detected_before_expensive_cognitive_work() -> None:
    classifier = DialogueIntentClassifier()
    expected = {
        "Działasz?": "runtime_health_check",
        "Czy działasz?": "runtime_health_check",
        "Czy to nadal Ty?": "identity_continuity_check",
        "Czy uruchomiłaś Jaźń?": "runtime_health_check",
        "Jest tu Łatka?": "presence_check",
        "Gdzie jest Łatka?": "presence_check",
    }

    for text, intent in expected.items():
        assert classifier.classify(text).primary_intent == intent


def test_isolated_health_check_is_fast_valid_and_has_no_semantic_write(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JAZN_NETWORK_TIME_FIRST", "0")
    monkeypatch.setenv("JAZN_NETWORK_TIME_IN_TURN", "0")
    monkeypatch.setenv("JAZN_ALLOW_NETWORK", "0")
    monkeypatch.setenv("JAZN_DICTIONARY_ALLOW_NETWORK", "0")
    monkeypatch.setenv("JAZN_MODEL_ADAPTER", "null")
    (tmp_path / "main.py").write_text("# isolated runtime root\n", encoding="utf-8")
    session = JaznRuntimeSession(
        JaznConfig(root=tmp_path),
        session_id="isolated-health",
        no_carryover=True,
        source_client="isolated-test",
    )
    try:
        memory_path = session.config.memory_db_path
        before = _semantic_counts(memory_path)
        started = time.perf_counter()
        result = session.process_user_text(
            "Działasz?",
            client="isolated-test",
            lifecycle="persistent_daemon_async_job",
            process_reused=True,
        )
        duration = time.perf_counter() - started
        after = _semantic_counts(memory_path)

        assert duration < 5.0
        assert result["conversation_decision"]["detected_user_intent"] == "runtime_health_check"
        assert result["conversation_decision"]["route"] == "runtime_health_check"
        assert result["final_visible_text"]
        assert result["final_visible_integrity"]["valid"] is True
        assert result["final_visible_integrity"]["consensus"] is True
        assert result["runtime_truth_gate"]["ok"] is True
        assert before == after
    finally:
        session.close()
