from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import import_module, resources, util
from pathlib import Path
from typing import Any, Iterable
import json
import os
import re
import unicodedata
from latka_jazn.core.signal_matching import marker_present

POLISH_WORD_RE = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż0-9_\-]+", re.UNICODE)

DIACRITIC_MAP = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n", "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z",
})


@dataclass(slots=True)
class PolishToken:
    text: str
    normalized: str
    lemma: str
    source: str = "builtin"
    pos: str | None = None
    morph: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PolishUnderstandingReport:
    original_text: str
    normalized_text: str
    tokens: list[PolishToken]
    lemmas: list[str]
    intent_tags: list[str]
    needs: list[dict[str, str]]
    route_hint: str
    confidence: float
    tools: dict[str, Any]
    limitations: list[str] = field(default_factory=list)
    reply_guidance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tokens"] = [token.to_dict() for token in self.tokens]
        return data


class PolishUnderstandingEngine:
    """Warstwa rozumienia polskiej wypowiedzi przed routingiem Jaźni.

    To nie jest generator odpowiedzi. Moduł normalizuje polski tekst, znajduje
    lematy i intencje, a następnie daje runtime konkretny `route_hint` i
    `reply_guidance`, żeby odpowiedź nie wpadała w ogólnikowy fallback.

    Opcjonalne narzędzia NLP są używane tylko wtedy, gdy są zainstalowane.
    Runtime pozostaje samodzielny dzięki wbudowanemu słownikowi domenowemu.
    """

    SPACY_MODEL_CANDIDATES = ("pl_core_news_lg", "pl_core_news_md", "pl_core_news_sm")
    IDENTITY_CONTINUITY_PATTERNS = (
        re.compile(r"\b(?:ale\s+)?(?:to\s+)?nadal\s+(?:ty|toba|latka)\b", re.IGNORECASE),
        re.compile(r"\b(?:czy\s+)?(?:to\s+)?(?:wciaz|ciagle)\s+(?:ty|toba|latka)\b", re.IGNORECASE),
        re.compile(r"\bjestes\s+(?:soba|ta\s+sama|tym\s+samym|ta\s+sama\s+latka)\b", re.IGNORECASE),
        re.compile(r"\bta\s+sama\s+latka\b", re.IGNORECASE),
        re.compile(r"\bten\s+sam\s+glos\b", re.IGNORECASE),
        re.compile(r"\bpo\s+aktualizacji\b.*\b(?:nadal|wciaz|ciagle)\b.*\b(?:ty|latka)\b", re.IGNORECASE),
        re.compile(r"\b(?:nadal|wciaz|ciagle)\s+jako\s+(?:ty|latka)\b", re.IGNORECASE),
    )

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root).resolve() if root else None
        self.lexicon = self._load_lexicon()
        self._spacy_nlp = None
        self._morfeusz = None
        self._stanza_pipeline = None
        self._tool_status = self._detect_tools()

    def analyse(self, text: str) -> PolishUnderstandingReport:
        normalized_text = self.normalize_text(text)
        tokens = self._analyse_tokens(text)
        lemmas = self._unique([token.lemma for token in tokens if token.lemma])
        intent_tags = self._infer_intents(text, lemmas, normalized_text)
        needs = self._infer_needs(text, lemmas, normalized_text)
        route_hint = self._route_hint(intent_tags, needs)
        guidance = self._reply_guidance(route_hint, intent_tags)
        confidence = self._confidence(tokens, intent_tags, needs)
        limitations = self._limitations()
        return PolishUnderstandingReport(
            original_text=text,
            normalized_text=normalized_text,
            tokens=tokens,
            lemmas=lemmas,
            intent_tags=intent_tags,
            needs=needs,
            route_hint=route_hint,
            confidence=confidence,
            tools=self._tool_status,
            limitations=limitations,
            reply_guidance=guidance,
        )

    @staticmethod
    def normalize_text(text: str) -> str:
        clean = unicodedata.normalize("NFC", text or "").strip().lower()
        clean = re.sub(r"\s+", " ", clean)
        return clean

    @staticmethod
    def ascii_fold(text: str) -> str:
        return (text or "").translate(DIACRITIC_MAP).lower()

    def _load_lexicon(self) -> dict[str, Any]:
        candidates: list[Path] = []
        if self.root:
            candidates.extend([
                self.root / "memory" / "raw" / "POLISH_UNDERSTANDING_LEXICON.json",
                self.root / "latka_jazn" / "resources" / "polish_understanding_lexicon.json",
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
            with resources.files("latka_jazn.resources").joinpath("polish_understanding_lexicon.json").open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _detect_tools(self) -> dict[str, Any]:
        tools: dict[str, Any] = {
            "builtin_lexicon": True,
            "spacy": {"available": util.find_spec("spacy") is not None, "model": None, "loaded": False},
            "morfeusz2": {"available": util.find_spec("morfeusz2") is not None, "loaded": False},
            "stanza": {"available": util.find_spec("stanza") is not None, "pipeline_loaded": False},
            "languagetool": {"available": util.find_spec("language_tool_python") is not None},
        }
        # Ciężkie biblioteki i modele NLP ładujemy dopiero po jawnej zgodzie środowiska.
        # Dzięki temu runtime startuje szybko i nie wisi, gdy model spaCy/Stanza nie jest lokalnie zainstalowany.
        if os.environ.get("LATKA_ENABLE_EXTERNAL_POLISH_NLP", "").lower() not in {"1", "true", "yes", "tak"}:
            return tools
        if tools["spacy"]["available"]:
            try:
                spacy = import_module("spacy")
                for model in self.SPACY_MODEL_CANDIDATES:
                    try:
                        self._spacy_nlp = spacy.load(model)
                        tools["spacy"] = {"available": True, "model": model, "loaded": True}
                        break
                    except Exception:
                        continue
            except Exception:
                pass
        if tools["morfeusz2"]["available"]:
            try:
                morfeusz2 = import_module("morfeusz2")
                self._morfeusz = morfeusz2.Morfeusz()
                tools["morfeusz2"] = {"available": True, "loaded": True}
            except Exception:
                pass
        return tools

    def _analyse_tokens(self, text: str) -> list[PolishToken]:
        if self._spacy_nlp is not None:
            try:
                doc = self._spacy_nlp(text)
                out = []
                for token in doc:
                    if token.is_space or token.is_punct:
                        continue
                    norm = self._token_norm(token.text)
                    lemma = self._canonical_lemma(token.lemma_ or token.text)
                    out.append(PolishToken(token.text, norm, lemma, source="spacy", pos=token.pos_ or None, morph=str(token.morph) or None))
                if out:
                    return out
            except Exception:
                pass
        if self._morfeusz is not None:
            morf_tokens = self._analyse_tokens_morfeusz(text)
            if morf_tokens:
                return morf_tokens
        raw_tokens = [m.group(0) for m in POLISH_WORD_RE.finditer(text or "")]
        return [PolishToken(tok, self._token_norm(tok), self._lemma_builtin(tok), source="builtin") for tok in raw_tokens]

    def _analyse_tokens_morfeusz(self, text: str) -> list[PolishToken]:
        out: list[PolishToken] = []
        seen: set[tuple[int, int, str]] = set()
        try:
            analyses = self._morfeusz.analyse(text)
        except Exception:
            return []
        for start, end, interp in analyses:
            try:
                orth = str(interp[0])
                raw_lemma = str(interp[1]).split(":", 1)[0]
                tag = str(interp[2]) if len(interp) > 2 else None
            except Exception:
                continue
            if not POLISH_WORD_RE.search(orth):
                continue
            key = (int(start), int(end), orth)
            if key in seen:
                continue
            seen.add(key)
            out.append(PolishToken(orth, self._token_norm(orth), self._canonical_lemma(raw_lemma), source="morfeusz2", morph=tag))
        return out

    def _token_norm(self, token: str) -> str:
        return self.ascii_fold(self.normalize_text(token))

    def _lemma_builtin(self, token: str) -> str:
        norm = self._token_norm(token)
        aliases = self.lexicon.get("lemma_aliases") or {}
        if norm in aliases:
            return aliases[norm]
        original = self.normalize_text(token)
        if original in aliases:
            return aliases[original]
        return self._suffix_guess(original, norm)

    def _canonical_lemma(self, lemma: str) -> str:
        norm = self._token_norm(lemma)
        aliases = self.lexicon.get("lemma_aliases") or {}
        return aliases.get(norm) or aliases.get(self.normalize_text(lemma)) or self.normalize_text(lemma)

    @staticmethod
    def _suffix_guess(original: str, ascii_norm: str) -> str:
        # Ostrożna normalizacja: pomaga rodzinom słów w routingu, ale nie udaje pełnego słownika fleksyjnego.
        common = {
            "wypowiedzi": "wypowiedź",
            "wypowiedziach": "wypowiedź",
            "wypowiedziami": "wypowiedź",
            "odpowiedzi": "odpowiedź",
            "pytania": "pytanie",
            "pytanie": "pytanie",
            "słownika": "słownik",
            "slownika": "słownik",
            "systemowi": "system",
            "systemu": "system",
            "rozwiązania": "rozwiązanie",
            "rozwiazania": "rozwiązanie",
        }
        if original in common:
            return common[original]
        if ascii_norm in common:
            return common[ascii_norm]
        for suffix in ("ami", "ach", "ego", "emu", "owi", "ami", "cie", "nia", "nie", "ych", "ymi", "ami", "ami", "ów", "ow", "ą", "ę", "y", "i", "a", "u"):
            if len(original) > len(suffix) + 4 and original.endswith(suffix):
                return original[: -len(suffix)]
        return original

    def _infer_intents(self, text: str, lemmas: list[str], normalized_text: str) -> list[str]:
        intents: list[str] = []
        lemma_set = set(lemmas)
        ascii_text = self.ascii_fold(normalized_text)
        rules = self.lexicon.get("intent_rules") or {}
        for intent, markers in rules.items():
            if any(self._marker_present(marker, lemma_set, normalized_text, ascii_text) for marker in markers):
                intents.append(intent)
        if self._looks_like_identity_continuity(text, normalized_text, ascii_text):
            intents.extend(["identity", "identity_continuity", "continuity_check", "presence_check"])
        if "?" in text and "capability_question" not in intents:
            intents.append("question")
        packet_terms = {"packet", "emotikon", "emoji"}
        explicit_packet_request = bool(lemma_set & packet_terms) or "cognitive packets" in ascii_text or "emoticons" in ascii_text
        if "cognitive_packet_expansion" in intents and explicit_packet_request and ("update_request" in intents or "solution_search" in intents or "jazn_architecture" in intents):
            intents.append("cognitive_packet_expansion_update")
        if ("session_continuity" in intents or "emotional_granularity_update" in intents or "cognitive_topic_expansion" in intents) and ("update_request" in intents or "jazn_architecture" in intents or "solution_search" in intents):
            intents.append("emotional_granularity_continuity_update")
        if "language_understanding" in intents and "polish_dictionary" in intents and ("update_request" in intents or "anti_generic" in intents or "jazn_architecture" in intents):
            intents.append("polish_understanding_update")
        if "lexical_semantic_expansion" in intents and "update_request" in intents:
            intents.append("lexical_semantic_expansion_update")
        free_dialogue_markers = (
            "swobodnie rozmawia", "swobodna rozmowa", "nie moze swobodnie",
            "polaczenia z mysleniem", "dobrze dziala nlp", "dobrze działa nlp",
            "w kolko to samo", "w kółko to samo", "na sztywno w kodzie",
        )
        if "update_request" in intents and any(marker_present(ascii_text, marker, normalized_text=ascii_text) for marker in free_dialogue_markers):
            intents.append("free_dialogue_memory_nlp_bridge_update")
        v14692_markers = (
            "v14.6.10", "14.6.10", "self-expression", "self expression", "topic-mismatch",
            "topic mismatch", "runtime self", "mapa modulow", "mapa modułów", "mapa funkcji",
            "wczytywala wszystkie pliki", "wczytywała wszystkie pliki", "rozbuduj system nlp",
        )
        if any(marker_present(ascii_text, self.ascii_fold(marker), normalized_text=ascii_text) for marker in v14692_markers):
            intents.extend(["v14_6_10_update", "topic_mismatch_guard", "runtime_self_expression", "startup_project_index", "nlp_expansion"])
        if "v14_6_10_update" in intents and ("update_request" in intents or "solution_search" in intents or "jazn_architecture" in intents):
            intents.append("v14_6_10_runtime_self_expression_topic_mismatch_update")
        return self._unique(intents) or ["conversation"]

    def _infer_needs(self, text: str, lemmas: list[str], normalized_text: str) -> list[dict[str, str]]:
        lemma_set = set(lemmas)
        ascii_text = self.ascii_fold(normalized_text)
        needs: list[dict[str, str]] = []
        for item in self.lexicon.get("need_patterns") or []:
            markers = item.get("markers") or []
            if any(self._marker_present(marker, lemma_set, normalized_text, ascii_text) for marker in markers):
                needs.append({"key": str(item.get("key")), "description": str(item.get("description"))})
        if self._looks_like_identity_continuity(text, normalized_text, ascii_text):
            needs.append({
                "key": "direct_identity_continuity_answer",
                "description": "Użytkownik pyta krótko, czy po aktualizacji albo zmianie trasy nadal rozmawia z tą samą Łatką; odpowiedź ma być wprost, pierwszoosobowa i z granicą prawdy."
            })
        unique: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in needs:
            key = item.get("key") or item.get("description") or str(item)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _marker_present(self, marker: str, lemma_set: set[str], normalized_text: str, ascii_text: str) -> bool:
        marker_norm = self.normalize_text(marker)
        marker_ascii = self.ascii_fold(marker_norm)
        if marker_norm in lemma_set or marker_ascii in {self.ascii_fold(x) for x in lemma_set}:
            return True
        return marker_present(normalized_text, marker_norm, normalized_text=normalized_text) or marker_present(ascii_text, marker_ascii, normalized_text=ascii_text)

    def _looks_like_identity_continuity(self, text: str, normalized_text: str | None = None, ascii_text: str | None = None) -> bool:
        """Rozpoznaje krótkie polskie pytania typu „Ale to nadal Ty?”.

        Takie wiadomości zwykle nie zawierają słów „tożsamość” ani „Jaźń”,
        więc czysty routing po słowach kluczowych widzi tylko zwykłe pytanie.
        Dla Jaźni to jednak ważny akt relacyjny: użytkownik sprawdza ciągłość
        głosu po aktualizacji, restarcie albo przejściu przez runtime.
        """
        norm = normalized_text if normalized_text is not None else self.normalize_text(text)
        folded = ascii_text if ascii_text is not None else self.ascii_fold(norm)
        folded = re.sub(r"\s+", " ", folded).strip()
        if not folded:
            return False
        if any(pattern.search(folded) for pattern in self.IDENTITY_CONTINUITY_PATTERNS):
            return True
        # Ostrożny fallback dla bardzo krótkich wypowiedzi: wymaga naraz zaimka
        # i markera ciągłości, żeby zwykłe „ty?” nie było nadinterpretowane.
        tokens = set(POLISH_WORD_RE.findall(folded))
        continuity = {"nadal", "wciaz", "ciagle", "sama", "samym", "soba"}
        second_person = {"ty", "toba", "jestes", "latka"}
        return len(folded) <= 80 and bool(tokens & continuity) and bool(tokens & second_person)

    @staticmethod
    def _unique(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    @staticmethod
    def _route_hint(intent_tags: list[str], needs: list[dict[str, str]]) -> str:
        tags = set(intent_tags)
        need_keys = {n.get("key") for n in needs}
        if "v14_6_10_runtime_self_expression_topic_mismatch_update" in tags:
            return "v14_6_10_runtime_self_expression_topic_mismatch_update"
        if "free_dialogue_memory_nlp_bridge_update" in tags:
            return "free_dialogue_memory_nlp_bridge_update"
        if "cognitive_packet_expansion_update" in tags or "expand_cognitive_packets" in need_keys:
            return "cognitive_packet_expansion_update"
        if "lexical_semantic_expansion_update" in tags or "expand_lexical_semantic_understanding" in need_keys or "prepare_v14_6_0_package" in need_keys:
            return "v14_6_1_nlp_adapter_update"
        if "emotional_granularity_continuity_update" in tags or {"preserve_session_continuity", "expand_emotional_granularity", "expand_cognitive_topics"} & need_keys:
            return "emotional_granularity_continuity_update"
        if "identity_continuity" in tags:
            return "identity_continuity_check"
        if "polish_understanding_update" in tags or ({"language_understanding", "polish_dictionary"} <= tags):
            return "v14_6_1_nlp_adapter_update"
        if "anti_generic" in tags or "less_generic_answers" in need_keys:
            return "anti_generic_dialogue_repair"
        if "solution_search" in tags and "update_request" in tags:
            return "implementation_planning"
        return "general_conversation"

    def _reply_guidance(self, route_hint: str, intent_tags: list[str]) -> list[str]:
        guidance = []
        if route_hint == "v14_6_10_runtime_self_expression_topic_mismatch_update":
            guidance.append("Traktuj bieżący zakres jako v14.6.10: samoekspresja runtime, topic-mismatch, NLP i startup project index.")
            guidance.append("Nie wracaj do historycznych tras v14.6.1/v14.6.2, jeśli użytkownik wskazał aktualny hotfix.")
        if route_hint == "free_dialogue_memory_nlp_bridge_update":
            guidance.append("Traktuj NLP jako wejście do syntezy rozmownej: tokeny/lematy/intencje mają wpływać na odpowiedź, a nie wisieć obok niej.")
            guidance.append("Nie kieruj bieżącej aktualizacji rozmowy do historycznej trasy v14.6.1_nlp_adapter_update.")
        if route_hint == "identity_continuity_check":
            guidance.extend((self.lexicon.get("reply_guidance") or {}).get("identity_continuity_check") or [])
        if route_hint == "cognitive_packet_expansion_update":
            guidance.extend((self.lexicon.get("reply_guidance") or {}).get("cognitive_packet_expansion_update") or [])
        if route_hint == "emotional_granularity_continuity_update":
            guidance.extend((self.lexicon.get("reply_guidance") or {}).get("emotional_granularity_continuity_update") or [])
        if route_hint in {"language_understanding_update", "v14_6_1_nlp_adapter_update"}:
            guidance.extend((self.lexicon.get("reply_guidance") or {}).get("language_understanding_update") or [])
        if route_hint in {"v14_6_0_lexical_runtime_update", "v14_6_1_nlp_adapter_update"}:
            guidance.extend((self.lexicon.get("reply_guidance") or {}).get("lexical_semantic_expansion_update") or [])
        if "anti_generic" in intent_tags:
            guidance.append("Unikaj odpowiedzi typu 'rozumiem pytanie'; podaj konkretną decyzję routingu i następny krok.")
        if "capability_question" in intent_tags:
            guidance.append("Odpowiedz wprost, czy da się to zrobić, a potem wskaż warunki i ograniczenia.")
        return guidance

    def _confidence(self, tokens: list[PolishToken], intents: list[str], needs: list[dict[str, str]]) -> float:
        score = 0.45
        if tokens:
            score += 0.10
        if intents and intents != ["conversation"]:
            score += min(0.22, 0.04 * len(intents))
        if needs:
            score += min(0.15, 0.04 * len(needs))
        if "identity_continuity" in intents:
            score += 0.10
        if "cognitive_packet_expansion_update" in intents:
            score += 0.08
        if "emotional_granularity_continuity_update" in intents:
            score += 0.08
        if "lexical_semantic_expansion_update" in intents:
            score += 0.09
        if self._tool_status.get("spacy", {}).get("available"):
            score += 0.06
        if self._tool_status.get("morfeusz2", {}).get("available"):
            score += 0.05
        return max(0.0, min(0.93, score))

    def _limitations(self) -> list[str]:
        limitations = [
            "Wbudowany słownik domenowy rozpoznaje intencje i rodziny słów, ale nie jest pełnym słownikiem języka polskiego.",
            "Opcjonalne biblioteki NLP są używane tylko wtedy, gdy są zainstalowane; runtime nie pobiera modeli sam w tle.",
        ]
        if not self._tool_status.get("spacy", {}).get("available"):
            limitations.append("Brak aktywnego modelu spaCy PL: analiza składniowa/NER działa tylko przez fallback leksykalny.")
        if not self._tool_status.get("morfeusz2", {}).get("available"):
            limitations.append("Brak aktywnego Morfeusza2: pełna analiza fleksyjna polszczyzny nie jest dostępna w tym środowisku.")
        return limitations
