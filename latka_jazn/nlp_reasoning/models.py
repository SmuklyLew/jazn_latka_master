from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "polish_reasoning_frame/v14.8.4"


@dataclass(slots=True)
class ProviderStatus:
    provider: str
    available: bool
    mode: str
    reason: str | None = None
    version: str | None = None
    license: str | None = None
    source_url: str | None = None
    data_path: str | None = None
    dictionary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MorphCandidate:
    surface: str
    lemma: str
    tag: str
    start: int | None = None
    end: int | None = None
    provider: str = "unknown"
    confidence: float = 0.5
    features: dict[str, str] = field(default_factory=dict)
    qualifiers: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SelectedLemma:
    surface: str
    lemma: str
    tag: str
    provider: str
    confidence: float
    reason: str
    candidate_count: int
    ambiguous: bool = False
    features: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TokenMorphAnalysis:
    token_index: int
    surface: str
    start: int | None
    end: int | None
    candidates: list[MorphCandidate] = field(default_factory=list)
    selected: SelectedLemma | None = None
    source_boundary: str = (
        "selected_lemma jest heurystycznym wyborem runtime. Morfeusz/PoliMorf zwracają kandydatów; "
        "pełna dezambiguacja kontekstowa należy do kolejnych warstw NLP/LLM."
    )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["candidates"] = [c.to_dict() for c in self.candidates]
        data["selected"] = self.selected.to_dict() if self.selected else None
        return data


@dataclass(slots=True)
class SemanticFrame:
    speech_act: str = "statement"
    primary_intent: str = "ordinary_conversation"
    tone: list[str] = field(default_factory=list)
    question_object: str | None = None
    requires_memory: bool = False
    requires_time: bool = False
    requires_diagnostic: bool = False
    allow_online_lookup: bool = False
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReplyPolicy:
    allow_poetic_reply: bool = False
    avoid_meta_commentary: bool = True
    needs_citation: bool = False
    llm_allowed: bool = True
    repeat_guard_key: str | None = None
    source_grounding_required: bool = False
    truth_boundary_note: str = (
        "Warstwa NLP wskazuje możliwe znaczenia i politykę odpowiedzi; nie udaje, "
        "że pobrała definicję albo korpus, jeśli provider nie został realnie użyty."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PolishReasoningFrame:
    source_text: str
    normalized_text: str
    folded_text: str
    tokens: list[str]
    morphology: list[MorphCandidate]
    semantic_frame: SemanticFrame
    reply_policy: ReplyPolicy
    provider_statuses: list[ProviderStatus]
    token_analyses: list[TokenMorphAnalysis] = field(default_factory=list)
    sources_used: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["morphology"] = [m.to_dict() for m in self.morphology]
        data["token_analyses"] = [t.to_dict() for t in self.token_analyses]
        data["semantic_frame"] = self.semantic_frame.to_dict()
        data["reply_policy"] = self.reply_policy.to_dict()
        data["provider_statuses"] = [p.to_dict() for p in self.provider_statuses]
        return data
