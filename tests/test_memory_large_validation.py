from __future__ import annotations

from pathlib import Path
import hashlib
import sqlite3

from latka_jazn.config import JaznConfig
from latka_jazn.memory.memory_tier_store import MemoryTierStore
from latka_jazn.memory.normalization_sidecar import MemoryNormalizationSidecar
from latka_jazn.tools.memory_validation import (
    MemoryValidationTarget,
    discover_memory_validation_targets,
    validate_large_memory,
    validate_sqlite_target,
)


def _source(path: Path, rows: int = 2000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE messages(
              message_id TEXT, conversation_id TEXT, conversation_title TEXT, role TEXT,
              timestamp TEXT, content_text TEXT, content_hash TEXT, first_source_file TEXT,
              first_source_sha256 TEXT, source_refs_json TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta(key,value) VALUES('created_by','large-validation-test');
            """
        )
        con.executemany(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"m-{index}", "c-1", "Large validation", "user" if index % 2 == 0 else "assistant",
                    f"2026-07-22T07:{index % 60:02d}:00+00:00", f"memory row {index}",
                    hashlib.sha256(f"memory row {index}".encode()).hexdigest(),
                    "source.json", "a" * 64, "[]", "2026-07-22T07:00:00+00:00",
                    "2026-07-22T07:00:00+00:00",
                )
                for index in range(rows)
            ],
        )


def _prepared_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    cfg = JaznConfig(root=root)
    _source(cfg.memory_db_path_readonly)
    sidecar = MemoryNormalizationSidecar(
        root,
        source_db_path=cfg.memory_db_path_readonly,
        sidecar_db_path=cfg.normalization_sidecar_db_path,
        runtime_version=cfg.version,
    )
    report = sidecar.prepare(deep_verify=True)
    assert report.status == "ready", report.to_dict()
    with MemoryTierStore(cfg.memory_tier_db_path):
        pass
    return root


def test_quick_and_full_validation_pass_for_prepared_memory(tmp_path: Path) -> None:
    root = _prepared_root(tmp_path)

    quick = validate_large_memory(root, full=False, table_counts=True)
    full = validate_large_memory(root, full=True, table_counts=False)

    assert quick["ok"] is True, quick
    assert quick["validation_mode"] == "quick"
    assert quick["summary"]["existing_database_count"] >= 3
    assert quick["summary"]["wake_state_ready"] is True
    assert quick["summary"]["memory_tiers_ready"] is True
    assert any(item["table_counts"].get("messages") == 2000 for item in quick["databases"])
    assert full["ok"] is True, full
    assert full["sqlite_pragma"] == "integrity_check"
    assert all(item["integrity_result"] == ["ok"] for item in full["databases"] if item["exists"])


def test_validation_detects_foreign_key_corruption(tmp_path: Path) -> None:
    database = tmp_path / "broken.sqlite3"
    with sqlite3.connect(database) as con:
        con.executescript(
            """
            PRAGMA foreign_keys=OFF;
            CREATE TABLE parent(id INTEGER PRIMARY KEY);
            CREATE TABLE child(parent_id INTEGER REFERENCES parent(id));
            INSERT INTO child(parent_id) VALUES(999);
            """
        )
    result = validate_sqlite_target(
        MemoryValidationTarget("broken", str(database), "pytest", True),
        full=True,
        max_errors=10,
    )

    assert result.ok is False
    assert result.integrity_result == ["ok"]
    assert result.foreign_key_error_count == 1
    assert result.foreign_key_errors


def test_validation_reports_incomplete_wal_sidecars(tmp_path: Path) -> None:
    database = tmp_path / "wal.sqlite3"
    with sqlite3.connect(database) as con:
        con.execute("CREATE TABLE item(id INTEGER PRIMARY KEY)")
    database.with_name(database.name + "-wal").write_bytes(b"incomplete")

    result = validate_sqlite_target(
        MemoryValidationTarget("wal", str(database), "pytest", True),
    )

    assert result.ok is False
    assert result.error_type == "SidecarStateError"
    assert result.error == "incomplete_sqlite_wal_sidecars"


def test_recursive_discovery_stays_under_runtime_root_and_deduplicates(tmp_path: Path) -> None:
    root = _prepared_root(tmp_path)
    extra = root / "memory/sqlite/extra/extra.sqlite3"
    extra.parent.mkdir(parents=True)
    with sqlite3.connect(extra) as con:
        con.execute("CREATE TABLE item(id INTEGER PRIMARY KEY)")

    targets = discover_memory_validation_targets(root, include_all_sqlite=True)
    paths = [item.path for item in targets]

    assert str(extra.resolve()) in paths
    assert len(paths) == len(set(paths))
    assert all(Path(path).is_relative_to(root.resolve()) for path in paths)


def test_report_output_is_written_under_runtime_root(tmp_path: Path) -> None:
    root = _prepared_root(tmp_path)
    report = validate_large_memory(
        root,
        output=Path("workspace_runtime/memory_validation/latest.json"),
    )

    assert report["ok"] is True
    destination = Path(report["report_path"])
    assert destination.is_file()
    assert destination.is_relative_to(root.resolve())
