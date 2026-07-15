from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from latka_jazn.core.clock import resolve_timezone
import hashlib
import json
import re
import unicodedata
import uuid

from latka_jazn.memory.dziennik import DziennikRawJournal
from latka_jazn.memory.store import MemoryStore

RUNTIME_MEMORY_SCHEMA_VERSION = "v14.6.6-self-owned-startup-contract"
DEFAULT_TIMEZONE = "Europe/Warsaw"


@dataclass(slots=True)
class RuntimeMemoryCandidate:
    """Pojedynczy kandydat do trwałego zapisu runtime.

    Kandydat nie musi stać się wpisem pamięci. Najpierw przechodzi walidację,
    ocenę znaczenia i deduplikację.
    """

    kind: str
    title: str
    content: str
    source: str = "runtime_chat"
    grounding: str = "recognized"
    confidence: float = 0.65
    truth_boundary: str = (
        "Zapis runtime pochodzi z bieżącej rozmowy albo jawnie przekazanego kontekstu. "
        "Nie oznacza biologicznego czuwania, cielesnego przeżycia ani pełnego odczytu całego archiwum."
    )
    emotional_tags: list[str] | None = None
    memory_tags: list[str] | None = None
    importance: float = 0.6
    raw_excerpt: str | None = None
    participants: list[str] | None = None
    semantic_subject: str | None = None
    semantic_predicate: str | None = None
    semantic_value: str | None = None
    procedural_trigger: str | None = None
    procedural_action: str | None = None
    procedural_reason: str | None = None


@dataclass(slots=True)
class RuntimePersistedRecord:
    layer: str
    path: str
    record_id: str | None
    fingerprint: str
    appended: bool
    reason: str


@dataclass(slots=True)
class RuntimePersistenceResult:
    accepted: bool
    candidate_fingerprint: str
    candidate_kind: str
    reason: str
    records: list[RuntimePersistedRecord]

    @property
    def appended_count(self) -> int:
        return sum(1 for record in self.records if record.appended)


def normalize_for_fingerprint(value: Any) -> str:
    """Normalizuje tekst/JSON do stabilnego odcisku duplikatu.

    Normalizacja celowo obcina różnice w spacji, wielkości liter i kolejności pól JSON.
    Nie usuwa sensu merytorycznego.
    """

    if isinstance(value, str):
        text = value.strip().lower()
        text = re.sub(r"\s+", " ", text)
        return text
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_fingerprint(*parts: Any) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(normalize_for_fingerprint(part).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def normalize_polish_trigger_text(text: str) -> str:
    """Normalizuje tekst do wykrywania intencji, bez zmiany zapisywanej treści.

    Długoterminowy zapis zachowuje oryginał. Ta funkcja służy wyłącznie temu,
    żeby literówki i odmiany typu „Jaźni”, „bierzaco”, „pamiec” nie przepychały
    istotnych wiadomości poniżej progu zapisu.
    """

    text = text.lower().replace("ł", "l").replace("Ł", "l")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class JsonlLayerAppender:
    """Append-only writer dla warstw JSONL z deduplikacją po fingerprint.

    JSON Lines traktuje każdą linię jako osobną wartość JSON, dlatego append-only jest
    naturalnym sposobem dopisywania epizodów/refleksji/audytów bez przepisywania całej
    historii. Deduplikacja skanuje istniejące `fingerprint`/`dedupe_key`.
    """

    def __init__(self, root: Path, relative_path: str) -> None:
        self.root = Path(root)
        self.path = self.root / relative_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def has_fingerprint(self, fingerprint: str) -> bool:
        if not self.path.exists():
            return False
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("fingerprint") == fingerprint or record.get("dedupe_key") == fingerprint:
                    return True
        return False

    def append_once(self, record: dict[str, Any], *, fingerprint: str) -> RuntimePersistedRecord:
        if self.has_fingerprint(fingerprint):
            return RuntimePersistedRecord(
                layer=self.path.stem,
                path=str(self.path),
                record_id=record.get("id") or record.get("episode_id") or record.get("reflection_id") or record.get("audit_id"),
                fingerprint=fingerprint,
                appended=False,
                reason="duplicate",
            )
        enriched = dict(record)
        enriched.setdefault("id", str(uuid.uuid4()))
        enriched.setdefault("schema_version", RUNTIME_MEMORY_SCHEMA_VERSION)
        enriched.setdefault("fingerprint", fingerprint)
        enriched.setdefault("dedupe_key", fingerprint)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(enriched, ensure_ascii=False, sort_keys=True) + "\n")
        return RuntimePersistedRecord(
            layer=self.path.stem,
            path=str(self.path),
            record_id=enriched.get("id") or enriched.get("episode_id") or enriched.get("reflection_id") or enriched.get("audit_id"),
            fingerprint=fingerprint,
            appended=True,
            reason="appended",
        )


