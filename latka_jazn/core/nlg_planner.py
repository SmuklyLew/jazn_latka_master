from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from latka_jazn.core.nlg_plan import NlgPlan, SCHEMA_VERSION, default_truth_boundary

ORDINARY_INTENTS = {
    "ordinary_conversation",
    "standalone_greeting",
    "casual_greeting",
    "short_free_dialogue",
    "ordinary_natural_presence_dialogue",
    "atmospheric_greeting",
    "expressive_reaction",
    "casual_feedback",
    "negative_feedback_current_turn",
}
HEALTH_INTENTS = {
    "runtime_activation_status_request",
    "runtime_health_check",
    "runtime_status_question",
    "startup_status_request",
    "system_health_check",
    "capability_status_question",
    "model_adapter_status_request",
}
FULL_DIAGNOSTIC_INTENTS = {
    "module_inventory_request",
    "system_repair_plan_request",
    "file_operation_request",
}
MEMORY_INTENTS = {
    "memory_recall_request",
    "self_memory_recall_request",
    "identity_memory_existence_compound_question",
    "identity_memory_question",
    "question_about_memory",
    "question_about_time_memory_and_experience",
}
EXACT_RUNTIME_INTENTS = {
    "runtime_exact_quote_request",
}
EXTERNAL_SOURCE_INTENTS = {
    "external_research_request",
    "dictionary_network_lookup_request",
    "internet_access_question",
}
CREATIVE_OR_DOCUMENT_INTENTS = {
    "creative_text_formatting",
    "creative_writing_request",
    "document_creation_request",
}
NULL_OR_DISABLED_MODEL_STATUSES = {
    "available_as_truthful_fallback",
    "requires_external_model_execution",
    "not_configured",
    "unknown",
    "none",
    "",
}


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        maybe = value.to_dict()
        return maybe if isinstance(maybe, dict) else {}
    if is_dataclass(value):
        return asdict(value)
    return {}


def _nested_dict(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = source
    for key in keys:
        current = _as_dict(current).get(key)
    return _as_dict(current)


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "tak", "required", "needed"}
    return bool(value)


def _text_contains_any(text: str, markers: set[str]) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in markers)


def _semantic_frame(cognitive_frame: dict[str, Any]) -> dict[str, Any]:
    frame = _as_dict(cognitive_frame)
    direct = _nested_dict(frame, "semantic_frame")
    if direct:
        return direct
    return _nested_dict(frame, "polish_reasoning", "semantic_frame")


def _reply_policy_from_frame(cognitive_frame: dict[str, Any]) -> dict[str, Any]:
    frame = _as_dict(cognitive_frame)
    direct = _nested_dict(frame, "reply_policy")
    if direct:
        return direct
    return _nested_dict(frame, "polish_reasoning", "reply_policy")


def _combined_policy(cognitive_frame: dict[str, Any], response_policy: dict[str, Any] | None) -> dict[str, Any]:
    out = _reply_policy_from_frame(cognitive_frame)
    out.update(_as_dict(response_policy))
    return out


def infer_answer_kind(detected_intent: str, response_policy: dict[str, Any] | None) -> str:
    policy = _as_dict(response_policy)
    intent = str(detected_intent or "ordinary_conversation")
    explicit = str(policy.get("answer_kind") or "").strip()
    if explicit:
        return explicit
    if intent in EXACT_RUNTIME_INTENTS or _boolish(policy.get("exact_runtime_required")):
        return "exact_runtime_quote"
    if intent in EXTERNAL_SOURCE_INTENTS or _boolish(policy.get("allow_online_lookup")):
        return "external_research_required"
    if intent in CREATIVE_OR_DOCUMENT_INTENTS:
        return "creative_or_document_answer"
    if intent in FULL_DIAGNOSTIC_INTENTS:
        return "diagnostic_full"
    if intent in HEALTH_INTENTS or _boolish(policy.get("requires_diagnostic")):
        return "diagnostic_brief"
    if intent in MEMORY_INTENTS or _boolish(policy.get("source_grounding_required")):
        return "memory_grounded_answer"
    return "natural_dialogue"


