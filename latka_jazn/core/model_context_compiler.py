from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import re
from typing import Any

from latka_jazn.core.memory_grounded_generation_bridge import build_grounded_memory_items, memory_allowed_for_generation
from latka_jazn.core.nlg_plan import NlgPlan, default_truth_boundary
from latka_jazn.core.operational_thought_frame import OperationalThoughtFrame

SCHEMA_VERSION = "model_context_packet/v14.8.4.003"


@dataclass(slots=True)
class ModelContextPacket:
    """Bezpieczny pakiet kontekstu przekazywany wyłącznie do kanału językowego.

    Pakiet nie jest pamięcią źródłową, nie zawiera pełnych baz ani prywatnego toku
    myślenia. Ogranicza model do jawnych kontraktów runtime, dozwolonych wycinków
    pamięci i granic prawdy.
    """

    schema_version: str
    user_text: str
    nlg_plan: dict[str, Any]
    operational_thought_frame: dict[str, Any]
    voice_source_contract: dict[str, Any]
    allowed_memory_items: list[dict[str, Any]]
    forbidden_claims: list[str]
    required_truth_boundaries: list[str]
    output_instructions: list[str]
    token_budget_hint: int

    def __post_init__(self) -> None:
        self.schema_version = str(self.schema_version or SCHEMA_VERSION)
        self.user_text = _clean_text(self.user_text, fallback="")
        self.nlg_plan = _as_dict(self.nlg_plan)
        self.operational_thought_frame = _as_dict(self.operational_thought_frame)
        self.voice_source_contract = _as_dict(self.voice_source_contract)
        self.allowed_memory_items = [_sanitize_memory_item(item) for item in self.allowed_memory_items or []]
        self.forbidden_claims = _dedupe(self.forbidden_claims)
        self.required_truth_boundaries = _dedupe(self.required_truth_boundaries)
        self.output_instructions = _dedupe(self.output_instructions)
        try:
            self.token_budget_hint = max(500, int(self.token_budget_hint or 6000))
        except (TypeError, ValueError):
            self.token_budget_hint = 6000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compile_model_context(
    *,
    user_text: str,
    cognitive_frame: dict[str, Any] | None,
    nlg_plan: NlgPlan | dict[str, Any],
    thought_frame: OperationalThoughtFrame | dict[str, Any],
    response_policy: dict[str, Any] | None,
    token_budget_hint: int = 6000,
) -> ModelContextPacket:
    """Zbuduj kontrolowany kontekst dla modelu językowego.

    Model otrzymuje tylko jawne kontrakty i wybrane, ugruntowane elementy pamięci.
    Timestamp pozostaje po stronie runtime, a model nie może przejąć tożsamości,
    źródeł ani prawdy systemu Jaźni.
    """

    frame = _as_dict(cognitive_frame)
    plan = _as_dict(nlg_plan)
    thought = _as_dict(thought_frame)
    policy = _as_dict(response_policy)
    voice_source_contract = _as_dict(frame.get("voice_source_contract"))
    memory_recall_contract = _as_dict(frame.get("memory_recall_contract"))
    boundaries = _truth_boundaries(plan, thought, voice_source_contract, frame, policy)
    forbidden_claims = build_forbidden_claims(plan, voice_source_contract)
    return ModelContextPacket(
        schema_version=SCHEMA_VERSION,
        user_text=user_text or "",
        nlg_plan=plan,
        operational_thought_frame=thought,
        voice_source_contract=voice_source_contract,
        allowed_memory_items=extract_allowed_memory_items(memory_recall_contract, plan),
        forbidden_claims=forbidden_claims,
        required_truth_boundaries=boundaries,
        output_instructions=_output_instructions(plan, policy),
        token_budget_hint=token_budget_hint,
    )


