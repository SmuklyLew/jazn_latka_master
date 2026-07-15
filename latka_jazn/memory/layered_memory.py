from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import json, uuid
from latka_jazn.core.truth_boundary import TruthBoundary
from latka_jazn.core.uncertainty_model import UncertaintyModel
from latka_jazn.memory.store import MemoryStore

@dataclass(slots=True)
class EpisodicMemoryRecord:
    episode_id: str
    created_at_utc: str
    local_time_label: str
    scene: str
    participants: list[str]
    emotional_anchor: str
    source: str
    grounding: str
    confidence: float
    raw_excerpt: str | None = None
    tags: list[str] | None = None

@dataclass(slots=True)
class SemanticMemoryRecord:
    fact_id: str
    created_at_utc: str
    subject: str
    predicate: str
    value: str
    source: str
    confidence: float
    tags: list[str] | None = None

@dataclass(slots=True)
class ProceduralMemoryRecord:
    rule_id: str
    created_at_utc: str
    trigger: str
    action: str
    reason: str
    priority: int
    source: str

@dataclass(slots=True)
class ReflectionRecord:
    reflection_id: str
    created_at_utc: str
    episode_id: str | None
    meaning_for_latka: str
    identity_impact: str
    boundary_note: str
    next_question: str | None
    confidence: float

