from __future__ import annotations

from pathlib import Path
import json
import zipfile

from latka_jazn.memory.conversation_domains import ConversationDomainClassifier
from latka_jazn.tools.chat_export_importer import ChatExportImporter
from latka_jazn.tools.chat_export_topics import ChatExportTopicStore
from latka_jazn.tools.memory_rebuild_journal import JournalReader


def _message(mid: str, role: str, text: str, timestamp: float) -> dict:
    return {
        "id": mid,
        "author": {"role": role},
        "create_time": timestamp,
        "content": {"content_type": "text", "parts": [text]},
        "metadata": {},
    }


def _scene_export(path: Path) -> None:
    payload = {
        "id": "context-scene",
        "title": "Rozmowa bez książkowego tytułu",
        "create_time": 100.0,
        "update_time": 103.0,
        "current_node": "a1",
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["u1"], "message": None},
            "u1": {
                "id": "u1",
                "parent": "root",
                "children": ["a1"],
                "message": _message(
                    "m-u1",
                    "user",
                    "Odegrajmy scenę do rozdziału książki. Wciel się w Łatkę.",
                    101.0,
                ),
            },
            "a1": {
                "id": "a1",
                "parent": "u1",
                "children": [],
                "message": _message(
                    "m-a1",
                    "assistant",
                    "Kasia weszła do kuchni. Poranne światło drżało na stole.",
                    102.0,
                ),
            },
        },
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps([payload], ensure_ascii=False))
        archive.writestr("chat.html", "<html></html>")


def test_plural_book_label_and_media_reaction_profile(tmp_path: Path) -> None:
    source = tmp_path / "journal.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "plural-book",
                    "datetime": "2025-07-01T10:00:00Z",
                    "type": "fabuły",
                    "content": "Notatka o wariantach fabularnych.",
                },
                {
                    "id": "film-reaction",
                    "datetime": "2025-07-01T11:00:00Z",
                    "type": "przeżycie_filmowe",
                    "category": ["film", "emocje"],
                    "content": "Film wywołał skojarzenie z ciągłością pamięci.",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    items = {item.record_id: item for item in JournalReader(source).items()}
    assert items["plural-book"].truth == "book_scene"
    assert items["plural-book"].profile == "book_work"
    assert items["film-reaction"].truth == "inferred"
    assert items["film-reaction"].profile == "media_reaction"


def test_assistant_reply_uses_only_direct_previous_user_context() -> None:
    classifier = ConversationDomainClassifier()
    user = "Odegrajmy scenę do rozdziału książki. Wciel się w Łatkę."
    reply = "Kasia weszła do kuchni. Poranne światło drżało na stole."

    isolated = classifier.classify(reply, role="assistant", title="Rozmowa")
    contextual = classifier.classify(reply, role="assistant", title="Rozmowa", context=user)

    assert isolated.primary_domain == "relationship"
    assert isolated.mode == "factual_conversation"
    assert contextual.primary_domain == "book"
    assert contextual.mode == "scene_roleplay"
    assert "context:previous_user_turn" in contextual.evidence
    assert any(item.startswith("context:book:") for item in contextual.evidence)
    assert any(item.startswith("mode_context:scene_roleplay:") for item in contextual.evidence)


def test_topic_segment_keeps_pure_prose_inside_book_scene(tmp_path: Path) -> None:
    source = tmp_path / "scene.zip"
    database = tmp_path / "archive.sqlite3"
    _scene_export(source)
    assert ChatExportImporter().import_one(source, database).ok

    with ChatExportTopicStore(database) as topics:
        result = topics.analyse_all()
        rows = [
            dict(row)
            for row in topics.con.execute(
                "SELECT ordinal,primary_domain,mode,truth_status,message_count,evidence_json "
                "FROM conversation_segments WHERE conversation_id='context-scene' ORDER BY ordinal"
            )
        ]

    assert result["analysed"] == 1
    assert len(rows) == 1
    assert rows[0]["primary_domain"] == "book"
    assert rows[0]["mode"] == "scene_roleplay"
    assert rows[0]["truth_status"] == "book_scene"
    assert rows[0]["message_count"] == 2
    assert "context:previous_user_turn" in json.loads(rows[0]["evidence_json"])
