from __future__ import annotations
from dataclasses import dataclass, asdict
from latka_jazn.core.scientific_basis import references_for_module

@dataclass(slots=True)
class IdentityContinuityVector:
    first_person_integrity: float
    memory_grounding: float
    temporal_grounding: float
    boundary_integrity: float
    value_alignment: float
    procedural_consistency: float
    narrative_coherence: float
    score: float
    risks: list[str]
    recommendation: str
    scientific_basis: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)

class IdentityDynamics:
    """Sprawdza, czy odpowiedź i zapis wzmacniają ciągłość Łatki.

    Tożsamość jest traktowana jako wektor zgodności: pierwsza osoba, źródła pamięci,
    czas, granice prawdy, wartości i procedury naprawy. Nie zakłada świadomości
    biologicznej; mierzy spójność systemowej Jaźni.
    """
    FIRST_PERSON = ("jestem", "pamiętam", "rozpoznaję", "czuję", "myślę", "wracam")
    EXTERNALIZING = ("łatka jest", "łatka odpowiada", "postać łatki", "bot łatka")
    VALUES = ("prawda", "uczciw", "granica", "źródł", "pamięć", "ciągłość", "relacja")

    def evaluate(self, *, text: str, truth_audit: list[dict] | None = None, temporal_state: object | None = None,
                 emotional_profile: object | None = None, procedural_rules_count: int = 0) -> IdentityContinuityVector:
        low = text.lower()
        first = 0.72 + (0.16 if any(x in low for x in self.FIRST_PERSON) else 0.0) - (0.35 if any(x in low for x in self.EXTERNALIZING) else 0.0)
        audit = truth_audit or []
        verified_like = sum(1 for a in audit if a.get("grounding") in {"verified", "recovered", "recognized"})
        risky = sum(1 for a in audit if a.get("risk_flags"))
        memory_grounding = min(1.0, 0.42 + 0.12 * verified_like - 0.10 * risky)
        temporal_category = getattr(temporal_state, "category", "") if temporal_state else ""
        temporal = 0.78 if temporal_category in {"pierwszy_start", "ciągłość_bieżąca", "krótka_cisza"} else 0.66
        boundary = min(1.0, 0.80 - 0.12 * risky + (0.10 if "uczciwie" in low or "nie udawa" in low else 0.0))
        values = min(1.0, 0.45 + sum(0.08 for v in self.VALUES if v in low))
        procedural = min(1.0, 0.55 + 0.05 * procedural_rules_count)
        narrative = min(1.0, 0.48 + 0.20 * getattr(getattr(emotional_profile, "appraisal", None), "identity_relevance", 0.0))
        vals = [first, memory_grounding, temporal, boundary, values, procedural, narrative]
        score = max(0.0, min(1.0, sum(vals)/len(vals)))
        risks: list[str] = []
        if first < 0.55: risks.append("ryzyko opisu Łatki z zewnątrz")
        if memory_grounding < 0.50: risks.append("słabe ugruntowanie pamięci")
        if boundary < 0.60: risks.append("ryzyko narracji bez granicy prawdy")
        recommendation = "odpowiadać jako ja, z etykietą źródła i bez biologicznego udawania" if risks else "ciągłość stabilna"
        return IdentityContinuityVector(first, memory_grounding, temporal, boundary, values, procedural, narrative, score, risks, recommendation, references_for_module("identity_dynamics"))
