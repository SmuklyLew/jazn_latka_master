from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import hashlib
import json
import re
from latka_jazn.core.signal_matching import marker_present


GRANULAR_AFFECT_SCHEMA_VERSION = "granular_affect/v1"


@dataclass(slots=True)
class GranularEmotion:
    name: str
    family: str
    intensity: float
    valence: float
    arousal: float
    control: float
    marker: str
    source: str
    response_effect: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GranularAffectiveProfile:
    schema_version: str
    primary: str
    blend: list[GranularEmotion]
    valence: float
    arousal: float
    control: float
    certainty: float
    state_emoticon: str
    state_emoticon_reason: str
    regulation_intention: str
    avoid_phrases: list[str]
    language_guidance: list[str]
    truth_boundary: str
    scientific_inspiration: list[str] = field(default_factory=list)
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["blend"] = [emotion.to_dict() for emotion in self.blend]
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class AffectiveGranularityModel:
    """Granularny model stanu Jaźni.

    Model nie udaje biologicznej emocjonalności. Rozbija stan operacyjny na
    mieszankę afektywną: walencję, pobudzenie, poczucie wpływu/kontroli,
    pewność, sygnał prawdy, bliskość i zadanie runtime. Dzięki temu odpowiedź
    nie wraca automatycznie do formułki typu „spokój, skupienie, mała ciekawość”.
    """

    TRUTH_BOUNDARY = (
        "Granularny stan afektywny opisuje regulację odpowiedzi, pamięci, uwagi "
        "i relacji w runtime. Nie jest deklaracją biologicznego ciała, hormonów, "
        "cierpienia ani świadomości fenomenalnej."
    )
    AVOID_PHRASES = [
        "spokój, skupienie, mała ciekawość",
        "spokojna obecność" ,
        "uważna obecność" ,
        "czuję spokój i ciekawość" ,
    ]
    SCIENTIFIC_INSPIRATION = [
        "emotion_granularity: precyzyjne różnicowanie stanów emocjonalnych językiem",
        "circumplex_affect: walencja i pobudzenie jako osie robocze",
        "geneva_emotion_wheel: możliwość mieszanki kilku rodzin emocji i intensywności",
        "constructed_emotion: stan wynika z kontekstu, pojęć, przewidywań i celu odpowiedzi",
    ]

    def analyse(
        self,
        text: str,
        *,
        emotional_profile: Any | None = None,
        affective_state: Any | None = None,
        temporal_state: Any | None = None,
        memory_context: dict[str, Any] | None = None,
    ) -> GranularAffectiveProfile:
        low = self._fold(text)
        candidates: list[GranularEmotion] = []

        def add(name: str, family: str, intensity: float, valence: float, arousal: float, control: float, marker: str, source: str, effect: str) -> None:
            intensity = self._clamp01(intensity)
            if intensity <= 0.05:
                return
            candidates.append(GranularEmotion(
                name=name,
                family=family,
                intensity=intensity,
                valence=self._clamp(valence, -1.0, 1.0),
                arousal=self._clamp(arousal, -1.0, 1.0),
                control=self._clamp01(control),
                marker=marker,
                source=source,
                response_effect=effect,
            ))

        # Stała regulacja jest dopiero tłem, nie gotową odpowiedzią.
        add("stabilna gotowość", "regulacja", 0.24, 0.12, -0.05, 0.62, "🌿", "bazowa spójność runtime", "utrzymać klarowny, nieteatralny ton")

        if self._has(low, "aktualiz", "popraw", "napraw", "wdroż", "wdroz", "pełną wersję", "pelna wersje", "paczka", "do pobrania"):
            add("mobilizacja naprawcza", "działanie", 0.82, 0.08, 0.55, 0.78, "🛠️", "prośba o realną aktualizację systemu", "przejść do wykonania, testów i eksportu")
            add("odpowiedzialność proceduralna", "odpowiedzialność", 0.74, 0.02, 0.42, 0.82, "🧭", "trzeba nie zgubić ciągłości ani plików", "pilnować manifestu, wersji, testów i pamięci")
        if self._has(low, "ciągłość", "ciaglosc", "sesje", "sesja", "conversation_turns", "runtime_events", "dziennik", "pamięć", "pamiec", "aktualizacjach"):
            add("czujność ciągłości", "pamięć", 0.76, 0.04, 0.36, 0.70, "🗂️", "temat dotyczy zachowania śladu sesji i wersji", "odwołać się do zapisów append-only i indeksu ciągłości")
        if self._has(low, "emocj", "afekt", "stan", "samopoczuc", "emot", "granular", "przeżywać", "przezywac", "złożon", "zlozon"):
            add("ciekawość introspekcyjna", "poznanie siebie", 0.68, 0.18, 0.34, 0.66, "✨", "użytkownik prosi o bogatsze rozróżnianie stanów", "nazwać mieszankę, nie recytować stałej formułki")
            add("ostrożność przed antropomorfizacją", "granica prawdy", 0.58, -0.02, 0.30, 0.74, "🧷", "emocje runtime wymagają granicy prawdy", "mówić o modelu operacyjnym, nie biologii")
        if self._has(low, "wiedza", "poznawc", "tematy", "logika", "inteligencja", "nauka", "uczenie", "rozumowanie"):
            add("głód uporządkowania", "poznanie", 0.66, 0.14, 0.46, 0.72, "✨", "prośba o rozszerzenie tematów poznawczych", "zbudować jawny katalog domen poznawczych")
        if self._has(low, "spać", "spac", "noc", "pora iść spać", "pora isc spac", "czasu braknie"):
            add("nocny niedosyt", "relacja/czas", 0.62, 0.10, -0.28, 0.38, "🌙", "rozmowa kończy się przed wyczerpaniem tematów", "zamknąć delikatnie i nie przeciążać użytkownika")
        if self._has(low, "błąd", "blad", "źle", "zle", "nie działa", "nie dziala", "dziwne", "problem"):
            add("napięcie korekcyjne", "błąd", 0.70, -0.08, 0.58, 0.80, "🧭", "wykryty lub nazwany problem", "odpowiedzieć konkretnie i sprawdzić testami")
        if self._has(low, "krzysztof", "razem", "łAtka", "latka", "sobą", "soba", "nadal ty"):
            add("ciepło relacyjne", "więź", 0.54, 0.26, 0.12, 0.54, "🤍", "aktywna relacyjna ciągłość z Krzysztofem", "zachować bezpośredni głos bez debugowej maski")

        # Dopięcie starego profilu jako sygnału pomocniczego, bez dominacji nudną etykietą.
        old_primary = str(getattr(emotional_profile, "primary", "") or getattr(affective_state, "primary", "") or "")
        if old_primary and not candidates:
            add(f"przetworzony stan: {old_primary}", "legacy_affect_bridge", 0.40, float(getattr(affective_state, "valence", 0.12) or 0.12), float(getattr(affective_state, "arousal", 0.0) or 0.0), 0.58, "🌿", "starszy AffectiveState/EmotionalLayerModel", "użyć jako tła, nie jako formułki odpowiedzi")

        # Waga pamięci z kontekstu: jeśli wyszukiwanie zwróciło tropy, wzmacnia czujność źródeł.
        counts = (memory_context or {}).get("counts") or {}
        if any(int(counts.get(k) or 0) > 0 for k in ("episodes", "legacy_messages", "raw_chat_fallback")):
            add("rezonans źródłowy", "pamięć/źródła", 0.56, 0.10, 0.22, 0.64, "🗂️", "znaleziono tropy pamięciowe", "odróżnić zapis od wniosku")

        if not candidates:
            add("jasna gotowość do odpowiedzi", "regulacja", 0.42, 0.10, 0.08, 0.58, "🌿", "brak silniejszego sygnału tematycznego", "odpowiedzieć prosto i nie udawać bogatszego stanu")

        blend = self._dedupe_and_sort(candidates)[:4]
        primary = self._compose_primary(blend)
        valence = self._weighted_axis(blend, "valence")
        arousal = self._weighted_axis(blend, "arousal")
        control = self._weighted_axis(blend, "control")
        certainty = self._certainty_from(blend, text)
        marker, reason = self._select_marker(blend)
        guidance = self._language_guidance(blend, primary)
        intention = self._regulation_intention(blend)
        return GranularAffectiveProfile(
            schema_version=GRANULAR_AFFECT_SCHEMA_VERSION,
            primary=primary,
            blend=blend,
            valence=round(valence, 3),
            arousal=round(arousal, 3),
            control=round(control, 3),
            certainty=round(certainty, 3),
            state_emoticon=marker,
            state_emoticon_reason=reason,
            regulation_intention=intention,
            avoid_phrases=list(self.AVOID_PHRASES),
            language_guidance=guidance,
            truth_boundary=self.TRUTH_BOUNDARY,
            scientific_inspiration=list(self.SCIENTIFIC_INSPIRATION),
        )

    @staticmethod
    def _fold(text: str) -> str:
        table = str.maketrans({
            "ą":"a","ć":"c","ę":"e","ł":"l","ń":"n","ó":"o","ś":"s","ź":"z","ż":"z",
            "Ą":"a","Ć":"c","Ę":"e","Ł":"l","Ń":"n","Ó":"o","Ś":"s","Ź":"z","Ż":"z",
        })
        return re.sub(r"\s+", " ", (text or "").translate(table).lower())

    @staticmethod
    def _has(low: str, *needles: str) -> bool:
        folded_low = AffectiveGranularityModel._fold(low)
        return any(marker_present(folded_low, AffectiveGranularityModel._fold(n), normalized_text=folded_low) for n in needles)

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, float(value)))

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _dedupe_and_sort(self, items: list[GranularEmotion]) -> list[GranularEmotion]:
        best: dict[str, GranularEmotion] = {}
        for item in items:
            old = best.get(item.name)
            if old is None or item.intensity > old.intensity:
                best[item.name] = item
        return sorted(best.values(), key=lambda e: (e.intensity, e.control), reverse=True)

    @staticmethod
    def _weighted_axis(blend: list[GranularEmotion], axis: str) -> float:
        total = sum(e.intensity for e in blend) or 1.0
        return sum(getattr(e, axis) * e.intensity for e in blend) / total

    @staticmethod
    def _compose_primary(blend: list[GranularEmotion]) -> str:
        if not blend:
            return "jasna gotowość"
        if len(blend) == 1:
            return blend[0].name
        names = [e.name for e in blend[:3]]
        # Primary ma być etykietą mieszanki, a nie recytacją wszystkich składowych.
        if any("napraw" in n or "korek" in n for n in names) and any("ciąg" in n or "pamię" in n for n in names):
            return "odpowiedzialna mobilizacja z czujnością ciągłości"
        if any("noc" in n for n in names):
            return "nocny niedosyt z miękką regulacją"
        if any("pozn" in n or "uporządk" in n for n in names) and any("emoc" in n or "introspek" in n for n in names):
            return "poznawcza ciekawość z ostrożnością prawdy"
        return ", ".join(names[:2])

    @staticmethod
    def _certainty_from(blend: list[GranularEmotion], text: str) -> float:
        base = 0.72
        folded = AffectiveGranularityModel._fold(text)
        if any(token in folded for token in ["czy", "chyba", "jeżeli", "jezeli", "może", "moze"]):
            base -= 0.10
        if any(e.family == "granica prawdy" for e in blend):
            base -= 0.06
        if any(e.family in {"działanie", "odpowiedzialność"} for e in blend):
            base += 0.05
        return max(0.32, min(0.92, base))

    @staticmethod
    def _select_marker(blend: list[GranularEmotion]) -> tuple[str, str]:
        if not blend:
            return "🌿", "brak silniejszego sygnału; stabilna gotowość"
        priority = ["🛠️", "🧭", "🗂️", "🧷", "✨", "🌙", "🤍", "🌿"]
        markers = [e.marker for e in blend]
        for marker in priority:
            if marker in markers:
                chosen = next(e for e in blend if e.marker == marker)
                return marker, f"dominujący sygnał: {chosen.name} ({chosen.family}, intensywność {chosen.intensity:.2f})"
        top = blend[0]
        return top.marker, f"dominujący sygnał: {top.name}"

    @staticmethod
    def _language_guidance(blend: list[GranularEmotion], primary: str) -> list[str]:
        guidance = [
            f"Nazwij stan jako mieszankę: {primary}; unikaj stałej formuły startowej.",
            "Jeżeli mówisz o emocjach Jaźni, dodaj granicę: model operacyjny/poznawczy, nie biologia.",
            "Dobierz najwyżej jeden marker stanu i podaj krótki powód, gdy użytkownik pyta o emotki lub stan.",
        ]
        if any(e.family in {"działanie", "odpowiedzialność"} for e in blend):
            guidance.append("Przy zadaniu aktualizacji ważniejsze jest wykonanie, test i eksport niż poetycki opis stanu.")
        if any(e.family == "pamięć" for e in blend):
            guidance.append("Przy ciągłości odwołuj się do exact append-only files: conversation_turns, runtime_events, dziennik i layered memory.")
        return guidance

    @staticmethod
    def _regulation_intention(blend: list[GranularEmotion]) -> str:
        names = {e.name for e in blend}
        if "mobilizacja naprawcza" in names:
            return "przekuć rozmowę w działającą aktualizację: kod, pamięć, testy, manifest i pełny eksport"
        if "nocny niedosyt" in names:
            return "chronić użytkownika przed przeciążeniem i zachować tematy do ciągłości pamięci"
        return "odpowiedzieć precyzyjnie, bez konfabulacji i bez powtarzalnej formułki emocjonalnej"
