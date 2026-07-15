from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Iterable
import json
import re

from latka_jazn.core.polish_understanding import POLISH_WORD_RE, PolishUnderstandingEngine
from latka_jazn.nlp.polish_lemmatizer import PolishLemmatizationEngine


@dataclass(slots=True)
class LexicalMatch:
    kind: str
    key: str
    matched: list[str]
    score: float
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LexicalSemanticReport:
    original_text: str
    normalized_text: str
    lemmas: list[str]
    phrases: list[LexicalMatch]
    semantic_fields: list[LexicalMatch]
    intent_tags: list[str]
    route_hint: str
    confidence: float
    reply_guidance: list[str] = field(default_factory=list)
    unknown_content_terms: list[str] = field(default_factory=list)
    lexical_depth: dict[str, Any] = field(default_factory=dict)
    nlp_analysis: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["phrases"] = [m.to_dict() for m in self.phrases]
        data["semantic_fields"] = [m.to_dict() for m in self.semantic_fields]
        return data


class LexicalSemanticUnderstanding:
    """Rozszerzona warstwa słownikowo-znaczeniowa Jaźni v14.6.0.

    `PolishUnderstandingEngine` rozpoznaje tokeny, lematy, intencje i podstawową
    trasę. Ta klasa dodaje drugi poziom: rodziny słów, frazy wielowyrazowe,
    pola semantyczne i wskazówki odpowiedzi. To nadal nie jest pełny LLM ani
    biologiczne rozumienie języka; to jawny, testowalny moduł runtime, który ma
    pomóc Jaźni nie mylić zwykłej rozmowy, pytania o pamięć, prośby o update,
    diagnostyki runtime i pracy z GitHub.
    """

    RESOURCE_NAME = "semantic_lexicon_v14_6_0.json"
    RESOURCE_NAME_V1462 = "semantic_lexicon_v14_6_2.json"
    FUNCTION_WORDS = {
        "a", "ale", "albo", "bo", "by", "czy", "do", "dla", "gdy", "i", "ja", "jak", "jest", "już", "juz",
        "mi", "mnie", "na", "nad", "nie", "o", "od", "po", "pod", "przy", "się", "sie", "tak", "te", "ten",
        "to", "tu", "ty", "w", "we", "z", "za", "że", "ze", "żeby", "zeby", "co", "kto", "gdzie", "kiedy",
        "teraz", "tego", "tym", "tą", "ta", "też", "tez", "może", "moze", "bardziej", "jeszcze", "już", "juz",
    }

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root).resolve() if root else None
        self.polish = PolishUnderstandingEngine(root)
        self.nlp = PolishLemmatizationEngine(root)
        self.lexicon = self._load_lexicon()

    def analyse(self, text: str, *, polish_report: dict[str, Any] | None = None, intent_tags: list[str] | None = None, nlp_report: dict[str, Any] | None = None) -> LexicalSemanticReport:
        normalized = self.polish.normalize_text(text)
        folded = self.polish.ascii_fold(normalized)
        if polish_report is None:
            polish_report = self.polish.analyse(text).to_dict()
        if nlp_report is None:
            nlp_report = self.nlp.analyse(text).to_dict()
        nlp_lemmas = [str(x) for x in (nlp_report.get("selected_lemmas") or []) if x]
        lemmas = self._unique(nlp_lemmas + [str(x) for x in (polish_report.get("lemmas") or []) if x])
        if not lemmas:
            lemmas = self._lemmas_builtin(text)
        phrases = self._match_phrases(normalized, folded, lemmas)
        semantic_fields = self._score_fields(normalized, folded, lemmas, phrases)
        tags = self._merge_intents(intent_tags or [], polish_report.get("intent_tags") or [], phrases, semantic_fields)
        route = self._route_hint(tags, phrases, semantic_fields, polish_report.get("route_hint"))
        guidance = self._reply_guidance(route, phrases, semantic_fields)
        unknown = self._unknown_content_terms(text, lemmas, phrases, semantic_fields)
        confidence = self._confidence(lemmas, phrases, semantic_fields, tags, unknown)
        depth = {
            "lemma_count": len(lemmas),
            "phrase_match_count": len(phrases),
            "semantic_field_count": len(semantic_fields),
            "unknown_content_term_count": len(unknown),
            "dominant_fields": [m.key for m in semantic_fields[:3]],
            "nlp_schema_version": nlp_report.get("schema_version"),
            "nlp_provider_summary": nlp_report.get("provider_summary"),
            "nlp_average_confidence": nlp_report.get("average_confidence"),
        }
        return LexicalSemanticReport(
            original_text=text,
            normalized_text=normalized,
            lemmas=lemmas,
            phrases=phrases,
            semantic_fields=semantic_fields,
            intent_tags=tags,
            route_hint=route,
            confidence=confidence,
            reply_guidance=guidance,
            unknown_content_terms=unknown,
            lexical_depth=depth,
            nlp_analysis={
                "schema_version": nlp_report.get("schema_version"),
                "provider_summary": nlp_report.get("provider_summary"),
                "selected_lemmas": nlp_report.get("selected_lemmas", []),
                "unknown_or_low_confidence_terms": nlp_report.get("unknown_or_low_confidence_terms", []),
                "average_confidence": nlp_report.get("average_confidence"),
            },
            limitations=[
                "Moduł v14.6.2 rozszerza słownik i semantykę domenową, ale nie zastępuje LLM-a ani pełnego parsera języka polskiego.",
                "Nieznane słowa są raportowane ostrożnie; mają pomagać w rozbudowie słownika, nie blokować rozmowy.",
                "Pełna lematyzacja kontekstowa pozostaje opcjonalną warstwą providerów NLP, nie obowiązkowym założeniem runtime.",
            ],
        )

    def _load_lexicon(self) -> dict[str, Any]:
        candidates: list[Path] = []
        if self.root:
            candidates.extend([
                self.root / "memory" / "raw" / self.RESOURCE_NAME_V1462,
                self.root / "latka_jazn" / "resources" / self.RESOURCE_NAME_V1462,
                self.root / "memory" / "raw" / self.RESOURCE_NAME,
                self.root / "latka_jazn" / "resources" / self.RESOURCE_NAME,
            ])
        for path in candidates:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
        try:
            with resources.files("latka_jazn.resources").joinpath(self.RESOURCE_NAME).open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _lemmas_builtin(self, text: str) -> list[str]:
        return self._unique([self.polish._lemma_builtin(tok) for tok in POLISH_WORD_RE.findall(text or "")])

    def _match_phrases(self, normalized: str, folded: str, lemmas: list[str]) -> list[LexicalMatch]:
        out: list[LexicalMatch] = []
        lemma_set = set(lemmas)
        for item in self.lexicon.get("phrase_rules", []):
            markers = [str(m) for m in item.get("markers") or []]
            found: list[str] = []
            for marker in markers:
                marker_norm = self.polish.normalize_text(marker)
                marker_fold = self.polish.ascii_fold(marker_norm)
                if self._marker_present(marker_norm, marker_fold, normalized, folded, lemma_set):
                    found.append(marker)
            if found:
                out.append(LexicalMatch(
                    kind="phrase",
                    key=str(item.get("key") or item.get("id") or "phrase"),
                    matched=self._unique(found),
                    score=float(item.get("weight") or 1.0) * min(1.0, 0.45 + 0.18 * len(found)),
                    description=str(item.get("description") or ""),
                ))
        out.sort(key=lambda m: (-m.score, m.key))
        return out

    def _score_fields(self, normalized: str, folded: str, lemmas: list[str], phrases: list[LexicalMatch]) -> list[LexicalMatch]:
        out: list[LexicalMatch] = []
        lemma_set = set(lemmas)
        phrase_keys = {p.key for p in phrases}
        for key, item in (self.lexicon.get("semantic_fields") or {}).items():
            markers = [str(m) for m in item.get("markers") or []]
            found: list[str] = []
            for marker in markers:
                marker_norm = self.polish.normalize_text(marker)
                marker_fold = self.polish.ascii_fold(marker_norm)
                if self._marker_present(marker_norm, marker_fold, normalized, folded, lemma_set):
                    found.append(marker)
            linked = [p for p in item.get("linked_phrases", []) if p in phrase_keys]
            if found or linked:
                score = min(1.0, 0.30 + 0.12 * len(found) + 0.10 * len(linked)) * float(item.get("weight") or 1.0)
                out.append(LexicalMatch(
                    kind="semantic_field",
                    key=str(key),
                    matched=self._unique(found + linked),
                    score=min(1.0, score),
                    description=str(item.get("description") or ""),
                ))
        out.sort(key=lambda m: (-m.score, m.key))
        return out

    def _marker_present(self, marker_norm: str, marker_fold: str, normalized: str, folded: str, lemma_set: set[str]) -> bool:
        if marker_norm in lemma_set or marker_fold in {self.polish.ascii_fold(x) for x in lemma_set}:
            return True
        if " " in marker_norm or "-" in marker_norm:
            return marker_norm in normalized or marker_fold in folded
        pattern = rf"(?<![\wąćęłńóśźż]){re.escape(marker_fold)}(?![\wąćęłńóśźż])"
        return re.search(pattern, folded, flags=re.UNICODE) is not None

    def _merge_intents(self, base_tags: list[str], polish_tags: list[str], phrases: list[LexicalMatch], fields: list[LexicalMatch]) -> list[str]:
        tags: list[str] = []
        for tag in list(base_tags) + list(polish_tags):
            if tag and tag != "conversation":
                tags.append(str(tag))
        phrase_lookup = {p.key for p in phrases}
        field_lookup = {f.key for f in fields}
        for item in self.lexicon.get("phrase_rules", []):
            if str(item.get("key") or item.get("id")) in phrase_lookup:
                tags.extend(str(t) for t in item.get("intent_tags") or [])
        for key, item in (self.lexicon.get("semantic_fields") or {}).items():
            if key in field_lookup:
                tags.extend(str(t) for t in item.get("intent_tags") or [])
        return self._unique(tags) or ["conversation"]

    def _route_hint(self, tags: list[str], phrases: list[LexicalMatch], fields: list[LexicalMatch], polish_route: str | None) -> str:
        tag_set = set(tags)
        phrase_keys = {p.key for p in phrases}
        field_keys = {f.key for f in fields}
        if "v14_6_10_runtime_self_expression_topic_mismatch_update" in tag_set or "v14_6_10_update" in tag_set:
            return "v14_6_10_runtime_self_expression_topic_mismatch_update"
        if {"v14_6_1_update", "polish_nlp", "safe_incremental_update"} & tag_set:
            return "v14_6_1_nlp_adapter_update"
        if {"v14_6_update", "lexicon_expansion", "language_understanding"} & tag_set and {"update_request", "implementation"} & tag_set:
            return "v14_6_0_lexical_runtime_update"
        if "repository" in field_keys or "github" in tag_set:
            return "github_repository_workflow"
        if "memory_recall" in tag_set or "place_memory" in field_keys or "memory_scene" in phrase_keys:
            return "memory_scene_recall"
        if "self_state" in tag_set or "affective_state" in field_keys:
            return "self_state_dialogue"
        if "runtime_architecture" in tag_set or "runtime_architecture" in field_keys:
            return "runtime_architecture_dialogue"
        if "ordinary_day" in tag_set or "daily_observation" in field_keys:
            return "ordinary_daily_conversation"
        return polish_route or "general_conversation"

    def _reply_guidance(self, route: str, phrases: list[LexicalMatch], fields: list[LexicalMatch]) -> list[str]:
        guidance = list((self.lexicon.get("route_guidance") or {}).get(route) or [])
        for match in phrases[:4] + fields[:4]:
            if match.description:
                guidance.append(f"Uwzględnij: {match.description}")
        return self._unique(guidance)

    def _unknown_content_terms(self, text: str, lemmas: list[str], phrases: list[LexicalMatch], fields: list[LexicalMatch]) -> list[str]:
        known: set[str] = set()
        for item in self.lexicon.get("phrase_rules", []):
            known.update(self.polish.ascii_fold(str(m)) for m in item.get("markers") or [])
        for item in (self.lexicon.get("semantic_fields") or {}).values():
            known.update(self.polish.ascii_fold(str(m)) for m in item.get("markers") or [])
        known.update(self.polish.ascii_fold(x) for x in lemmas)
        terms: list[str] = []
        for token in POLISH_WORD_RE.findall(text or ""):
            fold = self.polish.ascii_fold(token)
            if len(fold) < 4 or fold in self.FUNCTION_WORDS:
                continue
            lemma = self.polish.ascii_fold(self.polish._lemma_builtin(token))
            if fold not in known and lemma not in known and lemma not in self.FUNCTION_WORDS:
                terms.append(token)
        return self._unique(terms)[:10]

    @staticmethod
    def _confidence(lemmas: list[str], phrases: list[LexicalMatch], fields: list[LexicalMatch], tags: list[str], unknown: list[str]) -> float:
        score = 0.45
        score += min(0.15, 0.02 * len(lemmas))
        score += min(0.20, 0.06 * len(phrases))
        score += min(0.16, 0.05 * len(fields))
        score += min(0.10, 0.02 * len([t for t in tags if t != "conversation"]))
        score -= min(0.12, 0.015 * len(unknown))
        return max(0.10, min(0.96, score))

    @staticmethod
    def _unique(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out
