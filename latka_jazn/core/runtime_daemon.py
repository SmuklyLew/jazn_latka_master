from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Callable
import hashlib
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from latka_jazn.config import JaznConfig
from latka_jazn.core.runtime_root import (
    active_runtime_marker_path,
    find_start_file,
    resolve_active_runtime_marker_path,
    resolve_active_runtime_root,
)
from latka_jazn.core.clock import (
    TRUSTED_HOST_TIME_ISO_ENV_NAMES,
    TRUSTED_HOST_TIME_MAX_AGE_ENV_NAMES,
    TRUSTED_HOST_TIME_MONOTONIC_ANCHOR_ENV_NAMES,
    TRUSTED_HOST_TIME_SOURCE_ENV_NAMES,
    WarsawClock,
)
from latka_jazn.core.runtime_session import JaznRuntimeSession
from latka_jazn.core.turn_timeout import (
    RuntimeSessionWorker,
    RuntimeTurnTimeoutError,
    runtime_turn_timeout_seconds,
)
from latka_jazn.core.turn_execution import TurnExecutionContext
from latka_jazn.core.runtime_truth_gate import daemon_active_state, time_trust_state
from latka_jazn.memory.runtime_write_access_contract import build_runtime_write_access_status
from latka_jazn.tools.active_extraction_cache import (
    build_active_runtime_status,
    write_active_runtime_marker,
)
from latka_jazn.version import PACKAGE_VERSION, PACKAGE_VERSION_FULL, schema_version

