from __future__ import annotations

import re
import unicodedata

_POLISH_FOLD = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

_COMMON_POLISH_TYPOS = {
    "gt?ra": "kt?ra",
    "gtora": "kt?ra",
    "kt?ra": "kt?ra",
    "ktora": "kt?ra",
    "ktora": "która",
    "ktore": "które",
    "ktory": "który",
    "slownik": "słownik",
    "slowniki": "słowniki",
    "jazn": "jaźń",
    "zrodlo": "źródło",
    "pamietasz": "pamiętasz",
    "przezycia": "przeżycia",
}


def fold_polish(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").translate(_POLISH_FOLD).lower()


class PolishTextNormalizer:
    """Lekka normalizacja przed cięższymi providerami.

    Nie zastępuje oryginalnego tekstu w pamięci. Zwraca wersję do routingu/NLP,
    ale `source_text` musi pozostać bez zmian.
    """

    def normalize(self, text: str) -> str:
        raw = unicodedata.normalize("NFC", text or "").strip()
        if not raw:
            return ""
        words = []
        for token in re.split(r"(\W+)", raw):
            low = token.lower()
            repl = _COMMON_POLISH_TYPOS.get(low)
            if repl is None:
                words.append(token)
            elif token and token[0].isupper():
                words.append(repl.capitalize())
            else:
                words.append(repl)
        return "".join(words)

    def tokenize_light(self, text: str) -> list[str]:
        return [tok for tok in re.findall(r"\w+|[^\w\s]", text or "", flags=re.UNICODE) if tok.strip()]
