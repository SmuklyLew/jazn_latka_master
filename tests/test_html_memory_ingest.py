from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import sqlite3

from latka_jazn.config import JaznConfig
from latka_jazn.core.neuropsychology_map import NeuropsychologyMapper
from latka_jazn.core.template_registry import TemplateRegistry
from latka_jazn.memory.html_memory_ingest import HtmlMemoryIngestor
from latka_jazn.memory.legacy_memory_recovery import RECOVERY_SCHEMA
from latka_jazn.memory.normalization_sidecar import MemoryNormalizationSidecar


def _conversation(*, long_text: str = "Pamięć ze źródła HTML") -> dict:
    return {
        "conversation_id": "conv-html-1",
        "id": "conv-html-1",
        "title": "Import pamięci HTML",
        "create_time": 1_753_000_000.0,
        "update_time": 1_753_000_060.0,
        "current_node": "node-assistant",
        "mapping": {
            "node-user": {
                "id": "node-user",
                "parent": None,
                "children": ["node-assistant"],
                "message": {
                    "id": "msg-user",
                    "author": {"role": "user"},
                    "create_time": 1_753_000_000.0,
                    "content": {"content_type": "text", "parts": [long_text]},
                },
            },
            "node-assistant": {
                "id": "node-assistant",
                "parent": "node-user",
                "children": [],
                "message": {
                    "id": "msg-assistant",
                    "author": {"role": "assistant"},
                    "create_time": 1_753_000_060.0,
                    "content": {"content_type": "text", "parts": ["Odpowiedź Łatki ze źródłem."]},
                },
            },
        },
    }


def _write_html(path: Path, conversation: dict) -> Path:
    payload = json.dumps([conversation], ensure_ascii=False)
    path.write_text(
        f"<html><body><script>var jsonData = {payload};</script></body></html>",
        encoding="utf-8",
    )
    return path


def test_html_ingest_dry_run_does_not_create_database(tmp_path: Path) -> None:
    source = _write_html(tmp_path / "chat.html", _conversation())
    report = HtmlMemoryIngestor(tmp_path).run([source], dry_run=True)
    assert report.ok, report.errors
    assert report.status == "dry_run_ok"
    assert report.sources[0]["conversations_seen"] == 1
    assert report.sources[0]["messages_seen"] == 2
    assert not JaznConfig(root=tmp_path).recovered_memory_db_path.exists()


def test_html_ingest_preserves_full_text_and_builds_wake_state(tmp_path: Path) -> None:
    long_text = "Ź" * 6_500
    source = _write_html(tmp_path / "chat.html", _conversation(long_text=long_text))
    report = HtmlMemoryIngestor(tmp_path).run([source])
    assert report.ok, report.errors
    assert report.status == "ready"
    assert report.validation and report.validation["ok"] is True
    assert report.normalization and report.normalization["status"] == "ok"
    assert report.wake_state and report.wake_state["status"] == "ready"
    assert report.memory_tiers and report.memory_tiers["ready"] is True

    cfg = JaznConfig(root=tmp_path)
    target = cfg.recovered_memory_db_path
    assert cfg.memory_tier_db_path.is_file()
    assert not Path(str(target) + "-wal").exists()
    assert not Path(str(target) + "-shm").exists()
    with closing(sqlite3.connect(target)) as con:
        stored = con.execute(
            "SELECT content_text FROM messages WHERE message_id='msg-user'"
        ).fetchone()[0]
        assert stored == long_text
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM message_sources").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM html_import_sources").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM recovery_provenance").fetchone()[0] == 2

    repeated = HtmlMemoryIngestor(tmp_path).run([source])
    assert repeated.ok, repeated.errors
    assert repeated.sources[0]["status"] == "already_imported"
    assert repeated.backup_path and Path(repeated.backup_path).is_file()
    with closing(sqlite3.connect(target)) as con:
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2


def test_empty_valid_memory_can_build_truthful_wake_snapshot(tmp_path: Path) -> None:
    cfg = JaznConfig(root=tmp_path)
    cfg.recovered_memory_db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(cfg.recovered_memory_db_path)) as con:
        con.executescript(RECOVERY_SCHEMA)

    sidecar = MemoryNormalizationSidecar(
        tmp_path,
        source_db_path=cfg.recovered_memory_db_path,
        sidecar_db_path=cfg.normalization_sidecar_db_path,
        runtime_version=cfg.version,
    )
    normalized = sidecar.normalize()
    assert normalized.status == "ok"
    assert normalized.output_counts["normalized_memory_items"] == 0
    assert sidecar.status(deep_verify=True).status == "ready"

    wake = sidecar.build_wake_state()
    assert wake.status == "ready", wake.errors
    status = sidecar.wake_state_status(deep_verify=True)
    assert status.status == "ready", status.errors
    assert status.active_snapshot is not None
    assert status.active_snapshot["source_counts"]["memory_is_empty"] is True


def test_slots_dataclasses_are_exported_without_dunder_dict() -> None:
    principles = NeuropsychologyMapper().expanded_principles()
    assert principles and principles[0]["source_key"] == "hippocampus_episodic_memory"
    registry = TemplateRegistry().to_dict()
    assert registry["template_count"] == len(registry["templates"])
    assert registry["templates"][0]["template_id"] == "tpl_received_sense"


def test_memory_import_html_cli_dry_run(tmp_path: Path, capsys) -> None:
    from latka_jazn import cli

    source = _write_html(tmp_path / "chat.html", _conversation())
    code = cli.main([
        "memory-import-html",
        "--root", str(tmp_path),
        "--dry-run",
        "--json",
        str(source),
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "dry_run_ok"
    assert payload["sources"][0]["messages_seen"] == 2
