from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import re
from typing import Any

from latka_jazn.core.nlg_plan import NlgPlan, default_truth_boundary

SCHEMA_VERSION = "operational_thought_frame/v14.8.4.002"


@dataclass(slots=True)
class OperationalThoughtSignal:
    """Jawny sygnał decyzyjny runtime, nie prywatny tok myślenia."""

    name: str
    value: str
    confidence: float
    source: str

    def __post_init__(self) -> None:
        self.name = _clean_token(self.name, fallback="unknown_signal")
        self.value = _clean_text(self.value, fallback="unknown")
        self.confidence = _clamp_confidence(self.confidence)
        self.source = _clean_token(self.source, fallback="runtime")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OperationalThoughtFrame:
    """Audytowalna ramka decyzji wykonawczych dla jednej tury.

    Ramka nie jest biologicznym przeżyciem, prywatnym chain-of-thought ani finalną
    odpowiedzią użytkownika. Zapisuje wyłącznie jawne decyzje operacyjne: cel,
    ton, pamięć, źródła, model, granice oraz odrzucone ścieżki.
    """

    schema_version: str
    user_message_summary: str
    observed_signals: list[OperationalThoughtSignal]
    selected_goal: str
    selected_tone: list[str]
    memory_decision: str
    source_decision: str
    model_decision: str
    refusal_or_boundary: str | None
    rejected_paths: list[str]
    truth_boundary: str

    def __post_init__(self) -> None:
        self.schema_version = str(self.schema_version or SCHEMA_VERSION)
        self.user_message_summary = _clean_text(self.user_message_summary, fallback="brak treści użytkownika")
        self.observed_signals = [_coerce_signal(signal) for signal in self.observed_signals or []]
        self.selected_goal = _clean_text(self.selected_goal, fallback="odpowiedzieć do bieżącej tury")
        self.selected_tone = _dedupe(self.selected_tone)
        self.memory_decision = _clean_token(self.memory_decision, fallback="not_needed")
        self.source_decision = _clean_token(self.source_decision, fallback="runtime_only")
        self.model_decision = _clean_token(self.model_decision, fallback="allowed_if_configured")
        boundary = _clean_text(self.refusal_or_boundary or "", fallback="")
        self.refusal_or_boundary = boundary or None
        self.rejected_paths = _dedupe(self.rejected_paths)
        self.truth_boundary = _clean_text(self.truth_boundary, fallback=default_truth_boundary())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["observed_signals"] = [signal.to_dict() for signal in self.observed_signals]
        return data


def summarize_current_user_message(user_text: str, max_chars: int = 220) -> str:
    """Zwróć krótki opis bieżącej wiadomości bez sięgania do pamięci."""

    limit = max(40, int(max_chars or 220))
    normalized = re.sub(r"\s+", " ", str(user_text or "")).strip()
    if not normalized:
        return "brak treści użytkownika"
    if len(normalized) <= limit:
        return normalized
    cut = normalized[: max(1, limit - 1)].rstrip()
    return f"{cut}…"


def build_operational_thought_frame(
    *,
    user_text: str,
    nlg_plan: NlgPlan | dict[str, Any],
    cognitive_frame: dict[str, Any] | None,
    response_policy: dict[str, Any] | None,
) -> OperationalThoughtFrame:
    """Zbuduj audytowalną ramkę decyzji z NLG Plan i danych runtime.

    Funkcja nie generuje finalnej odpowiedzi, nie wywołuje modelu i nie pobiera
    pamięci. Używa wyłącznie jawnych pól już wyprowadzonych przez runtime.
    """

    plan = _as_dict(nlg_plan)
    frame = _as_dict(cognitive_frame)
    policy = _as_dict(response_policy)
    answer_kind = _clean_token(plan.get("answer_kind"), fallback="natural_dialogue")
    memory_policy = _clean_token(plan.get("memory_policy"), fallback="not_needed")
    source_policy = _clean_token(plan.get("source_policy"), fallback="runtime_only")
    model_policy = _clean_token(plan.get("model_policy"), fallback="allowed_if_configured")
    tone = _dedupe(plan.get("tone") or [])
    truth_boundary = _clean_text(plan.get("truth_boundary"), fallback=default_truth_boundary())

    signals = [
        OperationalThoughtSignal("current_turn", summarize_current_user_message(user_text), 1.0, "user_text"),
        OperationalThoughtSignal("answer_kind", answer_kind, 1.0, "nlg_plan"),
        OperationalThoughtSignal("memory_policy", memory_policy, 1.0, "nlg_plan"),
        OperationalThoughtSignal("source_policy", source_policy, 1.0, "nlg_plan"),
        OperationalThoughtSignal("model_policy", model_policy, 1.0, "nlg_plan"),
    ]
    memory_gate = str(frame.get("memory_gate") or frame.get("memory_use_gate") or "").strip()
    if memory_gate:
        signals.append(OperationalThoughtSignal("memory_gate", memory_gate, 0.95, "cognitive_frame"))
    if policy.get("exact_runtime_required") is True:
        signals.append(OperationalThoughtSignal("exact_runtime_required", "true", 1.0, "response_policy"))
    if memory_policy == "required_grounded_payload":
        signals.append(OperationalThoughtSignal("memory_grounding_required", "true", 1.0, "nlg_plan"))
    if source_policy == "requires_external_web":
        signals.append(OperationalThoughtSignal("external_source_required", "true", 1.0, "nlg_plan"))
    if model_policy.startswith("forbidden") or model_policy == "disabled_null_adapter":
        signals.append(OperationalThoughtSignal("model_boundary", model_policy, 1.0, "nlg_plan"))

    return OperationalThoughtFrame(
        schema_version=SCHEMA_VERSION,
        user_message_summary=summarize_current_user_message(user_text),
        observed_signals=signals,
        selected_goal=_selected_goal(answer_kind),
        selected_tone=tone or ["calm", "conversational"],
        memory_decision=memory_policy,
        source_decision=source_policy,
        model_decision=model_policy,
        refusal_or_boundary=_boundary_note(memory_policy, source_policy, model_policy, truth_boundary),
        rejected_paths=_rejected_paths(answer_kind, memory_policy, source_policy, model_policy),
        truth_boundary=truth_boundary,
    )


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


