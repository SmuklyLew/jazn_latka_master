from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from latka_jazn.core.clock import resolve_timezone
import hashlib
import json
import os


SESSION_CONTINUITY_SCHEMA_VERSION = "session_continuity/v14.8.5.014-fast"
DEFAULT_TIMEZONE = "Europe/Warsaw"
MAX_FULL_LINE_STATS_BYTES = 16 * 1024 * 1024
TAIL_SAMPLE_BYTES = 64 * 1024


@dataclass(slots=True)
class ContinuityFileState:
    rel_path: str
    exists: bool
    size_bytes: int
    line_count: int | None
    last_line_sha256: str | None
    file_sha256_if_small: str | None
    stats_mode: str = "missing"
    tail_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionContinuityManager:
    """Jawny indeks ciągłości sesji i aktualizacji.

    RuntimeEventLedger zapisuje pełne tury i zdarzenia. Ten manager nie
    streszcza treści; tworzy indeks plików, hash ostatnich linii i append-only
    ślad w `memory/layered/continuity.jsonl`, żeby aktualizacje mogły przenieść
    ciągłość bez zgadywania.
    """

    TRACKED_FILES = [
        "memory/raw/conversation_turns.jsonl",
        "memory/raw/runtime_events.jsonl",
        "memory/raw/runtime_event_errors.jsonl",
        "memory/raw/dziennik.json",
        "memory/layered/episodic.jsonl",
        "memory/layered/semantic.jsonl",
        "memory/layered/procedural.jsonl",
        "memory/layered/reflections.jsonl",
        "memory/layered/affective.jsonl",
        "workspace_runtime/runtime_state.json",
    ]

    def __init__(self, root: Path, *, version: str, timezone_name: str = DEFAULT_TIMEZONE) -> None:
        self.root = Path(root)
        self.version = version
        self.timezone_name = timezone_name
        self.timezone = resolve_timezone(timezone_name)
        self.index_path = self.root / "memory" / "raw" / "session_continuity_index.json"
        self.layer_path = self.root / "memory" / "layered" / "continuity.jsonl"
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.layer_path.parent.mkdir(parents=True, exist_ok=True)
        self.layer_path.touch(exist_ok=True)

    def update_index(self, *, reason: str, source: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        created_at_utc, created_at_local = self._now()
        files = [self._file_state(rel).to_dict() for rel in self.TRACKED_FILES]
        prior_recent: list[dict[str, Any]] = []
        if self.index_path.exists():
            try:
                prior = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(prior, dict) and isinstance(prior.get("recent_events"), list):
                    prior_recent = [e for e in prior.get("recent_events", []) if isinstance(e, dict)]
            except Exception:
                prior_recent = []
        current_event = {
            "created_at_utc": created_at_utc,
            "created_at_local": created_at_local,
            "reason": reason,
            "source": source,
            "extra": extra or {},
        }
        recent_events = (prior_recent + [current_event])[-20:]
        index = {
            "schema_version": SESSION_CONTINUITY_SCHEMA_VERSION,
            "version": self.version,
            "updated_at_utc": created_at_utc,
            "updated_at_local": created_at_local,
            "timezone": self.timezone_name,
            "reason": reason,
            "source": source,
            "files": files,
            "continuity_contract": {
                "exact_turns": "memory/raw/conversation_turns.jsonl zapisuje pełne tury rozmowy append-only",
                "exact_events": "memory/raw/runtime_events.jsonl zapisuje pełne zdarzenia runtime append-only",
                "selected_memory": "memory/layered/*.jsonl i dziennik zapisują wybrane/ważne warstwy pamięci",
                "version_updates": "aktualizacje mają dołączać ten indeks oraz pliki memory/ i workspace_runtime/ w eksporcie full/memory",
                "no_summary_rule": "indeks nie streszcza treści rozmów; używa liczników i hashy jako dowodów ciągłości",
                "fast_index_rule": "w normalnej turze duże JSONL/TXT/JSON nie są skanowane liniowo; pełny recount należy uruchamiać tylko jawnie w audycie deep",
            },
            "extra": extra or {},
            "recent_events": recent_events,
        }
        self.index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        self._append_layer(index)
        return index

    def read_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return self.update_index(reason="initial_read_index", source="SessionContinuityManager")
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _now(self) -> tuple[str, str]:
        utc = datetime.now(timezone.utc)
        local = utc.astimezone(self.timezone)
        return utc.isoformat(), local.strftime(f"%Y-%m-%d %H:%M:%S {local.tzname() or self.timezone_name}")

    def _append_layer(self, index: dict[str, Any]) -> None:
        record = {
            "schema_version": SESSION_CONTINUITY_SCHEMA_VERSION,
            "created_at_utc": index["updated_at_utc"],
            "created_at_local": index["updated_at_local"],
            "version": self.version,
            "event_type": "session_continuity_index_update",
            "reason": index.get("reason"),
            "source": index.get("source"),
            "index_sha256": hashlib.sha256(json.dumps(index, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "tracked_files": index.get("files", []),
            "no_summary": True,
            "truth_boundary": "Indeks ciągłości zapisuje stany plików i hash; nie streszcza rozmowy ani nie udaje procesu w tle.",
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with self.layer_path.open("ab") as f:
            data = line.encode("utf-8")
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    def _file_state(self, rel: str) -> ContinuityFileState:
        path = self.root / rel
        if not path.exists() or not path.is_file():
            return ContinuityFileState(rel, False, 0, None, None, None, "missing", None)
        size = path.stat().st_size
        line_count: int | None = None
        last_hash: str | None = None
        tail_hash: str | None = None
        stats_mode = "metadata_only"
        if path.suffix in {".jsonl", ".txt", ".json"}:
            if size <= MAX_FULL_LINE_STATS_BYTES:
                line_count, last_hash = self._line_stats(path)
                stats_mode = "full_line_stats"
            else:
                last_hash, tail_hash = self._tail_stats(path)
                stats_mode = "fast_tail_stats_large_file"
        small_hash = self._sha_file(path) if size <= 2_000_000 else None
        return ContinuityFileState(rel, True, size, line_count, last_hash, small_hash, stats_mode, tail_hash)

    @staticmethod
    def _line_stats(path: Path) -> tuple[int, str | None]:
        count = 0
        last = b""
        try:
            with path.open("rb") as f:
                for line in f:
                    count += 1
                    if line.strip():
                        last = line.rstrip(b"\n")
            return count, hashlib.sha256(last).hexdigest() if last else None
        except Exception:
            return 0, None

    @staticmethod
    def _tail_stats(path: Path) -> tuple[str | None, str | None]:
        try:
            size = path.stat().st_size
            with path.open("rb") as f:
                f.seek(max(0, size - TAIL_SAMPLE_BYTES))
                tail = f.read(TAIL_SAMPLE_BYTES)
            tail_hash = hashlib.sha256(tail).hexdigest() if tail else None
            lines = [line.rstrip(b"\r\n") for line in tail.splitlines() if line.strip()]
            last = lines[-1] if lines else b""
            last_hash = hashlib.sha256(last).hexdigest() if last else None
            return last_hash, tail_hash
        except Exception:
            return None, None

    @staticmethod
    def _sha_file(path: Path) -> str | None:
        try:
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None
