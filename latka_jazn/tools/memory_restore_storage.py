from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence
import hashlib
import sqlite3

from latka_jazn.tools.memory_rebuild_common import DATABASE_FILENAMES, sqlite_check
from latka_jazn.tools.memory_restore_types import ProgressCallback, SCHEMA_VERSION, atomic_json, sha256_file

def resolve_database_paths(root: str | Path) -> dict[str, Path]:
    base = Path(root).expanduser().resolve()
    nested = base / "memory" / "sqlite"
    chosen = nested if (nested / DATABASE_FILENAMES["archive_chats"]).exists() or not (base / DATABASE_FILENAMES["archive_chats"]).exists() else base
    return {name: chosen / filename for name, filename in DATABASE_FILENAMES.items()}

def _database_summary(path: Path, *, include_hash: bool = False) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "path": str(path), "tables": {}, "integrity": None, "foreign_key_error_count": None}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {table: int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}
        check = sqlite_check(con, full=False)
        schema_text = "\n".join(str(row[0] or "") for row in con.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))
        result = {
            "exists": True,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "schema_sha256": hashlib.sha256(schema_text.encode("utf-8")).hexdigest(),
            "tables": counts,
            "integrity": check["integrity"],
            "foreign_key_error_count": check["foreign_key_error_count"],
            "ok": check["ok"],
        }
        if include_hash:
            result["sha256"] = sha256_file(path)
        return result
    finally:
        con.close()

def database_set_summary(root: str | Path, *, include_hash: bool = False) -> dict[str, Any]:
    paths = resolve_database_paths(root)
    return {name: _database_summary(path, include_hash=include_hash) for name, path in paths.items()}

def _attach(con: sqlite3.Connection, alias: str, path: Path) -> None:
    con.execute(f"ATTACH DATABASE ? AS {alias}", (str(path),))

def _table_columns(con: sqlite3.Connection, alias: str, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA {alias}.table_info('{table}')")}

def _logical_subset_compare(old_paths: dict[str, Path], new_paths: dict[str, Path]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    old_archive, new_archive = old_paths["archive_chats"], new_paths["archive_chats"]
    if old_archive.is_file() and new_archive.is_file():
        con = sqlite3.connect(":memory:")
        try:
            _attach(con, "old", old_archive); _attach(con, "new", new_archive)
            archive: dict[str, Any] = {}
            if {"conversation_id", "raw_tree_sha256"} <= _table_columns(con, "old", "conversations") and {"conversation_id", "raw_tree_sha256"} <= _table_columns(con, "new", "conversations"):
                archive["missing_conversations"] = con.execute("SELECT COUNT(*) FROM old.conversations o LEFT JOIN new.conversations n USING(conversation_id) WHERE n.conversation_id IS NULL").fetchone()[0]
                archive["changed_conversations"] = con.execute("SELECT COUNT(*) FROM old.conversations o JOIN new.conversations n USING(conversation_id) WHERE COALESCE(o.raw_tree_sha256,'')<>COALESCE(n.raw_tree_sha256,'')").fetchone()[0]
            if {"conversation_id", "node_id", "text_sha256"} <= _table_columns(con, "old", "nodes") and {"conversation_id", "node_id", "text_sha256"} <= _table_columns(con, "new", "nodes"):
                archive["missing_nodes"] = con.execute("SELECT COUNT(*) FROM old.nodes o LEFT JOIN new.nodes n ON n.conversation_id=o.conversation_id AND n.node_id=o.node_id WHERE n.node_id IS NULL").fetchone()[0]
                archive["changed_nodes"] = con.execute("SELECT COUNT(*) FROM old.nodes o JOIN new.nodes n ON n.conversation_id=o.conversation_id AND n.node_id=o.node_id WHERE COALESCE(o.text_sha256,'')<>COALESCE(n.text_sha256,'')").fetchone()[0]
            if "sha256" in _table_columns(con, "old", "import_sources") and "sha256" in _table_columns(con, "new", "import_sources"):
                archive["missing_import_source_hashes"] = con.execute("SELECT COUNT(*) FROM old.import_sources o LEFT JOIN new.import_sources n USING(sha256) WHERE n.sha256 IS NULL").fetchone()[0]
            report["archive_chats"] = archive
        finally:
            con.close()
    old_journal, new_journal = old_paths["journal"], new_paths["journal"]
    if old_journal.is_file() and new_journal.is_file():
        con = sqlite3.connect(":memory:")
        try:
            _attach(con, "old", old_journal); _attach(con, "new", new_journal)
            journal: dict[str, Any] = {}
            if {"identity_key", "content_sha256"} <= _table_columns(con, "old", "journal_entries") and {"identity_key", "content_sha256"} <= _table_columns(con, "new", "journal_entries"):
                journal["missing_entries"] = con.execute("SELECT COUNT(*) FROM old.journal_entries o LEFT JOIN new.journal_entries n USING(identity_key) WHERE n.identity_key IS NULL").fetchone()[0]
                journal["changed_entries"] = con.execute("SELECT COUNT(*) FROM old.journal_entries o JOIN new.journal_entries n USING(identity_key) WHERE COALESCE(o.content_sha256,'')<>COALESCE(n.content_sha256,'')").fetchone()[0]
            report["journal"] = journal
        finally:
            con.close()
    return report

def compare_database_sets(current_root: str | Path, baseline_roots: Sequence[str | Path]) -> dict[str, Any]:
    current_paths = resolve_database_paths(current_root)
    current = database_set_summary(current_root, include_hash=True)
    baselines = []
    for raw in baseline_roots:
        root = Path(raw).expanduser().resolve()
        old_paths = resolve_database_paths(root)
        summary = database_set_summary(root, include_hash=True)
        logical = _logical_subset_compare(old_paths, current_paths)
        baselines.append({"root": str(root), "summary": summary, "logical_subset": logical})
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "current_root": str(Path(current_root).expanduser().resolve()),
        "current": current,
        "baselines": baselines,
        "truth_boundary": "Comparison is read-only. Counts and hashes describe database state; they do not approve memory or promote records.",
    }

def backup_database_set(target_root: str | Path, destination: str | Path, callback: ProgressCallback | None = None) -> dict[str, Any]:
    source_paths = resolve_database_paths(target_root)
    destination_root = Path(destination).expanduser().resolve()
    destination_sqlite = destination_root / "memory" / "sqlite"
    destination_sqlite.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, source in source_paths.items():
        target = destination_sqlite / DATABASE_FILENAMES[name]
        if not source.is_file():
            results[name] = {"exists": False, "source": str(source)}
            continue
        if callback:
            callback({"event": "backup_started", "database": name, "source": str(source), "target": str(target)})
        source_con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        target_con = sqlite3.connect(target)
        try:
            source_con.backup(target_con)
            validation = sqlite_check(target_con, full=True)
        finally:
            target_con.close(); source_con.close()
        results[name] = {"exists": True, "source": str(source), "target": str(target), "validation": validation}
        if callback:
            callback({"event": "backup_completed", "database": name, "ok": validation["ok"]})
    payload = {"ok": all(not row.get("exists") or row["validation"]["ok"] for row in results.values()), "databases": results}
    atomic_json(destination_root / "backup_manifest.json", payload)
    return payload

__all__ = [
    "backup_database_set", "compare_database_sets", "database_set_summary", "resolve_database_paths",
]
