from __future__ import annotations

from typing import Any
import copy
import os

from .null_model_adapter import NullModelAdapter
from .chatgpt_runtime_adapter import ChatgptRuntimeAdapter
from .terminal_runtime_adapter import TerminalRuntimeAdapter
from .openai_responses_adapter import OpenaiResponsesAdapter
from .local_llm_adapter import LocalLlmAdapter
from .lmstudio_runtime_adapter import LmStudioRuntimeAdapter
from .adapter_contract import ContractOnlyModelAdapter, backend_config_skeletons
from .openai_compatible_local_adapter import OpenAICompatibleLocalAdapter
from latka_jazn.core.llm_route_resolver import apply_llm_route_to_config, build_llm_route_status
from latka_jazn.core.runtime_environment import CHATGPT_ADAPTER, CODEX_ADAPTER, LMSTUDIO_ADAPTER, NULL_ADAPTER, OPENAI_COMPATIBLE_ADAPTER, apply_effective_runtime_adapter, detect_runtime_environment


_NULL_ALIASES = {"", "null", "none", "off", "fallback", "null_model_adapter"}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "tak", "on"}


def _route_disabled() -> bool:
    return str(os.environ.get("JAZN_MODEL_GUIDED_SPEECH", "1")).strip().lower() in {"0", "false", "no", "nie", "off"}


def _has_explicit_non_null_adapter(config: Any) -> bool:
    if os.environ.get("JAZN_MODEL_ADAPTER"):
        return str(os.environ.get("JAZN_MODEL_ADAPTER") or "").strip().lower() not in _NULL_ALIASES
    return str(getattr(config, "model_adapter", "null") or "null").strip().lower() not in _NULL_ALIASES | {"auto"}


def _config_with_llm_route(config: Any, *, command: str | None = None) -> Any:
    """Apply automatic LLM route before constructing the speech adapter.

    Explicit non-null `JAZN_MODEL_ADAPTER` / config.model_adapter still wins. The
    route resolver only fills the adapter when runtime is otherwise on null/auto.
    Route metadata is intentionally not attached to config because JaznConfig uses
    slots and forbids arbitrary runtime-only attributes.
    """

    if _route_disabled() or _has_explicit_non_null_adapter(config):
        return config
    env_mode = str(os.environ.get("JAZN_LLM_ROUTE") or getattr(config, "llm_route_mode", "auto") or "auto").strip().lower()
    if env_mode in {"none", "null", "off"}:
        return config
    routed = copy.copy(config)
    route_status = build_llm_route_status(
        config,
        command=command or os.environ.get("JAZN_RUNTIME_COMMAND"),
        infer_host_environment=_truthy(os.environ.get("JAZN_ASSUME_CHATGPT_HOST")),
        probe_local=not _truthy(os.environ.get("JAZN_LLM_ROUTE_SKIP_LOCAL_PROBE")),
    )
    apply_llm_route_to_config(routed, route_status)
    return routed


