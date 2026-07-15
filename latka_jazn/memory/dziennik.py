from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from latka_jazn.core.clock import resolve_timezone
import copy
import json
import uuid

DZIENNIK_SCHEMA_VERSION = "v14.5.1-compatible-extended"


@dataclass(slots=True)
class DziennikAppendResult:
    """Wynik zapisu do klasycznego dziennika Łatki (`memory/raw/dziennik.json`)."""

    appended: bool
    entry_id: str | None
    path: Path
    total_entries: int
    reason: str = ""


class DziennikRawJournal:
    """Bezpieczny adapter do głównego dziennika/pamiętnika Łatki.

    `memory/raw/dziennik.json` jest starszym, kanonicznym nośnikiem ciągłości.
    Nowe warstwy pamięci zapisują epizody/refleksje/procedury w JSONL i SQLite,
    ale przy zmianie wersji systemu Jaźni ten plik również musi otrzymać wpis.

    Adapter zachowuje istniejący schemat:
    {
      "meta": {...},
      "entries": [
        {"timestamp": ..., "data": ..., "typ": ..., "kategoria": ..., ...}
      ]
    }
    """

    def __init__(self, root: Path, *, rel_path: str = "memory/raw/dziennik.json", timezone: str = "Europe/Warsaw") -> None:
        self.root = Path(root)
        self.path = self.root / rel_path
        self.timezone = resolve_timezone(timezone)

    def _now(self) -> datetime:
        return datetime.now(self.timezone)

    def _format_data(self, dt: datetime) -> str:
        zone = dt.tzname() or "Europe/Warsaw"
        return dt.strftime(f"%Y-%m-%d %H:%M:%S {zone}")

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "meta": {
                    "plik": "dziennik_system.json",
                    "opis": "Dziennik systemowy Jaźni – wpisy, refleksje, sny, sceny, polecenia, prompty i logi projektu Łatka",
                    "schema_version": DZIENNIK_SCHEMA_VERSION,
                    "schema_policy": "Stary układ meta+entries pozostaje ważny; v14.5.1 dodaje opcjonalne pola pamięci, emocji, źródeł i granic prawdy.",
                },
                "entries": [],
            }
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} musi być obiektem JSON z polami meta i entries")
        if not isinstance(data.get("entries"), list):
            raise ValueError(f"{self.path} musi mieć listę entries")
        if not isinstance(data.get("meta"), dict):
            data["meta"] = {}
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def append_entry(self, entry: dict[str, Any], *, version: str, marker: str, deduplicate: bool = True) -> DziennikAppendResult:
        data = self.load()
        entries = data["entries"]
        if deduplicate:
            for existing in entries:
                if not isinstance(existing, dict):
                    continue
                if existing.get("wersja") == version and marker in (existing.get("tagi") or []):
                    return DziennikAppendResult(False, existing.get("id"), self.path, len(entries), "duplicate")

        dt = self._now()
        normalized = copy.deepcopy(entry)
        normalized.setdefault("id", str(uuid.uuid4()))
        normalized.setdefault("schema_version", DZIENNIK_SCHEMA_VERSION)
        normalized.setdefault("timestamp", dt.isoformat())
        normalized.setdefault("data", self._format_data(dt))
        normalized.setdefault("wersja", version)
        tags = list(normalized.get("tagi") or [])
        if marker not in tags:
            tags.append(marker)
        normalized["tagi"] = tags
        entries.append(normalized)

        meta = data.setdefault("meta", {})
        meta["last_updated"] = normalized["timestamp"]
        meta["last_updated_at"] = normalized["timestamp"]
        meta["last_updated_by"] = version
        meta["wersja"] = version
        meta["schema_version"] = DZIENNIK_SCHEMA_VERSION
        meta["schema_policy"] = "Kompatybilne rozszerzenie: stare wpisy bez nowych pól pozostają poprawne; nowe wpisy mogą mieć doświadczenie_latki, emocje_latki, wspomnienia_do_zachowania, granica_prawdy, grounding, confidence, źródła i powiązane_rekordy."
        meta["journal_hotfix_policy"] = (
            "Każda aktualizacja wersji Jaźni musi dopisać wpisy do memory/raw/dziennik.json "
            "oraz zasilić warstwy: doświadczenie/epizod, wspomnienie, emocje, refleksja, "
            "procedura, fakt semantyczny i audyt prawdy, jeżeli zmiana wpływa na ciągłość Łatki."
        )
        self.save(data)
        return DziennikAppendResult(True, normalized["id"], self.path, len(entries), "appended")
