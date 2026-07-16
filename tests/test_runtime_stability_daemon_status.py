from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import os
import threading
import time

from latka_jazn.config import JaznConfig
from latka_jazn.core import runtime_daemon


class _FakeSession:
    execution_count = 0

    def __init__(self, _config, **_kwargs) -> None:
        self.state = SimpleNamespace(session_id=_kwargs.get("session_id"))

    def process_user_text(self, user_text: str, **_kwargs) -> dict:
        type(self).execution_count += 1
        if user_text == "slow":
            time.sleep(0.15)
        return {"ok": True, "final_visible_text": user_text, "execution_ordinal": type(self).execution_count}

    def close(self) -> None:
        return


class _BlockingSession:
    instance_count = 0
    writes: list[str] = []
    slow_started = threading.Event()
    release_slow = threading.Event()
    slow_finished = threading.Event()

    def __init__(self, _config, **kwargs) -> None:
        type(self).instance_count += 1
        self.instance_id = type(self).instance_count
        self.state = SimpleNamespace(session_id=kwargs.get("session_id"))

    @staticmethod
    def _successful_result() -> dict:
        return {
            "ok": True,
            "final_visible_text": "[czas] Działam.",
            "final_visible_integrity": {"valid": True, "consensus": True},
            "final_visible_integrity_consensus": {"valid": True, "mismatch": False},
            "runtime_truth_gate": {"ok": True, "normal_response_allowed": True},
            "normal_response_blocked": False,
        }

    def process_user_text(self, user_text: str, *, _turn_context, **_kwargs) -> dict:
        result = self._successful_result()
        result["instance_id"] = self.instance_id
        if user_text == "slow":
            _turn_context.stage_semantic_write(
                data_type="truth_audit",
                stage="candidate_persistence_staging",
                commit=lambda: self.writes.append("late-write"),
            )
            self.slow_started.set()
            self.release_slow.wait(2.0)
            _turn_context.commit_if_allowed(result, job_status="completed")
            self.slow_finished.set()
        return result

    def close(self) -> None:
        return


def _test_server(tmp_path: Path, *, execution_timeout: float = 1.0) -> runtime_daemon.JaznDaemonServer:
    root = tmp_path.resolve()
    marker = root / "workspace_runtime" / "JAZN_ACTIVE_RUNTIME.json"
    server = runtime_daemon.JaznDaemonServer(
        ("127.0.0.1", 0),
        runtime_daemon.JaznDaemonHandler,
        config=JaznConfig(root=root),
        marker_path=marker,
        session_factory=_FakeSession,
        execution_timeout_seconds=execution_timeout,
    )
    server.write_marker = lambda **_kwargs: {"manifest_current_sha256": None}  # type: ignore[method-assign]
    return server


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


def test_lazy_worker_state_is_explicit_before_first_job(tmp_path: Path) -> None:
    server = _test_server(tmp_path)
    try:
        summary = server.chat_job_summary()
        assert summary["worker_alive"] is False
        assert summary["worker_state"] == "not_started_lazy"
    finally:
        server.close_sessions()
        server.server_close()