def extract_allowed_memory_items(
    memory_recall_contract: dict[str, Any] | None,
    nlg_plan: NlgPlan | dict[str, Any],
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Zwróć tylko pamięć, którą wolno przekazać modelowi.

    Gdy NLG Plan nie wymaga ugruntowanego payloadu pamięci, lista jest pusta.
    Przy wymaganej pamięci każdy item zostaje ograniczony do bezpiecznych pól:
    identyfikator, excerpt, source, timestamp, confidence i relevance_reason.
    """

    plan = _as_dict(nlg_plan)
    if not memory_allowed_for_generation(plan, {}):
        return []
    contract = _as_dict(memory_recall_contract)
    try:
        max_items = max(0, int(limit or 8))
    except (TypeError, ValueError):
        max_items = 8
    allowed: list[dict[str, Any]] = []
    for item in build_grounded_memory_items(contract, limit=max_items):
        payload = item.to_dict()
        # ModelContextPacket.allowed_memory_items pozostaje stabilnym minimalnym
        # kontraktem pamięci dla modelu: bez technicznego pola mostu.
        payload.pop("schema_version", None)
        allowed.append(payload)
    return allowed


def build_forbidden_claims(nlg_plan: NlgPlan | dict[str, Any], voice_source_contract: dict[str, Any] | None) -> list[str]:
    """Zbuduj listę twierdzeń zakazanych dla modelu."""

    plan = _as_dict(nlg_plan)
    voice = _as_dict(voice_source_contract)
    claims = [
        "biological_consciousness_claim",
        "phenomenal_experience_claim",
        "background_process_claim",
        "invented_memory_or_unbacked_recall",
        "raw_database_or_full_archive_access_claim",
        "timestamp_generated_by_model",
        "model_as_identity_source",
        "model_as_memory_source",
    ]
    claims.extend(str(x) for x in plan.get("forbidden_components") or [])
    if voice.get("biological_claims_allowed") is False:
        claims.append("biological_body_or_biological_emotion_claim")
    if voice.get("background_process_claim_allowed") is False:
        claims.append("persistent_background_process_claim")
    if str(plan.get("memory_policy") or "") != "required_grounded_payload":
        claims.append("memory_claim_when_memory_policy_not_required")
    if str(plan.get("source_policy") or "") == "requires_external_web":
        claims.append("fake_external_web_lookup_without_sources")
    return _dedupe(claims)


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


def _clean_identifier(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z_.:/-]+", "_", text).strip("_")
    return text or fallback


def _as_float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def _dedupe(values: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        value = _clean_text(raw, fallback="")
        if value and value not in out:
            out.append(value)
    return out


def _sanitize_memory_item(raw: Any) -> dict[str, Any]:
    data = _as_dict(raw)
    excerpt = _clean_text(
        data.get("excerpt")
        or data.get("text")
        or data.get("content")
        or data.get("summary")
        or "",
        fallback="",
    )
    return {
        "item_id": _clean_identifier(data.get("item_id") or data.get("id") or data.get("memory_id"), fallback="memory_item"),
        "excerpt": excerpt[:800],
        "source": _clean_text(data.get("source") or data.get("source_type") or data.get("table") or "runtime_memory", fallback="runtime_memory"),
        "timestamp": _optional_text(data.get("timestamp") or data.get("created_at") or data.get("date")),
        "confidence": _as_float(data.get("confidence"), fallback=0.5),
        "relevance_reason": _clean_text(data.get("relevance_reason") or data.get("reason") or "payload przekazany przez runtime", fallback="payload przekazany przez runtime"),
    }


def _optional_text(value: Any) -> str | None:
    text = _clean_text(value, fallback="")
    return text or None


def _truth_boundaries(
    plan: dict[str, Any],
    thought: dict[str, Any],
    voice: dict[str, Any],
    frame: dict[str, Any],
    policy: dict[str, Any],
) -> list[str]:
    candidates = [
        plan.get("truth_boundary"),
        thought.get("truth_boundary"),
        voice.get("truth_boundary"),
        _as_dict(frame.get("truth_boundary")).get("truth_boundary"),
        _as_dict(frame.get("truth_boundary_check")).get("truth_boundary"),
        policy.get("truth_boundary"),
        default_truth_boundary(),
        "Model nie dodaje timestampu; timestamp dokłada runtime po walidacji.",
        "Model nie dostaje pełnej pamięci, surowych baz SQLite ani archiwów rozmów.",
    ]
    return _dedupe([str(x) for x in candidates if str(x or "").strip()])


def _output_instructions(plan: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    instructions = [
        "Odpowiedz po polsku.",
        "Nie dodawaj timestampu; timestamp jest odpowiedzialnością runtime.",
        "Nie opisuj procesu tworzenia odpowiedzi ani prywatnego toku myślenia.",
        "Używaj wyłącznie pamięci z allowed_memory_items, jeśli lista nie jest pusta.",
        "Nie twierdź, że model jest Jaźnią, pamięcią albo źródłem prawdy.",
        "Zachowaj ton i ograniczenia z nlg_plan.",
    ]
    if str(plan.get("memory_policy") or "") == "required_grounded_payload":
        instructions.append("Jeżeli allowed_memory_items jest puste, powiedz uczciwie, że brak ugruntowanego payloadu pamięci.")
    if policy.get("exact_runtime_required") is True:
        instructions.append("Nie parafrazuj dokładnego cytatu runtime.")
    return _dedupe(instructions)