def infer_memory_policy(cognitive_frame: dict[str, Any], response_policy: dict[str, Any] | None) -> str:
    frame = _as_dict(cognitive_frame)
    semantic = _semantic_frame(frame)
    policy = _combined_policy(frame, response_policy)
    explicit = str(policy.get("memory_policy") or "").strip()
    if explicit:
        return explicit
    if _boolish(policy.get("exact_runtime_required")):
        return "forbidden"
    memory_gate = str(frame.get("memory_gate") or frame.get("memory_use_gate") or "").strip().lower()
    if memory_gate in {"not_needed", "not-needed", "not needed"}:
        return "not_needed"
    if _boolish(semantic.get("requires_memory")) or _boolish(policy.get("source_grounding_required")):
        return "required_grounded_payload"
    memory_contract = _as_dict(frame.get("memory_recall_contract"))
    if memory_contract.get("required") is True:
        return "required_grounded_payload"
    return "not_needed"


def infer_model_policy(
    detected_intent: str,
    response_policy: dict[str, Any] | None,
    model_adapter_status: dict[str, Any] | None = None,
) -> str:
    policy = _as_dict(response_policy)
    intent = str(detected_intent or "ordinary_conversation")
    if intent in EXACT_RUNTIME_INTENTS or _boolish(policy.get("exact_runtime_required")):
        return "forbidden_exact_runtime_required"
    if intent in EXTERNAL_SOURCE_INTENTS:
        return "forbidden_external_source_required"
    if policy.get("llm_allowed") is False:
        return "disabled_null_adapter"
    status = str(_as_dict(model_adapter_status).get("status") or "").strip()
    if status in NULL_OR_DISABLED_MODEL_STATUSES:
        return "disabled_null_adapter"
    if status == "configured":
        return "allowed"
    return "allowed_if_configured"


def infer_tone(user_text: str, cognitive_frame: dict[str, Any] | None, detected_intent: str) -> list[str]:
    frame = _as_dict(cognitive_frame)
    semantic = _semantic_frame(frame)
    tone = [str(x) for x in semantic.get("tone") or [] if str(x).strip()]
    intent = str(detected_intent or "ordinary_conversation")
    if intent in ORDINARY_INTENTS:
        tone.extend(["calm", "present", "conversational"])
    if intent in HEALTH_INTENTS or intent in FULL_DIAGNOSTIC_INTENTS:
        tone.extend(["precise", "brief", "technical"])
    if intent in MEMORY_INTENTS or _boolish(semantic.get("requires_memory")):
        tone.extend(["careful", "grounded"])
    if _text_contains_any(user_text, {"dobranoc", "spokojnej nocy", "śpij", "spij"}):
        tone.extend(["gentle", "closing_softly"])
    if not tone:
        tone.extend(["calm", "conversational"])
    return _dedupe(tone)


