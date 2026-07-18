from __future__ import annotations

from pathlib import Path
import json

from latka_jazn.memory.conversation_domains import ConversationDomainClassifier
from latka_jazn.tools.memory_rebuild import MemoryRebuildCoordinator
from latka_jazn.tools.memory_rebuild_journal import JournalReader, infer_domains
from latka_jazn.tools.memory_rebuild_journal_reader import classify_journal_raw
from latka_jazn.tools.memory_rebuild_journal_store import infer_domains_report


def _write_edge_journal(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "id": "imagined-story",
                    "datetime": "2025-07-01T10:00:00Z",
                    "type": "historia_wyobrazona",
                    "content": "Wyobrażona scena do książki, nie wydarzenie fizyczne.",
                },
                {
                    "id": "system-note",
                    "datetime": "2025-07-01T11:00:00Z",
                    "type": "notatka systemowa",
                    "content": "Notatka o konfiguracji i regułach działania runtime.",
                },
                {
                    "id": "sensory-anchor",
                    "datetime": "2025-07-01T12:00:00Z",
                    "type": "kotwica_sensoryczna",
                    "category": ["codzienność", "obecność"],
                    "content": "Zapach ogrodu i światło poranka pomagają wrócić do bieżącej chwili.",
                },
                {
                    "id": "memory",
                    "datetime": "2025-07-01T13:00:00Z",
                    "type": "wspomnienie",
                    "category": ["relacja", "codzienność"],
                    "content": "Ważna rozmowa z Krzysztofem i Kasią w ogrodzie.",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_test02_edge_labels_preserve_truth_boundaries(tmp_path: Path) -> None:
    source = tmp_path / "dziennik.json"
    _write_edge_journal(source)

    items = {item.record_id: item for item in JournalReader(source).items()}
    assert items["imagined-story"].truth == "book_scene"
    assert items["imagined-story"].profile == "book_work"
    assert items["system-note"].truth == "source_recorded"
    assert items["system-note"].profile == "system_meta"
    assert items["sensory-anchor"].truth == "inferred"
    assert items["sensory-anchor"].profile == "unclassified"
    assert items["memory"].profile == "experiential"

    inspection = JournalReader(source).inspect()
    assert inspection["truth_status_counts"]["book_scene"] == 1
    assert inspection["truth_status_counts"]["source_recorded"] == 1
    assert inspection["profile_counts"]["experiential"] == 1
    assert inspection["classification_schema_version"]
    assert inspection["automatic_l2"] is False
    assert inspection["automatic_l3"] is False


def test_explicit_truth_conflict_is_visible_for_manual_review() -> None:
    report = classify_journal_raw(
        {
            "type": "sen",
            "truth_status": "user_confirmed",
            "category": ["dziennik snów"],
        }
    )
    assert report.truth_status == "user_confirmed"
    assert "explicit_truth_conflict" in report.review_reasons
    assert "label_truth:symbolic" in report.evidence


def test_domain_inference_is_multilabel_and_token_safe() -> None:
    light = infer_domains_report("Poczułam lekkość i spokojny promień słońca w ogrodzie.")
    medication = infer_domains_report("Biorę leki na migrenę i konsultuję się z lekarzem.")
    context = infer_domains_report(
        "Rozmowa z Kasią o rozdziale książki podczas pracy w ogrodzie.",
        labels="type:wspomnienie category:relacja category:codzienność",
    )

    assert "health" not in light["domains"]
    assert "nature" in light["domains"]
    assert "health" in medication["domains"]
    assert {"relationship", "book", "work", "nature"}.issubset(context["domains"])
    assert context["evidence"]
    assert "health" not in infer_domains("Kotwica sensoryczna i lekkość poranka.")


def test_conversation_title_is_only_a_weak_prior() -> None:
    classifier = ConversationDomainClassifier()
    report = classifier.classify(
        "Napraw moduł Python, import SQLite i test pytest.",
        title="Witaj w podróży Jaźni — rozdział książki",
    )
    assert report.primary_domain == "development"
    assert any(item.startswith("title:book:") for item in report.evidence)
    assert any(item.startswith("text:development:") for item in report.evidence)


def test_classifier_audit_is_read_only_and_reports_context(tmp_path: Path) -> None:
    source = tmp_path / "dziennik.json"
    _write_edge_journal(source)
    root = tmp_path / "runtime"
    coordinator = MemoryRebuildCoordinator(root)
    imported = coordinator.import_journal(source)
    assert imported["ok"]

    audit = coordinator.audit_classifiers(limit=20)
    assert audit["ok"]
    assert audit["source_data_modified"] is False
    assert audit["journal"]["entries"] == 4
    assert audit["journal"]["truth_mismatch_count"] == 0
    assert audit["journal"]["profile_counts"]["book_work"] == 1
    assert audit["journal"]["profile_counts"]["system_meta"] == 1
    assert audit["journal"]["domain_counts"]["relationship"] >= 1
    assert audit["chats"]["analysis_required"] is True
    assert audit["automatic_l2"] is False
    assert audit["automatic_l3"] is False


def test_reclassify_journal_updates_only_derived_truth(tmp_path: Path) -> None:
    source = tmp_path / "dziennik.json"
    _write_edge_journal(source)
    coordinator = MemoryRebuildCoordinator(tmp_path / "runtime")
    coordinator.import_journal(source)

    with coordinator.paths.journal.open("rb") as handle:
        assert handle.read(16).startswith(b"SQLite format 3")

    import sqlite3
    with sqlite3.connect(coordinator.paths.journal) as con:
        before = con.execute(
            "SELECT raw_json,content_sha256,revision FROM journal_entries WHERE source_record_id='imagined-story'"
        ).fetchone()
        con.execute(
            "UPDATE journal_entries SET truth_status='inferred' WHERE source_record_id='imagined-story'"
        )
        con.execute(
            """UPDATE journal_fts_docs SET truth_status='inferred'
               WHERE entry_id=(SELECT entry_id FROM journal_entries WHERE source_record_id='imagined-story')"""
        )
        con.commit()

    dry = coordinator.reclassify_journal(dry_run=True)
    assert dry["changed"] == 1
    assert dry["source_content_modified"] is False
    assert dry["source_revisions_modified"] is False

    applied = coordinator.reclassify_journal(dry_run=False)
    assert applied["changed"] == 1
    assert applied["candidate_rebuild_recommended"] is False

    with sqlite3.connect(coordinator.paths.journal) as con:
        after = con.execute(
            "SELECT raw_json,content_sha256,revision,truth_status FROM journal_entries WHERE source_record_id='imagined-story'"
        ).fetchone()
        fts_truth = con.execute(
            """SELECT truth_status FROM journal_fts_docs
               WHERE entry_id=(SELECT entry_id FROM journal_entries WHERE source_record_id='imagined-story')"""
        ).fetchone()[0]
    assert after[:3] == before
    assert after[3] == "book_scene"
    assert fts_truth == "book_scene"
