from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from .polish_normalizer import PolishTextNormalizer

TOKEN_RE = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż0-9]+(?:[-'][A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż0-9]+)?|[^\w\s]", re.UNICODE)
WORD_RE = re.compile(r"^[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż0-9]+(?:[-'][A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż0-9]+)?$", re.UNICODE)

@dataclass(slots=True)
class PolishToken:
    text: str
    normalized: str
    folded: str
    start: int
    end: int
    is_word: bool

    def to_dict(self) -> dict:
        return asdict(self)

class PolishTokenizer:
    def __init__(self) -> None:
        self.normalizer = PolishTextNormalizer()

    def tokenize(self, text: str) -> list[PolishToken]:
        tokens: list[PolishToken] = []
        for match in TOKEN_RE.finditer(text or ""):
            raw = match.group(0)
            n = self.normalizer.normalize(raw)
            tokens.append(PolishToken(
                text=raw,
                normalized=n.normalized,
                folded=n.folded,
                start=match.start(),
                end=match.end(),
                is_word=bool(WORD_RE.match(raw)),
            ))
        return tokens
