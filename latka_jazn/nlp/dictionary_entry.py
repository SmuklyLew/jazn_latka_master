from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any
SCHEMA_VERSION="dictionary_entry/v14.6.10"

@dataclass(slots=True)
class LexicalSource:
    provider: str
    source_url: str | None = None
    license_hint: str | None = None
    retrieved_at_utc: str | None = None
    cache_status: str = "not_checked"
    confidence: float = 0.0
    truth_boundary: str = "Wynik leksykalny jest ważny tylko dla wskazanego źródła i czasu pobrania."
    def to_dict(self) -> dict[str, Any]: return asdict(self)

@dataclass(slots=True)
class ProviderStatus:
    provider: str
    status: str
    message: str | None = None
    source_url: str | None = None
    elapsed_ms: int | None = None
    def to_dict(self) -> dict[str, Any]: return asdict(self)

@dataclass(slots=True)
class DictionaryLookupResult:
    term: str
    normalized_term: str
    language: str='pl'
    lemma_candidates: list[str]=field(default_factory=list)
    pos_candidates: list[str]=field(default_factory=list)
    definitions: list[str]=field(default_factory=list)
    inflection: list[str]=field(default_factory=list)
    synonyms: list[str]=field(default_factory=list)
    antonyms: list[str]=field(default_factory=list)
    hypernyms: list[str]=field(default_factory=list)
    hyponyms: list[str]=field(default_factory=list)
    examples: list[str]=field(default_factory=list)
    source_name: str='none'
    source_url_or_id: str|None=None
    retrieved_at_utc: str|None=None
    license_note: str|None=None
    confidence: float=0.0
    cache_status: str='not_checked'
    truth_boundary: str='Wynik słownikowy jest ważny tylko dla wskazanego źródła i czasu pobrania; brak wyniku nie oznacza braku słowa.'
    # v14.6.10 structural fields
    query: str | None = None
    normalized_query: str | None = None
    found: bool = False
    lemmas: list[str] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)
    part_of_speech: list[str] = field(default_factory=list)
    semantic_relations: list[dict[str, Any]] = field(default_factory=list)
    spelling_suggestions: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    provider_statuses: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str=SCHEMA_VERSION
    def __post_init__(self):
        if self.query is None: self.query = self.term
        if self.normalized_query is None: self.normalized_query = self.normalized_term
        if not self.lemmas and self.lemma_candidates: self.lemmas = list(self.lemma_candidates)
        if not self.part_of_speech and self.pos_candidates: self.part_of_speech = list(self.pos_candidates)
        if not self.forms and self.inflection: self.forms = list(self.inflection)
        if self.definitions or self.lemmas or self.forms or self.semantic_relations:
            self.found = bool(self.found or self.definitions or self.lemmas or self.forms or self.semantic_relations)
    def to_dict(self)->dict[str,Any]: return asdict(self)

@dataclass(slots=True)
class NormalizedLexeme:
    term: str; normalized: str; language: str='pl'; provider: str='mini'; confidence: float=0.0; schema_version: str='normalized_lexeme/v14.6.10'
    def to_dict(self)->dict[str,Any]: return asdict(self)
@dataclass(slots=True)
class SemanticRelations:
    term: str; relation: str|None=None; related_terms: list[str]=field(default_factory=list); source_name: str='none'; confidence: float=0.0; schema_version: str='semantic_relations/v14.6.10'
    def to_dict(self)->dict[str,Any]: return asdict(self)
