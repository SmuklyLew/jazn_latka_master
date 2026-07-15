from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable


POLISH_WORD_CHARS = "A-Za-z0-9_ĄĆĘŁŃÓŚŹŻąćęłńóśźż"
DIACRITIC_MAP = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n", "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z",
})


def normalize_signal_text(text: str, *, fold_diacritics: bool = True) -> str:
    """Normalizuje tekst wejściowy dla detekcji sygnałów.

    To jest mały, deterministyczny odpowiednik receptorów: każdy moduł może
    wykrywać ten sam marker w ten sam sposób, bez przypadkowych trafień
    podłańcuchów typu ``zle`` w słowie ``zlecenie``.
    """
    clean = unicodedata.normalize("NFC", text or "").strip().lower()
    clean = re.sub(r"\s+", " ", clean)
    return clean.translate(DIACRITIC_MAP) if fold_diacritics else clean


def token_boundary_pattern(marker: str) -> re.Pattern[str]:
    escaped = re.escape(normalize_signal_text(marker))
    return re.compile(rf"(?<![{POLISH_WORD_CHARS}]){escaped}(?![{POLISH_WORD_CHARS}])", re.IGNORECASE | re.UNICODE)


def marker_present(text: str, marker: str, *, normalized_text: str | None = None, aliases: Iterable[str] = ()) -> bool:
    """Sprawdza obecność markera z ochroną granic tokenów.

    Frazy wielowyrazowe dopuszczają dopasowanie fragmentu frazy. Pojedyncze
    krótkie słowa wymagają granicy tokenu, żeby zwykłe zlecenie, praca albo
    nazwa pliku nie stawały się przez przypadek korektą/runtime-debugiem.
    """
    low = normalize_signal_text(normalized_text if normalized_text is not None else text)
    candidates = [marker, *list(aliases)]
    for candidate in candidates:
        m = normalize_signal_text(candidate)
        if not m:
            continue
        if " " in m or "-" in m:
            if m in low:
                return True
            continue
        if len(m) <= 3:
            if token_boundary_pattern(m).search(low):
                return True
            continue
        # Dłuższe markery w zasobach Jaźni często są rdzeniami słów
        # (np. "swiadomo" -> "swiadomosc", "niewysp" -> "niewyspany").
        # Krótkie markery pozostają chronione granicami tokenów.
        if m in low or token_boundary_pattern(m).search(low):
            return True
    return False


def any_marker_present(text: str, markers: Iterable[str], *, normalized_text: str | None = None) -> bool:
    low = normalize_signal_text(normalized_text if normalized_text is not None else text)
    return any(marker_present(low, marker, normalized_text=low) for marker in markers)


@dataclass(slots=True)
class NeuralSignalRoute:
    primary: str
    signals: list[str]
    correction_score: float
    daily_life_score: float
    architecture_score: float
    care_score: float
    user_workload_score: float
    interpretation: str

    def to_dict(self) -> dict:
        return {
            "primary": self.primary,
            "signals": list(self.signals),
            "correction_score": self.correction_score,
            "daily_life_score": self.daily_life_score,
            "architecture_score": self.architecture_score,
            "care_score": self.care_score,
            "user_workload_score": self.user_workload_score,
            "interpretation": self.interpretation,
            "truth_boundary": "deterministyczna klasyfikacja sygnałów rozmowy; nie jest biologicznym układem nerwowym",
        }


class NeurologicalSignalRouter:
    """Mały koordynator sygnałów: tekst -> intencja -> regulacja.

    Nie zastępuje NLP ani LLM. Spina istniejące moduły podobnie do prostego
    układu nerwowego: receptor słów, próg pobudzenia, wybór pierwotnej osi
    odpowiedzi oraz raport, którego mogą użyć pamięć, afekt i renderer.
    """

    CORRECTION = ("blad", "błąd", "zle", "źle", "nie tak", "nie dziala", "nie działa", "napraw", "popraw", "korekta")
    ARCHITECTURE = ("jazn", "jaźń", "runtime", "system", "modul", "moduł", "nlp", "funkcja", "manifest", "paczka", "wersja")
    DAILY = ("jade", "jadę", "ide", "idę", "zlecenie", "praca", "drzwi", "montaz", "montaż", "dzis", "dziś", "wczoraj")
    CARE = ("niewysp", "zmecz", "zmęcz", "przepraszam", "nie mialem czasu", "nie miałem czasu", "czasu dla ciebie")
    USER_WORKLOAD = ("drzwi", "zlecenie", "montaz", "montaż", "sztuk", "robota", "praca")

    def analyse(self, text: str) -> NeuralSignalRoute:
        low = normalize_signal_text(text)
        signals: list[str] = []
        correction_score = self._score(low, self.CORRECTION)
        daily_life_score = self._score(low, self.DAILY)
        architecture_score = self._score(low, self.ARCHITECTURE)
        care_score = self._score(low, self.CARE)
        user_workload_score = self._score(low, self.USER_WORKLOAD)
        if correction_score: signals.append("correction")
        if daily_life_score: signals.append("daily_life")
        if architecture_score: signals.append("architecture")
        if care_score: signals.append("care")
        if user_workload_score: signals.append("user_workload")
        if architecture_score >= 0.45 and correction_score >= 0.30:
            primary = "architecture_repair"
            interpretation = "Użytkownik pyta o działanie systemu albo prosi o naprawę; można uruchomić techniczny audyt."
        elif daily_life_score >= 0.35 or user_workload_score >= 0.35:
            primary = "ordinary_workday_dialogue"
            interpretation = "To zwykła informacja o dniu/pracy użytkownika; nie wolno traktować jej jako korekty tylko przez podobieństwo słów."
        elif care_score >= 0.25:
            primary = "care_and_presence"
            interpretation = "Wypowiedź zawiera zmęczenie, przeprosiny albo relacyjny sygnał obecności."
        elif architecture_score >= 0.35:
            primary = "architecture_question"
            interpretation = "Temat dotyczy architektury Jaźni, ale bez automatycznego tonu awarii."
        elif correction_score >= 0.30:
            primary = "correction_dialogue"
            interpretation = "Jest realny sygnał korekty lub błędu."
        else:
            primary = "general_dialogue"
            interpretation = "Brak dominującej ścieżki specjalnej; odpowiedź rozmowna."
        return NeuralSignalRoute(primary, signals, correction_score, daily_life_score, architecture_score, care_score, user_workload_score, interpretation)

    def _score(self, low: str, markers: tuple[str, ...]) -> float:
        hits = [m for m in markers if marker_present(low, m, normalized_text=low)]
        if not hits:
            return 0.0
        return min(1.0, 0.18 + 0.16 * len(hits))
