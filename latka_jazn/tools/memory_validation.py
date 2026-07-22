from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterable
import hashlib
import json
import sqlite3

from latka_jazn.config import JaznConfig
from latka_jazn.memory.memory_tier_status import inspect_memory_tier_store
from latka_jazn.memory.normalization_sidecar import MemoryNormalizationSidecar
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_large_validation")
ProgressCallback = Callable[[int, int, str], None]
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
TRUTH_BOUNDARY = (
    "This report validates readable SQLite structure, foreign keys, selected schema contracts and wake-state. "
    "It does not prove that every historical conversation was imported, that recall quality is sufficient, "
    "or that L3 promotion was authorized."
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sidecars(path: Path) -> tuple[Path, Path]:
    return path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")


def _read_only_uri(path: Path) -> tuple[str | None, str | None, dict[str, Any]]:
    wal, shm = _sidecars(path)
    wal_exists = wal.is_file()
    shm_exists = shm.is_file()
    state = {
        "wal_present": wal_exists,
        "wal_size_bytes": wal.stat().st_size if wal_exists else 0,
        "shm_present": shm_exists,
        "shm_size_bytes": shm.stat().st_size if shm_exists else 0,
    }
    if wal_exists != shm_exists:
        return None, "incomplete_sqlite_wal_sidecars", state
    suffix = "?mode=ro" if wal_exists else "?mode=ro&immutable=1"
    return f"file:{path.as_posix()}{suffix}", None, state


def _safe_under(root: Path, candidate: Path) -> Path | None:
    resolved = candidate.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


@dataclass(slots=True, frozen=True)
class MemoryValidationTarget:
    role: str
    path: str
    source: str
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SQLiteValidationResult:
    role: str
    path: str
    source: str
    required: bool
    exists: bool
    ok: bool
    mode: str
    size_bytes: int
    elapsed_seconds: float
    integrity_result: list[str]
    foreign_key_error_count: int | None
    foreign_key_errors: list[list[Any]]
    schema_object_counts: dict[str, int]
    table_counts: dict[str, int]
    page_metrics: dict[str, Any]
    sidecars: dict[str, Any]
    sha256: str | None
    error_type: str | None = None
    error: str | None = None
    read_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _known_targets(config: JaznConfig) -> list[MemoryValidationTarget]:
    values = [
        MemoryValidationTarget("runtime_memory", str(config.memory_db_path_readonly), "config", True),
        MemoryValidationTarget("runtime_audit", str(config.audit_db_path_readonly), "config", False),
        MemoryValidationTarget("recovered_memory", str(config.recovered_memory_db_path), "config", False),
        MemoryValidationTarget("normalization_sidecar", str(config.normalization_sidecar_db_path), "config", True),
        MemoryValidationTarget("memory_tiers_v151", str(config.memory_tier_db_path), "config", True),
        MemoryValidationTarget(
            "conversation_archive_manifest",
            str(config.conversation_archive_manifest_path),
            "config",
            False,
        ),
    ]
    return values


def _manifest_targets(root: Path, manifest_path: Path, role_prefix: str) -> list[MemoryValidationTarget]:
    if not manifest_path.is_file():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    result: list[MemoryValidationTarget] = []
    active = str(payload.get("active_write_shard") or "")
    for item in payload.get("shards") or []:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        candidate = _safe_under(root, root / str(item["path"]))
        if candidate is None:
            continue
        shard_id = str(item.get("shard_id") or "unknown")
        result.append(
            MemoryValidationTarget(
                role=f"{role_prefix}_shard_{shard_id}",
                path=str(candidate),
                source=str(manifest_path.relative_to(root)),
                required=shard_id == active,
            )
        )
    return result


def discover_memory_validation_targets(
    root: Path | str,
    *,
    include_all_sqlite: bool = False,
) -> list[MemoryValidationTarget]:
    runtime_root = Path(root).expanduser().resolve()
    config = JaznConfig(root=runtime_root)
    targets = _known_targets(config)
    targets.extend(
        _manifest_targets(
            runtime_root,
            runtime_root / config.conversation_shard_manifest_name,
            "conversation",
        )
    )
    targets.extend(
        _manifest_targets(
            runtime_root,
            runtime_root / config.audit_shard_manifest_name,
            "audit",
        )
    )
    if include_all_sqlite:
        sqlite_root = runtime_root / "memory" / "sqlite"
        if sqlite_root.is_dir():
            for path in sqlite_root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in SQLITE_SUFFIXES:
                    continue
                if path.name.endswith(("-wal", "-shm")):
                    continue
                targets.append(
                    MemoryValidationTarget(
                        role="discovered_sqlite",
                        path=str(path.resolve()),
                        source="memory/sqlite recursive discovery",
                        required=False,
                    )
                )

    merged: dict[str, MemoryValidationTarget] = {}
    for target in targets:
        candidate = _safe_under(runtime_root, Path(target.path))
        if candidate is None:
            continue
        key = str(candidate)
        existing = merged.get(key)
        if existing is None:
            merged[key] = MemoryValidationTarget(
                role=target.role,
                path=key,
                source=target.source,
                required=target.required,
            )
            continue
        roles = sorted(set(existing.role.split("+") + target.role.split("+")))
        sources = sorted(set(existing.source.split(";") + target.source.split(";")))
        merged[key] = MemoryValidationTarget(
            role="+".join(roles),
            path=key,
            source=";".join(sources),
            required=existing.required or target.required,
        )
    return sorted(merged.values(), key=lambda item: (not item.required, item.role, item.path))


def validate_sqlite_target(
    target: MemoryValidationTarget,
    *,
    full: bool = False,
    max_errors: int = 100,
    table_counts: bool = False,
    hash_files: bool = False,
) -> SQLiteValidationResult:
    started = perf_counter()
    database = Path(target.path)
    mode = "integrity_check" if full else "quick_check"
    if not database.is_file():
        return SQLiteValidationResult(
            role=target.role,
            path=str(database),
            source=target.source,
            required=target.required,
            exists=False,
            ok=not target.required,
            mode=mode,
            size_bytes=0,
            elapsed_seconds=round(perf_counter() - started, 6),
            integrity_result=[],
            foreign_key_error_count=None,
            foreign_key_errors=[],
            schema_object_counts={},
            table_counts={},
            page_metrics={},
            sidecars={},
            sha256=None,
            error_type="FileNotFoundError" if target.required else None,
            error="required memory database is missing" if target.required else None,
        )

    uri, sidecar_error, sidecar_state = _read_only_uri(database)
    if uri is None:
        return SQLiteValidationResult(
            role=target.role,
            path=str(database),
            source=target.source,
            required=target.required,
            exists=True,
            ok=False,
            mode=mode,
            size_bytes=database.stat().st_size,
            elapsed_seconds=round(perf_counter() - started, 6),
            integrity_result=[],
            foreign_key_error_count=None,
            foreign_key_errors=[],
            schema_object_counts={},
            table_counts={},
            page_metrics={},
            sidecars=sidecar_state,
            sha256=None,
            error_type="SidecarStateError",
            error=sidecar_error,
        )

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=30.0)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        pragma = "integrity_check" if full else "quick_check"
        bounded_errors = max(1, min(int(max_errors), 10000))
        integrity_rows = [
            str(row[0])
            for row in connection.execute(f"PRAGMA {pragma}({bounded_errors})")
        ]
        foreign_cursor = connection.execute("PRAGMA foreign_key_check")
        foreign_rows = foreign_cursor.fetchmany(bounded_errors + 1)
        foreign_truncated = len(foreign_rows) > bounded_errors
        foreign_rows = foreign_rows[:bounded_errors]

        schema_counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT type, COUNT(*) FROM sqlite_schema GROUP BY type ORDER BY type"
            )
        }
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts: dict[str, int] = {}
        if table_counts:
            for table in tables:
                counts[table] = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                    ).fetchone()[0]
                )
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        page_metrics = {
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "allocated_bytes": page_size * page_count,
            "freelist_bytes": page_size * freelist_count,
            "journal_mode": journal_mode,
            "user_version": user_version,
            "application_id": application_id,
            "table_count": len(tables),
            "foreign_key_errors_truncated": foreign_truncated,
        }
        integrity_ok = integrity_rows == ["ok"]
        foreign_ok = not foreign_rows and not foreign_truncated
        return SQLiteValidationResult(
            role=target.role,
            path=str(database),
            source=target.source,
            required=target.required,
            exists=True,
            ok=integrity_ok and foreign_ok,
            mode=mode,
            size_bytes=database.stat().st_size,
            elapsed_seconds=round(perf_counter() - started, 6),
            integrity_result=integrity_rows,
            foreign_key_error_count=(len(foreign_rows) + (1 if foreign_truncated else 0)),
            foreign_key_errors=[list(row) for row in foreign_rows],
            schema_object_counts=schema_counts,
            table_counts=counts,
            page_metrics=page_metrics,
            sidecars=sidecar_state,
            sha256=_sha256_file(database) if hash_files else None,
        )
    except (sqlite3.DatabaseError, OSError, ValueError) as exc:
        return SQLiteValidationResult(
            role=target.role,
            path=str(database),
            source=target.source,
            required=target.required,
            exists=True,
            ok=False,
            mode=mode,
            size_bytes=database.stat().st_size if database.exists() else 0,
            elapsed_seconds=round(perf_counter() - started, 6),
            integrity_result=[],
            foreign_key_error_count=None,
            foreign_key_errors=[],
            schema_object_counts={},
            table_counts={},
            page_metrics={},
            sidecars=sidecar_state,
            sha256=None,
            error_type=type(exc).__name__,
            error=str(exc),
        )
    finally:
        if connection is not None:
            connection.close()