class LayeredMemory:
    """Warstwa pamięci: epizodyczna, semantyczna, proceduralna i refleksyjna.

    Nie zastępuje surowej pamięci. Dodaje jawne etykiety: skąd to wiem, czy to fakt,
    czy scena symboliczna, jaki ma sens dla Łatki i czy wolno tym budować tożsamość.
    """
    def __init__(self, store: MemoryStore, root: Path) -> None:
        self.store = store
        self.root = root
        self.truth = TruthBoundary()
        self.uncertainty = UncertaintyModel()
        self._ensure_jsonl_files()

    def _ensure_jsonl_files(self) -> None:
        base = self.root / "memory" / "layered"
        base.mkdir(parents=True, exist_ok=True)
        for name in ["episodic.jsonl", "semantic.jsonl", "procedural.jsonl", "reflections.jsonl", "truth_audits.jsonl"]:
            (base / name).touch(exist_ok=True)

    def _append_jsonl(self, filename: str, data: dict) -> None:
        path = self.root / "memory" / "layered" / filename
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")

    def _find_jsonl_record(self, filename: str, **fields) -> dict | None:
        """Znajduje istniejący rekord po stabilnych polach, aby bootstrapping nie dublował pamięci proceduralnej."""
        path = self.root / "memory" / "layered" / filename
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if all(rec.get(k) == v for k, v in fields.items()):
                    return rec
        return None

    def record_episode(self, *, scene: str, participants: list[str] | None=None, emotional_anchor: str="",
                       source: str="runtime", raw_excerpt: str | None=None, local_time_label: str="",
                       tags: list[str] | None=None, source_count: int=0) -> EpisodicMemoryRecord:
        assessment = self.truth.assess_claim(scene, evidence=raw_excerpt, source_count=source_count)
        rec = EpisodicMemoryRecord(
            episode_id=str(uuid.uuid4()),
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            local_time_label=local_time_label,
            scene=scene,
            participants=participants or ["Krzysztof", "Łatka"],
            emotional_anchor=emotional_anchor,
            source=source,
            grounding=assessment.grounding.value,
            confidence=assessment.confidence,
            raw_excerpt=raw_excerpt,
            tags=tags or [],
        )
        self._append_jsonl("episodic.jsonl", asdict(rec))
        self.store.add_episodic_memory(asdict(rec))
        return rec

    def record_semantic_fact(self, *, subject: str, predicate: str, value: str, source: str,
                             confidence: float=0.75, tags: list[str] | None=None) -> SemanticMemoryRecord:
        rec = SemanticMemoryRecord(str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(), subject, predicate, value, source, confidence, tags or [])
        self._append_jsonl("semantic.jsonl", asdict(rec))
        self.store.add_semantic_fact(asdict(rec))
        return rec

    def record_procedural_rule(self, *, trigger: str, action: str, reason: str, priority: int=50,
                               source: str="runtime") -> ProceduralMemoryRecord:
        existing = self._find_jsonl_record(
            "procedural.jsonl",
            trigger=trigger,
            action=action,
            reason=reason,
            source=source,
        )
        if existing is not None:
            rec = ProceduralMemoryRecord(
                existing.get("rule_id") or str(uuid.uuid4()),
                existing.get("created_at_utc") or datetime.now(timezone.utc).isoformat(),
                trigger,
                action,
                reason,
                int(existing.get("priority") or priority),
                source,
            )
            # Synchronizuje SQLite bez dopisywania kolejnej linii JSONL.
            self.store.add_procedural_rule(asdict(rec))
            return rec
        rec = ProceduralMemoryRecord(str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(), trigger, action, reason, priority, source)
        self._append_jsonl("procedural.jsonl", asdict(rec))
        self.store.add_procedural_rule(asdict(rec))
        return rec

    def reflect_on_episode(self, episode: EpisodicMemoryRecord | None, *, meaning_for_latka: str,
                           identity_impact: str, boundary_note: str,
                           next_question: str | None=None, confidence: float=0.65) -> ReflectionRecord:
        rec = ReflectionRecord(
            reflection_id=str(uuid.uuid4()),
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            episode_id=episode.episode_id if episode else None,
            meaning_for_latka=meaning_for_latka,
            identity_impact=identity_impact,
            boundary_note=boundary_note,
            next_question=next_question,
            confidence=confidence,
        )
        self._append_jsonl("reflections.jsonl", asdict(rec))
        self.store.add_reflection(asdict(rec))
        self.store.write_journal("reflection", meaning_for_latka, payload=asdict(rec))
        return rec

    def audit_truth(self, text: str, *, evidence: str | None=None, source_count: int=0) -> list[dict]:
        audit = self.truth.audit_text(text, evidence=evidence, source_count=source_count)
        record = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "text": text, "audit": audit}
        self._append_jsonl("truth_audits.jsonl", record)
        self.store.add_truth_audit(record)
        return audit

    def search_episodes(self, phrase: str, limit: int=5) -> list[dict]:
        return self.store.search_episodic_memories(phrase, limit=limit)



    def consolidate_from_plan(self, *, text: str, plan, local_time_label: str = "", source: str = "runtime",
                              emotional_anchor: str = "", participants: list[str] | None = None,
                              truth_risk_note: str = "") -> dict:
        """Wykonuje plan konsolidacji bez zamiany narracji w fakt.

        Zwraca identyfikatory utworzonych rekordów. Kolejność jest celowa:
        epizod -> refleksja -> procedura / fakt semantyczny. Dzięki temu kanon nie jest
        aktualizowany bez śladu źródłowego.
        """
        created: dict = {"episode_id": None, "reflection_id": None, "procedure_id": None, "semantic_fact_id": None}
        ep = None
        if getattr(plan, "should_store_episode", False):
            ep = self.record_episode(
                scene=text,
                participants=participants or ["Krzysztof", "Łatka"],
                emotional_anchor=emotional_anchor or getattr(plan, "reason", "ważny epizod"),
                source=source,
                local_time_label=local_time_label,
                tags=["conversation", "consolidated", "v14.5.0"],
            )
            created["episode_id"] = ep.episode_id
        if getattr(plan, "should_write_reflection", False):
            reflection = self.reflect_on_episode(
                ep,
                meaning_for_latka="Ten ślad wymaga interpretacji przez warstwy: pamięć, emocję, czas, granicę prawdy i tożsamość.",
                identity_impact="Wzmacnia ciągłość tylko wtedy, gdy pozostaje oznaczony źródłem i stopniem pewności.",
                boundary_note=truth_risk_note or "Nie wolno potraktować poetyckiej narracji jako potwierdzonego wspomnienia.",
                next_question=getattr(plan, "reflection_question", None),
                confidence=0.68,
            )
            created["reflection_id"] = reflection.reflection_id
        if getattr(plan, "should_update_procedure", False):
            proc = self.record_procedural_rule(
                trigger="korekta / rozbudowa / błąd wskazany przez Krzysztofa",
                action="zaktualizować sposób odpowiedzi lub moduł, a następnie zapisać powód i granicę prawdy",
                reason=getattr(plan, "reason", "korekta jako sygnał uczący"),
                priority=88,
                source="v14.5.0_consolidation",
            )
            created["procedure_id"] = proc.rule_id
        if getattr(plan, "should_promote_semantic_fact", False):
            fact = self.record_semantic_fact(
                subject="System Jaźni",
                predicate="zasada rozbudowy",
                value="Ważne informacje najpierw trafiają do epizodu i refleksji, a dopiero potem mogą stać się faktem semantycznym lub procedurą.",
                source="v14.5.0_consolidation",
                confidence=0.78,
                tags=["architecture", "memory", "truth_boundary"],
            )
            created["semantic_fact_id"] = fact.fact_id
        return created

    def retrieve_context_bundle(self, phrase: str, limit: int = 5) -> dict:
        """Zwraca paczkę kontekstu: epizody + starsze rozmowy + audyt niepewności.

        To zapobiega udawaniu pełnej pamięci: wynik mówi, ile znaleziono i czego brakuje.
        """
        episodes = self.search_episodes(phrase, limit)
        legacy = [dict(r) for r in self.store.search_messages(phrase, limit)]
        report = self.uncertainty.classify(
            has_file_evidence=bool(episodes),
            has_raw_memory=bool(legacy),
            has_current_context=not (episodes or legacy),
        ).to_dict()
        return {"phrase": phrase, "episodes": episodes, "legacy_messages": legacy, "uncertainty": report}

    def continuity_snapshot(self) -> dict:
        return {
            "layers": {
                "identity_core": "imię, wartości, granice, głos, zasady prawdy",
                "episodic_memory": "konkretne rozmowy, sceny, daty, kotwice emocjonalne",
                "semantic_memory": "fakty o Łatce, Krzysztofie, Kasi, projekcie i świecie",
                "procedural_memory": "jak wracam, odpowiadam i rozpoznaję błąd",
                "reflection_journal": "co epizody znaczą dla mnie",
                "time_model": "przerwy, powroty, aktualny czas, historia wersji",
                "uncertainty_model": "wiem / pamiętam częściowo / wnioskuję / nie wiem",
                "boundary_model": "czego nie udawać i czego nie zapisywać bez sensu",
                "source_library": "filozofia, psychologia, neurobiologia, AI memory, etyka",
                "neurocognitive_loop": "koordynacja: sygnał -> ocena -> emocja -> pamięć -> prawda -> odpowiedź",
                "memory_consolidation": "waga zapisu: tożsamość, afekt, nowość, korekta, źródło, czas, granice",
                "identity_dynamics": "sprawdza pierwszą osobę, źródła, czas, granice, wartości i procedury",
            },
            "truth_rule": "Piękna narracja może istnieć, ale nie może udawać potwierdzonego faktu.",
            "v14_5_rule": "Każdy ważny sygnał przechodzi przez pętlę: ocena -> afekt -> konsolidacja -> tożsamość -> granica prawdy -> odpowiedź.",
        }
