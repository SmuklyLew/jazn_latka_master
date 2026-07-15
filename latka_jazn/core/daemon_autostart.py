from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from latka_jazn.config import JaznConfig
from latka_jazn.core.runtime_daemon import (
    DEFAULT_DAEMON_HOST,
    DEFAULT_DAEMON_PORT,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_START_TIMEOUT_SECONDS,
    DAEMON_SCHEMA_VERSION,
    start_daemon,
    status_daemon,
)
from latka_jazn.version import schema_version

AUTOSTART_ENV = "JAZN_DAEMON_AUTOSTART"
FORCE_ENSURE_ENV = "JAZN_ENSURE_DAEMON"
AUTOSTART_COMMANDS_ENV = "JAZN_DAEMON_AUTOSTART_COMMANDS"

ACTIVE_STATES = {"active_trusted", "active_degraded"}
ACTIVE_TRUSTED = "active_trusted"
ACTIVE_DEGRADED = "active_degraded"

RUNTIME_TURN_COMMANDS = {
    "--chat",
    "--chat-gpt",
    "--chat-gpt-final-only",
    "--chat-open-ai",
    "--chat-openai",
    "--chat-lm-studio",
    "--local-llm",
    "--daemon-send",
    "--daemon-submit",
    "direct_message",
}

NEVER_AUTOSTART_COMMANDS = {
    "--daemon-status",
    "--daemon-stop",
    "--daemon-run",
    "--daemon-start",
}

OBSERVATIONAL_COMMANDS = {
    "--startup-status",
    "--startup-status-fast",
    "--startup-status-deep",
    "--status-json",
    "--active-cache-status",
    "--llm-route-status",
    "--model-adapter-status",
    "--daemon-result",
}