def _clean_text(value: Any, *, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def _clean_token(value: Any, *, fallback: str) -> str:
    token = str(value or "").strip()
    token = re.sub(r"[^0-9A-Za-z_./:-]+", "_", token)
    token = token.strip("_")
    return token or fallback


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def _coerce_signal(signal: OperationalThoughtSignal | dict[str, Any]) -> OperationalThoughtSignal:
    if isinstance(signal, OperationalThoughtSignal):
        return signal
    data = _as_dict(signal)
    return OperationalThoughtSignal(
        name=str(data.get("name") or "unknown_signal"),
        value=str(data.get("value") or "unknown"),
        confidence=_clamp_confidence(data.get("confidence")),
        source=str(data.get("source") or "runtime"),
    )


def _dedupe(values: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        value = _clean_text(raw, fallback="")
        if value and value not in out:
            out.append(value)
    return out


def _selected_goal(answer_kind: str) -> str:
    return {
        "natural_dialogue": "odpowiedzieć naturalnie na bieżącą turę bez raportu technicznego",
        "diagnostic_brief": "dać krótki, sprawdzalny status runtime",
        "diagnostic_full": "dać pełny raport diagnostyczny tylko gdy jest potrzebny",
        "memory_grounded_answer": "odpowiedzieć wyłącznie na podstawie ugruntowanego payloadu pamięci",
        "exact_runtime_quote": "zachować dokładny tekst runtime bez parafrazy modelu",
        "external_research_required": "oddzielić runtime od zewnętrznego lookupu źródeł",
        "creative_or_document_answer": "zbudować tekst twórczy lub dokument w granicach kontraktu źródeł",
    }.get(answer_kind, "odpowiedzieć do bieżącej tury")


def _boundary_note(memory_policy: str, source_policy: str, model_policy: str, truth_boundary: str) -> str | None:
    if model_policy.startswith("forbidden"):
        return f"Model nie może przejąć tej odpowiedzi: {model_policy}. {truth_boundary}"
    if model_policy == "disabled_null_adapter":
        return "Model adapter jest wyłączony albo null; runtime nie może udawać generacji modelowej."
    if source_policy == "requires_external_web":
        return "Wymagane są źródła zewnętrzne; lokalny runtime nie może udawać web lookupu."
    if memory_policy == "required_grounded_payload":
        return "Wymagany jest ugruntowany payload pamięci; nie wolno mówić „pamiętam” bez źródła."
    return None


def _rejected_paths(answer_kind: str, memory_policy: str, source_policy: str, model_policy: str) -> list[str]:
    rejected = [
        "private_chain_of_thought_as_user_visible_content",
        "biological_consciousness_claim",
        "old_topic_stale_route",
    ]
    if memory_policy != "required_grounded_payload":
        rejected.extend(["random_memory_injection", "claiming_memory_without_payload"])
    if model_policy.startswith("forbidden") or model_policy == "disabled_null_adapter":
        rejected.append("model_generation_as_source_of_truth")
    if answer_kind == "exact_runtime_quote":
        rejected.append("model_paraphrase_of_exact_runtime_text")
    if source_policy == "requires_external_web":
        rejected.append("local_runtime_fake_web_lookup")
    return _dedupe(rejected)
