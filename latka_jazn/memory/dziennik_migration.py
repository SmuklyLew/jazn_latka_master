from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator
import hashlib
import json

from latka_jazn.memory.legacy_fanout_migration import LegacyMigrationCandidate
from latka_jazn.memory.memory_tiers import MemoryKind, MemoryTruthStatus
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("dziennik_migration")
DZIENNIK_TABLE = "dziennik_entries"

_CONTENT_FIELDS = (
    "tytuł",
    "tytul",
    "wpis",
    "treść",
    "tresc",
    "content",
    "opis",
    "doświadczenie_latki",
    "doswiadczenie_latki",
    "wspomnienia_do_zachowania",
    "refleksja",
    "emocje_latki",
    "granica_prawdy",
)
_FANOUT_FIELDS = {
    "doświadczenie_latki",
    "doswiadczenie_latki",
    "emocje_latki",
    "wspomnienia_do_zachowania",
    "refleksja",
    "procedura",
    "fakt_semantyczny",
    "grounding",
    "granica_prawdy",
    "powiązane_rekordy",
    "powiazane_rekordy",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return _canonical_json(value)


def _bounded_number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _entry_content(entry: dict[str, Any]) -> str:
    lines: list[str] = []
    timestamp = _value_text(entry.get("timestamp") or entry.get("data"))
    if timestamp:
        lines.append(f"Czas wpisu: {timestamp}")

    seen: set[str] = set()
    for key in _CONTENT_FIELDS:
        if key in seen or key not in entry:
            continue
        seen.add(key)
        text = _value_text(entry.get(key))
        if text:
            lines.append(f"{key}: {text}")

    if not lines:
        lines.append(_canonical_json(entry))
    return "\n".join(lines)


def _memory_kind(entry: dict[str, Any]) -> MemoryKind:
    label = " ".join(
        _value_text(entry.get(key)).lower()
        for key in ("typ", "type", "kategoria", "category", "tagi")
    )
    if any(token in label for token in ("procedur", "instruk", "zasad")):
        return MemoryKind.PROCEDURAL
    if any(token in label for token in ("semant", "fakt")):
        return MemoryKind.SEMANTIC
    if any(token in label for token in ("refleks", "analiz")):
        return MemoryKind.REFLECTION
    return MemoryKind.EPISODIC


def _truth_status(entry: dict[str, Any]) -> MemoryTruthStatus:
    label = " ".join(
        _value_text(entry.get(key)).lower()
        for key in ("truth_status", "grounding", "granica_prawdy", "source", "źródło", "zrodlo")
    )
    if any(token in label for token in ("user_confirmed", "użytkownik potwierdził", "uzytkownik potwierdzil", "verified")):
        return MemoryTruthStatus.USER_CONFIRMED
    if any(token in label for token in ("source_recorded", "recorded", "zapis źródłowy", "zapis zrodlowy", "runtime")):
        return MemoryTruthStatus.SOURCE_RECORDED
    return MemoryTruthStatus.INFERRED


def _suspected_fanout(entry: dict[str, Any]) -> bool:
    populated = sum(1 for key in _FANOUT_FIELDS if _value_text(entry.get(key)))
    return populated >= 2


class DziennikJsonScanner:
    """Read-only scanner for the legacy ``meta + entries`` journal format."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.source_sha256 = _sha256_file(self.path)
        data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("dziennik.json must be a JSON object with meta and entries")
        entries = data.get("entries")
        if not isinstance(entries, list):
            raise ValueError("dziennik.json must contain an entries list")
        meta = data.get("meta")
        self.meta = dict(meta) if isinstance(meta, dict) else {}
        self.entries = list(entries)

    def close(self) -> None:
        return None

    def __enter__(self) -> "DziennikJsonScanner":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def inventory(self) -> dict[str, int]:
        valid = sum(1 for item in self.entries if isinstance(item, dict))
        return {
            "dziennik_entries": valid,
            "invalid_entries": len(self.entries) - valid,
        }

    def candidates(self) -> Iterator[LegacyMigrationCandidate]:
        for entry in self.entries:
            if not isinstance(entry, dict):
                continue
            raw_entry = dict(entry)
            content = _entry_content(raw_entry)
            content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            explicit_id = _value_text(raw_entry.get("id") or raw_entry.get("entry_id"))
            record_id = explicit_id or hashlib.sha256(_canonical_json(raw_entry).encode("utf-8")).hexdigest()
            candidate_id = hashlib.sha256(
                f"legacy_dziennik_json|{record_id}|{content_sha}".encode("utf-8")
            ).hexdigest()
            related_group_id = _value_text(
                raw_entry.get("powiązane_rekordy")
                or raw_entry.get("powiazane_rekordy")
                or raw_entry.get("episode_id")
            ) or record_id
            yield LegacyMigrationCandidate(
                candidate_id=candidate_id,
                legacy_table=DZIENNIK_TABLE,
                legacy_record_id=record_id,
                related_group_id=related_group_id,
                memory_kind=_memory_kind(raw_entry),
                content=content,
                truth_status=_truth_status(raw_entry),
                confidence=_bounded_number(raw_entry.get("confidence"), 0.55),
                importance=_bounded_number(
                    raw_entry.get("importance", raw_entry.get("ważność", raw_entry.get("waznosc"))),
                    0.6,
                ),
                suspected_fanout=_suspected_fanout(raw_entry),
                source_path=str(self.path),
                source_sha256=self.source_sha256,
                raw_record={
                    "entry": raw_entry,
                    "journal_meta": self.meta,
                    "migration_schema_version": SCHEMA_VERSION,
                },
            )