@dataclass(slots=True)
class DaemonAutostartDecision:
    command: str | None
    should_ensure: bool
    reason: str
    explicit_ensure: bool = False
    disabled_for_turn: bool = False
    env_autostart: bool = True
    env_force: bool = False
    command_known_runtime_turn: bool = False
    command_observational: bool = False
    command_forbidden: bool = False
    schema_version: str = schema_version("daemon_autostart_decision")
    truth_boundary: str = (
        "Autostart daemonu dotyczy tylko tras rozmowy/runtime turn. "
        "Status, stop i foreground run pozostają komendami obserwacyjnymi/kontrolnymi i nie uruchamiają daemonu przypadkiem."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DaemonEnsureResult:
    ok: bool
    ensured: bool
    active_state: str
    reason: str
    decision: dict[str, Any]
    status_before: dict[str, Any] | None = None
    startup: dict[str, Any] | None = None
    status_after: dict[str, Any] | None = None
    schema_version: str = schema_version("daemon_ensure_result")
    truth_boundary: str = (
        "ensure_daemon_for_runtime_turn może uruchomić daemon tylko dla trasy rozmowy albo jawnego --ensure-daemon. "
        "Sukces oznacza aktywny trusted/degraded runtime potwierdzony przez marker, PID/endpoint/heartbeat według status_daemon."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _truthy(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "tak", "on"}


def _falsey(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "nie", "off"}


def _command_set_from_env(env: Mapping[str, str]) -> set[str] | None:
    raw = str(env.get(AUTOSTART_COMMANDS_ENV, "")).strip()
    if not raw:
        return None
    values = {item.strip() for item in raw.replace(";", ",").split(",") if item.strip()}
    return values or None


def _status_active_state(status: Mapping[str, Any] | None) -> str:
    if not isinstance(status, Mapping):
        return "inactive"
    return str(status.get("active_state") or status.get("runtime_active_state") or "inactive")


def status_allows_runtime_turn(status: Mapping[str, Any] | None, *, allow_degraded: bool = True) -> bool:
    active_state = _status_active_state(status)
    if active_state == ACTIVE_TRUSTED:
        return True
    if allow_degraded and active_state == ACTIVE_DEGRADED:
        return True
    return False


def daemon_autostart_decision(
    command: str | None,
    *,
    explicit_ensure: bool = False,
    disabled_for_turn: bool = False,
    env: Mapping[str, str] | None = None,
) -> DaemonAutostartDecision:
    env_map: Mapping[str, str] = env or {}
    command_norm = str(command or "").strip() or None
    env_autostart = not _falsey(env_map.get(AUTOSTART_ENV))
    env_force = _truthy(env_map.get(FORCE_ENSURE_ENV), default=False)
    command_set = _command_set_from_env(env_map)
    command_forbidden = bool(command_norm in NEVER_AUTOSTART_COMMANDS)
    command_observational = bool(command_norm in OBSERVATIONAL_COMMANDS)
    command_known_runtime_turn = bool(command_norm in RUNTIME_TURN_COMMANDS)

    if disabled_for_turn:
        return DaemonAutostartDecision(
            command=command_norm,
            should_ensure=False,
            reason="disabled_by_cli_no_ensure_daemon",
            explicit_ensure=explicit_ensure,
            disabled_for_turn=True,
            env_autostart=env_autostart,
            env_force=env_force,
            command_known_runtime_turn=command_known_runtime_turn,
            command_observational=command_observational,
            command_forbidden=command_forbidden,
        )
    if command_forbidden:
        return DaemonAutostartDecision(
            command=command_norm,
            should_ensure=False,
            reason="command_must_not_autostart_daemon",
            explicit_ensure=explicit_ensure,
            env_autostart=env_autostart,
            env_force=env_force,
            command_known_runtime_turn=command_known_runtime_turn,
            command_observational=command_observational,
            command_forbidden=True,
        )
    if explicit_ensure or env_force:
        return DaemonAutostartDecision(
            command=command_norm,
            should_ensure=True,
            reason="explicit_ensure_daemon" if explicit_ensure else "env_JAZN_ENSURE_DAEMON",
            explicit_ensure=explicit_ensure,
            env_autostart=env_autostart,
            env_force=env_force,
            command_known_runtime_turn=command_known_runtime_turn,
            command_observational=command_observational,
            command_forbidden=False,
        )
    if command_observational:
        return DaemonAutostartDecision(
            command=command_norm,
            should_ensure=False,
            reason="observational_command_does_not_autostart",
            env_autostart=env_autostart,
            env_force=env_force,
            command_known_runtime_turn=command_known_runtime_turn,
            command_observational=True,
        )
    if not env_autostart:
        return DaemonAutostartDecision(
            command=command_norm,
            should_ensure=False,
            reason="env_JAZN_DAEMON_AUTOSTART_disabled",
            env_autostart=False,
            env_force=env_force,
            command_known_runtime_turn=command_known_runtime_turn,
            command_observational=command_observational,
        )
    if command_set is not None and command_norm not in command_set:
        return DaemonAutostartDecision(
            command=command_norm,
            should_ensure=False,
            reason="command_not_in_JAZN_DAEMON_AUTOSTART_COMMANDS",
            env_autostart=env_autostart,
            env_force=env_force,
            command_known_runtime_turn=command_known_runtime_turn,
            command_observational=command_observational,
        )
    return DaemonAutostartDecision(
        command=command_norm,
        should_ensure=command_known_runtime_turn,
        reason="runtime_turn_requires_daemon" if command_known_runtime_turn else "command_not_runtime_turn",
        env_autostart=env_autostart,
        env_force=env_force,
        command_known_runtime_turn=command_known_runtime_turn,
        command_observational=command_observational,
    )


def ensure_daemon_for_runtime_turn(
    config: JaznConfig,
    *,
    command: str | None,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
    marker_output: Path | None = None,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    startup_timeout: float = DEFAULT_START_TIMEOUT_SECONDS,
    explicit_ensure: bool = False,
    disabled_for_turn: bool = False,
    env: Mapping[str, str] | None = None,
    allow_degraded: bool = True,
) -> DaemonEnsureResult:
    env_map = env if env is not None else __import__("os").environ
    decision = daemon_autostart_decision(
        command,
        explicit_ensure=explicit_ensure,
        disabled_for_turn=disabled_for_turn,
        env=env_map,
    )
    status_before = status_daemon(config, host=host, port=port, marker_output=marker_output)
    before_state = _status_active_state(status_before)
    if status_allows_runtime_turn(status_before, allow_degraded=allow_degraded):
        return DaemonEnsureResult(
            ok=True,
            ensured=True,
            active_state=before_state,
            reason="daemon_already_active",
            decision=decision.to_dict(),
            status_before=status_before,
            status_after=status_before,
        )
    if not decision.should_ensure:
        return DaemonEnsureResult(
            ok=False,
            ensured=False,
            active_state=before_state,
            reason=decision.reason,
            decision=decision.to_dict(),
            status_before=status_before,
            status_after=status_before,
        )
    startup = start_daemon(
        config,
        host=host,
        port=port,
        marker_output=marker_output,
        heartbeat_interval=heartbeat_interval,
        startup_timeout=startup_timeout,
    )
    status_after = status_daemon(config, host=host, port=port, marker_output=marker_output)
    after_state = _status_active_state(status_after)
    ok = status_allows_runtime_turn(status_after, allow_degraded=allow_degraded)
    return DaemonEnsureResult(
        ok=ok,
        ensured=ok,
        active_state=after_state,
        reason="daemon_started_or_reused" if ok else "daemon_start_failed",
        decision=decision.to_dict(),
        status_before=status_before,
        startup=startup,
        status_after=status_after,
    )


def daemon_autostart_policy_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env_map = env if env is not None else __import__("os").environ
    return {
        "schema_version": schema_version("daemon_autostart_policy"),
        "daemon_schema_version": DAEMON_SCHEMA_VERSION,
        "enabled_by_default": not _falsey(env_map.get(AUTOSTART_ENV)),
        "force_env": _truthy(env_map.get(FORCE_ENSURE_ENV), default=False),
        "autostart_env": env_map.get(AUTOSTART_ENV),
        "command_filter_env": env_map.get(AUTOSTART_COMMANDS_ENV),
        "runtime_turn_commands": sorted(RUNTIME_TURN_COMMANDS),
        "observational_commands": sorted(OBSERVATIONAL_COMMANDS),
        "never_autostart_commands": sorted(NEVER_AUTOSTART_COMMANDS),
        "truth_boundary": "Autostart jest kontraktem liveness dla tras rozmowy, nie dowodem świadomości ani zgodą na start przy komendach status/stop.",
    }
