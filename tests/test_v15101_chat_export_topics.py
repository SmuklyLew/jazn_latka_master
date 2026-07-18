from __future__ import annotations

from pathlib import Path
import json
import zipfile

from latka_jazn.tools.chat_export_importer import ChatExportImporter
from latka_jazn.tools.chat_export_topics import ChatExportTopicStore


def _message(mid: str, role: str, text: str, timestamp: float) -> dict:
    return {
        "id": mid,
        "author": {"role": role},
        "create_time": timestamp,
        "content": {"content_type": "text", "parts": [text]},
        "metadata": {},
    }


def _multi_topic_conversation() -> dict:
    messages = [
        ("u1", "user", "Napraw moduł Python i dodaj test pytest dla bazy SQLite."),
        ("a1", "assistant", "Przygotuję patch kodu i test regresyjny."),
        ("u2", "user", "Teraz odegrajmy scenę do rozdziału książki. Wciel się w Łatkę; to szkic."),
        ("a2", "assistant", "W tej próbnej scenie odpowiadam jako postać Łatki."),
        ("u3", "user", "Przeanalizuj tekst piosenki, melodię i refren; jakie budzą skojarzenia?"),
        ("a3", "assistant", "Analiza utworu dotyczy rytmu, znaczenia i skojarzeń."),
    ]
    mapping: dict[str, dict] = {
        "root": {"id": "root", "parent": None, "children": ["u1"], "message": None}
    }
    for index, (node_id, role, text) in enumerate(messages):
        parent = "root" if index == 0 else messages[index - 1][0]
        children = [messages[index + 1][0]] if index + 1 < len(messages) else []
        mapping[node_id] = {
            "id": node_id,
            "parent": parent,
            "children": children,
            "message": _message(f"m-{node_id}", role, text, 100.0 + index),
        }
    return {
        "id": "conv-topics",
        "title": "Rozmowa wielotematyczna",
        "create_time": 99.0,
        "update_time": 106.0,
        "current_node": "a3",
        "mapping": mapping,
    }


def _write_export(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "conversations.json",
            json.dumps([_multi_topic_conversation()], ensure_ascii=False),
        )
        archive.writestr("chat.html", "<script>const assetsJson = {};</script>")


def _prepared_database(tmp_path: Path) -> Path:
    source = tmp_path / "chat.zip"
    database = tmp_path / "archive.sqlite3"
    _write_export(source)
    result = ChatExportImporter().import_one(source, database)
    assert result.ok
    return database


def test_topics_segment_domain_changes_and_preserve_truth_status(tmp_path: Path) -> None:
    database = _prepared_database(tmp_path)
    with ChatExportTopicStore(database) as topics:
        report = topics.analyse_all()
        rows = [dict(row) for row in topics.con.execute(
            "SELECT primary_domain,mode,truth_status FROM conversation_segments "
            "WHERE conversation_id='conv-topics' ORDER BY ordinal"
        )]
    assert report["analysed"] == 1
    assert any(row["primary_domain"] == "development" for row in rows)
    assert any(
        row["primary_domain"] == "book"
        and row["mode"] == "scene_roleplay"
        and row["truth_status"] == "book_scene"
        for row in rows
    )
    assert any(row["primary_domain"] == "music" for row in rows)


def test_review_queue_is_manual_idempotent_and_contains_only_source_refs(tmp_path: Path) -> None:
    database = _prepared_database(tmp_path)
    with ChatExportTopicStore(database) as topics:
        topics.analyse_all()
        first = topics.queue_domains(["book"], reason="manual user review")
        second = topics.queue_domains(["book"], reason="manual user review")
        queue = topics.review_queue()
        columns = {
            str(row[1])
            for row in topics.con.execute("PRAGMA table_info(memory_review_queue)")
        }
    assert first >= 1
    assert second == 0
    assert queue and all(item["status"] == "pending_review" for item in queue)
    assert "text" not in columns
    refs = json.loads(queue[0]["source_refs_json"])
    assert refs["conversation_id"] == "conv-topics"
    assert refs["truth_status"] == "book_scene"
    assert queue[0]["promotion_target"] is None


def test_forced_reanalysis_invalidates_pending_candidates_without_promoting(tmp_path: Path) -> None:
    database = _prepared_database(tmp_path)
    with ChatExportTopicStore(database) as topics:
        topics.analyse_all()
        assert topics.queue_domains(["book"], reason="manual user review") >= 1
        result = topics.analyse_conversation("conv-topics", force=True)
        stale = topics.review_queue(status="stale_reanalysis")
        pending = topics.review_queue(status="pending_review")
    assert result is not None
    assert stale
    assert not pending
    assert all(item["promotion_target"] is None for item in stale)
