from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sqlite3

from latka_jazn.memory.normalization_sidecar import MemoryNormalizationSidecar


def _source(path: Path, count: int = 1, *, bad_fk: bool = False) -> None:
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE messages(
              message_id TEXT, conversation_id TEXT, conversation_title TEXT, role TEXT,
              timestamp TEXT, content_text TEXT, content_hash TEXT, first_source_file TEXT,
              first_source_sha256 TEXT, source_refs_json TEXT, created_at TEXT, updated_at TEXT
            );
            """
        )
        con.executemany(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"m{i}", "c1", "Test", "assistant" if i % 2 else "user",
                    f"2026-07-16T10:{i % 60:02d}:00+00:00", f"memory item {i}",
                    hashlib.sha256(f"memory item {i}".encode()).hexdigest(), "source.json", "a" * 64,
                    "[]", "2026-07-16T10:00:00+00:00", "2026-07-16T10:00:00+00:00",
                )
                for i in range(count)
            ],
        )
        if bad_fk:
            con.executescript(
                """
                PRAGMA foreign_keys=OFF;
                CREATE TABLE parent(id INTEGER PRIMARY KEY);
                CREATE TABLE child(parent_id INTEGER REFERENCES parent(id));
                INSERT INTO child(parent_id) VALUES(99);
                """
            )
        con.commit()


def _sidecar(tmp_path: Path, *, count: int = 1, bad_fk: bool = False) -> MemoryNormalizationSidecar:
    source = tmp_path / "source.sqlite3"
    _source(source, count, bad_fk=bad_fk)
    return MemoryNormalizationSidecar(
        tmp_path,
        source_db_path=source,
        sidecar_db_path=tmp_path / "audit.sqlite3",
        runtime_version="v15.0.3.2",
    )


def test_prepare_builds_verified_snapshot_and_is_idempotent(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path)
    first = sidecar.prepare().to_dict()
    assert first["status"] == "ready"
    assert first["normalization_performed"] is True
    assert first["snapshot_built"] is True
    snapshot_id = first["wake_state"]["active_snapshot"]["snapshot_id"]

    second = sidecar.prepare().to_dict()
    assert second["status"] == "ready"
    assert second["normalization_performed"] is False
    assert second["snapshot_built"] is False
    assert second["wake_state"]["active_snapshot"]["snapshot_id"] == snapshot_id


def test_source_change_requires_new_run(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path)
    first = sidecar.prepare().to_dict()
    with sqlite3.connect(sidecar.source_db_path) as con:
        con.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("m2", "c1", "Test", "user", "2026-07-16T12:00:00+00:00", "changed", "h", None, None, "[]", "x", "x"),
        )
        con.commit()
    assert sidecar.status().status == "source_changed"
    second = sidecar.prepare().to_dict()
    assert second["status"] == "ready"
    assert second["normalization_performed"] is True
    assert second["wake_state"]["active_snapshot"]["source_run_id"] != first["wake_state"]["active_snapshot"]["source_run_id"]


def test_snapshot_hash_mismatch_is_reported(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path)
    assert sidecar.prepare().status == "ready"
    with sqlite3.connect(sidecar.sidecar_db_path) as con:
        con.execute("UPDATE wake_state_snapshots SET snapshot_json=snapshot_json || ' '")
        con.commit()
    assert sidecar.wake_state_status(deep_verify=True).status == "snapshot_hash_mismatch"


def test_legacy_multiple_active_rows_are_migrated_to_one(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path)
    assert sidecar.prepare().status == "ready"
    with sqlite3.connect(sidecar.sidecar_db_path) as con:
        con.execute("DROP INDEX uq_wake_state_single_active")
        row = con.execute("SELECT * FROM wake_state_snapshots WHERE active=1").fetchone()
        con.execute(
            "INSERT INTO wake_state_snapshots VALUES(?,?,?,?,?,?,?,?,?)",
            ("legacy-second", row[1], "2099-01-01T00:00:00+00:00", 1, row[4], row[5], row[6], row[7], row[8]),
        )
        con.commit()
    assert sidecar.wake_state_status().status == "validation_failed"
    sidecar.ensure_schema()
    with sqlite3.connect(sidecar.sidecar_db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM wake_state_snapshots WHERE active=1").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='uq_wake_state_single_active'").fetchone()[0] == 1


def test_invalid_source_run_id_is_reported(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path)
    assert sidecar.prepare().status == "ready"
    with sqlite3.connect(sidecar.sidecar_db_path) as con:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("UPDATE wake_state_snapshots SET source_run_id='missing-run' WHERE active=1")
        con.commit()
    assert sidecar.wake_state_status().status == "source_run_invalid"


def test_source_foreign_key_error_blocks_all_writes(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path, bad_fk=True)
    report = sidecar.prepare()
    assert report.status == "validation_failed"
    assert not sidecar.sidecar_db_path.exists()


def test_unreadable_sqlite_reports_validation_failure(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    source.write_bytes(b"not a sqlite database")
    sidecar = MemoryNormalizationSidecar(
        tmp_path, source_db_path=source, sidecar_db_path=tmp_path / "audit.sqlite3", runtime_version="v15.0.3.2"
    )
    report = sidecar.prepare()
    assert report.status == "validation_failed"
    assert not sidecar.sidecar_db_path.exists()


def test_dry_run_does_not_create_sidecar(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path)
    report = sidecar.prepare(dry_run=True)
    assert report.dry_run is True
    assert report.normalization_performed is False
    assert not sidecar.sidecar_db_path.exists()


def test_sidecar_foreign_key_error_fails_deep_verify(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path)
    assert sidecar.prepare().status == "ready"
    with sqlite3.connect(sidecar.sidecar_db_path) as con:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("UPDATE normalized_memory_items SET speaker_actor_id='missing-actor'")
        con.commit()
    assert sidecar.wake_state_status(deep_verify=True).status == "validation_failed"


def test_large_source_produces_bounded_snapshot(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path, count=5000)
    report = sidecar.prepare()
    assert report.status == "ready"
    with sqlite3.connect(sidecar.sidecar_db_path) as con:
        raw, = con.execute("SELECT snapshot_json FROM wake_state_snapshots WHERE active=1").fetchone()
    snapshot = json.loads(raw)
    assert snapshot["source_counts"]["normalized_memory_items"] == 5000
    assert len(snapshot["recent_events"]) <= 12
    assert len(raw.encode("utf-8")) < 100_000


def test_fast_status_observes_active_wal_without_writing(tmp_path: Path) -> None:
    sidecar = _sidecar(tmp_path)
    assert sidecar.prepare().status == "ready"
    writer = sqlite3.connect(sidecar.sidecar_db_path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE IF NOT EXISTS wal_visibility_probe(value TEXT)")
        writer.execute("INSERT INTO wal_visibility_probe(value) VALUES('keeps-wal-visible')")
        writer.commit()
        assert sidecar.status().status == "ready"
        assert sidecar.wake_state_status().status == "ready"
    finally:
        writer.close()
