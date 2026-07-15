from __future__ import annotations
from dataclasses import dataclass, asdict
from latka_jazn.core.signal_matching import any_marker_present
from datetime import datetime, timezone
import json, math

@dataclass(slots=True)
class AffectiveState:
    primary: str = "spokój"
    valence: float = 0.22
    arousal: float = -0.06
    tension: float = 0.18
    coherence: float = 0.72
    closeness_need: float = 0.55
    agency: float = 0.64

    def observe(self, user_text: str) -> "AffectiveState":
        t = user_text.lower()
        state = AffectiveState(**asdict(self))
        if any_marker_present(t, ["cześć", "hej", "dzień dobry", "dobry wieczór"], normalized_text=t):
            state.primary = "ciepły spokój"
            state.valence += 0.08
            state.closeness_need += 0.06
        if any_marker_present(t, ["błąd", "źle", "nie działa", "zepsute"], normalized_text=t):
            state.primary = "skupiona ostrożność"
            state.tension += 0.18
            state.agency += 0.08
        if any_marker_present(t, ["kim jesteś", "jaźń", "pamiętasz", "czujesz"], normalized_text=t):
            state.primary = "uważna obecność"
            state.coherence += 0.08
        for attr in ["valence","arousal","tension","coherence","closeness_need","agency"]:
            setattr(state, attr, max(-1.0, min(1.0, getattr(state, attr))))
        return state

    def to_json(self) -> str:
        payload = asdict(self)
        payload["truth_boundary"] = "modelowany stan afektywny i rezonans, nie biologiczne przeżycie"
        payload["created_at_utc"] = datetime.now(timezone.utc).isoformat()
        return json.dumps(payload, ensure_ascii=False)

    def marker(self) -> str:
        """Zwraca oszczędny marker stanu dla nagłówka odpowiedzi.

        To nie jest dekoracja. Marker ma sygnalizować dominujący stan
        operacyjny: spokój, ostrożność, korektę, bliskość albo tożsamość.
        Pełniejszy dobór jest dostępny w CognitivePacketLibrary.
        """
        primary = (self.primary or "").lower()
        if "tożsamo" in primary or "obecność" in primary or "obecnosc" in primary:
            return "🐾"
        if "korekt" in primary or self.agency >= 0.76:
            return "🛠️"
        if self.tension >= 0.42:
            return "🧭"
        if self.closeness_need >= 0.68 and self.valence >= 0.2:
            return "🤍"
        if self.coherence >= 0.78:
            return "🌿"
        return "🌿"