def _dedupe(values: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _infer_source_policy(answer_kind: str, memory_policy: str, detected_intent: str) -> str:
    if answer_kind == "exact_runtime_quote":
        return "exact_runtime_only"
    if answer_kind == "external_research_required" or detected_intent in EXTERNAL_SOURCE_INTENTS:
        return "requires_external_web"
    if memory_policy == "required_grounded_payload":
        return "runtime_plus_memory"
    return "runtime_only"


def _required_components(answer_kind: str, memory_policy: str, source_policy: str) -> list[str]:
    components = [
        "timestamp_runtime_side",
        "current_turn_alignment",
        "truth_boundary_respected",
    ]
    if answer_kind == "natural_dialogue":
        components.append("ordinary_language_not_report")
    if answer_kind.startswith("diagnostic"):
        components.extend(["active_root_or_runtime_status", "brief_status_fields"])
    if memory_policy == "required_grounded_payload":
        components.extend(["memory_item_excerpt", "memory_item_source", "memory_confidence_or_limit"])
    if source_policy == "requires_external_web":
        components.append("external_source_separation")
    if answer_kind == "exact_runtime_quote":
        components.append("exact_runtime_text_no_paraphrase")
    return _dedupe(components)


def _forbidden_components(answer_kind: str, memory_policy: str) -> list[str]:
    components = [
        "private_chain_of_thought",
        "fake_memory_without_grounding",
        "biological_consciousness_claim",
        "raw_sqlite_or_full_archive_in_model_context",
        "timestamp_generated_by_model",
        "old_topic_stale_route",
    ]
    if answer_kind == "natural_dialogue":
        components.extend(["technical_report_for_ordinary_dialogue", "random_memory_injection", "memory_denial_when_memory_not_needed"])
    if memory_policy != "required_grounded_payload":
        components.append("claiming_memory_without_payload")
    if answer_kind == "exact_runtime_quote":
        components.append("model_paraphrase_of_exact_runtime_text")
    return _dedupe(components)


def _style_constraints(answer_kind: str) -> list[str]:
    constraints = ["polish_language", "runtime_adds_timestamp", "do_not_expose_private_chain_of_thought"]
    if answer_kind == "natural_dialogue":
        constraints.extend(["natural_presence", "no_unrequested_technical_report"])
    if answer_kind.startswith("diagnostic"):
        constraints.extend(["concise_diagnostic", "facts_before_style"])
    return _dedupe(constraints)


def _max_length_hint(answer_kind: str) -> str:
    if answer_kind == "natural_dialogue":
        return "short_to_medium"
    if answer_kind == "diagnostic_brief":
        return "short"
    if answer_kind == "diagnostic_full":
        return "long_if_requested"
    if answer_kind == "memory_grounded_answer":
        return "medium_with_sources"
    return "medium"


def _truth_boundary(cognitive_frame: dict[str, Any], response_policy: dict[str, Any] | None) -> str:
    frame = _as_dict(cognitive_frame)
    policy = _combined_policy(frame, response_policy)
    for value in (
        policy.get("truth_boundary"),
        policy.get("truth_boundary_note"),
        _as_dict(frame.get("truth_boundary")).get("truth_boundary"),
        _as_dict(frame.get("truth_boundary_check")).get("truth_boundary"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return default_truth_boundary()


def build_nlg_plan(
    *,
    user_text: str,
    cognitive_frame: dict[str, Any] | None,
    response_policy: dict[str, Any] | None,
    route: str,
    detected_intent: str,
    model_adapter_status: dict[str, Any] | None = None,
) -> NlgPlan:
    frame = _as_dict(cognitive_frame)
    semantic = _semantic_frame(frame)
    combined_policy = _combined_policy(frame, response_policy)
    intent = str(detected_intent or semantic.get("primary_intent") or "ordinary_conversation")
    speech_act = str(semantic.get("speech_act") or "statement")
    memory_policy = infer_memory_policy(frame, combined_policy)
    answer_kind = infer_answer_kind(intent, combined_policy)
    if memory_policy == "required_grounded_payload" and answer_kind == "natural_dialogue":
        answer_kind = "memory_grounded_answer"
    source_policy = _infer_source_policy(answer_kind, memory_policy, intent)
    model_policy = infer_model_policy(intent, combined_policy, model_adapter_status)
    return NlgPlan(
        schema_version=SCHEMA_VERSION,
        user_text=user_text or "",
        detected_intent=intent,
        route=route or "unknown",
        speech_act=speech_act,
        answer_kind=answer_kind,
        tone=infer_tone(user_text or "", frame, intent),
        style_constraints=_style_constraints(answer_kind),
        required_components=_required_components(answer_kind, memory_policy, source_policy),
        forbidden_components=_forbidden_components(answer_kind, memory_policy),
        memory_policy=memory_policy,
        source_policy=source_policy,
        model_policy=model_policy,
        truth_boundary=_truth_boundary(frame, combined_policy),
        timestamp_required=True,
        max_length_hint=_max_length_hint(answer_kind),
    )
