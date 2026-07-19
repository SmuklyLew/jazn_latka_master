from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import zipfile

import pytest

from latka_jazn.tools.memory_restore_ui import MemoryRestoreCursorApp
from latka_jazn.tools.memory_restore import (
    DEVELOPER_CONFIRMATION,
    MemoryRestoreOrchestrator,
    MemoryRestoreSettings,
    compare_database_sets,
    confirmation_token,
    discover_restore_sources,
    resolve_database_paths,
    target_preflight,
)


class _NonTtyBuffer:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def write(self, value: str) -> int:
        self.parts.append(value)
        return len(value)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


def _message(mid: str, role: str, text: str, timestamp: float) -> dict:
    return {
        "id": mid,
        "author": {"role": role},
        "create_time": timestamp,
        "content": {"content_type": "text", "parts": [text]},
        "metadata": {},
    }


def _conversation(cid: str, title: str, text: str) -> dict:
    return {
        "id": cid,
        "title": title,
        "create_time": 100.0,
        "update_time": 103.0,
        "current_node": "assistant",
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["user"], "message": None},
            "user": {
                "id": "user",
                "parent": "root",
                "children": ["assistant"],
                "message": _message(f"{cid}-u", "user", text, 101.0),
            },
            "assistant": {
                "id": "assistant",
                "parent": "user",
                "children": [],
                "message": _message(f"{cid}-a", "assistant", "Rozumiem i zachowuję granicę źródła.", 102.0),
            },
        },
    }


def _write_export(path: Path, cid: str) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "conversations.json",
            json.dumps([_conversation(cid, f"Rozmowa {cid}", "Byliśmy nad jeziorem z Kasią.")], ensure_ascii=False),
        )
        archive.writestr("chat.html", "<html></html>")


def _write_journal(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "id": "journal-memory",
                    "datetime": "2025-08-17T18:00:00+02:00",
                    "type": "wspomnienie",
                    "content": "Wspólna chwila nad jeziorem była ważna.",
                },
                {
                    "id": "journal-scene",
                    "datetime": "2025-08-17T19:00:00+02:00",
                    "type": "historia_wyobrazona",
                    "content": "Scena książki w świetle poranka.",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _settings(source: Path, target: Path, **changes: object) -> MemoryRestoreSettings:
    payload = MemoryRestoreSettings(
        source_directory=str(source),
        target_root=str(target),
        mode="developer",
        create_backup=True,
        verify_after_each=True,
        full_validation=False,
        audit_classifiers=True,
        reclassify_journal_dry_run=True,
    ).to_dict()
    payload.update(changes)
    return MemoryRestoreSettings(**payload)


def test_restore_ui_plain_multi_select_preserves_existing_selection(tmp_path: Path) -> None:
    answers = iter([""])
    app = MemoryRestoreCursorApp(
        tmp_path,
        input_func=lambda _prompt: next(answers),
        output=_NonTtyBuffer(),
    )
    assert app._choose_multi("Pliki", ["a", "b", "c"], {0, 2}) == {0, 2}


def test_discovery_is_stable_and_filters_unsupported_files(tmp_path: Path) -> None:
    source = tmp_path / "exports"
    source.mkdir()
    _write_export(source / "small.zip", "small")
    _write_export(source / "large.zip", "large")
    (source / "ignore.txt").write_text("x", encoding="utf-8")
    nested = source / "nested"
    nested.mkdir()
    _write_journal(nested / "journal.json")

    flat = discover_restore_sources(source, recursive=False)
    recursive = discover_restore_sources(source, recursive=True)

    assert {item.path.name for item in flat} == {"small.zip", "large.zip"}
    assert {item.path.name for item in recursive} == {"small.zip", "large.zip", "journal.json"}
    assert recursive == sorted(recursive, key=lambda item: (-item.size_bytes, item.path.name.casefold()))


def test_developer_mode_refuses_target_inside_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "latka_jazn").mkdir()
    (repo / "latka_jazn" / "version.py").write_text("VERSION='x'", encoding="utf-8")
    source = tmp_path / "exports"; source.mkdir()
    settings = _settings(source, repo / "test_03")
    report = target_preflight(settings, tool_root=repo)
    assert not report["ok"]
    assert "developer_target_must_be_outside_repository" in report["blocking_errors"]


def test_system_confirmation_binds_to_exact_target_path(tmp_path: Path) -> None:
    settings = MemoryRestoreSettings(source_directory=str(tmp_path), target_root=str(tmp_path / "system"), mode="system")
    token = confirmation_token(settings)
    assert token.startswith("SYSTEM_RESTORE:")
    assert token.endswith(str((tmp_path / "system").resolve()))


