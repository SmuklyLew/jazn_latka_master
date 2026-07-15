from __future__ import annotations

import copy
import os
from dataclasses import asdict, dataclass
from typing import Any, Mapping
from latka_jazn.model_adapters.null_model_adapter import NullModelAdapter

from latka_jazn.core.llm_route_resolver import (
    ROUTE_CHATGPT_BRIDGE,
    ROUTE_LOCAL,
    ROUTE_NULL,
    ROUTE_OPENAI_PAID,
    LlmRouteStatus,
    apply_llm_route_to_config,
    build_llm_route_status,
)
from latka_jazn.core.runtime_environment import CHATGPT_ADAPTER, NULL_ADAPTER
from latka_jazn.model_adapters.factory import build_model_adapter
from latka_jazn.version import schema_version


SPEECH_STATUS_SCHEMA = schema_version("model_guided_speech_status")
SPEECH_ROUTE_ENV = "JAZN_MODEL_GUIDED_SPEECH_ROUTE"
SPEECH_ENABLED_ENV = "JAZN_MODEL_GUIDED_SPEECH"


@dataclass(slots=True)
class ModelGuidedSpeechStatus:
    ok: bool
    selected_route: str
    selected_adapter: str
    selected_provider: str
    model: str | None
    can_attempt_model_guided_speech: bool
    can_generate_model_guided_speech: bool
    can_generate_model_guided_speech_locally: bool
    can_complete_model_guided_speech_via_host: bool
    generation_executor: str
    host_completion_required: bool
    requires_host_model: bool
    paid_route: bool
    paid_route_allowed: bool
    status: str
    reason: str
    llm_route_status: dict[str, Any]
    adapter_status: dict[str, Any]
    schema_version: str = SPEECH_STATUS_SCHEMA
    truth_boundary: str = (
        "Adapter mowy generuje wyłącznie kandydata językowego z kontekstu runtime. "
        "Nie jest źródłem tożsamości, pamięci, czasu ani prawdy Jaźni; final_visible_text nadal przechodzi przez runtime, walidację i provenance."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "tak", "on"}


def _speech_disabled(env: Mapping[str, str]) -> bool:
    value = str(env.get(SPEECH_ENABLED_ENV, "1")).strip().lower()
    return value in {"0", "false", "no", "nie", "off"}


def _command_from_client_context(client_context: Mapping[str, Any] | None) -> str | None:
    if not isinstance(client_context, Mapping):
        return None
    explicit = str(client_context.get("command") or client_context.get("runtime_command") or "").strip()
    if explicit:
        return explicit
    client = str(client_context.get("client") or "").strip().lower()
    language_channel = str(client_context.get("language_channel") or "").strip().lower()
    if "chatgpt" in client or "chatgpt" in language_channel:
        return "--chat-gpt"
    if "terminal" in client or language_channel in {"terminal", "chat"}:
        return "--chat"
    return None


def configured_speech_config(
    config: Any,
    *,
    command: str | None = None,
    env: Mapping[str, str] | None = None,
    infer_host_environment: bool = False,
    probe_local: bool = True,
) -> tuple[Any, LlmRouteStatus]:
    """Return a copy of config with the effective speech adapter selected.

    The original config is not mutated. Route metadata is returned as the second
    tuple item instead of being attached to config, because JaznConfig uses slots
    and intentionally does not allow arbitrary runtime-only attributes.
    """

    env_map: Mapping[str, str] = env if env is not None else os.environ
    routed = copy.copy(config)
    if _speech_disabled(env_map):
        setattr(routed, "model_adapter", "null")
        status = build_llm_route_status(
            routed,
            command=command,
            env=env_map,
            infer_host_environment=infer_host_environment,
            probe_local=False,
        )
        return routed, status
    status = build_llm_route_status(
        config,
        command=command,
        env=env_map,
        infer_host_environment=infer_host_environment,
        probe_local=probe_local,
    )
    routed = apply_llm_route_to_config(routed, status)
    return routed, status


def build_speech_adapter_for_turn(
    config: Any,
    *,
    client_context: Mapping[str, Any] | None = None,
    command: str | None = None,
    env: Mapping[str, str] | None = None,
    fallback_adapter: Any | None = None,
    probe_local: bool = True,
) -> tuple[Any, ModelGuidedSpeechStatus]:
    """Build the effective speech adapter for one runtime turn.

    If no route is available, returns the provided fallback adapter or a freshly
    built null adapter. ChatGPT host bridge is reported as requiring host model,
    not as local generation.
    """

    env_map: Mapping[str, str] = env if env is not None else os.environ
    effective_command = command or _command_from_client_context(client_context)
    infer_host = bool(effective_command == "--chat-gpt" or _truthy(env_map.get("JAZN_ASSUME_CHATGPT_HOST")))
    routed_config, route_status = configured_speech_config(
        config,
        command=effective_command,
        env=env_map,
        infer_host_environment=infer_host,
        probe_local=probe_local,
    )
    adapter = build_model_adapter(routed_config)
    if route_status.selected_route == ROUTE_NULL and fallback_adapter is not None:
        adapter = fallback_adapter
    status = build_model_guided_speech_status(
        routed_config,
        command=effective_command,
        env=env_map,
        infer_host_environment=infer_host,
        probe_local=False,
        adapter=adapter,
        llm_route_status=route_status,
    )
    return adapter, status


