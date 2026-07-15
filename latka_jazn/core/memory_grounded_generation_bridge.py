from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import re
from typing import Any

from latka_jazn.core.response_candidate import CandidateEvaluation, ResponseCandidate

SCHEMA_VERSION = "memory_grounded_generation_bridge/v14.8.4.006"
GROUNDING_EVALUATION_REASON = "memory_grounding_bridge_checked"
MEMORY_CLAIM_MARKERS = (
    "pamiętam",
    "pamietam",
    "w mojej pamięci",
    "w mojej pamieci",
    "z pamięci wiem",
    "z pamieci wiem",
    "przypominam sobie",
    "wspomnienie",
    "wspominam",
)


@dataclass(slots=True)
class GroundedMemoryItem:
    """Mały, bezpieczny element pamięci dopuszczony do generowania odpowiedzi.

    To nie jest pełny rekord SQLite ani surowy fragment archiwum. Bridge zachowuje
    tylko minimalny payload: identyfikator, fragment, źródło, czas, confidence i
    powód trafności. Dzięki temu model może formułować zdania, ale nie może
    przejąć pamięci ani wymyślać źródeł.
    """

    item_id: str
    excerpt: str
    source: str
    timestamp: str | None
    confidence: float
    relevance_reason: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.item_id = _clean_identifier(self.item_id, fallback="memory_item")
        self.excerpt = _redact_sensitive_text(_clean_text(self.excerpt, fallback=""))[:800]
        self.source = _clean_text(self.source, fallback="runtime_memory")
        self.timestamp = _optional_text(self.timestamp)
        self.confidence = _as_float(self.confidence, fallback=0.5)
        self.relevance_reason = _clean_text(self.relevance_reason, fallback="grounded_memory_payload")
        self.schema_version = str(self.schema_version or SCHEMA_VERSION)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def memory_allowed_for_generation(nlg_plan: Any, response_policy: dict[str, Any] | None) -> bool:
    """Czy pamięć wolno przekazać do generowania w tej turze."""

    plan = _as_dict(nlg_plan)
    policy = _as_dict(response_policy)
    if policy.get("allow_memory_content") is False:
        return False
    if str(policy.get("memory_gate") or "").lower() in {"not_needed", "forbidden", "blocked"}:
        return False
    return str(plan.get("memory_policy") or "") == "required_grounded_payload"


def build_grounded_memory_items(memory_recall_contract: dict[str, Any] | None, *, limit: int = 8) -> list[GroundedMemoryItem]:
    """Zbuduj ugruntowane elementy pamięci z kontraktu recall.

    Funkcja przyjmuje zarówno kontrakt `memory_recall_contract`, jak i uproszczony
    słownik `{"items": [...]}` z model context. Nie odpytuje pamięci samodzielnie,
    nie skanuje baz i nie tworzy nowych wspomnień.
    """

    contract = _as_dict(memory_recall_contract)
    try:
        max_items = max(0, int(limit or 8))
    except (TypeError, ValueError):
        max_items = 8
    out: list[GroundedMemoryItem] = []
    for index, raw in enumerate(contract.get("items") or []):
        if len(out) >= max_items:
            break
        item = _grounded_item_from_raw(raw, index=index)
        if item.excerpt and item.source:
            out.append(item)
    return out


def enforce_memory_grounding(candidate: ResponseCandidate, grounded_items: list[GroundedMemoryItem]) -> CandidateEvaluation:
    """Oceń, czy kandydat nie używa pamięci poza przekazanym payloadem."""

    grounded_by_id = {item.item_id: item for item in grounded_items}
    used_ids = {str(item_id) for item_id in candidate.used_memory_item_ids or []}
    text = candidate.text or ""
    has_memory_claim = _has_memory_claim(text)
    violations: list[str] = []
    reasons: list[str] = [GROUNDING_EVALUATION_REASON]

    if used_ids and not grounded_by_id:
        violations.append("used_memory_ids_without_grounded_payload")
    unknown_ids = sorted(item_id for item_id in used_ids if item_id not in grounded_by_id)
    if unknown_ids:
        violations.append("used_memory_id_not_in_grounded_payload")
    if has_memory_claim and not grounded_by_id:
        violations.append("memory_claim_without_grounded_items")
    if has_memory_claim and candidate.source == "model_adapter" and grounded_by_id and not used_ids:
        violations.append("model_memory_claim_without_declared_used_memory_ids")

    if grounded_by_id:
        reasons.append("grounded_memory_items_available")
    if used_ids and not unknown_ids:
        reasons.append("candidate_memory_ids_match_grounded_payload")
    if not has_memory_claim and not used_ids:
        reasons.append("candidate_makes_no_memory_claim")

    accepted = not violations
    score = 0.8 if accepted else 0.25
    if grounded_by_id and used_ids and not unknown_ids:
        score += 0.1
    if candidate.source == "runtime_fallback" and candidate.text.strip():
        accepted = True
        score = max(score, 0.55)
    return CandidateEvaluation(
        candidate_id=candidate.candidate_id,
        accepted=accepted,
        score=score,
        reasons=reasons,
        violations=violations,
        requires_repair=bool(violations),
    )


def _grounded_item_from_raw(raw: Any, *, index: int) -> GroundedMemoryItem:
    data = _as_dict(raw)
    item_id = data.get("item_id") or data.get("id") or data.get("memory_id") or f"memory_item_{index + 1}"
    excerpt = (
        data.get("excerpt")
        or data.get("content_excerpt")
        or data.get("content")
        or data.get("text")
        or data.get("summary")
        or ""
    )
    source = data.get("source") or data.get("source_type") or data.get("memory_type") or data.get("table") or "runtime_memory"
    timestamp = data.get("timestamp") or data.get("created_at") or data.get("date") or data.get("create_time_warsaw")
    relevance_reason = data.get("relevance_reason") or data.get("reason") or data.get("meaning_assessment") or "grounded_memory_payload"
    return GroundedMemoryItem(
        item_id=str(item_id),
        excerpt=str(excerpt),
        source=str(source),
        timestamp=str(timestamp) if timestamp is not None else None,
        confidence=_as_float(data.get("confidence"), fallback=0.5),
        relevance_reason=str(relevance_reason),
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


def _has_memory_claim(text: str) -> bool:
    folded = _fold(text)
    return any(_fold(marker) in folded for marker in MEMORY_CLAIM_MARKERS)


def _clean_text(value: Any, *, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def _clean_identifier(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z_.:/-]+", "_", text).strip("_")
    return text or fallback


def _optional_text(value: Any) -> str | None:
    text = _clean_text(value, fallback="")
    return text or None


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


def _fold(text: str) -> str:
    return (text or "").lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))


def _redact_sensitive_text(text: str) -> str:
    if re.search(r"PESEL|dane kliniczne|pacjent|uraz|diagnoz|badanie kliniczne|charakter urazu", text, flags=re.IGNORECASE):
        return "[FRAGMENT ZAWIERA DANE WRAŻLIWE LUB MEDYCZNE — UKRYTY W ODPOWIEDZI]"
    text = re.sub(r"(?<!\d)\d{11}(?!\d)", "[PESEL/DANE_WRAŻLIWE_UKRYTE]", text)
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL_UKRYTY]", text)
    text = re.sub(r"(?<!\d)(?:\+?48[ -]?)?(?:\d[ -]?){9}(?!\d)", "[TELEFON_UKRYTY]", text)
    return text
