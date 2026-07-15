from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import sqlite3

from latka_jazn.audit.audit_context_store import AuditContextStore
from latka_jazn.config import JaznConfig
from latka_jazn.db.shard_manifest import ensure_manifest
from latka_jazn.memory.store import MemoryStore
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("runtime_write_access_contract")


@dataclass(slots=True)
class RuntimeWriteAccessStatus:
    schema_version: str
    status: str
    ok: bool
    initialized: bool
    writes_enabled: bool
    active_runtime_write_database: str | None
    active_runtime_audit_database: str | None
    memory_db_exists: bool
    audit_db_exists: bool
    memory_integrity: str | None = None
    audit_integrity: str | None = None
    memory_error: str | None = None
    audit_error: str | None = None
    access_mode: str = "disabled_missing"
    write_capable: bool = False
    writes_observed: bool = False
    memory_foreign_key_violations: int = 0
    audit_foreign_key_violations: int = 0
    memory_table_count: int = 0
    audit_table_count: int = 0
    memory_record_count: int = 0
    audit_record_count: int = 0
    weak_points_repaired: list[str] = field(default_factory=lambda: [
        "niepewny_czas_bez_trusted_timestamp",
        "brak_biezacego_runtime_write_v1_po_odchudzeniu_paczki",
        "osuwanie_glosu_Latki_w_trzecia_osobe_lub_techniczny_loader",
        "status_initialized_false_mimo_spojnych_baz",
        "mieszanie_biezacego_trybu_z_historia_zapisow",
    ])
    truth_boundary: str = (
        "runtime_write_v1 jest bieżącą lokalną warstwą zapisu runtime. Pole writes_enabled opisuje bieżące "
        "zezwolenie tej operacji/procesu, write_capable opisuje sprawność techniczną baz, a writes_observed "
        "potwierdza historyczne rekordy. Warstwa nie jest archiwum pełnych eksportów ChatGPT, repozytorium Git "
        "ani materiałem do publikacji."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _relative_or_none(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return str(path)


def _sqlite_status(path: Path) -> dict[str, Any]:
    result = {
        "integrity": None,
        "error": None,
        "foreign_key_violations": 0,
        "table_count": 0,
        "record_count": 0,
    }
    if not path.exists():
        return result
    try:
        con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10.0)
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
            result["integrity"] = str(row[0]) if row else None
            result["foreign_key_violations"] = len(list(con.execute("PRAGMA foreign_key_check")))
            tables = [
                str(row[0]) for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            result["table_count"] = len(tables)
            total = 0
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                try:
                    total += int(con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
                except sqlite3.DatabaseError:
                    continue
            result["record_count"] = total
        finally:
            con.close()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def ensure_runtime_write_v1(config: JaznConfig) -> RuntimeWriteAccessStatus:
    """Create a clean runtime_write_v1 store only when explicitly requested."""
    root = Path(config.root).resolve()
    memory_path = Path(config.memory_db_path)
    audit_path = Path(config.audit_db_path)

    store = MemoryStore(memory_path)
    try:
        store.set_meta("runtime_write_access_contract", SCHEMA_VERSION)
        store.set_meta("runtime_write_source", "clean_runtime_write_v1_initialized_after_pack_exclusion")
    finally:
        store.close()

    audit = AuditContextStore(audit_path)
    try:
        audit.append_event(
            "runtime_write_v1_initialized",
            {
                "schema_version": SCHEMA_VERSION,
                "memory_db": _relative_or_none(root, memory_path),
                "audit_db": _relative_or_none(root, audit_path),
                "reason": "clean runtime_write_v1 recreated after excluding stale runtime_write shards from release pack",
            },
            source="RuntimeWriteAccessContract",
            actor="system",
            tags=["runtime_write", "init", "clean_store"],
        )
    finally:
        audit.close()

    ensure_manifest(
        root,
        config.conversation_shard_manifest_name,
        logical_database="chat_context",
        role="canonical_runtime_conversation_memory",
        default_db_path=config.memory_db_name,
        max_file_bytes=config.max_sqlite_file_bytes,
    )
    ensure_manifest(
        root,
        config.audit_shard_manifest_name,
        logical_database="chat_context_audit",
        role="canonical_realtime_audit",
        default_db_path=config.audit_db_name,
        max_file_bytes=config.max_sqlite_file_bytes,
    )
    return build_runtime_write_access_status(config, initialize=False, writes_enabled=True)


def build_runtime_write_access_status(
    config: JaznConfig,
    *,
    initialize: bool = False,
    writes_enabled: bool | None = None,
) -> RuntimeWriteAccessStatus:
    root = Path(config.root).resolve()
    if initialize:
        return ensure_runtime_write_v1(config)

    memory_path = Path(config.memory_db_path_readonly)
    audit_path = Path(config.audit_db_path_readonly)
    memory_exists = memory_path.exists()
    audit_exists = audit_path.exists()
    memory = _sqlite_status(memory_path)
    audit = _sqlite_status(audit_path)

    ok = bool(
        memory_exists and audit_exists
        and memory["integrity"] == "ok" and audit["integrity"] == "ok"
        and not memory["foreign_key_violations"] and not audit["foreign_key_violations"]
        and not memory["error"] and not audit["error"]
    )
    initialized = bool(memory["table_count"] and audit["table_count"])
    write_capable = bool(ok and initialized)
    writes_observed = bool(memory["record_count"] or audit["record_count"])
    current_write_enabled = bool(writes_enabled) and write_capable
    if ok:
        status = "ready"
        access_mode = "ready_write_capable" if current_write_enabled else "ready_write_capable_readonly"
    elif not memory_exists and not audit_exists:
        status = "missing_can_initialize"
        access_mode = "disabled_missing"
    else:
        status = "partial_or_integrity_failed"
        access_mode = "error_integrity_failed" if (
            memory["error"] or audit["error"] or memory["integrity"] not in {None, "ok"}
            or audit["integrity"] not in {None, "ok"}
            or memory["foreign_key_violations"] or audit["foreign_key_violations"]
        ) else "partial_missing"

    return RuntimeWriteAccessStatus(
        schema_version=SCHEMA_VERSION,
        status=status,
        ok=ok,
        initialized=initialized,
        writes_enabled=current_write_enabled,
        active_runtime_write_database=_relative_or_none(root, memory_path) if memory_exists else None,
        active_runtime_audit_database=_relative_or_none(root, audit_path) if audit_exists else None,
        memory_db_exists=memory_exists,
        audit_db_exists=audit_exists,
        memory_integrity=memory["integrity"],
        audit_integrity=audit["integrity"],
        memory_error=memory["error"],
        audit_error=audit["error"],
        access_mode=access_mode,
        write_capable=write_capable,
        writes_observed=writes_observed,
        memory_foreign_key_violations=int(memory["foreign_key_violations"]),
        audit_foreign_key_violations=int(audit["foreign_key_violations"]),
        memory_table_count=int(memory["table_count"]),
        audit_table_count=int(audit["table_count"]),
        memory_record_count=int(memory["record_count"]),
        audit_record_count=int(audit["record_count"]),
    )
