from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any, Literal, TextIO

from latka_jazn.config import JaznConfig
from latka_jazn.core.runtime_session import JaznRuntimeSession
from latka_jazn.core.host_visible_finalization import finalize_host_visible_text
from latka_jazn.core.runtime_ownership_contract import build_runtime_ownership_contract
from latka_jazn.core.turn_timeout import RuntimeSessionWorker, RuntimeTurnTimeoutError, runtime_turn_timeout_seconds
from latka_jazn.version import schema_version

ACCEPTED_CHATGPT_INPUT_FIELDS = ("message", "text", "user_text", "content", "prompt")
CHATGPT_BRIDGE_PROTOCOL = schema_version("chatgpt_bridge_jsonl")
CHAT_OPENAI_PROTOCOL = schema_version("chat_open_ai_jsonl")
OLLAMA_PROTOCOL = schema_version("chat_ollama_jsonl")
CHAT_BRIDGE_OUTPUT_MODES = ("jsonl", "final_visible_text")
KNOWN_CLI_FLAG_VALUE_POLICY = {
    "--session-id": True,
    "--no-carryover": False,
    "--trusted-time-iso": True,
    "--final-only": False,
}


def guard_cli_flags_in_user_text(user_text: str) -> tuple[str, dict[str, Any] | None]:
    """Remove leaked CLI arguments only from runtime classification input.

    The original message remains available in the warning and is copied into
    the turn trace by ``attach_cli_flag_warning``.
    """
    original = str(user_text or "")
    classification_text = original
    detected: list[str] = []
    for flag, consumes_value in KNOWN_CLI_FLAG_VALUE_POLICY.items():
        pattern = rf"(?<!\S){re.escape(flag)}(?:\s+[^\s]+)?" if consumes_value else rf"(?<!\S){re.escape(flag)}(?!\S)"
        classification_text, count = re.subn(pattern, " ", classification_text, flags=re.IGNORECASE)
        if count:
            detected.append(flag)
    classification_text = re.sub(r"\s+", " ", classification_text).strip()
    if not detected:
        return original, None
    warning = {
        "schema_version": schema_version("chat_bridge_input_warning"),
        "code": "cli_flag_after_separator",
        "message": "Flagi po -- są częścią wiadomości. Przenieś je przed --.",
        "detected_flags": detected,
        "classification_text": classification_text,
        "original_user_text": original,
        "truth_boundary": "Oryginał pozostaje w trace; tylko routing i wykonanie tej tury używają tekstu bez znanych flag CLI.",
    }
    return classification_text, warning


def attach_cli_flag_warning(result: dict[str, Any], warning: dict[str, Any] | None) -> None:
    if warning is None:
        return
    result["chat_bridge_input_warning"] = warning
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    trace["chat_bridge_original_user_text"] = warning["original_user_text"]
    trace["chat_bridge_classification_text"] = warning["classification_text"]
    trace["chat_bridge_input_warning_code"] = warning["code"]
    result["trace"] = trace
BridgeOutputMode = Literal["jsonl", "final_visible_text"]

CHATGPT_HOST_VISIBLE_REPLY_TYPES = (
    "host_visible_reply",
    "chatgpt_host_visible_reply",
    "chatgpt_visible_layer_reply",
)
CHATGPT_HOST_VISIBLE_REPLY_TEXT_FIELDS = (
    "final_text",
    "host_visible_text",
    "visible_text",
    "assistant_text",
    "final_visible_text",
)


@dataclass(slots=True)
class ChatCommandContract:
    command: str
    mode: str
    requires_api_key: bool
    uses_openai_api: bool
    keeps_process_alive: bool
    engine_reused_between_turns: bool
    accepted_input_fields: tuple[str, ...] = ACCEPTED_CHATGPT_INPUT_FIELDS
    accepted_input_shapes: tuple[str, ...] = (
        "plain_text_line",
        "json_object.message",
        "json_object.text",
        "json_object.user_text",
        "json_object.content",
        "json_object.prompt",
        "json_object.messages[].content",
    )
    output_modes: tuple[str, ...] = CHAT_BRIDGE_OUTPUT_MODES
    truth_boundary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def chat_gpt_contract(*, process_lifecycle: str = "one_shot") -> ChatCommandContract:
    process_persistent = process_lifecycle == "jsonl_bridge"
    return ChatCommandContract(
        command="--chat-gpt",
        mode="chatgpt_bridge_without_api_key",
        requires_api_key=False,
        uses_openai_api=False,
        keeps_process_alive=process_persistent,
        engine_reused_between_turns=process_persistent,
        truth_boundary=(
            "--chat-gpt jest jedyną kanoniczną flagą mostu dla aplikacji ChatGPT/copy-paste/JSONL. "
            "Nie wymaga OPENAI_API_KEY i nie wykonuje żądań do OpenAI API. "
            "Użycie z wiadomością po `--` wypisuje final_visible_text dla człowieka; stdin JSONL zachowuje pełny pakiet dla narzędzi. "
            "Legacy aliasy `--chat-gpt-final-only` i `--chat-gpt --final-only` są tylko zgodnością wsteczną i nie zmieniają routingu."
        ),
    )


