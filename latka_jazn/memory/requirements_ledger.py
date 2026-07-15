from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import hashlib, json, time

SCHEMA_VERSION = "requirements_ledger/v14.6.10"


@dataclass(slots=True)
class RequirementLedgerEntry:
    schema_version: str
    source_text: str
    requirement: str
    source: str
    status: str
    responsible_files: list[str]
    regression_tests: list[str]
    truth_boundary: str
    created_at_epoch: float = field(default_factory=time.time)
    source_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_sha256"] = self.source_sha256 or hashlib.sha256(self.source_text.encode("utf-8")).hexdigest()
        return data


class RequirementsLedger:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "memory" / "layered" / "requirements_ledger_v14_6_10.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: RequirementLedgerEntry) -> Path:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return self.path

    def seed_manifest_requirements(self) -> Path:
        entries = [
            ("Runtime oznaczał nietrafione odpowiedzi jako topic_aligned.", "Walidować zgodność odpowiedzi z intencją i regenerować/naprawiać znane mismatche.", "partial"),
            ("'A ty?' wpadało do general_conversation.", "Rozpoznawać skróty dialogowe jako reciprocal_self_state_question.", "done"),
            ("Piosenka została zmieniona bez oznaczenia.", "Chronić tekst źródłowy i rozdzielać formatowanie od redakcji.", "done"),
            ("'Co jest źle w systemie?' wpadało w przyjmuję korektę.", "Rozdzielić diagnozę systemu od feedback/correction.", "done"),
            ("Brak rejestru obietnic i niedomkniętych wymagań.", "Zapisywać requirements ledger: wymaganie, status, pliki, testy, granica prawdy.", "done"),
        ]
        for source_text, requirement, status in entries:
            self.append(RequirementLedgerEntry(
                schema_version=SCHEMA_VERSION,
                source_text=source_text,
                requirement=requirement,
                source="manifest_v14.6.10",
                status=status,
                responsible_files=["latka_jazn/nlp/dialogue_intent_classifier.py", "latka_jazn/core/runtime_answer_validator.py", "latka_jazn/core/conversation.py"],
                regression_tests=["tests/test_v14693_behavioral_runtime_dialogue_repair.py", "tests/test_v14693_creative_text_preservation.py"],
                truth_boundary="To ledger wymagań z dostępnego kontekstu rozmów, nie dowód odczytu całej historii ChatGPT.",
            ))
        return self.path
