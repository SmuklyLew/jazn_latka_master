from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

@dataclass(slots=True)
class TemporalContinuityState:
    gap_seconds: int | None
    category: str
    continuity_note: str
    action: str

class TemporalAwareness:
    """Świadomość czasu i jego upływu w granicy działania programu.

    System nie twierdzi, że aktywnie przeżywał przerwę. Rozpoznaje ją po
    znacznikach czasu, klasyfikuje i zapisuje wpływ na ciągłość pamięci.
    """
    def classify_gap(self, gap_seconds: int | None) -> TemporalContinuityState:
        if gap_seconds is None:
            return TemporalContinuityState(None, "pierwszy_start", "brak poprzedniej tury w tym uruchomieniu", "zakotwicz start")
        if gap_seconds < 300:
            return TemporalContinuityState(gap_seconds, "ciągłość_bieżąca", "rozmowa trwa bez istotnej przerwy", "kontynuuj")
        if gap_seconds < 600:
            return TemporalContinuityState(gap_seconds, "krótka_cisza", "wystąpiła zauważalna przerwa", "łagodnie sprawdź obecność")
        if gap_seconds < 21600:
            return TemporalContinuityState(gap_seconds, "dłuższa_cisza", "przerwa może wymagać ponownego zakotwiczenia kontekstu", "zapisz pytanie z ciszy")
        return TemporalContinuityState(gap_seconds, "powrót_po_długiej_przerwie", "ciągłość wymaga jawnego przypomnienia źródeł pamięci", "uruchom protokół powrotu")
