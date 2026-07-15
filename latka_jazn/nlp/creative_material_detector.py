from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("creative_material_detector")
DIACRITIC_MAP = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


@dataclass(slots=True)
class CreativeMaterialReport:
    creative_material_present: bool
    material_kind: str
    preserve_default: bool
    evidence: list[str]
    schema_version: str = SCHEMA_VERSION
    negative_evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    ambiguous: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CreativeMaterialDetector:
    """Rozpoznaje materiał twórczy bez uznawania słowa „generator” za dowód.

    ``generator`` jest terminem domenowo niejednoznacznym. Staje się sygnałem
    twórczym dopiero, gdy ma jawny obiekt twórczy (muzyka, obraz, piosenka,
    prompt itp.). Paczka/ZIP/runtime są ujemnym kontekstem dla tej klasy.
    """

    CREATIVE_OBJECT_RE = re.compile(
        r"\b(?:piosenk\w*|lyrics|zwrotk\w*|refren\w*|wiersz\w*|muzyk\w*|"
        r"obraz\w*|grafik\w*|video\w*|film\w*|opowiad\w*|suno|midjourney|dall-?e)\b",
        re.IGNORECASE | re.UNICODE,
    )
    CREATIVE_ACTION_RE = re.compile(
        r"\b(?:napisz\w*|stworz\w*|przygotuj\w*|przerob\w*|zredaguj\w*|"
        r"sformatuj\w*|zachowaj\w*)\b|\bnie zmieniaj\b|\bbez zmian\b",
        re.IGNORECASE | re.UNICODE,
    )
    PACKAGE_RE = re.compile(
        r"\b(?:pacz\w*|zip\w*|archiw\w*|manifest\w*|crc|sha256|runtime\w*|"
        r"daemon\w*|sqlite\w*|rozpak\w*|wypak\w*)\b",
        re.IGNORECASE | re.UNICODE,
    )
    STRUCTURE_RE = re.compile(
        r"\[(?:chorus|verse|bridge|refren|zwrotka)[^\]]*\]",
        re.IGNORECASE | re.UNICODE,
    )

    @staticmethod
    def _fold(text: str) -> str:
        return (text or "").translate(DIACRITIC_MAP).lower()

    def detect(self, text: str) -> CreativeMaterialReport:
        source = text or ""
        folded = self._fold(source)
        lines = [line for line in source.splitlines() if line.strip()]
        evidence: list[str] = []
        negative: list[str] = []

        structured = bool(self.STRUCTURE_RE.search(source))
        lyrics_marker = bool(re.search(r"\b(?:lyrics|chorus|verse|bridge)\b", folded))
        creative_object = bool(self.CREATIVE_OBJECT_RE.search(folded))
        creative_action = bool(self.CREATIVE_ACTION_RE.search(folded))
        package_context = bool(self.PACKAGE_RE.search(folded))
        prompt_marker = bool(re.search(r"\bprompt\w*\b", folded, flags=re.UNICODE))
        generator_marker = bool(re.search(r"\bgenerator\w*\b", folded, flags=re.UNICODE))
        multiline_creative = len(lines) > 10 and (creative_object or structured or lyrics_marker)

        if package_context:
            negative.append("package_runtime_context")
        if generator_marker and not (creative_object or prompt_marker):
            negative.append("generator_without_creative_object")

        if structured or lyrics_marker or multiline_creative:
            if structured:
                evidence.append("structured_lyrics_tags")
            if lyrics_marker:
                evidence.append("lyrics_vocabulary")
            if multiline_creative:
                evidence.append("multiline_creative_material")
            confidence = 0.94 if not package_context else 0.68
            return CreativeMaterialReport(
                True,
                "lyrics_or_structured_text",
                True,
                evidence,
                negative_evidence=negative,
                confidence=confidence,
                ambiguous=package_context,
            )

        if prompt_marker and (creative_action or creative_object):
            evidence.append("explicit_prompt_instruction")
            if creative_object:
                evidence.append("creative_prompt_object")
            confidence = 0.91 if not package_context else 0.62
            return CreativeMaterialReport(
                confidence >= 0.65,
                "creative_prompt_instruction" if confidence >= 0.65 else "ambiguous_prompt_instruction",
                True,
                evidence,
                negative_evidence=negative,
                confidence=confidence,
                ambiguous=package_context,
            )

        if generator_marker and creative_object and creative_action and not package_context:
            evidence.extend(["generator_grounded_by_creative_object", "creative_transformation_action"])
            return CreativeMaterialReport(
                True,
                "creative_generator_instruction",
                True,
                evidence,
                negative_evidence=negative,
                confidence=0.90,
                ambiguous=False,
            )

        if creative_object and creative_action and not package_context:
            evidence.extend(["creative_object", "creative_transformation_action"])
            return CreativeMaterialReport(
                True,
                "creative_text_instruction",
                True,
                evidence,
                negative_evidence=negative,
                confidence=0.84,
                ambiguous=False,
            )

        return CreativeMaterialReport(
            False,
            "none",
            False,
            evidence,
            negative_evidence=negative,
            confidence=0.05 if package_context else 0.20,
            ambiguous=generator_marker and not (creative_object or prompt_marker),
        )
