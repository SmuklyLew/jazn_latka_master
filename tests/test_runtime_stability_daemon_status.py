from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import os

from latka_jazn.config import JaznConfig
from latka_jazn.core import runtime_daemon


def _iso(age_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()


def _marker(root: Path, *, pid: int = 1234, age: int = 0) -> dict:
    return {
        "pid": pid,
        "active_root": str(root.resolve()),
        "last_heartbeat_at_utc": _iso(age),
        "heartbeat_interval_seconds": 10,
        "timestamp_contract": {"trusted": False, "source": "local_machine"},
    }


def _ping(root: Path, *, pid: int = 1234, age: int = 0, trusted: bool = False) -> dict:
    return {
        "daemon_pid": pid,
        "runtime_process_active": True,
        "active_root": str(root.resolve()),
        "last_heartbeat_at_utc": _iso(age),
        "heartbeat_interval_seconds": 10,
        "timestamp_trusted": trusted,
        "timestamp_contract": {"trusted": trusted, "source": "test_network" if trusted else "local_machine"},
    }


def _install(monkeypatch, root: Path, marker: dict, ping: dict | None, *, pid_alive: bool = True) -> None:
    monkeypatch.setattr(runtime_daemon, "resolve_active_runtime_marker_path", lambda *_args, **_kwargs: root / "marker.json")
    monkeypatch.setattr(runtime_daemon, "read_json_file", lambda _path: marker)
    monkeypatch.setattr(
        runtime_daemon,
        "resolve_active_runtime_root",
        lambda *_args, **_kwargs: SimpleNamespace(root=root.resolve(), marker_valid=True, source="marker", error=None),
    )
    monkeypatch.setattr(runtime_daemon, "pid_is_alive", lambda _pid: pid_alive)
    monkeypatch.setattr(runtime_daemon, "_probe_daemon_status", lambda *_args, **_kwargs: (ping, None if ping else "timeout", "/ready" if ping else None))


def test_healthy_daemon_is_active_trusted_even_with_local_time(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    _install(monkeypatch, root, _marker(root), _ping(root, trusted=False))
    result = runtime_daemon.status_daemon(JaznConfig(root=root))
    assert result["active_state"] == "active_trusted"
    assert result["readiness_state"] == "ready"
    assert result["time_trust_state"] == "local_machine_unverified"


def test_snapshot_does_not_probe_or_claim_live_verification(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    marker = _marker(root)
    _install(monkeypatch, root, marker, None)
    monkeypatch.setattr(runtime_daemon, "_probe_daemon_status", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("HTTP called")))
    result = runtime_daemon.status_daemon(JaznConfig(root=root), probe_endpoint=False)
    assert result["endpoint_probe_performed"] is False
    assert result["observation_state"] == "endpoint_not_probed"
    assert result["active_state"] == "active_unverified"
    assert result["process_identity_confirmed"] is False


def test_pid_and_root_mismatches_fail_closed(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    _install(monkeypatch, root, _marker(root), _ping(root, pid=9999))
    assert runtime_daemon.status_daemon(JaznConfig(root=root))["active_state_reason"] == "endpoint_pid_mismatch"

    wrong = tmp_path / "other"
    wrong.mkdir()
    _install(monkeypatch, root, _marker(root), _ping(wrong))
    assert runtime_daemon.status_daemon(JaznConfig(root=root))["active_state_reason"] == "endpoint_runtime_root_mismatch"


def test_stale_heartbeat_is_degraded(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    _install(monkeypatch, root, _marker(root, age=300), _ping(root, age=300))
    result = runtime_daemon.status_daemon(JaznConfig(root=root))
    assert result["active_state"] == "active_degraded"
    assert result["heartbeat_state"] == "stale"


def test_endpoint_timeout_with_fresh_marker_is_degraded(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    _install(monkeypatch, root, _marker(root), None)
    result = runtime_daemon.status_daemon(JaznConfig(root=root))
    assert result["active_state"] == "active_degraded"
    assert result["active_state_reason"] == "fresh_marker_and_live_pid_endpoint_unreachable"


def test_dead_pid_is_inactive(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    _install(monkeypatch, root, _marker(root), None, pid_alive=False)
    result = runtime_daemon.status_daemon(JaznConfig(root=root))
    assert result["active_state"] == "inactive"
    assert result["process_state"] == "dead"


def test_probe_retries_and_third_attempt_can_succeed(monkeypatch) -> None:
    calls: list[str] = []

    def fake_http(_method: str, url: str, *, timeout: float) -> dict:
        calls.append(url)
        if len(calls) < 3:
            raise TimeoutError("temporary")
        return {"runtime_process_active": True}

    monkeypatch.setattr(runtime_daemon, "http_json", fake_http)
    monkeypatch.setattr(runtime_daemon.time, "sleep", lambda _seconds: None)
    payload, error, endpoint = runtime_daemon._probe_daemon_status("127.0.0.1", 8787)
    assert error is None
    assert endpoint == "/ready"
    assert payload["endpoint_probe_attempt"] == 3
    assert len(calls) == 3


def test_windows_pid_probe_distinguishes_live_and_missing_process() -> None:
    assert runtime_daemon.pid_is_alive(os.getpid()) is True
    assert runtime_daemon.pid_is_alive(2_147_483_647) is False