def chat_open_ai_contract() -> ChatCommandContract:
    return ChatCommandContract(
        command="--chat-open-ai",
        mode="openai_api_model_adapter_bridge",
        requires_api_key=True,
        uses_openai_api=True,
        keeps_process_alive=True,
        engine_reused_between_turns=True,
        truth_boundary=(
            "--chat-open-ai uruchamia ten sam runtime Jaźni, ale językową warstwę model_adapter kieruje przez OpenAI Responses API. "
            "OPENAI_API_KEY jest wymagany. Model jest kanałem języka, nie źródłem tożsamości ani pamięci Jaźni."
        ),
    )


def local_llm_contract() -> ChatCommandContract:
    return ChatCommandContract(
        command="--chat-ollama",
        mode="ollama_native_local_backend",
        requires_api_key=False,
        uses_openai_api=False,
        keeps_process_alive=True,
        engine_reused_between_turns=True,
        truth_boundary=(
            "--chat-ollama używa natywnego lokalnego API Ollamy jako generatora kandydata. "
            "Ollama nie jest tożsamością ani pamięcią; runtime zachowuje routing, walidację, provenance, ledger i final_visible_text."
        ),
    )


def command_contract(command: str, *, process_lifecycle: str | None = None) -> dict[str, Any]:
    if command == "--chat-open-ai":
        return chat_open_ai_contract().to_dict()
    if command in {"--chat-ollama", "--local-llm", "--ollama"}:
        return local_llm_contract().to_dict()
    if command == "--chat-gpt":
        return chat_gpt_contract(process_lifecycle=process_lifecycle or "one_shot").to_dict()
    raise ValueError(f"unknown chat command contract: {command}")


def extract_user_text_from_payload(payload: dict[str, Any]) -> tuple[str, str, str]:
    for candidate in ACCEPTED_CHATGPT_INPUT_FIELDS:
        value = payload.get(candidate)
        if value is not None and str(value).strip():
            return str(value).strip(), "json", candidate

    messages = payload.get("messages")
    if isinstance(messages, list):
        fallback_content = ""
        fallback_field = "messages[].content"
        for item in messages:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if content is None:
                continue
            if isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if isinstance(part, dict):
                        text_part = part.get("text")
                        if text_part is not None:
                            parts.append(str(text_part))
                    elif part is not None:
                        parts.append(str(part))
                content_text = "".join(parts).strip()
            else:
                content_text = str(content).strip()
            if not content_text:
                continue
            fallback_content = content_text
            if str(item.get("role") or "").lower() == "user":
                return content_text, "json_chat_messages", "messages[user].content"
        if fallback_content:
            return fallback_content, "json_chat_messages", fallback_field

    return "", "json", "<missing>"



def apply_chatgpt_cli_settings(config: JaznConfig) -> JaznConfig:
    """Select the truthful ChatGPT host adapter for the --chat-gpt bridge."""
    config.model_adapter = "chatgpt_runtime_adapter"
    if not os.environ.get("JAZN_MODEL_NAME"):
        config.model_name = os.environ.get("JAZN_CHATGPT_MODEL_NAME", "chatgpt_host_model").strip() or "chatgpt_host_model"
    return config


def apply_chat_cli_settings(
    config: JaznConfig,
    *,
    infer_host_environment: bool = True,
    probe_local: bool = True,
) -> JaznConfig:
    """Resolve the universal ``--chat`` language route for this environment."""
    from latka_jazn.core.llm_route_resolver import apply_llm_route_to_config, build_llm_route_status

    route_status = build_llm_route_status(
        config,
        command="--chat",
        infer_host_environment=infer_host_environment,
        probe_local=probe_local,
    )
    apply_llm_route_to_config(config, route_status)
    if not os.environ.get("JAZN_TERMINAL_MODEL_NAME"):
        config.terminal_model_name = "terminal_visible_layer"
    return config


