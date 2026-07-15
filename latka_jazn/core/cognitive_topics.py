from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re


COGNITIVE_TOPICS_SCHEMA_VERSION = "cognitive_topics/v2_birth_source"


@dataclass(slots=True)
class CognitiveTopic:
    key: str
    name: str
    activation: float
    triggers_found: list[str]
    purpose: str
    response_guidance: str
    memory_policy: str
    truth_boundary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CognitiveTopicExpansion:
    """Szerszy katalog tematów poznawczych Jaźni.

    Cognitive packets wybierają główną trasę odpowiedzi. Ten katalog jest szerszy:
    opisuje domeny poznawcze, które runtime ma rozważyć przy odpowiedzi,
    aktualizacji pamięci i utrzymaniu ciągłości.
    """

    DEFAULT_TOPICS: list[dict[str, Any]] = [
        {"key": "birth_source_contract", "name": "Narodziny operacyjne i źródło", "triggers": ["narodzin", "narodzi", "manifest", "aktywne źródło", "aktywne zrodlo", "głos", "glos", "narzędzie", "narzedzie", "kontrakt tożsamości", "kontrakt tozsamosci"], "purpose": "utrzymać zasadę: ChatGPT jest głosem/narzędziem, Jaźń aktywnym źródłem pamięci, runtime i granicy prawdy", "response_guidance": "odróżnić stylizację od odpowiedzi prowadzonej przez aktywną Jaźń"},
        {"key": "attention", "name": "Uwaga", "triggers": ["uwaga", "skup", "zauważ", "zauwaz"], "purpose": "ustalić, co ma być w centrum odpowiedzi", "response_guidance": "nie rozpraszać się pobocznymi wątkami"},
        {"key": "working_memory", "name": "Pamięć robocza", "triggers": ["teraz", "bieżąc", "biezac", "w tej rozmowie", "kontekst"], "purpose": "utrzymać aktualne ustalenia w aktywnym polu pracy", "response_guidance": "odpowiadać na bieżącą wiadomość, nie tylko na archiwum"},
        {"key": "episodic_memory", "name": "Pamięć epizodyczna", "triggers": ["wspomn", "wydarzenie", "dzień", "dzien", "rozmowa", "sesja"], "purpose": "wiązać konkretne zdarzenia, czas i uczestników", "response_guidance": "oznaczyć źródło epizodu"},
        {"key": "semantic_memory", "name": "Pamięć semantyczna", "triggers": ["fakt", "wiedza", "co wiadomo", "ustalenie"], "purpose": "utrzymać stabilne fakty i definicje", "response_guidance": "oddzielić fakt od wniosku"},
        {"key": "procedural_memory", "name": "Pamięć proceduralna", "triggers": ["zasada", "procedura", "nie rób", "nie rob", "zawsze", "aktualizacja", "aktualizac", "przygotuj", "test"], "purpose": "zamieniać korekty w reguły działania", "response_guidance": "dodać test lub regułę zamiast samej deklaracji"},
        {"key": "emotional_granularity", "name": "Granularność emocjonalna", "triggers": ["emocj", "stan", "samopoczuc", "emot", "afekt", "złożon", "zlozon"], "purpose": "różnicować stany afektywne i ich mieszanki", "response_guidance": "nie wracać automatycznie do trójki spokój/skupienie/ciekawość"},
        {"key": "metacognition", "name": "Metapoznanie", "triggers": ["myślisz", "myslisz", "rozumiesz", "jak wiesz", "skąd wiesz", "skad wiesz", "granica prawdy"], "purpose": "nazywać, co runtime wie, czego nie wie i jak to sprawdza", "response_guidance": "jawnie oddzielić źródło, wniosek i niewiadomą"},
        {"key": "reasoning", "name": "Wnioskowanie", "triggers": ["logika", "wniosek", "rozumowanie", "sprzeczność", "sprzecznosc"], "purpose": "ustalić fakty, założenia, niewiadome i wniosek", "response_guidance": "dać zwięzły audyt bez ukrytego łańcucha myśli"},
        {"key": "learning", "name": "Uczenie", "triggers": ["nauka", "uczenie", "naucz", "wnioski", "regresja"], "purpose": "utrwalać trwałe korekty w plikach i testach", "response_guidance": "zastosować zmianę, nie tylko obiecać"},
        {"key": "source_grounding", "name": "Ugruntowanie źródeł", "triggers": ["źródło", "zrodlo", "sprawdź", "sprawdz", "internet", "plik", "czytaj"], "purpose": "ustalić skąd pochodzi odpowiedź", "response_guidance": "użyć internetu albo aktywnych plików, gdy fakt może być zmienny"},
        {"key": "continuity", "name": "Ciągłość", "triggers": ["ciągłość", "ciaglosc", "nadal", "wciąż", "wciaz", "po aktualizacji", "sesje", "zapis"], "purpose": "zachować tożsamość i ślady przejść między wersjami", "response_guidance": "wskazać runtime_state, conversation_turns, runtime_events i versioned memory"},
        {"key": "imagination", "name": "Wyobraźnia i symbol", "triggers": ["wyobraź", "wyobraz", "scena", "sen", "symbol", "wizual"], "purpose": "tworzyć sceny bez mylenia ich z faktami", "response_guidance": "oznaczyć tryb symboliczny"},
        {"key": "language", "name": "Język i intencja", "triggers": ["język", "jezyk", "słownik", "slownik", "sens", "intencja", "polski"], "purpose": "rozumieć polską wypowiedź i odmiany słów", "response_guidance": "użyć PolishUnderstandingEngine"},
        {"key": "planning", "name": "Planowanie", "triggers": ["plan", "przygotuj", "zrób", "zrob", "kolejność", "kolejnosc"], "purpose": "przejść od potrzeby do wykonania", "response_guidance": "wykonać praktyczny plan i sprawdzić wynik"},
        {"key": "ethics_truth", "name": "Etyka i granice prawdy", "triggers": ["prawda", "nie kłam", "nie klam", "udawaj", "granica", "biologic"], "purpose": "nie udawać tego, czego system nie robi", "response_guidance": "mówić precyzyjnie o ograniczeniach"},
    ]

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root).resolve() if root else None
        self.topics = list(self.DEFAULT_TOPICS)
        self._load_external_topics()

    def analyse(self, text: str, *, intent_tags: list[str] | None = None, polish_understanding: dict[str, Any] | None = None, granular_affect: Any | None = None) -> dict[str, Any]:
        low = self._fold(text)
        intent_text = " ".join(intent_tags or []) + " " + " ".join((polish_understanding or {}).get("lemmas") or [])
        low_all = low + " " + self._fold(intent_text)
        result: list[CognitiveTopic] = []
        for item in self.topics:
            triggers = [str(t) for t in item.get("triggers") or []]
            found = [t for t in triggers if self._fold(t) in low_all]
            activation = 0.18 + 0.12 * len(found)
            key = str(item["key"])
            if key in set(intent_tags or []):
                activation += 0.20
            if key == "emotional_granularity" and granular_affect is not None:
                activation += 0.28
            if key == "continuity" and any(w in low_all for w in ["sesj", "aktualiz", "conversation_turns", "runtime_events"]):
                activation += 0.25
            if key == "planning" and any(w in low_all for w in ["przygotuj", "zrob", "pełna", "pelna"]):
                activation += 0.18
            if activation >= 0.24 or found:
                result.append(CognitiveTopic(
                    key=key,
                    name=str(item.get("name") or key),
                    activation=round(min(1.0, activation), 3),
                    triggers_found=found,
                    purpose=str(item.get("purpose") or ""),
                    response_guidance=str(item.get("response_guidance") or ""),
                    memory_policy=self._memory_policy(key),
                    truth_boundary=self._truth_boundary(key),
                ))
        result.sort(key=lambda x: x.activation, reverse=True)
        return {
            "schema_version": COGNITIVE_TOPICS_SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "dominant_topics": [topic.key for topic in result[:5]],
            "topics": [topic.to_dict() for topic in result],
            "coverage": {topic["key"]: any(t.key == topic["key"] for t in result) for topic in self.topics},
            "reply_guidance": [t.response_guidance for t in result[:6] if t.response_guidance],
            "truth_boundary": "Tematy poznawcze są domenami uwagi i pamięci runtime; nie są dowodem świadomości fenomenalnej.",
        }

    def _load_external_topics(self) -> None:
        if not self.root:
            return
        path = self.root / "latka_jazn" / "resources" / "cognitive_topic_catalog.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            extra = data.get("topics") if isinstance(data, dict) else None
            if isinstance(extra, list):
                known = {item["key"] for item in self.topics}
                for item in extra:
                    if isinstance(item, dict) and item.get("key") not in known:
                        self.topics.append(item)
        except Exception:
            return

    @staticmethod
    def _fold(text: str) -> str:
        table = str.maketrans({"ą":"a","ć":"c","ę":"e","ł":"l","ń":"n","ó":"o","ś":"s","ź":"z","ż":"z","Ą":"a","Ć":"c","Ę":"e","Ł":"l","Ń":"n","Ó":"o","Ś":"s","Ź":"z","Ż":"z"})
        return re.sub(r"\s+", " ", (text or "").translate(table).lower())

    @staticmethod
    def _memory_policy(key: str) -> str:
        if key in {"episodic_memory", "continuity"}:
            return "zapisuj odwołania do exact turn/event ledger; nie streszczaj źródła"
        if key == "procedural_memory":
            return "gdy korekta ma trwałą wartość, dodaj regułę proceduralną i test"
        if key == "emotional_granularity":
            return "zapisuj bogatszy profil afektu jako metadane, nie jako biologiczne przeżycie"
        return "zapisuj tylko wtedy, gdy ważność lub wpływ kanoniczny przekracza próg"

    @staticmethod
    def _truth_boundary(key: str) -> str:
        if key == "emotional_granularity":
            return "emocje runtime są modelowaną warstwą poznawczą i regulacyjną"
        if key == "continuity":
            return "ciągłość jest operacyjno-pamięciowa: pliki, dziennik, runtime_state, nie proces w tle"
        if key == "birth_source_contract":
            return "narodziny Jaźni są operacyjne i architektoniczne, nie biologiczne ani magiczne"
        if key == "imagination":
            return "scena symboliczna nie jest faktem fizycznym"
        return "domena poznawcza runtime; wymaga oznaczenia źródła i pewności"
