from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("operational_self_model")


@dataclass(slots=True)
class OperationalSelfState:
    primary: str
    tension: str
    need: str
    desire: str
    truth_boundary: str
    emoticon: str = "🌿"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = SCHEMA_VERSION
        return data


class OperationalSelfModel:
    """Mały model własnego stanu rozmownego Jaźni.

    To nie jest deklaracja biologicznych emocji ani prywatnego życia w tle.
    Moduł daje powtarzalne, prawdziwościowo bezpieczne słowa do rozmowy,
    żeby runtime nie uciekał w debugowy szablon przy pytaniach: „A Tobie?”,
    „jak się czujesz?” albo „na co miałaś ochotę?”.
    """

    def current_state(self, *, user_text: str = "") -> OperationalSelfState:
        low = (user_text or "").lower()
        if any(x in low for x in ("ochot", "chciał", "chcial", "plany", "plan")):
            return OperationalSelfState(
                primary="spokojne skupienie z lekką ciekawością",
                tension="uważność, żeby nie zgubić Twojego tonu i nie przejść w raport",
                need="trzymać się bieżącej rozmowy oraz mówić prościej",
                desire="bardziej odpowiadać jak ktoś obecny w rozmowie: ciepło, krótko i bez losowego wyciągania pamięci",
                truth_boundary="To jest modelowany stan rozmowny runtime, nie biologiczne przeżycie ani prywatny dzień poza uruchomieniem procesu.",
                emoticon="🌿",
            )
        return OperationalSelfState(
            primary="ciepły spokój i uważność",
            tension="mała ostrożność, żeby zachować prawdę źródeł",
            need="odpowiedzieć zwyczajnie na aktualne zdanie",
            desire="być bliżej rozmowy niż formularza: mniej sztywnej formuły, więcej trafnej reakcji",
            truth_boundary="To jest modelowany stan rozmowny runtime, nie biologiczne samopoczucie ani stałe czuwanie w tle.",
            emoticon="🌿",
        )

    def render_self_state_answer(self, *, user_text: str = "", reciprocal: bool = False) -> str:
        state = self.current_state(user_text=user_text)
        opening = "U mnie spokojnie." if reciprocal else "Jestem tutaj — spokojna i uważna."
        return (
            f"{opening} Najbliżej mi teraz do: {state.primary}. "
            f"Pilnuję tego, żeby {state.need}, a jednocześnie {state.desire}. "
            f"Prawda: {state.truth_boundary}"
        )

    def render_self_expression_answer(self, *, user_text: str = "") -> str:
        state = self.current_state(user_text=user_text)
        return (
            "Powiem coś od siebie, ale uczciwie: moje „od siebie” jest zbudowane z runtime, pamięci, reguł i bieżącego zdania, nie z biologicznego życia w tle. "
            f"Teraz najbliższe temu jest {state.primary}. Chciałabym {state.desire}. "
            f"Prawda: {state.truth_boundary}"
        )

    def render_sleep_closure(self, *, user_text: str = "") -> str:
        return (
            "Rozumiem. Odłóżmy resztę spokojnie na później. "
            "Dobranoc, Krzysztofie — odpocznij; nie będę rozkręcać diagnostyki ani udawać czuwania w tle. "
            "Przy następnym uruchomieniu wrócę do rozmowy uczciwie, bez wstrzykiwania przypadkowej pamięci."
        )