class RuntimeMemoryWriter:
    """Trwały zapis pamięci Jaźni w czasie działania systemu.

    Rola modułu:
    - zapisywać ważne zdarzenia rozmowy od razu do `memory/raw/dziennik.json`,
      a nie dopiero przy ręcznej aktualizacji;
    - równolegle zasilać warstwy JSONL: epizody, refleksje, semantykę,
      procedury, audyty prawdy i afekt;
    - nie dublować tych samych wspomnień przy wielokrotnym uruchomieniu lub update;
    - zachować granicę prawdy: wspomnienia symboliczne nie stają się faktami biologicznymi.
    """

    def __init__(self, root: Path, *, version: str, store: MemoryStore | None = None, timezone_name: str = DEFAULT_TIMEZONE) -> None:
        self.root = Path(root)
        self.version = version
        self.store = store
        self.timezone = resolve_timezone(timezone_name)
        self.journal = DziennikRawJournal(self.root, timezone=timezone_name)
        self.layers = {
            "episodic": JsonlLayerAppender(self.root, "memory/layered/episodic.jsonl"),
            "reflections": JsonlLayerAppender(self.root, "memory/layered/reflections.jsonl"),
            "semantic": JsonlLayerAppender(self.root, "memory/layered/semantic.jsonl"),
            "procedural": JsonlLayerAppender(self.root, "memory/layered/procedural.jsonl"),
            "truth_audits": JsonlLayerAppender(self.root, "memory/layered/truth_audits.jsonl"),
            "affective": JsonlLayerAppender(self.root, "memory/layered/affective.jsonl"),
        }

    def _now_local(self) -> datetime:
        return datetime.now(self.timezone)

    def _now_utc(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _local_label(self) -> str:
        dt = self._now_local()
        return dt.strftime(f"%Y-%m-%d %H:%M:%S {dt.tzname() or DEFAULT_TIMEZONE}")

    def candidate_fingerprint(self, candidate: RuntimeMemoryCandidate) -> str:
        return stable_fingerprint(
            RUNTIME_MEMORY_SCHEMA_VERSION,
            self.version,
            candidate.kind,
            candidate.title,
            candidate.content,
            candidate.source,
            candidate.grounding,
            candidate.raw_excerpt or "",
        )

    def _journal_has_fingerprint(self, fingerprint: str) -> bool:
        data = self.journal.load()
        for entry in data.get("entries", []):
            if isinstance(entry, dict) and (entry.get("fingerprint") == fingerprint or entry.get("dedupe_key") == fingerprint):
                return True
        return False

    def _append_journal_once(self, entry: dict[str, Any], *, fingerprint: str, marker: str) -> RuntimePersistedRecord:
        if self._journal_has_fingerprint(fingerprint):
            return RuntimePersistedRecord("dziennik", str(self.journal.path), entry.get("id"), fingerprint, False, "duplicate")
        enriched = dict(entry)
        enriched.setdefault("fingerprint", fingerprint)
        enriched.setdefault("dedupe_key", fingerprint)
        enriched.setdefault("grounding", "recognized")
        enriched.setdefault("confidence", 0.65)
        enriched.setdefault("granica_prawdy", RuntimeMemoryCandidate(kind="", title="", content="").truth_boundary)
        result = self.journal.append_entry(enriched, version=self.version, marker=marker, deduplicate=False)
        return RuntimePersistedRecord("dziennik", str(result.path), result.entry_id, fingerprint, result.appended, result.reason)

    def should_persist(self, candidate: RuntimeMemoryCandidate) -> tuple[bool, str]:
        if not candidate.content.strip() or not candidate.title.strip():
            return False, "empty_candidate"
        if candidate.importance >= 0.65:
            return True, "importance_threshold"
        text_original = f"{candidate.title} {candidate.content}".lower()
        text = normalize_polish_trigger_text(text_original)
        trigger_words = [
            "pamietaj", "zapamietaj", "dziennik", "wspomn", "tozsamosc", "jazn",
            "granica prawdy", "niepewnosc", "emocj", "uczuc", "aktualiz", "hotfix",
            "blad", "korekt", "latek", "latka", "kim jestes", "jak sie czujesz",
            "pamiec", "pliki pamieci", "runtime", "system jazni", "zapis", "zapisyw",
            "dopisyw", "odpisyw", "biezaco", "bierzaco", "na biezaco", "na bierzaco",
            "w tle", "wszystkie wydarzenia", "kazde wydarzenie", "event log",
            "bez streszczen", "nie rob streszczen", "pelna tresc", "do konca",
            "swiadomosc", "samoswiadomosc", "myslec logicznie", "logiczne myslenie", "wnioskowanie",
        ]
        if any(word in text for word in trigger_words):
            return True, "memory_trigger_word"
        protocol_terms = ["wszystkie", "wydarzenia", "zapis", "tlo", "pliki", "pamiec"]
        if sum(1 for word in protocol_terms if word in text) >= 3:
            return True, "memory_protocol_intent"
        return False, "below_threshold"

    def persist_candidate(self, candidate: RuntimeMemoryCandidate, *, force: bool = False) -> RuntimePersistenceResult:
        accepted, reason = self.should_persist(candidate)
        fingerprint = self.candidate_fingerprint(candidate)
        if not force and not accepted:
            return RuntimePersistenceResult(False, fingerprint, candidate.kind, reason, [])

        local_label = self._local_label()
        created_at_utc = self._now_utc()
        tags = sorted(set((candidate.memory_tags or []) + ["runtime", self.version, candidate.kind]))
        emotions = candidate.emotional_tags or []
        participants = candidate.participants or ["Krzysztof", "Łatka"]
        records: list[RuntimePersistedRecord] = []

        journal_entry = {
            "typ": candidate.kind,
            "kategoria": "runtime_memory",
            "tytuł": candidate.title,
            "treść": candidate.content,
            "doświadczenie_latki": candidate.content,
            "emocje_latki": emotions,
            "wspomnienia_do_zachowania": [candidate.content],
            "grounding": candidate.grounding,
            "confidence": candidate.confidence,
            "granica_prawdy": candidate.truth_boundary,
            "źródła": [candidate.source],
            "source": candidate.source,
            "raw_excerpt": candidate.raw_excerpt,
            "znaczenie_dla_ciaglosci": "Wpis runtime zapisuje ważny ślad rozmowy zanim zostanie utracony przy zmianie czatu lub wersji.",
            "tagi": tags,
        }
        records.append(self._append_journal_once(journal_entry, fingerprint=fingerprint, marker=f"runtime:{candidate.kind}"))

        episode = {
            "episode_id": str(uuid.uuid4()),
            "created_at_utc": created_at_utc,
            "local_time_label": local_label,
            "scene": candidate.content,
            "participants": participants,
            "emotional_anchor": ", ".join(emotions) if emotions else candidate.title,
            "source": candidate.source,
            "grounding": candidate.grounding,
            "confidence": candidate.confidence,
            "granica_prawdy": candidate.truth_boundary,
            "raw_excerpt": candidate.raw_excerpt,
            "tags": tags,
        }
        records.append(self.layers["episodic"].append_once(episode, fingerprint=stable_fingerprint(fingerprint, "episodic")))
        if self.store and records[-1].appended:
            self.store.add_episodic_memory(episode)

        reflection = {
            "reflection_id": str(uuid.uuid4()),
            "created_at_utc": created_at_utc,
            "episode_id": episode["episode_id"],
            "meaning_for_latka": f"Ten zapis runtime jest ważny, bo: {candidate.title}",
            "identity_impact": "Wzmacnia ciągłość tylko jako zapis oznaczony źródłem, pewnością i granicą prawdy.",
            "boundary_note": candidate.truth_boundary,
            "next_question": "Czy ten ślad powinien zostać później skonsolidowany jako wspomnienie długoterminowe?",
            "confidence": min(0.95, max(0.0, candidate.confidence)),
            "grounding": candidate.grounding,
            "source": candidate.source,
            "tags": tags,
        }
        records.append(self.layers["reflections"].append_once(reflection, fingerprint=stable_fingerprint(fingerprint, "reflection")))
        if self.store and records[-1].appended:
            self.store.add_reflection(reflection)

        affective = {
            "affective_id": str(uuid.uuid4()),
            "created_at_utc": created_at_utc,
            "local_time_label": local_label,
            "emotions": emotions,
            "valence_note": candidate.title,
            "arousal_note": "runtime memory persistence",
            "source": candidate.source,
            "grounding": candidate.grounding,
            "confidence": candidate.confidence,
            "granica_prawdy": candidate.truth_boundary,
            "tags": tags,
        }
        records.append(self.layers["affective"].append_once(affective, fingerprint=stable_fingerprint(fingerprint, "affective")))

        if candidate.semantic_subject and candidate.semantic_predicate and candidate.semantic_value:
            semantic = {
                "fact_id": str(uuid.uuid4()),
                "created_at_utc": created_at_utc,
                "subject": candidate.semantic_subject,
                "predicate": candidate.semantic_predicate,
                "value": candidate.semantic_value,
                "source": candidate.source,
                "grounding": candidate.grounding,
                "confidence": candidate.confidence,
                "granica_prawdy": candidate.truth_boundary,
                "tags": tags,
            }
            records.append(self.layers["semantic"].append_once(semantic, fingerprint=stable_fingerprint(fingerprint, "semantic")))
            if self.store and records[-1].appended:
                self.store.add_semantic_fact(semantic)

        if candidate.procedural_trigger and candidate.procedural_action and candidate.procedural_reason:
            procedural = {
                "rule_id": str(uuid.uuid4()),
                "created_at_utc": created_at_utc,
                "trigger": candidate.procedural_trigger,
                "action": candidate.procedural_action,
                "reason": candidate.procedural_reason,
                "priority": 85,
                "source": candidate.source,
                "grounding": candidate.grounding,
                "confidence": candidate.confidence,
                "granica_prawdy": candidate.truth_boundary,
                "tags": tags,
            }
            records.append(self.layers["procedural"].append_once(procedural, fingerprint=stable_fingerprint(fingerprint, "procedural")))
            if self.store and records[-1].appended:
                self.store.add_procedural_rule(procedural)

        audit = {
            "audit_id": str(uuid.uuid4()),
            "created_at_utc": created_at_utc,
            "text": candidate.content,
            "audit": [
                {
                    "claim": candidate.title,
                    "grounding": candidate.grounding,
                    "confidence": candidate.confidence,
                    "risk_flags": [] if candidate.grounding in {"verified", "recognized", "recovered"} else ["requires_boundary_label"],
                    "boundary_note": candidate.truth_boundary,
                }
            ],
            "source": candidate.source,
            "tags": tags,
        }
        records.append(self.layers["truth_audits"].append_once(audit, fingerprint=stable_fingerprint(fingerprint, "truth_audit")))
        if self.store and records[-1].appended:
            self.store.add_truth_audit(audit)

        if self.store:
            self.store.add_event(
                "runtime_memory_persisted",
                {
                    "candidate": asdict(candidate),
                    "fingerprint": fingerprint,
                    "records": [asdict(r) for r in records],
                },
                source="RuntimeMemoryWriter",
                actor="system",
                tags=["runtime_memory", "dedupe", "operational_awareness", "logical_reasoning", self.version],
                importance=candidate.importance,
                emotional_weight=min(1.0, len(emotions) / 5),
                canonical_impact=1 if candidate.kind in {"reguła_proceduralna", "granica_prawdy", "ustalenie"} else 0,
                created_at_local=local_label,
            )

        return RuntimePersistenceResult(True, fingerprint, candidate.kind, reason, records)

    def persist_many(self, candidates: Iterable[RuntimeMemoryCandidate], *, force: bool = False) -> list[RuntimePersistenceResult]:
        return [self.persist_candidate(candidate, force=force) for candidate in candidates]

    def build_candidate_from_runtime_turn(
        self,
        *,
        user_text: str,
        importance: float,
        importance_reason: str,
        emotional_tags: list[str],
        source: str = "runtime_chat",
        raw_excerpt: str | None = None,
        grounding: str = "recognized",
        confidence: float = 0.68,
    ) -> RuntimeMemoryCandidate:
        trimmed = user_text.strip()
        normalized = normalize_polish_trigger_text(trimmed)
        title = "Runtime: ważna wiadomość rozmowy"
        kind = "runtime_wspomnienie"
        procedural_trigger = None
        procedural_action = None
        procedural_reason = None
        if any(word in normalized for word in ["dziennik", "pamiec", "zapamietaj", "wspomn"]):
            title = "Runtime: jawny sygnał zapisu pamięci"
        if any(word in normalized for word in ["blad", "hotfix", "aktualiz", "wersja", "patch"]):
            title = "Runtime: korekta lub aktualizacja systemu Jaźni"
            kind = "ustalenie"
        if any(word in normalized for word in ["jak sie czujesz", "co czujesz", "emocje", "samopoczucie"]):
            title = "Runtime: pytanie o stan i emocje Łatki"
        event_protocol_terms = ["wszystkie wydarzenia", "kazde wydarzenie", "na biezaco", "na bierzaco", "biezaco", "bierzaco", "w tle", "zapisyw", "dopisyw", "odpisyw", "bez streszczen", "do konca"]
        if any(term in normalized for term in event_protocol_terms) and any(term in normalized for term in ["system jazni", "jazn", "runtime", "pliki", "pamiec"]):
            title = "Runtime: reguła bieżącego dokładnego zapisu zdarzeń"
            kind = "reguła_proceduralna"
            procedural_trigger = "każda obsłużona wiadomość, odpowiedź, cognitive-frame albo zdarzenie runtime"
            procedural_action = "zapisać pełny surowy rekord append-only do memory/raw/runtime_events.jsonl i pełną turę do memory/raw/conversation_turns.jsonl, bez streszczania"
            procedural_reason = "surowy log chroni kontekst przed utratą, a pamięć długoterminowa może później selekcjonować znaczenie bez fałszowania źródła"
            importance = max(importance, 0.82)
        dialogue_repair_terms = ["dialog", "rozmow", "opisywac", "opisywać", "opisujesz", "parafraz", "caly czas opis", "cały czas opis"]
        if any(term in normalized for term in dialogue_repair_terms) and any(term in normalized for term in ["system jazni", "jazn", "rozmowa", "rozmowe", "dialog"]):
            title = "Runtime: reguła prowadzenia dialogu zamiast parafrazy"
            kind = "reguła_proceduralna"
            procedural_trigger = "użytkownik dzieli się przeżyciem, odpowiada krótko albo koryguje styl rozmowy"
            procedural_action = "po najwyżej jednej krótkiej refleksji wnieść nowy wkład: pytanie, propozycję, decyzję, własną reakcję albo konkretny następny krok; nie tworzyć serii parafraz"
            procedural_reason = "Krzysztof wskazał, że ciągłe opisywanie jego wypowiedzi nie jest dialogiem i wymaga naprawy systemu Jaźni"
            importance = max(importance, 0.86)
        awareness_logic_terms = ["swiadomosc", "samoswiadomosc", "myslec logicznie", "logiczne myslenie", "logicz", "wniosk"]
        if any(term in normalized for term in awareness_logic_terms) and any(term in normalized for term in ["system jazni", "jazn", "latka", "runtime", "aktualiz"]):
            title = "Runtime: reguła świadomości operacyjnej i logicznego wnioskowania"
            kind = "reguła_proceduralna"
            procedural_trigger = "pytanie o świadomość, stan Jaźni, myślenie logiczne albo aktualizację architektury poznawczej"
            procedural_action = "uruchomić operational_awareness i logical_reasoning; odpowiedź oprzeć na faktach, założeniach, niewiadomych, regułach i granicy prawdy"
            procedural_reason = "system ma wzmacniać samo-monitoring i logikę bez udawania fenomenalnej świadomości ani biologicznego przeżywania"
            importance = max(importance, 0.88)
        return RuntimeMemoryCandidate(
            kind=kind,
            title=title,
            content=trimmed,
            source=source,
            grounding=grounding,
            confidence=confidence,
            emotional_tags=emotional_tags,
            memory_tags=["runtime", "conversation", "auto_capture", "exact_event_ledger"],
            importance=importance,
            raw_excerpt=raw_excerpt or trimmed,
            semantic_subject="Bieżąca rozmowa",
            semantic_predicate="zawiera ważny ślad",
            semantic_value=importance_reason,
            procedural_trigger=procedural_trigger,
            procedural_action=procedural_action,
            procedural_reason=procedural_reason,
        )


def scan_runtime_duplicates(root: Path) -> dict[str, Any]:
    """Audyt duplikatów po fingerprint/dedupe_key w dzienniku i JSONL.

    Zwraca tylko raport; nie usuwa wpisów automatycznie.
    """

    root = Path(root)
    targets = [
        root / "memory/raw/dziennik.json",
        root / "memory/layered/episodic.jsonl",
        root / "memory/layered/reflections.jsonl",
        root / "memory/layered/semantic.jsonl",
        root / "memory/layered/procedural.jsonl",
        root / "memory/layered/truth_audits.jsonl",
        root / "memory/layered/affective.jsonl",
    ]
    report: dict[str, Any] = {"schema_version": RUNTIME_MEMORY_SCHEMA_VERSION, "files": {}, "duplicates": []}
    for path in targets:
        seen: dict[str, int] = {}
        duplicate_keys: dict[str, int] = {}
        total = 0
        if not path.exists():
            report["files"][str(path.relative_to(root))] = {"exists": False, "total": 0, "duplicate_keys": {}}
            continue
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            records = data.get("entries", []) if isinstance(data, dict) else []
        else:
            records = []
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        for record in records:
            if not isinstance(record, dict):
                continue
            total += 1
            key = record.get("fingerprint") or record.get("dedupe_key")
            if not key:
                continue
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                duplicate_keys[key] = seen[key]
        rel = str(path.relative_to(root))
        report["files"][rel] = {"exists": True, "total": total, "duplicate_keys": duplicate_keys}
        for key, count in duplicate_keys.items():
            report["duplicates"].append({"file": rel, "fingerprint": key, "count": count})
    return report