def test_restore_test3_runs_all_safe_steps_and_never_promotes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = tmp_path / "exports"
    source.mkdir()
    first = source / "chat-1.zip"; second = source / "chat-2.zip"; journal = source / "dziennik.json"
    _write_export(first, "c1"); _write_export(second, "c2"); _write_journal(journal)
    target = tmp_path / "jazn_memory_test_03"
    events: list[dict] = []
    orchestrator = MemoryRestoreOrchestrator(_settings(source, target), tool_root=repo, callback=events.append)

    plan = orchestrator.plan([first, second, journal])
    assert plan.ok
    assert len(plan.chats) == 2
    assert len(plan.journals) == 1
    assert not target.exists(), "plan must not write the target"

    result = orchestrator.run([first, second, journal], confirmation=DEVELOPER_CONFIRMATION)
    assert result["ok"], result
    paths = resolve_database_paths(target)
    assert all(path.is_file() for path in paths.values())
    with sqlite3.connect(paths["archive_chats"]) as con:
        assert con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM import_conflicts").fetchone()[0] == 0
    with sqlite3.connect(paths["journal"]) as con:
        assert con.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == 2
        truth = dict(con.execute("SELECT source_record_id,truth_status FROM journal_entries"))
        assert truth["journal-scene"] == "book_scene"
    with sqlite3.connect(paths["experience"]) as con:
        assert con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM experiences").fetchone()[0] == 0
    with sqlite3.connect(paths["memory_jazn"]) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("memory_items", "promotion_ledger"):
            if table in tables:
                assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert any(event.get("stage") == "conversations_imported" for event in events)
    report_dirs = list((target / "reports" / "memory_restore").iterdir())
    assert len(report_dirs) == 1
    assert (report_dirs[0] / "events.jsonl").is_file()
    assert (report_dirs[0] / "summary.json").is_file()


def test_restore_is_idempotent_and_preserves_existing_rows(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    source = tmp_path / "exports"; source.mkdir()
    chat = source / "chat.zip"; _write_export(chat, "same")
    target = tmp_path / "jazn_memory_test_03"
    settings = _settings(source, target, create_backup=False, audit_classifiers=False, reclassify_journal_dry_run=False)

    first = MemoryRestoreOrchestrator(settings, tool_root=repo).run([chat], confirmation=DEVELOPER_CONFIRMATION)
    second = MemoryRestoreOrchestrator(settings, tool_root=repo).run([chat], confirmation=DEVELOPER_CONFIRMATION)
    assert first["ok"] and second["ok"]
    with sqlite3.connect(resolve_database_paths(target)["archive_chats"]) as con:
        assert con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 3


def test_baseline_comparison_proves_old_archive_is_preserved(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    source = tmp_path / "exports"; source.mkdir()
    first = source / "first.zip"; second = source / "second.zip"
    _write_export(first, "old"); _write_export(second, "new")
    baseline = tmp_path / "test_01"; current = tmp_path / "test_03"
    base_settings = _settings(source, baseline, create_backup=False, audit_classifiers=False, reclassify_journal_dry_run=False)
    current_settings = _settings(source, current, create_backup=False, audit_classifiers=False, reclassify_journal_dry_run=False)
    assert MemoryRestoreOrchestrator(base_settings, tool_root=repo).run([first], confirmation=DEVELOPER_CONFIRMATION)["ok"]
    assert MemoryRestoreOrchestrator(current_settings, tool_root=repo).run([first, second], confirmation=DEVELOPER_CONFIRMATION)["ok"]

    report = compare_database_sets(current, [baseline])
    archive = report["baselines"][0]["logical_subset"]["archive_chats"]
    assert archive["missing_conversations"] == 0
    assert archive["changed_conversations"] == 0
    assert archive["missing_nodes"] == 0
    assert archive["changed_nodes"] == 0
    assert archive["missing_import_source_hashes"] == 0


def test_restore_requires_explicit_confirmation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    source = tmp_path / "exports"; source.mkdir()
    chat = source / "chat.zip"; _write_export(chat, "c")
    target = tmp_path / "target"
    orchestrator = MemoryRestoreOrchestrator(_settings(source, target), tool_root=repo)
    with pytest.raises(PermissionError, match="RESTORE"):
        orchestrator.run([chat], confirmation="")
    assert not target.exists()


def test_known_package_json_is_not_offered_as_memory_source(tmp_path: Path) -> None:
    source = tmp_path / "exports"; source.mkdir()
    sidecar = source / "chatGPT-export.zip.package.json"
    sidecar.write_text(json.dumps({"files": [{"path": "a.py", "sha256": "x"}]}), encoding="utf-8")
    journal = source / "dziennik.json"; _write_journal(journal)
    discovered = discover_restore_sources(source)
    assert [item.path.name for item in discovered] == ["dziennik.json"]
