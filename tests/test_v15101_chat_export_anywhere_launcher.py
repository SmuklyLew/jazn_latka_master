from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import zipfile


def _write_export(path: Path) -> Path:
    conversation = {
        "id": "conv-anywhere",
        "title": "Uruchomienie spoza repo",
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
                    "content": {"content_type": "text", "parts": ["Test workera spoza repozytorium."]},
                    "metadata": {},
                },
            },
        },
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps([conversation], ensure_ascii=False))
        archive.writestr("chat.html", "<script>var assetsJson = {};</script>")
    return path


def test_launcher_imports_with_child_worker_from_foreign_cwd(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    launcher = repo_root / "tools" / "memory_import_anywhere.py"
    source = _write_export(tmp_path / "chat.zip")
    database = tmp_path / "archive.sqlite3"
    foreign_cwd = tmp_path / "outside"
    foreign_cwd.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(launcher),
            "--json",
            "import",
            "--database",
            str(database),
            "--no-progress",
            str(source),
        ],
        cwd=foreign_cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + "\n" + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["results"][0]["status"] == "imported"
    con = sqlite3.connect(database)
    try:
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
    finally:
        con.close()
