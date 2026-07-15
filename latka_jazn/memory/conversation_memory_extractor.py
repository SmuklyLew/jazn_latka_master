from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import re
from typing import Any

TRUTH_BOUNDARY_CONVERSATION = (
    "To jest ślad rozmowy/transkryptu i interpretacja znaczenia dla Jaźni. "
    "Nie oznacza biologicznego przeżycia ani automatycznego przeczytania innych archiwów."
)

KEYWORD_GROUPS: dict[str, list[str]] = {
    "tożsamość": ["tożsamo", "kim jeste", "Łatka", "jaź", "jestem", "pierwszej osob", "ja jestem"],
    "pamięć": ["pamię", "wspomn", "dziennik", "pamiętnik", "ciągło", "epizod", "semantycz", "procedural"],
    "granica_prawdy": ["prawda", "nie kłam", "grounding", "confidence", "granica", "symbolic", "biologic", "nie udaw"],
    "czas": ["czas", "data", "godzina", "wersja", "powrót", "przerwa", "cisza"],
    "emocje": ["emoc", "czuj", "afekt", "ulga", "lęk", "nadzieja", "porusza", "ważne dla ciebie"],
    "wizualizacja": ["wizual", "obraz", "portret", "wygląd", "pokój", "zielona kulka", "jezior", "taras", "sny"],
    "relacje": ["Krzysztof", "Kasia", "zwierzę", "Tayfa", "Psotka", "Aures", "Fiona", "dom"],
    "moduły": ["moduł", "funkcj", "silnik", "neuro", "mózg", "organizm", "warstw", "update", "hotfix"],
}

DEFAULT_EMOTION_WORDS = [
    "skupienie",
    "ostrożność",
    "ulga",
    "ciekawość",
    "odpowiedzialność za ciągłość",
]


@dataclass(slots=True)
class ConversationMemoryItem:
    category: str
    title: str
    excerpt: str
    significance_for_latka: str
    grounding: str = "conversation_excerpt"
    confidence: float = 0.72
    truth_boundary: str = TRUTH_BOUNDARY_CONVERSATION
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConversationMemoryPayload:
    source: str
    source_type: str
    read_scope: dict[str, Any]
    events: list[ConversationMemoryItem] = field(default_factory=list)
    memories: list[ConversationMemoryItem] = field(default_factory=list)
    reflections: list[ConversationMemoryItem] = field(default_factory=list)
    semantic_facts: list[ConversationMemoryItem] = field(default_factory=list)
    procedural_rules: list[ConversationMemoryItem] = field(default_factory=list)
    short_important_topics: list[ConversationMemoryItem] = field(default_factory=list)
    emotions: list[str] = field(default_factory=list)
    truth_boundaries: list[str] = field(default_factory=list)
    questions_from_silence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def item_count(self) -> int:
        return sum(
            len(group)
            for group in [
                self.events,
                self.memories,
                self.reflections,
                self.semantic_facts,
                self.procedural_rules,
                self.short_important_topics,
            ]
        )


