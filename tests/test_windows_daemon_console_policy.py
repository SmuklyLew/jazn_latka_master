from __future__ import annotations

import json
from pathlib import Path

from latka_jazn.core.runtime_daemon import (
    DAEMON_CONSOLE_HIDDEN,
    DAEMON_CONSOLE_VISIBLE,
    append_daemon_process_event,
    resolve_daemon_console_mode,
    windows_daemon_creationflags,
)


class FakeSubprocess:
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NEW_CONSOLE = 0x00000010
    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008


def test_hidden_daemon_uses_no_window_without_detached_process() -> None:
    flags = windows_daemon_creationflags(DAEMON_CONSOLE_HIDDEN, subprocess_module=FakeSubprocess)
    assert flags & FakeSubprocess.CREATE_NO_WINDOW
    assert flags & FakeSubprocess.CREATE_NEW_PROCESS_GROUP
    assert not flags & FakeSubprocess.DETACHED_PROCESS
    assert not flags & FakeSubprocess.CREATE_NEW_CONSOLE


def test_visible_daemon_uses_one_new_console() -> None:
    flags = windows_daemon_creationflags(DAEMON_CONSOLE_VISIBLE, subprocess_module=FakeSubprocess)
    assert flags & FakeSubprocess.CREATE_NEW_CONSOLE
    assert flags & FakeSubprocess.CREATE_NEW_PROCESS_GROUP
    assert not flags & FakeSubprocess.CREATE_NO_WINDOW


def test_invalid_console_mode_falls_back_to_hidden() -> None:
    assert resolve_daemon_console_mode("flash-every-heartbeat", env={}) == DAEMON_CONSOLE_HIDDEN


def test_process_event_is_jsonl_and_contains_parent_pid(tmp_path: Path) -> None:
    path = append_daemon_process_event(tmp_path, "spawned", daemon_pid=123, command=["python", "main.py"])
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["event"] == "spawned"
    assert payload["daemon_pid"] == 123
    assert payload["launcher_pid"] > 0
    assert payload["command"] == ["python", "main.py"]
