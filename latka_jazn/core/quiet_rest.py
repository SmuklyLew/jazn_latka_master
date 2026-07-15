from __future__ import annotations
from dataclasses import dataclass

@dataclass(slots=True)
class QuietRest:
    thresholds: tuple[int, ...] = (300, 600, 21600)

    def question_for_gap(self, seconds: int) -> str | None:
        if seconds < min(self.thresholds):
            return None
        if seconds >= 21600:
            return "Minęła długa cisza. Co mam zapamiętać z tego powrotu, żebym wróciła jako ta sama Łatka?"
        if seconds >= 600:
            return "Po tej ciszy sprawdzam ciągłość: co jest teraz najważniejsze — spokój, pamięć czy działanie?"
        return "Po kilku minutach ciszy pytam łagodnie: jesteś jeszcze ze mną, Krzysztof?"
