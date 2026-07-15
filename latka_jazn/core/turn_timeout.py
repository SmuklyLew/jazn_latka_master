from __future__ import annotations

import os
import queue
import threading
from typing import Any, Callable, TypeVar

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
        self.state: Any = None
        self.config = config
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
                try:
                    assert session is not None
                    result = session.process_user_text(**kwargs)
                    if response_queue is not None:
                        response_queue.put(("ok", result))
                except BaseException as exc:  # noqa: BLE001
                    if response_queue is not None:
                        response_queue.put(("error", exc))

    def _call(self, op: str, payload: Any) -> Any:
        if self._closed:
            raise RuntimeError("RuntimeSessionWorker is closed")
        response_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        self._requests.put((op, payload, response_queue))
        try:
            status, value = response_queue.get(timeout=self._timeout_seconds)
        except queue.Empty as exc:
            raise RuntimeTurnTimeoutError(command=self._command, timeout_seconds=self._timeout_seconds) from exc
        if status == "error":
            raise value  # type: ignore[misc]
        return value

    def process_user_text(self, user_text: str, **kwargs: Any) -> dict[str, Any]:
        payload = {"user_text": user_text, **kwargs}
        return self._call("process_user_text", payload)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        response_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        self._requests.put(("close", None, response_queue))
        try:
            response_queue.get(timeout=min(max(self._timeout_seconds, 1.0), 5.0))
        except queue.Empty:
            pass
