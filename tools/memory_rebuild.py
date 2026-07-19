#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jaźń / Łatka — Memory Rebuild v24.0.2.01

Pełne narzędzie operatorskie do kontrolowanego odtworzenia pięciu baz pamięci:
  - archive_chats.sqlite3
  - journal.sqlite3
  - experience.sqlite3
  - memory_jazn.sqlite3
  - import_catalog.sqlite3

Program korzysta z kanonicznego silnika latka_jazn.tools.memory_restore.
Ten plik jest samodzielnym punktem wejścia; nie wymaga osobnego launchera.
Nie duplikuje transakcji, schematów ani klasyfikatorów. Zapewnia:
  - pełnoekranowy interfejs kursorowy z myszą i PPM=wstecz,
  - kompletny tryb tekstowy bez prompt_toolkit,
  - plan bez zapisu, jawne potwierdzenie odbudowy i raportowanie,
  - wybór wielu eksportów ChatGPT oraz dziennika,
  - tryb developerski poza repo albo jawną odbudowę systemową,
  - backup całego zestawu SQLite, walidację po każdym źródle,
  - porównanie z wcześniejszymi bazami testowymi,
  - brak automatycznej promocji L2/L3.

Przykłady:
  py -X utf8 tools/memory_rebuild.py
  py -X utf8 tools/memory_rebuild.py --text-ui
  py -X utf8 tools/memory_rebuild.py --config D:\\.AI\\memory_rebuild_test_03.json
  py -X utf8 tools/memory_rebuild.py --no-ui --plan-only --all-discovered
  py -X utf8 tools/memory_rebuild.py --version
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import traceback
from typing import Any, Callable, Iterable, Sequence

TOOL_VERSION = "24.0.2.01"
TOOL_SCHEMA = "jazn_memory_rebuild_tool/v24"
CONFIG_NAME = "memory_rebuild_v24.json"
LEGACY_CONFIG_NAME = "restore_memory_v24.json"
UI_CANCEL = "__JAZN_MEMORY_REBUILD_UI_CANCEL__"
UI_EXIT = "__JAZN_MEMORY_REBUILD_UI_EXIT__"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from latka_jazn.tools.memory_restore import (  # noqa: E402
    DEVELOPER_CONFIRMATION,
    SYSTEM_CONFIRMATION,
    MemoryRestoreOrchestrator,
    MemoryRestorePlan,
    MemoryRestoreSettings,
    RestoreSource,
    confirmation_token,
    discover_restore_sources,
)

# prompt_toolkit jest opcjonalny. Tryb tekstowy zawsze pozostaje dostępny.
try:  # pragma: no cover - zależne od terminala
    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.completion import PathCompleter
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Dimension, Layout
    from prompt_toolkit.layout.containers import HSplit, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.mouse_events import MouseButton, MouseEventType
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import TextArea

    HAS_PROMPT_TOOLKIT = True
except Exception:  # pragma: no cover
    Application = None  # type: ignore[assignment]
    get_app = None  # type: ignore[assignment]
    PathCompleter = None  # type: ignore[assignment]
    Condition = None  # type: ignore[assignment]
    KeyBindings = None  # type: ignore[assignment]
    Dimension = None  # type: ignore[assignment]
    Layout = None  # type: ignore[assignment]
    HSplit = None  # type: ignore[assignment]
    VSplit = None  # type: ignore[assignment]
    Window = None  # type: ignore[assignment]
    FormattedTextControl = None  # type: ignore[assignment]
    MouseButton = None  # type: ignore[assignment]
    MouseEventType = None  # type: ignore[assignment]
    Style = None  # type: ignore[assignment]
    TextArea = None  # type: ignore[assignment]
    HAS_PROMPT_TOOLKIT = False


class MemoryRebuildToolError(RuntimeError):
    """Kontrolowany błąd programu operatorskiego."""


class UserCancelled(Exception):
    """Powrót z bieżącego widoku bez zmiany."""


class UserRequestedExit(Exception):
    """Natychmiastowe zamknięcie interfejsu."""


class PlanCancelled(BaseException):
    """Bezpieczne przerwanie planowania bez zapisu baz pamięci."""


@dataclass(slots=True)
class ToolState:
    settings: MemoryRestoreSettings
    selected_paths: list[Path] = field(default_factory=list)
    ui_mode: str = "cursor"
    config_path: Path | None = None
    dirty: bool = False
    last_plan: MemoryRestorePlan | None = None
    last_result: dict[str, Any] | None = None

    def normalize(self) -> None:
        self.settings = self.settings.normalized()
        seen: set[str] = set()
        cleaned: list[Path] = []
        for item in self.selected_paths:
            path = Path(item).expanduser().resolve()
            key = os.path.normcase(str(path))
            if key not in seen and path.is_file():
                cleaned.append(path)
                seen.add(key)
        self.selected_paths = cleaned
        self.ui_mode = "cursor" if self.ui_mode == "cursor" and HAS_PROMPT_TOOLKIT else "text"

    def payload(self) -> dict[str, Any]:
        self.normalize()
        return {
            "schema_version": TOOL_SCHEMA,
            "tool_version": TOOL_VERSION,
            "saved_at_utc": _utc_iso(),
            "ui_mode": self.ui_mode,
            "settings": self.settings.to_dict(),
            "selected_sources": [str(path) for path in self.selected_paths],
        }


def _utc_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _human_size(value: int | float | None) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{int(value or 0)} B"


