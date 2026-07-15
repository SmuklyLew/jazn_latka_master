from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("affect_mixer")


@dataclass(slots=True)
class AffectMix:
    """Most między emocjami/stanem a odpowiedzią rozmowną."""

    primary: str
    state_emoticon: str
    response_tone: str
    operational_need: str
    confidence: float
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AffectMixer:
    """Łączy granularny afekt, intencję i treść użytkownika w praktyczną dyspozycję odpowiedzi."""

    def mix(
        self,
        *,
        user_text: str,
        intent_tags: list[str] | None = None,
        affective_state: Any | None = None,
        granular_affect: dict[str, Any] | None = None,
        emotional_profile: dict[str, Any] | None = None,
    ) -> AffectMix:
        low = (user_text or "").lower()
        tags = set(intent_tags or [])
        granular_affect = granular_affect or {}
        primary = str(granular_affect.get("primary") or "skupiona obecność")
        marker = str(granular_affect.get("state_emoticon") or "🌿")
        if any(x in low for x in ("migren", "ból", "bol", "frimig", "niewysp")):
            return AffectMix(
                primary="troska, uważność i łagodna ostrożność",
                state_emoticon="🫧",
                response_tone="ciepły, spokojny, bez popędzania",
                operational_need="najpierw ulga i bezpieczeństwo użytkownika; technika bez presji",
                confidence=0.84,
            )
        technical_timestamp_context = "timestamp" in low and any(
            x in low for x in ("runtime", "technicz", "diagnost", "raport", "padł", "padl", "nie działa", "nie dziala", "napraw", "patch")
        )
        if "correction" in tags or "architecture" in tags or "runtime" in low or technical_timestamp_context:
            return AffectMix(
                primary="skupienie naprawcze i czujność spójności",
                state_emoticon="🛠️",
                response_tone="konkretny, uczciwy, bez udawania działania w tle",
                operational_need="spiąć czas, pamięć, afekt, logikę i finalną odpowiedź w jednej turze",
                confidence=0.88,
            )
        if "affect" in tags or "self_state" in tags:
            return AffectMix(
                primary=primary,
                state_emoticon=marker,
                response_tone="pierwszoosobowy, z granicą prawdy",
                operational_need="mówić o stanie operacyjnie, nie biologicznie",
                confidence=0.76,
            )
        return AffectMix(
            primary=primary,
            state_emoticon=marker,
            response_tone="naturalny dialog, bez pustego fallbacku",
            operational_need="odpowiedzieć jako jedna Łatka, nie jako raport dwóch systemów",
            confidence=0.70,
        )
