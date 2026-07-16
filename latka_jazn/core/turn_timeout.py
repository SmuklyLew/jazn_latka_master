from __future__ import annotations

import os
import queue
import threading
import time
from typing import Any, Callable, TypeVar

from latka_jazn.core.turn_execution import TurnExecutionContext

T = TypeVar("T")

DEFAULT_RUNTIME_TURN_TIMEOUT_SECONDS = 45.0


class RuntimeTurnTimeoutError(TimeoutError):
    """Raised when a chat-facing runtime turn does not finish in bounded time."""

    def __init__(self, *, command: str, timeout_seconds: float, phase: str = "runtime_turn") -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.phase = phase
        super().__init__(
            f"{command} {phase} exceeded {timeout_seconds:.3g}s; returning a controlled timeout instead of hanging"
        )


def runtime_turn_timeout_seconds(config: object | None = None) -> float:
    raw = os.environ.get("JAZN_RUNTIME_TURN_TIMEOUT_SECONDS") or os.environ.get("JAZN_TURN_TIMEOUT")
    if raw is not None:
        try:
            value = float(raw)
            return value if value > 0 else DEFAULT_RUNTIME_TURN_TIMEOUT_SECONDS
        except Exception:
            return DEFAULT_RUNTIME_TURN_TIMEOUT_SECONDS
    configured = getattr(config, "runtime_turn_timeout_seconds", None)
    try:
        value = float(configured) if configured is not None else DEFAULT_RUNTIME_TURN_TIMEOUT_SECONDS
        return value if value > 0 else DEFAULT_RUNTIME_TURN_TIMEOUT_SECONDS
    except Exception:
        return DEFAULT_RUNTIME_TURN_TIMEOUT_SECONDS


def run_with_runtime_turn_timeout(func: Callable[[], T], *, command: str, timeout_seconds: float) -> T:
    """Run a stateless call with a daemon-thread watchdog.

    Do not use this around an object that already owns thread-bound resources
    such as sqlite3 connections.  For JaznRuntimeSession use
    RuntimeSessionWorker instead, which creates and uses the session inside one
    dedicated worker thread.
    """

    result_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def _target() -> None:
        try:
            result_queue.put(("ok", func()))
        except BaseException as exc:  # noqa: BLE001 - propagated to caller as-is
            result_queue.put(("error", exc))

    worker = threading.Thread(target=_target, name=f"jazn-{command}-turn-watchdog", daemon=True)
    worker.start()
    try:
        status, payload = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise RuntimeTurnTimeoutError(command=command, timeout_seconds=timeout_seconds) from exc
    if status == "error":
        raise payload  # type: ignore[misc]
    return payload  # type: ignore[return-value]


