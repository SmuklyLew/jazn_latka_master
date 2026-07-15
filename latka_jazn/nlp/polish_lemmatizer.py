from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
import json

from .polish_normalizer import PolishTextNormalizer
from .polish_tokenizer import PolishTokenizer
from .providers.base import ProviderLemmaCandidate
from .providers.builtin_provider import BuiltinPolishLemmaProvider
from .providers.optional_stanza_provider import OptionalStanzaPolishProvider
from .providers.optional_morfeusz_provider import OptionalMorfeuszPolishProvider

@dataclass(slots=True)
class LemmaCandidate:
    lemma: str
    confidence: float
    provider: str
    pos: str | None = None
    morph: dict[str, str] = field(default_factory=dict)
    explanation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass(slots=True)
class LemmatizedToken:
    text: str
    normalized: str
    folded: str
    start: int
    end: int
    is_word: bool
    lemma_candidates: list[LemmaCandidate]
    selected_lemma: str | None
    confidence: float
    provider: str
    ambiguity: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["lemma_candidates"] = [c.to_dict() for c in self.lemma_candidates]
        return data

@dataclass(slots=True)
class LemmatizationReport:
    schema_version: str
    original_text: str
    normalized_text: str
    provider_summary: str
    active_providers: list[str]
    unavailable_optional_providers: list[str]
    tokens: list[LemmatizedToken]
    selected_lemmas: list[str]
    unknown_or_low_confidence_terms: list[str]
    average_confidence: float
    limitations: list[str]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["tokens"] = [t.to_dict() for t in self.tokens]
        return data

class PolishLemmatizationEngine:
    """Warstwowa lematyzacja PL dla Jaźni.

    v14.6.1 wprowadza kontrakt NLP: tokeny, kandydaci lematów, wybrany lemat,
    pewność, provider i jawne ograniczenia. Provider builtin jest ostrożny i
    zawsze dostępny; zewnętrzne providery są opcjonalne.
    """
    schema_version = "polish_nlp/v14.6.2"

    def __init__(self, root: Path | None = None, *, enable_optional: bool | None = None) -> None:
        self.root = Path(root).resolve() if root else None
        self.normalizer = PolishTextNormalizer()
        self.tokenizer = PolishTokenizer()
        config = self._load_registry()
        if enable_optional is None:
            enable_optional = bool(config.get("enable_optional_providers_by_default", False))
        self.providers = [BuiltinPolishLemmaProvider(self.root)]
        # opcjonalne providery są rejestrowane, ale domyślnie nie startują ciężkich modeli
        self.optional_providers = [
            OptionalMorfeuszPolishProvider(enabled=enable_optional),
            OptionalStanzaPolishProvider(enabled=enable_optional),
        ]
        self.providers.extend([p for p in self.optional_providers if getattr(p, "available", False)])

    def _load_registry(self) -> dict:
        if not self.root:
            return {}
        path = self.root / "latka_jazn" / "resources" / "nlp_provider_registry_v14_6_2.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def analyse(self, text: str) -> LemmatizationReport:
        normalized = self.normalizer.normalize(text)
        tokens_raw = self.tokenizer.tokenize(text)
        tokens: list[LemmatizedToken] = []
        selected: list[str] = []
        low: list[str] = []
        for tok in tokens_raw:
            if not tok.is_word:
                tokens.append(LemmatizedToken(tok.text, tok.normalized, tok.folded, tok.start, tok.end, tok.is_word, [], None, 1.0, "punctuation", "none"))
                continue
            candidates = self._candidates(tok.text, tok.folded, context=normalized.normalized)
            if candidates:
                best = candidates[0]
                selected.append(best.lemma)
                confidence = float(best.confidence)
                provider = best.provider
                ambiguity = "ambiguous" if len({c.lemma for c in candidates}) > 1 and confidence < 0.90 else "resolved"
                if confidence < 0.60:
                    low.append(tok.text)
            else:
                confidence = 0.0
                provider = "none"
                ambiguity = "unknown"
                low.append(tok.text)
            tokens.append(LemmatizedToken(
                text=tok.text,
                normalized=tok.normalized,
                folded=tok.folded,
                start=tok.start,
                end=tok.end,
                is_word=tok.is_word,
                lemma_candidates=candidates,
                selected_lemma=candidates[0].lemma if candidates else None,
                confidence=confidence,
                provider=provider,
                ambiguity=ambiguity,
            ))
        word_conf = [t.confidence for t in tokens if t.is_word]
        unavailable = [p.name for p in self.optional_providers if not getattr(p, "available", False)]
        active = [getattr(p, "name", type(p).__name__) for p in self.providers]
        return LemmatizationReport(
            schema_version=self.schema_version,
            original_text=text or "",
            normalized_text=normalized.normalized,
            provider_summary=" + ".join(active) if active else "none",
            active_providers=active,
            unavailable_optional_providers=unavailable,
            tokens=tokens,
            selected_lemmas=self._unique(selected),
            unknown_or_low_confidence_terms=self._unique(low),
            average_confidence=round(sum(word_conf) / len(word_conf), 4) if word_conf else 1.0,
            limitations=[
                "Builtin provider jest bezpiecznym fallbackiem, nie pełnym kontekstowym lematyzatorem języka polskiego.",
                "Opcjonalne providery Stanza/Morfeusz2 są przygotowane, ale nie są wymagane i nie są uruchamiane bez lokalnej instalacji/modeli.",
                "Pełny wybór sensu słowa w zdaniu powinien trafić do kolejnej warstwy v14.6.x jako contextual lemma selection.",
            ],
        )

    def _candidates(self, token: str, folded: str, *, context: str) -> list[LemmaCandidate]:
        merged: list[ProviderLemmaCandidate] = []
        for provider in self.providers:
            try:
                merged.extend(provider.analyse_token(token, folded=folded, context=context))
            except Exception:
                continue
        best: dict[str, LemmaCandidate] = {}
        for c in merged:
            cc = LemmaCandidate(c.lemma, c.confidence, c.provider, c.pos, c.morph, c.explanation)
            prev = best.get(cc.lemma)
            if prev is None or cc.confidence > prev.confidence:
                best[cc.lemma] = cc
        return sorted(best.values(), key=lambda x: (-x.confidence, x.lemma))[:8]

    @staticmethod
    def _unique(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out
