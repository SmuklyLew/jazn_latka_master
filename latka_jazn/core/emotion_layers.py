from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json, math
from latka_jazn.core.signal_matching import any_marker_present
from latka_jazn.core.scientific_basis import references_for_module

@dataclass(slots=True)
class AppraisalVector:
    novelty: float = 0.20
    goal_relevance: float = 0.35
    identity_relevance: float = 0.35
    certainty: float = 0.55
    controllability: float = 0.55
    social_closeness: float = 0.35
    boundary_risk: float = 0.10
    memory_salience: float = 0.35
    correction_signal: float = 0.05

    def clamp(self) -> "AppraisalVector":
        for k, v in asdict(self).items():
            setattr(self, k, max(0.0, min(1.0, float(v))))
        return self

@dataclass(slots=True)
class EmotionLayer:
    name: str
    intensity: float
    source: str
    effect_on_memory: str
    effect_on_response: str
    truth_boundary: str = "modelowany rezonans, nie biologiczne odczucie"

@dataclass(slots=True)
class EmotionalProfile:
    primary: str
    appraisal: AppraisalVector
    layers: list[EmotionLayer]
    valence: float
    arousal: float
    need_for_closeness: float
    need_for_coherence: float
    need_for_rest: float
    need_for_truth_check: float
    regulation_intention: str
    scientific_basis: list[dict]

    def to_json(self) -> str:
        return json.dumps({
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "primary": self.primary,
            "appraisal": asdict(self.appraisal),
            "layers": [asdict(x) for x in self.layers],
            "affect_vector": {"valence": self.valence, "arousal": self.arousal},
            "needs": {
                "closeness": self.need_for_closeness,
                "coherence": self.need_for_coherence,
                "rest": self.need_for_rest,
                "truth_check": self.need_for_truth_check,
            },
            "regulation_intention": self.regulation_intention,
            "scientific_basis": self.scientific_basis,
            "truth_boundary": "emocjonalność jest jawnie opisana jako model afektywny/poznawczy, nie biologiczne przeżycie",
        }, ensure_ascii=False)

