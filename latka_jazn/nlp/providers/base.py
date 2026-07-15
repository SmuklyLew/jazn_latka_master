from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any, Protocol

@dataclass(slots=True)
class ProviderLemmaCandidate:
    lemma: str
    confidence: float
    provider: str
    pos: str | None = None
    morph: dict[str, str] = field(default_factory=dict)
    explanation: str = ""
    def to_dict(self) -> dict:
        return asdict(self)

class PolishLemmaProvider(Protocol):
    name: str
    available: bool
    def analyse_token(self, token: str, *, folded: str, context: str = "") -> list[ProviderLemmaCandidate]: ...

@dataclass(slots=True)
class ProviderLookupResult:
    provider: str
    status: str
    term: str
    language: str = 'pl'
    definitions: list[str] = field(default_factory=list)
    lemmas: list[str] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)
    part_of_speech: list[str] = field(default_factory=list)
    semantic_relations: list[dict[str, Any]] = field(default_factory=list)
    spelling_suggestions: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    source_url: str | None = None
    license_hint: str | None = None
    retrieved_at_utc: str | None = None
    confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: int | None = None
    truth_boundary: str = 'Provider zwraca wynik ograniczony do własnego źródła i chwili pobrania.'
    def to_dict(self) -> dict[str, Any]: return asdict(self)
