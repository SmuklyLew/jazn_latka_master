from __future__ import annotations

from pathlib import Path
import sqlite3

from latka_jazn.memory.memory_tier_status import inspect_memory_tier_store
from latka_jazn.memory.memory_tier_store import MemoryTierStore


def test_status_does_not_modify_valid_database_or_create_sidecars(tmp_path: Path) -> None:
    database = tmp_path / "tiers.sqlite3"
    with MemoryTierStore(database) as store:
        assert store.validate(full=False)["ok"] is True
    before_mtime = database.stat().st_mtime_ns
    before_files = sorted(path.name for path in tmp_path.iterdir())

    status = inspect_memory_tier_store(database, full=False)

    assert status.ready is True
    assert status.read_only is True
    assert status.integrity_check == "ok"
    assert status.foreign_key_error_count == 0
    assert status.automatic_commit_violation_count == 0
    assert status.missing_tables == ()
    assert database.stat().st_mtime_ns == before_mtime
    assert sorted(path.name for path in tmp_path.iterdir()) == before_files


def test_status_reports_missing_schema_without_repairing_it(tmp_path: Path) -> None:
    database = tmp_path / "incomplete.sqlite3"
    con = sqlite3.connect(database)
    try:
        con.execute("CREATE TABLE only_one(id INTEGER PRIMARY KEY)")
        con.commit()
    finally:
        con.close()
    before_size = database.stat().st_size
    before_mtime = database.stat().st_mtime_ns

    status = inspect_memory_tier_store(database)

    assert status.ready is False
    assert status.error_type == "SchemaError"
    assert "memory_records" in status.missing_tables
    assert database.stat().st_size == before_size
    assert database.stat().st_mtime_ns == before_mtime
