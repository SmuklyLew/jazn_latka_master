from __future__ import annotations

from pathlib import Path
import json
import sqlite3

from latka_jazn.config import JaznConfig
from latka_jazn.core.runtime_session import JaznRuntimeSession
from latka_jazn.memory.memory_tier_store import MemoryTierStore


def _legacy_semantic_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    con = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        }
        return {
            table: int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in (
                "episodic_memories",
                "semantic_facts",
                "procedural_rules",
                "reflection_entries",
                "truth_audits",
            )
            if table in tables
        }
    finally:
        con.close()


def _safe_failure_diagnostic(result: dict) -> str:
    payload = {
        "ok": result.get("ok"),
        "normal_response_blocked": result.get("normal_response_blocked"),
        "canonical_persistence": result.get("canonical_persistence"),
        "runtime_truth_gate": result.get("runtime_truth_gate"),
        "final_visible_integrity": result.get("final_visible_integrity"),
        "final_visible_integrity_consensus": result.get("final_visible_integrity_consensus"),
        "memory_v151": result.get("memory_v151"),
        "conversation_route": (result.get("conversation_decision") or {}).get("route"),
        "conversation_intent": (result.get("conversation_decision") or {}).get("detected_user_intent"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def test_full_runtime_turn_commits_v151_without_legacy_fanout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JAZN_NETWORK_TIME_FIRST", "0")
    monkeypatch.setenv("JAZN_NETWORK_TIME_IN_TURN", "0")
    monkeypatch.setenv("JAZN_ALLOW_NETWORK", "0")
    monkeypatch.setenv("JAZN_DICTIONARY_ALLOW_NETWORK", "0")
    monkeypatch.setenv("JAZN_MODEL_ADAPTER", "null")
    (tmp_path / "main.py").write_text("# isolated runtime root\n", encoding="utf-8")

    session = JaznRuntimeSession(
        JaznConfig(root=tmp_path),
        session_id="v151-full-turn",
        no_carryover=True,
        source_client="isolated-v151-test",
    )
    try:
        # The integration test targets persistence wiring, not threshold tuning.
        # Routing, answer validation, staging and commit remain the real runtime path.
        monkeypatch.setattr(
            session.engine.runtime_memory.classifier,
            "should_persist",
            lambda _candidate: (True, "forced_for_full_turn_integration_test"),
        )
        legacy_path = session.config.memory_db_path
        before_legacy = _legacy_semantic_counts(legacy_path)

        # A health-check intentionally skips semantic persistence. Use the exact
        # deterministic classifier phrase for the read-only architecture audit so
        # general candidate staging runs while final text remains valid with null adapter.
        result = session.process_user_text(
            "Audyt architektury Jaźni.",
            client="isolated-v151-test",
            lifecycle="persistent_daemon_async_job",
            process_reused=True,
        )

        after_legacy = _legacy_semantic_counts(legacy_path)
        assert result["ok"] is True, _safe_failure_diagnostic(result)
        assert result["canonical_persistence"]["committed"] is True
        assert result["memory_v151"]["available"] is True
        assert result["memory_v151"]["store"]["ready"] is True
        assert result["session_provenance"]["memory_v151_ready"] is True
        assert before_legacy == after_legacy

        database = Path(result["memory_v151"]["install"]["database_path"])
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
            assert store.validate(full=True)["ok"] is True
    finally:
        session.close()
