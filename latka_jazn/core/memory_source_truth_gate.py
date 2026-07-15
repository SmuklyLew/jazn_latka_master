from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import sqlite3
from typing import Any

from latka_jazn.memory.database_identity import (
    DatabaseIdentity,
    initialize_database_identity,
    read_database_identity,
)
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version

SCHEMA_VERSION = schema_version("memory_source_truth_gate")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


@dataclass(slots=True)
class MemorySourceTruthResult:
    active: bool
    state: str
    database_path: str
    active_root: str
    trusted_memory_root: str
    database_identity: dict[str, Any] | None
    integrity_check: list[str]
    foreign_key_check: list[list[Any]]
    record_count: int
    checks: dict[str, bool]
    violations: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemorySourceTruthGate:
    """Fail-closed activation gate for a persistent SQLite memory database.

    Code root and data root are deliberately distinct. The data root may be a
    persistent location that survives replacing the active code directory, but
    it must be explicitly trusted by the runtime marker/configuration.
    """

    def __init__(
        self,
        *,
        active_root: Path | str,
        trusted_memory_root: Path | str | None = None,
        marker_path: Path | str | None = None,
        manifest_path: Path | str | None = None,
        runtime_version: str = PACKAGE_VERSION_FULL,
    ) -> None:
        self.active_root = Path(active_root).resolve()
        self.trusted_memory_root = Path(
            trusted_memory_root or (self.active_root / "memory")
        ).resolve()
        self.marker_path = Path(marker_path).resolve() if marker_path else None
        self.manifest_path = Path(manifest_path).resolve() if manifest_path else self.active_root / "MANIFEST_CURRENT.json"
        self.runtime_version = runtime_version

    def evaluate(
        self,
        database_path: Path | str,
        *,
        expected_database_uuid: str | None = None,
        expected_schema_identity: str | None = None,
        min_schema_version: int | None = None,
        initialize_identity: bool = False,
    ) -> MemorySourceTruthResult:
        db_path = Path(database_path).resolve()
        marker = _load_json(self.marker_path)
        manifest = _load_json(self.manifest_path)
        violations: list[str] = []
        checks: dict[str, bool] = {}

        checks["database_exists"] = db_path.is_file()
        checks["path_under_trusted_memory_root"] = _is_relative_to(db_path, self.trusted_memory_root)
        if not checks["database_exists"]:
            violations.append("database_missing")
        if not checks["path_under_trusted_memory_root"]:
            violations.append("database_outside_trusted_memory_root")

        marker_root_value = marker.get("active_root") or marker.get("active_folder")
        if self.marker_path is None:
            checks["marker_root_matches"] = True
        else:
            checks["marker_root_matches"] = bool(marker_root_value) and Path(str(marker_root_value)).resolve() == self.active_root
            if not checks["marker_root_matches"]:
                violations.append("active_runtime_marker_mismatch")

        marker_memory_root = marker.get("trusted_memory_root")
        if marker_memory_root:
            checks["marker_memory_root_matches"] = Path(str(marker_memory_root)).resolve() == self.trusted_memory_root
            if not checks["marker_memory_root_matches"]:
                violations.append("trusted_memory_root_marker_mismatch")
        else:
            checks["marker_memory_root_matches"] = self.marker_path is None
            if self.marker_path is not None:
                violations.append("trusted_memory_root_missing_from_marker")

        manifest_version = str(manifest.get("runtime_version") or manifest.get("version") or "")
        checks["manifest_version_compatible"] = not manifest or manifest_version in {self.runtime_version, self.runtime_version.removeprefix("v")}
        if not checks["manifest_version_compatible"]:
            violations.append("manifest_runtime_version_mismatch")

        integrity_rows: list[str] = []
        foreign_rows: list[list[Any]] = []
        identity: DatabaseIdentity | None = None
        record_count = 0
        if checks["database_exists"]:
            try:
                connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=rw", uri=True, timeout=30.0)
                connection.execute("PRAGMA foreign_keys=ON")
                integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()]
                foreign_rows = [list(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()]
                identity = read_database_identity(connection)
                if identity is None and initialize_identity and expected_schema_identity:
                    with connection:
                        identity = initialize_database_identity(
                            connection,
                            schema_identity=expected_schema_identity,
                            schema_version_number=int(min_schema_version or 1),
                            runtime_version=self.runtime_version,
                        )
                tables = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                ]
                for table in tables:
                    quoted = table.replace('"', '""')
                    try:
                        record_count += int(connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0])
                    except sqlite3.DatabaseError:
                        continue
                connection.close()
            except Exception as exc:
                violations.append(f"sqlite_open_or_check_failed:{type(exc).__name__}")

        checks["integrity_ok"] = integrity_rows == ["ok"]
        checks["foreign_keys_ok"] = not foreign_rows
        checks["identity_present"] = identity is not None
        checks["records_present"] = record_count > 0
        if not checks["integrity_ok"]:
            violations.append("integrity_check_failed")
        if not checks["foreign_keys_ok"]:
            violations.append("foreign_key_check_failed")
        if identity is None:
            violations.append("database_identity_missing")
        else:
            checks["identity_trusted"] = identity.trust_state == "trusted"
            if not checks["identity_trusted"]:
                violations.append(f"database_identity_{identity.trust_state}")
            if expected_database_uuid is not None:
                checks["database_uuid_matches"] = identity.database_uuid == expected_database_uuid
                if not checks["database_uuid_matches"]:
                    violations.append("database_uuid_mismatch")
            if expected_schema_identity is not None:
                checks["schema_identity_matches"] = identity.schema_identity == expected_schema_identity
                if not checks["schema_identity_matches"]:
                    violations.append("schema_identity_mismatch")
            if min_schema_version is not None:
                checks["schema_version_compatible"] = identity.schema_version_number >= min_schema_version
                if not checks["schema_version_compatible"]:
                    violations.append("schema_version_too_old")
        if record_count <= 0:
            violations.append("database_has_no_records")

        active = not violations
        return MemorySourceTruthResult(
            active=active,
            state="active_trusted" if active else "rejected",
            database_path=str(db_path),
            active_root=str(self.active_root),
            trusted_memory_root=str(self.trusted_memory_root),
            database_identity=identity.to_dict() if identity else None,
            integrity_check=integrity_rows,
            foreign_key_check=foreign_rows,
            record_count=record_count,
            checks=checks,
            violations=sorted(set(violations)),
        )
