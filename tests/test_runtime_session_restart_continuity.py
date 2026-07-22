from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from latka_jazn.core.runtime_session_state import RuntimeSessionStateStore


def _wake(snapshot: str = "wake-1", digest: str = "a" * 64) -> dict:
    return {
        "status": "hydrated",
        "ok": True,
        "snapshot_id": snapshot,
        "snapshot_sha256": digest,
        "source_run_id": "run-1",
        "validation_status": "valid",
    }


def test_restart_without_explicit_session_id_resumes_latest_verified_checkpoint(tmp_path: Path) -> None:
    first = RuntimeSessionStateStore(tmp_path)
    state = first.load_or_create(source_client="pytest")
    state.update(user_text="Kontynuuj ten wątek", intent="continuity", route="memory")
    saved = first.save(state, continuity_context=_wake(), turn_count=3)

    assert saved["session_state_saved"] is True
    assert saved["latest_session_pointer_saved"] is True
    assert Path(saved["session_state_path"]).is_file()
    assert (tmp_path / "workspace_runtime/runtime_session_state.json").is_file()

    second = RuntimeSessionStateStore(tmp_path)
    restored = second.load_or_create(source_client="pytest")
    verified = second.verify_loaded_continuity(restored, _wake())

    assert restored.session_id == state.session_id
    assert restored.last_user_text == "Kontynuuj ten wątek"
    assert verified["status"] == "verified"
    assert verified["verified"] is True
    assert second.last_load_metadata["session_loaded_from"] == "latest_session_pointer"
    assert second.last_load_metadata["restart_continuity_verified"] is True
    assert second.last_load_metadata["continuity_turn_count"] == 3


def test_tampered_latest_checkpoint_is_rejected_fail_closed(tmp_path: Path) -> None:
    store = RuntimeSessionStateStore(tmp_path)
    state = store.load_or_create(source_client="pytest")
    state.update(user_text="oryginał", intent="test", route="test")
    store.save(state, continuity_context=_wake(), turn_count=1)

    latest = tmp_path / "workspace_runtime/runtime_session_state.json"
    payload = json.loads(latest.read_text(encoding="utf-8"))
    payload["last_user_text"] = "podmienione bez aktualizacji hash"
    latest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    restarted = RuntimeSessionStateStore(tmp_path)
    fresh = restarted.load_or_create(source_client="pytest")

    assert fresh.session_id != state.session_id
    assert fresh.last_user_text is None
    assert restarted.last_load_metadata["restart_continuity_status"] == "checkpoint_rejected"
    errors = restarted.last_load_metadata["ignored_checkpoint_errors"]
    assert any(item["error"] == "continuity_state_hash_mismatch" for item in errors)


def test_wake_state_mismatch_clears_conversation_carryover(tmp_path: Path) -> None:
    first = RuntimeSessionStateStore(tmp_path)
    state = first.load_or_create(source_client="pytest")
    state.update(user_text="wrażliwy poprzedni tekst", intent="private", route="memory")
    first.save(state, continuity_context=_wake("wake-old", "b" * 64), turn_count=2)

    second = RuntimeSessionStateStore(tmp_path)
    restored = second.load_or_create(source_client="pytest")
    status = second.verify_loaded_continuity(restored, _wake("wake-new", "c" * 64))

    assert status["status"] == "wake_state_binding_mismatch"
    assert status["carryover_allowed"] is False
    assert restored.last_user_text is None
    assert restored.last_intent is None
    assert restored.last_route is None
    assert second.last_load_metadata["session_carryover_blocked"] is True
    assert second.last_load_metadata["session_reused"] is False


def test_expired_checkpoint_is_not_resurrected(tmp_path: Path) -> None:
    first = RuntimeSessionStateStore(tmp_path)
    state = first.load_or_create(source_client="pytest")
    state.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    state.update(user_text="stare", intent="expired", route="expired")
    first.save(state, continuity_context=_wake(), turn_count=1)

    second = RuntimeSessionStateStore(tmp_path)
    fresh = second.load_or_create(source_client="pytest")

    assert fresh.session_id != state.session_id
    assert fresh.last_user_text is None
    assert any(
        item["error"] == "session_state_expired"
        for item in second.last_load_metadata["ignored_checkpoint_errors"]
    )


def test_no_carryover_session_does_not_replace_latest_pointer(tmp_path: Path) -> None:
    durable = RuntimeSessionStateStore(tmp_path)
    first = durable.load_or_create(source_client="pytest")
    first.update(user_text="trwały", intent="keep", route="keep")
    durable.save(first, continuity_context=_wake(), turn_count=1)

    latest = tmp_path / "workspace_runtime/runtime_session_state.json"
    original = latest.read_bytes()

    isolated = RuntimeSessionStateStore(tmp_path)
    second = isolated.load_or_create(source_client="pytest", no_carryover=True)
    second.update(user_text="izolowany", intent="drop", route="drop")
    status = isolated.save(second, continuity_context=_wake(), turn_count=1)

    assert status["session_state_saved"] is True
    assert status["latest_session_pointer_required"] is False
    assert status["latest_session_pointer_saved"] is False
    assert latest.read_bytes() == original


def test_checkpoint_replacement_leaves_no_temporary_files(tmp_path: Path) -> None:
    store = RuntimeSessionStateStore(tmp_path)
    state = store.load_or_create(source_client="pytest")
    for turn in range(1, 4):
        state.update(user_text=f"turn {turn}", intent="test", route="test")
        status = store.save(state, continuity_context=_wake(), turn_count=turn)
        assert status["session_state_saved"] is True

    temporary = list((tmp_path / "workspace_runtime").rglob("*.tmp"))
    assert temporary == []
    payload = json.loads((tmp_path / "workspace_runtime/runtime_session_state.json").read_text(encoding="utf-8"))
    assert payload["_continuity"]["generation"] == 3
    assert payload["_continuity"]["turn_count"] == 3