DEFAULT_DAEMON_HOST = "127.0.0.1"
DEFAULT_DAEMON_PORT = 8787
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_START_TIMEOUT_SECONDS = 12.0
DEFAULT_STOP_TIMEOUT_SECONDS = 5.0
DEFAULT_HTTP_TIMEOUT_SECONDS = 2.0
DEFAULT_STATUS_HTTP_TIMEOUT_SECONDS = 3.0
DEFAULT_LITE_STATUS_HTTP_TIMEOUT_SECONDS = 0.75
DEFAULT_DAEMON_CHAT_TIMEOUT_SECONDS = 180.0
DEFAULT_DAEMON_CHAT_CLI_WAIT_BUDGET_SECONDS = 30.0
DEFAULT_DAEMON_CHAT_INLINE_WAIT_SECONDS = 0.5
DEFAULT_DAEMON_CHAT_POLL_INTERVAL_SECONDS = 0.25
DEFAULT_DAEMON_CHAT_JOB_TTL_SECONDS = 3600.0
DEFAULT_DAEMON_CHAT_QUEUE_SIZE = 64
DEFAULT_HEARTBEAT_FRESH_MULTIPLIER = 3.0
DEFAULT_TIMESTAMP_BACKGROUND_REFRESH_MIN_SECONDS = 20.0
DEFAULT_TIMESTAMP_BACKGROUND_REFRESH_TIMEOUT_SECONDS = 0.35
DEFAULT_DAEMON_TRUSTED_TIME_HOLD_SECONDS = 120.0
DAEMON_MAX_BODY_BYTES = 1_000_000
DAEMON_SCHEMA_VERSION = schema_version("persistent_daemon_runtime", version=PACKAGE_VERSION_FULL)
DAEMON_MARKER_STATUS_ACTIVE = "active_daemon_runtime"
DAEMON_MARKER_STATUS_STOPPED = "stopped_daemon_runtime"
LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost"}
DAEMON_CHAT_JOB_TERMINAL_STATES = {"completed", "failed", "execution_timeout", "cancelled", "recovered_after_restart"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def daemon_default_marker_path(root: Path) -> Path:
    return active_runtime_marker_path(root)


def daemon_pid_path(root: Path) -> Path:
    return Path(root).resolve() / "workspace_runtime" / "jazn_daemon.pid"


def daemon_log_dir(root: Path) -> Path:
    return Path(root).resolve() / "workspace_runtime" / "daemon"


def daemon_url(host: str = DEFAULT_DAEMON_HOST, port: int = DEFAULT_DAEMON_PORT, path: str = "/status") -> str:
    return f"http://{host}:{int(port)}{path}"


def read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


_JSON_ATOMIC_WRITE_LOCK = threading.RLock()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with _JSON_ATOMIC_WRITE_LOCK:
            tmp.write_text(serialized, encoding="utf-8")
            last_error: PermissionError | None = None
            for attempt in range(6):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError as exc:
                    last_error = exc
                    if attempt == 5:
                        raise
                    time.sleep(0.02 * (attempt + 1))
            if last_error is not None:
                raise last_error
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _env_bool_value(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "tak", "on"}


def _env_float_value(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int_value(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _first_env_value(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for name in names:
        value = str(os.environ.get(name, "")).strip()
        if value:
            return value, name
    return None, None


def trusted_host_time_env_available() -> bool:
    """True only when a host/loader supplied an explicit trusted timestamp."""
    value, _name = _first_env_value(TRUSTED_HOST_TIME_ISO_ENV_NAMES)
    return bool(value)


def apply_daemon_trusted_time_env(
    *,
    trusted_time_iso: str | None = None,
    source: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, Any]:
    """Inject a host-provided trusted timestamp for this process and daemon children.

    ChatGPT-hosted runs cannot rely on Python network access from the sandbox.
    The loader can pass its trusted current timestamp into the runtime with
    --trusted-time-iso, JAZN_TRUSTED_TIME_ISO, or one of the explicit host
    aliases such as CHATGPT_HOST_TIME_ISO / OPENAI_HOST_TIME_ISO.  This helper
    only records supplied host data; it never invents trusted time from the
    local system clock.
    """
    changed: list[str] = []
    alias_used: str | None = None
    explicit_iso_supplied = bool(str(trusted_time_iso or "").strip())
    supplied_iso = str(trusted_time_iso or "").strip()
    if not supplied_iso:
        supplied_iso, alias_used = _first_env_value(TRUSTED_HOST_TIME_ISO_ENV_NAMES)
        supplied_iso = str(supplied_iso or "").strip()
    if supplied_iso:
        os.environ["JAZN_TRUSTED_TIME_ISO"] = supplied_iso
        changed.append("JAZN_TRUSTED_TIME_ISO")
        if alias_used and alias_used != "JAZN_TRUSTED_TIME_ISO":
            changed.append(f"canonicalized:{alias_used}->JAZN_TRUSTED_TIME_ISO")

        anchor_name = TRUSTED_HOST_TIME_MONOTONIC_ANCHOR_ENV_NAMES[0]
        anchor_present = bool(str(os.environ.get(anchor_name, "")).strip())
        if explicit_iso_supplied or alias_used or not anchor_present:
            os.environ[anchor_name] = str(time.monotonic_ns())
            changed.append(anchor_name)

    supplied_source = str(source or "").strip()
    if not supplied_source:
        supplied_source, source_alias = _first_env_value(TRUSTED_HOST_TIME_SOURCE_ENV_NAMES)
        supplied_source = str(supplied_source or "").strip()
        if source_alias and source_alias != "JAZN_TRUSTED_TIME_SOURCE":
            changed.append(f"canonicalized:{source_alias}->JAZN_TRUSTED_TIME_SOURCE")
    if not supplied_source and alias_used and alias_used != "JAZN_TRUSTED_TIME_ISO":
        supplied_source = f"{alias_used.lower()}_alias"
    if supplied_source:
        os.environ["JAZN_TRUSTED_TIME_SOURCE"] = supplied_source
        changed.append("JAZN_TRUSTED_TIME_SOURCE")

    supplied_max_age = max_age_seconds
    max_age_alias: str | None = None
    if supplied_max_age is None:
        raw_max_age, max_age_alias = _first_env_value(TRUSTED_HOST_TIME_MAX_AGE_ENV_NAMES)
        if raw_max_age:
            try:
                supplied_max_age = int(raw_max_age)
            except (TypeError, ValueError):
                supplied_max_age = None
    if supplied_max_age is not None and int(supplied_max_age) > 0:
        os.environ["JAZN_TRUSTED_TIME_MAX_AGE_SECONDS"] = str(int(supplied_max_age))
        changed.append("JAZN_TRUSTED_TIME_MAX_AGE_SECONDS")
        if max_age_alias and max_age_alias != "JAZN_TRUSTED_TIME_MAX_AGE_SECONDS":
            changed.append(f"canonicalized:{max_age_alias}->JAZN_TRUSTED_TIME_MAX_AGE_SECONDS")

    canonical_present = bool(os.environ.get("JAZN_TRUSTED_TIME_ISO", "").strip())
    return {
        "trusted_time_env_present": canonical_present,
        "trusted_time_env_alias_used": alias_used,
        "trusted_time_source": os.environ.get("JAZN_TRUSTED_TIME_SOURCE", "chatgpt_loader_time"),
        "trusted_time_max_age_seconds": _env_int_value("JAZN_TRUSTED_TIME_MAX_AGE_SECONDS", 120),
        "trusted_time_monotonic_anchor_present": bool(
            str(os.environ.get(TRUSTED_HOST_TIME_MONOTONIC_ANCHOR_ENV_NAMES[0], "")).strip()
        ),
        "changed_env": changed,
        "truth_boundary": "Trusted time is accepted only when explicitly injected by the host/loader or supplied through the environment; the daemon must not silently promote local fallback time to trusted.",
    }


def daemon_timestamp_contract(
    config: JaznConfig,
    *,
    network_first: bool | None = None,
    timeout_seconds: float | None = None,
    reason: str = "direct",
) -> dict[str, Any]:
    # /status nadal musi być szybki, ale nie może z definicji fałszować stanu
    # czasu. Jeśli daemon może pobrać zaufany czas sieciowy albo dostał świeży
    # czas wstrzyknięty przez loader, marker ma prawo przejść w active_trusted.
    # Gdy sieć zawiedzie, wracamy do jawnego active_degraded.
    clock = WarsawClock(config.timezone)
    network_allowed = bool(getattr(config, "allow_network", True)) and _env_bool_value("JAZN_DAEMON_STATUS_NETWORK_TIME", True)
    if network_first is None:
        network_first = network_allowed and bool(getattr(config, "network_time_first", True))
    else:
        network_first = bool(network_first) and network_allowed
    if timeout_seconds is None:
        timeout_seconds = _env_float_value("JAZN_DAEMON_STATUS_NETWORK_TIME_TIMEOUT", 0.8)
    sample = clock.now(
        network_first=network_first,
        allow_fallback=True,
        timeout_seconds=float(timeout_seconds),
    )
    contract = clock.sample_contract(sample)
    contract["daemon_status_network_time_checked"] = bool(network_first)
    contract["daemon_status_network_time_allowed"] = network_allowed
    contract["daemon_status_network_time_timeout_seconds"] = float(timeout_seconds)
    contract["daemon_status_refresh_reason"] = reason
    contract["time_trust_state"] = time_trust_state(
        timestamp_trusted=bool(contract.get("trusted")),
        timestamp_source=contract.get("source"),
        time_error=contract.get("error"),
    )
    contract["does_not_block_startup"] = True
    contract["runtime_startup_blocking"] = False
    if contract.get("trusted") is True:
        if str(contract.get("timestamp_source") or "") == "host_injected":
            contract["daemon_status_time_mode"] = "trusted_host_time_confirmed_network_unavailable"
        else:
            contract["daemon_status_time_mode"] = "trusted_time_confirmed"
        contract["error"] = None
    else:
        contract["daemon_status_time_mode"] = "local_machine_unverified_nonblocking"
        contract["error"] = contract.get("error") or "trusted network or injected time unavailable; using explicit local machine fallback without blocking runtime startup"
    return contract


_POLISH_WEEKDAYS = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")


def _project_retained_trusted_time(
    previous: dict[str, Any],
    *,
    elapsed_seconds: float,
    reason: str,
    hold_seconds: float,
) -> dict[str, Any] | None:
    """Advance a recently trusted sample by monotonic elapsed time.

    This never promotes local fallback time. It only preserves a sample that was
    already trusted and labels the retention explicitly. Once the hold TTL ends,
    callers must return to degraded local time.
    """
    if previous.get("trusted") is not True:
        return None
    raw = str(previous.get("sample_iso") or previous.get("local_iso") or "").strip()
    if not raw or elapsed_seconds < 0 or elapsed_seconds > hold_seconds:
        return None
    try:
        base = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if base.tzinfo is None:
            return None
        local = (base + timedelta(seconds=elapsed_seconds)).astimezone(ZoneInfo("Europe/Warsaw"))
    except Exception:
        return None
    offset = local.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    gmt = f"GMT{sign}{hours}" + (f":{minutes:02d}" if minutes else "")
    header = f"[🕒 {local:%Y-%m-%d %H:%M:%S} {gmt}, {_POLISH_WEEKDAYS[local.weekday()]}, Europe/Warsaw]"
    retained = dict(previous)
    retained.update({
        "sample_iso": local.isoformat(),
        "local_iso": local.isoformat(),
        "utc_iso": local.astimezone(timezone.utc).isoformat(),
        "human_time_header": header,
        "timestamp_header": header,
        "source": "trusted_time_retention",
        "timestamp_source": "runtime_projected_trusted_time",
        "timestamp_source_detail": str(previous.get("source") or previous.get("timestamp_source_detail") or "trusted_sample"),
        "trusted": True,
        "timestamp_trusted": True,
        "timestamp_freshness_ok": True,
        "timestamp_freshness_seconds": 0,
        "trusted_source_age_seconds": round(float(elapsed_seconds), 3),
        "trusted_time_hold_seconds": float(hold_seconds),
        "trusted_time_retained": True,
        "daemon_status_refresh_reason": reason,
        "daemon_status_time_mode": "trusted_time_retained_with_monotonic_projection",
        "time_trust_state": "trusted_time_retained",
        "error": None,
        "degradation_reason": None,
    })
    return retained


def pid_is_alive(pid: int | None) -> bool:
    if not pid or int(pid) <= 0:
        return False
    if os.name == "posix":
        # ``kill(pid, 0)`` reports zombie children as existing even though they
        # can no longer serve requests.  Treat terminal /proc states as dead so
        # daemon stop can complete without weakening the endpoint+PID identity
        # checks used before shutdown.
        try:
            stat_text = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
            closing = stat_text.rfind(")")
            state = stat_text[closing + 2 : closing + 3] if closing >= 0 else ""
            if state in {"Z", "X", "x"}:
                return False
        except (FileNotFoundError, ProcessLookupError):
            return False
        except (OSError, ValueError):
            # /proc may be unavailable in some POSIX environments; the portable
            # signal probe below remains the fallback.
            pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            process_query_limited_information = 0x1000
            still_active = 259
            handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
            if not handle:
                return ctypes.get_last_error() == 5  # access denied still proves an existing PID
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return int(exit_code.value) == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            # Fall through to the portable probe only if the Windows API is
            # unexpectedly unavailable.
            pass
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def extract_daemon_user_text(payload: dict[str, Any] | str) -> tuple[str, str]:
    if isinstance(payload, str):
        return payload.strip(), "plain_text"
    if not isinstance(payload, dict):
        return "", "invalid_payload"
    for field_name in ("message", "text", "user_text", "content", "prompt"):
        value = payload.get(field_name)
        if value is not None and str(value).strip():
            return str(value).strip(), f"json.{field_name}"
    messages = payload.get("messages")
    if isinstance(messages, list):
        fallback = ""
        for item in messages:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if isinstance(part, dict) and part.get("text") is not None:
                        parts.append(str(part.get("text")))
                    elif part is not None:
                        parts.append(str(part))
                text = "".join(parts).strip()
            else:
                text = str(content or "").strip()
            if not text:
                continue
            fallback = text
            if str(item.get("role") or "").lower() == "user":
                return text, "json.messages[user].content"
        if fallback:
            return fallback, "json.messages[].content"
    return "", "missing_message"


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _age_seconds(value: str | None, *, now: datetime | None = None) -> float | None:
    parsed = _parse_iso_utc(value)
    if parsed is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - parsed).total_seconds())


def _heartbeat_fresh(marker: dict[str, Any] | None) -> tuple[bool, float | None, float]:
    if not isinstance(marker, dict):
        return False, None, DEFAULT_HEARTBEAT_INTERVAL_SECONDS * DEFAULT_HEARTBEAT_FRESH_MULTIPLIER
    interval = marker.get("heartbeat_interval_seconds") or (marker.get("runtime_daemon") or {}).get("heartbeat_interval_seconds") or DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    try:
        interval_f = max(1.0, float(interval))
    except (TypeError, ValueError):
        interval_f = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    threshold = max(interval_f * DEFAULT_HEARTBEAT_FRESH_MULTIPLIER, interval_f + 10.0)
    age = _age_seconds(marker.get("last_heartbeat_at_utc") or (marker.get("runtime_daemon") or {}).get("last_heartbeat_at_utc"))
    return bool(age is not None and age <= threshold), age, threshold


@dataclass(slots=True)
class DaemonRuntimeState:
    root: str
    host: str = DEFAULT_DAEMON_HOST
    port: int = DEFAULT_DAEMON_PORT
    pid: int = field(default_factory=os.getpid)
    started_at_utc: str = field(default_factory=utc_now_iso)
    last_heartbeat_at_utc: str = field(default_factory=utc_now_iso)
    request_count: int = 0
    turn_count: int = 0
    sessions: int = 0
    status: str = DAEMON_MARKER_STATUS_ACTIVE
    last_request_at_utc: str | None = None
    last_status_latency_ms: int | None = None
    response_write_error_count: int = 0
    last_response_write_error: str | None = None
    timestamp_refresh_count: int = 0
    timestamp_refresh_in_progress: bool = False
    last_timestamp_refresh_at_utc: str | None = None
    chat_job_submitted_count: int = 0
    chat_job_completed_count: int = 0
    chat_job_failed_count: int = 0
    chat_job_execution_timeout_count: int = 0
    chat_job_cancelled_count: int = 0
    chat_job_recovered_count: int = 0
    chat_job_pending_count: int = 0
    chat_job_queued_count: int = 0
    chat_job_running_count: int = 0
    last_chat_job_id: str | None = None

    def touch(self) -> None:
        self.last_heartbeat_at_utc = utc_now_iso()

    def note_request(self, *, latency_ms: int | None = None) -> None:
        self.request_count += 1
        self.last_request_at_utc = utc_now_iso()
        if latency_ms is not None:
            self.last_status_latency_ms = int(latency_ms)

    def uptime_seconds(self) -> float | None:
        started = _parse_iso_utc(self.started_at_utc)
        if started is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["uptime_seconds"] = self.uptime_seconds()
        payload.update({
            "submitted_total": self.chat_job_submitted_count,
            "accepted_total": self.chat_job_submitted_count,
            "completed_total": self.chat_job_completed_count,
            "failed_non_timeout_total": self.chat_job_failed_count,
            "execution_timeout_total": self.chat_job_execution_timeout_count,
            "cancelled_total": self.chat_job_cancelled_count,
            "recovered_total": self.chat_job_recovered_count,
            "terminal_failure_total": (
                self.chat_job_failed_count
                + self.chat_job_execution_timeout_count
                + self.chat_job_cancelled_count
                + self.chat_job_recovered_count
            ),
            "pending_current": self.chat_job_pending_count,
            "queued_current": self.chat_job_queued_count,
            "running_current": self.chat_job_running_count,
        })
        return payload


@dataclass(slots=True)
class DaemonChatJob:
    request_id: str
    user_text: str
    input_field: str
    session_id: str | None
    no_carryover: bool
    client: str
    request_fingerprint: str | None = None
    created_at_utc: str = field(default_factory=utc_now_iso)
    started_at_utc: str | None = None
    completed_at_utc: str | None = None
    status: str = "queued"
    result: dict[str, Any] | None = None
    error: str | None = None
    last_heartbeat_at_utc: str | None = None
    execution_timeout_seconds: float | None = None
    recovery_disposition: str | None = None
    turn_context: TurnExecutionContext | None = field(default=None, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def terminal(self) -> bool:
        return self.status in DAEMON_CHAT_JOB_TERMINAL_STATES

    def snapshot(self, *, include_result: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": DAEMON_SCHEMA_VERSION,
            "ok": bool(self.status == "completed" and isinstance(self.result, dict) and self.result.get("ok", True)),
            "accepted": True,
            "request_id": self.request_id,
            "job_status": self.status,
            "done": self.terminal(),
            "created_at_utc": self.created_at_utc,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "session_id": self.session_id,
            "client": self.client,
            "error": self.error,
            "last_heartbeat_at_utc": self.last_heartbeat_at_utc,
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "recovery_disposition": self.recovery_disposition,
            "result_endpoint": f"/chat-result/{urllib.parse.quote(self.request_id, safe='')}",
        }
        if self.turn_context is not None:
            payload["turn_telemetry"] = self.turn_context.snapshot()
        if include_result and self.result is not None:
            payload["result"] = self.result
        return payload


def normalize_daemon_request_id(value: str | None) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return str(uuid.uuid4())
    if len(candidate) > 128:
        raise ValueError("request_id_too_long")
    if any(ch.isspace() or ch in {"/", "\\", "?", "#"} for ch in candidate):
        raise ValueError("request_id_contains_unsafe_characters")
    return candidate


def daemon_chat_request_fingerprint(
    *, user_text: str, session_id: str | None, no_carryover: bool, client: str
) -> str:
    canonical = json.dumps(
        {
            "user_text": str(user_text),
            "session_id": session_id,
            "no_carryover": bool(no_carryover),
            "client": str(client),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class JaznDaemonServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        config: JaznConfig,
        marker_path: Path,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        session_factory: Callable[..., Any] = JaznRuntimeSession,
        execution_timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.config = config
        self.marker_path = Path(marker_path)
        self.heartbeat_interval = float(heartbeat_interval)
        self._session_factory = session_factory
        self.execution_timeout_seconds = float(
            execution_timeout_seconds or runtime_turn_timeout_seconds(config)
        )
        self.sessions: dict[str, RuntimeSessionWorker] = {}
        bound_host, bound_port = self.server_address[:2]
        self.state = DaemonRuntimeState(root=str(config.root), host=str(bound_host), port=int(bound_port))
        self.shutdown_requested = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._timestamp_lock = threading.Lock()
        self._timestamp_contract: dict[str, Any] | None = None
        self._timestamp_contract_updated_at_utc: str | None = None
        self._trusted_timestamp_contract: dict[str, Any] | None = None
        self._trusted_timestamp_monotonic: float | None = None
        self._timestamp_refresh_thread: threading.Thread | None = None
        self._last_timestamp_refresh_started_monotonic = 0.0
        self._sessions_lock = threading.RLock()
        self.chat_jobs: dict[str, DaemonChatJob] = {}
        self._chat_jobs_lock = threading.RLock()
        self._chat_queue: queue.Queue[DaemonChatJob | None] = queue.Queue(
            maxsize=max(1, _env_int_value("JAZN_DAEMON_CHAT_QUEUE_SIZE", DEFAULT_DAEMON_CHAT_QUEUE_SIZE))
        )
        self._chat_worker_thread: threading.Thread | None = None
        self._chat_worker_start_lock = threading.Lock()
        self._chat_worker_state = "not_started_lazy"
        self._chat_worker_error: str | None = None
        self._chat_state_path = self.marker_path.parent / "daemon_chat_jobs.json"
        self._recover_chat_jobs()

    def _close_session_worker_async(self, worker: RuntimeSessionWorker) -> None:
        def _close() -> None:
            try:
                worker.close()
            except Exception:
                return

        threading.Thread(
            target=_close,
            name="jazn-retired-session-worker-close",
            daemon=True,
        ).start()

    def _retire_session_worker(self, worker: RuntimeSessionWorker) -> None:
        with self._sessions_lock:
            retired_keys = [key for key, value in self.sessions.items() if value is worker]
            for key in retired_keys:
                self.sessions.pop(key, None)
        self._close_session_worker_async(worker)

    def get_session(self, session_id: str | None, *, no_carryover: bool = False, client: str = "daemon_http") -> tuple[RuntimeSessionWorker, str]:
        with self._sessions_lock:
            if session_id:
                existing = self.sessions.get(session_id)
                if existing is not None and not existing.usable:
                    self.sessions.pop(session_id, None)
                    self._close_session_worker_async(existing)
                    existing = None
                if existing is None:
                    self.sessions[session_id] = RuntimeSessionWorker(
                        session_factory=self._session_factory,
                        config=self.config,
                        session_id=session_id,
                        no_carryover=no_carryover,
                        source_client=client,
                        command="daemon-chat",
                        timeout_seconds=self.execution_timeout_seconds,
                    )
                return self.sessions[session_id], "payload"
            generated = f"daemon-{uuid.uuid4()}"
            self.sessions[generated] = RuntimeSessionWorker(
                session_factory=self._session_factory,
                config=self.config,
                session_id=generated,
                no_carryover=no_carryover,
                source_client=client,
                command="daemon-chat",
                timeout_seconds=self.execution_timeout_seconds,
            )
            return self.sessions[generated], "generated"

    def _persist_chat_jobs_locked(self) -> None:
        recoverable = []
        for job in self.chat_jobs.values():
            if job.terminal():
                continue
            recoverable.append({
                "request_id": job.request_id,
                "request_fingerprint": job.request_fingerprint,
                "input_field": job.input_field,
                "session_id": job.session_id,
                "no_carryover": job.no_carryover,
                "client": job.client,
                "created_at_utc": job.created_at_utc,
                "started_at_utc": job.started_at_utc,
                "status": job.status,
                "last_heartbeat_at_utc": job.last_heartbeat_at_utc,
                "execution_timeout_seconds": job.execution_timeout_seconds,
            })
        write_json_atomic(
            self._chat_state_path,
            {
                "schema_version": DAEMON_SCHEMA_VERSION,
                "persistence_contract": "daemon_chat_recovery_metadata/v1",
                "contains_user_text": False,
                "jobs": recoverable,
            },
        )

    def _recover_chat_jobs(self) -> None:
        payload = read_json_file(self._chat_state_path)
        if not isinstance(payload, dict):
            return
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            return
        for raw in jobs:
            if not isinstance(raw, dict) or str(raw.get("status")) not in {"accepted", "queued", "running", "starting"}:
                continue
            request_id = str(raw.get("request_id") or "").strip()
            request_fingerprint = str(raw.get("request_fingerprint") or "").strip()
            if not request_id or not request_fingerprint:
                continue
            recovered = DaemonChatJob(
                request_id=request_id,
                user_text="",
                input_field=str(raw.get("input_field") or "recovered_metadata"),
                session_id=str(raw.get("session_id")) if raw.get("session_id") is not None else None,
                no_carryover=bool(raw.get("no_carryover")),
                client=str(raw.get("client") or "daemon_http"),
                request_fingerprint=request_fingerprint,
                created_at_utc=str(raw.get("created_at_utc") or utc_now_iso()),
                started_at_utc=str(raw.get("started_at_utc")) if raw.get("started_at_utc") else None,
                completed_at_utc=utc_now_iso(),
                status="recovered_after_restart",
                error="interrupted daemon job recovered without automatic replay",
                last_heartbeat_at_utc=str(raw.get("last_heartbeat_at_utc")) if raw.get("last_heartbeat_at_utc") else None,
                execution_timeout_seconds=float(raw.get("execution_timeout_seconds") or self.execution_timeout_seconds),
                recovery_disposition="failed_without_replay",
                result={
                    "ok": False,
                    "error_code": "recovered_after_restart",
                    "request_id": request_id,
                    "recovery_disposition": "failed_without_replay",
                    "automatic_replay_performed": False,
                },
            )
            recovered.done_event.set()
            self.chat_jobs[request_id] = recovered
            self.state.chat_job_recovered_count += 1
        if self.chat_jobs:
            self._persist_chat_jobs_locked()

    def _cleanup_chat_jobs_locked(self) -> None:
        ttl_seconds = max(60.0, _env_float_value("JAZN_DAEMON_CHAT_JOB_TTL_SECONDS", DEFAULT_DAEMON_CHAT_JOB_TTL_SECONDS))
        now = datetime.now(timezone.utc)
        stale_ids: list[str] = []
        for request_id, job in self.chat_jobs.items():
            if not job.terminal():
                continue
            completed = _parse_iso_utc(job.completed_at_utc)
            if completed is not None and (now - completed).total_seconds() > ttl_seconds:
                stale_ids.append(request_id)
        for request_id in stale_ids:
            self.chat_jobs.pop(request_id, None)

    def chat_job_summary(self) -> dict[str, Any]:
        with self._chat_jobs_lock:
            self._cleanup_chat_jobs_locked()
            counts = {
                "accepted": 0, "queued": 0, "running": 0, "completed": 0,
                "failed": 0, "execution_timeout": 0, "cancelled": 0, "recovered_after_restart": 0,
            }
            for job in self.chat_jobs.values():
                counts[job.status] = counts.get(job.status, 0) + 1
            pending = counts.get("accepted", 0) + counts.get("queued", 0) + counts.get("running", 0)
            self.state.chat_job_pending_count = pending
            self.state.chat_job_queued_count = counts.get("accepted", 0) + counts.get("queued", 0)
            self.state.chat_job_running_count = counts.get("running", 0)
            worker_alive = bool(self._chat_worker_thread and self._chat_worker_thread.is_alive())
            return {
                "queue_size": self._chat_queue.qsize(),
                "queue_capacity": self._chat_queue.maxsize,
                "worker_alive": worker_alive,
                "worker_state": self._chat_worker_state,
                "worker_error": self._chat_worker_error,
                "execution_timeout_seconds": self.execution_timeout_seconds,
                "pending": pending,
                "submitted_total": self.state.chat_job_submitted_count,
                "accepted_total": self.state.chat_job_submitted_count,
                "completed_total": self.state.chat_job_completed_count,
                "failed_non_timeout_total": self.state.chat_job_failed_count,
                "execution_timeout_total": self.state.chat_job_execution_timeout_count,
                "cancelled_total": self.state.chat_job_cancelled_count,
                "recovered_total": self.state.chat_job_recovered_count,
                "terminal_failure_total": (
                    self.state.chat_job_failed_count
                    + self.state.chat_job_execution_timeout_count
                    + self.state.chat_job_cancelled_count
                    + self.state.chat_job_recovered_count
                ),
                "pending_current": pending,
                "queued_current": self.state.chat_job_queued_count,
                "running_current": self.state.chat_job_running_count,
                **counts,
            }

    def start_chat_worker(self) -> None:
        with self._chat_worker_start_lock:
            if self._chat_worker_thread is not None and self._chat_worker_thread.is_alive():
                return
            self._chat_worker_state = "starting"
            self._chat_worker_error = None

            def worker() -> None:
                self._chat_worker_state = "alive"
                try:
                    while not self.shutdown_requested.is_set():
                        try:
                            job = self._chat_queue.get(timeout=0.25)
                        except queue.Empty:
                            continue
                        if job is None:
                            self._chat_queue.task_done()
                            break
                        self._process_chat_job(job)
                        self._chat_queue.task_done()
                except BaseException as exc:  # noqa: BLE001 - status must expose worker failure
                    self._chat_worker_state = "failed"
                    self._chat_worker_error = f"{type(exc).__name__}: {exc}"
                    raise
                finally:
                    if self._chat_worker_state != "failed":
                        self._chat_worker_state = "stopped"

            self._chat_worker_thread = threading.Thread(
                target=worker,
                name="jazn-daemon-chat-worker",
                daemon=True,
            )
            self._chat_worker_thread.start()

    def submit_chat_job(
        self,
        *,
        user_text: str,
        input_field: str,
        session_id: str | None,
        no_carryover: bool,
        client: str,
        request_id: str | None = None,
    ) -> tuple[DaemonChatJob | None, bool, dict[str, Any] | None]:
        if self.shutdown_requested.is_set():
            return None, False, {"ok": False, "error_code": "daemon_shutting_down"}
        try:
            normalized_id = normalize_daemon_request_id(request_id)
        except ValueError as exc:
            return None, False, {"ok": False, "error_code": str(exc), "request_id": str(request_id or "")}
        request_fingerprint = daemon_chat_request_fingerprint(
            user_text=user_text,
            session_id=session_id,
            no_carryover=no_carryover,
            client=client,
        )

        with self._chat_jobs_lock:
            self._cleanup_chat_jobs_locked()
            existing = self.chat_jobs.get(normalized_id)
            if existing is not None:
                same_request = existing.request_fingerprint == request_fingerprint
                if existing.request_fingerprint is None:
                    same_request = (
                        existing.user_text == user_text
                        and existing.session_id == session_id
                        and existing.no_carryover == bool(no_carryover)
                        and existing.client == client
                    )
                if not same_request:
                    return None, False, {
                        "ok": False,
                        "error_code": "request_id_conflict",
                        "request_id": normalized_id,
                        "job_status": existing.status,
                    }
                return existing, False, None

            job = DaemonChatJob(
                request_id=normalized_id,
                user_text=user_text,
                input_field=input_field,
                session_id=session_id,
                no_carryover=bool(no_carryover),
                client=client,
                request_fingerprint=request_fingerprint,
                execution_timeout_seconds=self.execution_timeout_seconds,
                turn_context=TurnExecutionContext.create(
                    request_id=normalized_id,
                    session_id=session_id or "daemon-generated-session",
                    timeout_seconds=self.execution_timeout_seconds,
                    audit_db_path=self.config.audit_db_path,
                ),
            )
            self.chat_jobs[normalized_id] = job
            try:
                self._persist_chat_jobs_locked()
            except Exception as exc:
                self.chat_jobs.pop(normalized_id, None)
                return None, False, {
                    "ok": False,
                    "error_code": "daemon_chat_state_persist_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            try:
                self._chat_queue.put_nowait(job)
            except queue.Full:
                self.chat_jobs.pop(normalized_id, None)
                self._persist_chat_jobs_locked()
                return None, False, {
                    "ok": False,
                    "error_code": "daemon_chat_queue_full",
                    "queue_capacity": self._chat_queue.maxsize,
                }
            self.state.chat_job_submitted_count += 1
            self.state.chat_job_pending_count += 1
            self.state.last_chat_job_id = normalized_id

        self.start_chat_worker()
        return job, True, None

    def get_chat_job(self, request_id: str) -> DaemonChatJob | None:
        with self._chat_jobs_lock:
            self._cleanup_chat_jobs_locked()
            return self.chat_jobs.get(str(request_id))

    def _process_chat_job(self, job: DaemonChatJob) -> None:
        pickup_started = time.monotonic()
        session: RuntimeSessionWorker | None = None
        with self._chat_jobs_lock:
            job.status = "running"
            job.started_at_utc = utc_now_iso()
            job.last_heartbeat_at_utc = job.started_at_utc
            self._persist_chat_jobs_locked()
        if job.turn_context is not None:
            job.turn_context.mark_interval(
                "queue_wait",
                started_monotonic=job.turn_context.created_monotonic,
            )
            job.turn_context.mark_interval("worker_pickup", started_monotonic=pickup_started)
        try:
            session_init_started = time.monotonic()
            session, session_id_source = self.get_session(
                job.session_id,
                no_carryover=job.no_carryover,
                client=job.client,
            )
            if job.turn_context is not None:
                job.turn_context.mark_interval(
                    "session_initialization",
                    started_monotonic=session_init_started,
                    status="completed_or_reused",
                )
            result = session.process_user_text(
                job.user_text,
                client=job.client,
                lifecycle="persistent_daemon_async_job",
                session_id_source=session_id_source,
                process_reused=True,
                _turn_context=job.turn_context,
                _heartbeat_callback=lambda: setattr(job, "last_heartbeat_at_utc", utc_now_iso()),
            )
            if result.get("ok") is True:
                self.state.turn_count += 1
            marker = self.write_marker()
            result["ok"] = bool(result.get("ok", True))
            result["daemon"] = {
                "schema_version": DAEMON_SCHEMA_VERSION,
                "pid": self.state.pid,
                "host": self.state.host,
                "port": self.state.port,
                "status": self.state.status,
                "last_heartbeat_at_utc": self.state.last_heartbeat_at_utc,
                "marker_path": str(self.marker_path),
                "marker_sha_source": marker.get("manifest_current_sha256"),
                "request_id": job.request_id,
                "async_job": True,
            }
            with self._chat_jobs_lock:
                job.result = result
                if result["ok"]:
                    job.status = "completed"
                    self.state.chat_job_completed_count += 1
                else:
                    job.status = "failed"
                    job.error = str(result.get("error_code") or "runtime_turn_not_accepted")
                    self.state.chat_job_failed_count += 1
        except RuntimeTurnTimeoutError as exc:
            if session is not None:
                # The dedicated session thread may still be finishing an
                # uncooperative handler.  Its turn context is cancelled and
                # blocks late canonical writes, but the worker itself must not
                # be reused because a subsequent turn would queue behind it.
                self._retire_session_worker(session)
            error = f"{type(exc).__name__}: {exc}"
            with self._chat_jobs_lock:
                job.error = error
                job.result = {
                    "ok": False,
                    "error_code": "execution_timeout",
                    "error": error,
                    "request_id": job.request_id,
                    "execution_timeout_seconds": self.execution_timeout_seconds,
                    "schema_version": DAEMON_SCHEMA_VERSION,
                }
                job.status = "execution_timeout"
                self.state.chat_job_execution_timeout_count += 1
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            with self._chat_jobs_lock:
                job.error = error
                job.result = {
                    "ok": False,
                    "error_code": "runtime_turn_failed",
                    "error": error,
                    "request_id": job.request_id,
                    "schema_version": DAEMON_SCHEMA_VERSION,
                }
                job.status = "failed"
                self.state.chat_job_failed_count += 1
        finally:
            with self._chat_jobs_lock:
                job.completed_at_utc = utc_now_iso()
                self.state.chat_job_pending_count = max(0, self.state.chat_job_pending_count - 1)
                job.done_event.set()
                self._persist_chat_jobs_locked()
            try:
                self.write_marker()
            except Exception:
                pass

    def _local_timestamp_contract(self, *, reason: str) -> dict[str, Any]:
        return daemon_timestamp_contract(
            self.config,
            network_first=False,
            timeout_seconds=0.0,
            reason=reason,
        )

    def cached_timestamp_contract(self) -> dict[str, Any]:
        with self._timestamp_lock:
            if self._timestamp_contract is not None:
                contract = dict(self._timestamp_contract)
                updated_at = self._timestamp_contract_updated_at_utc
            else:
                contract = self._local_timestamp_contract(reason="status_fast_local_bootstrap")
                updated_at = utc_now_iso()
                self._timestamp_contract = dict(contract)
                self._timestamp_contract_updated_at_utc = updated_at
        contract["daemon_status_fast_path"] = True
        contract["daemon_status_cached_at_utc"] = updated_at
        contract["daemon_status_cache_age_seconds"] = _age_seconds(updated_at)
        contract["daemon_status_refresh_in_progress"] = self.state.timestamp_refresh_in_progress
        return contract

    def refresh_timestamp_contract(self, *, reason: str, background: bool = True, force: bool = False) -> None:
        min_interval = _env_float_value("JAZN_DAEMON_TIMESTAMP_REFRESH_MIN_SECONDS", DEFAULT_TIMESTAMP_BACKGROUND_REFRESH_MIN_SECONDS)
        timeout_seconds = _env_float_value("JAZN_DAEMON_TIMESTAMP_REFRESH_TIMEOUT", DEFAULT_TIMESTAMP_BACKGROUND_REFRESH_TIMEOUT_SECONDS)
        now_monotonic = time.monotonic()
        if not force and now_monotonic - self._last_timestamp_refresh_started_monotonic < min_interval:
            return
        if self.state.timestamp_refresh_in_progress:
            return

        def worker() -> None:
            self.state.timestamp_refresh_in_progress = True
            self._last_timestamp_refresh_started_monotonic = time.monotonic()
            try:
                contract = daemon_timestamp_contract(
                    self.config,
                    network_first=True,
                    timeout_seconds=timeout_seconds,
                    reason=reason,
                )
                with self._timestamp_lock:
                    if contract.get("trusted") is True:
                        self._trusted_timestamp_contract = dict(contract)
                        self._trusted_timestamp_monotonic = time.monotonic()
                    elif self._trusted_timestamp_contract is not None and self._trusted_timestamp_monotonic is not None:
                        hold_seconds = max(0.0, _env_float_value("JAZN_DAEMON_TRUSTED_TIME_HOLD_SECONDS", DEFAULT_DAEMON_TRUSTED_TIME_HOLD_SECONDS))
                        retained = _project_retained_trusted_time(
                            self._trusted_timestamp_contract,
                            elapsed_seconds=time.monotonic() - self._trusted_timestamp_monotonic,
                            reason=reason,
                            hold_seconds=hold_seconds,
                        )
                        if retained is not None:
                            contract = retained
                    self._timestamp_contract = dict(contract)
                    self._timestamp_contract_updated_at_utc = utc_now_iso()
                self.state.timestamp_refresh_count += 1
                self.state.last_timestamp_refresh_at_utc = self._timestamp_contract_updated_at_utc
            except Exception as exc:
                fallback = self._local_timestamp_contract(reason=f"{reason}_refresh_failed")
                fallback["daemon_status_refresh_error"] = f"{type(exc).__name__}: {exc}"
                with self._timestamp_lock:
                    if self._trusted_timestamp_contract is not None and self._trusted_timestamp_monotonic is not None:
                        hold_seconds = max(0.0, _env_float_value("JAZN_DAEMON_TRUSTED_TIME_HOLD_SECONDS", DEFAULT_DAEMON_TRUSTED_TIME_HOLD_SECONDS))
                        retained = _project_retained_trusted_time(
                            self._trusted_timestamp_contract,
                            elapsed_seconds=time.monotonic() - self._trusted_timestamp_monotonic,
                            reason=f"{reason}_exception_retention",
                            hold_seconds=hold_seconds,
                        )
                        if retained is not None:
                            retained["daemon_status_refresh_error"] = fallback["daemon_status_refresh_error"]
                            fallback = retained
                    self._timestamp_contract = dict(fallback)
                    self._timestamp_contract_updated_at_utc = utc_now_iso()
            finally:
                self.state.timestamp_refresh_in_progress = False

        if background:
            self._timestamp_refresh_thread = threading.Thread(target=worker, name="jazn-daemon-time-refresh", daemon=True)
            self._timestamp_refresh_thread.start()
        else:
            worker()

    def install_injected_timestamp_contract(self, *, reason: str) -> dict[str, Any]:
        """Refresh cached time from host-injected env without a network attempt."""
        contract = daemon_timestamp_contract(
            self.config,
            network_first=False,
            timeout_seconds=0.0,
            reason=reason,
        )
        if contract.get("trusted") is not True:
            return {
                "ok": False,
                "error_code": "trusted_time_rejected",
                "timestamp_contract": contract,
            }
        with self._timestamp_lock:
            self._timestamp_contract = dict(contract)
            self._timestamp_contract_updated_at_utc = utc_now_iso()
            self._trusted_timestamp_contract = dict(contract)
            self._trusted_timestamp_monotonic = time.monotonic()
        self.state.timestamp_refresh_count += 1
        self.state.last_timestamp_refresh_at_utc = self._timestamp_contract_updated_at_utc
        return {
            "ok": True,
            "timestamp_contract": dict(contract),
            "cached_at_utc": self._timestamp_contract_updated_at_utc,
        }

    def marker_payload(self, *, status: str | None = None, timestamp_contract: dict[str, Any] | None = None) -> dict[str, Any]:
        self.state.sessions = len(self.sessions)
        self.state.status = status or self.state.status
        chat_jobs = self.chat_job_summary()
        active = build_active_runtime_status(self.config.root, marker_output=self.marker_path)
        timestamp_contract = timestamp_contract or self.cached_timestamp_contract()
        runtime_version = str(active.get("version") or PACKAGE_VERSION)
        active_state = daemon_active_state(
            marker_found=True,
            pid_alive=True,
            ping_ok=True,
            timestamp_trusted=timestamp_contract.get("trusted"),
        )
        time_state = time_trust_state(
            timestamp_trusted=timestamp_contract.get("trusted"),
            timestamp_source=timestamp_contract.get("source"),
            time_error=timestamp_contract.get("error"),
        )
        runtime_write_access_status = build_runtime_write_access_status(self.config, initialize=False).to_dict()
        payload = {
            **active,
            "schema_version": DAEMON_SCHEMA_VERSION,
            "runtime_daemon": self.state.to_dict(),
            "active_state": active_state,
            "runtime_active_state": active_state,
            "time_trust_state": time_state,
            "timestamp_contract": timestamp_contract,
            "timestamp_trusted": bool(timestamp_contract.get("trusted")),
            "timestamp_degraded": timestamp_contract.get("trusted") is not True,
            "timestamp_does_not_block_startup": True,
            "runtime_write_access_status": runtime_write_access_status,
            "runtime_write_ready": bool(runtime_write_access_status.get("ok")),
            "daemon_chat_jobs": chat_jobs,
            "daemon_chat_async_supported": True,
            "daemon_chat_submit_endpoint": "/chat-submit",
            "daemon_chat_result_endpoint_template": "/chat-result/{request_id}",
            "daemon_pid": self.state.pid,
            "daemon_host": self.state.host,
            "daemon_port": self.state.port,
            "daemon_url": daemon_url(self.state.host, self.state.port),
            "daemon_status": self.state.status,
            "daemon_started_at_utc": self.state.started_at_utc,
            "last_heartbeat_at_utc": self.state.last_heartbeat_at_utc,
            "heartbeat_interval_seconds": self.heartbeat_interval,
            "runtime_process_active": True,
            "runtime_version": runtime_version,
            "runtime_version_full": PACKAGE_VERSION_FULL,
            "start_file": (find_start_file(self.config.root) or Path(self.config.root).resolve() / "main.py").name,
            "version": runtime_version,
            "truth_boundary": "Ten marker oznacza działający lokalny proces daemonu, gdy PID żyje, heartbeat jest świeży i /status odpowiada z localhost. Zaufanie czasu jest osobnym time_trust_state: brak czasu sieciowego nie blokuje startu, tylko jawnie oznacza lokalny czas maszyny jako niezweryfikowany.",
        }
        fresh, age, threshold = _heartbeat_fresh(payload)
        payload["heartbeat_fresh"] = fresh
        payload["heartbeat_age_seconds"] = age
        payload["heartbeat_fresh_threshold_seconds"] = threshold
        return payload

    def lite_status_payload(self, *, endpoint: str = "/ready", latency_ms: int | None = None) -> dict[str, Any]:
        timestamp_contract = self.cached_timestamp_contract()
        chat_jobs = self.chat_job_summary()
        heartbeat_marker = {
            "last_heartbeat_at_utc": self.state.last_heartbeat_at_utc,
            "heartbeat_interval_seconds": self.heartbeat_interval,
        }
        heartbeat_fresh, heartbeat_age, heartbeat_threshold = _heartbeat_fresh(heartbeat_marker)
        liveness_ok = bool(heartbeat_fresh and self.state.status != DAEMON_MARKER_STATUS_STOPPED)
        readiness_ok = bool(liveness_ok and self.marker_path.exists())
        active_state = daemon_active_state(
            marker_found=readiness_ok,
            pid_alive=liveness_ok,
            ping_ok=True,
            timestamp_trusted=timestamp_contract.get("trusted"),
        )
        time_state = time_trust_state(
            timestamp_trusted=timestamp_contract.get("trusted"),
            timestamp_source=timestamp_contract.get("source"),
            time_error=timestamp_contract.get("error"),
        )
        return {
            "schema_version": DAEMON_SCHEMA_VERSION,
            "ok": bool(readiness_ok and active_state == "active_trusted"),
            "liveness_ok": liveness_ok,
            "readiness_ok": readiness_ok,
            "active_state": active_state if readiness_ok else "inactive",
            "runtime_active_state": active_state if readiness_ok else "inactive",
            "time_trust_state": time_state,
            "daemon_pid": self.state.pid,
            "daemon_host": self.state.host,
            "daemon_port": self.state.port,
            "runtime_process_active": True,
            "runtime_version": PACKAGE_VERSION_FULL,
            "active_root": str(self.config.root),
            "marker_path": str(self.marker_path),
            "marker_found": self.marker_path.exists(),
            "endpoint_ok": True,
            "endpoint": endpoint,
            "status_latency_ms": int(latency_ms or 0),
            "timestamp_trusted": bool(timestamp_contract.get("trusted")),
            "timestamp_degraded": timestamp_contract.get("trusted") is not True,
            "timestamp_does_not_block_startup": True,
            "timestamp_contract": timestamp_contract,
            "heartbeat_fresh": heartbeat_fresh,
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat_fresh_threshold_seconds": heartbeat_threshold,
            "last_heartbeat_at_utc": self.state.last_heartbeat_at_utc,
            "heartbeat_interval_seconds": self.heartbeat_interval,
            "request_count": self.state.request_count,
            "turn_count": self.state.turn_count,
            "sessions": len(self.sessions),
            "daemon_chat_jobs": chat_jobs,
            "daemon_chat_async_supported": True,
            "uptime_seconds": self.state.uptime_seconds(),
            "truth_boundary": "Fast status endpoints avoid network and heavy cache work. Runtime active_state depends on liveness/readiness; time_trust_state separately reports whether the timestamp is network/injected trusted or local machine unverified.",
        }

    def write_marker(self, *, status: str | None = None, timestamp_contract: dict[str, Any] | None = None) -> dict[str, Any]:
        self.state.touch()
        payload = self.marker_payload(status=status, timestamp_contract=timestamp_contract)
        write_json_atomic(self.marker_path, payload)
        daemon_pid_path(self.config.root).parent.mkdir(parents=True, exist_ok=True)
        daemon_pid_path(self.config.root).write_text(str(self.state.pid), encoding="utf-8")
        return payload

    def start_heartbeat(self) -> None:
        def loop() -> None:
            while not self.shutdown_requested.is_set():
                try:
                    self.refresh_timestamp_contract(reason="heartbeat_background", background=True)
                    self.write_marker()
                except Exception:
                    pass
                self.shutdown_requested.wait(self.heartbeat_interval)
        self._heartbeat_thread = threading.Thread(target=loop, name="jazn-daemon-heartbeat", daemon=True)
        self._heartbeat_thread.start()

    def close_sessions(self) -> None:
        self.shutdown_requested.set()
        try:
            self._chat_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._chat_worker_thread is not None and self._chat_worker_thread.is_alive():
            self._chat_worker_thread.join(timeout=2.0)
        if self._chat_worker_thread is not None and self._chat_worker_thread.is_alive():
            return
        for session in list(self.sessions.values()):
            try:
                session.close()
            except Exception:
                pass
        self.sessions.clear()


class JaznDaemonHandler(BaseHTTPRequestHandler):
    server: JaznDaemonServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return

    def _loopback_only(self) -> bool:
        host = str(self.client_address[0])
        if host in LOOPBACK_CLIENTS or host.startswith("127."):
            return True
        self._json_response({"ok": False, "error": "daemon accepts loopback clients only", "client": host}, status=403)
        return False

    def _read_json_or_text(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        max_body = _env_int_value("JAZN_DAEMON_MAX_BODY_BYTES", DAEMON_MAX_BODY_BYTES)
        if length > max_body:
            return {"__daemon_error__": "body_too_large", "max_body_bytes": max_body, "received_bytes": length}
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _json_response(self, payload: dict[str, Any], *, status: int = 200) -> None:
        payload = sanitize_status_payload(payload)
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout, OSError) as exc:
            self.server.state.response_write_error_count += 1
            self.server.state.last_response_write_error = f"{type(exc).__name__}: client disconnected before daemon response was written"
            self.close_connection = True

    def _request_path_and_query(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlsplit(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query, keep_blank_values=False)

    def _chat_job_response(self, job: DaemonChatJob, *, created: bool, force_envelope: bool = False) -> None:
        snapshot = job.snapshot(include_result=True)
        snapshot["created"] = bool(created)
        snapshot["idempotent_replay"] = not bool(created)
        snapshot["daemon_chat_jobs"] = self.server.chat_job_summary()
        snapshot["runtime_daemon"] = self.server.state.to_dict()
        if job.status == "completed" and isinstance(job.result, dict) and not force_envelope:
            result = dict(job.result)
            result.setdefault("ok", True)
            result["daemon_job"] = snapshot
            self._json_response(result, status=HTTPStatus.OK)
            return
        if job.terminal() and job.status != "completed" and isinstance(job.result, dict) and not force_envelope:
            result = dict(job.result)
            result["daemon_job"] = snapshot
            self._json_response(result, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._json_response(snapshot, status=HTTPStatus.ACCEPTED if not job.terminal() else HTTPStatus.OK)

    def _submit_chat_job_from_payload(self, payload: Any) -> tuple[DaemonChatJob | None, bool, dict[str, Any] | None]:
        if isinstance(payload, dict) and payload.get("__daemon_error__") == "body_too_large":
            return None, False, {"ok": False, "error_code": "body_too_large", **payload}
        user_text, input_field = extract_daemon_user_text(payload)
        if not user_text:
            return None, False, {"ok": False, "error_code": "empty_message", "input_field": input_field}
        session_id = payload.get("session_id") if isinstance(payload, dict) else None
        no_carryover = bool(payload.get("no_carryover")) if isinstance(payload, dict) else False
        client = str(payload.get("client") or "daemon_http") if isinstance(payload, dict) else "daemon_http"
        request_id = payload.get("request_id") if isinstance(payload, dict) else None
        return self.server.submit_chat_job(
            user_text=user_text,
            input_field=input_field,
            session_id=str(session_id).strip() if session_id else None,
            no_carryover=no_carryover,
            client=client,
            request_id=str(request_id).strip() if request_id else None,
        )

    def do_GET(self) -> None:
        if not self._loopback_only():
            return
        started = time.perf_counter()
        path, query = self._request_path_and_query()
        if path.startswith("/chat-result/") or path == "/chat-result":
            request_id = urllib.parse.unquote(path.removeprefix("/chat-result/")).strip() if path.startswith("/chat-result/") else ""
            if not request_id:
                request_id = str((query.get("request_id") or [""])[0]).strip()
            if not request_id:
                self._json_response({"ok": False, "error_code": "missing_request_id"}, status=HTTPStatus.BAD_REQUEST)
                return
            job = self.server.get_chat_job(request_id)
            if job is None:
                self._json_response({"ok": False, "error_code": "chat_job_not_found", "request_id": request_id}, status=HTTPStatus.NOT_FOUND)
                return
            self._chat_job_response(job, created=False, force_envelope=True)
            return
        if path == "/chat-jobs":
            self._json_response({"ok": True, "daemon_chat_jobs": self.server.chat_job_summary()})
            return
        if path in {"/live", "/liveness"}:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.server.state.note_request(latency_ms=latency_ms)
            payload = self.server.lite_status_payload(endpoint=path, latency_ms=latency_ms)
            payload["ok"] = bool(payload.get("liveness_ok"))
            self._json_response(payload)
            return
        if path in {"/ready", "/status-lite", "/readiness"}:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.server.state.note_request(latency_ms=latency_ms)
            payload = self.server.lite_status_payload(endpoint=path, latency_ms=latency_ms)
            self._json_response(payload)
            return
        if path in {"/", "/status", "/health"}:
            payload = self.server.write_marker()
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.server.state.note_request(latency_ms=latency_ms)
            payload["endpoint_ok"] = True
            payload["ok"] = payload.get("active_state") == "active_trusted"
            payload["endpoint"] = path
            payload["status_latency_ms"] = latency_ms
            self._json_response(payload)
            return
        if path == "/refresh-time":
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.server.state.note_request(latency_ms=latency_ms)
            self.server.refresh_timestamp_contract(reason="manual_http_refresh", background=True, force=True)
            payload = self.server.lite_status_payload(endpoint=path, latency_ms=latency_ms)
            payload.update({
                "ok": True,
                "refresh_started": True,
                "timestamp_refresh_in_progress": self.server.state.timestamp_refresh_in_progress,
            })
            self._json_response(payload)
            return
        if path == "/runtime-write-status":
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.server.state.note_request(latency_ms=latency_ms)
            payload = build_runtime_write_access_status(self.server.config, initialize=False).to_dict()
            payload["endpoint"] = path
            payload["status_latency_ms"] = latency_ms
            self._json_response(payload)
            return
        self._json_response({"ok": False, "error": "not_found", "path": path}, status=404)

    def do_POST(self) -> None:
        if not self._loopback_only():
            return
        path, _query = self._request_path_and_query()
        self.server.state.note_request()
        if path in {"/chat", "/message", "/chat-submit"}:
            payload = self._read_json_or_text()
            job, created, error = self._submit_chat_job_from_payload(payload)
            if error is not None:
                status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if error.get("error_code") == "body_too_large" else HTTPStatus.BAD_REQUEST
                if error.get("error_code") == "daemon_chat_queue_full":
                    status = HTTPStatus.SERVICE_UNAVAILABLE
                if error.get("error_code") == "request_id_conflict":
                    status = HTTPStatus.CONFLICT
                self._json_response(error, status=status)
                return
            assert job is not None
            if path == "/chat-submit":
                self._chat_job_response(job, created=created, force_envelope=True)
                return
            inline_wait = _env_float_value("JAZN_DAEMON_CHAT_INLINE_WAIT_SECONDS", DEFAULT_DAEMON_CHAT_INLINE_WAIT_SECONDS)
            if isinstance(payload, dict) and payload.get("inline_wait_seconds") is not None:
                try:
                    inline_wait = float(payload.get("inline_wait_seconds"))
                except (TypeError, ValueError):
                    inline_wait = DEFAULT_DAEMON_CHAT_INLINE_WAIT_SECONDS
            inline_wait = max(0.0, min(float(inline_wait), 5.0))
            if not job.terminal() and inline_wait > 0:
                job.done_event.wait(inline_wait)
            self._chat_job_response(job, created=created, force_envelope=False)
            return
        if path == "/trusted-time":
            payload_in = self._read_json_or_text()
            if not isinstance(payload_in, dict):
                self._json_response({"ok": False, "error_code": "invalid_json_payload"}, status=400)
                return
            trusted_time_iso = str(payload_in.get("trusted_time_iso") or "").strip()
            if not trusted_time_iso:
                self._json_response({"ok": False, "error_code": "missing_trusted_time_iso"}, status=400)
                return
            max_age_raw = payload_in.get("max_age_seconds")
            try:
                max_age = int(max_age_raw) if max_age_raw is not None else None
            except (TypeError, ValueError):
                max_age = None
            env_status = apply_daemon_trusted_time_env(
                trusted_time_iso=trusted_time_iso,
                source=str(payload_in.get("source") or "chatgpt_loader_time"),
                max_age_seconds=max_age,
            )
            installed = self.server.install_injected_timestamp_contract(reason="manual_trusted_time_injection")
            if not installed.get("ok"):
                self._json_response(
                    {
                        "ok": False,
                        "error_code": installed.get("error_code") or "trusted_time_rejected",
                        "trusted_time_env": env_status,
                        "timestamp_contract": installed.get("timestamp_contract"),
                    },
                    status=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
                return
            marker = self.server.write_marker(timestamp_contract=installed["timestamp_contract"])
            marker["trusted_time_env"] = env_status
            marker["trusted_time_installation"] = installed
            marker["ok"] = marker.get("active_state") == "active_trusted"
            self._json_response(marker)
            return
        if path == "/runtime-write-init":
            status_payload = build_runtime_write_access_status(self.server.config, initialize=True, writes_enabled=True).to_dict()
            marker = self.server.write_marker()
            marker["runtime_write_access_status"] = status_payload
            marker["runtime_write_ready"] = bool(status_payload.get("ok"))
            marker["ok"] = marker.get("active_state") == "active_trusted" and bool(status_payload.get("ok"))
            self._json_response(marker)
            return
        if path == "/shutdown":
            payload = self.server.write_marker(status="shutdown_requested")
            payload["ok"] = True
            self._json_response(payload)
            def stop_later() -> None:
                time.sleep(0.15)
                self.server.shutdown_requested.set()
                self.server.shutdown()
            threading.Thread(target=stop_later, name="jazn-daemon-shutdown", daemon=True).start()
            return
        self._json_response({"ok": False, "error": "not_found", "path": path}, status=404)


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, *, timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def run_daemon(
    config: JaznConfig,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    marker_output: Path | None = None,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
) -> int:
    marker_path = resolve_active_runtime_marker_path(config.root, marker_output)
    # runtime_write_v1 is a critical local write layer; initialize a clean store if the release pack omitted stale shards.
    build_runtime_write_access_status(config, initialize=True, writes_enabled=True)
    # Write the normal active-runtime marker first; then the daemon marker extends it.
    write_active_runtime_marker(config.root, marker_output=marker_path, action="daemon_run_start")
    server = JaznDaemonServer((host, int(port)), JaznDaemonHandler, config=config, marker_path=marker_path, heartbeat_interval=heartbeat_interval)
    server.refresh_timestamp_contract(reason="startup_background", background=True, force=True)
    server.write_marker()
    server.start_heartbeat()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.shutdown_requested.set()
        try:
            server.close_sessions()
        finally:
            payload = server.marker_payload(status=DAEMON_MARKER_STATUS_STOPPED)
            payload["runtime_process_active"] = False
            payload["stopped_at_utc"] = utc_now_iso()
            write_json_atomic(marker_path, payload)
            server.server_close()
    return 0


def build_daemon_start_command(
    root: Path,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    marker_output: Path | None = None,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
) -> list[str]:
    root = Path(root).resolve()
    start_file = root / "main.py" if (root / "main.py").is_file() else (find_start_file(root) or root / "main.py")
    cmd = [sys.executable, str(start_file), "--root", str(root), "--daemon-run", "--daemon-host", host, "--daemon-port", str(int(port)), "--daemon-heartbeat-interval", str(float(heartbeat_interval))]
    if marker_output:
        cmd.extend(["--daemon-marker-output", str(resolve_active_runtime_marker_path(root, marker_output))])
    return cmd


def _daemon_pid_from_status(status: dict[str, Any]) -> int | None:
    value = status.get("daemon_pid") or status.get("pid") or (status.get("runtime_daemon") or {}).get("pid")
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _same_runtime_path(left: Any, right: Any) -> bool:
    if left in (None, "") or right in (None, ""):
        return False
    try:
        return Path(str(left)).expanduser().resolve() == Path(str(right)).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _endpoint_confirms_pid(pid: int | None, ping: dict[str, Any] | None) -> bool:
    if not pid or not isinstance(ping, dict):
        return False
    ping_pid = _daemon_pid_from_status(ping)
    return bool(ping_pid and int(ping_pid) == int(pid) and ping.get("runtime_process_active") is True)


def _endpoint_confirms_root(root: Path, ping: dict[str, Any] | None) -> bool:
    if not isinstance(ping, dict):
        return False
    endpoint_root = ping.get("active_root") or ping.get("configured_runtime_root")
    if endpoint_root in (None, "") and isinstance(ping.get("marker"), dict):
        endpoint_root = ping["marker"].get("active_root")
    return _same_runtime_path(root, endpoint_root)


def _endpoint_confirms_runtime_identity(
    *,
    root: Path,
    pid: int | None,
    ping: dict[str, Any] | None,
) -> bool:
    return bool(_endpoint_confirms_pid(pid, ping) and _endpoint_confirms_root(root, ping))


def _probe_daemon_status(host: str, port: int, *, timeout: float = DEFAULT_LITE_STATUS_HTTP_TIMEOUT_SECONDS) -> tuple[dict[str, Any] | None, str | None, str | None]:
    errors: list[str] = []
    # Three bounded attempts cover a transient timeout without invoking the
    # heavier /status endpoint. Alternating paths also preserves compatibility
    # with daemons that expose only one of the two lightweight endpoints.
    for attempt, endpoint in enumerate(("/ready", "/status-lite", "/ready"), start=1):
        try:
            payload = http_json("GET", daemon_url(host, int(port), endpoint), timeout=timeout)
            payload.setdefault("endpoint", endpoint)
            payload.setdefault("endpoint_probe_attempt", attempt)
            return payload, None, endpoint
        except Exception as exc:
            errors.append(f"attempt={attempt} {endpoint}: {type(exc).__name__}: {exc}")
            if attempt < 3:
                time.sleep(0.05 * attempt)
    return None, "; ".join(errors) if errors else "daemon endpoint unavailable", None


def _daemon_degraded_recommendation(
    *,
    endpoint_reachable: bool,
    endpoint_root_matches: bool,
    endpoint_pid_matches: bool,
    timestamp_trusted: bool | None,
    heartbeat_fresh: bool,
) -> dict[str, Any] | None:
    if endpoint_reachable and not endpoint_root_matches:
        return {
            "kind": "runtime_root_mismatch",
            "summary": "Port Daemona odpowiada, ale endpoint należy do innego folderu runtime. Nie uruchamiaj ani nie zatrzymuj procesu przez ten marker; wybierz inny port albo zatrzymaj właściwy runtime.",
        }
    if endpoint_reachable and not endpoint_pid_matches:
        return {
            "kind": "daemon_pid_mismatch",
            "summary": "Endpoint odpowiada, ale jego PID nie zgadza się z PID-em zapisanym w markerze. Odśwież marker lub uruchom ponownie właściwego Daemona; status nie może być active_trusted.",
        }
    if endpoint_reachable and not heartbeat_fresh:
        return {
            "kind": "heartbeat_stale",
            "summary": "Tożsamość endpointu jest zgodna, ale heartbeat jest nieświeży. Sprawdź wątek heartbeat i logi workspace_runtime/daemon, a następnie wykonaj kontrolowany restart Daemona.",
        }
    if endpoint_reachable and timestamp_trusted is not True:
        return {
            "kind": "trusted_time_missing_nonblocking",
            "summary": "Daemon żyje. Nie ma potwierdzonego czasu sieciowego/wstrzykniętego, więc czas jest lokalny i niezweryfikowany, ale nie blokuje startu runtime.",
            "example": "Opcjonalnie: python -X utf8 main.py --trusted-time-iso <ISO_FROM_CHATGPT_LOADER> --trusted-time-source chatgpt_loader --daemon-start",
        }
    if heartbeat_fresh and not endpoint_reachable:
        return {
            "kind": "endpoint_unreachable",
            "summary": "PID i heartbeat wyglądają świeżo, ale HTTP endpoint nie odpowiada. Sprawdź port, logi workspace_runtime/daemon oraz wykonaj --daemon-stop/--daemon-start.",
        }
    return None


def start_daemon(
    config: JaznConfig,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    marker_output: Path | None = None,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    startup_timeout: float = DEFAULT_START_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    marker_path = resolve_active_runtime_marker_path(config.root, marker_output)
    try:
        existing, existing_error, existing_endpoint = _probe_daemon_status(host, int(port))
        if isinstance(existing, dict):
            existing_pid = _daemon_pid_from_status(existing)
            root_matches = _endpoint_confirms_root(config.root, existing)
            if existing.get("active_state") in {"active_trusted", "active_degraded"} and root_matches:
                return {
                    "ok": True,
                    "trusted": existing.get("active_state") == "active_trusted",
                    "already_running": True,
                    "started": False,
                    "degraded": existing.get("active_state") == "active_degraded",
                    "pid": existing_pid,
                    "status": existing,
                    "marker_path": str(marker_path),
                }
            if not root_matches:
                return {
                    "ok": False,
                    "trusted": False,
                    "already_running": False,
                    "started": False,
                    "error_code": "daemon_port_in_use_by_other_runtime",
                    "error": (
                        f"Port {int(port)} odpowiada, ale endpoint należy do innego rootu runtime: "
                        f"{existing.get('active_root') or existing.get('configured_runtime_root') or 'unknown'}"
                    ),
                    "endpoint": existing_endpoint or existing.get("endpoint"),
                    "existing_pid": existing_pid,
                    "existing_status": existing,
                    "marker_path": str(marker_path),
                }
        elif existing_error:
            pass
    except Exception:
        pass
    log_dir = daemon_log_dir(config.root)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"
    cmd = build_daemon_start_command(config.root, host=host, port=port, marker_output=marker_path, heartbeat_interval=heartbeat_interval)
    creationflags = 0
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        popen_kwargs["start_new_session"] = True
    with stdout_path.open("ab") as out, stderr_path.open("ab") as err:
        proc = subprocess.Popen(cmd, cwd=str(config.root), stdout=out, stderr=err, stdin=subprocess.DEVNULL, creationflags=creationflags, **popen_kwargs)
    deadline = time.time() + float(startup_timeout)
    last_error: str | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            last_error = f"daemon process exited early with code {proc.returncode}"
            break
        try:
            status, status_error, status_endpoint = _probe_daemon_status(host, int(port))
            if isinstance(status, dict) and status.get("active_state") in {"active_trusted", "active_degraded"}:
                status_pid = _daemon_pid_from_status(status)
                root_matches = _endpoint_confirms_root(config.root, status)
                process_matches = bool(status_pid and int(status_pid) == int(proc.pid))
                if root_matches and process_matches:
                    status.setdefault("endpoint", status_endpoint or status.get("endpoint"))
                    return {
                        "ok": True,
                        "trusted": status.get("active_state") == "active_trusted",
                        "started": True,
                        "degraded": status.get("active_state") == "active_degraded",
                        "pid": proc.pid,
                        "status": status,
                        "marker_path": str(marker_path),
                        "stdout_log": str(stdout_path),
                        "stderr_log": str(stderr_path),
                        "command": cmd,
                    }
                last_error = (
                    "daemon endpoint identity mismatch after spawn: "
                    f"expected_root={Path(config.root).resolve()}, endpoint_root="
                    f"{status.get('active_root') or status.get('configured_runtime_root')}, "
                    f"spawned_pid={proc.pid}, endpoint_pid={status_pid}"
                )
            else:
                last_error = status_error or "daemon probe returned no active state"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.2)
    return {"ok": False, "started": False, "pid": proc.pid, "error": last_error or "daemon did not answer before timeout", "marker_path": str(marker_path), "stdout_log": str(stdout_path), "stderr_log": str(stderr_path), "command": cmd}


def status_daemon(
    config: JaznConfig,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    marker_output: Path | None = None,
    probe_endpoint: bool = True,
) -> dict[str, Any]:
    marker_path = resolve_active_runtime_marker_path(config.root, marker_output)
    marker = read_json_file(marker_path)
    root_resolution = resolve_active_runtime_root(config.root, marker_path=marker_path)
    marker_root_valid = bool(marker is not None and root_resolution.marker_valid)
    pid_int = _daemon_pid_from_status(marker or {})
    os_pid_alive = pid_is_alive(pid_int) if pid_int else False

    if probe_endpoint:
        ping, ping_error, ping_endpoint = _probe_daemon_status(host, int(port))
    else:
        ping = None
        ping_error = "endpoint_probe_skipped_read_only"
        ping_endpoint = None

    endpoint_reachable = isinstance(ping, dict)
    endpoint_pid_matches = _endpoint_confirms_pid(pid_int, ping)
    endpoint_root_matches = _endpoint_confirms_root(config.root, ping)
    endpoint_identity_matches = bool(endpoint_pid_matches and endpoint_root_matches)

    marker_heartbeat_fresh, marker_heartbeat_age, marker_heartbeat_threshold = _heartbeat_fresh(marker)
    ping_heartbeat_fresh, ping_heartbeat_age, ping_heartbeat_threshold = _heartbeat_fresh(ping)
    if endpoint_identity_matches and ping_heartbeat_age is not None:
        heartbeat_is_fresh = ping_heartbeat_fresh
        heartbeat_age_seconds = ping_heartbeat_age
        heartbeat_fresh_threshold_seconds = ping_heartbeat_threshold
        heartbeat_source = "endpoint"
    else:
        heartbeat_is_fresh = marker_heartbeat_fresh
        heartbeat_age_seconds = marker_heartbeat_age
        heartbeat_fresh_threshold_seconds = marker_heartbeat_threshold
        heartbeat_source = "marker"

    process_identity_confirmed = endpoint_identity_matches
    alive = bool(os_pid_alive or process_identity_confirmed)
    if process_identity_confirmed:
        pid_alive_source = "endpoint_runtime_identity"
    elif os_pid_alive:
        pid_alive_source = "os_process_probe_unconfirmed_identity"
    else:
        pid_alive_source = "unverified"

    marker_timestamp = (marker or {}).get("timestamp_contract") if isinstance(marker, dict) else {}
    marker_timestamp = marker_timestamp if isinstance(marker_timestamp, dict) else {}
    timestamp_trusted = (ping or {}).get("timestamp_trusted") if isinstance(ping, dict) else marker_timestamp.get("trusted")
    ping_timestamp = (ping or {}).get("timestamp_contract") if isinstance(ping, dict) else {}
    ping_timestamp = ping_timestamp if isinstance(ping_timestamp, dict) else {}
    timestamp_source = ping_timestamp.get("source") if isinstance(ping, dict) else marker_timestamp.get("source")
    timestamp_error = ping_timestamp.get("error") if isinstance(ping, dict) else marker_timestamp.get("error")
    time_state = time_trust_state(
        timestamp_trusted=timestamp_trusted,
        timestamp_source=timestamp_source,
        time_error=timestamp_error,
    )

    active_state = "inactive"
    active_state_reason = "daemon_process_not_confirmed"
    if marker is not None and not marker_root_valid:
        active_state_reason = root_resolution.error or "active_root_marker_invalid"
    elif not marker_root_valid:
        active_state_reason = "active_runtime_marker_missing"
    elif endpoint_reachable:
        if not endpoint_root_matches:
            active_state_reason = "endpoint_runtime_root_mismatch"
        elif not endpoint_pid_matches:
            active_state_reason = "endpoint_pid_mismatch"
        elif not heartbeat_is_fresh:
            active_state = "active_degraded"
            active_state_reason = "endpoint_identity_confirmed_heartbeat_stale"
        else:
            active_state = daemon_active_state(
                marker_found=True,
                pid_alive=True,
                ping_ok=True,
                timestamp_trusted=timestamp_trusted,
            )
            active_state_reason = "endpoint_runtime_identity_confirmed"
    elif os_pid_alive and marker_heartbeat_fresh:
        if probe_endpoint:
            active_state = "active_degraded"
            active_state_reason = "fresh_marker_and_live_pid_endpoint_unreachable"
        else:
            active_state = "active_unverified"
            active_state_reason = "fresh_marker_and_live_pid_endpoint_not_probed"
    elif os_pid_alive:
        active_state_reason = "live_pid_but_heartbeat_stale"

    if process_identity_confirmed:
        identity_state = "endpoint_identity_confirmed"
    elif endpoint_reachable:
        identity_state = "identity_mismatch"
    elif marker_root_valid and os_pid_alive:
        identity_state = "marker_pid_unverified"
    else:
        identity_state = "unknown"
    process_state = "active" if alive else ("dead" if pid_int else "not_observed")
    heartbeat_state = "fresh" if heartbeat_is_fresh else ("stale" if heartbeat_age_seconds is not None else "unknown")
    if not probe_endpoint:
        readiness_state = "endpoint_not_probed"
        observation_state = "endpoint_not_probed"
    elif active_state == "active_trusted":
        readiness_state = "ready"
        observation_state = "live_verified"
    elif active_state == "active_degraded":
        readiness_state = "temporarily_unreachable_or_stale"
        observation_state = "live_degraded"
    else:
        readiness_state = "not_ready"
        observation_state = "live_probe_failed"

    return {
        "schema_version": DAEMON_SCHEMA_VERSION,
        "ok": active_state == "active_trusted",
        "active_state": active_state,
        "degraded": active_state == "active_degraded",
        "runtime_active_state": active_state,
        "process_state": process_state,
        "identity_state": identity_state,
        "readiness_state": readiness_state,
        "heartbeat_state": heartbeat_state,
        "observation_state": observation_state,
        "time_trust_state": time_state,
        "timestamp_degraded": timestamp_trusted is not True,
        "timestamp_does_not_block_startup": True,
        "runtime_version": PACKAGE_VERSION_FULL,
        "active_root": str(root_resolution.root),
        "configured_runtime_root": str(Path(config.root).resolve()),
        "active_root_source": root_resolution.source,
        "active_root_validation_error": root_resolution.error,
        "marker_path": str(marker_path),
        "marker_found": marker is not None,
        "marker_valid": marker_root_valid,
        "marker": marker,
        "pid": pid_int,
        "pid_alive": alive,
        "pid_alive_os_probe": os_pid_alive,
        "pid_alive_source": pid_alive_source,
        "process_identity_confirmed": process_identity_confirmed,
        "endpoint_probe_performed": bool(probe_endpoint),
        "endpoint_pid_matches": endpoint_pid_matches,
        "endpoint_root_matches": endpoint_root_matches,
        "endpoint_identity_matches": endpoint_identity_matches,
        "endpoint_reachable": endpoint_reachable,
        "ping_endpoint": ping_endpoint,
        "ping": ping,
        "ping_error": ping_error,
        "timestamp_trusted": timestamp_trusted,
        "timestamp_source": timestamp_source,
        "timestamp_error": timestamp_error,
        "heartbeat_fresh": heartbeat_is_fresh,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "heartbeat_fresh_threshold_seconds": heartbeat_fresh_threshold_seconds,
        "heartbeat_source": heartbeat_source,
        "marker_heartbeat_fresh": marker_heartbeat_fresh,
        "marker_heartbeat_age_seconds": marker_heartbeat_age,
        "endpoint_heartbeat_fresh": ping_heartbeat_fresh if endpoint_reachable else None,
        "endpoint_heartbeat_age_seconds": ping_heartbeat_age if endpoint_reachable else None,
        "active_state_reason": active_state_reason,
        "recommended_repair": _daemon_degraded_recommendation(
            endpoint_reachable=endpoint_reachable,
            endpoint_root_matches=endpoint_root_matches,
            endpoint_pid_matches=endpoint_pid_matches,
            timestamp_trusted=timestamp_trusted,
            heartbeat_fresh=heartbeat_is_fresh,
        ),
        "truth_boundary": (
            "Status active_trusted wymaga zgodnego markera, zgodności rootu runtime, tego samego PID-u "
            "w markerze i endpointcie, runtime_process_active=true, świeżego heartbeat oraz odpowiedzi lokalnego endpointu. "
            "Żywy PID bez potwierdzonej tożsamości nie wystarcza. Zaufanie czasu jest raportowane osobno; "
            "brak czasu sieciowego nie blokuje startu. Endpoint chwilowo niedostępny przy żywym PID i świeżym "
            "markerze daje wyłącznie active_degraded."
        ),
    }


def refresh_daemon_time(
    config: JaznConfig,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    response: dict[str, Any] | None = None
    error: str | None = None
    try:
        response = http_json("GET", daemon_url(host, int(port), "/refresh-time"), timeout=timeout)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    status = status_daemon(config, host=host, port=port)
    return {
        "schema_version": DAEMON_SCHEMA_VERSION,
        "ok": error is None,
        "refresh_response": response,
        "refresh_error": error,
        "status": status,
    }


def inject_daemon_trusted_time(
    config: JaznConfig,
    *,
    trusted_time_iso: str,
    source: str | None = None,
    max_age_seconds: int | None = None,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "trusted_time_iso": str(trusted_time_iso).strip(),
        "source": source or "chatgpt_loader_time",
    }
    if max_age_seconds is not None:
        payload["max_age_seconds"] = int(max_age_seconds)
    response: dict[str, Any] | None = None
    error: str | None = None
    try:
        response = http_json("POST", daemon_url(host, int(port), "/trusted-time"), payload, timeout=timeout)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    probe, probe_error, probe_endpoint = _probe_daemon_status(
        host,
        int(port),
        timeout=min(max(float(timeout), 0.1), DEFAULT_LITE_STATUS_HTTP_TIMEOUT_SECONDS),
    )
    active_state = str((probe or {}).get("active_state") or (response or {}).get("active_state") or "inactive")
    status = {
        "active_state": active_state,
        "ping": probe,
        "ping_error": probe_error,
        "ping_endpoint": probe_endpoint,
        "timestamp_trusted": (probe or {}).get("timestamp_trusted", (response or {}).get("timestamp_trusted")),
    }
    return {
        "schema_version": DAEMON_SCHEMA_VERSION,
        "ok": bool(error is None and active_state == "active_trusted"),
        "inject_response": response,
        "inject_error": error,
        "status": status,
        "truth_boundary": "Trusted time is accepted only when explicitly injected by the host/loader or confirmed by network time; local fallback is never silently promoted.",
    }


def init_runtime_write_v1_daemon(
    config: JaznConfig,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    response: dict[str, Any] | None = None
    error: str | None = None
    try:
        response = http_json("POST", daemon_url(host, int(port), "/runtime-write-init"), {}, timeout=timeout)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    status = status_daemon(config, host=host, port=port)
    return {
        "schema_version": DAEMON_SCHEMA_VERSION,
        "ok": error is None,
        "init_response": response,
        "init_error": error,
        "status": status,
    }


def chat_daemon_submit(
    config: JaznConfig,
    user_text: str,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    session_id: str | None = None,
    no_carryover: bool = False,
    client: str = "chatgpt_daemon_bridge",
    request_id: str | None = None,
    timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    text = str(user_text or "").strip()
    if not text:
        return {"ok": False, "error_code": "empty_message", "schema_version": DAEMON_SCHEMA_VERSION}
    try:
        normalized_request_id = normalize_daemon_request_id(request_id)
    except ValueError as exc:
        return {
            "ok": False,
            "error_code": str(exc),
            "request_id": str(request_id or ""),
            "schema_version": DAEMON_SCHEMA_VERSION,
        }
    payload: dict[str, Any] = {
        "message": text,
        "client": client,
        "no_carryover": bool(no_carryover),
        "request_id": normalized_request_id,
    }
    if session_id:
        payload["session_id"] = session_id
    try:
        result = http_json("POST", daemon_url(host, int(port), "/chat-submit"), payload, timeout=max(0.1, float(timeout)))
        result.setdefault("accepted", True)
        result.setdefault("request_id", normalized_request_id)
        return result
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            body = {}
        if isinstance(body, dict):
            body.setdefault("ok", False)
            body.setdefault("accepted", False)
            body.setdefault("request_id", normalized_request_id)
            body.setdefault("http_status", exc.code)
            body.setdefault("schema_version", DAEMON_SCHEMA_VERSION)
            return body
        return {
            "ok": False,
            "accepted": False,
            "error_code": "daemon_chat_submit_http_error",
            "http_status": exc.code,
            "error": f"HTTPError: {exc}",
            "request_id": normalized_request_id,
            "schema_version": DAEMON_SCHEMA_VERSION,
        }
    except Exception as exc:
        return {
            "ok": False,
            "accepted": False,
            "error_code": "daemon_chat_submit_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "request_id": normalized_request_id,
            "schema_version": DAEMON_SCHEMA_VERSION,
            "status": status_daemon(config, host=host, port=port),
        }


def chat_daemon_result(
    config: JaznConfig,
    request_id: str,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    raw_request_id = str(request_id or "").strip()
    if not raw_request_id:
        return {
            "ok": False,
            "error_code": "missing_request_id",
            "request_id": "",
            "schema_version": DAEMON_SCHEMA_VERSION,
        }
    try:
        normalized_request_id = normalize_daemon_request_id(raw_request_id)
    except ValueError as exc:
        return {
            "ok": False,
            "error_code": str(exc),
            "request_id": str(request_id or ""),
            "schema_version": DAEMON_SCHEMA_VERSION,
        }
    endpoint = f"/chat-result/{urllib.parse.quote(normalized_request_id, safe='')}"
    try:
        result = http_json("GET", daemon_url(host, int(port), endpoint), timeout=max(0.1, float(timeout)))
        result.setdefault("request_id", normalized_request_id)
        return result
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            body = {}
        if isinstance(body, dict) and body:
            body.setdefault("ok", False)
            body.setdefault("request_id", normalized_request_id)
            body.setdefault("http_status", exc.code)
            body.setdefault("schema_version", DAEMON_SCHEMA_VERSION)
            return body
        return {
            "ok": False,
            "error_code": "daemon_chat_result_http_error",
            "http_status": exc.code,
            "error": f"HTTPError: {exc}",
            "request_id": normalized_request_id,
            "schema_version": DAEMON_SCHEMA_VERSION,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "daemon_chat_result_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "request_id": normalized_request_id,
            "schema_version": DAEMON_SCHEMA_VERSION,
        }


def _unwrap_daemon_chat_job(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = str(payload.get("request_id") or "")
    job_status = str(payload.get("job_status") or "")
    result = payload.get("result")
    if payload.get("done") is True and isinstance(result, dict):
        unwrapped = dict(result)
        unwrapped.setdefault("ok", job_status == "completed")
        unwrapped["daemon_job"] = dict(payload)
        return unwrapped
    return {
        "ok": False,
        "accepted": bool(payload.get("accepted", True)),
        "done": False,
        "error_code": "daemon_chat_pending",
        "request_id": request_id,
        "job_status": job_status or "queued",
        "result_endpoint": payload.get("result_endpoint") or f"/chat-result/{urllib.parse.quote(request_id, safe='')}",
        "retryable": True,
        "schema_version": DAEMON_SCHEMA_VERSION,
        "daemon_job": dict(payload),
    }


def chat_daemon(
    config: JaznConfig,
    user_text: str,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    session_id: str | None = None,
    no_carryover: bool = False,
    client: str = "chatgpt_daemon_bridge",
    request_id: str | None = None,
    timeout: float = DEFAULT_DAEMON_CHAT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_DAEMON_CHAT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    submit_timeout = min(DEFAULT_HTTP_TIMEOUT_SECONDS, max(0.5, float(timeout)))
    submitted = chat_daemon_submit(
        config,
        user_text,
        host=host,
        port=port,
        session_id=session_id,
        no_carryover=no_carryover,
        client=client,
        request_id=request_id,
        timeout=submit_timeout,
    )
    if submitted.get("accepted") is not True:
        return submitted
    if submitted.get("done") is True:
        return _unwrap_daemon_chat_job(submitted)

    normalized_request_id = str(submitted.get("request_id") or request_id or "")
    deadline = time.monotonic() + max(0.0, float(timeout))
    last_payload = submitted
    last_poll_error: str | None = None
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            break
        time.sleep(min(max(0.02, float(poll_interval)), remaining))
        polled = chat_daemon_result(
            config,
            normalized_request_id,
            host=host,
            port=port,
            timeout=min(DEFAULT_HTTP_TIMEOUT_SECONDS, max(0.1, remaining)),
        )
        error_code = str(polled.get("error_code") or "")
        if error_code in {"daemon_chat_result_failed", "daemon_chat_result_http_error"}:
            last_poll_error = str(polled.get("error") or error_code)
            continue
        if error_code in {
            "chat_job_not_found",
            "missing_request_id",
            "request_id_too_long",
            "request_id_contains_unsafe_characters",
        }:
            return polled
        last_payload = polled
        if polled.get("done") is True:
            return _unwrap_daemon_chat_job(polled)

    pending = _unwrap_daemon_chat_job(last_payload)
    pending["request_id"] = normalized_request_id
    pending["wait_timeout_seconds"] = float(timeout)
    pending["client_wait_status"] = "client_wait_timeout"
    pending["client_wait_timeout"] = True
    pending["execution_failed"] = False
    if last_poll_error:
        pending["last_poll_error"] = last_poll_error
    return pending


def stop_daemon(
    config: JaznConfig,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    marker_output: Path | None = None,
    timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    marker_path = resolve_active_runtime_marker_path(config.root, marker_output)
    before = status_daemon(config, host=host, port=port, marker_output=marker_path)

    if not bool(before.get("endpoint_identity_matches")):
        return {
            "schema_version": DAEMON_SCHEMA_VERSION,
            "ok": False,
            "stopped": False,
            "error_code": "daemon_identity_not_confirmed",
            "error": (
                "Odmowa wysłania /shutdown: endpoint nie potwierdził jednocześnie zgodnego rootu runtime "
                "i PID-u z aktywnego markera."
            ),
            "before": before,
            "shutdown_response": None,
            "shutdown_error": None,
            "after": before,
        }

    shutdown_response: dict[str, Any] | None = None
    shutdown_error: str | None = None
    try:
        shutdown_response = http_json("POST", daemon_url(host, int(port), "/shutdown"), {}, timeout=2.0)
    except Exception as exc:
        shutdown_error = f"{type(exc).__name__}: {exc}"

    expected_pid = _daemon_pid_from_status(before)
    deadline = time.monotonic() + max(0.0, float(timeout))
    endpoint_down_observed = False
    process_exit_observed = False
    while time.monotonic() < deadline:
        try:
            http_json("GET", daemon_url(host, int(port), "/ready"), timeout=0.5)
            endpoint_down_observed = False
        except Exception:
            endpoint_down_observed = True
        process_exit_observed = bool(expected_pid and not pid_is_alive(expected_pid))
        if endpoint_down_observed and process_exit_observed:
            break
        time.sleep(0.2)

    after = status_daemon(config, host=host, port=port, marker_output=marker_path)
    stopped = bool(
        after.get("active_state") == "inactive"
        and after.get("endpoint_reachable") is False
        and after.get("pid_alive") is False
    )
    error_code = None if stopped else "daemon_stop_timeout"
    error = None if stopped else (
        "Daemon nie zakończył pełnego cyklu stop przed timeoutem: endpoint i PID muszą jednocześnie zniknąć."
    )
    return {
        "schema_version": DAEMON_SCHEMA_VERSION,
        "ok": stopped,
        "stopped": stopped,
        "error_code": error_code,
        "error": error,
        "expected_pid": expected_pid,
        "endpoint_down_observed": endpoint_down_observed,
        "process_exit_observed": process_exit_observed,
        "before": before,
        "shutdown_response": shutdown_response,
        "shutdown_error": shutdown_error,
        "after": after,
        "truth_boundary": (
            "Stop jest sukcesem tylko wtedy, gdy właściwy endpoint Daemona został wcześniej potwierdzony "
            "przez zgodny root i PID, a po /shutdown proces nie żyje i endpoint nie odpowiada."
        ),
    }


# v14.8.5.036b: status text sanitizer.
# This does not change runtime mechanics. It only normalizes diagnostic/status text
# emitted through daemon marker, /status, /ready and CLI daemon commands.
STATUS_TEXT_REPLACEMENTS = {
    "albo" + "wstrzyknięty": "albo wstrzyknięty",
    "brak" + "nie": "brak nie",
    "blokuje" + "zwykłej": "blokuje zwykłej",
    "proces" + "daemonu": "proces daemonu",
    "endpoint" + "nie": "endpoint nie",
    "nie" + "blokuje": "nie blokuje",
    "PID" + "dają": "PID dają",
    "active_state" + "depends": "active_state depends",
}


def sanitize_status_payload(value: Any) -> Any:
    if isinstance(value, str):
        for old, new in STATUS_TEXT_REPLACEMENTS.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, dict):
        return {key: sanitize_status_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_status_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_status_payload(item) for item in value)
    return value


if "_ORIGINAL_WRITE_JSON_ATOMIC_UNSANITIZED" not in globals():
    _ORIGINAL_WRITE_JSON_ATOMIC_UNSANITIZED = write_json_atomic

    def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        return _ORIGINAL_WRITE_JSON_ATOMIC_UNSANITIZED(path, sanitize_status_payload(payload))


if "_ORIGINAL_JSON_RESPONSE_UNSANITIZED" not in globals():
    _ORIGINAL_JSON_RESPONSE_UNSANITIZED = JaznDaemonHandler._json_response

    def _sanitized_json_response(self: JaznDaemonHandler, payload: dict[str, Any], *, status: int = 200) -> None:
        return _ORIGINAL_JSON_RESPONSE_UNSANITIZED(self, sanitize_status_payload(payload), status=status)

    JaznDaemonHandler._json_response = _sanitized_json_response


def _wrap_status_payload_function(name: str) -> None:
    original = globals().get(name)
    if original is None or getattr(original, "_status_payload_sanitized", False):
        return

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return sanitize_status_payload(original(*args, **kwargs))

    wrapped.__name__ = getattr(original, "__name__", name)
    wrapped.__doc__ = getattr(original, "__doc__", None)
    wrapped._status_payload_sanitized = True  # type: ignore[attr-defined]
    globals()[f"_ORIGINAL_{name.upper()}_UNSANITIZED"] = original
    globals()[name] = wrapped


for _status_function_name in (
    "start_daemon",
    "status_daemon",
    "stop_daemon",
    "refresh_daemon_time",
    "inject_daemon_trusted_time",
):
    _wrap_status_payload_function(_status_function_name)