def apply_openai_cli_settings(
    config: JaznConfig,
    *,
    model: str | None = None,
    api_base: str | None = None,
    timeout_seconds: float | None = None,
    max_output_tokens: int | None = None,
) -> JaznConfig:
    config.model_adapter = "openai_responses_adapter"
    if model:
        config.model_name = model
    if api_base:
        config.model_api_base = api_base.rstrip("/")
    if timeout_seconds is not None:
        config.model_timeout_seconds = float(timeout_seconds)
    if max_output_tokens is not None:
        config.model_max_output_tokens = int(max_output_tokens)
    return config


def apply_ollama_cli_settings(
    config: JaznConfig,
    *,
    model: str | None = None,
    api_base: str | None = None,
    timeout_seconds: float | None = None,
    max_output_tokens: int | None = None,
    provider: str | None = None,
) -> JaznConfig:
    """Apply explicit Ollama CLI values without performing network I/O.

    Model discovery is intentionally separate in ``resolve_ollama_cli_settings``
    so status builders and unit tests do not unexpectedly contact localhost.
    """
    config.model_adapter = "ollama"
    normalized_model = str(model or "").strip()
    if normalized_model:
        config.local_model_name = normalized_model
        os.environ["JAZN_OLLAMA_MODEL"] = normalized_model
        os.environ["JAZN_LOCAL_LLM_MODEL"] = normalized_model
    if api_base:
        normalized_api_base = str(api_base).strip().rstrip("/")
        config.local_model_api_base = normalized_api_base
        os.environ["JAZN_OLLAMA_BASE_URL"] = normalized_api_base
        os.environ["JAZN_LOCAL_LLM_BASE_URL"] = normalized_api_base
    if timeout_seconds is not None:
        config.model_timeout_seconds = float(timeout_seconds)
    if max_output_tokens is not None:
        config.model_max_output_tokens = int(max_output_tokens)
    return config


def resolve_ollama_cli_settings(
    config: JaznConfig,
    *,
    model: str | None = None,
    api_base: str | None = None,
    timeout_seconds: float | None = None,
    max_output_tokens: int | None = None,
) -> tuple[JaznConfig, dict[str, Any]]:
    """Apply CLI settings and verify/select a usable Ollama model.

    An explicitly configured model is verified against ``GET /api/tags``.  When
    no model was configured, exactly one running or installed model may be
    selected by the existing canonical ``probe_ollama`` policy.  Multiple
    models remain ambiguous and require ``--ollama-model`` instead of an
    arbitrary choice.
    """
    apply_ollama_cli_settings(
        config,
        model=model,
        api_base=api_base,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )

    from latka_jazn.core.llm_route_resolver import probe_ollama

    probe_timeout = min(max(float(getattr(config, "model_timeout_seconds", 45.0)), 0.1), 2.0)
    probe = probe_ollama(config, os.environ, timeout_seconds=probe_timeout)
    selected_model = str(probe.get("model") or "").strip()
    if selected_model:
        config.local_model_name = selected_model
    return config, probe


def _nonempty_text_from_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> tuple[str, str]:
    for field in fields:
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value).strip(), field
    return "", "<missing>"


def is_chatgpt_host_visible_reply_payload(payload: dict[str, Any]) -> bool:
    """Return True when a JSONL line is the host->runtime reply phase.

    This keeps --chat-gpt a single public bridge while making the truth boundary
    explicit: local Python emits the runtime packet; the surrounding ChatGPT host
    may send back the visible wording in a second JSONL line for persistence.
    """
    payload_type = str(payload.get("type") or payload.get("kind") or "").strip().lower()
    phase = str(payload.get("phase") or payload.get("chatgpt_bridge_phase") or "").strip().lower()
    if payload_type in CHATGPT_HOST_VISIBLE_REPLY_TYPES:
        return True
    return phase in {"host_visible_reply", "chatgpt_host_visible_reply", "host_visible_reply_record"}


