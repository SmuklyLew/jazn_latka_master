from __future__ import annotations

from pathlib import Path
import json
import zipfile

from latka_jazn.tools.chat_export_importer import ChatExportImporter
from latka_jazn.tools.chat_export_store import ChatExportArchiveStore


def _write_export(path: Path) -> Path:
    conversation = {
        "id": "conv-plan",
        "title": "Plan bez zapisu",
        "create_time": 1.0,
        "update_time": 2.0,
        "current_node": "user",
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["user"], "message": None},
            "user": {
                "id": "user",
                "parent": "root",
                "children": [],
                "message": {
                    "id": "msg-user",
                    "author": {"role": "user"},
                    "create_time": 2.0,
                    "content": {"content_type": "text", "parts": ["Sprawdź plan importu."]},
                    "metadata": {},
                },
            },
        },
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps([conversation], ensure_ascii=False))
        archive.writestr("chat.html", "<script>var assetsJson = {};</script>")
    return path


def test_plan_is_read_only_and_detects_known_export_sha(tmp_path: Path) -> None:
    source = _write_export(tmp_path / "chat.zip")
    database = tmp_path / "archive.sqlite3"
    importer = ChatExportImporter()

    first = importer.plan(source, database)
    assert first.export_relation == "new_export"
    assert first.counters() == {"new": 1}
    with ChatExportArchiveStore(database) as store:
        assert store.counts()["conversations"] == 0

    imported = importer.import_one(source, database)
    assert imported.ok
    duplicate = importer.plan(source, database)
    assert duplicate.export_relation == "identical_export_duplicate"
    assert duplicate.duplicate_import_id == imported.import_id
    with ChatExportArchiveStore(database) as store:
        assert store.counts()["conversations"] == 1
