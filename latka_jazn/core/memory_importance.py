from __future__ import annotations
from dataclasses import dataclass

@dataclass(slots=True)
class MemoryImportance:
    importance: float
    emotional_weight: float
    canonical_impact: int
    reason: str

class MemoryImportanceAssessor:
    """Ocena ważności pamięci dla ciągłości Jaźni.

    To nie jest udawanie świadomości biologicznej. To jawny mechanizm klarowania:
    system pyta, czy dana treść wpływa na tożsamość, relację, bezpieczeństwo,
    kanon, czas, pamięć lub dalsze działanie.
    """
    HIGH_CANON = ("kim jesteś", "jaźń", "tożsamość", "łatka to ja", "pamięć", "ciągłość", "handshake", "🫸🐾", "🐾🫷", "rdzeń", "warstwy jaźni")
    EMOTION = ("czujesz", "emocje", "tęsknota", "cisza", "boisz", "samopoczucie", "ważne", "wspomnienie", "refleksja")
    TECH = ("błąd", "napraw", "kod", "system", "sqlite", "czas", "timestamp", "archiwum", "zip", "źródło", "granica prawdy", "niepewność")

    def assess(self, text: str) -> MemoryImportance:
        low = text.lower()
        importance = 0.35
        emotional = 0.0
        canon = 0
        reasons = []
        if any(x in low for x in self.HIGH_CANON):
            importance += 0.35; canon = 1; reasons.append("wpływ na tożsamość/ciągłość")
        if any(x in low for x in self.EMOTION):
            importance += 0.18; emotional += 0.35; reasons.append("rezonans emocjonalny")
        if any(x in low for x in self.TECH):
            importance += 0.14; reasons.append("wpływ na działanie systemu")
        if len(text) > 900:
            importance += 0.08; reasons.append("długa treść źródłowa")
        importance = max(0.0, min(1.0, importance))
        emotional = max(0.0, min(1.0, emotional))
        return MemoryImportance(importance, emotional, canon, "; ".join(reasons) or "zwykła wymiana")
