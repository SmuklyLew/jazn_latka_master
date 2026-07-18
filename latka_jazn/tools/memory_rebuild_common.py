from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re
import sqlite3
import uuid

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_rebuild")
TRUTH_BOUNDARY = (
    "Importer preserves sources and creates review candidates. It never promotes "
    "content automatically to L2/L3 or turns roleplay/book scenes into physical events."
)
DATABASE_FILENAMES = {
    "archive_chats": "archive_chats.sqlite3",
    "journal": "journal.sqlite3",
    "memory_jazn": "memory_jazn.sqlite3",
    "experience": "experience.sqlite3",
    "import_catalog": "import_catalog.sqlite3",
}
ACK_RE = re.compile(
    r"^(?:ok|okej|tak|nie|dobrze|w porządku|rozumiem|jasne|dzięki|dziękuję|hej|cześć|witaj)[.!?\s]*$",
    re.IGNORECASE,
)
NOISE_RE = re.compile(
    r"traceback \(most recent call last\)|error: patch failed|stack trace|"
    r"file \".*\", line \d+|^\s*ps [a-z]:\\|^\s*python -x utf8",
    re.IGNORECASE | re.MULTILINE,
)
FANOUT_FIELDS = {
    "doświadczenie_latki", "doswiadczenie_latki", "emocje_latki",
    "wspomnienia_do_zachowania", "refleksja", "procedura",
    "fakt_semantyczny", "grounding", "granica_prawdy",
}
CONTENT_FIELDS = (
    "tytuł", "tytul", "title", "wpis", "treść", "tresc", "content", "opis",
    "doświadczenie_latki", "doswiadczenie_latki", "wspomnienia_do_zachowania",
    "refleksja", "emocje_latki", "granica_prawdy",
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def uid(namespace: str, *parts: Any) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join([namespace, *(str(x) for x in parts)])))


def norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.strip().split())
    if isinstance(value, (int, float, bool)):
        return str(value)
    return canonical_json(value)


def bounded(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def fts_queries(query: str) -> tuple[str, ...]:
    tokens = re.findall(r"[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", query, flags=re.UNICODE)
    if not tokens:
        return (query.strip(),)
    exact = " ".join(tokens)
    prefix = " ".join(f"{token[:-1] if len(token) >= 5 else token}*" for token in tokens)
    return (exact, prefix) if exact != prefix else (exact,)


def sqlite_check(con: sqlite3.Connection, *, full: bool) -> dict[str, Any]:
    name = "integrity_check" if full else "quick_check"
    integrity = [str(row[0]) for row in con.execute(f"PRAGMA {name}")]
    foreign = [tuple(row) for row in con.execute("PRAGMA foreign_key_check")]
    return {
        "ok": integrity == ["ok"] and not foreign,
        "check": name,
        "integrity": integrity,
        "foreign_key_error_count": len(foreign),
        "foreign_key_errors": foreign[:100],
    }


@dataclass(slots=True, frozen=True)
class MemoryRebuildPaths:
    root: Path
    sqlite_dir: Path
    archive_chats: Path
    journal: Path
    memory_jazn: Path
    experience: Path
    import_catalog: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "MemoryRebuildPaths":
        root_path = Path(root).expanduser().resolve()
        base = root_path / "memory" / "sqlite"
        return cls(root_path, base, *(base / DATABASE_FILENAMES[key] for key in DATABASE_FILENAMES))

    def as_dict(self) -> dict[str, str]:
        return {key: str(getattr(self, key)) for key in DATABASE_FILENAMES}