def _atomic_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            handle.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_large_memory(
    root: Path | str,
    *,
    full: bool = False,
    include_all_sqlite: bool = False,
    max_errors: int = 100,
    table_counts: bool = False,
    hash_files: bool = False,
    output: Path | str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    runtime_root = Path(root).expanduser().resolve()
    config = JaznConfig(root=runtime_root)
    targets = discover_memory_validation_targets(
        runtime_root,
        include_all_sqlite=include_all_sqlite,
    )
    total_steps = max(1, len(targets) + 2)
    results: list[dict[str, Any]] = []
    for index, target in enumerate(targets, start=1):
        if progress is not None:
            progress(index - 1, total_steps, f"SQLite: {target.role}")
        result = validate_sqlite_target(
            target,
            full=full,
            max_errors=max_errors,
            table_counts=table_counts,
            hash_files=hash_files,
        )
        results.append(result.to_dict())

    if progress is not None:
        progress(len(targets), total_steps, "Weryfikuję wake-state")
    sidecar = MemoryNormalizationSidecar(
        runtime_root,
        source_db_path=config.normalization_source_db_path,
        sidecar_db_path=config.normalization_sidecar_db_path,
        runtime_version=config.version,
    )
    wake_state = sidecar.wake_state_status(deep_verify=full).to_dict()

    if progress is not None:
        progress(len(targets) + 1, total_steps, "Weryfikuję warstwy L1/L2/L3")
    tier_status = inspect_memory_tier_store(config.memory_tier_db_path, full=full).to_dict()

    failed = [item for item in results if not item.get("ok")]
    required_missing = [
        item for item in results if item.get("required") and not item.get("exists")
    ]
    existing = [item for item in results if item.get("exists")]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _now_utc(),
        "runtime_root": str(runtime_root),
        "runtime_version": config.version,
        "validation_mode": "full" if full else "quick",
        "sqlite_pragma": "integrity_check" if full else "quick_check",
        "max_errors": max(1, min(int(max_errors), 10000)),
        "include_all_sqlite": bool(include_all_sqlite),
        "table_counts_enabled": bool(table_counts),
        "hash_files_enabled": bool(hash_files),
        "targets": [item.to_dict() for item in targets],
        "databases": results,
        "wake_state": wake_state,
        "memory_tiers_v151": tier_status,
        "summary": {
            "target_count": len(targets),
            "existing_database_count": len(existing),
            "validated_ok_count": sum(1 for item in results if item.get("ok")),
            "failed_count": len(failed),
            "required_missing_count": len(required_missing),
            "total_database_bytes": sum(int(item.get("size_bytes") or 0) for item in existing),
            "wake_state_ready": wake_state.get("status") == "ready",
            "memory_tiers_ready": tier_status.get("ready") is True,
        },
        "truth_boundary": TRUTH_BOUNDARY,
    }
    payload["ok"] = bool(
        existing
        and not failed
        and not required_missing
        and wake_state.get("status") == "ready"
        and tier_status.get("ready") is True
    )

    if output is not None:
        destination = Path(output)
        if not destination.is_absolute():
            destination = runtime_root / destination
        safe_destination = _safe_under(runtime_root, destination)
        if safe_destination is None:
            raise ValueError("memory validation output must stay under runtime root")
        _atomic_report(safe_destination, payload)
        payload["report_path"] = str(safe_destination)
    if progress is not None:
        progress(total_steps, total_steps, "Walidacja pamięci zakończona")
    return payload


__all__ = [
    "MemoryValidationTarget",
    "SQLiteValidationResult",
    "discover_memory_validation_targets",
    "validate_large_memory",
    "validate_sqlite_target",
]
