from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from typing import Any, Mapping, TextIO

from latka_jazn.version import schema_version

CHATGPT_ADAPTER = "chatgpt_runtime_adapter"
TERMINAL_ADAPTER = "terminal_runtime_adapter"
OPENAI_ADAPTER = "openai_responses_adapter"
LMSTUDIO_ADAPTER = "lmstudio_runtime_adapter"
OPENAI_COMPATIBLE_ADAPTER = "openai_compatible_local_adapter"
CODEX_ADAPTER = "codex_development_adapter"
NULL_ADAPTER = "null_model_adapter"

_CHATGPT_COMMANDS = {"--chat-gpt", "--chat-gpt-final-only"}  # legacy alias canonicalizes to --chat-gpt
_TERMINAL_COMMANDS = {"--chat", "--loop"}
_OPENAI_COMMANDS = {"--chat-open-ai"}
_LMSTUDIO_COMMANDS = {"--chat-lm-studio"}
_LOCAL_LLM_COMMANDS = {"--local-llm"}


def _adapter_name(config: Any) -> str:
    raw = str(getattr(config, "model_adapter", "null") or "null").strip().lower()
    if raw in {"", "null", "none", "null_model_adapter", "auto"}:
        return NULL_ADAPTER
    if raw in {"chatgpt", "chatgpt_runtime", "chatgpt_runtime_adapter", "chat_gpt", "chat-gpt"}:
        return CHATGPT_ADAPTER
    if raw in {"chat", "terminal", "terminal_runtime", "terminal_runtime_adapter", "local_terminal", "chat_loop"}:
        return TERMINAL_ADAPTER
    if raw in {"openai", "openai_responses", "openai_responses_adapter"}:
        return OPENAI_ADAPTER
    if raw in {"lmstudio", "lm_studio", "lmstudio_runtime", "lmstudio_runtime_adapter"}:
        return LMSTUDIO_ADAPTER
    if raw in {"local_llm", "openai_compatible", "openai_compatible_local", "openai_compatible_local_adapter"}:
        return OPENAI_COMPATIBLE_ADAPTER
    if raw in {"codex", "codex_development", "codex_development_adapter"}:
        return CODEX_ADAPTER
    return raw


def _truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "tak", "on"}


def _normalize_channel(value: str | None) -> str | None:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw in {"chatgpt", "chat-gpt", "chatgpt-host", "openai-chatgpt", "host-chatgpt"}:
        return "chatgpt"
    if raw in {"terminal", "chat", "cli", "local-terminal", "terminal-chat"}:
        return "terminal"
    if raw in {"openai", "openai-api", "responses", "responses-api"}:
        return "openai"
    if raw in {"null", "none", "offline"}:
        return "null"
    return None


def _stream_isatty(stream: TextIO | None) -> bool | None:
    if stream is None:
        return None
    try:
        return bool(stream.isatty())
    except Exception:
        return None


def _detected_openai_chatgpt_tool_container(env: Mapping[str, str]) -> bool:
    # These are host/container signals seen by the ChatGPT tool runtime. They are
    # not treated as proof that local Python can call ChatGPT; they only select a
    # visible-channel status when no explicit command or env marker is provided.
    if _truthy_env(env.get("JAZN_ASSUME_CHATGPT_HOST")):
        return True
    if env.get("JAZN_HOST_RUNTIME", "").strip().lower() in {"chatgpt", "chatgpt_host", "openai_chatgpt"}:
        return True
    if env.get("JAZN_VISIBLE_CHANNEL", "").strip().lower() in {"chatgpt", "chatgpt_host", "openai_chatgpt"}:
        return True
    if "JUPYTER_SERVER_OAI_PATH" in env:
        return True
    return any(key.startswith("CUA_DD_") for key in env)


@dataclass(slots=True)
class RuntimeEnvironmentStatus:
    explicit_command: str | None
    selected_backend_adapter: str
    visible_channel_adapter: str | None
    effective_runtime_adapter: str
    environment_host: str
    detection_basis: list[str]
    stdin_isatty: bool | None
    stdout_isatty: bool | None
    is_chatgpt_host_bridge: bool
    is_terminal_chat_loop: bool
    uses_openai_api: bool
    requires_openai_api_key: bool
    schema_version: str = schema_version("runtime_environment")
    truth_boundary: str = (
        "Środowisko i kanał widzialnej rozmowy są oddzielone od backendu modelu. "
        "chatgpt_runtime_adapter oznacza kanał hosta ChatGPT/copy-paste/JSONL, nie lokalne wywołanie modelu ChatGPT. "
        "terminal_runtime_adapter oznacza pętlę terminalową, nie lokalny model generacyjny. "
        "null_model_adapter pozostaje bazowym fallbackiem offline, gdy nie wybrano kanału ani backendu."
    )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        configured_backend = self.selected_backend_adapter
        payload["configured_backend_adapter"] = configured_backend
        payload["programmatic_backend_adapter"] = configured_backend
        payload["selected_backend_adapter"] = self.effective_runtime_adapter
        payload["selection_scope"] = "effective_runtime_channel"
        payload["host_bridge_external_generation_required"] = bool(self.is_chatgpt_host_bridge)
        return payload