def build_model_adapter(config: Any):
    config = _config_with_llm_route(config)
    name = str(getattr(config, "model_adapter", "null") or "null").strip().lower()
    if name in {"chatgpt", "chatgpt_runtime", "chatgpt_runtime_adapter", "chat_gpt", "chat-gpt"}:
        return ChatgptRuntimeAdapter(
            model=str(getattr(config, "model_name", "chatgpt_host_model") or "chatgpt_host_model"),
            root=getattr(config, "root", None),
        )
    if name in {"chat", "terminal", "terminal_runtime", "terminal_runtime_adapter", "local_terminal", "chat_loop"}:
        return TerminalRuntimeAdapter(
            model=str(getattr(config, "terminal_model_name", "terminal_visible_layer") or "terminal_visible_layer"),
            root=getattr(config, "root", None),
        )
    if name in {"openai", "openai_responses", "openai_responses_adapter"} and bool(getattr(config, "allow_network", True)):
        return OpenaiResponsesAdapter(
            model=str(getattr(config, "model_name", "") or ""),
            api_base=str(getattr(config, "model_api_base", "https://api.openai.com/v1")),
            timeout_seconds=float(getattr(config, "model_timeout_seconds", 45.0)),
            max_output_tokens=int(getattr(config, "model_max_output_tokens", 800)),
            root=getattr(config, "root", None),
        )
    if name in {"local", "ollama", "local_llm_adapter"}:
        return LocalLlmAdapter(
            model=str(getattr(config, "local_model_name", "")),
            api_base=str(getattr(config, "local_model_api_base", "http://127.0.0.1:11434")),
            timeout_seconds=float(getattr(config, "model_timeout_seconds", 45.0)),
            max_output_tokens=int(getattr(config, "model_max_output_tokens", 800)),
            root=getattr(config, "root", None),
        )
    if name in {"ollama_openai", "ollama_openai_compatible"}:
        base = str(getattr(config, "local_model_api_base", "http://127.0.0.1:11434/v1"))
        if not base.rstrip("/").endswith("/v1"):
            base = base.rstrip("/") + "/v1"
        return OpenAICompatibleLocalAdapter(
            provider="ollama",
            adapter_id="ollama_openai_compatible_adapter",
            model=str(getattr(config, "local_model_name", "")),
            api_base=base,
            api_key=os.environ.get("JAZN_LOCAL_LLM_API_KEY", "ollama"),
            timeout_seconds=float(getattr(config, "model_timeout_seconds", 45.0)),
            max_output_tokens=int(getattr(config, "model_max_output_tokens", 800)),
        )
    if name in {"local_llm", "openai_compatible", "openai_compatible_local", "openai_compatible_local_adapter", "external_openai_compatible"}:
        return OpenAICompatibleLocalAdapter(
            provider=os.environ.get("JAZN_LOCAL_LLM_PROVIDER", "openai_compatible"),
            adapter_id=OPENAI_COMPATIBLE_ADAPTER,
            model=os.environ.get("JAZN_LOCAL_LLM_MODEL", str(getattr(config, "local_model_name", ""))),
            api_base=os.environ.get("JAZN_LOCAL_LLM_API_BASE", str(getattr(config, "local_model_api_base", "http://127.0.0.1:8080/v1"))),
            api_key=os.environ.get("JAZN_LOCAL_LLM_API_KEY", ""),
            timeout_seconds=float(getattr(config, "model_timeout_seconds", 45.0)),
            max_output_tokens=int(getattr(config, "model_max_output_tokens", 800)),
        )
    if name in {"llama_cpp", "llamacpp", "llama.cpp"}:
        return OpenAICompatibleLocalAdapter(
            provider="llama_cpp",
            adapter_id="llama_cpp_openai_compatible_adapter",
            model=str(getattr(config, "llama_cpp_model_name", "")),
            api_base=str(getattr(config, "llama_cpp_model_api_base", "http://127.0.0.1:8080/v1")),
            timeout_seconds=float(getattr(config, "model_timeout_seconds", 45.0)),
            max_output_tokens=int(getattr(config, "model_max_output_tokens", 800)),
        )
    if name in {"lmstudio", "lm_studio", "lmstudio_runtime", LMSTUDIO_ADAPTER}:
        return LmStudioRuntimeAdapter(
            model=str(getattr(config, "lm_studio_model_name", "")),
            api_base=str(getattr(config, "lm_studio_api_base", "http://127.0.0.1:1234/v1")),
            timeout_seconds=float(getattr(config, "lm_studio_timeout_seconds", 45.0)),
            max_output_tokens=int(getattr(config, "lm_studio_max_output_tokens", 800)),
            root=getattr(config, "root", None),
        )
    if name in {"codex", "codex_development", CODEX_ADAPTER}:
        return ContractOnlyModelAdapter(
            provider="codex",
            model="development_tooling",
            endpoint="local_codex_workspace",
            adapter_id=CODEX_ADAPTER,
            kind="development_tooling_status",
            failure_reason="codex_not_speech_adapter",
            response_status="codex_not_speech_adapter",
            truth_boundary=(
                "Codex is for repository work, tests, patches, and PRs. "
                "It is not Latka's voice and must not be used as a final_visible_text model adapter."
            ),
        )
    return NullModelAdapter()



