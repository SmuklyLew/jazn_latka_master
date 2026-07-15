from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .base import ProviderLemmaCandidate


@dataclass(slots=True)
class StanzaTokenAnnotation:
    text: str
    lemma: str | None
    upos: str | None
    xpos: str | None
    feats: dict[str, str] = field(default_factory=dict)
    head: int | None = None
    deprel: str | None = None
    start_char: int | None = None
    end_char: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StanzaEntityAnnotation:
    text: str
    entity_type: str
    start_char: int | None = None
    end_char: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StanzaTextAnalysis:
    provider: str
    available: bool
    sentences: list[list[StanzaTokenAnnotation]] = field(default_factory=list)
    entities: list[StanzaEntityAnnotation] = field(default_factory=list)
    processors: list[str] = field(default_factory=list)
    error: str | None = None
    truth_boundary: str = (
        "Wynik istnieje tylko, gdy lokalnie zainstalowano Stanza i polskie modele. "
        "Provider nie pobiera modeli automatycznie i nie udaje analizy przy braku zasobów."
    )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sentences"] = [[token.to_dict() for token in sentence] for sentence in self.sentences]
        data["entities"] = [entity.to_dict() for entity in self.entities]
        return data


class OptionalStanzaPolishProvider:
    """Opcjonalny adapter Stanza dla lematu oraz pełnego tekstu.

    Nie pobiera modeli i nie instaluje zależności samodzielnie. Podstawowy
    pipeline lematyzacyjny zachowuje zgodność z istniejącym kontraktem.
    Pełna analiza tekstu (zdania, POS/UFeats, dependencies i opcjonalne NER)
    jest budowana leniwie, dopiero po jawnym wywołaniu ``analyse_text``.
    """

    name = "optional_stanza_pl"

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self._pipeline = None
        self._text_pipelines: dict[bool, Any] = {}
        self.available = False
        self.last_error: str | None = None
        if enabled:
            self._try_init()

    def _try_init(self) -> None:
        try:
            import stanza  # type: ignore

            self._pipeline = stanza.Pipeline(
                lang="pl",
                processors="tokenize,pos,lemma",
                tokenize_no_ssplit=True,
                verbose=False,
                download_method=None,
            )
            self.available = True
            self.last_error = None
        except Exception as exc:
            self._pipeline = None
            self.available = False
            self.last_error = repr(exc)

    @staticmethod
    def _parse_feats(raw: str | None) -> dict[str, str]:
        out: dict[str, str] = {}
        for item in (raw or "").split("|"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            if key:
                out[key] = value
        return out

    def _text_pipeline(self, *, include_ner: bool) -> Any | None:
        if not self.enabled:
            return None
        if include_ner in self._text_pipelines:
            return self._text_pipelines[include_ner]
        try:
            import stanza  # type: ignore

            processors = "tokenize,mwt,pos,lemma,depparse" + (",ner" if include_ner else "")
            pipeline = stanza.Pipeline(
                lang="pl",
                processors=processors,
                verbose=False,
                download_method=None,
            )
            self._text_pipelines[include_ner] = pipeline
            return pipeline
        except Exception as exc:
            self.last_error = repr(exc)
            self._text_pipelines[include_ner] = None
            return None

    def analyse_token(self, token: str, *, folded: str, context: str = "") -> list[ProviderLemmaCandidate]:
        if not self.available or self._pipeline is None or not token.strip():
            return []
        try:
            doc = self._pipeline(token)
            for sentence in doc.sentences:
                if sentence.words:
                    word = sentence.words[0]
                    lemma = (word.lemma or folded).lower()
                    return [
                        ProviderLemmaCandidate(
                            lemma=lemma,
                            confidence=0.86,
                            provider=self.name,
                            pos=getattr(word, "upos", None),
                            morph=self._parse_feats(getattr(word, "feats", None)),
                            explanation="wynik opcjonalnego pipeline Stanza dla PL",
                        )
                    ]
        except Exception as exc:
            self.last_error = repr(exc)
        return []

    def analyse_text(self, text: str, *, include_ner: bool = False) -> StanzaTextAnalysis:
        processors = ["tokenize", "mwt", "pos", "lemma", "depparse"] + (["ner"] if include_ner else [])
        if not text.strip():
            return StanzaTextAnalysis(self.name, self.available, processors=processors)
        pipeline = self._text_pipeline(include_ner=include_ner)
        if pipeline is None:
            return StanzaTextAnalysis(self.name, False, processors=processors, error=self.last_error or "stanza_pipeline_unavailable")
        try:
            doc = pipeline(text)
            sentences: list[list[StanzaTokenAnnotation]] = []
            for sentence in doc.sentences:
                annotations: list[StanzaTokenAnnotation] = []
                for word in sentence.words:
                    parent = getattr(word, "parent", None)
                    annotations.append(
                        StanzaTokenAnnotation(
                            text=str(getattr(word, "text", "")),
                            lemma=getattr(word, "lemma", None),
                            upos=getattr(word, "upos", None),
                            xpos=getattr(word, "xpos", None),
                            feats=self._parse_feats(getattr(word, "feats", None)),
                            head=getattr(word, "head", None),
                            deprel=getattr(word, "deprel", None),
                            start_char=getattr(parent, "start_char", None),
                            end_char=getattr(parent, "end_char", None),
                        )
                    )
                sentences.append(annotations)
            entities = [
                StanzaEntityAnnotation(
                    text=str(getattr(entity, "text", "")),
                    entity_type=str(getattr(entity, "type", "")),
                    start_char=getattr(entity, "start_char", None),
                    end_char=getattr(entity, "end_char", None),
                )
                for entity in getattr(doc, "entities", [])
            ]
            return StanzaTextAnalysis(self.name, True, sentences, entities, processors)
        except Exception as exc:
            self.last_error = repr(exc)
            return StanzaTextAnalysis(self.name, False, processors=processors, error=self.last_error)
