from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from latka_jazn.config import JaznConfig
from latka_jazn.memory.raw_archive import chat_archive_diagnostics
from latka_jazn.memory.conversation_archive import build_conversation_archive_status
from latka_jazn.memory.event_ledger import (
    DEFAULT_JSONL_SHARD_MAX_BYTES,
    RUNTIME_EVENT_ERRORS_PREFIX,
    RUNTIME_EVENTS_DIRNAME,
    RUNTIME_EVENTS_PREFIX,
    jsonl_shard_paths,
)
from latka_jazn.audit.audit_context_store import AuditContextStore
from latka_jazn.bootstrap.contract_loader import BootstrapContractRepository

STAT_TABLES = [
    "events",
    "journal",
    "source_files",
    "legacy_conversations",
    "legacy_messages",
    "episodic_memories",
    "semantic_facts",
    "procedural_rules",
    "reflection_entries",
    "truth_audits",
]

LAYER_FILES = [
    "episodic.jsonl",
    "semantic.jsonl",
    "procedural.jsonl",
    "reflections.jsonl",
    "truth_audits.jsonl",
    "affective.jsonl",
]


def display_path(root: Path, path: str | Path | None) -> str | None:
    """Return a stable package-relative path when possible.

    Diagnostic reports should not bake builder-specific absolute paths such as
    /mnt/data/latka_hotfix_... into distributable reports or memory entries.
    """
    if path is None:
        return None
    p = Path(path)
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def _sqlite_stats_readonly(db_path: Path) -> tuple[dict[str, int], str]:
    stats = {name: 0 for name in STAT_TABLES}
    if not db_path.exists():
        return stats, "missing_sqlite"
    try:
        uri = f"file:{db_path.resolve()}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            for table in STAT_TABLES:
                try:
                    stats[table] = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except sqlite3.Error:
                    stats[table] = 0
        finally:
            con.close()
        return stats, "sqlite_readonly"
    except sqlite3.Error as exc:
        return stats, f"sqlite_readonly_error:{exc!r}"


def _sqlite_meta_readonly(db_path: Path, key: str) -> str | None:
    if not db_path.exists():
        return None
    try:
        uri = f"file:{db_path.resolve()}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None
        finally:
            con.close()
    except sqlite3.Error:
        return None


def layer_counts(root: Path) -> dict[str, int | str]:
    layered = root / "memory" / "layered"
    counts: dict[str, int | str] = {}
    for name in LAYER_FILES:
        path = layered / name
        if path.exists():
            try:
                counts[name] = sum(1 for line in path.open("r", encoding="utf-8") if line.strip())
            except Exception:
                counts[name] = "error"
        else:
            counts[name] = "missing"
    return counts


def _jsonl_count_or_status(path: Path) -> int | str:
    if not path.exists():
        return "missing"
    try:
        return sum(1 for line in path.open("r", encoding="utf-8") if line.strip())
    except Exception as exc:
        return f"error:{type(exc).__name__}:{exc}"


def _legacy_jsonl_status(root: Path, path: Path) -> dict[str, Any] | str:
    if not path.exists():
        return "missing"
    size = path.stat().st_size
    status: dict[str, Any] = {
        "path": display_path(root, path),
        "size_bytes": size,
        "legacy": True,
        "active_write_target": False,
    }
    if size > DEFAULT_JSONL_SHARD_MAX_BYTES:
        status["line_count"] = "skipped_large_legacy_file"
        status["issue"] = "legacy JSONL exceeds active shard limit and should be archived or removed after migration"
    else:
        status["line_count"] = _jsonl_count_or_status(path)
    return status


def _jsonl_shard_status(root: Path, directory: Path, prefix: str) -> list[dict[str, Any]]:
    shards = []
    for path in jsonl_shard_paths(directory, prefix):
        size = path.stat().st_size if path.exists() else 0
        shards.append(
            {
                "path": display_path(root, path),
                "size_bytes": size,
                "line_count": _jsonl_count_or_status(path),
                "within_2gb_limit": size <= DEFAULT_JSONL_SHARD_MAX_BYTES,
            }
        )
    return shards


