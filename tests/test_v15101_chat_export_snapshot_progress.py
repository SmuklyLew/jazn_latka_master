from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import zipfile

from latka_jazn.tools.chat_export_importer import ChatExportImporter
from latka_jazn.tools.chat_export_store import ChatExportArchiveStore
from latka_jazn.tools.sqlite_archive_snapshot import create_sqlite_snapshot


def _conversation() -> dict:
    root = "root"
    user = "user-node"
    assistant = "assistant-node"
    return {
        "id": "conv-progress",
        "title": "Test pamięci",
        "create_time": 100.0,
        "update_time": 102.0,
        "current_node": assistant,
        "mapping": {
            root: {"id": root, "parent": None, "children": [user], "message": None},
            user: {
                "id": user,
                "parent": root,
                "children": [assistant],
                "message": {
                    "id": "msg-user",
                    "author": {"role": "user"},
                    "create_time": 101.0,
                    "content": {"content_type": "text", "parts": ["Pamiętaj o katedrze."]},
                    "metadata": {},
                },
            },
            assistant: {
                "id": assistant,
                "parent": user,
                "children": [],
                "message": {
                    "id": "msg-assistant",
                    "author": {"role": "assistant"},
                    "create_time": 102.0,
                    "content": {"content_type": "text", "parts": ["Zachowuję kontekst rozmowy."]},
                    "metadata": {},
                },
            },
        },
    }


def _export_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps([_conversation()], ensure_ascii=False))
        archive.writestr("chat.html", "<script>var assetsJson = {};</script>")
    return path


def test_incremental_payload_compression_round_trips(tmp_path: Path) -> None:
    source = _export_zip(tmp_path / "chat.zip")
    database = tmp_path / "archive.sqlite3"
    result = ChatExportImporter().import_one(source, database)
    assert result.ok
    with ChatExportArchiveStore(database) as store:
        restored = store.conversation_payload("conv-progress")
        assert restored == _conversation()
        row = store.con.execute(
            "SELECT payload_size_uncompressed,payload_size_compressed FROM conversations"
        ).fetchone()
        assert row["payload_size_uncompressed"] > row["payload_size_compressed"] > 0


def test_import_reports_internal_progress_stages(tmp_path: Path) -> None:
    source = _export_zip(tmp_path / "chat.zip")
    events: list[dict] = []
    result = ChatExportImporter().import_one(
        source,
        tmp_path / "archive.sqlite3",
        progress_callback=events.append,
        progress_every_conversations=1,
    )
    assert result.ok
    stages = [event["stage"] for event in events]
    assert stages[0] == "source_hash_started"
    assert "source_validation_completed" in stages
    assert "transaction_started" in stages
    assert "conversations_imported" in stages
    assert "transaction_committed" in stages
    assert stages[-1] == "database_validation_completed"
    progress = next(event for event in events if event["stage"] == "conversations_imported")
    assert progress["conversations"] == 1
    assert progress["nodes"] == 3


def test_sqlite_snapshot_includes_committed_wal_and_is_valid(tmp_path: Path) -> None:
    source = tmp_path / "live.sqlite3"
    con = sqlite3.connect(source)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL REFERENCES parent(id))")
        con.execute("INSERT INTO parent(value) VALUES('committed-in-wal')")
        parent_id = con.execute("SELECT id FROM parent").fetchone()[0]
        con.execute("INSERT INTO child(parent_id) VALUES(?)", (parent_id,))
        con.commit()

        destination = tmp_path / "backups" / "snapshot.sqlite3"
        steps: list[tuple[int, int]] = []
        report = create_sqlite_snapshot(source, destination, progress=lambda done, total: steps.append((done, total)))
        assert report.ok
        assert report.snapshot_sha256
        assert destination.is_file()
        assert steps

        snap = sqlite3.connect(destination)
        try:
            assert snap.execute("SELECT value FROM parent").fetchone()[0] == "committed-in-wal"
            assert snap.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert list(snap.execute("PRAGMA foreign_key_check")) == []
        finally:
            snap.close()
    finally:
        con.close()


def test_snapshot_failure_does_not_replace_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "not-a-database.sqlite3"
    source.write_bytes(b"not sqlite")
    destination = tmp_path / "snapshot.sqlite3"
    destination.write_bytes(b"existing-safe-copy")
    try:
        create_sqlite_snapshot(source, destination)
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("invalid source must fail")
    assert destination.read_bytes() == b"existing-safe-copy"


def test_performance_overrides_preserve_fingerprints_and_payload(tmp_path: Path) -> None:
    from latka_jazn.tools import chat_export_reader as reader_module
    from latka_jazn.tools import chat_export_store as store_module
    from latka_jazn.tools.chat_export_performance import install_performance_overrides

    source = _conversation()
    original_graph = reader_module.build_conversation_graph(source)
    original_raw = original_graph.raw_tree_sha256
    original_semantic = original_graph.semantic_tree_sha256
    install_performance_overrides()
    optimized_graph = reader_module.build_conversation_graph(source)
    assert optimized_graph.raw_tree_sha256 == original_raw
    assert optimized_graph.semantic_tree_sha256 == original_semantic
    blob, raw_size = store_module._compressed_payload(optimized_graph)
    assert json.loads(__import__("zlib").decompress(blob).decode("utf-8")) == source
    assert raw_size > len(blob)
