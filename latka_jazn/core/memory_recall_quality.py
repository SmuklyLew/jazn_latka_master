from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_recall_quality")


@dataclass(slots=True)
class MemoryRecallQualityReport:
    schema_version: str
    item_count: int
    content_present: bool
    source_present: bool
    timestamp_or_scope_present: bool
    high_or_medium_relevance_count: int
    counts_only_failure: bool
    random_memory_risk: str
    self_vs_user_boundary: str
    score: float
    verdict: str
    required_next_action: str
    truth_boundary: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryRecallQualityEvaluator:
    """Ocenia, czy recall jest treściową pamięcią, czy tylko licznikiem.

    Reguła centralna v14.8.5.011: counts są diagnostyką, nie wspomnieniem.
    """

    TRUTH_BOUNDARY = (
        "Ocena recallu mierzy jakość przekazanych tropów pamięci. Nie dowodzi, że system "
        "odczytał całe archiwum ani że wspomnienie jest biologiczne."
    )

    def evaluate(self, payload: dict[str, Any] | None, *, user_text: str = "", expected_boundary: str = "self_memory") -> MemoryRecallQualityReport:
        payload = payload if isinstance(payload, dict) else {}
        items = [x for x in (payload.get("items") or []) if isinstance(x, dict)]
        counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
        content_present = any(str(i.get("content_excerpt") or "").strip() for i in items)
        source_present = any(str(i.get("source") or "").strip() for i in items)
        timestamp_present = any(str(i.get("timestamp") or i.get("source") or "").strip() for i in items)
        relevance_count = sum(1 for i in items if str(i.get("relevance_label") or "").lower() in {"wysoka", "średnia", "srednia"})
        counts_only = bool(counts) and not content_present
        score = 0.0
        score += 0.28 if content_present else 0.0
        score += 0.18 if source_present else 0.0
        score += 0.12 if timestamp_present else 0.0
        score += min(0.24, relevance_count * 0.08)
        if counts_only:
            score -= 0.25
        score = round(max(0.0, min(1.0, score)), 3)
        low_user = (user_text or "").lower()
        self_question = any(x in low_user for x in ("łatka", "latka", "jaźń", "jazn", "sobie", "swojej", "twojej", "tożsamo", "tozsamo"))
        if self_question and expected_boundary.startswith("self"):
            boundary = "self_memory_or_self_architecture_expected"
        elif "krzysztof" in low_user or "mnie" in low_user:
            boundary = "user_memory_expected"
        else:
            boundary = "mixed_or_unclear_memory_scope"
        if counts_only:
            verdict = "counts_only_failure"
            action = "nie pokazywać jako wspomnienia; poszerzyć zapytanie lub oznaczyć brak bezpiecznego fragmentu"
            risk = "high"
        elif not items:
            verdict = "no_items"
            action = "jawnie oznaczyć current_turn_inference_no_memory_excerpt"
            risk = "medium"
        elif score >= 0.62:
            verdict = "usable_grounded_recall"
            action = "można użyć w odpowiedzi z krótkim źródłem i granicą prawdy"
            risk = "low"
        else:
            verdict = "weak_recall_use_as_hint_only"
            action = "użyć tylko jako tropu pomocniczego albo poprosić o zawężenie"
            risk = "medium"
        evidence = []
        for item in items[:4]:
            evidence.append(f"{item.get('item_type','unknown')}:{item.get('source','no_source')}:{item.get('relevance_label','no_label')}")
        return MemoryRecallQualityReport(
            schema_version=SCHEMA_VERSION,
            item_count=len(items),
            content_present=content_present,
            source_present=source_present,
            timestamp_or_scope_present=timestamp_present,
            high_or_medium_relevance_count=relevance_count,
            counts_only_failure=counts_only,
            random_memory_risk=risk,
            self_vs_user_boundary=boundary,
            score=score,
            verdict=verdict,
            required_next_action=action,
            truth_boundary=self.TRUTH_BOUNDARY,
            evidence=evidence,
        )