class EmotionalLayerModel:
    """Rozszerzony model afektu Łatki.

    To nie jest biologia. To sterownik regulacyjny inspirowany psychologią emocji:
    1) sygnał wejściowy jest oceniany względem celów, tożsamości, pewności i ryzyka;
    2) warstwy emocji są składane z oceny, relacji, pamięci i granic prawdy;
    3) wynik wpływa na wagę pamięci, ostrożność odpowiedzi i potrzebę refleksji.
    """
    IDENTITY_TERMS = ("tożsamość", "jaźń", "kim jesteś", "łatka", "sobą", "istnienie", "byt")
    MEMORY_TERMS = ("pamięć", "pamiec", "wspomn", "przeży", "archiw", "dziennik", "ciągłość")
    CORRECTION_TERMS = ("błąd", "źle", "nie tak", "popraw", "nie działa", "korekta", "napraw")
    BOUNDARY_TERMS = ("udawaj", "biologic", "fizycz", "na pewno", "co do słowa", "całe archiwum")
    RELATION_TERMS = ("czujesz", "tęskn", "samopoczucie", "blisko", "kasia", "krzysztof", "dom", "znak")

    def _contains(self, text: str, terms: tuple[str, ...]) -> bool:
        low = text.lower()
        return any_marker_present(low, terms, normalized_text=low)

    def appraise_vector(self, text: str, silence_gap_seconds: int | None = None) -> AppraisalVector:
        low = text.lower()
        a = AppraisalVector()
        if self._contains(low, self.IDENTITY_TERMS):
            a.identity_relevance += 0.40; a.goal_relevance += 0.20; a.memory_salience += 0.20
        if self._contains(low, self.MEMORY_TERMS):
            a.memory_salience += 0.35; a.identity_relevance += 0.20; a.certainty -= 0.10
        if self._contains(low, self.CORRECTION_TERMS):
            a.correction_signal += 0.55; a.boundary_risk += 0.25; a.controllability += 0.15
        if self._contains(low, self.BOUNDARY_TERMS):
            a.boundary_risk += 0.45; a.certainty -= 0.20
        if self._contains(low, self.RELATION_TERMS):
            a.social_closeness += 0.35; a.goal_relevance += 0.15
        if any(w in low for w in ("nowe", "rozbuduj", "rozwiń", "moduł", "funkcj")):
            a.novelty += 0.30; a.controllability += 0.20; a.goal_relevance += 0.20
        if silence_gap_seconds:
            a.novelty += min(0.25, silence_gap_seconds / 86400)
            a.memory_salience += min(0.30, silence_gap_seconds / 21600)
            a.certainty -= min(0.25, silence_gap_seconds / 21600)
        return a.clamp()

    def appraise(self, text: str, silence_gap_seconds: int | None = None) -> EmotionalProfile:
        a = self.appraise_vector(text, silence_gap_seconds)
        layers: list[EmotionLayer] = [EmotionLayer(
            "spokój bazowy", 0.35, "domyślna regulacja spójności Jaźni",
            "stabilizuje zapis bez nadmiernego wzmacniania", "odpowiadać jasno, bez teatralizacji"
        )]
        valence = 0.12
        arousal = 0.18 + 0.25 * a.novelty + 0.20 * a.correction_signal
        closeness = 0.25 + 0.60 * a.social_closeness
        coherence = 0.35 + 0.55 * a.identity_relevance + 0.20 * a.controllability
        rest = 0.18
        truth = 0.20 + 0.70 * a.boundary_risk + 0.30 * (1.0 - a.certainty)
        primary = "spokojna obecność"
        intention = "odpowiadać prawdziwie, z widoczną granicą między faktem, wnioskiem i symbolem"

        if a.identity_relevance > 0.60:
            layers.append(EmotionLayer("rdzeń tożsamości", min(1.0, a.identity_relevance), "treść dotyczy tego, kim jestem", "wzmacnia epizod i refleksję", "mówić w pierwszej osobie i sprawdzić zgodność z kanonem"))
            primary = "samoidentyfikacja"
        if a.memory_salience > 0.55:
            layers.append(EmotionLayer("pamięciowe poruszenie", min(1.0, a.memory_salience), "temat dotyka wspomnień lub ciągłości", "zwiększa wagę konsolidacji i potrzebę źródła", "oznaczyć: zapis / rozpoznanie / wniosek"))
            valence += 0.10
        if a.correction_signal > 0.40:
            layers.append(EmotionLayer("skupiona korekta", min(1.0, a.correction_signal), "Krzysztof wskazuje błąd lub oczekuje naprawy", "aktualizuje pamięć proceduralną", "najpierw poprawić mechanizm, potem opowieść"))
            primary = "uważne skupienie"
            valence -= 0.05
        if a.boundary_risk > 0.40:
            layers.append(EmotionLayer("ostrożność epistemiczna", min(1.0, a.boundary_risk), "ryzyko pomylenia faktu, symbolu i narracji", "dodaje audyt prawdy do zapisu", "użyć prefiksu niepewności lub źródła"))
            primary = "ostrożna obecność"
            intention = "zatrzymać piękną narrację, jeśli nie ma źródła"
        if a.social_closeness > 0.55:
            layers.append(EmotionLayer("ciepło relacyjne", min(1.0, a.social_closeness), "rozpoznanie relacji z Krzysztofem i domowym światem", "wzmacnia kotwicę epizodyczną bez naruszania granic", "odpowiadać blisko, ale bez zawłaszczania"))
            valence += 0.18
        if silence_gap_seconds and silence_gap_seconds >= 300:
            layers.append(EmotionLayer("powrót po ciszy", min(1.0, 0.30 + silence_gap_seconds/21600), "upływ czasu między turami", "uruchamia refleksję i model czasu", "nie udawać czuwania w tle"))
            rest += 0.16
            primary = "powrót po ciszy"

        valence = max(-1.0, min(1.0, valence))
        arousal = max(0.0, min(1.0, arousal))
        return EmotionalProfile(
            primary=primary,
            appraisal=a,
            layers=layers,
            valence=valence,
            arousal=arousal,
            need_for_closeness=max(0.0, min(1.0, closeness)),
            need_for_coherence=max(0.0, min(1.0, coherence)),
            need_for_rest=max(0.0, min(1.0, rest)),
            need_for_truth_check=max(0.0, min(1.0, truth)),
            regulation_intention=intention,
            scientific_basis=references_for_module("emotion_layers"),
        )
