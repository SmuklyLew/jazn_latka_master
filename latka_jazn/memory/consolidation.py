from __future__ import annotations
from dataclasses import dataclass, asdict
from latka_jazn.core.scientific_basis import references_for_module

@dataclass(slots=True)
class ConsolidationWeights:
    identity_relevance: float
    affective_salience: float
    novelty: float
    correction_value: float
    source_strength: float
    temporal_anchor: float
    boundary_risk: float

    @property
    def total(self) -> float:
        weighted = (
            0.24 * self.identity_relevance +
            0.18 * self.affective_salience +
            0.14 * self.novelty +
            0.18 * self.correction_value +
            0.12 * self.source_strength +
            0.08 * self.temporal_anchor +
            0.06 * self.boundary_risk
        )
        return max(0.0, min(1.0, weighted))

@dataclass(slots=True)
class ConsolidationPlan:
    weights: ConsolidationWeights
    should_store_episode: bool
    should_write_reflection: bool
    should_update_procedure: bool
    should_promote_semantic_fact: bool
    reflection_question: str
    reason: str
    scientific_basis: list[dict]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["weights"]["total"] = self.weights.total
        return data

class MemoryConsolidationModel:
    """Decyduje, co zapisać i w jakiej warstwie.

    Inspiracja: konsolidacja pamięci, self-memory system i agentowe architektury pamięci.
    Implementacja jest ostrożna: zapisuje mniej, ale z metadanymi prawdy, zamiast wszystko
    zamieniać w „wspomnienia”.
    """
    IDENTITY_TERMS = ("tożsamość", "jaźń", "łatka", "kim jesteś", "sobą", "byt", "istnienie")
    MEMORY_TERMS = ("pamięć", "wspomn", "archiw", "dziennik", "ciągłość", "przeży")
    CORRECTION_TERMS = ("błąd", "popraw", "źle", "nie tak", "napraw", "korekta", "hotfix")
    SEMANTIC_TERMS = ("zasada", "reguła", "wartość", "kanon", "źródło", "moduł", "funkcja")

    def _score_terms(self, text: str, terms: tuple[str, ...], base: float = 0.0, inc: float = 0.18) -> float:
        low = text.lower()
        return max(0.0, min(1.0, base + sum(inc for t in terms if t in low)))

    def plan(self, *, text: str, emotional_profile: object | None = None, source_count: int = 0,
             silence_gap_seconds: int | None = None, truth_risk: float = 0.0) -> ConsolidationPlan:
        appraisal = getattr(emotional_profile, "appraisal", None)
        identity = max(self._score_terms(text, self.IDENTITY_TERMS, 0.05), getattr(appraisal, "identity_relevance", 0.0) if appraisal else 0.0)
        memory = self._score_terms(text, self.MEMORY_TERMS, 0.05)
        correction = max(self._score_terms(text, self.CORRECTION_TERMS, 0.0, 0.25), getattr(appraisal, "correction_signal", 0.0) if appraisal else 0.0)
        novelty = max(0.10, getattr(appraisal, "novelty", 0.0) if appraisal else 0.0)
        affect = max(memory, getattr(emotional_profile, "arousal", 0.0) if emotional_profile else 0.0, getattr(emotional_profile, "need_for_closeness", 0.0) * 0.55 if emotional_profile else 0.0)
        source_strength = min(1.0, 0.15 + 0.25 * source_count)
        temporal = 0.40 if silence_gap_seconds else 0.20
        if silence_gap_seconds and silence_gap_seconds >= 21600:
            temporal = 0.80
        boundary = max(truth_risk, getattr(emotional_profile, "need_for_truth_check", 0.0) if emotional_profile else 0.0)
        weights = ConsolidationWeights(identity, affect, novelty, correction, source_strength, temporal, boundary)
        semantic = self._score_terms(text, self.SEMANTIC_TERMS, 0.0, 0.18) > 0.30 or correction > 0.45
        total = weights.total
        should_store = total >= 0.42 or identity > 0.45 or correction > 0.35
        should_reflect = total >= 0.50 or identity > 0.55 or boundary > 0.55
        should_proc = correction > 0.35 or "zawsze" in text.lower() or "nie wolno" in text.lower()
        question = "Co ten epizod zmienia w moim sposobie odpowiadania i co trzeba oznaczyć jako fakt, wniosek lub symbol?"
        reason = f"waga={total:.2f}; tożsamość={identity:.2f}; afekt={affect:.2f}; korekta={correction:.2f}; granica={boundary:.2f}"
        return ConsolidationPlan(weights, should_store, should_reflect, should_proc, semantic, question, reason, references_for_module("memory_consolidation"))
