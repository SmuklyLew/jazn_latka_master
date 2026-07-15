from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

SCHEMA_VERSION = "nlg_plan/v14.8.4.001"

ANSWER_KINDS = {
    "natural_dialogue",
    "diagnostic_brief",
    "diagnostic_full",
    "memory_grounded_answer",
    "exact_runtime_quote",
    "external_research_required",
    "creative_or_document_answer",
}
MEMORY_POLICIES = {
    "not_needed",
    "allowed_if_available",
    "required_grounded_payload",
    "forbidden",
    "unavailable_truthful_notice",
}
SOURCE_POLICIES = {
    "runtime_only",
    "runtime_plus_memory",
    "requires_external_web",
    "exact_runtime_only",
    "reference_only",
}
MODEL_POLICIES = {
    "allowed",
    "allowed_if_configured",
    "forbidden_exact_runtime_required",
    "forbidden_external_source_required",
    "disabled_null_adapter",
}


def _dedupe(values: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _coerce_choice(value: str, allowed: set[str], fallback: str) -> str:
    clean = str(value or "").strip()
    return clean if clean in allowed else fallback


@dataclass(slots=True)
class NlgPlan:
    """Audytowalny plan odpowiedzi między NLP/runtime a syntezą tekstu.

    Ten obiekt nie jest finalną odpowiedzią i nie zawiera prywatnego chain-of-thought.
    Opisuje wyłącznie jawne decyzje wykonawcze: typ odpowiedzi, politykę pamięci,
    źródła, model, wymagane i zakazane komponenty oraz granicę prawdy.
    """

    schema_version: str
    user_text: str
    detected_intent: str
    route: str
    speech_act: str
    answer_kind: str
    tone: list[str]
    style_constraints: list[str]
    required_components: list[str]
    forbidden_components: list[str]
    memory_policy: str
    source_policy: str
    model_policy: str
    truth_boundary: str
    timestamp_required: bool
    max_length_hint: str

    def __post_init__(self) -> None:
        self.schema_version = str(self.schema_version or SCHEMA_VERSION)
        self.user_text = str(self.user_text or "")
        self.detected_intent = str(self.detected_intent or "ordinary_conversation")
        self.route = str(self.route or "unknown")
        self.speech_act = str(self.speech_act or "statement")
        self.answer_kind = _coerce_choice(self.answer_kind, ANSWER_KINDS, "natural_dialogue")
        self.tone = _dedupe(self.tone)
        self.style_constraints = _dedupe(self.style_constraints)
        self.required_components = _dedupe(self.required_components)
        self.forbidden_components = _dedupe(self.forbidden_components)
        self.memory_policy = _coerce_choice(self.memory_policy, MEMORY_POLICIES, "not_needed")
        self.source_policy = _coerce_choice(self.source_policy, SOURCE_POLICIES, "runtime_only")
        self.model_policy = _coerce_choice(self.model_policy, MODEL_POLICIES, "allowed_if_configured")
        self.truth_boundary = str(self.truth_boundary or default_truth_boundary())
        self.timestamp_required = bool(self.timestamp_required)
        self.max_length_hint = str(self.max_length_hint or "medium")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_truth_boundary() -> str:
    return (
        "NLG Plan jest operacyjnym kontraktem generowania odpowiedzi. "
        "Nie jest prywatnym chain-of-thought, biologicznym przeżyciem ani dowodem "
        "fenomenalnej świadomości. Model może być wyłącznie kanałem formułowania zdań; "
        "źródłem pamięci, tożsamości i prawdy pozostaje runtime Jaźni."
    )
