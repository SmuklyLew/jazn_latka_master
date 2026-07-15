from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "template_origin/v14.6.10"

@dataclass(slots=True)
class TemplateOrigin:
    template_id: str
    template_purpose: str
    template_file: str
    template_line: int | None
    allowed_intents: list[str] = field(default_factory=list)
    forbidden_intents: list[str] = field(default_factory=list)
    deprecated_if: list[str] = field(default_factory=list)
    matched_signature: str | None = None
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "TemplateOrigin oznacza stały tekst lub refren jako szablon, nie jako samodzielną myśl runtime."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