def _environment_availability_basis(environment: Any) -> str | None:
    if not getattr(environment, "visible_channel_adapter", None):
        return None
    basis = list(getattr(environment, "detection_basis", []) or [])
    first = str(basis[0] if basis else "").strip()
    if first == "explicit_command:--chat-gpt":
        return "explicit_chat_gpt_bridge_command"
    if first == "explicit_command:--chat":
        return "explicit_chat_terminal_command"
    if first == "explicit_command:--chat-open-ai":
        return "explicit_openai_api_bridge_command"
    if first == "explicit_command:--chat-lm-studio":
        return "explicit_lmstudio_local_bridge_command"
    if first == "detected_openai_chatgpt_tool_container":
        return "detected_openai_chatgpt_tool_container"
    if first.startswith("env:"):
        return "environment_visible_channel_marker"
    if first == "config.model_adapter":
        return "configured_model_adapter"
    return first or None


def _apply_environment_basis(payload: dict[str, Any], environment: Any) -> dict[str, Any]:
    availability_basis = _environment_availability_basis(environment)
    if not availability_basis:
        return payload
    payload = dict(payload)
    payload["availability_basis"] = availability_basis
    contract = payload.get("adapter_contract")
    if isinstance(contract, dict):
        contract = dict(contract)
        contract["availability_basis"] = availability_basis
        payload["adapter_contract"] = contract
    return payload


def build_model_adapter_status(
    config: Any,
    *,
    command: str | None = None,
    infer_host_environment: bool = False,
) -> dict[str, Any]:
    environment = detect_runtime_environment(
        config,
        command=command,
        infer_host_environment=infer_host_environment,
    )
    base_status = build_model_adapter(config).describe()
    effective_config = apply_effective_runtime_adapter(copy.copy(config), environment)
    active = _apply_environment_basis(build_model_adapter(effective_config).describe(), environment)
    configured_backend = environment.selected_backend_adapter
    effective_adapter = environment.effective_runtime_adapter
    payload = {
        **active,
        "selected_adapter": effective_adapter,
        "selected_backend_adapter": effective_adapter,
        "configured_backend_adapter": configured_backend,
        "programmatic_backend_adapter": configured_backend,
        "visible_channel_adapter": environment.visible_channel_adapter,
        "effective_runtime_adapter": effective_adapter,
        "runtime_environment": environment.to_dict(),
        "backend_config_skeletons": backend_config_skeletons(config),
        "normal_runtime_requires_openai_api_key": False,
        "truth_boundary_summary": (
            "Adapter jest kanałem językowym, nie tożsamością Jaźni. selected_backend_adapter pokazuje efektywnie wybrany "
            "kanał tej komendy, a configured_backend_adapter/programmatic_backend_adapter pokazuje bazowy backend dostępny "
            "bez udziału hosta. Dla --chat-gpt host może domknąć model-guided speech, choć lokalny Python nie wywołuje modelu ChatGPT."
        ),
    }
    if effective_adapter == CHATGPT_ADAPTER:
        payload.update({
            "can_generate_model_guided_speech": True,
            "can_generate_model_guided_speech_locally": False,
            "can_complete_model_guided_speech_via_host": True,
            "host_generation_available": True,
            "host_completion_required": True,
            "generation_executor": "chatgpt_host",
            "capability_scope": "runtime_plus_visible_host",
        })
    else:
        payload.setdefault("can_generate_model_guided_speech_locally", bool(payload.get("can_generate_model_guided_speech")))
        payload.setdefault("can_complete_model_guided_speech_via_host", False)
        payload.setdefault("host_generation_available", False)
        payload.setdefault("host_completion_required", False)
        payload.setdefault("generation_executor", str(payload.get("provider") or "none"))
        payload.setdefault("capability_scope", "programmatic_backend")
    if environment.effective_runtime_adapter != environment.selected_backend_adapter:
        payload["base_backend_adapter_status"] = base_status
    return payload
