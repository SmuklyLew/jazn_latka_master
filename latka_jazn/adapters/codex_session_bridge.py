from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import signal
import time
import traceback
import uuid
from typing import Any

from latka_jazn.core.runtime_session import JaznRuntimeSession


@dataclass(slots=True)
class CodexSessionBridge:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        for name in ["requests", "responses", "processed", "status"]:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    @property
    def stop_path(self) -> Path:
        return self.root / "STOP"

    @property
    def runtime_status_path(self) -> Path:
        return self.root / "status" / "runtime.json"

    def submit(self, payload: dict[str, Any]) -> Path:
        request_id = str(payload.get("id") or uuid.uuid4().hex)
        body = {**payload, "id": request_id}
        path = self.root / "requests" / f"{request_id}.json"
        self._write_json(path, body)
        return path

    def send(
        self,
        text: str,
        *,
        client: str = "codex",
        timeout_seconds: float = 120.0,
        session_id: str = "codex-live",
        direct_if_unavailable: bool = True,
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        self.submit({"id": request_id, "text": text, "client": client, "created_at_utc": _utc_now()})
        response_path = self.root / "responses" / f"{request_id}.json"
        if direct_if_unavailable and not self._has_active_server():
            self.process_pending_once(session_id=session_id)
            if response_path.exists():
                return self._read_json(response_path)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if response_path.exists():
                return self._read_json(response_path)
            if direct_if_unavailable and not self._has_active_server():
                self.process_pending_once(session_id=session_id)
                if response_path.exists():
                    return self._read_json(response_path)
            time.sleep(0.1)
        status = self.status()
        raise TimeoutError(
            f"No bridge response for {request_id} after {timeout_seconds:.1f}s "
            f"(bridge_state={status.get('state')}, queues={status.get('queues')})"
        )

    def serve(self, *, session_id: str = "codex-live", poll_seconds: float = 0.2) -> None:
        self.stop_path.unlink(missing_ok=True)
        _sanitize_runtime_environment()
        runtime = JaznRuntimeSession(session_id=session_id)
        self._write_runtime_status("active", runtime, session_id=session_id)
        try:
            while not self.stop_path.exists():
                processed = self.process_once(runtime)
                if not processed:
                    time.sleep(max(0.05, poll_seconds))
        finally:
            runtime.close()
            self._write_runtime_status("stopped", runtime, session_id=session_id)

    def process_pending_once(self, *, session_id: str = "codex-live") -> int:
        """Process queued requests without claiming that a background bridge is alive."""
        _sanitize_runtime_environment()
        runtime = JaznRuntimeSession(session_id=session_id)
        self._write_runtime_status("processing_once", runtime, session_id=session_id)
        try:
            return self.process_once(runtime, status_state="processing_once")
        finally:
            runtime.close()
            self._write_runtime_status("one_shot_complete", runtime, session_id=session_id)

    def process_once(self, runtime: JaznRuntimeSession, *, status_state: str = "active") -> int:
        count = 0
        for request_path in sorted((self.root / "requests").glob("*.json")):
            request_id = request_path.stem
            try:
                payload = self._read_json(request_path)
                request_id = str(payload.get("id") or request_id)
                response = runtime.process_user_text(str(payload.get("text") or ""), client=str(payload.get("client") or "codex_session_bridge"))
                response["request_id"] = request_id
            except Exception as exc:
                response = {
                    "request_id": request_id,
                    "ok": False,
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                }
                self._append_log(response)
            self._write_json(self.root / "responses" / f"{request_id}.json", response)
            request_path.replace(self.root / "processed" / f"{request_id}.json")
            self._write_runtime_status(status_state, runtime, session_id=runtime.state.session_id, last_request=f"{request_id}.json")
            count += 1
        return count

    def request_stop(self) -> None:
        self.stop_path.write_text(_utc_now(), encoding="utf-8")

    def status(self) -> dict[str, Any]:
        runtime = self._read_json(self.runtime_status_path) if self.runtime_status_path.exists() else {"state": "missing"}
        recorded_root = runtime.get("bridge_root")
        if runtime.get("state") in {"active", "processing_once"} and recorded_root and not _same_path(recorded_root, self.root.resolve()):
            runtime["state"] = "stale"
            runtime["stale_reason"] = "recorded_bridge_root_mismatch"
            runtime["expected_bridge_root"] = str(self.root.resolve())
        if runtime.get("state") in {"active", "processing_once"} and not _pid_running(runtime.get("pid")):
            runtime["state"] = "stale"
            runtime["stale_reason"] = "recorded_runtime_pid_is_not_running"
        runtime["queues"] = {
            name: len(list((self.root / name).glob("*.json")))
            for name in ["requests", "responses", "processed", "status"]
        }
        return runtime

    def _has_active_server(self) -> bool:
        return self.status().get("state") == "active"

    def _write_runtime_status(self, state: str, runtime: JaznRuntimeSession, *, session_id: str, last_request: str | None = None) -> None:
        prior = self._read_json(self.runtime_status_path) if self.runtime_status_path.exists() else {}
        started_epoch = time.time() if state == "active" and last_request is None else prior.get("started_epoch") or time.time()
        payload = {
            "state": state,
            "pid": os.getpid(),
            "session_id": session_id,
            "started_epoch": started_epoch,
            "runtime_version": runtime.config.version,
            "active_database": str(runtime.config.conversation_archive_manifest_path),
            "active_runtime_write_database": str(runtime.config.memory_db_path),
            "active_conversation_fts": str(runtime.config.conversation_fts_dir / "conversation_fts_0001.sqlite3"),
            "active_staging_database": str(runtime.config.conversation_staging_dir / "staging_memory_0001.sqlite3"),
            "bridge_root": str(self.root.resolve()),
            "last_request": last_request or prior.get("last_request"),
            "updated_at_utc": _utc_now(),
        }
        self._write_json(self.runtime_status_path, payload)

    def _append_log(self, payload: dict[str, Any]) -> None:
        path = self.root / "runtime.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_runtime_environment() -> None:
    # The Codex desktop environment may set this variable. Some bundled Windows
    # Python/OpenSSL combinations then abort before the runtime can answer.
    os.environ.pop("SSLKEYLOGFILE", None)


def _pid_running(pid: Any) -> bool:
    try:
        process_id = int(pid)
        if process_id <= 0:
            return False
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.restype = ctypes.c_void_p
            handle = kernel32.OpenProcess(0x1000, False, process_id)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        os.kill(process_id, signal.SIG_DFL)
        return True
    except Exception:
        return False


def _same_path(left: Any, right: Any) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))
    except Exception:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent Codex file bridge for the active Jazn runtime.")
    parser.add_argument("command", choices=["serve", "send", "status", "stop"])
    parser.add_argument("--root", default="workspace_runtime/codex_session_bridge")
    parser.add_argument("--session", default="codex-live")
    parser.add_argument("--client", default="codex")
    parser.add_argument("--text", default="")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--poll", type=float, default=0.2)
    parser.add_argument("--require-server", action="store_true", help="Nie przetwarzaj żądania jednorazowo, gdy bridge daemon nie działa.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge = CodexSessionBridge(Path(args.root))
    if args.command == "serve":
        bridge.serve(session_id=args.session, poll_seconds=args.poll)
        return 0
    if args.command == "send":
        text = args.text
        if not text:
            raise SystemExit("--text is required")
        response = bridge.send(
            text,
            client=args.client,
            timeout_seconds=args.timeout,
            session_id=args.session,
            direct_if_unavailable=not args.require_server,
        )
        print(json.dumps(response, ensure_ascii=False, indent=2) if args.json else response.get("final_visible_text") or response.get("error_message") or "")
        return 0 if response.get("ok", True) else 1
    if args.command == "stop":
        bridge.request_stop()
        print(f"Stop requested for session {args.session}")
        return 0
    print(json.dumps(bridge.status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
