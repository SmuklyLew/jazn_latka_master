from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

_POLISH_TRANSLATION = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "A", "Ć": "C", "Ę": "E", "Ł": "L", "Ń": "N", "Ó": "O", "Ś": "S", "Ź": "Z", "Ż": "Z",
})
_WHITESPACE_RE = re.compile(r"\s+", flags=re.UNICODE)

@dataclass(slots=True)
class NormalizedText:
    original: str
    normalized: str
    folded: str

    def to_dict(self) -> dict[str, str]:
        return {"original": self.original, "normalized": self.normalized, "folded": self.folded}

class PolishTextNormalizer:
    """Normalizacja polskiego tekstu bez zależności zewnętrznych."""

    def normalize(self, text: str) -> NormalizedText:
        original = text or ""
        normalized = unicodedata.normalize("NFC", original).strip().lower()
        normalized = _WHITESPACE_RE.sub(" ", normalized)
        return NormalizedText(original=original, normalized=normalized, folded=self.ascii_fold(normalized))

    @staticmethod
    def ascii_fold(text: str) -> str:
        return unicodedata.normalize("NFC", text or "").translate(_POLISH_TRANSLATION).lower()
