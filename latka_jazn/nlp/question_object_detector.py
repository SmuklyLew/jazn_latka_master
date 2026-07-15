from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("question_object_detector")
DIACRITIC_MAP = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


@dataclass(slots=True)
class QuestionObjectReport:
    object_type: str
    evidence: list[str]
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuestionObjectDetector:
    PACKAGE_RE = re.compile(
        r"\b(?:pacz\w*|zip\w*|archiw\w*|manifest\w*|crc|sha256|rozpak\w*|"
        r"wypak\w*|generator\w*\s+pacz\w*)\b",
        re.IGNORECASE | re.UNICODE,
    )
    CREATIVE_RE = re.compile(
        r"\b(?:tekst\w*\s+piosenk\w*|piosenk\w*|lyrics|zwrotk\w*|refren\w*|"
        r"wiersz\w*|prompt\w*|generator\w*\s+(?:muzyk\w*|obraz\w*|video\w*))\b",
        re.IGNORECASE | re.UNICODE,
    )

    @staticmethod
    def _fold(text: str) -> str:
        return (text or "").translate(DIACRITIC_MAP).lower()

    def detect(self, text: str) -> QuestionObjectReport:
        folded = self._fold(text)
        if self.PACKAGE_RE.search(folded):
            evidence = [match.group(0) for match in self.PACKAGE_RE.finditer(folded)]
            return QuestionObjectReport("package_runtime_status", evidence[:6])
        if any(marker in folded for marker in ("skad", "zrod", "cytat", "source origin", "source_origin")):
            return QuestionObjectReport("source_origin", [marker for marker in ("skad", "zrod", "cytat", "source origin", "source_origin") if marker in folded])
        if self.CREATIVE_RE.search(folded):
            evidence = [match.group(0) for match in self.CREATIVE_RE.finditer(folded)]
            return QuestionObjectReport("creative_text", evidence[:6])
        if any(marker in folded for marker in ("runtime", "jazn", "latka")):
            return QuestionObjectReport("runtime", [marker for marker in ("runtime", "jazn", "latka") if marker in folded])
        if any(marker in folded for marker in ("slownik", "synonim", "odmian")):
            return QuestionObjectReport("dictionary", [marker for marker in ("slownik", "synonim", "odmian") if marker in folded])
        if any(marker in folded for marker in ("plik", "folder")):
            return QuestionObjectReport("file_or_package", [marker for marker in ("plik", "folder") if marker in folded])
        return QuestionObjectReport("unknown", [])