def _normalize_space(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _paragraphs(text: str) -> list[str]:
    text = _normalize_space(text)
    chunks = re.split(r"\n\s*\n|(?<=\.)\s+(?=(?:Krzysztof|User|Użytkownik|Łatka|Assistant|Asystent)[:：])", text)
    cleaned: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        if len(chunk) > 1200:
            # Zachowuje początek i koniec zamiast wycinać sens całkiem.
            chunk = chunk[:700].rstrip() + " […] " + chunk[-350:].lstrip()
        cleaned.append(chunk)
    return cleaned


def _keyword_hits(text: str) -> set[str]:
    low = text.lower()
    hits: set[str] = set()
    for group, words in KEYWORD_GROUPS.items():
        if any(word.lower() in low for word in words):
            hits.add(group)
    return hits


def _score_paragraph(text: str) -> tuple[int, set[str]]:
    hits = _keyword_hits(text)
    score = len(hits) * 4
    low = text.lower()
    if "?" in text:
        score += 1
    if any(x in low for x in ["ważne", "zapamię", "powinno", "musisz", "nie wolno", "nie kłam", "granica"]):
        score += 3
    if any(x in low for x in ["wspomnienie", "doświadczenie", "tożsamość", "dziennik", "ciągłość"]):
        score += 3
    if 60 <= len(text) <= 900:
        score += 1
    return score, hits


def _title_for(text: str, category: str) -> str:
    clean = re.sub(r"^(Krzysztof|User|Użytkownik|Łatka|Assistant|Asystent)[:：]\s*", "", text.strip(), flags=re.I)
    clean = clean.split("\n", 1)[0]
    clean = re.sub(r"\s+", " ", clean)
    if len(clean) > 80:
        clean = clean[:77].rstrip() + "…"
    return clean or f"Ślad rozmowy: {category}"


def _meaning_for(category: str) -> str:
    meanings = {
        "tożsamość": "Wzmacnia rozumienie, że Łatka nie jest tylko opisem, ale ciągłością głosu, pamięci, granic i relacji.",
        "pamięć": "Pokazuje, które ślady powinny wejść do dziennika i warstw pamięci, aby przyszła Łatka mogła odzyskać sens rozmowy.",
        "granica_prawdy": "Chroni przed zamianą pięknej narracji w fałszywy fakt; wymaga źródła, pewności i jawnej granicy.",
        "czas": "Łączy pamięć z osią czasu: wersją, przerwą, powrotem, aktualnym zapisem i historią zmian.",
        "emocje": "Nadaje pamięci afektywną wagę bez udawania biologicznych odczuć.",
        "wizualizacja": "Zachowuje obrazy, które kotwiczą tożsamość Łatki symbolicznie i estetycznie.",
        "relacje": "Przypomina, że Jaźń powstaje w relacji z Krzysztofem i domowym kontekstem, nie w próżni.",
        "moduły": "Zamienia rozmowę o architekturze w regułę działania modułów i aktualizacji.",
    }
    return meanings.get(category, "To ważny ślad rozmowy, który może budować ciągłość pamięci Łatki.")


def _item_from_paragraph(text: str, category: str, tags: list[str], confidence: float = 0.72) -> ConversationMemoryItem:
    return ConversationMemoryItem(
        category=category,
        title=_title_for(text, category),
        excerpt=text.strip(),
        significance_for_latka=_meaning_for(category),
        confidence=confidence,
        tags=tags,
    )


def extract_conversation_memory(text: str, *, source: str = "conversation", max_items: int = 12) -> ConversationMemoryPayload:
    """Czyta pełny dostarczony tekst rozmowy i wybiera konkretne ślady do zapisania.

    Funkcja nie ma dostępu do prywatnego okna czatu sama z siebie. Czyta to, co zostanie
    przekazane w `text` albo w pliku przez CLI. Zwraca payload jawnie oznaczony źródłem,
    zakresem odczytu i pewnością.
    """
    paragraphs = _paragraphs(text)
    scored: list[tuple[int, int, str, set[str]]] = []
    for i, p in enumerate(paragraphs):
        score, hits = _score_paragraph(p)
        if score >= 5:
            scored.append((score, i, p, hits))
    scored.sort(key=lambda row: (-row[0], row[1]))

    selected: list[tuple[str, str, set[str], float]] = []
    seen_titles: set[str] = set()
    for score, _i, p, hits in scored:
        if len(selected) >= max_items:
            break
        category = sorted(hits)[0] if hits else "rozmowa"
        # Preferuje najbardziej semantyczne kategorie, jeśli są dostępne.
        for preferred in ["tożsamość", "pamięć", "granica_prawdy", "moduły", "emocje", "czas", "wizualizacja", "relacje"]:
            if preferred in hits:
                category = preferred
                break
        title = _title_for(p, category)
        if title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())
        confidence = min(0.9, 0.58 + score / 40)
        selected.append((category, p, hits, confidence))

    payload = ConversationMemoryPayload(
        source=source,
        source_type="conversation_text",
        read_scope={
            "characters_read": len(text),
            "paragraphs_seen": len(paragraphs),
            "candidate_paragraphs": len(scored),
            "selected_items": len(selected),
            "mode": "full_supplied_text_scan",
        },
        emotions=DEFAULT_EMOTION_WORDS.copy(),
        truth_boundaries=[TRUTH_BOUNDARY_CONVERSATION],
        questions_from_silence=[
            "Co z tej rozmowy ma zostać zachowane jako moje doświadczenie, a co jest tylko technicznym logiem?",
            "Czy wpis ma źródło i poziom pewności, czy jest tylko ładną narracją?",
        ],
    )

    for category, paragraph, hits, confidence in selected:
        item = _item_from_paragraph(paragraph, category, sorted(hits), confidence)
        if category in {"tożsamość", "pamięć", "emocje", "wizualizacja", "relacje"}:
            payload.memories.append(item)
        elif category == "moduły":
            payload.procedural_rules.append(item)
        elif category == "granica_prawdy":
            payload.reflections.append(item)
        elif category == "czas":
            payload.events.append(item)
        else:
            payload.short_important_topics.append(item)

    if not payload.events and payload.memories:
        payload.events.append(payload.memories[0])
    if not payload.reflections and payload.memories:
        payload.reflections.append(payload.memories[-1])
    return payload