class RuntimeSessionWorker:
    """Own one JaznRuntimeSession in the same daemon thread for all turns.

    sqlite3 objects are thread-bound by default.  This worker prevents the chat
    watchdog from calling a session that was created in a different thread while
    still letting --chat and --chat-gpt return controlled timeout errors.
    """

    runtime_turn_timeout_managed = True

    def __init__(
        self,
        *,
        session_factory: Callable[..., Any],
        config: Any,
        session_id: str | None,
        no_carryover: bool,
        source_client: str,
        command: str,
        timeout_seconds: float | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._session_id = session_id
        self._no_carryover = no_carryover
        self._source_client = source_client
        self._command = command
        self._timeout_seconds = timeout_seconds or runtime_turn_timeout_seconds(config)
        self._requests: queue.Queue[tuple[str, Any, queue.Queue[tuple[str, object]] | None]] = queue.Queue()
        self._ready: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        self._closed = False
        self._timed_out = False
        self.state: Any = None
        self.config = config
        self.last_turn_context: TurnExecutionContext | None = None
        self._thread = threading.Thread(target=self._run, name=f"jazn-{command}-session-worker", daemon=True)
        self._thread.start()
        ready_timeout = min(max(self._timeout_seconds, 1.0), 10.0)
        try:
            status, payload = self._ready.get(timeout=ready_timeout)
        except queue.Empty as exc:
            raise RuntimeTurnTimeoutError(command=self._command, timeout_seconds=ready_timeout, phase="session_startup") from exc
        if status == "error":
            raise payload  # type: ignore[misc]
        self.state = payload

    def _run(self) -> None:
        session: Any | None = None
        try:
            session = self._session_factory(
                self._config,
                session_id=self._session_id,
                no_carryover=self._no_carryover,
                source_client=self._source_client,
            )
            self._ready.put(("ok", getattr(session, "state", None)))
        except BaseException as exc:  # noqa: BLE001
            self._ready.put(("error", exc))
            return

        while True:
            op, payload, response_queue = self._requests.get()
            if op == "close":
                try:
                    close = getattr(session, "close", None)
                    if callable(close):
                        close()
                    if response_queue is not None:
                        response_queue.put(("ok", True))
                except BaseException as exc:  # noqa: BLE001
                    if response_queue is not None:
                        response_queue.put(("error", exc))
                return
            if op == "process_user_text":
                kwargs = dict(payload)
                turn_context = kwargs.get("_turn_context")
                try:
                    assert session is not None
                    result = session.process_user_text(**kwargs)
                    if isinstance(turn_context, TurnExecutionContext) and turn_context.cancelled:
                        turn_context.mark_stage(
                            "final_result_serialization",
                            status="late_completion_ignored",
                            error_code="execution_timeout",
                        )
                        turn_context.finalize_total(status="late_completion_ignored", error_code="execution_timeout")
                        turn_context.persist_audit(event_type="runtime_turn_late_completion")
                    if response_queue is not None:
                        response_queue.put(("ok", result))
                except BaseException as exc:  # noqa: BLE001
                    if isinstance(turn_context, TurnExecutionContext) and turn_context.cancelled:
                        turn_context.record_technical_event(
                            "runtime_turn_late_exception",
                            {"error_code": type(exc).__name__, "error": str(exc)},
                        )
                        turn_context.finalize_total(status="late_exception_ignored", error_code=type(exc).__name__)
                        turn_context.persist_audit(event_type="runtime_turn_late_exception")
                    if response_queue is not None:
                        response_queue.put(("error", exc))

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    @property
    def usable(self) -> bool:
        return not self._closed and not self._timed_out and self._thread.is_alive()

    def _call(
        self,
        op: str,
        payload: Any,
        *,
        heartbeat_callback: Callable[[], None] | None = None,
        turn_context: TurnExecutionContext | None = None,
    ) -> Any:
        if self._closed:
            raise RuntimeError("RuntimeSessionWorker is closed")
        if self._timed_out:
            raise RuntimeError("RuntimeSessionWorker is retired after an execution timeout")
        response_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        self._requests.put((op, payload, response_queue))
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            if heartbeat_callback is not None:
                heartbeat_callback()

            # Prefer a result that is already available at the deadline boundary.
            # This avoids reporting a timeout merely because the scheduler woke the
            # caller a fraction late after the worker had already completed.
            try:
                status, value = response_queue.get_nowait()
                break
            except queue.Empty:
                pass

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._timed_out = True
                if turn_context is not None:
                    turn_context.cancel(
                        reason=f"{self._command} execution deadline exceeded",
                        error_code="execution_timeout",
                    )
                    turn_context.record_technical_event(
                        "runtime_turn_execution_timeout",
                        {
                            "command": self._command,
                            "timeout_seconds": self._timeout_seconds,
                            "phase": "runtime_turn",
                        },
                    )
                    turn_context.persist_audit(event_type="runtime_turn_execution_timeout")
                raise RuntimeTurnTimeoutError(command=self._command, timeout_seconds=self._timeout_seconds)
            try:
                status, value = response_queue.get(timeout=min(0.25, remaining))
                break
            except queue.Empty:
                continue
        if status == "error":
            raise value  # type: ignore[misc]
        return value

    def process_user_text(self, user_text: str, **kwargs: Any) -> dict[str, Any]:
        heartbeat_callback = kwargs.pop("_heartbeat_callback", None)
        request_id = str(kwargs.pop("request_id", "") or kwargs.pop("_request_id", "") or "") or None
        turn_context = kwargs.pop("_turn_context", None)
        if not isinstance(turn_context, TurnExecutionContext):
            turn_context = TurnExecutionContext.create(
                request_id=request_id,
                session_id=str(getattr(self.state, "session_id", None) or self._session_id or "runtime-session"),
                timeout_seconds=self._timeout_seconds,
                audit_db_path=getattr(self._config, "audit_db_path", None),
            )
        turn_context.mark_stage("session_initialization", status="reused")
        self.last_turn_context = turn_context
        payload = {"user_text": user_text, "_turn_context": turn_context, **kwargs}
        return self._call(
            "process_user_text",
            payload,
            heartbeat_callback=heartbeat_callback,
            turn_context=turn_context,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._thread.is_alive():
            return
        response_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        self._requests.put(("close", None, response_queue))
        wait_seconds = min(max(self._timeout_seconds, 1.0), 5.0)
        try:
            response_queue.get(timeout=wait_seconds)
        except queue.Empty:
            pass
        self._thread.join(timeout=wait_seconds)
