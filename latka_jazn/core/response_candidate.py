from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

SCHEMA_VERSION = "response_candidate/v14.8.4.004"
EVALUATION_SCHEMA_VERSION = "candidate_evaluation/v14.8.4.004"


@dataclass(slots=True)
class ResponseCandidate:
    candidate_id: str
    text: str
    source: str
    provider: str
    model: str
    status: str
    used_memory_item_ids: list[str]
    generation_reason: str
    source_origin: str = "runtime_fallback"
    endpoint_used: str | None = None
    adapter_response: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.schema_version = str(self.schema_version or SCHEMA_VERSION)
        self.candidate_id = _clean_identifier(self.candidate_id, fallback="candidate")
        self.text = _clean_text(self.text, fallback="")
        self.source = _clean_identifier(self.source, fallback="unknown_source")
        self.provider = _clean_text(self.provider, fallback="unknown")
        self.model = _clean_text(self.model, fallback="unknown")
        self.status = _clean_identifier(self.status, fallback="unknown")
        self.used_memory_item_ids = [_clean_identifier(item, fallback="memory_item") for item in self.used_memory_item_ids or []]
        self.generation_reason = _clean_text(self.generation_reason, fallback="unspecified")
        self.source_origin = _clean_identifier(self.source_origin, fallback=self.source)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CandidateEvaluation:
    candidate_id: str
    accepted: bool
    score: float
    reasons: list[str]
    violations: list[str]
    requires_repair: bool
    schema_version: str = EVALUATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.schema_version = str(self.schema_version or EVALUATION_SCHEMA_VERSION)
        self.candidate_id = _clean_identifier(self.candidate_id, fallback="candidate")
        self.accepted = bool(self.accepted)
        self.score = _clamp_score(self.score)
        self.reasons = _dedupe(self.reasons)
        self.violations = _dedupe(self.violations)
        self.requires_repair = bool(self.requires_repair or self.violations)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_text(value: Any, *, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def _clean_identifier(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z_.:/-]+", "_", text).strip("_")
    return text or fallback


def _dedupe(values: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        value = _clean_text(raw, fallback="")
        if value and value not in out:
            out.append(value)
    return out


def _clamp_score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number