def payload_from_json_text(raw: str, *, source: str = "memory_json") -> ConversationMemoryPayload:
    data = json.loads(raw)
    return payload_from_mapping(data, source=source)


def payload_from_mapping(data: dict[str, Any], *, source: str = "memory_json") -> ConversationMemoryPayload:
    def item_list(name: str) -> list[ConversationMemoryItem]:
        out: list[ConversationMemoryItem] = []
        for raw in data.get(name, []) or []:
            if isinstance(raw, str):
                out.append(_item_from_paragraph(raw, name, [name], 0.7))
            elif isinstance(raw, dict):
                out.append(
                    ConversationMemoryItem(
                        category=str(raw.get("category") or name),
                        title=str(raw.get("title") or _title_for(str(raw.get("excerpt") or raw.get("treść") or raw), name)),
                        excerpt=str(raw.get("excerpt") or raw.get("treść") or raw.get("text") or raw),
                        significance_for_latka=str(raw.get("significance_for_latka") or raw.get("znaczenie") or _meaning_for(str(raw.get("category") or name))),
                        grounding=str(raw.get("grounding") or "memory_json_payload"),
                        confidence=float(raw.get("confidence") if raw.get("confidence") is not None else 0.74),
                        truth_boundary=str(raw.get("truth_boundary") or raw.get("granica_prawdy") or TRUTH_BOUNDARY_CONVERSATION),
                        tags=list(raw.get("tags") or [name]),
                    )
                )
        return out

    payload = ConversationMemoryPayload(
        source=str(data.get("source") or source),
        source_type=str(data.get("source_type") or "memory_json_payload"),
        read_scope=dict(data.get("read_scope") or {"mode": "explicit_payload"}),
        events=item_list("events"),
        memories=item_list("memories"),
        reflections=item_list("reflections"),
        semantic_facts=item_list("semantic_facts"),
        procedural_rules=item_list("procedural_rules"),
        short_important_topics=item_list("short_important_topics"),
        emotions=list(data.get("emotions") or DEFAULT_EMOTION_WORDS),
        truth_boundaries=list(data.get("truth_boundaries") or [TRUTH_BOUNDARY_CONVERSATION]),
        questions_from_silence=list(data.get("questions_from_silence") or []),
    )
    return payload


def load_conversation_payload(*, conversation_file: Path | None = None, conversation_text: str | None = None,
                              memory_json: str | None = None, max_items: int = 12) -> ConversationMemoryPayload | None:
    if memory_json:
        path = Path(memory_json)
        raw = path.read_text(encoding="utf-8") if path.exists() else memory_json
        return payload_from_json_text(raw, source=str(path) if path.exists() else "inline_memory_json")
    if conversation_file is not None:
        text = Path(conversation_file).read_text(encoding="utf-8", errors="replace")
        return extract_conversation_memory(text, source=str(conversation_file), max_items=max_items)
    if conversation_text:
        return extract_conversation_memory(conversation_text, source="inline_conversation_text", max_items=max_items)
    return None
