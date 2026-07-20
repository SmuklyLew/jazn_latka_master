from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sqlite3

import pytest

from latka_jazn import cli
from latka_jazn.tools.console_progress import TerminalProgress, add_progress_arguments
from latka_jazn.tools import release_metadata_sync
from latka_jazn.tools.runtime_contract_version_normalizer import normalize_runtime_contract_versions
from latka_jazn.tools.version_consistency_audit import build_audit
from tools import memory_import_snapshot


class TtyBuffer(io.StringIO):
    encoding = "utf-8"

    def isatty(self) -> bool:
        return True


class PlainBuffer(io.StringIO):
    encoding = "utf-8"

    def isatty(self) -> bool:
        return False


def test_progress_renderer_uses_semantic_symbols_and_elapsed_time() -> None:
    stream = TtyBuffer()
    progress = TerminalProgress("doctor", style="bar", stream=stream, mode="always", width=20)
    progress.update(5, 10, "Sprawdzanie integralności", symbol="lock")
    progress.finish(True, "Diagnostyka zakończona")

    rendered = stream.getvalue()
    assert "🔒" in rendered
    assert " 50%" in rendered
    assert "✔" in rendered
    assert "100%" in rendered
    assert "Diagnostyka zakończona" in rendered


def test_progress_renderer_has_ascii_fallback() -> None:
    stream = TtyBuffer()
    progress = TerminalProgress(
        "release",
        style="bar",
        stream=stream,
        mode="always",
        ascii_only=True,
        width=16,
    )
    progress.update(1, 2, "Writing", symbol="folder")
    progress.finish(False, "Failed")

    rendered = stream.getvalue()
    assert "DIR" in rendered
    assert "#" in rendered
    assert "X" in rendered
    assert "📁" not in rendered


def test_auto_progress_is_silent_for_non_tty_stream() -> None:
    stream = PlainBuffer()
    progress = TerminalProgress("quiet", stream=stream, mode="auto")
    progress.update(1, 1, "done")
    progress.finish(True, "done")
    assert stream.getvalue() == ""


def test_shared_parser_flags_are_mutually_exclusive() -> None:
    parser = argparse.ArgumentParser()
    add_progress_arguments(parser)
    assert parser.parse_args([]).progress_mode == "auto"
    assert parser.parse_args(["--progress"]).progress_mode == "always"
    assert parser.parse_args(["--no-progress"]).progress_mode == "never"
    assert parser.parse_args(["--ascii-progress"]).ascii_progress is True
    with pytest.raises(SystemExit):
        parser.parse_args(["--progress", "--no-progress"])


def test_runpy_doctor_keeps_json_on_stdout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_doctor(_root: Path, *, progress=None, **kwargs: object) -> dict[str, object]:
        assert progress is not None
        assert kwargs["daemon_host"] == "127.0.0.1"
        assert kwargs["daemon_port"] == 8791
        assert kwargs["marker_output"] is None
        progress(0, 2, "start")
        progress(2, 2, "done")
        return {"ok": True, "schema_version": "test/doctor"}

    monkeypatch.setattr(cli.diagnostics, "doctor_payload", fake_doctor)
    code = cli.main(["doctor", "--daemon-port", "8791", "--json", "--progress", "--ascii-progress"])
    captured = capsys.readouterr()

    assert code == 0
    assert json.loads(captured.out)["ok"] is True
    assert "100%" in captured.err
    assert "Diagnostyka zakończona" in captured.err


def test_runpy_no_progress_keeps_stderr_empty(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli.diagnostics, "doctor_payload", lambda _root, **_kwargs: {"ok": True})
    code = cli.main(["doctor", "--json", "--no-progress"])
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["ok"] is True
    assert captured.err == ""


def test_release_metadata_sync_progress_preserves_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_check(_root: Path, *, base_branch=None, progress=None) -> dict[str, object]:
        assert base_branch is None
        assert progress is not None
        progress(0, 100, "start")
        progress(45, 100, "scan")
        progress(100, 100, "done")
        return {"ok": True, "synchronized": True, "file_count": 12}

    monkeypatch.setattr(release_metadata_sync, "check_release_metadata", fake_check)
    code = release_metadata_sync.main(["--check", "--json", "--progress", "--ascii-progress"])
    captured = capsys.readouterr()

    assert code == 0
    assert json.loads(captured.out)["synchronized"] is True
    assert "[ 45%]" in captured.err
    assert "100%" in captured.err


def test_sqlite_snapshot_progress_is_visible_with_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "snapshot.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
        connection.executemany("INSERT INTO sample(value) VALUES (?)", [(str(index),) for index in range(200)])
        connection.commit()

    code = memory_import_snapshot.main(
        [str(source), str(destination), "--json", "--progress", "--ascii-progress"]
    )
    captured = capsys.readouterr()

    assert code == 0
    assert json.loads(captured.out)["ok"] is True
    assert destination.is_file()
    assert "Snapshot SQLite gotowy" in captured.err
    assert "100%" in captured.err


def test_version_audit_exposes_progress_callback(tmp_path: Path) -> None:
    (tmp_path / "latka_jazn").mkdir()
    (tmp_path / "latka_jazn" / "version.py").write_text(
        'DISTRIBUTION_VERSION = "1.2.3"\nPACKAGE_VERSION = "v1.2.3"\nPACKAGE_VERSION_FULL = PACKAGE_VERSION\n',
        encoding="utf-8",
    )
    events: list[tuple[int, int, str]] = []
    payload = build_audit(tmp_path, progress=lambda done, total, label: events.append((done, total, label)))
    assert isinstance(payload["errors"], list)
    assert events[0][0] == 0
    assert events[-1][0] == 100


def test_runtime_contract_normalizer_exposes_stage_progress(tmp_path: Path) -> None:
    package = tmp_path / "latka_jazn"
    package.mkdir()
    (package / "version.py").write_text(
        'DISTRIBUTION_VERSION = "1.2.3"\nPACKAGE_VERSION = "v1.2.3"\nPACKAGE_VERSION_FULL = PACKAGE_VERSION\n',
        encoding="utf-8",
    )
    events: list[tuple[int, int, str]] = []
    report = normalize_runtime_contract_versions(
        tmp_path,
        apply=False,
        progress=lambda done, total, label: events.append((done, total, label)),
    )
    assert report["runtime_version"] == "v1.2.3"
    assert events[0][0] == 0
    assert events[-1][0] == 100
