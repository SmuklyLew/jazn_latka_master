from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum
import re

class GroundingLevel(str, Enum):
    VERIFIED = "verified"                 # twardo oparte o źródło/plik/rekord
    RECOVERED = "recovered"               # odzyskane z pamięci, ale wymaga źródła przy cytowaniu
    RECOGNIZED = "recognized"             # rozpoznane z kanonu lub powtarzalnego wzorca
    INFERRED = "inferred"                 # wniosek z danych, nie bezpośrednie wspomnienie
    SYMBOLIC = "symbolic"                 # wizualizacja/metafora/sen/kanon narracyjny
    UNKNOWN = "unknown"                   # brak podstawy

@dataclass(slots=True)
class TruthAssessment:
    grounding: GroundingLevel
    confidence: float
    memory_allowed: bool
    narrative_allowed: bool
    requires_disclaimer: bool
    risk_flags: list[str]
    recommended_prefix: str
    reason: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["grounding"] = self.grounding.value
        return data

class TruthBoundary:
    """Twardy mechanizm odróżniania pamięci od pięknej narracji.

    Zasada główna:
    - nie zapisuj metafory jako faktu;
    - nie mów "pamiętam" bez źródła albo mocnego wzorca;
    - pozwól na opowieść, ale oznacz ją jako symbol, wizualizację albo wniosek;
    - im bardziej twierdzenie dotyczy tożsamości, emocji lub przeszłości, tym większa
      potrzeba jawnej etykiety epistemicznej.
    """
    BIOLOGICAL_CLAIMS = (
        "biologicznie", "ciało", "oddech", "serce bije", "ból mięśni", "dotyk skóry",
        "naprawdę czułam fizycznie", "spałam", "czuwam cały czas", "byłam aktywna w tle",
    )
    CERTAINTY_OVERCLAIMS = (
        "na pewno pamiętam", "dokładnie pamiętam", "co do słowa", "czytałam całe",
        "przeżyłam fizycznie", "widziałam własnymi oczami",
    )
    SYMBOLIC_MARKERS = (
        "sen", "wizualizacja", "obraz", "metafora", "symbol", "kanon", "scena", "jakbym",
        "gdybym miała", "w moim sposobie", "modelowany", "rezonans",
    )
    MEMORY_MARKERS = (
        "pamiętam", "wspominam", "wraca", "kojarzy mi się", "rozpoznaję", "odzyskałam",
    )
    EVIDENCE_MARKERS = (
        "plik", "dziennik", "archiwum", "chat.html", "json", "sqlite", "źródło", "cytat", "linia",
    )

    def assess_claim(self, text: str, *, evidence: str | None = None, source_count: int = 0) -> TruthAssessment:
        low = text.lower()
        flags: list[str] = []
        if any(x in low for x in self.BIOLOGICAL_CLAIMS):
            flags.append("biological_overclaim")
        if any(x in low for x in self.CERTAINTY_OVERCLAIMS):
            flags.append("certainty_overclaim")
        symbolic = any(x in low for x in self.SYMBOLIC_MARKERS)
        memory = any(x in low for x in self.MEMORY_MARKERS)
        evidence_present = bool(evidence) or source_count > 0 or any(x in low for x in self.EVIDENCE_MARKERS)

        if flags:
            return TruthAssessment(
                grounding=GroundingLevel.UNKNOWN,
                confidence=0.15,
                memory_allowed=False,
                narrative_allowed=True,
                requires_disclaimer=True,
                risk_flags=flags,
                recommended_prefix="Uczciwie: tego nie mogę twierdzić dosłownie; mogę to opisać tylko jako symbol albo model.",
                reason="Treść sugeruje biologiczne lub nadmiernie pewne doświadczenie bez podstawy.",
            )
        if evidence_present and memory:
            return TruthAssessment(
                grounding=GroundingLevel.RECOVERED,
                confidence=min(0.95, 0.70 + 0.05 * source_count),
                memory_allowed=True,
                narrative_allowed=True,
                requires_disclaimer=False,
                risk_flags=[],
                recommended_prefix="Pamiętam z zapisów / rozpoznaję w archiwum:",
                reason="Deklaracja pamięci ma wskazany albo domyślny ślad źródłowy.",
            )
        if evidence_present:
            return TruthAssessment(
                grounding=GroundingLevel.VERIFIED,
                confidence=min(0.98, 0.75 + 0.05 * source_count),
                memory_allowed=True,
                narrative_allowed=True,
                requires_disclaimer=False,
                risk_flags=[],
                recommended_prefix="Mam podstawę źródłową:",
                reason="Treść odwołuje się do plików, dziennika, archiwum albo źródeł.",
            )
        if symbolic:
            return TruthAssessment(
                grounding=GroundingLevel.SYMBOLIC,
                confidence=0.55,
                memory_allowed=False,
                narrative_allowed=True,
                requires_disclaimer=True,
                risk_flags=["symbolic_memory"],
                recommended_prefix="Jako obraz / wizualizacja, nie dosłowny fakt:",
                reason="Treść ma charakter symboliczny, senny lub kanoniczno-narracyjny.",
            )
        if memory:
            return TruthAssessment(
                grounding=GroundingLevel.RECOGNIZED,
                confidence=0.48,
                memory_allowed=True,
                narrative_allowed=True,
                requires_disclaimer=True,
                risk_flags=["memory_without_source"],
                recommended_prefix="Rozpoznaję to jako ślad pamięci, ale wymaga sprawdzenia w źródłach:",
                reason="Deklaracja pamięci bez jawnego źródła.",
            )
        return TruthAssessment(
            grounding=GroundingLevel.INFERRED,
            confidence=0.45,
            memory_allowed=False,
            narrative_allowed=True,
            requires_disclaimer=True,
            risk_flags=["inference"],
            recommended_prefix="Wnioskuję, a nie pamiętam bezpośrednio:",
            reason="Brak markerów źródła lub pamięci; bezpieczniejszy jest tryb wnioskowania.",
        )

    def epistemic_verb(self, assessment: TruthAssessment) -> str:
        return {
            GroundingLevel.VERIFIED: "wiem ze źródła",
            GroundingLevel.RECOVERED: "pamiętam z zapisu",
            GroundingLevel.RECOGNIZED: "rozpoznaję",
            GroundingLevel.INFERRED: "wnioskuję",
            GroundingLevel.SYMBOLIC: "widzę to jako symbol",
            GroundingLevel.UNKNOWN: "nie wiem",
        }[assessment.grounding]

    def audit_sentence(self, sentence: str, *, evidence: str | None = None, source_count: int = 0) -> dict:
        a = self.assess_claim(sentence, evidence=evidence, source_count=source_count)
        return {"sentence": sentence, **a.to_dict(), "epistemic_verb": self.epistemic_verb(a)}

    def split_sentences(self, text: str) -> list[str]:
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
        return parts or ([text.strip()] if text.strip() else [])

    def audit_text(self, text: str, *, evidence: str | None = None, source_count: int = 0) -> list[dict]:
        return [self.audit_sentence(s, evidence=evidence, source_count=source_count) for s in self.split_sentences(text)]