def _short_path(value: str | Path, width: int = 72) -> str:
    raw = str(value)
    if len(raw) <= width:
        return raw
    keep = max(8, (width - 3) // 2)
    return raw[:keep] + "..." + raw[-keep:]


def _wrap(value: Any, width: int, *, indent: str = "") -> list[str]:
    return textwrap.wrap(
        str(value),
        width=max(12, width),
        subsequent_indent=indent,
        replace_whitespace=False,
        drop_whitespace=True,
    ) or [""]


def _terminal_columns() -> int:
    """Zwróć szerokość aktywnego wyjścia prompt_toolkit lub terminala."""

    if get_app is not None:
        try:
            columns = int(get_app().output.get_size().columns)
            if columns > 0:
                return columns
        except Exception:
            pass
    try:
        return max(1, int(shutil.get_terminal_size((110, 32)).columns))
    except Exception:
        return 110


def _menu_width_dimension(*, minimum: int = 42) -> Any:
    """Elastyczny panel 72%, który wykorzystuje całą dostępną szerokość."""

    if Dimension is None:
        return None
    return Dimension(min=minimum, weight=72)


def _detail_width_dimension(*, minimum: int = 28) -> Any:
    """Elastyczny panel szczegółów 28% wypełniający VSplit razem z menu."""

    if Dimension is None:
        return None
    return Dimension(min=minimum, weight=28)


def _detail_width() -> int:
    columns = _terminal_columns()
    usable = max(1, columns - 1)  # pionowy separator między panelami
    preferred = int(round(usable * 0.28))
    return max(28, min(preferred, max(28, usable - 42)))


def _copy_windows_clipboard(text: str) -> None:
    """Copy Unicode text to the native Windows clipboard using only stdlib."""

    import ctypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    payload = (str(text) + "\0").encode("utf-16-le")
    handle = None
    clipboard_open = False
    try:
        if not user32.OpenClipboard(None):
            raise MemoryRebuildToolError("Nie można otworzyć schowka systemowego.")
        clipboard_open = True
        if not user32.EmptyClipboard():
            raise MemoryRebuildToolError("Nie można wyczyścić schowka systemowego.")
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not handle:
            raise MemoryError("GlobalAlloc failed for clipboard payload")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise MemoryError("GlobalLock failed for clipboard payload")
        try:
            ctypes.memmove(pointer, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise MemoryRebuildToolError("Nie można zapisać tekstu do schowka systemowego.")
        handle = None  # ownership belongs to the clipboard after SetClipboardData
    finally:
        if handle:
            kernel32.GlobalFree(handle)
        if clipboard_open:
            user32.CloseClipboard()


def copy_system_clipboard(text: str) -> None:
    """Copy text to the OS clipboard without making pyperclip mandatory."""

    value = str(text)
    if os.name == "nt":
        _copy_windows_clipboard(value)
        return

    commands: list[list[str]] = []
    if sys.platform == "darwin":
        commands.append(["pbcopy"])
    else:
        commands.extend((["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]))
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        completed = subprocess.run(
            command,
            input=value,
            text=True,
            encoding="utf-8",
            errors="strict",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode == 0:
            return
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(value)
        root.update()
        root.destroy()
        return
    except Exception as exc:
        raise MemoryRebuildToolError(f"Nie udało się skopiować tekstu do schowka: {exc}") from exc


def _atomic_text(path: Path, text: str) -> Path:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)
    return target


def save_plan_report(state: "ToolState", plan: MemoryRestorePlan, text: str) -> tuple[Path, Path]:
    """Save the readable plan and its exact JSON payload outside the repository."""

    target_root = Path(state.settings.target_root).expanduser().resolve()
    output_root = target_root.parent / "memory_rebuild_plans"
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    target_name = target_root.name or "memory_rebuild"
    base = output_root / f"{target_name}_plan_{stamp}"
    text_path = _atomic_text(base.with_suffix(".txt"), text.rstrip() + "\n")
    json_text = json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    json_path = _atomic_text(base.with_suffix(".json"), json_text)
    return text_path, json_path


def default_config_path() -> Path:
    override = os.environ.get("JAZN_MEMORY_REBUILD_CONFIG") or os.environ.get("JAZN_RESTORE_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".jazn" / CONFIG_NAME).resolve()


def legacy_config_path() -> Path:
    """Return the previous v24 config location for one-way compatibility reads."""

    return (Path.home() / ".jazn" / LEGACY_CONFIG_NAME).resolve()


def _candidate_baselines() -> list[str]:
    parent = ROOT.parent
    result: list[str] = []
    for name in ("jazn_memory_test_01", "jazn_memory_test_02"):
        candidate = (parent / name).resolve()
        if candidate.is_dir():
            result.append(str(candidate))
    return result


def default_settings() -> MemoryRestoreSettings:
    return MemoryRestoreSettings(
        source_directory=str(ROOT.parent.resolve()),
        target_root=str((ROOT.parent / "jazn_memory_test_03").resolve()),
        mode="developer",
        recursive_scan=False,
        verify_after_each=True,
        full_validation=True,
        continue_on_error=False,
        create_backup=True,
        audit_classifiers=True,
        reclassify_journal_dry_run=True,
        apply_reclassification=False,
        analyse_topics=False,
        force_topics=False,
        candidate_limit=0,
        progress_every_conversations=5,
        baseline_roots=_candidate_baselines(),
    ).normalized()


def load_state(path: Path | None) -> ToolState:
    target_config = (path or default_config_path()).expanduser().resolve()
    source_config = target_config
    if path is None and not source_config.is_file():
        legacy = legacy_config_path()
        if legacy.is_file():
            source_config = legacy
    if not source_config.is_file():
        return ToolState(default_settings(), ui_mode="cursor" if HAS_PROMPT_TOOLKIT else "text", config_path=target_config)
    payload = json.loads(source_config.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise MemoryRebuildToolError(f"Konfiguracja nie jest obiektem JSON: {source_config}")
    raw_settings = payload.get("settings", payload)
    if not isinstance(raw_settings, dict):
        raise MemoryRebuildToolError(f"Brak obiektu settings w konfiguracji: {source_config}")
    allowed = set(MemoryRestoreSettings.__dataclass_fields__)
    settings_payload = {key: value for key, value in raw_settings.items() if key in allowed}
    settings = MemoryRestoreSettings(**settings_payload).normalized()
    raw_sources = payload.get("selected_sources", [])
    selected = [
        Path(str(item)).expanduser().resolve()
        for item in raw_sources
        if str(item).strip()
    ] if isinstance(raw_sources, list) else []
    ui_mode = str(payload.get("ui_mode") or "cursor").lower()
    state = ToolState(settings, selected, ui_mode=ui_mode, config_path=target_config)
    state.normalize()
    return state


def save_state(state: ToolState, path: Path | None = None) -> Path:
    target = (path or state.config_path or default_config_path()).expanduser().resolve()
    # Konfiguracja operatorska nie może przypadkowo brudzić repozytorium.
    try:
        target.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise MemoryRebuildToolError(
            f"Plik konfiguracji musi znajdować się poza repozytorium: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(state.payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    state.config_path = target
    state.dirty = False
    return target


def _settings_from_args(args: argparse.Namespace, loaded: ToolState) -> ToolState:
    payload = loaded.settings.to_dict()
    if args.source_dir:
        payload["source_directory"] = str(args.source_dir.expanduser().resolve())
    if args.target_root:
        payload["target_root"] = str(args.target_root.expanduser().resolve())
    if args.mode:
        payload["mode"] = args.mode
    loaded.settings = MemoryRestoreSettings(**payload).normalized()
    if args.text_ui:
        loaded.ui_mode = "text"
    elif args.cursor_ui:
        loaded.ui_mode = "cursor" if HAS_PROMPT_TOOLKIT else "text"
    loaded.normalize()
    return loaded


def _configured_sources(path: Path | None) -> list[Path]:
    if path is None or not path.is_file():
        return []
    return load_state(path).selected_paths


def _cursor_ready() -> bool:
    return HAS_PROMPT_TOOLKIT and all(
        item is not None
        for item in (
            Application,
            get_app,
            PathCompleter,
            KeyBindings,
            Dimension,
            Layout,
            HSplit,
            VSplit,
            Window,
            FormattedTextControl,
            MouseButton,
            MouseEventType,
            Style,
            TextArea,
        )
    )


def _is_right_up(event: Any) -> bool:
    return bool(
        MouseButton is not None
        and MouseEventType is not None
        and event.event_type == MouseEventType.MOUSE_UP
        and event.button == MouseButton.RIGHT
    )


def _is_left_up(event: Any) -> bool:
    return bool(
        MouseButton is not None
        and MouseEventType is not None
        and event.event_type == MouseEventType.MOUSE_UP
        and event.button == MouseButton.LEFT
    )


def _cursor_style() -> Any:
    if Style is None:
        return None
    return Style.from_dict(
        {
            "root": "bg:ansiblack fg:ansiwhite",
            "header": "bg:ansiblack",
            "header.title": "ansibrightcyan bold",
            "header.subtitle": "ansibrightblack",
            "border": "ansicyan",
            "menu": "bg:ansiblack",
            "menu.item": "ansiwhite",
            "menu.selected": "reverse bold",
            "menu.section": "ansibrightcyan bold",
            "menu.checked": "ansibrightgreen bold",
            "menu.unchecked": "ansibrightblack",
            "panel": "bg:ansiblack",
            "panel.title": "ansibrightcyan bold",
            "panel.label": "ansibrightblack",
            "panel.text": "ansiwhite",
            "panel.ok": "ansibrightgreen bold",
            "panel.warn": "ansiyellow bold",
            "panel.error": "ansired bold",
            "footer": "bg:ansiblack",
            "footer.key": "reverse bold",
            "footer.text": "ansibrightblack",
            "input": "reverse",
            "progress.done": "ansibrightgreen bold",
            "progress.todo": "ansibrightblack",
        }
    )


def cursor_select(
    title: str,
    rows: Sequence[str],
    *,
    details: Sequence[str] | None = None,
    selected: int = 0,
    subtitle: str = "",
    status_lines: Sequence[str] | None = None,
    groups: dict[int, str] | None = None,
) -> int | None:
    if not _cursor_ready():
        raise MemoryRebuildToolError("Interfejs kursorowy wymaga kompletnego prompt_toolkit.")
    assert Application and get_app and KeyBindings and Dimension and Layout
    assert HSplit and VSplit and Window and FormattedTextControl
    if not rows:
        return None
    index = max(0, min(selected, len(rows) - 1))
    detail_rows = list(details or [""] * len(rows))
    detail_rows.extend([""] * max(0, len(rows) - len(detail_rows)))
    status = list(status_lines or [])
    sections = dict(groups or {})
    keys = KeyBindings()

    def mouse_handler(row_index: int) -> Callable[[Any], object]:
        def handle(event: Any) -> object:
            nonlocal index
            app = get_app()
            if _is_right_up(event):
                app.exit(result=None)
                return None
            if _is_left_up(event):
                index = row_index
                app.invalidate()
                app.exit(result=index)
                return None
            if MouseEventType is not None and event.event_type == MouseEventType.SCROLL_UP:
                index = max(0, index - 1)
                app.invalidate()
                return None
            if MouseEventType is not None and event.event_type == MouseEventType.SCROLL_DOWN:
                index = min(len(rows) - 1, index + 1)
                app.invalidate()
                return None
            return NotImplemented
        return handle

    def render_menu() -> list[Any]:
        fragments: list[Any] = []
        for number, row in enumerate(rows):
            section = sections.get(number)
            if section:
                prefix = f"── {section} "
                fragments.append(
                    (
                        "class:menu.section",
                        ("\n" if fragments else "") + "  " + prefix + "─" * max(2, 44 - len(prefix)) + "\n",
                    )
                )
            handler = mouse_handler(number)
            if number == index:
                fragments.append(("[SetCursorPosition]", ""))
                fragments.append(("class:menu.selected", "  ▶ " + row + "\n", handler))
            else:
                fragments.append(("class:menu.item", "    " + row + "\n", handler))
        return fragments

    def render_detail() -> list[tuple[str, str]]:
        width = max(20, _detail_width() - 4)
        out: list[tuple[str, str]] = [("class:panel.title", "  STAN\n")]
        for line in status:
            out.append(("class:panel.label", "  " + line + "\n"))
        out.append(("class:panel.label", "\n  " + "─" * width + "\n"))
        out.append(("class:panel.title", "  WYBRANA OPCJA\n"))
        for line in _wrap(detail_rows[index] or rows[index], width):
            out.append(("class:panel.text", "  " + line + "\n"))
        return out

    @keys.add("up")
    @keys.add("k")
    def _up(event: Any) -> None:
        nonlocal index
        index = max(0, index - 1)
        event.app.invalidate()

    @keys.add("down")
    @keys.add("j")
    def _down(event: Any) -> None:
        nonlocal index
        index = min(len(rows) - 1, index + 1)
        event.app.invalidate()

    @keys.add("pageup")
    def _page_up(event: Any) -> None:
        nonlocal index
        index = max(0, index - 8)
        event.app.invalidate()

    @keys.add("pagedown")
    def _page_down(event: Any) -> None:
        nonlocal index
        index = min(len(rows) - 1, index + 8)
        event.app.invalidate()

    @keys.add("home")
    def _home(event: Any) -> None:
        nonlocal index
        index = 0
        event.app.invalidate()

    @keys.add("end")
    def _end(event: Any) -> None:
        nonlocal index
        index = len(rows) - 1
        event.app.invalidate()

    @keys.add("enter")
    def _enter(event: Any) -> None:
        event.app.exit(result=index)

    @keys.add("escape", eager=True)
    @keys.add("q", eager=True)
    def _back(event: Any) -> None:
        event.app.exit(result=None)

    @keys.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=UI_EXIT)

    header = FormattedTextControl(
        [
            ("class:header.title", f"  {title}"),
            ("class:header.subtitle", f"  •  {subtitle}" if subtitle else ""),
        ]
    )
    footer = FormattedTextControl(
        [
            ("class:footer.key", " ↑/↓ "), ("class:footer.text", "wybór  "),
            ("class:footer.key", " LPM/Enter "), ("class:footer.text", "otwórz  "),
            ("class:footer.key", " Scroll "), ("class:footer.text", "przewiń  "),
            ("class:footer.key", " Esc/Q/PPM "), ("class:footer.text", "wróć  "),
            ("class:footer.key", " Ctrl+X "), ("class:footer.text", "wyjdź "),
        ]
    )
    menu = FormattedTextControl(render_menu, focusable=True, show_cursor=False)
    detail = FormattedTextControl(render_detail, focusable=False, show_cursor=False)
    original_menu_mouse = menu.mouse_handler
    original_detail_mouse = detail.mouse_handler

    def back_or_delegate(original: Callable[[Any], object], event: Any) -> object:
        if _is_right_up(event):
            get_app().exit(result=None)
            return None
        return original(event)

    header.mouse_handler = lambda event: back_or_delegate(lambda _event: NotImplemented, event)
    footer.mouse_handler = lambda event: back_or_delegate(lambda _event: NotImplemented, event)
    menu.mouse_handler = lambda event: back_or_delegate(original_menu_mouse, event)
    detail.mouse_handler = lambda event: back_or_delegate(original_detail_mouse, event)

    app = Application(
        layout=Layout(
            HSplit(
                [
                    Window(height=2, content=header, style="class:header"),
                    Window(height=1, char="─", style="class:border"),
                    VSplit(
                        [
                            Window(
                                content=menu,
                                width=lambda: _menu_width_dimension(minimum=42),
                                style="class:menu",
                                wrap_lines=False,
                            ),
                            Window(width=1, char="│", style="class:border"),
                            Window(
                                content=detail,
                                width=lambda: _detail_width_dimension(minimum=28),
                                style="class:panel",
                                wrap_lines=True,
                            ),
                        ],
                        padding=0,
                    ),
                    Window(height=1, char="─", style="class:border"),
                    Window(height=1, content=footer, style="class:footer"),
                ]
            )
        ),
        key_bindings=keys,
        style=_cursor_style(),
        full_screen=True,
        erase_when_done=True,
        mouse_support=True,
    )
    result = app.run()
    if result == UI_EXIT:
        raise UserRequestedExit()
    return result


def cursor_multi_select(
    title: str,
    items: Sequence[RestoreSource | Path],
    selected_paths: Sequence[Path],
) -> list[Path] | None:
    if not _cursor_ready():
        raise MemoryRebuildToolError("Interfejs kursorowy wymaga kompletnego prompt_toolkit.")
    assert Application and get_app and KeyBindings and Dimension and Layout
    assert HSplit and VSplit and Window and FormattedTextControl
    paths = [item.path if isinstance(item, RestoreSource) else Path(item) for item in items]
    if not paths:
        return []
    chosen = {os.path.normcase(str(Path(path).resolve())) for path in selected_paths}
    index = 0
    keys = KeyBindings()

    def toggle(row: int) -> None:
        key = os.path.normcase(str(paths[row].resolve()))
        if key in chosen:
            chosen.remove(key)
        else:
            chosen.add(key)

    def mouse_handler(row_index: int) -> Callable[[Any], object]:
        def handle(event: Any) -> object:
            nonlocal index
            app = get_app()
            if _is_right_up(event):
                app.exit(result=True)
                return None
            if _is_left_up(event):
                index = row_index
                toggle(index)
                app.invalidate()
                return None
            if MouseEventType is not None and event.event_type == MouseEventType.SCROLL_UP:
                index = max(0, index - 1)
                app.invalidate()
                return None
            if MouseEventType is not None and event.event_type == MouseEventType.SCROLL_DOWN:
                index = min(len(paths) - 1, index + 1)
                app.invalidate()
                return None
            return NotImplemented
        return handle

    def render_list() -> list[Any]:
        out: list[Any] = []
        for number, path in enumerate(paths):
            key = os.path.normcase(str(path.resolve()))
            checked = key in chosen
            marker = "☑" if checked else "☐"
            style = "class:menu.selected" if number == index else "class:menu.item"
            if number == index:
                out.append(("[SetCursorPosition]", ""))
            out.append((style, f"  {'▶' if number == index else ' '} {marker} {path.name}\n", mouse_handler(number)))
        return out

    def render_detail() -> list[tuple[str, str]]:
        path = paths[index]
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        key = os.path.normcase(str(path.resolve()))
        selected = key in chosen
        width = max(20, _detail_width() - 4)
        out = [
            ("class:panel.title", "  WYBRANE ŹRÓDŁO\n"),
            ("class:panel.label", f"  Pozycja: {index + 1}/{len(paths)}\n"),
            ("class:panel.label", f"  Stan: {'ZAZNACZONE' if selected else 'NIEZAZNACZONE'}\n"),
            ("class:panel.label", f"  Rozmiar: {_human_size(size)}\n"),
            ("class:panel.label", f"  Sufiks: {path.suffix.lower() or '-'}\n"),
            ("class:panel.label", "\n  " + "─" * width + "\n"),
            ("class:panel.title", "  PEŁNA ŚCIEŻKA\n"),
        ]
        for line in _wrap(path, width):
            out.append(("class:panel.text", "  " + line + "\n"))
        return out

    @keys.add("up")
    def _up(event: Any) -> None:
        nonlocal index
        index = max(0, index - 1)
        event.app.invalidate()

    @keys.add("down")
    def _down(event: Any) -> None:
        nonlocal index
        index = min(len(paths) - 1, index + 1)
        event.app.invalidate()

    @keys.add(" ")
    def _toggle(event: Any) -> None:
        toggle(index)
        event.app.invalidate()

    @keys.add("a")
    def _all(event: Any) -> None:
        chosen.clear()
        chosen.update(os.path.normcase(str(path.resolve())) for path in paths)
        event.app.invalidate()

    @keys.add("n")
    def _none(event: Any) -> None:
        chosen.clear()
        event.app.invalidate()

    @keys.add("enter")
    def _accept(event: Any) -> None:
        event.app.exit(result=True)

    @keys.add("escape", eager=True)
    @keys.add("q", eager=True)
    def _cancel(event: Any) -> None:
        event.app.exit(result=None)

    @keys.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=UI_EXIT)

    header = FormattedTextControl(
        [
            ("class:header.title", f"  {title}"),
            ("class:header.subtitle", f"  •  {len(paths)} wykrytych  •  {len(chosen)} zaznaczonych"),
        ]
    )
    footer = FormattedTextControl(
        [
            ("class:footer.key", " ↑/↓ "), ("class:footer.text", "plik  "),
            ("class:footer.key", " Spacja/LPM "), ("class:footer.text", "zaznacz  "),
            ("class:footer.key", " A/N "), ("class:footer.text", "wszystkie/żadne  "),
            ("class:footer.key", " Enter/PPM "), ("class:footer.text", "zapisz i wróć  "),
            ("class:footer.key", " Esc/Q "), ("class:footer.text", "anuluj "),
        ]
    )

    def commit_selection(event: Any) -> object:
        if _is_right_up(event):
            get_app().exit(result=True)
            return None
        return NotImplemented

    header.mouse_handler = commit_selection
    footer.mouse_handler = commit_selection
    list_control = FormattedTextControl(render_list, focusable=True)
    detail_control = FormattedTextControl(render_detail)
    original_list_mouse = list_control.mouse_handler
    original_detail_mouse = detail_control.mouse_handler

    def commit_or_delegate(original: Callable[[Any], object], event: Any) -> object:
        if _is_right_up(event):
            get_app().exit(result=True)
            return None
        return original(event)

    list_control.mouse_handler = lambda event: commit_or_delegate(original_list_mouse, event)
    detail_control.mouse_handler = lambda event: commit_or_delegate(original_detail_mouse, event)

    app = Application(
        layout=Layout(
            HSplit(
                [
                    Window(height=2, content=header, style="class:header"),
                    Window(height=1, char="─", style="class:border"),
                    VSplit(
                        [
                            Window(
                                content=list_control,
                                width=lambda: _menu_width_dimension(minimum=48),
                                style="class:menu",
                                wrap_lines=False,
                            ),
                            Window(width=1, char="│", style="class:border"),
                            Window(
                                content=detail_control,
                                width=lambda: _detail_width_dimension(minimum=28),
                                style="class:panel",
                                wrap_lines=True,
                            ),
                        ],
                        padding=0,
                    ),
                    Window(height=1, char="─", style="class:border"),
                    Window(height=1, content=footer, style="class:footer"),
                ]
            )
        ),
        key_bindings=keys,
        style=_cursor_style(),
        full_screen=True,
        erase_when_done=True,
        mouse_support=True,
    )
    result = app.run()
    if result == UI_EXIT:
        raise UserRequestedExit()
    if result is None:
        return None
    return [path.resolve() for path in paths if os.path.normcase(str(path.resolve())) in chosen]


def cursor_edit_value(
    title: str,
    current: str,
    *,
    path_mode: bool = False,
    only_directories: bool = False,
    help_text: str = "",
) -> str | None:
    if not _cursor_ready():
        raise MemoryRebuildToolError("Interfejs kursorowy wymaga kompletnego prompt_toolkit.")
    assert Application and get_app and KeyBindings and Layout and HSplit and Window
    assert FormattedTextControl and TextArea and PathCompleter
    completer = PathCompleter(only_directories=only_directories, expanduser=True) if path_mode else None
    keys = KeyBindings()

    def accept(buffer: Any) -> bool:
        get_app().exit(result=buffer.text)
        return True

    editor = TextArea(
        text=current,
        multiline=False,
        wrap_lines=False,
        completer=completer,
        complete_while_typing=False,
        accept_handler=accept,
        style="class:input",
    )
    editor.buffer.cursor_position = len(current)
    original_mouse = editor.control.mouse_handler

    def editor_mouse(event: Any) -> object:
        # Krytyczne: PPM jest przechwytywany przed domyślną obsługą bufora,
        # więc na Windows nie uruchamia wklejania ze schowka.
        if _is_right_up(event):
            get_app().exit(result=UI_CANCEL)
            return None
        return original_mouse(event)

    editor.control.mouse_handler = editor_mouse

    @keys.add("escape", eager=True)
    def _cancel(event: Any) -> None:
        event.app.exit(result=UI_CANCEL)

    @keys.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=UI_EXIT)

    @keys.add("c-a", eager=True)
    def _clear(event: Any) -> None:
        editor.buffer.text = ""

    header = FormattedTextControl(
        [
            ("class:header.title", f"  {title}"),
            ("class:header.subtitle", "  •  edycja pola"),
        ]
    )
    footer = FormattedTextControl(
        [
            ("class:footer.key", " Enter "), ("class:footer.text", "zapisz  "),
            ("class:footer.key", " Tab "), ("class:footer.text", "uzupełnij ścieżkę  "),
            ("class:footer.key", " Ctrl+A "), ("class:footer.text", "wyczyść  "),
            ("class:footer.key", " Esc/PPM "), ("class:footer.text", "anuluj  "),
            ("class:footer.key", " Ctrl+X "), ("class:footer.text", "wyjdź "),
        ]
    )
    app = Application(
        layout=Layout(
            HSplit(
                [
                    Window(height=2, content=header, style="class:header"),
                    Window(height=1, char="─", style="class:border"),
                    Window(height=2, content=FormattedTextControl([("class:panel.text", "  " + (help_text or "Wpisz wartość i zatwierdź Enterem."))]), wrap_lines=True),
                    Window(height=1),
                    editor,
                    Window(),
                    Window(height=1, char="─", style="class:border"),
                    Window(height=1, content=footer, style="class:footer"),
                ]
            ),
            focused_element=editor,
        ),
        key_bindings=keys,
        style=_cursor_style(),
        full_screen=True,
        erase_when_done=True,
        mouse_support=True,
    )
    result = app.run()
    if result == UI_EXIT:
        raise UserRequestedExit()
    if result == UI_CANCEL:
        return None
    return str(result or "").strip()


def cursor_view_text(
    title: str,
    text: str,
    *,
    subtitle: str = "",
    save_callback: Callable[[], Sequence[Path]] | None = None,
) -> None:
    if not _cursor_ready():
        print(text)
        return
    assert Application and get_app and KeyBindings and Layout and HSplit and Window
    assert FormattedTextControl and TextArea
    keys = KeyBindings()
    status_message = ""
    area = TextArea(
        text=text,
        read_only=True,
        scrollbar=True,
        wrap_lines=False,
        focusable=True,
        style="class:panel.text",
    )
    original_mouse = area.control.mouse_handler

    def reader_mouse(event: Any) -> object:
        if _is_right_up(event):
            get_app().exit(result=None)
            return None
        return original_mouse(event)

    area.control.mouse_handler = reader_mouse

    @keys.add("c-c", eager=True)
    def _copy(event: Any) -> None:
        nonlocal status_message
        try:
            copy_system_clipboard(text)
            status_message = "Skopiowano cały raport do schowka systemowego."
        except Exception as exc:
            status_message = f"Błąd kopiowania: {exc}"
        event.app.invalidate()

    @keys.add("c-s", eager=True)
    def _save(event: Any) -> None:
        nonlocal status_message
        if save_callback is None:
            status_message = "Ten raport nie udostępnia zapisu Ctrl+S."
            event.app.invalidate()
            return
        try:
            paths = [Path(item) for item in save_callback()]
            rendered = ", ".join(str(path) for path in paths)
            status_message = "Zapisano: " + _short_path(rendered, 96)
        except Exception as exc:
            status_message = f"Błąd zapisu: {exc}"
        event.app.invalidate()

    @keys.add("escape", eager=True)
    @keys.add("q", eager=True)
    def _back(event: Any) -> None:
        event.app.exit(result=None)

    @keys.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=UI_EXIT)

    header = FormattedTextControl(
        [
            ("class:header.title", f"  {title}"),
            ("class:header.subtitle", f"  •  {subtitle}" if subtitle else ""),
        ]
    )

    def render_footer() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = [
            ("class:footer.key", " ↑/↓ PgUp/PgDn "), ("class:footer.text", "przewiń  "),
            ("class:footer.key", " Ctrl+C "), ("class:footer.text", "kopiuj całość  "),
        ]
        if save_callback is not None:
            fragments.extend([
                ("class:footer.key", " Ctrl+S "), ("class:footer.text", "zapisz TXT+JSON  "),
            ])
        fragments.extend([
            ("class:footer.key", " Esc/Q/PPM "), ("class:footer.text", "wróć  "),
            ("class:footer.key", " Ctrl+X "), ("class:footer.text", "wyjdź"),
        ])
        fragments.append(("class:footer.status", "\n " + status_message if status_message else "\n "))
        return fragments

    footer = FormattedTextControl(render_footer)
    result = Application(
        layout=Layout(
            HSplit(
                [
                    Window(height=2, content=header, style="class:header"),
                    Window(height=1, char="─", style="class:border"),
                    area,
                    Window(height=1, char="─", style="class:border"),
                    Window(height=2, content=footer, style="class:footer", wrap_lines=False),
                ]
            ),
            focused_element=area,
        ),
        key_bindings=keys,
        style=_cursor_style(),
        full_screen=True,
        erase_when_done=True,
        mouse_support=True,
    ).run()
    if result == UI_EXIT:
        raise UserRequestedExit()


class ProgressScreen:
    def __init__(self, title: str, *, echo: bool = False, cancellable: bool = False):
        self.title = title
        self.echo = bool(echo)
        self.cancellable = bool(cancellable)
        self.cancel_requested = False
        self.cancel_event = threading.Event()
        self.event: dict[str, Any] = {"event": "starting"}
        self.lines: list[str] = []
        self.result: Any = None
        self.error: BaseException | None = None
        self.lock = threading.Lock()
        self.app: Any = None

    def request_cancel(self) -> None:
        if not self.cancellable:
            return
        self.cancel_requested = True
        self.cancel_event.set()
        if self.app is not None:
            try:
                self.app.invalidate()
            except Exception:
                pass

    def callback(self, payload: dict[str, Any]) -> None:
        if self.cancellable and self.cancel_event.is_set():
            raise PlanCancelled()
        with self.lock:
            self.event = dict(payload)
            event_name = str(payload.get("event") or "progress")
            source = Path(str(payload.get("source") or "")).name
            index = payload.get("index")
            total = payload.get("total")
            counters = []
            for key in ("conversations", "nodes", "messages"):
                if payload.get(key) is not None:
                    counters.append(f"{key}={payload[key]}")
            line = event_name
            if index is not None and total is not None:
                line += f" [{index}/{total}]"
            if source:
                line += f" {source}"
            if counters:
                line += " | " + " ".join(counters)
            self.lines.append(line)
            self.lines = self.lines[-18:]
        if self.echo and self.lines:
            print(self.lines[-1], flush=True)
        if self.app is not None:
            try:
                self.app.invalidate()
            except Exception:
                pass

    def run(self, worker: Callable[[], Any]) -> Any:
        if not _cursor_ready():
            return worker()

        assert Application and KeyBindings and Layout and HSplit and Window and FormattedTextControl
        keys = KeyBindings()

        def render() -> list[tuple[str, str]]:
            with self.lock:
                event = dict(self.event)
                lines = list(self.lines)
            name = str(event.get("event") or "working")
            index = int(event.get("index") or 0)
            total = int(event.get("total") or 0)
            fraction = 0.0 if total <= 0 else min(1.0, max(0.0, index / total))
            width = 46
            done = int(width * fraction)
            bar = "█" * done + "░" * (width - done)
            out: list[tuple[str, str]] = [
                ("class:panel.title", f"  {name}\n\n"),
                ("class:progress.done", "  " + bar[:done]),
                ("class:progress.todo", bar[done:] + f"  {fraction * 100:5.1f}%\n\n"),
            ]
            if self.cancel_requested:
                out.extend(
                    [
                        ("class:panel.warn", "  ANULOWANIE ZGŁOSZONE\n"),
                        (
                            "class:panel.label",
                            "  Plan zostanie przerwany w najbliższym bezpiecznym punkcie inspekcji.\n\n",
                        ),
                    ]
                )
            source = event.get("source")
            if source:
                out.append(("class:panel.label", f"  Źródło: {source}\n"))
            for key in ("stage", "conversations", "nodes", "messages", "elapsed_seconds"):
                if event.get(key) is not None:
                    out.append(("class:panel.label", f"  {key}: {event[key]}\n"))
            out.append(("class:panel.label", "\n  OSTATNIE ZDARZENIA\n"))
            for line in lines:
                out.append(("class:panel.text", "  " + line + "\n"))
            return out

        def cancel_from_ui(event: Any | None = None) -> None:
            self.request_cancel()
            if event is not None:
                event.app.invalidate()

        if self.cancellable:
            @keys.add("escape", eager=True)
            @keys.add("q", eager=True)
            def _cancel(event: Any) -> None:
                cancel_from_ui(event)

        @keys.add("c-x", eager=True)
        def _ignore_exit(event: Any) -> None:
            # Odbudowa z zapisem nie może być przerwana przez sam interfejs.
            # Plan bez zapisu używa osobno Esc/Q/PPM i bezpiecznego punktu przerwania.
            if self.cancellable:
                cancel_from_ui(event)

        header = FormattedTextControl(
            [
                ("class:header.title", f"  {self.title}"),
                (
                    "class:header.subtitle",
                    "  •  Esc/Q/PPM anuluje plan bez zapisu"
                    if self.cancellable
                    else "  •  nie zamykaj terminala podczas transakcji",
                ),
            ]
        )
        progress_control = FormattedTextControl(render)
        original_progress_mouse = progress_control.mouse_handler

        def progress_mouse(event: Any) -> object:
            if self.cancellable and _is_right_up(event):
                self.request_cancel()
                return None
            return original_progress_mouse(event)

        progress_control.mouse_handler = progress_mouse
        footer_text = (
            " Esc/Q/PPM — anuluj plan i wróć do menu głównego. "
            if self.cancellable
            else " Trwa bezpieczna odbudowa. Po zakończeniu pojawi się raport. "
        )
        footer_control = FormattedTextControl([("class:footer.text", footer_text)])

        def cancel_mouse(event: Any) -> object:
            if self.cancellable and _is_right_up(event):
                self.request_cancel()
                return None
            return NotImplemented

        if self.cancellable:
            header.mouse_handler = cancel_mouse
            footer_control.mouse_handler = cancel_mouse

        self.app = Application(
            layout=Layout(
                HSplit(
                    [
                        Window(height=2, content=header, style="class:header"),
                        Window(height=1, char="─", style="class:border"),
                        Window(content=progress_control, style="class:panel", wrap_lines=True),
                        Window(height=1, char="─", style="class:border"),
                        Window(
                            height=1,
                            content=footer_control,
                            style="class:footer",
                        ),
                    ]
                )
            ),
            key_bindings=keys,
            style=_cursor_style(),
            full_screen=True,
            erase_when_done=True,
            mouse_support=self.cancellable,
            refresh_interval=0.25,
        )

        def target() -> None:
            try:
                result = worker()
                if self.cancellable and self.cancel_event.is_set():
                    raise PlanCancelled()
                self.result = result
            except BaseException as exc:
                self.error = exc
            finally:
                if self.app is not None:
                    try:
                        loop = getattr(self.app, "loop", None)
                        if loop is not None:
                            loop.call_soon_threadsafe(lambda: self.app.exit(result=True))
                        else:
                            self.app.exit(result=True)
                    except Exception:
                        pass

        thread = threading.Thread(target=target, name="memory-rebuild-worker", daemon=False)
        self.app.run(pre_run=thread.start)
        thread.join()
        self.app = None
        if self.error is not None:
            raise self.error
        return self.result

def _text_menu(title: str, rows: Sequence[str]) -> int | None:
    print(f"\n=== {title} ===")
    for index, row in enumerate(rows, 1):
        print(f"{index:>2}. {row}")
    raw = input("Wybór [Enter=powrót, X=wyjście]: ").strip().lower()
    if not raw:
        return None
    if raw in {"x", "exit", "quit", "koniec"}:
        raise UserRequestedExit()
    try:
        value = int(raw) - 1
    except ValueError:
        return None
    return value if 0 <= value < len(rows) else None


def choose(
    state: ToolState,
    title: str,
    rows: Sequence[str],
    *,
    details: Sequence[str] | None = None,
    selected: int = 0,
    subtitle: str = "",
    status_lines: Sequence[str] | None = None,
    groups: dict[int, str] | None = None,
) -> int | None:
    if state.ui_mode == "cursor":
        return cursor_select(
            title,
            rows,
            details=details,
            selected=selected,
            subtitle=subtitle,
            status_lines=status_lines,
            groups=groups,
        )
    return _text_menu(title, rows)


def edit_value(
    state: ToolState,
    title: str,
    current: str,
    *,
    path_mode: bool = False,
    only_directories: bool = False,
    help_text: str = "",
) -> str | None:
    if state.ui_mode == "cursor":
        return cursor_edit_value(
            title,
            current,
            path_mode=path_mode,
            only_directories=only_directories,
            help_text=help_text,
        )
    raw = input(f"{title} [{current}] (Enter=bez zmian, Esc=anuluj): ").strip()
    if raw.lower() in {"esc", "escape", "anuluj"}:
        return None
    return raw or current


def _state_status(state: ToolState) -> list[str]:
    settings = state.settings.normalized()
    return [
        f"Tryb: {settings.mode}",
        f"Cel: {_short_path(settings.target_root, 42)}",
        f"Źródła: {len(state.selected_paths)}",
        f"Backup: {'ON' if settings.create_backup else 'OFF'}",
        f"Walidacja: {'FULL' if settings.full_validation else 'QUICK'}",
        f"L2/L3: OFF/OFF",
    ]


def _main_rows(state: ToolState) -> list[str]:
    s = state.settings.normalized()
    return [
        "Wybierz źródła z katalogu",
        "Dodaj pojedynczy plik spoza katalogu",
        f"Katalog źródeł: [{_short_path(s.source_directory, 54)}]",
        f"Katalog docelowy: [{_short_path(s.target_root, 54)}]",
        f"Tryb odbudowy: [{s.mode}]",
        f"Bazy porównawcze: [{len(s.baseline_roots)}]",
        "Ustawienia odbudowy",
        "Pokaż plan bez zapisu",
        "Uruchom odbudowę",
        f"Zapisz konfigurację: [{_short_path(state.config_path or default_config_path(), 48)}]",
        f"Interfejs: [{'kursorowy' if state.ui_mode == 'cursor' else 'tekstowy'}]",
        "Pokaż ostatni raport",
        "Autotest programu",
        "Wyjdź",
    ]


def _main_details(state: ToolState) -> list[str]:
    s = state.settings.normalized()
    return [
        "Skanuje katalog i otwiera wielokrotny wybór eksportów ZIP/JSON/HTML oraz dzienników.",
        "Dodaje dziennik lub inne obsługiwane źródło znajdujące się poza głównym katalogiem.",
        "Edytuj katalog zawierający eksporty ChatGPT. Sekrety i techniczne sidecary są filtrowane.",
        "W developer mode musi leżeć poza repo. System mode wymaga zatrzymanego, zweryfikowanego runtime.",
        "Developer tworzy lub uzupełnia osobny zestaw testowy. System zapisuje do właściwego systemu po silniejszym potwierdzeniu.",
        "Katalogi test_01/test_02 używane do końcowego dowodu logicznego zachowania wcześniejszych danych.",
        "Pełna strona bezpieczeństwa, walidacji, backupu, klasyfikacji, tematów i próbek kandydatów.",
        "Wykonuje CRC/inspekcję, plan importu, preflight celu i nie zapisuje żadnej bazy.",
        "Wymaga dokładnego tokenu potwierdzenia. Wykonuje backup, importy, kontrole i raporty.",
        "Zapisuje ustawienia atomowo poza repozytorium, razem z listą zaznaczonych źródeł.",
        "Przełącza pełnoekranowy panel albo numerowane menu bez zależności dodatkowych.",
        "Pokazuje wynik ostatniego planu lub odbudowy w bieżącej sesji.",
        "Sprawdza importy, konfigurację, źródła, preflight i podstawowe kontrakty bez zapisu pamięci.",
        "Kończy program. Niezapisane ustawienia nie są zapisywane automatycznie.",
    ]


def select_sources(state: ToolState) -> None:
    discovered = discover_restore_sources(
        state.settings.source_directory,
        recursive=state.settings.recursive_scan,
    )
    # zachowaj ręcznie dodane źródła spoza katalogu
    by_key: dict[str, RestoreSource | Path] = {
        os.path.normcase(str(item.path.resolve())): item for item in discovered
    }
    for path in state.selected_paths:
        by_key.setdefault(os.path.normcase(str(path.resolve())), path)
    items = list(by_key.values())
    if state.ui_mode == "cursor":
        selected = cursor_multi_select("ŹRÓDŁA ODBUDOWY", items, state.selected_paths)
        if selected is None:
            return
        state.selected_paths = selected
    else:
        paths = [item.path if isinstance(item, RestoreSource) else Path(item) for item in items]
        chosen = {os.path.normcase(str(path.resolve())) for path in state.selected_paths}
        while True:
            print("\n=== ŹRÓDŁA ODBUDOWY ===")
            for index, path in enumerate(paths, 1):
                mark = "X" if os.path.normcase(str(path.resolve())) in chosen else " "
                print(f"{index:>3}. [{mark}] {path.name} ({_human_size(path.stat().st_size)})")
            raw = input("Numer=przełącz, A=wszystkie, N=żadne, Enter=zatwierdź: ").strip().lower()
            if not raw:
                break
            if raw == "a":
                chosen = {os.path.normcase(str(path.resolve())) for path in paths}
                continue
            if raw == "n":
                chosen.clear()
                continue
            try:
                idx = int(raw) - 1
            except ValueError:
                continue
            if 0 <= idx < len(paths):
                key = os.path.normcase(str(paths[idx].resolve()))
                chosen.remove(key) if key in chosen else chosen.add(key)
        state.selected_paths = [path.resolve() for path in paths if os.path.normcase(str(path.resolve())) in chosen]
    state.dirty = True
    state.last_plan = None


def add_external_source(state: ToolState) -> None:
    raw = edit_value(
        state,
        "Dodaj plik źródłowy",
        "",
        path_mode=True,
        only_directories=False,
        help_text="Wskaż pojedynczy eksport ChatGPT albo dziennik JSON. PPM/Esc anuluje.",
    )
    if raw is None or not raw.strip():
        return
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise MemoryRebuildToolError(f"Plik nie istnieje: {path}")
    if path not in state.selected_paths:
        state.selected_paths.append(path)
        state.dirty = True
        state.last_plan = None


def edit_baselines(state: ToolState) -> None:
    selected = 0
    while True:
        roots = list(state.settings.baseline_roots)
        rows = [
            f"Test 01 / baza 1: [{_short_path(roots[0], 54) if len(roots) > 0 else 'brak'}]",
            f"Test 02 / baza 2: [{_short_path(roots[1], 54) if len(roots) > 1 else 'brak'}]",
            "Usuń wszystkie bazy porównawcze",
            "Wróć",
        ]
        details = [
            "Katalog zestawu pięciu baz z pierwszego testu. Służy tylko do końcowego porównania.",
            "Katalog zestawu pięciu baz z drugiego testu. Służy tylko do końcowego porównania.",
            "Wyłącza porównanie, ale nie zmienia żadnej bazy.",
            "Powrót do głównego menu.",
        ]
        choice = choose(state, "BAZY PORÓWNAWCZE", rows, details=details, selected=selected)
        if choice is None or choice == 3:
            return
        selected = choice
        if choice in {0, 1}:
            current = roots[choice] if len(roots) > choice else ""
            value = edit_value(
                state,
                f"Katalog bazy porównawczej {choice + 1}",
                current,
                path_mode=True,
                only_directories=True,
            )
            if value is None:
                continue
            while len(roots) <= choice:
                roots.append("")
            roots[choice] = str(Path(value).expanduser().resolve())
            roots = [item for item in roots if item]
        elif choice == 2:
            roots = []
        payload = state.settings.to_dict()
        payload["baseline_roots"] = roots
        state.settings = MemoryRestoreSettings(**payload).normalized()
        state.dirty = True
        state.last_plan = None


SETTING_SPECS = [
    ("recursive_scan", "Skanuj podkatalogi", "Wyszukuje źródła także w podkatalogach."),
    ("verify_after_each", "Walidacja po każdym źródle", "Po każdym eksporcie i dzienniku uruchamia kontrolę pięciu baz."),
    ("full_validation", "Pełne integrity_check", "Wykonuje pełną, wolniejszą walidację SQLite."),
    ("continue_on_error", "Kontynuuj po błędzie", "Niebezpieczne w pierwszej pełnej odbudowie; zalecane OFF."),
    ("create_backup", "Backup przed zapisem", "Tworzy spójne kopie całego istniejącego zestawu baz."),
    ("audit_classifiers", "Audyt klasyfikatorów", "Końcowy audyt granic book/symbolic/system/media."),
    ("reclassify_journal_dry_run", "Dry-run reklasyfikacji", "Pokazuje możliwe zmiany klasyfikacji bez zapisu."),
    ("apply_reclassification", "Zastosuj reklasyfikację", "Zmienia tylko pochodną klasyfikację i FTS; domyślnie OFF."),
    ("analyse_topics", "Analizuj tematy", "Uruchamiaj dopiero po kompletnym imporcie i przeglądzie."),
    ("force_topics", "Wymuś ponowną analizę", "Przelicza istniejące profile tematów."),
]


def edit_settings(state: ToolState) -> None:
    selected = 0
    while True:
        s = state.settings
        rows = []
        details = []
        for field_name, label, detail in SETTING_SPECS:
            rows.append(f"{label}: [{'ON' if bool(getattr(s, field_name)) else 'OFF'}]")
            details.append(detail)
        rows.extend(
            [
                f"Postęp co rozmów: [{s.progress_every_conversations}]",
                f"Próbka kandydatów: [{s.candidate_limit}]",
                "Przywróć bezpieczne ustawienia pierwszego pełnego testu",
                "Wróć",
            ]
        )
        details.extend(
            [
                "Częstotliwość zdarzeń postępu z importera. Minimum 1.",
                "0 nie tworzy żadnej próbki. Wartość dodatnia tworzy tylko kandydatów do review, bez L2/L3.",
                "Backup ON, pełna walidacja ON, stop po błędzie, audyt ON, tylko dry-run, tematy OFF, kandydaci 0.",
                "Powrót do głównego menu.",
            ]
        )
        choice = choose(
            state,
            "USTAWIENIA ODBUDOWY",
            rows,
            details=details,
            selected=min(selected, len(rows) - 1),
            subtitle="Bezpieczne wartości są domyślne",
            groups={0: "SKAN I WALIDACJA", 4: "BEZPIECZEŃSTWO", 5: "KLASYFIKACJA", 10: "POSTĘP I KANDYDACI", 12: "RESET"},
        )
        if choice is None or choice == len(rows) - 1:
            return
        selected = choice
        payload = s.to_dict()
        if choice < len(SETTING_SPECS):
            field_name = SETTING_SPECS[choice][0]
            payload[field_name] = not bool(payload[field_name])
        elif choice == 10:
            raw = edit_value(state, "Postęp co N rozmów", str(s.progress_every_conversations))
            if raw is None:
                continue
            payload["progress_every_conversations"] = max(1, int(raw))
        elif choice == 11:
            raw = edit_value(state, "Limit próbki kandydatów", str(s.candidate_limit))
            if raw is None:
                continue
            payload["candidate_limit"] = max(0, int(raw))
        elif choice == 12:
            payload.update(
                {
                    "recursive_scan": False,
                    "verify_after_each": True,
                    "full_validation": True,
                    "continue_on_error": False,
                    "create_backup": True,
                    "audit_classifiers": True,
                    "reclassify_journal_dry_run": True,
                    "apply_reclassification": False,
                    "analyse_topics": False,
                    "force_topics": False,
                    "candidate_limit": 0,
                    "progress_every_conversations": 5,
                }
            )
        # zależności bezpieczeństwa
        if payload.get("apply_reclassification"):
            payload["reclassify_journal_dry_run"] = True
        if payload.get("force_topics"):
            payload["analyse_topics"] = True
        state.settings = MemoryRestoreSettings(**payload).normalized()
        state.dirty = True
        state.last_plan = None


def build_plan(state: ToolState, *, cancellable: bool = False) -> MemoryRestorePlan:
    state.normalize()
    if not state.selected_paths:
        raise MemoryRebuildToolError("Nie wybrano żadnych źródeł.")
    progress = ProgressScreen(
        "PLAN ODBUDOWY — BEZ ZAPISU",
        echo=state.ui_mode != "cursor",
        cancellable=cancellable,
    )
    orchestrator = MemoryRestoreOrchestrator(
        state.settings,
        tool_root=ROOT,
        callback=progress.callback,
    )
    plan = progress.run(lambda: orchestrator.plan(state.selected_paths))
    state.last_plan = plan
    return plan


def _plan_text(plan: MemoryRestorePlan) -> str:
    payload = plan.to_dict()
    lines = [
        f"MEMORY REBUILD v{TOOL_VERSION}",
        "",
        f"OK: {payload.get('ok')}",
        f"Wybrane źródła: {payload.get('selected_source_count')}",
        f"Eksporty rozmów: {payload.get('chat_source_count')}",
        f"Dzienniki: {payload.get('journal_source_count')}",
        f"Odrzucone: {payload.get('rejected_source_count')}",
        f"Tryb: {payload.get('settings', {}).get('mode')}",
        f"Cel: {payload.get('settings', {}).get('target_root')}",
        f"Backup: {payload.get('settings', {}).get('create_backup')}",
        f"Walidacja po każdym: {payload.get('settings', {}).get('verify_after_each')}",
        f"Pełna walidacja: {payload.get('settings', {}).get('full_validation')}",
        f"Automatyczne doświadczenia: {payload.get('automatic_experience')}",
        f"Automatyczne L2: {payload.get('automatic_l2')}",
        f"Automatyczne L3: {payload.get('automatic_l3')}",
        "",
        "PREFLIGHT:",
        json.dumps(payload.get("target_preflight"), ensure_ascii=False, indent=2, sort_keys=True, default=str),
        "",
        "EKSPORTY:",
    ]
    for item in payload.get("chats", []):
        lines.append(f"- {item.get('path')}")
        plan_data = item.get("plan", {})
        lines.append(f"  ok={plan_data.get('ok')} relation={plan_data.get('export_relation')} counters={plan_data.get('conversation_counters')}")
        source = plan_data.get("source", {})
        members = source.get("conversation_members") or [source.get("conversations_member")]
        lines.append(f"  members={members}")
    lines.append("")
    lines.append("DZIENNIKI:")
    for item in payload.get("journals", []):
        lines.append(f"- {item.get('path')} | valid={item.get('inspection', {}).get('valid_entries')}")
    lines.append("")
    lines.append("ODRZUCONE:")
    for item in payload.get("rejected", []):
        lines.append(f"- {item.get('path')} | {item.get('reason') or item.get('error')}")
    return "\n".join(lines)


def _confirm_plan_start(state: ToolState) -> bool:
    rows = [
        "Uruchom analizę planu — bez zapisu baz",
        "Wróć do menu głównego",
    ]
    details = [
        "Czyta i weryfikuje wybrane źródła oraz preflight celu. Nie zapisuje baz pamięci.",
        "Nie rozpoczyna żadnej inspekcji. To jest domyślnie zaznaczona, bezpieczna opcja.",
    ]
    choice = choose(
        state,
        "URUCHOMIĆ PLAN ODBUDOWY?",
        rows,
        details=details,
        selected=1,
        subtitle="Esc/Q/PPM również wraca bez uruchamiania",
        status_lines=[f"Wybrane źródła: {len(state.selected_paths)}", "Zapis baz: NIE"],
    )
    return choice == 0


def show_plan(state: ToolState) -> None:
    if not _confirm_plan_start(state):
        return
    try:
        plan = build_plan(state, cancellable=True)
    except PlanCancelled:
        return
    text = _plan_text(plan)
    if state.ui_mode == "cursor":
        cursor_view_text(
            "PLAN ODBUDOWY — BEZ ZAPISU",
            text,
            subtitle="Esc/Q/PPM wraca do menu • Ctrl+S zapisuje raport",
            save_callback=lambda: save_plan_report(state, plan, text),
        )
    else:
        print("\n" + text)


def _confirm_rebuild(state: ToolState, plan: MemoryRestorePlan) -> str | None:
    expected = confirmation_token(state.settings)
    warning = (
        f"TRYB: {state.settings.mode}\n"
        f"CEL: {state.settings.target_root}\n"
        f"EKSPORTY: {len(plan.chats)}\n"
        f"DZIENNIKI: {len(plan.journals)}\n"
        f"ODRZUCONE: {len(plan.rejected)}\n\n"
        f"Wpisz dokładnie:\n{expected}"
    )
    if state.ui_mode == "cursor":
        cursor_view_text("OSTATNIA KONTROLA", warning, subtitle="ten ekran jeszcze niczego nie zapisuje")
    else:
        print(warning)
    value = edit_value(
        state,
        "Token potwierdzenia odbudowy",
        "",
        help_text=f"Wpisz dokładnie {expected}. PPM/Esc anuluje.",
    )
    if value is None:
        return None
    if value != expected:
        raise MemoryRebuildToolError("Nieprawidłowy token potwierdzenia. Odbudowa anulowana.")
    return value


def run_rebuild(state: ToolState) -> None:
    plan = state.last_plan or build_plan(state)
    if not plan.ok:
        raise MemoryRebuildToolError("Plan jest zablokowany. Otwórz plan i usuń błędy preflight.")
    token = _confirm_rebuild(state, plan)
    if token is None:
        return
    progress = ProgressScreen("ODBUDOWA PAMIĘCI ŁATKI", echo=state.ui_mode != "cursor")
    orchestrator = MemoryRestoreOrchestrator(
        state.settings,
        tool_root=ROOT,
        callback=progress.callback,
    )
    result = progress.run(
        lambda: orchestrator.run(
            state.selected_paths,
            confirmation=token,
            prepared_plan=plan,
        )
    )
    state.last_result = result
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if state.ui_mode == "cursor":
        cursor_view_text(
            "WYNIK ODBUDOWY",
            text,
            subtitle="OK" if result.get("ok") else "WYMAGA UWAGI",
        )
    else:
        print(text)
    if not result.get("ok"):
        raise MemoryRebuildToolError("Odbudowa zakończyła się błędem. Szczegóły znajdują się w raporcie.")


def show_last_report(state: ToolState) -> None:
    if state.last_result is not None:
        text = json.dumps(state.last_result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        title = "OSTATNI WYNIK ODBUDOWY"
    elif state.last_plan is not None:
        text = _plan_text(state.last_plan)
        title = "OSTATNI PLAN"
    else:
        text = "Brak planu lub odbudowy w bieżącej sesji."
        title = "BRAK RAPORTU"
    if state.ui_mode == "cursor":
        cursor_view_text(title, text)
    else:
        print(text)


def self_test(state: ToolState) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    config = state.config_path or default_config_path()
    checks.append({"name": "tool_version", "ok": TOOL_VERSION == "24.0.2.01", "value": TOOL_VERSION})
    checks.append({"name": "canonical_filename", "ok": Path(__file__).name == "memory_rebuild.py", "value": Path(__file__).name})
    checks.append({"name": "single_file_entrypoint", "ok": callable(globals().get("main"))})
    checks.append({"name": "plan_cancel_supported", "ok": True, "keys": ["Esc", "Q", "PPM"]})
    checks.append({"name": "layout_uses_weighted_width", "ok": Dimension is None or _detail_width_dimension() is not None})
    checks.append({"name": "core_import", "ok": MemoryRestoreOrchestrator is not None})
    checks.append({"name": "prompt_toolkit_optional", "ok": True, "available": HAS_PROMPT_TOOLKIT})
    checks.append({"name": "config_outside_repo", "ok": not _is_relative_to(config, ROOT), "path": str(config)})
    try:
        state.settings.normalized()
        checks.append({"name": "settings_normalize", "ok": True})
    except Exception as exc:
        checks.append({"name": "settings_normalize", "ok": False, "error": str(exc)})
    try:
        discovered = discover_restore_sources(
            state.settings.source_directory,
            recursive=state.settings.recursive_scan,
        )
        checks.append({"name": "source_discovery", "ok": True, "count": len(discovered)})
    except Exception as exc:
        checks.append({"name": "source_discovery", "ok": False, "error": str(exc)})
    try:
        from latka_jazn.tools.memory_restore_types import target_preflight

        preflight = target_preflight(state.settings, tool_root=ROOT)
        checks.append({"name": "target_preflight", "ok": isinstance(preflight, dict), "result": preflight})
    except Exception as exc:
        checks.append({"name": "target_preflight", "ok": False, "error": str(exc)})
    report = {
        "ok": all(item.get("ok") for item in checks),
        "tool_version": TOOL_VERSION,
        "checks": checks,
        "automatic_experience": False,
        "automatic_l2": False,
        "automatic_l3": False,
        "write_performed": False,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if state.ui_mode == "cursor":
        cursor_view_text("AUTOTEST MEMORY REBUILD", text)
    else:
        print(text)
    return report


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def edit_mode(state: ToolState) -> None:
    rows = [
        "developer — osobny zestaw testowy poza repo",
        "system — właściwe bazy systemu, zatrzymany runtime",
    ]
    details = [
        "Najbezpieczniejszy wybór dla test_03. Cel musi leżeć poza repozytorium.",
        "Wymaga poprawnego runtime, status/doctor i jawnego tokenu SYSTEM_RESTORE:<pełna ścieżka>.",
    ]
    choice = choose(state, "TRYB ODBUDOWY", rows, details=details, selected=0 if state.settings.mode == "developer" else 1)
    if choice is None:
        return
    payload = state.settings.to_dict()
    payload["mode"] = "developer" if choice == 0 else "system"
    state.settings = MemoryRestoreSettings(**payload).normalized()
    state.dirty = True
    state.last_plan = None


def toggle_ui(state: ToolState) -> None:
    if state.ui_mode == "cursor":
        state.ui_mode = "text"
    elif HAS_PROMPT_TOOLKIT:
        state.ui_mode = "cursor"
    else:
        raise MemoryRebuildToolError("Brak prompt_toolkit; dostępny jest tylko interfejs tekstowy.")
    state.dirty = True


def run_interactive(state: ToolState) -> int:
    selected = 0
    while True:
        state.normalize()
        rows = _main_rows(state)
        choice = choose(
            state,
            f"NARZĘDZIA JAŹNI — MEMORY REBUILD v{TOOL_VERSION}",
            rows,
            details=_main_details(state),
            selected=min(selected, len(rows) - 1),
            subtitle="pięć baz • plan • backup • walidacja • brak automatycznego L2/L3",
            status_lines=_state_status(state),
            groups={0: "ŹRÓDŁA", 2: "CEL", 5: "KONTROLA", 7: "PLAN I WYKONANIE", 9: "PROGRAM", 13: "WYJŚCIE"},
        )
        if choice is None:
            continue
        selected = choice
        try:
            if choice == 0:
                select_sources(state)
            elif choice == 1:
                add_external_source(state)
            elif choice == 2:
                value = edit_value(
                    state,
                    "Katalog źródeł",
                    state.settings.source_directory,
                    path_mode=True,
                    only_directories=True,
                    help_text="Folder z eksportami ChatGPT. PPM/Esc wraca bez zmiany.",
                )
                if value is not None:
                    payload = state.settings.to_dict()
                    payload["source_directory"] = str(Path(value).expanduser().resolve())
                    state.settings = MemoryRestoreSettings(**payload).normalized()
                    state.selected_paths = []
                    state.last_plan = None
                    state.dirty = True
            elif choice == 3:
                value = edit_value(
                    state,
                    "Katalog docelowy",
                    state.settings.target_root,
                    path_mode=True,
                    only_directories=True,
                    help_text="Developer: poza repo. System: root zweryfikowanego, zatrzymanego runtime.",
                )
                if value is not None:
                    payload = state.settings.to_dict()
                    payload["target_root"] = str(Path(value).expanduser().resolve())
                    state.settings = MemoryRestoreSettings(**payload).normalized()
                    state.last_plan = None
                    state.dirty = True
            elif choice == 4:
                edit_mode(state)
            elif choice == 5:
                edit_baselines(state)
            elif choice == 6:
                edit_settings(state)
            elif choice == 7:
                show_plan(state)
            elif choice == 8:
                run_rebuild(state)
            elif choice == 9:
                saved = save_state(state)
                message = f"Zapisano konfigurację:\n{saved}"
                cursor_view_text("KONFIGURACJA ZAPISANA", message) if state.ui_mode == "cursor" else print(message)
            elif choice == 10:
                toggle_ui(state)
            elif choice == 11:
                show_last_report(state)
            elif choice == 12:
                self_test(state)
            elif choice == 13:
                return 0
        except UserRequestedExit:
            return 0
        except (MemoryRebuildToolError, ValueError, OSError, json.JSONDecodeError) as exc:
            message = f"{type(exc).__name__}: {exc}"
            if state.ui_mode == "cursor":
                cursor_view_text("BŁĄD KONTROLOWANY", message, subtitle="żadna nowa operacja nie została rozpoczęta")
            else:
                print(f"BŁĄD: {message}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory_rebuild",
        description=f"Memory Rebuild v{TOOL_VERSION}: kontrolowana odbudowa pięciu baz pamięci Jaźni.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    parser.add_argument("--config", type=Path, help="Konfiguracja v24 JSON.")
    parser.add_argument("--no-ui", action="store_true", help="Tryb bez interfejsu.")
    parser.add_argument("--text-ui", action="store_true", help="Wymuś menu tekstowe.")
    parser.add_argument("--cursor-ui", action="store_true", help="Wymuś prompt_toolkit.")
    parser.add_argument("--plan-only", action="store_true", help="Plan bez zapisu.")
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--target-root", type=Path)
    parser.add_argument("--mode", choices=("developer", "system"))
    parser.add_argument("--source", action="append", type=Path, dest="sources")
    parser.add_argument("--all-discovered", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--write-example-config", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--debug", action="store_true", help="Pokaż pełny traceback błędu.")
    return parser


def _headless_sources(args: argparse.Namespace, state: ToolState) -> list[Path]:
    if args.sources:
        return [path.expanduser().resolve() for path in args.sources]
    if args.all_discovered:
        return [
            item.path
            for item in discover_restore_sources(
                state.settings.source_directory,
                recursive=state.settings.recursive_scan,
            )
        ]
    if state.selected_paths:
        return list(state.selected_paths)
    raise MemoryRebuildToolError(
        "Tryb bez UI wymaga --source, --all-discovered albo selected_sources w konfiguracji."
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        state = _settings_from_args(args, load_state(args.config),)
        if args.write_example_config:
            state.config_path = args.write_example_config.expanduser().resolve()
            print(save_state(state, state.config_path))
            return 0
        if args.self_test:
            state.ui_mode = "text"
            report = self_test(state)
            return 0 if report.get("ok") else 2
        interactive = not args.no_ui and not args.plan_only and not args.sources and not args.all_discovered
        if interactive:
            return run_interactive(state)

        sources = _headless_sources(args, state)
        orchestrator = MemoryRestoreOrchestrator(
            state.settings,
            tool_root=ROOT,
            callback=lambda event: print(
                json.dumps(event, ensure_ascii=False, sort_keys=True, default=str),
                flush=True,
            ),
        )
        plan = orchestrator.plan(sources)
        if args.plan_only:
            payload = plan.to_dict()
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
            return 0 if payload.get("ok") else 2
        result = orchestrator.run(
            sources,
            confirmation=args.confirm or "",
            prepared_plan=plan,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return 0 if result.get("ok") else 2
    except KeyboardInterrupt:
        print(
            "Przerwano. Zakończone transakcje pozostają zatwierdzone; aktywna transakcja powinna zostać cofnięta przez silnik.",
            file=sys.stderr,
        )
        return 130
    except UserRequestedExit:
        return 0
    except PlanCancelled:
        return 0
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        else:
            print(f"BŁĄD: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
