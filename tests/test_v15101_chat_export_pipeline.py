from __future__ import annotations

from pathlib import Path
import json
import shutil
import zipfile

import pytest

from latka_jazn.memory.conversation_domains import ConversationDomainClassifier
from latka_jazn.tools.chat_export_dedupe import ActiveConversationState, plan_conversation, stable_node_hash
from latka_jazn.tools.chat_export_importer import ChatExportImporter
from latka_jazn.tools.chat_export_reader import ChatExportReader, build_conversation_graph
from latka_jazn.tools.chat_export_store import ChatExportArchiveStore


def message(mid: str, role: str, text: str, *, timestamp: float | None, metadata: dict | None = None) -> dict:
    return {
        "id": mid,
        "author": {"role": role},
        "create_time": timestamp,
        "content": {"content_type": "text", "parts": [text]},
        "metadata": metadata or {},
    }


def conversation(*, include_second_branch: bool = True, volatile: str = "a") -> dict:
    children = ["assistant-a", "assistant-b"] if include_second_branch else ["assistant-a"]
    mapping = {
        "root": {"id": "root", "parent": None, "children": ["user"], "message": None},
        "user": {
            "id": "user", "parent": "root", "children": children,
            "message": message("m-user", "user", "Napisz scenę do książki.", timestamp=100.0),
        },
        "assistant-a": {
            "id": "assistant-a", "parent": "user", "children": [],
            "message": message(
                "m-a", "assistant", "Pierwsza wersja.", timestamp=101.0,
                metadata={"lpe_keep_patch_ijhw": volatile},
            ),
        },
    }
    if include_second_branch:
        mapping["assistant-b"] = {
            "id": "assistant-b", "parent": "user", "children": [],
            "message": message("m-b", "assistant", "Druga wersja.", timestamp=None),
        }
    return {
        "id": "conv-1",
        "title": "Książka — scena próbna",
        "create_time": 99.0,
        "update_time": 102.0,
        "current_node": "assistant-b" if include_second_branch else "assistant-a",
        "mapping": mapping,
    }


def write_export(path: Path, payload: list[dict]) -> None:
    html = '<script>const assetsJson = {"asset-1":"obraz.png"};</script>'
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps(payload, ensure_ascii=False))
        archive.writestr("chat.html", html)


def test_reader_preserves_tree_branches_and_structural_timestamp(tmp_path: Path) -> None:
    source = tmp_path / "chat.zip"
    write_export(source, [conversation()])
    with ChatExportReader(source) as reader:
        report = reader.inspect()
        graph = next(reader.iter_graphs())
    assert report.ok
    assert report.conversation_count == 1
    assert report.branch_point_count == 1
    assert graph.current_path == ("root", "user", "assistant-b")
    assert graph.node_index()["assistant-b"].timestamp_status == "structural_only"
    assert graph.node_index()["assistant-a"].on_current_path is False


def test_volatile_metadata_does_not_create_changed_node() -> None:
    old = build_conversation_graph(conversation(volatile="old"))
    new = build_conversation_graph(conversation(volatile="new"))
    assert stable_node_hash(old.node_index()["assistant-a"]) == stable_node_hash(new.node_index()["assistant-a"])
    plan = plan_conversation(old, ActiveConversationState.from_graph(new))
    assert plan.relation == "identical"
    assert plan.changed_node_ids == ()


def test_newer_tree_then_older_subset_and_exact_file_alias(tmp_path: Path) -> None:
    newer = tmp_path / "newer.zip"
    older = tmp_path / "older.zip"
    alias = tmp_path / "renamed-newer.zip"
    write_export(newer, [conversation(include_second_branch=True)])
    write_export(older, [conversation(include_second_branch=False)])
    shutil.copyfile(newer, alias)
    database = tmp_path / "archive.sqlite3"
    importer = ChatExportImporter()

    first = importer.import_one(newer, database)
    second = importer.import_one(older, database)
    duplicate = importer.import_one(alias, database)

    assert first.ok and first.conversation_counters == {"new": 1}
    assert second.ok and second.conversation_counters == {"older_subset": 1}
    assert second.inserted_nodes == 0
    assert duplicate.status == "identical_export_duplicate"
    with ChatExportArchiveStore(database) as store:
        assert store.counts()["conversations"] == 1
        assert store.counts()["nodes"] == 4
        assert store.counts()["import_sources"] == 2
        assert store.counts()["import_source_aliases"] == 3
        assert store.validate(full=True)["ok"]


def test_contentless_fts_returns_locator_and_payload_is_reconstructable(tmp_path: Path) -> None:
    source = tmp_path / "chat.zip"
    write_export(source, [conversation()])
    database = tmp_path / "archive.sqlite3"
    ChatExportImporter().import_one(source, database)
    with ChatExportArchiveStore(database) as store:
        hits = store.search("książki")
        payload = store.conversation_payload("conv-1")
    assert hits and hits[0]["conversation_id"] == "conv-1"
    assert "text" not in hits[0]
    assert payload and payload["id"] == "conv-1"


def test_export_transaction_rolls_back_completely(tmp_path: Path) -> None:
    source = tmp_path / "chat.zip"
    write_export(source, [conversation()])
    database = tmp_path / "archive.sqlite3"
    with ChatExportReader(source) as reader, ChatExportArchiveStore(database) as store:
        with pytest.raises(RuntimeError, match="forced"):
            with store.transaction():
                store.begin_import(reader.info)
                raise RuntimeError("forced")
        assert store.counts()["import_sources"] == 0
        assert store.validate(full=True)["ok"]


def test_book_roleplay_is_not_general_imagination() -> None:
    report = ConversationDomainClassifier().classify(
        "Odegrajmy scenę do rozdziału książki. Wciel się w Łatkę, ale to tylko szkic.",
        title="Witaj w podróży Jaźni",
    )
    assert report.primary_domain == "book"
    assert report.mode == "scene_roleplay"
    assert "nie zmienia" in report.truth_boundary.lower()


def test_corrupt_zip_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "bad.zip"
    source.write_bytes(b"not-a-zip")
    with pytest.raises(zipfile.BadZipFile):
        ChatExportReader(source)
