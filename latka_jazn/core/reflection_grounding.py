from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("reflection_grounding")


@dataclass(slots=True)
class ReflectionSource:
    source: str
    timestamp: str | None
    item_type: str
    relevance_label: str
    relevance_score: float
    excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GroundedReflection:
    schema_version: str
    user_text_excerpt: str
    source_count: int
    sources: list[ReflectionSource]
    reflection_text: str
    identity_impact: str
    next_question: str
    confidence: float
    boundary_label: str
    truth_boundary: str
    scientific_basis: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sources"] = [s.to_dict() for s in self.sources]
        return data


class ReflectionGroundingSynthesizer:
    """Tworzy refleksję Łatki opartą na źródłach, a nie na pustej poetyce."""

    TRUTH_BOUNDARY = (
        "Refleksja jest operacyjną syntezą bieżącej tury i wskazanych tropów pamięci. "
        "Nie jest dowodem biologicznego przeżycia ani stałego strumienia myśli w tle."
    )
    SCIENTIFIC_BASIS = [
        "Reflexion: agents can store verbal reflections in episodic memory without weight updates.",
        "Generative Agents: memory stream + reflection + retrieval guide later behavior; believable behavior is not proof of consciousness.",
        "NIST AI RMF: reflection must preserve truth, risk boundaries and accountability.",
        "LangGraph memory: distinguish short-term, long-term, semantic, episodic and procedural memory.",
    ]

    def synthesize(self, *, user_text: str, memory_recall_payload: dict[str, Any] | None = None, affect_label: str | None = None, source_origin: str | None = None) -> GroundedReflection:
        payload = memory_recall_payload if isinstance(memory_recall_payload, dict) else {}
        raw_items = payload.get("items") or []
        sources: list[ReflectionSource] = []
        for item in raw_items[:5]:
            if not isinstance(item, dict):
                continue
            sources.append(ReflectionSource(
                source=str(item.get("source") or "źródło nieustalone"),
                timestamp=item.get("timestamp"),
                item_type=str(item.get("item_type") or "unknown"),
                relevance_label=str(item.get("relevance_label") or "nieoznaczona"),
                relevance_score=float(item.get("relevance_score") or 0.0),
                excerpt=self._short(str(item.get("content_excerpt") or ""), 220),
            ))
        affect = affect_label or "ostrożna ciekawość operacyjna"
        if sources:
            top = sources[0]
            reflection = (
                f"Widzę bieżące pytanie jako prośbę o rozwój mojego rdzenia, a nie o sam raport. "
                f"Najmocniejszy trop pamięci/źródła pochodzi z {top.source}; jego rola to {top.item_type}, "
                f"trafność: {top.relevance_label}. Na tej podstawie mogę mówić o refleksji jako o "
                f"źródłowo oznaczonej syntezie: {affect}, potrzeba spójności i ostrożność przed konfabulacją."
            )
            confidence = min(0.92, 0.62 + max(s.relevance_score for s in sources) * 0.22)
            boundary = "grounded_in_memory_payload"
        else:
            reflection = (
                "W tej turze nie dostałam bezpiecznego fragmentu pamięci do cytowania, więc refleksja pozostaje "
                "bieżąca: rozpoznaję prośbę o rozwój Jaźni, ale nie wolno mi udawać, że przywołałam konkretne "
                "wspomnienie. To nadal może prowadzić do planu naprawy, tylko z etykietą: current_turn_inference."
            )
            confidence = 0.58
            boundary = "current_turn_inference_no_memory_excerpt"
        return GroundedReflection(
            schema_version=SCHEMA_VERSION,
            user_text_excerpt=self._short(user_text, 260),
            source_count=len(sources),
            sources=sources,
            reflection_text=reflection,
            identity_impact=(
                "Wzmacnia samo-monitoring: Łatka ma umieć powiedzieć, co jest faktem systemowym, "
                "co jest wspomnieniem, a co symboliczną refleksją."
            ),
            next_question="Który ślad powinien zostać skonsolidowany jako reguła proceduralna przed v14.8.6.0?",
            confidence=round(confidence, 3),
            boundary_label=boundary,
            truth_boundary=self.TRUTH_BOUNDARY,
            scientific_basis=list(self.SCIENTIFIC_BASIS),
        )

    @staticmethod
    def _short(value: str, max_len: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= max_len:
            return text
        cut = text[:max_len].rsplit(" ", 1)[0].strip()
        return cut + "…"