def build_event_ledger_status(root: Path, raw: Path) -> dict[str, Any]:
    directory = raw / RUNTIME_EVENTS_DIRNAME
    conversation_turns = raw / "conversation_turns.jsonl"
    return {
        "directory": display_path(root, directory),
        "max_shard_bytes": DEFAULT_JSONL_SHARD_MAX_BYTES,
        "runtime_events_shards": _jsonl_shard_status(root, directory, RUNTIME_EVENTS_PREFIX),
        "runtime_event_errors_shards": _jsonl_shard_status(root, directory, RUNTIME_EVENT_ERRORS_PREFIX),
        "conversation_turns.jsonl": {
            "path": display_path(root, conversation_turns),
            "line_count": _jsonl_count_or_status(conversation_turns),
            "size_bytes": conversation_turns.stat().st_size if conversation_turns.exists() else 0,
        },
        "legacy_runtime_events.jsonl": _legacy_jsonl_status(root, raw / "runtime_events.jsonl"),
        "legacy_runtime_event_errors.jsonl": _legacy_jsonl_status(root, raw / "runtime_event_errors.jsonl"),
    }


def build_runtime_status(config: JaznConfig | None = None, store: Any | None = None, *, readonly: bool = False) -> str:
    """Build the runtime diagnostic text without forcing write-side effects.

    When ``store`` is omitted this function opens SQLite in read-only mode and
    does not create, update or close a MemoryStore. When a live ``store`` is
    passed, the caller may already be inside a mutable runtime session; this
    function still does not write events, journal rows, truth audits or runtime
    memory candidates.
    """
    cfg = config or JaznConfig()
    root = Path(cfg.root).resolve()
    raw = root / "memory" / "raw"
    chat = raw / "chat.html"
    archive_diag = chat_archive_diagnostics(root)
    counts = layer_counts(root)
    event_ledger_counts = build_event_ledger_status(root, raw)

    previous_sqlite = []
    previous_dir = root / "workspace_runtime" / "previous_versions"
    if previous_dir.exists():
        for db in sorted(previous_dir.glob("*.sqlite3")):
            previous_sqlite.append({"path": display_path(root, db), "size_bytes": db.stat().st_size})

    audit_status = AuditContextStore.readonly_status(cfg.audit_db_path)
    contract_status = BootstrapContractRepository(root).status()
    conversation_archive_status = build_conversation_archive_status(root).to_dict()

    if store is not None:
        stats = store.stats()
        imported_sha = store.get_meta("chat_html_import_sha256")
        sqlite_mode = "live_store_no_diagnostic_write"
    else:
        stats, sqlite_mode = _sqlite_stats_readonly(cfg.memory_db_path)
        imported_sha = _sqlite_meta_readonly(cfg.memory_db_path, "chat_html_import_sha256")

    issues: list[str] = []
    raw_memory_note = ""
    indexed_raw_memory_available = bool(imported_sha) and stats.get("legacy_messages", 0) > 0
    if not chat.exists():
        if archive_diag.get("archive_present"):
            if indexed_raw_memory_available:
                raw_memory_note = (
                    "rozpakowany memory/raw/chat.html nie jest w katalogu, ale skompresowane chat.html.7z "
                    "jest obecne, a SQLite ma aktywny indeks surowej pamięci; rozpakowanie HTML jest opcjonalne "
                    "do awaryjnego pełnego skanu raw"
                )
            elif not archive_diag.get("py7zr_available") and not archive_diag.get("system_7z"):
                issues.append(
                    "chat.html.7z jest, ale py7zr nie jest zainstalowane i nie znaleziono systemowego 7z/7za/7zr — "
                    "uruchom `python -m pip install -r requirements.txt`, a potem `python tools/memory_repair.py --import-chat-html`"
                )
            else:
                issues.append(
                    "chat.html.7z jest i ekstraktor jest dostępny, ale memory/raw/chat.html nie jest jeszcze rozpakowany ani zaindeksowany — "
                    "uruchom `synchAll`, `/import_chat_html` albo `python tools/memory_repair.py --import-chat-html`"
                )
        else:
            issues.append("brak memory/raw/chat.html i brak chat.html.7z — surowa pamięć nie jest dostępna")
    if chat.exists() and stats.get("legacy_messages", 0) == 0:
        issues.append("chat.html istnieje, ale legacy_messages=0 — trzeba wykonać `/import_chat_html`, `synchAll` albo `python tools/memory_repair.py --import-chat-html`")
    episodic_file_count = counts.get("episodic.jsonl")
    if isinstance(episodic_file_count, int) and stats.get("episodic_memories", 0) < episodic_file_count:
        issues.append("SQLite widzi mniej epizodów niż plik episodic.jsonl — trzeba zsynchronizować pliki pamięci")

    state_note = "ciągłość przerw jest zapisywana w workspace_runtime/runtime_state.json, ale to nadal nie jest Daemon w tle"
    root_note = ". (ścieżki diagnostyczne są względne względem katalogu paczki, żeby nie utrwalać ścieżek budowania)"
    archive_path = display_path(root, archive_diag.get("archive_path"))

    return (
        f"Diagnoza runtime {cfg.version}:\n"
        f"- aktywny root: {root_note}\n"
        f"- tryb diagnostyki: {'read-only' if readonly else sqlite_mode}\n"
        f"- SQLite pamięci rozmownej/runtime: {display_path(root, cfg.memory_db_path)}\n"
        f"- conversation_archive/FTS/staging: status={conversation_archive_status.get('status')}, ready_for_search={conversation_archive_status.get('ready_for_search')}, counts={json.dumps(conversation_archive_status.get('counts') or {}, ensure_ascii=False)}\n"
        f"- SQLite audytu: {display_path(root, cfg.audit_db_path)}; status={json.dumps(audit_status, ensure_ascii=False)}\n"
        f"- shard manifest rozmowny: {display_path(root, root / cfg.conversation_shard_manifest_name)}\n"
        f"- shard manifest audytu: {display_path(root, root / cfg.audit_shard_manifest_name)}\n"
        f"- embedded bootstrap/README/AGENTS/contracts: {json.dumps(contract_status, ensure_ascii=False)}\n"
        f"- chat.html: {'jest' if chat.exists() else 'brak'}" + (f", ścieżka={display_path(root, chat)}, rozmiar={chat.stat().st_size} B" if chat.exists() else "") + "\n"
        f"- chat.html.7z: {'jest' if archive_diag.get('archive_present') else 'brak'}" + (f", ścieżka={archive_path}, rozmiar={archive_diag.get('archive_size_bytes')} B" if archive_diag.get('archive_present') else "") + "\n"
        f"- py7zr: {'dostępne' if archive_diag.get('py7zr_available') else 'brak'}\n"
        f"- systemowy 7z/7za/7zr: {archive_diag.get('system_7z') or 'brak'}\n"
        f"- pełny import raw możliwy teraz: {'tak' if archive_diag.get('can_unpack') else 'nie'}\n"
        f"- chat_html_import_sha256: {'jest' if imported_sha else 'brak'}\n"
        f"- SQLite statystyki: {json.dumps(stats, ensure_ascii=False)}\n"
        f"- pliki warstwowe: {json.dumps(counts, ensure_ascii=False)}\n"
        f"- surowy event ledger: {json.dumps(event_ledger_counts, ensure_ascii=False)}\n"
        f"- zachowane poprzednie bazy SQLite: {json.dumps(previous_sqlite, ensure_ascii=False)}\n"
        f"- stan między wywołaniami: {state_note}\n"
        f"- stan surowej pamięci: {raw_memory_note or 'aktywny albo niewymagający dodatkowego komentarza'}\n"
        f"- nadal do pilnowania: {('; '.join(issues) if issues else 'brak krytycznych braków wykrytych przez diagnostykę')}"
    )
