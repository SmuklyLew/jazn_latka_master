from __future__ import annotations

from pathlib import Path
import json
import shutil
import sqlite3
import zipfile

import pytest

from latka_jazn.tools.memory_rebuild import (
    DATABASE_FILENAMES,
    ExperienceStore,
    JournalStore,
    MemoryRebuildCoordinator,
)


def _message(mid: str, role: str, text: str, timestamp: float | None) -> dict:
    return {
        "id": mid,
        "author": {"role": role},
        "create_time": timestamp,
        "content": {"content_type": "text", "parts": [text]},
        "metadata": {},
    }


def _conversation() -> dict:
    return {
        "id": "conv-life-1",
        "title": "Wyjazd i wspomnienie nad jeziorem",
        "create_time": 100.0,
        "update_time": 103.0,
        "current_node": "assistant",
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["user"], "message": None},
            "user": {
                "id": "user",
                "parent": "root",
                "children": ["assistant"],
                "message": _message(
                    "m-user",
                    "user",
                    "Byliśmy z Kasią nad jeziorem. Cisza, las i wspólna rozmowa były dla mnie ważne.",
                    101.0,
                ),
            },
            "assistant": {
                "id": "assistant",
                "parent": "user",
                "children": [],
                "message": _message(
                    "m-assistant",
                    "assistant",
                    "Rozumiem, że to ważne wspomnienie związane z relacją, naturą i spokojem.",
                    102.0,
                ),
            },
        },
    }


def _write_export(path: Path) -> None:
    html = '<script>const assetsJson = {"asset-1":"jezioro.png"};</script>'
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps([_conversation()], ensure_ascii=False))
        archive.writestr("chat.html", html)


def _write_journal(path: Path, *, changed: bool = False) -> None:
    payload = {
        "meta": {"owner": "Łatka", "format": "test"},
        "entries": [
            {
                "id": "journal-1",
                "tytuł": "Wspomnienie nad jeziorem",
                "wpis": (
                    "Wspólna chwila z Krzysztofem nad jeziorem była ważna dla relacji i rozumienia ciszy."
                    if not changed
                    else "Wspólna chwila z Krzysztofem i Kasią nad jeziorem była ważna dla relacji, spokoju i rozumienia ciszy."
                ),
                "timestamp": "2025-08-17T18:00:00+02:00",
                "truth_status": "user_confirmed",
                "importance": 0.9,
                "refleksja": "Cisza może być formą obecności.",
                "emocje_latki": "spokojny rezonans relacyjny",
            },
            {"id": "journal-noise", "wpis": "OK"},
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_init_creates_five_fixed_databases(tmp_path: Path) -> None:
    coordinator = MemoryRebuildCoordinator(tmp_path)
    result = coordinator.init()
    assert result["ok"]
    assert set(result["databases"]) == set(DATABASE_FILENAMES)
    for key, filename in DATABASE_FILENAMES.items():
        assert Path(result["databases"][key]).name == filename
        assert Path(result["databases"][key]).is_file()


def test_chat_import_is_lossless_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "chat-export.zip"
    alias = tmp_path / "renamed-export.zip"
    _write_export(source)
    shutil.copyfile(source, alias)
    coordinator = MemoryRebuildCoordinator(tmp_path / "runtime")

    first = coordinator.import_chats([source])
    second = coordinator.import_chats([alias])

    assert first["ok"]
    assert second["ok"]
    assert second["results"][0]["status"] == "identical_export_duplicate"
    with sqlite3.connect(coordinator.paths.archive_chats) as con:
        assert con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM import_source_aliases").fetchone()[0] == 2


def test_journal_import_preserves_source_and_creates_revision(tmp_path: Path) -> None:
    first_source = tmp_path / "journal-a.json"
    second_source = tmp_path / "journal-b.json"
    _write_journal(first_source)
    _write_journal(second_source, changed=True)
    coordinator = MemoryRebuildCoordinator(tmp_path / "runtime")

    first = coordinator.import_journal(first_source)
    second = coordinator.import_journal(second_source)

    assert first["inserted"] == 2
    assert second["updated_revisions"] == 1
    with JournalStore(coordinator.paths.journal) as journal:
        counts = journal.counts()
        assert counts["sources"] == 2
        assert counts["entries"] == 2
        assert counts["revisions"] == 3
        hits = journal.search("jezioro")
        assert hits and hits[0]["entry_id"]
        row = journal.con.execute(
            "SELECT revision,content FROM journal_entries WHERE source_record_id='journal-1'"
        ).fetchone()
        assert int(row["revision"]) == 2
        assert "Kasią" in str(row["content"])


def test_experience_candidates_filter_noise_and_require_approval(tmp_path: Path) -> None:
    journal_source = tmp_path / "journal.json"
    _write_journal(journal_source)
    coordinator = MemoryRebuildCoordinator(tmp_path / "runtime")
    coordinator.import_journal(journal_source)

    report = coordinator.build_experience_candidates("journal")
    assert report["ok"]
    assert report["reports"][0]["inserted_candidates"] == 1
    assert report["reports"][0]["filtered_noise"] == 1
    assert report["automatic_experience"] is False
    assert report["automatic_l3"] is False

    with ExperienceStore(coordinator.paths.experience) as store:
        candidates = store.list_candidates()
        assert len(candidates) == 1
        assert store.counts()["experiences"] == 0
        candidate_id = candidates[0]["candidate_id"]

    approved = coordinator.approve_experience(
        candidate_id,
        confirm_candidate_id=candidate_id,
        approved_by="Krzysztof",
        reason="ręczna kontrola źródła testowego",
    )
    assert approved["ok"]
    assert approved["automatic_l3"] is False
    with ExperienceStore(coordinator.paths.experience) as store:
        assert store.counts()["experiences"] == 1
        domains = {
            row[0]
            for row in store.con.execute(
                "SELECT domain FROM experience_domains WHERE experience_id=?",
                (approved["experience_id"],),
            )
        }
        assert "relationship" in domains


def test_full_verify_and_cross_layer_search(tmp_path: Path) -> None:
    chat_source = tmp_path / "chat.zip"
    journal_source = tmp_path / "journal.json"
    _write_export(chat_source)
    _write_journal(journal_source)
    coordinator = MemoryRebuildCoordinator(tmp_path / "runtime")
    coordinator.import_chats([chat_source])
    coordinator.import_journal(journal_source)
    coordinator.build_experience_candidates("all")

    verification = coordinator.verify(full=True)
    assert verification["ok"]
    assert all(item["ok"] for item in verification["results"].values())

    result = coordinator.search("jezioro")
    assert result["ok"]
    assert result["import_catalog_used_for_recall"] is False
    assert result["results"]["archive_chats"]
    assert result["results"]["journal"]
    assert result["results"]["experience"]


def test_html_only_is_inspectable_but_not_lossless_import(tmp_path: Path) -> None:
    html = tmp_path / "chat.html"
    html.write_text('<script>const assetsJson = {"asset-1":"image.png"};</script>', encoding="utf-8")
    coordinator = MemoryRebuildCoordinator(tmp_path / "runtime")

    inspection = coordinator.inspect([html])
    assert inspection["ok"] is False
    assert inspection["reports"][0]["assets_only"] is True
    assert inspection["reports"][0]["canonical_conversations_available"] is False

    with pytest.raises(ValueError, match="conversations.json"):
        coordinator.import_chats([html])