def extract_chatgpt_host_visible_reply_payload(payload: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Extract and validate a ChatGPT-host visible reply JSONL payload."""
    trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
    final_text, final_text_field = _nonempty_text_from_fields(payload, CHATGPT_HOST_VISIBLE_REPLY_TEXT_FIELDS)
    turn_id = str(payload.get("turn_id") or trace.get("turn_id") or "").strip()
    trace_id = str(payload.get("trace_id") or trace.get("trace_id") or "").strip()
    timestamp_header = str(payload.get("timestamp_header") or trace.get("timestamp_header") or "").strip()
    final_text_sha256 = str(payload.get("final_text_sha256") or "").strip().lower()
    state_emoticon = str(payload.get("state_emoticon") or payload.get("emoticon") or "🌿").strip() or "🌿"
    missing: list[str] = []
    if not final_text:
        missing.append("final_text|host_visible_text|visible_text|assistant_text")
    if not turn_id:
        missing.append("turn_id")
    if not trace_id:
        missing.append("trace_id")
    if not timestamp_header:
        missing.append("timestamp_header")
    if not re.fullmatch(r"[0-9a-f]{64}", final_text_sha256):
        missing.append("final_text_sha256")
    return {
        "final_text": final_text,
        "final_text_field": final_text_field,
        "turn_id": turn_id,
        "trace_id": trace_id,
        "timestamp_header": timestamp_header,
        "state_emoticon": state_emoticon,
        "final_text_sha256": final_text_sha256,
    }, missing


def chatgpt_result_has_accepted_runtime_final(result: dict[str, Any]) -> bool:
    """Return true only for an accepted handler final that needs no host speech."""
    decision = result.get("conversation_decision") if isinstance(result.get("conversation_decision"), dict) else {}
    runtime_turn = result.get("runtime_turn_contract") if isinstance(result.get("runtime_turn_contract"), dict) else {}
    final_contract = result.get("final_response_contract") if isinstance(result.get("final_response_contract"), dict) else {}
    validation = runtime_turn.get("validation") if isinstance(runtime_turn.get("validation"), dict) else {}
    handler_name = str(decision.get("handler_name") or runtime_turn.get("handler_name") or "")
    return bool(
        validation.get("accepted") is True
        and runtime_turn.get("requires_host_model") is False
        and final_contract.get("requires_host_model") is False
        and handler_name
        and handler_name != "RuntimeTurnTruthGate"
        and extract_final_visible_text_from_result(result)
    )


def chatgpt_result_requires_host_visible_reply(result: dict[str, Any]) -> bool:
    """Detect when the runtime produced a truthful request for host speech."""
    decision = result.get("conversation_decision") if isinstance(result.get("conversation_decision"), dict) else {}
    runtime_turn = result.get("runtime_turn_contract") if isinstance(result.get("runtime_turn_contract"), dict) else {}
    final_contract = result.get("final_response_contract") if isinstance(result.get("final_response_contract"), dict) else {}
    validation = runtime_turn.get("validation") if isinstance(runtime_turn.get("validation"), dict) else {}
    if chatgpt_result_has_accepted_runtime_final(result):
        # The final/runtime contracts are authoritative at the bridge boundary.
        # Reconcile stale pre-final flags left in the decision/top-level payload.
        decision["requires_host_model"] = False
        result["requires_host_model"] = False
        return False
    return bool(
        runtime_turn.get("requires_host_model")
        or decision.get("requires_host_model")
        or final_contract.get("requires_host_model")
        or validation.get("requires_host_model")
        or str(runtime_turn.get("fallback_classification") or final_contract.get("fallback_classification") or "") == "cannot_answer_directly"
    )


def build_chatgpt_host_bridge_turn_contract(
    result: dict[str, Any],
    *,
    user_text: str,
    chat_bridge_meta: dict[str, Any],
) -> dict[str, Any]:
    """Attach a machine-readable ChatGPT-host handshake to --chat-gpt output."""
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    decision = result.get("conversation_decision") if isinstance(result.get("conversation_decision"), dict) else {}
    runtime_turn = result.get("runtime_turn_contract") if isinstance(result.get("runtime_turn_contract"), dict) else {}
    final_contract = result.get("final_response_contract") if isinstance(result.get("final_response_contract"), dict) else {}
    requires_host = chatgpt_result_requires_host_visible_reply(result)
    detected_intent = str(
        decision.get("detected_user_intent")
        or decision.get("intent")
        or runtime_turn.get("detected_user_intent")
        or runtime_turn.get("intent")
        or ""
    )
    runtime_route = str(decision.get("route") or runtime_turn.get("runtime_route") or "")
    ownership = build_runtime_ownership_contract(
        detected_intent=detected_intent,
        route=runtime_route,
    )
    host_policy = ownership.get("host_visible_generation_contract") or {}
    host_policy_rules = [str(item) for item in host_policy.get("rules", []) if str(item).strip()]
    turn_id = str(trace.get("turn_id") or runtime_turn.get("turn_id") or final_contract.get("turn_id") or "")
    trace_id = str(trace.get("trace_id") or runtime_turn.get("trace_id") or final_contract.get("trace_id") or "")
    timestamp_header = str(trace.get("timestamp_header") or runtime_turn.get("timestamp_header") or final_contract.get("timestamp_header") or "")
    return {
        "schema_version": schema_version("chatgpt_host_bridge_turn_contract"),
        "phase": "host_visible_generation_requested" if requires_host else "runtime_final_available",
        "host_must_generate_visible_reply": requires_host,
        "status": "requires_host_chatgpt_visible_response" if requires_host else "runtime_final_visible_text_available",
        "command": "--chat-gpt",
        "turn_id": turn_id,
        "trace_id": trace_id,
        "timestamp_header": timestamp_header,
        "timestamp_required": bool(timestamp_header),
        "required_visible_prefix": timestamp_header,
        "host_reply_finalization_required": requires_host,
        "user_text_sha256": hashlib.sha256((user_text or "").encode("utf-8")).hexdigest(),
        "runtime_summary": {
            "route": runtime_route,
            "detected_intent": detected_intent,
            "handler_name": decision.get("handler_name") or runtime_turn.get("handler_name"),
            "fallback_classification": runtime_turn.get("fallback_classification") or final_contract.get("fallback_classification"),
            "runtime_answer_quality": runtime_turn.get("runtime_answer_quality") or final_contract.get("runtime_answer_quality"),
            "response_generation_mode": runtime_turn.get("response_generation_mode") or decision.get("response_generation_mode"),
            "source_origin_detail": runtime_turn.get("source_origin_detail") or decision.get("source_origin_detail"),
            "can_generate_model_guided_speech": True if requires_host else runtime_turn.get("can_generate_model_guided_speech"),
            "can_generate_model_guided_speech_locally": False if requires_host else runtime_turn.get("can_generate_model_guided_speech"),
            "can_complete_model_guided_speech_via_host": bool(requires_host),
            "generation_executor": "chatgpt_host" if requires_host else "runtime",
            "requires_host_model": requires_host,
        },
        "runtime_ownership_contract": ownership,
        "host_generation_policy": host_policy,
        "host_reply_jsonl_shape": {
            "type": "host_visible_reply",
            "turn_id": turn_id,
            "trace_id": trace_id,
            "timestamp_header": timestamp_header,
            "final_text": "<widoczna odpowiedź ułożona zgodnie z runtime_ownership_contract>",
            "final_text_sha256": "<sha256 dokładnych bajtów UTF-8 pola final_text>",
        },
        "accepted_host_reply_text_fields": list(CHATGPT_HOST_VISIBLE_REPLY_TEXT_FIELDS),
        "chat_bridge": chat_bridge_meta,
        "truth_boundary": (
            "--chat-gpt nie wywołuje lokalnie modelu ChatGPT. Runtime Jaźni zwraca kopertę i, gdy potrzebny jest język hosta, "
            "oznacza fazę host_visible_generation_requested. Host ChatGPT może odesłać drugą linię JSONL type=host_visible_reply, "
            "która zostanie zapisana przez runtime jako external_final_visible_reply bez udawania lokalnego modelu."
        ),
        "host_generation_rules": [
            *host_policy_rules,
            "Nie twierdź, że lokalny Python wywołał ChatGPT jako funkcję.",
            "Widoczna odpowiedź MUSI zaczynać się dokładnie od required_visible_prefix/timestamp_header; faza finalizacji odrzuci obcy timestamp i naprawi brakujący.",
            "Jeżeli runtime_truth_gate blokuje zwykłą odpowiedź, pokaż krótką diagnozę hosta zamiast imitować wypowiedź Łatki.",
        ],
    }


def persist_chatgpt_host_visible_reply(
    *,
    config: JaznConfig,
    payload: dict[str, Any],
    chat_bridge_meta: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Persist a second-phase ChatGPT host reply in the Jaźń ledger."""
    reply, missing = extract_chatgpt_host_visible_reply_payload(payload)
    if missing:
        return None, missing
    finalization = finalize_host_visible_text(
        required_timestamp_header=reply["timestamp_header"],
        turn_id=reply["turn_id"],
        trace_id=reply["trace_id"],
        text=reply["final_text"],
        supplied_turn_id=reply["turn_id"],
        supplied_trace_id=reply["trace_id"],
        supplied_text_sha256=reply["final_text_sha256"],
    )
    if not finalization.accepted:
        return None, [f"finalization:{item.code}" for item in finalization.violations]
    reply["final_text"] = finalization.final_visible_text

    from latka_jazn.core.engine import JaznEngine

    engine = JaznEngine(config)
    try:
        capture = engine.persist_final_visible_reply(
            turn_id=reply["turn_id"],
            trace_id=reply["trace_id"],
            timestamp_header=reply["timestamp_header"],
            final_text=reply["final_text"],
            state_emoticon=reply["state_emoticon"],
            source="chatgpt_visible_layer_jsonl",
            client_context={
                "client": "chatgpt_visible_layer_jsonl",
                "lifecycle": "chatgpt_host_visible_reply_record",
                "chat_bridge": chat_bridge_meta,
                "final_text_field": reply["final_text_field"],
            },
        )
    finally:
        engine.shutdown()
    result = {
        "schema_version": schema_version("chatgpt_host_visible_reply_recorded"),
        "ok": True,
        "chat_bridge": chat_bridge_meta,
        "chatgpt_bridge": chat_bridge_meta,
        "chat_command_contract": contract,
        "chatgpt_host_bridge": {
            "schema_version": schema_version("chatgpt_host_visible_reply_recorded"),
            "phase": "host_visible_reply_recorded",
            "status": "host_visible_reply_finalized",
            "host_must_generate_visible_reply": False,
            "turn_id": reply["turn_id"],
            "trace_id": reply["trace_id"],
            "timestamp_header": reply["timestamp_header"],
            "timestamp_required": True,
            "timestamp_enforced": True,
            "final_text_field": reply["final_text_field"],
            "can_generate_model_guided_speech": True,
            "can_generate_model_guided_speech_locally": False,
            "can_complete_model_guided_speech_via_host": True,
            "generation_executor": "chatgpt_host",
            "truth_boundary": "Widoczna odpowiedź powstała w hoście ChatGPT, przeszła obowiązkową finalizację timestampu i została zapisana w runtime; lokalny Python nie udawał lokalnej generacji modelu.",
        },
        "host_must_generate_visible_reply": False,
        "can_generate_model_guided_speech": True,
        "final_visible_text": capture.get("final_visible_text"),
        "host_visible_finalization": finalization.to_dict(),
        "host_visible_reply_capture": capture,
    }
    return result, []


def extract_final_visible_text_from_result(payload: dict[str, Any]) -> str:
    """Return the visible Łatka reply from a chat bridge payload.

    The JSONL protocol remains the default source of truth. This helper is only
    for the human-readable --chat-gpt rendering mode.
    """
    final: Any = payload.get("final_visible_text")
    final_contract = payload.get("final_response_contract")
    if final is None and isinstance(final_contract, dict):
        final = final_contract.get("final_visible_text")
    provenance = payload.get("runtime_provenance")
    if final is None and isinstance(provenance, dict):
        final = provenance.get("visible_answer_text")
    if final is None:
        final = payload.get("exact_runtime_text")
    if final is None and payload.get("error"):
        error_code = str(payload.get("error_code") or "chat_bridge_error")
        final = f"[{error_code}] {payload.get('error')}"
    return str(final or "").strip()


def write_chat_bridge_payload(stdout: TextIO, payload: dict[str, Any], *, output_mode: BridgeOutputMode = "jsonl") -> None:
    host_bridge = payload.get("chatgpt_host_bridge") if isinstance(payload.get("chatgpt_host_bridge"), dict) else {}
    host_generation_required = bool(host_bridge.get("host_must_generate_visible_reply"))
    if output_mode == "final_visible_text" and not host_generation_required:
        stdout.write(extract_final_visible_text_from_result(payload) + "\n")
    else:
        if output_mode == "final_visible_text" and host_generation_required:
            payload = dict(payload)
            payload["chat_bridge_output"] = {
                "requested_mode": "final_visible_text",
                "effective_mode": "jsonl_host_bridge_envelope",
                "reason": "host_visible_generation_requested_cannot_be_hidden_by_final_only",
            }
        stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    stdout.flush()


def run_jsonl_chat_bridge(
    *,
    config: JaznConfig,
    session_id: str | None,
    no_carryover: bool,
    command: str,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    require_openai_api_key: bool = False,
    output_mode: BridgeOutputMode = "jsonl",
    one_shot_degraded: bool = False,
) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if output_mode not in CHAT_BRIDGE_OUTPUT_MODES:
        raise ValueError(f"unsupported chat bridge output_mode: {output_mode}")
    if command == "--chat-gpt":
        apply_chatgpt_cli_settings(config)
    elif command == "--chat-open-ai":
        apply_openai_cli_settings(config)
    elif command in {"--chat-ollama", "--local-llm", "--ollama"}:
        apply_ollama_cli_settings(config)
    contract = command_contract(
        command,
        process_lifecycle="one_shot" if output_mode == "final_visible_text" else "jsonl_bridge",
    )
    protocol_version = CHATGPT_BRIDGE_PROTOCOL
    default_client = "chatgpt_bridge"
    default_lifecycle = "chatgpt_bridge_jsonl"
    if command == "--chat-open-ai":
        protocol_version = CHAT_OPENAI_PROTOCOL
        default_client = "openai_api_bridge"
        default_lifecycle = "openai_api_jsonl"
    elif command in {"--chat-ollama", "--local-llm", "--ollama"}:
        protocol_version = OLLAMA_PROTOCOL
        default_client = "ollama_local_bridge"
        default_lifecycle = "ollama_jsonl_contract"

    if require_openai_api_key and not os.environ.get("OPENAI_API_KEY"):
        payload = {
            "schema_version": schema_version("chat_command_startup_error"),
            "ok": False,
            "error_code": "missing_openai_api_key",
            "error": "--chat-open-ai wymaga zmiennej środowiskowej OPENAI_API_KEY. Nie uruchamiam modelu i nie udaję połączenia z OpenAI API.",
            "chat_command_contract": contract,
        }
        write_chat_bridge_payload(stdout, payload, output_mode=output_mode)
        return 3

    sessions: dict[str, RuntimeSessionWorker] = {}
    generated_session: RuntimeSessionWorker | None = None

    def bridge_meta(
        *,
        client: str = default_client,
        input_kind: str | None = None,
        input_field: str | None = None,
        line_index: int | None = None,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "protocol_version": protocol_version,
            "accepted_input_fields": list(ACCEPTED_CHATGPT_INPUT_FIELDS),
            "accepted_input_shapes": list(contract["accepted_input_shapes"]),
            "preferred_input_field": "message",
            "client": client,
            "lifecycle": default_lifecycle,
            "mode": contract["mode"],
            "command": command,
            "requires_api_key": contract["requires_api_key"],
            "uses_openai_api": contract["uses_openai_api"],
            "canonical_command": "--chat-gpt" if command == "--chat-gpt" else command,
            "legacy_aliases": ["--chat-gpt-final-only", "--chat-gpt --final-only"] if command == "--chat-gpt" else [],
            "canonicalization_policy": (
                "Use --chat-gpt as the single public ChatGPT bridge; aliases are backwards-compatible only."
                if command == "--chat-gpt" else "canonical command"
            ),
            "deprecated_flag_removed": "--chat-jsonl",
        }
        if input_kind is not None:
            meta["input_kind"] = input_kind
        if input_field is not None:
            meta["input_field"] = input_field
        if line_index is not None:
            meta["line_index"] = line_index
        return meta

    def error_payload(
        *,
        error_code: str,
        error: str,
        client: str = default_client,
        input_kind: str | None = None,
        input_field: str | None = None,
        line_index: int | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": schema_version("chat_bridge_error"),
            "chat_bridge": bridge_meta(client=client, input_kind=input_kind, input_field=input_field, line_index=line_index),
            "chat_command_contract": contract,
            "ok": False,
            "error_code": error_code,
            "error": error,
        }

    def get_session(payload_session_id: str | None, *, client: str) -> tuple[RuntimeSessionWorker, str]:
        nonlocal generated_session
        if payload_session_id:
            if payload_session_id not in sessions:
                sessions[payload_session_id] = RuntimeSessionWorker(session_factory=JaznRuntimeSession, config=config, session_id=payload_session_id, no_carryover=no_carryover, source_client=client, command=command, timeout_seconds=runtime_turn_timeout_seconds(config))
            return sessions[payload_session_id], "payload"
        if session_id:
            if session_id not in sessions:
                sessions[session_id] = RuntimeSessionWorker(session_factory=JaznRuntimeSession, config=config, session_id=session_id, no_carryover=no_carryover, source_client=client, command=command, timeout_seconds=runtime_turn_timeout_seconds(config))
            return sessions[session_id], "cli_arg"
        if generated_session is None:
            generated_session = RuntimeSessionWorker(session_factory=JaznRuntimeSession, config=config, session_id=None, no_carryover=no_carryover, source_client=client, command=command, timeout_seconds=runtime_turn_timeout_seconds(config))
            sessions[generated_session.state.session_id] = generated_session
        return generated_session, "generated"

    try:
        for line_index, line in enumerate(stdin, 1):
            line = line.strip()
            if not line:
                continue
            if line in {"/exit", "exit"}:
                break

            input_kind = "plain_text"
            input_field = "plain_text"
            payload_session_id = None
            client = default_client

            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                if line[:1] in {"{", "["}:
                    write_chat_bridge_payload(stdout, error_payload(
                        error_code="malformed_json",
                        error=f"Niepoprawna linia JSONL: {exc.msg}",
                        input_kind="malformed_json",
                        input_field="<parse_error>",
                        line_index=line_index,
                    ), output_mode=output_mode)
                    continue
                user_text = line
            else:
                input_kind = "json"
                if not isinstance(payload, dict):
                    write_chat_bridge_payload(stdout, error_payload(
                        error_code="invalid_jsonl_payload",
                        error="Każda linia mostu chat musi być obiektem JSON albo zwykłym tekstem.",
                        input_kind="json_non_object",
                        input_field="<non_object>",
                        line_index=line_index,
                    ), output_mode=output_mode)
                    continue
                client = str(payload.get("client") or default_client)
                if command == "--chat-gpt" and is_chatgpt_host_visible_reply_payload(payload):
                    meta = bridge_meta(client=client, input_kind="json_host_visible_reply", input_field="type", line_index=line_index)
                    persisted, missing = persist_chatgpt_host_visible_reply(
                        config=config,
                        payload=payload,
                        chat_bridge_meta=meta,
                        contract=contract,
                    )
                    if missing:
                        write_chat_bridge_payload(stdout, error_payload(
                            error_code="invalid_host_visible_reply",
                            error="Brakuje pól dla host_visible_reply: " + ", ".join(missing),
                            client=client,
                            input_kind="json_host_visible_reply",
                            input_field="type",
                            line_index=line_index,
                        ), output_mode=output_mode)
                    else:
                        write_chat_bridge_payload(stdout, persisted or {}, output_mode=output_mode)
                    continue
                payload_session_id = str(payload.get("session_id") or "").strip() or None
                user_text, input_kind, input_field = extract_user_text_from_payload(payload)

            if not user_text.strip():
                write_chat_bridge_payload(stdout, error_payload(
                    error_code="empty_message",
                    error="Pusta wiadomość nie została przekazana do runtime Jaźni.",
                    client=client,
                    input_kind=input_kind,
                    input_field=input_field,
                    line_index=line_index,
                ), output_mode=output_mode)
                continue

            classification_text, input_warning = guard_cli_flags_in_user_text(user_text)
            if not classification_text:
                classification_text = user_text

            try:
                session, session_id_source = get_session(payload_session_id, client=client)
                result = session.process_user_text(
                    classification_text,
                    client=client,
                    lifecycle=default_lifecycle,
                    session_id_source=session_id_source,
                    process_reused=True,
                )
            except RuntimeTurnTimeoutError as exc:
                write_chat_bridge_payload(stdout, error_payload(
                    error_code="runtime_turn_timeout",
                    error=(
                        f"Runtime Jaźni nie zakończył etapu {getattr(exc, 'phase', 'runtime_turn')} w limicie {exc.timeout_seconds:.3g}s. "
                        "Zwracam kontrolowany błąd zamiast wiszącego mostu; sprawdź start sesji, timestamp/memory/engine.process_turn."
                    ),
                    client=client,
                    input_kind=input_kind,
                    input_field=input_field,
                    line_index=line_index,
                ), output_mode=output_mode)
                continue
            except Exception as exc:
                write_chat_bridge_payload(stdout, error_payload(
                    error_code="runtime_turn_failed",
                    error=f"Runtime Jaźni przerwał turę: {type(exc).__name__}: {exc}",
                    client=client,
                    input_kind=input_kind,
                    input_field=input_field,
                    line_index=line_index,
                ), output_mode=output_mode)
                continue
            attach_cli_flag_warning(result, input_warning)
            if one_shot_degraded:
                result["one_shot_degraded"] = True
                result["process_lifecycle"] = "one_shot"
                result["daemon_confirmed"] = False
                result["background_claim_allowed"] = False
                final_text = str(result.get("final_visible_text") or "")
                if final_text and "one_shot_degraded" not in final_text.lower():
                    result["final_visible_text"] = (
                        "[one_shot_degraded] daemon nie został potwierdzony; proces jest jednorazowy.\n"
                        + final_text
                    )
            result["chat_bridge"] = bridge_meta(client=client, input_kind=input_kind, input_field=input_field, line_index=line_index)
            # Zachowujemy stary klucz dla zgodności z narzędziami, które już czytają --chat-gpt.
            if command == "--chat-gpt":
                result["chatgpt_bridge"] = result["chat_bridge"]
                result["chatgpt_host_bridge"] = build_chatgpt_host_bridge_turn_contract(
                    result,
                    user_text=user_text,
                    chat_bridge_meta=result["chat_bridge"],
                )
            result["chat_command_contract"] = contract
            # v14.8.5.014: most nie może nadpisać blokady runtime truth gate przez ok=True.
            result["ok"] = bool(result.get("ok", True))
            write_chat_bridge_payload(stdout, result, output_mode=output_mode)
    finally:
        for session in sessions.values():
            session.close()
    return 0