def detect_runtime_environment(
    config: Any,
    *,
    command: str | None = None,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    infer_host_environment: bool = False,
) -> RuntimeEnvironmentStatus:
    env_map: Mapping[str, str] = env if env is not None else os.environ
    selected = _adapter_name(config)
    raw_explicit = str(command or "").strip() or None
    explicit = "--chat-gpt" if raw_explicit in _CHATGPT_COMMANDS else raw_explicit
    visible: str | None = None
    host = "unknown"
    basis: list[str] = []
    uses_openai = False
    requires_key = False

    if explicit in _CHATGPT_COMMANDS:
        visible = CHATGPT_ADAPTER
        host = "chatgpt_explicit_command"
        basis.append(f"explicit_command:{explicit}")
        if raw_explicit and raw_explicit != explicit:
            basis.append(f"legacy_alias:{raw_explicit}")
    elif explicit in _TERMINAL_COMMANDS:
        visible = TERMINAL_ADAPTER
        host = "terminal_explicit_command"
        basis.append(f"explicit_command:{explicit}")
    elif explicit in _OPENAI_COMMANDS:
        visible = OPENAI_ADAPTER
        host = "openai_api_explicit_command"
        basis.append(f"explicit_command:{explicit}")
        uses_openai = True
        requires_key = True
    elif explicit in _LMSTUDIO_COMMANDS:
        visible = LMSTUDIO_ADAPTER
        host = "lmstudio_explicit_command"
        basis.append(f"explicit_command:{explicit}")
    elif explicit in _LOCAL_LLM_COMMANDS:
        visible = OPENAI_COMPATIBLE_ADAPTER
        host = "openai_compatible_local_explicit_command"
        basis.append(f"explicit_command:{explicit}")

    if visible is None:
        channel = _normalize_channel(env_map.get("JAZN_VISIBLE_CHANNEL") or env_map.get("JAZN_HOST_RUNTIME"))
        if channel == "chatgpt":
            visible = CHATGPT_ADAPTER
            host = "chatgpt_env_marker"
            basis.append("env:JAZN_VISIBLE_CHANNEL/JAZN_HOST_RUNTIME")
        elif channel == "terminal":
            visible = TERMINAL_ADAPTER
            host = "terminal_env_marker"
            basis.append("env:JAZN_VISIBLE_CHANNEL/JAZN_HOST_RUNTIME")
        elif channel == "openai":
            visible = OPENAI_ADAPTER
            host = "openai_api_env_marker"
            basis.append("env:JAZN_VISIBLE_CHANNEL/JAZN_HOST_RUNTIME")
            uses_openai = True
            requires_key = True
        elif channel == "null":
            visible = None
            host = "null_env_marker"
            basis.append("env:JAZN_VISIBLE_CHANNEL/JAZN_HOST_RUNTIME")

    if visible is None and selected in {CHATGPT_ADAPTER, TERMINAL_ADAPTER, OPENAI_ADAPTER, LMSTUDIO_ADAPTER, OPENAI_COMPATIBLE_ADAPTER}:
        visible = selected
        host = f"configured_{selected}"
        basis.append("config.model_adapter")
        if selected == OPENAI_ADAPTER:
            uses_openai = True
            requires_key = True

    if visible is None and infer_host_environment and _detected_openai_chatgpt_tool_container(env_map):
        visible = CHATGPT_ADAPTER
        host = "openai_chatgpt_tool_container"
        basis.append("detected_openai_chatgpt_tool_container")

    effective = visible or selected
    if effective == "null":
        effective = NULL_ADAPTER
    if effective == OPENAI_ADAPTER:
        uses_openai = True
        requires_key = True
    if not basis:
        basis.append("config.model_adapter_default")

    return RuntimeEnvironmentStatus(
        explicit_command=explicit,
        selected_backend_adapter=selected,
        visible_channel_adapter=visible,
        effective_runtime_adapter=effective,
        environment_host=host,
        detection_basis=basis,
        stdin_isatty=_stream_isatty(stdin if stdin is not None else sys.stdin),
        stdout_isatty=_stream_isatty(stdout if stdout is not None else sys.stdout),
        is_chatgpt_host_bridge=effective == CHATGPT_ADAPTER,
        is_terminal_chat_loop=effective == TERMINAL_ADAPTER,
        uses_openai_api=uses_openai,
        requires_openai_api_key=requires_key,
    )


def apply_effective_runtime_adapter(config: Any, environment: RuntimeEnvironmentStatus) -> Any:
    effective = environment.effective_runtime_adapter
    if effective == NULL_ADAPTER:
        setattr(config, "model_adapter", "null")
    else:
        setattr(config, "model_adapter", effective)
    if effective == CHATGPT_ADAPTER and not os.environ.get("JAZN_MODEL_NAME"):
        setattr(config, "model_name", os.environ.get("JAZN_CHATGPT_MODEL_NAME", "chatgpt_host_model").strip() or "chatgpt_host_model")
    if effective == TERMINAL_ADAPTER and not os.environ.get("JAZN_TERMINAL_MODEL_NAME"):
        setattr(config, "terminal_model_name", "terminal_visible_layer")
    return config