def build_model_guided_speech_status(
    config: Any,
    *,
    command: str | None = None,
    env: Mapping[str, str] | None = None,
    infer_host_environment: bool = False,
    probe_local: bool = True,
    adapter: Any | None = None,
    llm_route_status: LlmRouteStatus | None = None,
) -> ModelGuidedSpeechStatus:
    env_map: Mapping[str, str] = env if env is not None else os.environ
    routed_config, route_status = configured_speech_config(
        config,
        command=command,
        env=env_map,
        infer_host_environment=infer_host_environment,
        probe_local=probe_local,
    ) if llm_route_status is None else (config, llm_route_status)
    if _speech_disabled(env_map):
        effective_adapter = adapter or NullModelAdapter()
    else:
        effective_adapter = adapter or build_model_adapter(routed_config)
    adapter_status = effective_adapter.describe() if hasattr(effective_adapter, "describe") else {"status": "unknown"}
    contract = adapter_status.get("adapter_contract") if isinstance(adapter_status.get("adapter_contract"), dict) else {}
    adapter_id = str(
        adapter_status.get("adapter_id")
        or adapter_status.get("name")
        or contract.get("adapter_id")
        or route_status.selected_adapter
        or NULL_ADAPTER
    )
    provider = str(adapter_status.get("provider") or contract.get("provider") or adapter_id)
    model = adapter_status.get("model") or adapter_status.get("model_name") or contract.get("model_name")
    can_attempt = bool(adapter_status.get("can_attempt_model_guided_speech") or contract.get("can_attempt_model_guided_speech"))
    can_generate = bool(adapter_status.get("can_generate_model_guided_speech") or contract.get("can_generate_model_guided_speech"))
    can_generate_locally = can_generate
    can_complete_via_host = False
    generation_executor = str(provider or adapter_id or "none")
    host_completion_required = False
    requires_host = bool(route_status.selected_route == ROUTE_CHATGPT_BRIDGE or adapter_id == CHATGPT_ADAPTER)

    if route_status.selected_route == ROUTE_CHATGPT_BRIDGE:
        status = "requires_host_chatgpt_visible_response"
        reason = "chatgpt_host_bridge_selected"
        ok = True
        can_attempt = True
        can_generate = True
        can_generate_locally = False
        can_complete_via_host = True
        generation_executor = "chatgpt_host"
        host_completion_required = True
    elif _speech_disabled(env_map):
        status = str(adapter_status.get("status") or "not_configured")
        reason = "model_guided_speech_disabled_by_env"
        ok = False
        can_attempt = False
        can_generate = False
    elif route_status.selected_route in {ROUTE_LOCAL, ROUTE_OPENAI_PAID}:
        ok = bool(can_attempt or can_generate)
        status = str(adapter_status.get("status") or contract.get("status") or ("configured" if ok else "not_configured"))
        reason = "model_guided_speech_adapter_selected" if ok else "selected_adapter_not_configured"
    else:
        ok = adapter_id != NULL_ADAPTER and bool(can_attempt or can_generate)
        status = str(adapter_status.get("status") or "not_configured")
        reason = "null_fallback_no_model_guided_speech" if adapter_id == NULL_ADAPTER else "fallback_adapter_status"

    return ModelGuidedSpeechStatus(
        ok=ok,
        selected_route=route_status.selected_route,
        selected_adapter=adapter_id,
        selected_provider=provider,
        model=str(model) if model else None,
        can_attempt_model_guided_speech=can_attempt,
        can_generate_model_guided_speech=can_generate,
        can_generate_model_guided_speech_locally=can_generate_locally,
        can_complete_model_guided_speech_via_host=can_complete_via_host,
        generation_executor=generation_executor,
        host_completion_required=host_completion_required,
        requires_host_model=requires_host,
        paid_route=bool(route_status.paid_route),
        paid_route_allowed=bool(route_status.paid_route_allowed),
        status=status,
        reason=reason,
        llm_route_status=route_status.to_dict(),
        adapter_status=adapter_status,
    )
