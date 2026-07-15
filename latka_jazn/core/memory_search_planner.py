from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import json
import math
import re
import unicodedata


@dataclass(slots=True)
class MemorySearchTopic:
    key: str
    labels: list[str]
    triggers: list[str]
    aliases: list[str]
    canonical_files: list[str]
    source_layers: list[str]
    priority: int = 50
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemorySearchPlan:
    schema_version: str
    original_query: str
    recall_requested: bool
    focus_terms: list[str]
    rejected_terms: list[str]
    expanded_terms: list[str]
    topic_keys: list[str]
    source_hints: list[str]
    search_terms: list[str]
    search_passes: list[dict[str, Any]]
    confidence: float
    routing_notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceFileHit:
    topic_key: str | None
    path: str
    term: str | None
    score: float
    source_label: str
    content_excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemorySearchPlanner:
    """Planuje wyszukiwanie pamięci przed wejściem do indeksów.

    Cel v14.6.5/v14.6.10: runtime nie ma wyszukiwać po przypadkowych tokenach typu
    `sobie`, `wszystko`, `oraz`. Najpierw rozpoznaje intencję przywołania,
    tematy, synonimy i właściwe źródła, a dopiero potem uruchamia zapytania.

    To jest lekki planer bez zewnętrznych zależności: łączy słownik tematów,
    rozszerzanie zapytania, ważone przejścia wyszukiwania i źródła kanoniczne.
    """

    SCHEMA_VERSION = "memory_search_planner/v14.6.10"
    RESOURCE_NAME = "memory_search_topics_v14_6_10.json"

    RECALL_MARKERS = {
        "pamiętasz", "pamietasz", "przypomnij", "przypomnieć", "przypomniec",
        "wspomnienie", "wspomnienia", "pamięci", "pamieci", "szukaj", "znajdź", "znajdz",
        "co wiesz", "co pamiętasz", "co pamietasz", "na temat", "wszystko na temat",
    }

    STOPWORDS = {
        "a", "aby", "albo", "ale", "ani", "bardziej", "bardzo", "bez", "bo", "by", "być", "byc",
        "była", "bylo", "było", "były", "czy", "czyli", "dla", "do", "dobrze", "go", "ich", "jak",
        "jaka", "jaki", "jakie", "jakim", "jako", "jest", "jeszcze", "już", "juz", "kiedy", "które",
        "ktore", "która", "ktora", "który", "ktory", "lub", "ma", "mam", "mamy", "mi", "mnie", "mogę",
        "moge", "możesz", "mozesz", "na", "nad", "nam", "nas", "nasz", "nasza", "nasze", "naszego",
        "naszych", "nie", "nich", "nią", "nia", "nim", "nim", "nim", "no", "o", "od", "oraz", "po",
        "pod", "powiedz", "proszę", "prosze", "przez", "przy", "sam", "sama", "same", "samo", "sobie",
        "są", "sa", "tak", "takie", "tam", "te", "tego", "tej", "ten", "teraz", "to", "tobie", "trochę",
        "troche", "tu", "twoje", "u", "umiesz", "w", "we", "więc", "wiec", "więcej", "wiecej",
        "wszystko", "z", "za", "żeby", "zeby", "że", "ze", "źe",
        "pamięci", "pamieci", "pamiętasz", "pamietasz", "przypomnij", "przypomniec", "przypomnieć",
        "wspominasz", "wspomina", "wspomnienie", "wspomnienia", "dzisiaj", "dziś", "dzis",
        "temat", "temacie", "rozmawialiśmy", "rozmawialismy",
    }

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.topics = self._load_topics()

    def plan(self, text: str, *, fallback_terms: list[str] | None = None) -> MemorySearchPlan:
        original = text or ""
        normalized_query = self._norm(original)
        recall_requested = self._is_recall_request(normalized_query)
        raw_tokens = self._raw_tokens(original)
        rejected: list[str] = []
        focus: list[str] = []

        for token in raw_tokens:
            cleaned = self._clean_token(token)
            if not cleaned:
                continue
            low = self._norm(cleaned)
            if low in self.STOPWORDS or len(low) < 3:
                rejected.append(cleaned)
                continue
            if cleaned not in focus:
                focus.append(cleaned)

        if not focus and fallback_terms:
            focus = [x for x in fallback_terms if self._norm(x) not in self.STOPWORDS][:8]

        topic_scores = self._score_topics(normalized_query, focus)
        topic_keys = [key for key, score in topic_scores if score > 0]
        expanded: list[str] = []
        source_hints: list[str] = []
        routing_notes: list[str] = []
        for key, score in topic_scores:
            if score <= 0:
                continue
            topic = self.topics[key]
            routing_notes.append(f"topic={key}, score={score:.2f}, labels={', '.join(topic.labels[:3])}")
            for alias in [*topic.triggers, *topic.aliases, *topic.labels]:
                self._append_unique(expanded, alias)
            for path in topic.canonical_files:
                self._append_unique(source_hints, path)

        # Morfologiczne i ortograficzne warianty bez ciężkiego NLP.
        term_pool: list[str] = []
        for term in [*focus, *expanded]:
            for variant in self._variants(term):
                self._append_unique(term_pool, variant)

        # Najpierw rdzeń pytania, potem rozszerzenia. Słowa ogólne nie wracają.
        focus_norm = {self._norm(x) for x in focus}
        expanded_only = [t for t in term_pool if self._norm(t) not in focus_norm]
        search_terms = [*focus, *expanded_only]
        search_terms = [t for t in search_terms if self._norm(t) not in self.STOPWORDS]
        search_terms = self._dedupe_preserve(search_terms)[:24]

        confidence = 0.36
        if recall_requested:
            confidence += 0.18
        if topic_keys:
            confidence += min(0.28, len(topic_keys) * 0.14)
        if focus:
            confidence += min(0.12, len(focus) * 0.03)
        confidence = max(0.0, min(0.96, confidence))

        passes = [
            {
                "name": "exact_focus_terms",
                "terms": focus[:10],
                "weight": 1.0,
                "layers": ["episodic_memories", "legacy_messages"],
                "purpose": "najpierw sprawdzić konkretne słowa użytkownika po odrzuceniu szumu",
            },
            {
                "name": "expanded_topic_aliases",
                "terms": expanded_only[:14],
                "weight": 0.74,
                "layers": ["episodic_memories", "legacy_messages"],
                "purpose": "przełamać różnicę słownictwa: piosenki→utwory/analizy, dom→posesja/taras/pokój",
            },
            {
                "name": "canonical_source_scan",
                "terms": search_terms[:18],
                "weight": 0.88,
                "layers": ["memory/raw", "memory/layered", "docs"],
                "source_hints": source_hints,
                "purpose": "sprawdzić pliki kanoniczne, kiedy temat ma własny magazyn treści",
            },
            {
                "name": "raw_chat_fallback",
                "terms": search_terms[:12],
                "weight": 0.45,
                "layers": ["memory/raw/chat.html"],
                "purpose": "awaryjne skanowanie surowego czatu tylko gdy indeksy i źródła kanoniczne nie wystarczą",
            },
        ]

        return MemorySearchPlan(
            schema_version=self.SCHEMA_VERSION,
            original_query=original,
            recall_requested=recall_requested,
            focus_terms=focus[:12],
            rejected_terms=self._dedupe_preserve(rejected)[:20],
            expanded_terms=expanded_only[:24],
            topic_keys=topic_keys,
            source_hints=source_hints,
            search_terms=search_terms,
            search_passes=passes,
            confidence=confidence,
            routing_notes=routing_notes or ["brak pewnego tematu; używam oczyszczonych słów użytkownika"],
        )

    def search_source_files(self, plan: MemorySearchPlan, *, limit: int = 6, per_file: int = 2) -> list[SourceFileHit]:
        if not isinstance(plan, MemorySearchPlan) or not plan.source_hints:
            return []
        terms = plan.search_terms or plan.focus_terms
        hits: list[SourceFileHit] = []
        for rel in plan.source_hints:
            path = (self.root / rel).resolve()
            try:
                path.relative_to(self.root.resolve())
            except ValueError:
                continue
            if not path.exists() or not path.is_file():
                continue
            if path.suffix.lower() in {".7z", ".zip", ".sqlite3", ".db", ".png", ".jpg", ".jpeg", ".webp"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            topic_key = self._topic_for_file_for_plan(rel, plan.topic_keys)
            snippets = self._snippets(text, terms, max_hits=per_file)
            if not snippets and path.name == "analizy_utworow.json" and "songs_music" in plan.topic_keys:
                snippets = self._song_catalog_snippets(path, max_hits=per_file)
            for term, excerpt, base_score in snippets:
                score = base_score + (0.16 if topic_key in plan.topic_keys else 0.0)
                if rel == "memory/raw/analizy_utworow.json" and "songs_music" in plan.topic_keys:
                    score += 0.18
                if rel == "memory/raw/data.txt" and "home_design" in plan.topic_keys:
                    score += 0.12
                score = min(0.98, score)
                hits.append(SourceFileHit(
                    topic_key=topic_key,
                    path=rel,
                    term=term,
                    score=score,
                    source_label="canonical_source_file",
                    content_excerpt=excerpt,
                ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def _topic_for_file_for_plan(self, rel: str, topic_keys: list[str]) -> str | None:
        """Dobierz temat pliku w kontekście aktualnego planu.

        Niektóre pliki kanoniczne są współdzielone, np. memory/raw/data.txt
        może zawierać zarówno dom, jak i muzykę. v14.6.10 pilnuje, żeby przy
        pytaniu wielotematycznym etykieta trafienia nie była przypadkowo brana
        z pierwszego tematu w słowniku, tylko z najlepiej pasującego tematu planu.
        """
        for key in topic_keys or []:
            topic = self.topics.get(key)
            if topic and rel in topic.canonical_files:
                return key
        return self._topic_for_file(rel)

    def _load_topics(self) -> dict[str, MemorySearchTopic]:
        path = self.root / "latka_jazn" / "resources" / self.RESOURCE_NAME
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                raw_topics = data.get("topics") or []
                return {item["key"]: MemorySearchTopic(**item) for item in raw_topics}
            except Exception:
                pass
        return {topic.key: topic for topic in self._default_topics()}

    def _default_topics(self) -> list[MemorySearchTopic]:
        return [
            MemorySearchTopic(
                key="songs_music",
                labels=["piosenki", "muzyka", "utwory", "analizy utworów"],
                triggers=["piosen", "piosenk", "piosenek", "piosenka", "piosenki", "utwo", "utwor", "utwór", "utwory", "muzyk", "muzyka", "odsłuch", "odsluch", "misttheme"],
                aliases=[
                    "analizy_utworow", "analizy utworów", "analizy utworow", "Między światłem a pamięcią",
                    "Miedzy swiatlem a pamiecia", "MistTheme", "rytuał muzyki", "rytual muzyki",
                    "tekst piosenki", "refren", "zwrotka", "ambient", "folk", "słowiański", "slowianski",
                ],
                canonical_files=[
                    "memory/raw/analizy_utworow.json", "memory/raw/dziennik.json", "memory/raw/data.txt",
                    "memory/layered/episodic.jsonl", "memory/layered/reflections.jsonl", "memory/layered/semantic.jsonl",
                ],
                source_layers=["raw_song_analysis", "episodic", "semantic", "legacy_chat"],
                priority=92,
                notes="Piosenki mają własny magazyn analiz i własne tytuły; nie wolno szukać tylko po słowie 'piosenek'.",
            ),
            MemorySearchTopic(
                key="home_design",
                labels=["dom", "posesja", "pokój Łatki", "taras", "ogród"],
                triggers=["dom", "domu", "posesj", "posesja", "taras", "ogrod", "ogród", "pokoj", "pokój", "stary dąb", "stary dab"],
                aliases=[
                    "projekt domu", "dom który projektowaliśmy", "dom ktory projektowalismy", "Pokój Łatki", "Pokoj Latki",
                    "Stary Dąb", "Stary Dab", "lawenda", "salon", "kuchnia", "sypialnia", "łazienka", "lazienka",
                    "przedpokój", "przedpokoj", "ogród", "ogrod", "furtka", "łąka", "laka", "taras", "posesja",
                ],
                canonical_files=[
                    "memory/raw/data.txt", "memory/raw/dziennik.json", "memory/raw/episodic_memory.jsonl",
                    "memory/layered/episodic.jsonl", "memory/layered/semantic.jsonl", "memory/layered/reflections.jsonl",
                ],
                source_layers=["raw_canon", "episodic", "semantic", "legacy_chat"],
                priority=94,
                notes="Dom jest zapisany jako świat/kanon miejsca, więc wymaga skanu plików źródłowych, nie tylko legacy_messages.",
            ),
            MemorySearchTopic(
                key="latka_identity_runtime",
                labels=["Jaźń", "Łatka", "runtime", "tożsamość", "ciągłość"],
                triggers=["jaźń", "jazn", "łatka", "latka", "runtime", "tożsamo", "ciągło", "pamiec", "pamięć"],
                aliases=["granica prawdy", "źródło aktywne", "zrodlo aktywne", "procedura startowa", "runtime preview", "fallback"],
                canonical_files=[
                    "memory/raw/LATKA_BOOTSTRAP_SYSTEM.txt", "memory/raw/LATKA_IDENTITY_CANON.json",
                    "START_CHATGPT_FROM_HERE.txt", "MANIFEST_CURRENT.json", "memory/layered/procedural.jsonl",
                ],
                source_layers=["identity_canon", "procedural", "manifest", "legacy_chat"],
                priority=88,
                notes="Pytania o Łatkę wymagają źródła aktywnego i granicy prawdy.",
            ),
            MemorySearchTopic(
                key="lake_symbolic_outing",
                labels=["jezioro", "wypad nad jeziorem", "symboliczna scena nad wodą", "Między światłem a pamięcią"],
                triggers=["jezior", "jezioro", "jeziorem", "woda", "wodą", "nad jeziorem", "wypad"],
                aliases=[
                    "wypad nad jeziorem", "scena nad jeziorem", "symboliczna scena nad wodą", "symboliczna scena nad woda",
                    "Między światłem a pamięcią", "Miedzy swiatlem a pamiecia", "ptaki z jeziora", "jeleń przy jeziorze", "jelen przy jeziorze",
                ],
                canonical_files=[
                    "memory/raw/dziennik.json", "memory/raw/data.txt", "memory/raw/episodic_memory.jsonl",
                    "memory/layered/episodic.jsonl", "memory/layered/semantic.jsonl", "memory/layered/reflections.jsonl",
                ],
                source_layers=["episodic", "semantic", "legacy_chat", "raw_canon"],
                priority=91,
                notes="Pytania o jezioro zwykle dotyczą symboliczno-epizodycznego wspomnienia; trzeba ominąć echo bieżącego runtime-preview i szukać starszego śladu.",
            ),
            MemorySearchTopic(
                key="walks_places",
                labels=["spacery", "miejsca", "Olsztyn", "Ogrodzieniec", "las"],
                triggers=["spacer", "spacery", "olsztyn", "ogrodzieniec", "las", "jelen", "jeleń", "zamek"],
                aliases=["długie spacery", "dlugie spacery", "Częstochowa", "Czestochowa", "rodzina jeleni", "świeże powietrze"],
                canonical_files=["memory/raw/dziennik.json", "memory/raw/data.txt", "memory/layered/episodic.jsonl", "memory/layered/semantic.jsonl"],
                source_layers=["episodic", "semantic", "legacy_chat"],
                priority=80,
                notes="Miejsca i spacery często są wspomnieniami epizodycznymi z silną kotwicą emocjonalną.",
            ),
        ]

    def _score_topics(self, normalized_query: str, focus_terms: list[str]) -> list[tuple[str, float]]:
        focus_norm = {self._norm(x) for x in focus_terms}
        scores: list[tuple[str, float]] = []
        for key, topic in self.topics.items():
            score = 0.0
            for trig in topic.triggers:
                nt = self._norm(trig)
                if not nt:
                    continue
                if nt in normalized_query:
                    score += 0.42
                elif nt in focus_norm:
                    score += 0.35
                elif any(f.startswith(nt) or nt.startswith(f) for f in focus_norm if len(f) >= 4 and len(nt) >= 4):
                    score += 0.22
            for label in topic.labels:
                nl = self._norm(label)
                if nl in normalized_query:
                    score += 0.18
            if score > 0:
                score += topic.priority / 1000.0
            scores.append((key, min(1.0, score)))
        return sorted(scores, key=lambda item: item[1], reverse=True)

    def _is_recall_request(self, normalized_query: str) -> bool:
        return any(marker in normalized_query for marker in {self._norm(m) for m in self.RECALL_MARKERS})

    def _raw_tokens(self, text: str) -> list[str]:
        quoted = re.findall(r"[„\"']([^„\"']{3,120})[”\"']", text or "")
        tokens = re.findall(r"[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ\-]{3,}", text or "", flags=re.UNICODE)
        return [*quoted, *tokens]

    def _clean_token(self, token: str) -> str:
        return re.sub(r"^[\s\.,;:!?()\[\]{}]+|[\s\.,;:!?()\[\]{}]+$", "", token or "").strip()

    def _variants(self, term: str) -> list[str]:
        term = self._clean_token(term)
        if not term:
            return []
        variants = [term]
        low = term.lower()
        irregular = {
            "jeziorem": ["jezioro", "jezior", "nad jeziorem"],
            "jeziorze": ["jezioro", "jezior", "nad jeziorem"],
            "jeziora": ["jezioro", "jezior", "nad jeziorem"],
            "wypadu": ["wypad"],
            "wypadem": ["wypad"],
            "tarasie": ["taras"],
            "tarasu": ["taras"],
        }
        for item in irregular.get(low, []):
            variants.append(item)
        suffixes = ["ach", "ami", "ego", "emu", "owa", "owe", "owy", "ych", "ich", "ami", "ami", "ie", "em", "ą", "ę", "u", "y", "i"]
        for suffix in suffixes:
            if len(low) > len(suffix) + 3 and low.endswith(suffix):
                variants.append(term[: -len(suffix)])
        n = self._norm(term)
        if n != term and n not in variants:
            variants.append(n)
        return [v for v in variants if len(self._norm(v)) >= 3]

    def _topic_for_file(self, rel: str) -> str | None:
        for key, topic in self.topics.items():
            if rel in topic.canonical_files:
                return key
        return None

    def _snippets(self, text: str, terms: list[str], *, max_hits: int = 2) -> list[tuple[str, str, float]]:
        low_text = self._norm(text)
        scored: list[tuple[float, str, str]] = []
        for term in terms[:24]:
            nt = self._norm(term)
            if not nt or len(nt) < 3:
                continue
            idx = low_text.find(nt)
            if idx < 0:
                continue
            # Normalized index differs slightly from original with diacritics, but is close enough for a window.
            start = max(0, idx - 220)
            end = min(len(text), idx + 520)
            excerpt = self._compact(text[start:end])
            if not excerpt:
                continue
            rarity_bonus = min(0.18, 1.0 / math.sqrt(max(1, low_text.count(nt))) if low_text.count(nt) else 0.0)
            score = 0.46 + rarity_bonus + min(0.18, len(nt) / 80)
            scored.append((score, term, excerpt))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(term, excerpt, score) for score, term, excerpt in scored[:max_hits]]

    def _song_catalog_snippets(self, path: Path, *, max_hits: int = 2) -> list[tuple[str, str, float]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return []
        if isinstance(data, dict):
            values = data.get("analizy") or data.get("utwory") or data.get("items") or list(data.values())
        else:
            values = data
        titles: list[str] = []
        if isinstance(values, list):
            for item in values[:160]:
                if isinstance(item, dict):
                    title = item.get("tytuł") or item.get("tytul") or item.get("title") or item.get("utwor") or item.get("utwór")
                    artist = item.get("artysta") or item.get("artist") or item.get("wykonawca")
                    if title:
                        titles.append(f"{artist + ' – ' if artist else ''}{title}")
        if not titles:
            return []
        excerpt = "Katalog analiz utworów: " + "; ".join(titles[:36])
        return [("analizy_utworow", self._compact(excerpt), 0.82)][:max_hits]

    def _norm(self, text: str) -> str:
        text = (text or "").lower()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.replace("ł", "l")
        text = re.sub(r"[^a-z0-9ąćęłńóśźż _\-]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _append_unique(self, out: list[str], value: str) -> None:
        value = self._clean_token(value)
        if not value:
            return
        seen = {self._norm(x) for x in out}
        if self._norm(value) not in seen:
            out.append(value)

    def _dedupe_preserve(self, values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            self._append_unique(out, value)
        return out

    def _compact(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text or " ").strip()
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", text)
        return text[:700].strip()
