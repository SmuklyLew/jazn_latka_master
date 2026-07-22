from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("response_language_guard")
_TOKEN_RE = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]+", re.UNICODE)
_POLISH_DIACRITICS = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
_POLISH_MARKERS = {
    "ale", "albo", "bardzo", "bez", "będzie", "być", "ci", "ciebie", "co", "czy", "dla",
    "dobrze", "do", "gdzie", "i", "jak", "jest", "jesteś", "już", "mam", "ma", "mnie",
    "może", "na", "nie", "od", "odpowiedz", "po", "polsku", "proszę", "się", "skąd", "tak",
    "teraz", "to", "ty", "w", "więc", "z", "że", "rozumiem", "model", "dostępny",
}
_ENGLISH_MARKERS = {
    "a", "and", "are", "can", "continue", "do", "great", "hear", "hello", "i", "if", "is",
    "it", "let", "me", "new", "okay", "or", "response", "sounds", "that", "the", "this", "to",
    "understand", "want", "what", "you", "your",
}


@dataclass(slots=True)
class LanguageAssessment:
    language: str
    confidence: float
    polish_score: float
    english_score: float
    token_count: int
    accepted_for_polish: bool
    reason: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_response_language(text: str, *, expected: str = "pl") -> LanguageAssessment:
    """Small deterministic guard for final candidate language.

    This is a conservative runtime gate, not a replacement for a trained LID
    model. Unknown/technical short text is accepted; clearly English prose is
    rejected when Polish is required. The design follows established LID
    practice of combining character and word evidence while avoiding a new
    mandatory model dependency in the core runtime.
    """
    raw = str(text or "").strip()
    tokens = [token.lower() for token in _TOKEN_RE.findall(raw)]
    if not tokens:
        return LanguageAssessment("unknown", 0.0, 0.0, 0.0, 0, True, "no_alphabetic_tokens")

    polish_hits = sum(token in _POLISH_MARKERS for token in tokens)
    english_hits = sum(token in _ENGLISH_MARKERS for token in tokens)
    diacritics = sum(ch in _POLISH_DIACRITICS for ch in raw)
    polish_score = float(polish_hits) + min(3.0, diacritics * 0.75)
    english_score = float(english_hits)

    if polish_score >= 2.0 and polish_score >= english_score + 1.0:
        language = "pl"
        reason = "polish_word_or_diacritic_evidence"
    elif english_score >= 3.0 and english_score >= polish_score + 2.0:
        language = "en"
        reason = "clear_english_function_word_evidence"
    elif polish_score > 0 and english_score > 0:
        language = "mixed"
        reason = "mixed_language_evidence"
    else:
        language = "unknown"
        reason = "insufficient_language_evidence"

    total = max(1.0, polish_score + english_score)
    confidence = round(abs(polish_score - english_score) / total, 4)
    accepted = expected != "pl" or language in {"pl", "unknown", "mixed"}
    return LanguageAssessment(language, confidence, polish_score, english_score, len(tokens), accepted, reason)


def user_explicitly_requested_non_polish(text: str) -> bool:
    low = str(text or "").lower()
    markers = (
        "po angielsku", "in english", "przetłumacz na angielski", "przetlumacz na angielski",
        "odpowiedz po niemiecku", "po niemiecku", "po hiszpańsku", "po hiszpansku",
    )
    return any(marker in low for marker in markers)