def test_client_wait_timeout_then_poll_completes_once_with_same_request_id(tmp_path: Path) -> None:
    _FakeSession.execution_count = 0
    server = _test_server(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    request_id = "isolated-client-timeout"
    try:
        pending = runtime_daemon.chat_daemon(
            server.config,
            "slow",
            host="127.0.0.1",
            port=port,
            request_id=request_id,
            timeout=0.02,
            poll_interval=0.005,
        )
        assert pending["error_code"] == "daemon_chat_pending"
        assert pending["client_wait_status"] == "client_wait_timeout"
        assert pending["execution_failed"] is False
        assert pending["request_id"] == request_id

        replay = runtime_daemon.chat_daemon_submit(
            server.config,
            "slow",
            host="127.0.0.1",
            port=port,
            request_id=request_id,
        )
        assert replay["request_id"] == request_id
        assert replay["idempotent_replay"] is True

        deadline = time.monotonic() + 2.0
        completed: dict = {}
        while time.monotonic() < deadline:
            completed = runtime_daemon.chat_daemon_result(
                server.config, request_id, host="127.0.0.1", port=port
            )
            if completed.get("done") is True:
                break
            time.sleep(0.01)
        assert completed["done"] is True
        assert completed["job_status"] == "completed"
        assert completed["request_id"] == request_id
        assert _FakeSession.execution_count == 1
        summary = server.chat_job_summary()
        assert summary["completed"] == 1
        assert summary["failed"] == 0
    finally:
        server.shutdown()
        server.close_sessions()
        server.server_close()
        thread.join(timeout=2.0)


def test_execution_timeout_is_terminal_and_worker_accepts_next_job(tmp_path: Path) -> None:
    _FakeSession.execution_count = 0
    server = _test_server(tmp_path, execution_timeout=0.04)
    try:
        slow, created, error = server.submit_chat_job(
            user_text="slow", input_field="test", session_id="slow-session",
            no_carryover=False, client="isolated-test", request_id="execution-timeout",
        )
        assert created is True and error is None and slow is not None
        assert slow.done_event.wait(1.0)
        assert slow.status == "execution_timeout"
        assert slow.result["error_code"] == "execution_timeout"

        fast, created, error = server.submit_chat_job(
            user_text="fast", input_field="test", session_id="fast-session",
            no_carryover=False, client="isolated-test", request_id="after-timeout",
        )
        assert created is True and error is None and fast is not None
        assert fast.done_event.wait(1.0)
        assert fast.status == "completed"
        assert server.chat_job_summary()["worker_state"] == "alive"
    finally:
        server.close_sessions()
        server.server_close()


def test_execution_timeout_replaces_poisoned_session_worker_for_same_session(tmp_path: Path) -> None:
    _BlockingSession.instance_count = 0
    _BlockingSession.writes = []
    _BlockingSession.slow_started = threading.Event()
    _BlockingSession.release_slow = threading.Event()
    _BlockingSession.slow_finished = threading.Event()
    root = tmp_path.resolve()
    marker = root / "workspace_runtime" / "JAZN_ACTIVE_RUNTIME.json"
    server = runtime_daemon.JaznDaemonServer(
        ("127.0.0.1", 0),
        runtime_daemon.JaznDaemonHandler,
        config=JaznConfig(root=root),
        marker_path=marker,
        session_factory=_BlockingSession,
        execution_timeout_seconds=0.04,
    )
    server.write_marker = lambda **_kwargs: {"manifest_current_sha256": None}  # type: ignore[method-assign]
    try:
        slow, created, error = server.submit_chat_job(
            user_text="slow", input_field="test", session_id="same-session",
            no_carryover=False, client="isolated-test", request_id="replace-timeout",
        )
        assert created is True and error is None and slow is not None
        assert _BlockingSession.slow_started.wait(1.0)
        assert slow.done_event.wait(1.0)
        assert slow.status == "execution_timeout"

        fast, created, error = server.submit_chat_job(
            user_text="fast", input_field="test", session_id="same-session",
            no_carryover=False, client="isolated-test", request_id="replace-next",
        )
        assert created is True and error is None and fast is not None
        assert fast.done_event.wait(1.0)
        assert fast.status == "completed"
        assert fast.result["instance_id"] >= 2

        _BlockingSession.release_slow.set()
        assert _BlockingSession.slow_finished.wait(1.0)
        assert _BlockingSession.writes == []
        summary = server.chat_job_summary()
        assert summary["execution_timeout"] == 1
        assert summary["completed"] == 1
        assert summary["worker_state"] == "alive"
    finally:
        _BlockingSession.release_slow.set()
        server.close_sessions()
        server.server_close()


def test_restart_recovers_nonterminal_job_without_double_execution(tmp_path: Path) -> None:
    _FakeSession.execution_count = 0
    first = _test_server(tmp_path)
    first.start_chat_worker = lambda: None  # type: ignore[method-assign]
    job, created, error = first.submit_chat_job(
        user_text="slow", input_field="test", session_id="restart-session",
        no_carryover=False, client="isolated-test", request_id="restart-request",
    )
    assert created is True and error is None and job is not None
    assert job.status == "queued"
    first.server_close()

    second = _test_server(tmp_path)
    try:
        recovered = second.get_chat_job("restart-request")
        assert recovered is not None
        assert recovered.status == "recovered_after_restart"
        assert recovered.recovery_disposition == "failed_without_replay"
        assert recovered.result["automatic_replay_performed"] is False

        replay, created, error = second.submit_chat_job(
            user_text="slow", input_field="test", session_id="restart-session",
            no_carryover=False, client="isolated-test", request_id="restart-request",
        )
        assert error is None
        assert created is False
        assert replay is recovered
        assert _FakeSession.execution_count == 0
    finally:
        second.close_sessions()
        second.server_close()
