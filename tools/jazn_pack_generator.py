#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jaźń / Łatka — generator paczek v7.0.1

Najważniejsza zasada: podgląd, manifest i ZIP korzystają z jednego,
zamrożonego planu plików. Generator nie pakuje archiwów, baz systemowych,
sekretów, cache ani plików ignorowanych przez Git.

Interfejs:
  kursorowy — strzałki ↑/↓, Enter, Esc oraz Ctrl+X; wymaga prompt_toolkit
  tekstowy  — pełne menu numerowane, działające bez bibliotek dodatkowych
  ustawienia obu trybów są zapisywane obok skryptu i migrowane ze starszego formatu

Tryby:
  system    — kod i pliki statyczne, bez memory/ i workspace_runtime/
  memory    — wyłącznie memory/, z bazami SQLite, bez WAL/SHM i archiwów
  combined  — system i memory/ w jednej paczce
  dual      — osobna paczka systemowa i osobna paczka pamięci

Formaty:
  independent — każdy wolumin jest samodzielnym ZIP-em
  binary      — jeden logiczny ZIP dzielony na .zip.001, .002... (jak 1.2_FINAL)
  auto        — independent, chyba że pojedynczy plik przekracza limit

Przykłady:
  py _jazn_pack_generator.py
  py _jazn_pack_generator.py pack D:\\.AI\\jazn_latka_master --out D:\\.AI\\packages --profile dual
  py _jazn_pack_generator.py verify D:\\.AI\\packages\\jazn_latka_vX_system.zip.package.json
  py _jazn_pack_generator.py extract D:\\.AI\\packages\\jazn_latka_vX_system.zip.package.json D:\\.AI\\runtime_test
"""

from __future__ import annotations

import argparse
import contextlib
import ast
import bisect
import datetime as dt
import fnmatch
import hashlib
import inspect
import io
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import textwrap
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Iterator, Sequence, cast

# prompt_toolkit jest opcjonalny. Tryb tekstowy zawsze działa bez zależności.
try:  # pragma: no cover - zależne od terminala użytkownika
    from prompt_toolkit import prompt as _pt_prompt
    from prompt_toolkit.application import Application as _pt_Application
    from prompt_toolkit.completion import DynamicCompleter as _pt_DynamicCompleter
    from prompt_toolkit.completion import PathCompleter as _pt_PathCompleter
    from prompt_toolkit.completion import ThreadedCompleter as _pt_ThreadedCompleter
    from prompt_toolkit.filters import Condition as _pt_Condition
    from prompt_toolkit.key_binding import KeyBindings as _pt_KeyBindings
    from prompt_toolkit.layout import Layout as _pt_Layout
    from prompt_toolkit.layout.containers import ConditionalContainer as _pt_ConditionalContainer
    from prompt_toolkit.layout.containers import DynamicContainer as _pt_DynamicContainer
    from prompt_toolkit.layout.containers import Float as _pt_Float
    from prompt_toolkit.layout.containers import FloatContainer as _pt_FloatContainer
    from prompt_toolkit.layout.containers import HSplit as _pt_HSplit
    from prompt_toolkit.layout.containers import VSplit as _pt_VSplit
    from prompt_toolkit.layout.containers import Window as _pt_Window
    from prompt_toolkit.layout.controls import FormattedTextControl as _pt_FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension as _pt_Dimension
    from prompt_toolkit.layout.menus import CompletionsMenu as _pt_CompletionsMenu
    from prompt_toolkit.layout.scrollable_pane import ScrollablePane as _pt_ScrollablePane
    from prompt_toolkit.mouse_events import MouseButton as _pt_MouseButton
    from prompt_toolkit.mouse_events import MouseEventType as _pt_MouseEventType
    from prompt_toolkit.styles import Style as _pt_Style
    from prompt_toolkit.widgets import Frame as _pt_Frame
    from prompt_toolkit.widgets import TextArea as _pt_TextArea
    HAS_PROMPT_TOOLKIT = True
except Exception:  # pragma: no cover
    _pt_prompt = None
    _pt_Application = None
    _pt_DynamicCompleter = None
    _pt_PathCompleter = None
    _pt_ThreadedCompleter = None
    _pt_Condition = None
    _pt_KeyBindings = None
    _pt_Layout = None
    _pt_ConditionalContainer = None
    _pt_DynamicContainer = None
    _pt_Float = None
    _pt_FloatContainer = None
    _pt_HSplit = None
    _pt_VSplit = None
    _pt_Window = None
    _pt_FormattedTextControl = None
    _pt_Dimension = None
    _pt_CompletionsMenu = None
    _pt_ScrollablePane = None
    _pt_MouseButton = None
    _pt_MouseEventType = None
    _pt_Style = None
    _pt_Frame = None
    _pt_TextArea = None
    HAS_PROMPT_TOOLKIT = False


GENERATOR_VERSION = "7.0.1"
CHUNK_SIZE = 1024 * 1024
DEFAULT_PART_SIZE_MB = 400
DEFAULT_COMPRESSION_LEVEL = 6
DEFAULT_PROFILE = "dual"
DEFAULT_FORMAT = "auto"

SETTINGS_FILE_NAME = "__jazn_pack_generator_settings.json"
SETTINGS_SCHEMA = "jazn_pack_generator_settings/v7.0.1"
UI_MODE_CHOICES = ("tekstowy", "kursorowy")
UI_EXIT_MARKER = "__JAZN_UI_EXIT__"
UI_CANCEL_MARKER = "__JAZN_UI_CANCEL__"

PACKAGE_INTEGRITY_MANIFEST = "PACKAGE_INTEGRITY_MANIFEST.json"
MEMORY_PACKAGE_MANIFEST = "memory/MEMORY_PACKAGE_MANIFEST.json"
PACKAGE_SET_SCHEMA = "jazn_package_set/v2"
MEMORY_MANIFEST_SCHEMA = "jazn_memory_package_manifest/v1"

REQUIRED_SYSTEM_PATHS = {
    "SOURCE_PROVENANCE.json",
    "run.py",
    "main.py",
    "latka_jazn/version.py",
}

PROFILE_CHOICES: tuple[str, ...] = ("system", "memory", "combined", "dual")
FORMAT_CHOICES: tuple[str, ...] = ("auto", "independent", "binary")

@dataclass(frozen=True, slots=True)
class Theme:
    """Centralna konfiguracja wyglądu i responsywności interfejsu.

    Lewy panel ma stałą szerokość, wystarczającą dla etykiet i pól menu.
    Prawy panel wypełnia całą pozostałą przestrzeń i reaguje na zmianę
    rozmiaru terminala. Gdy terminal jest zbyt wąski, VSplit pokazuje
    kontrolowany ekran ``window_too_small`` zamiast ściskać zawartość.
    """

    name: str = "latka-cyan-v7.0.1"
    left_panel_width: int = 54
    right_min_width: int = 36
    compact_breakpoint: int = 96
    page_step: int = 8
    info_min_height: int = 3
    info_max_height: int = 9
    popup_min_width: int = 28
    popup_max_width: int = 48
    popup_min_height: int = 10
    popup_max_height: int = 13
    section_fill: str = "─"
    border_char: str = "│"
    styles: dict[str, str] = field(default_factory=lambda: {
        "root": "bg:ansiblack fg:ansiwhite",
        "header": "bg:ansiblack",
        "header.title": "ansibrightcyan bold",
        "header.subtitle": "ansibrightblack",
        "border": "ansicyan",
        "menu": "bg:ansiblack",
        "menu.item": "ansiwhite",
        "menu.section": "ansibrightcyan bold",
        "menu.selected": "bg:ansicyan fg:ansiblack bold",
        "menu.editing": "bg:ansiwhite fg:ansiblack",
        "panel": "bg:ansiblack",
        "panel.title": "ansibrightcyan bold",
        "panel.label": "ansibrightblack",
        "panel.rule": "ansicyan",
        "panel.text": "ansiwhite",
        "panel.error": "ansired bold",
        "footer": "bg:ansiblack",
        "footer.key": "bg:ansicyan fg:ansiblack bold",
        "footer.text": "ansibrightblack",
        "input": "bg:ansiwhite fg:ansiblack",
        "message.ok": "ansibrightgreen",
        "message.error": "ansired bold",
        "message.warn": "ansiyellow",
        "info": "bg:ansiblack fg:ansiwhite",
        "info.title": "ansibrightcyan bold",
        "info.text": "ansibrightblack",
        "popup": "bg:ansiblack fg:ansiwhite",
        "popup.title": "ansibrightcyan bold",
        "popup.text": "ansiwhite",
        "popup.selected": "bg:ansicyan fg:ansiblack bold",
        "popup.rule": "ansicyan",
        "popup.footer": "ansibrightblack",
        "completion-menu.completion": "bg:ansiblack fg:ansiwhite",
        "completion-menu.completion.current": "bg:ansicyan fg:ansiblack bold",
        "scrollbar.background": "bg:ansiblack",
        "scrollbar.button": "bg:ansicyan",
    })

    @property
    def minimum_terminal_width(self) -> int:
        return 44

    def is_compact(self, columns: int) -> bool:
        return int(columns) < self.compact_breakpoint

    def prompt_toolkit_style(self):
        style_cls = _pt_Style
        if style_cls is None:
            return None
        return style_cls.from_dict(dict(self.styles))

    def terminal_size(self) -> tuple[int, int]:
        try:
            from prompt_toolkit.application.current import get_app
            size = get_app().output.get_size()
            return max(20, int(size.columns)), max(8, int(size.rows))
        except Exception:
            size = shutil.get_terminal_size((120, 32))
            return max(20, int(size.columns)), max(8, int(size.lines))

    def layout_metrics(self, columns: int, rows: int) -> dict[str, int]:
        columns = max(20, int(columns))
        rows = max(8, int(rows))
        usable = max(2, columns - 1)
        compact = self.is_compact(columns)
        left = usable if compact else min(self.left_panel_width, max(1, usable - self.right_min_width))
        right = usable if compact else max(1, usable - left)
        info_height = max(
            self.info_min_height,
            min(self.info_max_height, int(round(rows * 0.22))),
        )
        return {
            "columns": columns,
            "rows": rows,
            "compact": int(compact),
            "left_width": left,
            "right_width": right,
            "left_text_width": max(12, left - 5),
            "right_text_width": max(12, right - 4),
            "info_height": info_height,
            "popup_width": max(self.popup_min_width, min(self.popup_max_width, columns - 4)),
            "popup_height": max(self.popup_min_height, min(self.popup_max_height, rows - 4)),
        }

    def current_metrics(self) -> dict[str, int]:
        return self.layout_metrics(*self.terminal_size())

    def left_dimension(self):
        dimension_cls = _pt_Dimension
        if dimension_cls is None:
            return None
        return dimension_cls.exact(self.left_panel_width)

    def right_dimension(self):
        dimension_cls = _pt_Dimension
        if dimension_cls is None:
            return None
        return dimension_cls(min=self.right_min_width, weight=1)

    def info_dimension(self):
        dimension_cls = _pt_Dimension
        if dimension_cls is None:
            return None

        def current_dimension():
            return dimension_cls.exact(self.current_metrics()["info_height"])

        return current_dimension

    def popup_width(self) -> int:
        return self.current_metrics()["popup_width"]

    def popup_height(self) -> int:
        return self.current_metrics()["popup_height"]


APP_THEME = Theme()

# Wykluczenia edytowalne przez użytkownika. Krytyczne reguły bezpieczeństwa
# (repozytoria VCS, sekrety, archiwa zagnieżdżone, WAL/SHM i ścieżki runtime)
# pozostają wymuszane niezależnie od tej listy.
DEFAULT_BASE_EXCLUDES = [
    "**/__pycache__/**",
    ".pytest_cache/**",
    ".pytest-tmp/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    ".tox/**",
    ".nox/**",
    ".archives/**",
    "backups/**",
    "backups_git/**",
    "exports/**",
    "*.patch",
    "*.rej",
    "*.orig",
    "*.bak",
    "*.bad",
    "*.corrupt",
    "*.tmp",
    "*.temp",
    "*.partial",
    "*.log",
    "*.pyc",
    "*.pyo",
]

PROFILE_DISPLAY = {
    "dual": "system + pamięć",
    "system": "tylko system",
    "memory": "tylko pamięć",
    "combined": "system i pamięć w jednym ZIP",
}

PROFILE_DESCRIPTIONS = {
    "dual": "Tworzy dwie osobne paczki: statyczny system oraz pamięć.",
    "system": "Pakuje kod i pliki statyczne bez memory/ i workspace_runtime/.",
    "memory": "Pakuje wyłącznie memory/, w tym SQLite, bez WAL/SHM i archiwów.",
    "combined": "Pakuje system oraz pamięć w jednym logicznym zestawie ZIP.",
}

# Wykluczenia nie są listą historycznych nazw paczek. Są zasadami klas plików.
COMMON_FORBIDDEN_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".pytest-tmp",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".archives",
    "backups",
    "backups_git",
    "exports",
}

SYSTEM_FORBIDDEN_ROOTS = {
    "memory",
    "workspace_runtime",
    "processed",
    "requests",
    "responses",
    "status",
    "checkpoints",
}

SYSTEM_FORBIDDEN_FILE_NAMES = {
    PACKAGE_INTEGRITY_MANIFEST.lower(),
    "manifest_current.json",
    "version.txt",
    "runtime_state.json",
    "jazn_active_runtime.json",
    "bootstrap_jazn_current.json",
    "active_runtime_cache_contract.json",
    "__jazn_pack_generator.lock.json",
    "__jazn_pack_generator_settings.json",
}

COMMON_FORBIDDEN_SUFFIXES = (
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".patch",
    ".rej",
    ".orig",
    ".bak",
    ".bad",
    ".corrupt",
    ".tmp",
    ".temp",
    ".partial",
    ".log",
    ".pyc",
    ".pyo",
)

SYSTEM_DATABASE_SUFFIXES = (
    ".sqlite",
    ".sqlite3",
    ".db",
)

TRANSIENT_DATABASE_SUFFIXES = (
    ".sqlite-wal",
    ".sqlite-shm",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".db-wal",
    ".db-shm",
    "-wal",
    "-shm",
)

SECRET_EXACT_NAMES = {
    ".env",
    "credentials.json",
    "client_secret.json",
    "service_account.json",
    "id_rsa",
    "id_ed25519",
}

SECRET_NAME_TOKENS = (
    "private_key",
    "client_secret",
    "service_account_key",
)


class PackError(RuntimeError):
    """Błąd kontrolowany generatora."""


@dataclass(frozen=True, slots=True)
class VersionInfo:
    version_file: Path
    package_version: str
    release_name: str
    full_version: str
    filename_version: str


@dataclass(frozen=True, slots=True)
class PlanEntry:
    relative: str
    source: Path | None
    size_bytes: int
    sha256: str
    classification: str
    mtime_ns: int | None = None
    virtual_bytes: bytes | None = None

    @property
    def is_virtual(self) -> bool:
        return self.virtual_bytes is not None


@dataclass(slots=True)
class PackPlan:
    root: Path
    profile: str
    version: VersionInfo
    entries: list[PlanEntry]
    excluded: list[tuple[str, str]] = field(default_factory=list)
    scan_method: str = "filesystem"
    manifest_builder: str = "internal"
    generated_at_utc: str = field(default_factory=lambda: utc_now())

    @property
    def file_count(self) -> int:
        return len(self.entries)

    @property
    def total_size(self) -> int:
        return sum(item.size_bytes for item in self.entries)

    @property
    def paths(self) -> list[str]:
        return [item.relative for item in self.entries]

    def plan_sha256(self) -> str:
        canonical = [
            {
                "path": item.relative,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "classification": item.classification,
            }
            for item in sorted(self.entries, key=lambda x: x.relative)
        ]
        raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class OutputPart:
    filename: str
    size_bytes: int
    sha256: str
    part_no: int
    is_complete_zip: bool


@dataclass(slots=True)
class PackageResult:
    package_name: str
    profile: str
    archive_format: str
    plan: PackPlan
    outputs: list[OutputPart]
    logical_zip_sha256: str | None
    package_set_sha256: str
    sidecar_path: Path
    committed_paths: list[Path]


@dataclass(slots=True)
class PackOptions:
    source: Path
    out_dir: Path
    profile: str = DEFAULT_PROFILE
    archive_format: str = DEFAULT_FORMAT
    archive_basename: str = "jazn_latka"
    part_size_mb: int = DEFAULT_PART_SIZE_MB
    compression_level: int = DEFAULT_COMPRESSION_LEVEL
    force: bool = False
    base_excludes: list[str] = field(default_factory=lambda: list(DEFAULT_BASE_EXCLUDES))
    custom_excludes: list[str] = field(default_factory=list)
    manual_excludes_enabled: bool = False
    sidecars: bool = True
    update_source_manifest: bool = True
    compatibility_checks: bool = True


@dataclass(slots=True)
class InteractiveState:
    source: Path
    out_dir: Path
    profile: str = DEFAULT_PROFILE
    archive_format: str = DEFAULT_FORMAT
    archive_basename: str = "jazn_latka"
    part_size_mb: int = DEFAULT_PART_SIZE_MB
    compression_level: int = DEFAULT_COMPRESSION_LEVEL
    force: bool = False
    base_excludes: list[str] = field(default_factory=lambda: list(DEFAULT_BASE_EXCLUDES))
    custom_excludes: list[str] = field(default_factory=list)
    manual_excludes_enabled: bool = False
    sidecars: bool = True
    update_source_manifest: bool = True
    compatibility_checks: bool = True
    ui_mode: str = "tekstowy"
    ui_auto_start: bool = False
    dirty: bool = False

    def to_options(self) -> PackOptions:
        return PackOptions(
            source=self.source,
            out_dir=self.out_dir,
            profile=self.profile,
            archive_format=self.archive_format,
            archive_basename=self.archive_basename,
            part_size_mb=self.part_size_mb,
            compression_level=self.compression_level,
            force=self.force,
            base_excludes=list(self.base_excludes),
            custom_excludes=list(self.custom_excludes),
            manual_excludes_enabled=bool(self.manual_excludes_enabled and self.custom_excludes),
            sidecars=self.sidecars,
            update_source_manifest=self.update_source_manifest,
            compatibility_checks=self.compatibility_checks,
        )


class UserCancelledInput(Exception):
    """Powrót z bieżącego pola lub podmenu bez zmiany."""


class UserRequestedExit(Exception):
    """Ctrl+X: zamknij interfejs bez automatycznego zapisu ustawień."""


# -----------------------------------------------------------------------------
# Podstawowe narzędzia
# -----------------------------------------------------------------------------


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def human_size(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{value} B"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_rel(value: str) -> str:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or raw.startswith(("/", "\\")):
        raise PackError(f"Niebezpieczna ścieżka bezwzględna: {value!r}")
    if len(raw) >= 2 and raw[1] == ":":
        raise PackError(f"Niebezpieczna ścieżka dyskowa: {value!r}")
    if "\x00" in raw or any(part in {"", ".", ".."} for part in path.parts):
        raise PackError(f"Niebezpieczna ścieżka względna: {value!r}")
    return path.as_posix()


def safe_destination_path(destination: Path, relative: str) -> Path:
    relative = normalize_rel(relative)
    target = (destination / Path(*PurePosixPath(relative).parts)).resolve()
    destination = destination.resolve()
    try:
        target.relative_to(destination)
    except ValueError as exc:
        raise PackError(f"Ścieżka wychodzi poza katalog docelowy: {relative}") from exc
    return target


def ensure_output_outside_source(source: Path, out_dir: Path) -> None:
    source = source.resolve()
    out_dir = out_dir.resolve()
    try:
        out_dir.relative_to(source)
    except ValueError:
        return
    raise PackError("Folder wyjściowy nie może znajdować się wewnątrz folderu źródłowego.")


def sanitize_archive_stem(value: str) -> str:
    raw = str(value or "jazn_latka").strip().strip('"\'')
    raw = re.sub(r"\s+", "_", raw)
    raw = raw[:-4] if raw.lower().endswith(".zip") else raw
    if not raw:
        raise PackError("Nazwa paczki jest pusta.")
    if any(ch in raw for ch in '\\/:*?"<>|'):
        raise PackError(f"Nazwa paczki zawiera niedozwolone znaki: {raw!r}")
    return raw


def safe_zip_datetime(path: Path | None = None) -> tuple[int, int, int, int, int, int]:
    if path is not None:
        try:
            tm = time.localtime(path.stat().st_mtime)
            return (min(max(tm.tm_year, 1980), 2107), tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec)
        except OSError:
            pass
    now = time.localtime()
    return (min(max(now.tm_year, 1980), 2107), now.tm_mon, now.tm_mday, now.tm_hour, now.tm_min, now.tm_sec)


def make_zipinfo(entry: PlanEntry, compression_level: int) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(entry.relative, date_time=safe_zip_datetime(entry.source))
    zi.compress_type = zipfile.ZIP_DEFLATED
    if hasattr(zi, "compress_level"):
        try:
            zi.compress_level = compression_level  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        zi._compresslevel = compression_level  # type: ignore[attr-defined]
    except Exception:
        pass
    zi.file_size = entry.size_bytes
    zi.external_attr = (0o100644 & 0xFFFF) << 16
    return zi


_OPERATION_OUTPUT = threading.local()


def _set_operation_output_sink(sink: Any | None) -> None:
    _OPERATION_OUTPUT.sink = sink


def _emit_operation_line(message: object) -> None:
    sink = getattr(_OPERATION_OUTPUT, "sink", None)
    if sink is not None:
        sink(str(message))
    else:
        print(str(message), flush=True)


def print_progress(done: int, total: int, label: str) -> None:
    total = max(total, 1)
    done = max(0, min(done, total))
    width = 28
    filled = int(width * done / total)
    bar = "█" * filled + "░" * (width - filled)
    percent = int(done * 100 / total)
    sink = getattr(_OPERATION_OUTPUT, "sink", None)
    if sink is not None:
        sink(f"{label}: {done}/{total} ({percent:3d}%)")
    else:
        print(f"\r{label}: {bar} {percent:3d}%", end="\n" if done == total else "", flush=True)


# -----------------------------------------------------------------------------
# Wersja runtime
# -----------------------------------------------------------------------------


def _literal_assignments(path: Path) -> dict[str, str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except Exception as exc:
        raise PackError(f"Nie można odczytać {path}: {exc}") from exc
    values: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.AST] = []
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value_node = node.value
        if not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = value_node.value.strip()
    return values


def normalize_version(value: str) -> str:
    """Normalizuje wersję pakietu do postaci bez początkowego ``v``.

    Funkcja pozostaje publicznym helperem kompatybilności dla testów i narzędzi
    wydaniowych. Nie zmienia sufiksu ``PACKAGE_RELEASE_NAME``.
    """

    version = str(value or "").strip().strip('"\'')
    version = re.sub(r"^v", "", version, flags=re.IGNORECASE)
    if not version:
        raise ValueError("Wersja z version.py jest pusta")
    if any(ch in version for ch in '\\/:*?"<>|'):
        raise ValueError(f"Wersja zawiera znaki niedozwolone w nazwie pliku: {version!r}")
    return version


def normalize_release_name(value: str | None) -> str:
    """Zachowuje czytelną nazwę wydania dla runtime i manifestu."""

    release = str(value or "").strip().strip('"\'')
    if not release:
        return ""
    if any(ch in release for ch in '\\/:*?"<>|'):
        raise ValueError(
            f"PACKAGE_RELEASE_NAME zawiera znaki niedozwolone: {release!r}"
        )
    return re.sub(r"\s+", " ", release).strip("-_. ")


def sanitize_release_name_for_filename(value: str | None) -> str:
    """Tworzy bezpieczny sufiks nazwy pliku bez zmiany wersji runtime."""

    release = normalize_release_name(value)
    return re.sub(r"\s+", "-", release).strip("-_.") if release else ""


def compose_runtime_version_full(
    package_version: str,
    package_release_name: str | None = None,
) -> str:
    """Pełna wersja runtime; zachowuje spacje w nazwie wydania."""

    version = str(package_version or "").strip().strip('"\'')
    if not version:
        raise ValueError("PACKAGE_VERSION jest pusty")
    if any(ch in version for ch in '\\/:*?"<>|'):
        raise ValueError(f"PACKAGE_VERSION zawiera niedozwolone znaki: {version!r}")
    release = normalize_release_name(package_release_name)
    if not release:
        return version
    suffix = f"-{release}"
    if version.lower().endswith(suffix.lower()):
        return version
    return f"{version}{suffix}"


def compose_package_version_full(
    package_version: str,
    package_release_name: str | None = None,
) -> str:
    """Wersja bezpieczna dla nazwy pliku, bez początkowego ``v``."""

    version = normalize_version(package_version)
    release = sanitize_release_name_for_filename(package_release_name)
    if not release:
        return version
    suffix = f"-{release}"
    if version.lower().endswith(suffix.lower()):
        return version
    return f"{version}{suffix}"


def manifest_version_matches(
    manifest_version: str,
    package_version: str,
    package_release_name: str | None = None,
) -> bool:
    """Porównuje manifest z dokładną pełną wersją runtime."""

    expected = compose_runtime_version_full(package_version, package_release_name)
    return normalize_version(manifest_version) == normalize_version(expected)


def read_version_info(root: Path) -> VersionInfo:
    version_file = root / "latka_jazn" / "version.py"
    if not version_file.is_file():
        raise PackError(f"Brak wymaganego pliku: {version_file}")
    values = _literal_assignments(version_file)
    package_version = (
        values.get("PACKAGE_VERSION")
        or values.get("DISTRIBUTION_VERSION")
        or values.get("__version__")
        or values.get("VERSION")
        or ""
    ).strip()
    if not package_version:
        raise PackError("latka_jazn/version.py nie zawiera literału PACKAGE_VERSION/DISTRIBUTION_VERSION.")
    release = normalize_release_name(values.get("PACKAGE_RELEASE_NAME", ""))
    full = compose_runtime_version_full(package_version, release)
    filename_version = compose_package_version_full(package_version, release)
    if any(ch in filename_version for ch in '\\/:*?"<>|'):
        raise PackError(f"Wersja nie nadaje się do nazwy pliku: {filename_version!r}")
    return VersionInfo(
        version_file=version_file.resolve(),
        package_version=package_version,
        release_name=release,
        full_version=full,
        filename_version=filename_version,
    )


def archived_version_from_bytes(raw: bytes) -> str:
    try:
        tree = ast.parse(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise PackError(f"Nie można odczytać wersji z ZIP-a: {exc}") from exc
    values: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.AST] = []
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value_node = node.value
        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    values[target.id] = value_node.value.strip()
    base = values.get("PACKAGE_VERSION") or values.get("DISTRIBUTION_VERSION") or values.get("__version__") or values.get("VERSION")
    if not base:
        raise PackError("Archiwalny version.py nie zawiera wersji.")
    return compose_runtime_version_full(base, values.get("PACKAGE_RELEASE_NAME", ""))


# -----------------------------------------------------------------------------
# Skanowanie i polityka planu
# -----------------------------------------------------------------------------


def git_is_available(root: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode != 0:
            return False
        top = Path(completed.stdout.decode("utf-8", "replace").strip()).resolve()
        return top == root.resolve()
    except (OSError, ValueError):
        return False


def _git_paths(root: Path, *args: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise PackError(f"git ls-files nie powiódł się: {detail}")
    result: list[str] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        result.append(normalize_rel(raw.decode("utf-8", "surrogateescape")))
    return result


def discover_filesystem_candidates(root: Path, subtree: str | None = None) -> tuple[list[str], str]:
    """Skanuje zwykły system plików, niezależnie od reguł .gitignore.

    Dla pamięci jest to celowe: memory/ jest zazwyczaj ignorowane przez Git,
    ale profil memory ma właśnie wykonywać jej kopię. Nadal obowiązuje późniejsza
    centralna polityka wykluczająca WAL/SHM, archiwa, cache i sekrety.
    """

    root = root.resolve()
    scan_root = root
    label = "filesystem:rglob"
    if subtree:
        relative_subtree = normalize_rel(subtree.rstrip("/"))
        scan_root = root / Path(*PurePosixPath(relative_subtree).parts)
        label = f"filesystem:rglob({relative_subtree}/;gitignore-bypassed)"
        if not scan_root.exists():
            return [], label + ":missing"
        if not scan_root.is_dir():
            raise PackError(f"Oczekiwano katalogu pamięci, ale ścieżka nie jest katalogiem: {scan_root}")

    candidates: list[str] = []
    for path in scan_root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            candidates.append(normalize_rel(path.relative_to(root).as_posix()))
    return sorted(set(candidates)), label


def discover_candidates(root: Path) -> tuple[list[str], str]:
    """Kandydaci systemowi: Git-aware w repozytorium, filesystem poza Git."""

    if git_is_available(root):
        tracked = _git_paths(root, "--cached")
        missing = [rel for rel in tracked if not (root / Path(*PurePosixPath(rel).parts)).is_file()]
        if missing:
            raise PackError(
                "Śledzone pliki są usunięte z working tree. Przed pakowaniem zatwierdź albo cofnij usunięcia: "
                + ", ".join(missing[:10])
            )
        others = _git_paths(root, "--others", "--exclude-standard")
        return sorted(set(tracked) | set(others)), "git:tracked+untracked-nonignored"

    return discover_filesystem_candidates(root)


def discover_memory_candidates(root: Path) -> tuple[list[str], str]:
    """Kandydaci pamięci: zawsze bezpośrednio z memory/, także gdy Git ją ignoruje."""

    return discover_filesystem_candidates(root, "memory")


def matches_custom_exclude(relative: str, patterns: Iterable[str]) -> str | None:
    name = PurePosixPath(relative).name
    for raw in patterns:
        pattern = str(raw).strip().replace("\\", "/")
        if not pattern:
            continue
        pattern = pattern.lstrip("/")
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern):
            return raw
        if pattern.endswith("/"):
            folder = pattern.rstrip("/")
            if relative == folder or relative.startswith(folder + "/"):
                return raw
    return None


def common_forbidden_reason(relative: str) -> str | None:
    """Niezmienialne reguły bezpieczeństwa, niezależne od list użytkownika."""

    relative = normalize_rel(relative)
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    name = parts[-1]

    if any(part in {".git", ".hg", ".svn", ".venv", "venv"} for part in parts[:-1]):
        return "immutable_repository_or_environment_directory"
    if name in SECRET_EXACT_NAMES and name != ".env.example":
        return "secret_file"
    if any(token in name for token in SECRET_NAME_TOKENS):
        return "secret_name"
    if name.endswith(TRANSIENT_DATABASE_SUFFIXES):
        return "transient_database_file"
    if ".zip." in name:
        return "split_or_nested_archive"
    if any(name.endswith(suffix) for suffix in (
        ".zip", ".7z", ".rar", ".tar", ".tar.gz", ".tgz", ".bz2", ".xz"
    )):
        return "nested_archive"
    if name.endswith(".before.py"):
        return "backup_source_file"
    return None


def profile_forbidden_reason(relative: str, profile: str) -> str | None:
    relative = normalize_rel(relative)
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    root_name = parts[0]
    name = parts[-1]

    if profile == "memory":
        if root_name != "memory":
            return "outside_memory_profile"
        if relative == MEMORY_PACKAGE_MANIFEST:
            return "virtual_manifest_replaces_source"
        return common_forbidden_reason(relative)

    common = common_forbidden_reason(relative)
    if common:
        return common
    if root_name in SYSTEM_FORBIDDEN_ROOTS:
        return "runtime_or_memory_root"
    if name in SYSTEM_FORBIDDEN_FILE_NAMES:
        return "runtime_or_legacy_marker"
    if any(name.endswith(suffix) for suffix in SYSTEM_DATABASE_SUFFIXES):
        return "database_outside_memory"
    return None


def filter_candidates(
    candidates: Iterable[str],
    *,
    profile: str,
    base_excludes: Iterable[str],
    custom_excludes: Iterable[str],
    manual_excludes_enabled: bool,
) -> tuple[list[str], list[tuple[str, str]]]:
    selected: list[str] = []
    excluded: list[tuple[str, str]] = []
    for relative in candidates:
        base = matches_custom_exclude(relative, base_excludes)
        if base is not None:
            excluded.append((relative, f"base:{base}"))
            continue
        if manual_excludes_enabled:
            custom = matches_custom_exclude(relative, custom_excludes)
            if custom is not None:
                excluded.append((relative, f"manual:{custom}"))
                continue
        reason = profile_forbidden_reason(relative, profile)
        if reason:
            excluded.append((relative, reason))
            continue
        selected.append(relative)
    return sorted(set(selected)), excluded


def hash_source_entry(root: Path, relative: str, classification: str) -> PlanEntry:
    path = (root / Path(*PurePosixPath(relative).parts)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PackError(f"Ścieżka wychodzi poza root: {relative}") from exc
    if not path.is_file() or path.is_symlink():
        raise PackError(f"Planowana ścieżka nie jest zwykłym plikiem: {relative}")
    stat = path.stat()
    digest = sha256_file(path)
    stat_after = path.stat()
    if stat.st_size != stat_after.st_size or stat.st_mtime_ns != stat_after.st_mtime_ns:
        raise PackError(f"Plik zmienił się podczas budowania planu: {relative}")
    return PlanEntry(
        relative=relative,
        source=path,
        size_bytes=stat_after.st_size,
        sha256=digest,
        classification=classification,
        mtime_ns=stat_after.st_mtime_ns,
    )


def virtual_entry(relative: str, data: bytes, classification: str) -> PlanEntry:
    return PlanEntry(
        relative=normalize_rel(relative),
        source=None,
        size_bytes=len(data),
        sha256=sha256_bytes(data),
        classification=classification,
        virtual_bytes=data,
    )


def serialize_json(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")


# -----------------------------------------------------------------------------
# Kanoniczny manifest systemowy
# -----------------------------------------------------------------------------


def source_manifest_bridge(root: Path, relative_paths: Sequence[str]) -> tuple[dict[str, Any], bytes] | None:
    """Używa modułu integralności pakowanego runtime, jeśli obsługuje exact plan."""

    bridge = r'''
import inspect
import io
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
request = json.load(sys.stdin)
sys.path.insert(0, str(root))
try:
    from latka_jazn.tools.package_integrity import build_package_integrity_manifest
except Exception as exc:
    print("__JAZN_RESULT__" + json.dumps({"available": False, "reason": f"import: {type(exc).__name__}: {exc}"}, ensure_ascii=False))
    raise SystemExit(0)

signature = inspect.signature(build_package_integrity_manifest)
if "relative_paths" not in signature.parameters:
    print("__JAZN_RESULT__" + json.dumps({"available": False, "reason": "relative_paths unsupported"}, ensure_ascii=False))
    raise SystemExit(0)

try:
    payload = build_package_integrity_manifest(root, relative_paths=request["relative_paths"])
    try:
        from latka_jazn.tools.package_integrity import serialize_package_integrity_manifest
    except Exception:
        raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    else:
        raw = serialize_package_integrity_manifest(payload)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
    result = {"available": True, "payload": payload, "manifest_text": raw.decode("utf-8")}
except Exception as exc:
    result = {"available": True, "error": f"{type(exc).__name__}: {exc}"}
print("__JAZN_RESULT__" + json.dumps(result, ensure_ascii=False))
'''
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", bridge, str(root)],
        input=json.dumps({"relative_paths": list(relative_paths)}, ensure_ascii=False),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    marker = "__JAZN_RESULT__"
    lines = [line for line in completed.stdout.splitlines() if line.startswith(marker)]
    if not lines:
        detail = (completed.stderr or completed.stdout).strip()
        raise PackError(f"Moduł integralności nie zwrócił kontraktu JSON: {detail[:1000]}")
    result = json.loads(lines[-1][len(marker):])
    if not result.get("available"):
        return None
    if result.get("error"):
        raise PackError(f"Kanoniczny moduł integralności odrzucił plan: {result['error']}")
    payload = result.get("payload")
    manifest_text = result.get("manifest_text")
    if not isinstance(payload, dict) or not isinstance(manifest_text, str):
        raise PackError("Niepełna odpowiedź modułu integralności.")
    return payload, manifest_text.encode("utf-8")


def internal_manifest_schema(root: Path) -> str:
    source = root / PACKAGE_INTEGRITY_MANIFEST
    if source.is_file():
        try:
            payload = json.loads(source.read_text(encoding="utf-8-sig"))
            value = str(payload.get("schema_version") or "").strip()
            if value:
                return value
        except Exception:
            pass
    return "package_integrity_manifest/v2"


def build_internal_system_manifest(
    root: Path,
    version: VersionInfo,
    entries: Sequence[PlanEntry],
    excluded: Sequence[tuple[str, str]],
) -> tuple[dict[str, Any], bytes]:
    files = [
        {
            "path": item.relative,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "mutable_runtime": False,
            "classification": "static_project_file",
            "archive": False,
            "hash_policy": "sha256_file_bytes",
        }
        for item in sorted(entries, key=lambda x: x.relative)
    ]
    present = {item["path"] for item in files}
    missing = sorted(REQUIRED_SYSTEM_PATHS - present)
    if missing:
        raise PackError(f"Brak wymaganych plików systemowych: {missing}")
    generated = utc_now()
    payload: dict[str, Any] = {
        "schema_version": internal_manifest_schema(root),
        "version": version.full_version,
        "runtime_version": version.full_version,
        "package_version": version.full_version,
        "generated_at_utc": generated,
        "updated_at_utc": generated,
        "start_file": "run.py",
        "file_count": len(files),
        "static_file_count": len(files),
        "mutable_runtime_file_count": 0,
        "runtime_mutable_file_count": 0,
        "excluded_file_count": len(excluded),
        "runtime_state_file": "RUNTIME_STATE.json",
        "runtime_memory_split_policy": {
            "static_manifest": "PACKAGE_INTEGRITY_MANIFEST.json protects static project files only.",
            "runtime_state": "Runtime state, memory, SQLite and workspace_runtime are excluded.",
        },
        "excluded_policy": {
            "roots": sorted(SYSTEM_FORBIDDEN_ROOTS | COMMON_FORBIDDEN_DIR_NAMES),
            "file_names": sorted(SYSTEM_FORBIDDEN_FILE_NAMES),
            "suffixes": sorted(set(COMMON_FORBIDDEN_SUFFIXES + SYSTEM_DATABASE_SUFFIXES + TRANSIENT_DATABASE_SUFFIXES)),
        },
        "truth_boundary": (
            "The manifest hashes the exact canonical static plan. It excludes itself, Git history, memory, "
            "runtime state, SQLite, archives, secrets, logs, backups and temporary files."
        ),
        "files": files,
        "excluded_files": [path for path, _ in excluded],
        "deferred_hash_files": [],
    }
    return payload, serialize_json(payload)


def build_system_plan(
    root: Path,
    version: VersionInfo,
    candidates: Sequence[str],
    excluded: list[tuple[str, str]],
    scan_method: str,
) -> PackPlan:
    # Źródłowy manifest nigdy nie jest kopiowany. Zastąpi go świeży plik wirtualny.
    candidates = [path for path in candidates if path != PACKAGE_INTEGRITY_MANIFEST]

    bridge_result = source_manifest_bridge(root, candidates)
    entries: list[PlanEntry] = []
    if bridge_result is not None:
        payload, manifest_bytes = bridge_result
        manifest_files = payload.get("files")
        if not isinstance(manifest_files, list):
            raise PackError("Kanoniczny manifest nie zawiera listy files.")
        candidate_set = set(candidates)
        for item in manifest_files:
            if not isinstance(item, dict):
                raise PackError("Niepoprawny wpis files w kanonicznym manifeście.")
            relative = normalize_rel(str(item.get("path") or ""))
            if relative not in candidate_set:
                raise PackError(f"Manifest wskazuje plik spoza zaakceptowanych kandydatów: {relative}")
            entry = hash_source_entry(root, relative, "static_project_file")
            expected_size = int(item.get("size_bytes", -1))
            expected_hash = str(item.get("sha256") or "").lower()
            if expected_size != entry.size_bytes or expected_hash != entry.sha256:
                raise PackError(f"Manifest źródłowy rozjechał się z working tree: {relative}")
            entries.append(entry)
        builder = "latka_jazn.tools.package_integrity:relative_paths"
    else:
        total = len(candidates)
        for index, relative in enumerate(candidates, start=1):
            entries.append(hash_source_entry(root, relative, "static_project_file"))
            if index % 50 == 0 or index == total:
                print_progress(index, total, "Hash system")
        payload, manifest_bytes = build_internal_system_manifest(root, version, entries, excluded)
        builder = "internal_fallback"

    present = {item.relative for item in entries}
    missing = sorted(REQUIRED_SYSTEM_PATHS - present)
    if missing:
        raise PackError(f"Kanoniczny plan nie zawiera wymaganych plików: {missing}")

    manifest_version = str(payload.get("runtime_version") or payload.get("version") or "")
    if manifest_version != version.full_version:
        # Dopuszczamy jedynie różnicę w wiodącym v.
        if re.sub(r"^v", "", manifest_version, flags=re.I) != re.sub(r"^v", "", version.full_version, flags=re.I):
            raise PackError(
                "Wersja świeżego manifestu różni się od version.py: "
                f"manifest={manifest_version!r}, version.py={version.full_version!r}"
            )

    entries.append(virtual_entry(PACKAGE_INTEGRITY_MANIFEST, manifest_bytes, "package_integrity_manifest"))
    entries.sort(key=lambda x: x.relative)
    return PackPlan(
        root=root,
        profile="system",
        version=version,
        entries=entries,
        excluded=excluded,
        scan_method=scan_method,
        manifest_builder=builder,
    )


def build_memory_plan(
    root: Path,
    version: VersionInfo,
    candidates: Sequence[str],
    excluded: list[tuple[str, str]],
    scan_method: str,
) -> PackPlan:
    candidates = [path for path in candidates if path != MEMORY_PACKAGE_MANIFEST]
    entries: list[PlanEntry] = []
    total = len(candidates)
    for index, relative in enumerate(candidates, start=1):
        entries.append(hash_source_entry(root, relative, "memory_file"))
        if index % 20 == 0 or index == total:
            print_progress(index, total, "Hash memory")

    files = [
        {
            "path": item.relative,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "classification": item.classification,
        }
        for item in sorted(entries, key=lambda x: x.relative)
    ]
    payload = {
        "schema_version": MEMORY_MANIFEST_SCHEMA,
        "runtime_version": version.full_version,
        "generated_at_utc": utc_now(),
        "file_count": len(files),
        "files": files,
        "excluded_files": [path for path, _ in excluded if path.startswith("memory/")],
        "truth_boundary": "This manifest protects memory/ files and excludes transient WAL/SHM and nested archives.",
    }
    entries.append(virtual_entry(MEMORY_PACKAGE_MANIFEST, serialize_json(payload), "memory_package_manifest"))
    entries.sort(key=lambda x: x.relative)
    return PackPlan(
        root=root,
        profile="memory",
        version=version,
        entries=entries,
        excluded=excluded,
        scan_method=scan_method,
        manifest_builder="internal_memory_manifest",
    )


def _empty_memory_profile_error(
    root: Path,
    discovered: Sequence[str],
    excluded: Sequence[tuple[str, str]],
    scan_method: str,
) -> PackError:
    memory_root = root / "memory"
    if not memory_root.exists():
        return PackError(
            "Profil memory wymaga katalogu memory/ w wybranym rootcie. "
            f"Nie znaleziono: {memory_root}"
        )
    if not memory_root.is_dir():
        return PackError(f"Ścieżka memory istnieje, ale nie jest katalogiem: {memory_root}")
    reasons: dict[str, int] = {}
    for _, reason in excluded:
        reasons[reason] = reasons.get(reason, 0) + 1
    reason_text = ", ".join(f"{name}={count}" for name, count in sorted(reasons.items())) or "brak"
    return PackError(
        "Profil memory nie znalazł plików dopuszczonych do pakowania. "
        f"Skan={scan_method}; znaleziono surowo={len(discovered)}; wykluczenia: {reason_text}"
    )


def build_plan(
    root: Path,
    profile: str,
    custom_excludes: Sequence[str],
    *,
    base_excludes: Sequence[str] | None = None,
    manual_excludes_enabled: bool = True,
) -> PackPlan:
    root = root.expanduser().resolve()
    if profile not in {"system", "memory", "combined"}:
        raise PackError(f"Niepoprawny profil planu: {profile}")
    version = read_version_info(root)
    base_excludes = list(DEFAULT_BASE_EXCLUDES if base_excludes is None else base_excludes)

    if profile == "system":
        system_candidates, system_scan_method = discover_candidates(root)
        selected, excluded = filter_candidates(
            system_candidates,
            profile="system",
            base_excludes=base_excludes,
            custom_excludes=custom_excludes,
            manual_excludes_enabled=manual_excludes_enabled,
        )
        return build_system_plan(root, version, selected, excluded, system_scan_method)

    if profile == "memory":
        memory_candidates, memory_scan_method = discover_memory_candidates(root)
        selected, excluded = filter_candidates(
            memory_candidates,
            profile="memory",
            base_excludes=base_excludes,
            custom_excludes=custom_excludes,
            manual_excludes_enabled=manual_excludes_enabled,
        )
        if not selected:
            raise _empty_memory_profile_error(
                root,
                memory_candidates,
                excluded,
                memory_scan_method,
            )
        return build_memory_plan(root, version, selected, excluded, memory_scan_method)

    # combined celowo używa dwóch źródeł kandydatów:
    # - system: tracked + untracked non-ignored,
    # - pamięć: bezpośredni filesystem memory/, nawet jeśli Git ją ignoruje.
    system_candidates, system_scan_method = discover_candidates(root)
    memory_candidates, memory_scan_method = discover_memory_candidates(root)
    system_selected, system_excluded = filter_candidates(
        system_candidates,
        profile="system",
        base_excludes=base_excludes,
        custom_excludes=custom_excludes,
        manual_excludes_enabled=manual_excludes_enabled,
    )
    memory_selected, memory_excluded = filter_candidates(
        memory_candidates,
        profile="memory",
        base_excludes=base_excludes,
        custom_excludes=custom_excludes,
        manual_excludes_enabled=manual_excludes_enabled,
    )
    system_plan = build_system_plan(
        root,
        version,
        system_selected,
        system_excluded,
        system_scan_method,
    )
    memory_plan = (
        build_memory_plan(
            root,
            version,
            memory_selected,
            memory_excluded,
            memory_scan_method,
        )
        if memory_selected
        else None
    )
    entries = list(system_plan.entries)
    if memory_plan:
        entries.extend(memory_plan.entries)
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in entries:
        if item.relative in seen:
            duplicates.append(item.relative)
        seen.add(item.relative)
    if duplicates:
        raise PackError("Duplikaty w planie combined: " + ", ".join(sorted(set(duplicates))))
    entries.sort(key=lambda x: x.relative)
    combined_scan_method = (
        f"system={system_scan_method};memory={memory_scan_method}"
    )
    return PackPlan(
        root=root,
        profile="combined",
        version=version,
        entries=entries,
        excluded=system_excluded + memory_excluded,
        scan_method=combined_scan_method,
        manifest_builder=system_plan.manifest_builder + "+memory",
    )


# -----------------------------------------------------------------------------
# Zapis ZIP
# -----------------------------------------------------------------------------


def verify_source_unchanged(entry: PlanEntry) -> None:
    if entry.source is None:
        return
    stat = entry.source.stat()
    if stat.st_size != entry.size_bytes or stat.st_mtime_ns != entry.mtime_ns:
        raise PackError(f"Plik zmienił się po zatwierdzeniu planu: {entry.relative}")


def write_entry(zf: zipfile.ZipFile, entry: PlanEntry, compression_level: int) -> str:
    verify_source_unchanged(entry)
    digest = hashlib.sha256()
    zi = make_zipinfo(entry, compression_level)
    zip64_limit = int(getattr(zipfile, "ZIP64_LIMIT", (1 << 31) - 1))
    with zf.open(zi, "w", force_zip64=entry.size_bytes >= zip64_limit) as target:
        if entry.virtual_bytes is not None:
            data = entry.virtual_bytes
            for offset in range(0, len(data), CHUNK_SIZE):
                chunk = data[offset: offset + CHUNK_SIZE]
                digest.update(chunk)
                target.write(chunk)
        else:
            assert entry.source is not None
            with entry.source.open("rb") as source:
                for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
                    target.write(chunk)
    actual = digest.hexdigest()
    if actual != entry.sha256:
        raise PackError(f"Hash pliku zmienił się podczas pakowania: {entry.relative}")
    verify_source_unchanged(entry)
    return actual


def write_zip_file(path: Path, entries: Sequence[PlanEntry], compression_level: int) -> None:
    with zipfile.ZipFile(
        path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=compression_level,
        allowZip64=True,
        strict_timestamps=False,
    ) as zf:
        total = len(entries)
        for index, entry in enumerate(entries, start=1):
            write_entry(zf, entry, compression_level)
            if index % 20 == 0 or index == total:
                print_progress(index, total, "Pakowanie")


def split_group_by_size(entries: Sequence[PlanEntry]) -> tuple[list[PlanEntry], list[PlanEntry]]:
    total = sum(max(item.size_bytes, 1) for item in entries)
    target = total / 2
    current = 0
    split_at = 1
    for index, item in enumerate(entries[:-1], start=1):
        current += max(item.size_bytes, 1)
        split_at = index
        if current >= target:
            break
    return list(entries[:split_at]), list(entries[split_at:])


def initial_groups(entries: Sequence[PlanEntry], part_size: int) -> list[list[PlanEntry]]:
    groups: list[list[PlanEntry]] = []
    current: list[PlanEntry] = []
    current_size = 0
    for entry in entries:
        estimate = entry.size_bytes + 2048
        if current and current_size + estimate > part_size:
            groups.append(current)
            current = []
            current_size = 0
        current.append(entry)
        current_size += estimate
    if current:
        groups.append(current)
    return groups or [[]]


def independent_volume_name(base_zip_name: str, number: int) -> str:
    if number == 1:
        return base_zip_name
    stem = base_zip_name[:-4]
    return f"{stem}.part{number:03d}.zip"


class SplitPartWriter:
    """Nie-seekowalny writer: jeden logiczny ZIP trafia do .001, .002..."""

    def __init__(self, out_dir: Path, base_zip_name: str, part_size: int):
        self.out_dir = out_dir
        self.base_zip_name = base_zip_name
        self.part_size = part_size
        self.total_written = 0
        self.current_no = 0
        self.current_written = 0
        self.current: BinaryIO | None = None
        self.current_hash: Any | None = None
        self.logical_hash = hashlib.sha256()
        self.parts: list[OutputPart] = []
        self.closed = False

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readable(self) -> bool:
        return False

    def tell(self) -> int:
        return self.total_written

    def flush(self) -> None:
        if self.current:
            self.current.flush()

    def write(self, data: bytes | bytearray | memoryview) -> int:
        if self.closed:
            raise ValueError("write to closed SplitPartWriter")
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            if self.current is None:
                self._open_next()
            free = self.part_size - self.current_written
            if free <= 0:
                self._close_current()
                continue
            take = min(free, len(view) - offset)
            chunk = view[offset: offset + take]
            assert self.current is not None and self.current_hash is not None
            self.current.write(chunk)
            self.current_hash.update(chunk)
            self.logical_hash.update(chunk)
            self.current_written += take
            self.total_written += take
            offset += take
            if self.current_written >= self.part_size:
                self._close_current()
        return len(view)

    def close(self) -> None:
        if self.closed:
            return
        self._close_current()
        self.closed = True

    def _open_next(self) -> None:
        self.current_no += 1
        self.current_written = 0
        path = self.out_dir / f"{self.base_zip_name}.{self.current_no:03d}"
        self.current = path.open("xb")
        self.current_hash = hashlib.sha256()

    def _close_current(self) -> None:
        if self.current is None:
            return
        self.current.flush()
        self.current.close()
        assert self.current_hash is not None
        path = self.out_dir / f"{self.base_zip_name}.{self.current_no:03d}"
        self.parts.append(OutputPart(
            filename=path.name,
            size_bytes=path.stat().st_size,
            sha256=self.current_hash.hexdigest(),
            part_no=self.current_no,
            is_complete_zip=False,
        ))
        self.current = None
        self.current_hash = None
        self.current_written = 0


class SplitPartsReader:
    """Seekowalny czytnik logicznego pliku z kolejnych części binarnych."""

    def __init__(self, paths: Sequence[Path]):
        if not paths:
            raise PackError("Brak części binarnych.")
        self.paths = list(paths)
        self.sizes = [path.stat().st_size for path in self.paths]
        self.ends: list[int] = []
        total = 0
        for size in self.sizes:
            total += size
            self.ends.append(total)
        self.total_size = total
        self.position = 0
        self.handle: BinaryIO | None = None
        self.handle_index = -1
        self.closed = False

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            position = offset
        elif whence == os.SEEK_CUR:
            position = self.position + offset
        elif whence == os.SEEK_END:
            position = self.total_size + offset
        else:
            raise ValueError("invalid whence")
        if position < 0:
            raise ValueError("negative seek position")
        self.position = min(position, self.total_size)
        return self.position

    def read(self, size: int = -1) -> bytes:
        if self.closed or self.position >= self.total_size:
            return b""
        remaining = self.total_size - self.position if size is None or size < 0 else min(size, self.total_size - self.position)
        chunks: list[bytes] = []
        while remaining > 0:
            index = bisect.bisect_right(self.ends, self.position)
            start = 0 if index == 0 else self.ends[index - 1]
            local_offset = self.position - start
            available = self.sizes[index] - local_offset
            take = min(remaining, available)
            handle = self._handle_for(index)
            handle.seek(local_offset)
            chunk = handle.read(take)
            if not chunk:
                break
            chunks.append(chunk)
            self.position += len(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _handle_for(self, index: int) -> BinaryIO:
        if self.handle_index != index:
            if self.handle:
                self.handle.close()
            self.handle = self.paths[index].open("rb")
            self.handle_index = index
        assert self.handle is not None
        return self.handle

    def close(self) -> None:
        if self.handle:
            self.handle.close()
        self.handle = None
        self.closed = True

    def __enter__(self) -> "SplitPartsReader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def write_independent(
    temp_dir: Path,
    base_zip_name: str,
    plan: PackPlan,
    part_size: int,
    compression_level: int,
) -> tuple[list[OutputPart], None]:
    accepted: list[Path] = []

    def emit(entries: list[PlanEntry]) -> None:
        candidate = temp_dir / f".candidate-{uuid.uuid4().hex}.zip"
        write_zip_file(candidate, entries, compression_level)
        size = candidate.stat().st_size
        if size > part_size and len(entries) > 1:
            candidate.unlink()
            left, right = split_group_by_size(entries)
            emit(left)
            emit(right)
            return
        accepted.append(candidate)

    for group in initial_groups(plan.entries, part_size):
        emit(group)

    outputs: list[OutputPart] = []
    for index, candidate in enumerate(accepted, start=1):
        name = independent_volume_name(base_zip_name, index)
        final_temp = temp_dir / name
        os.replace(candidate, final_temp)
        outputs.append(OutputPart(
            filename=name,
            size_bytes=final_temp.stat().st_size,
            sha256=sha256_file(final_temp),
            part_no=index,
            is_complete_zip=True,
        ))
    return outputs, None


def write_binary(
    temp_dir: Path,
    base_zip_name: str,
    plan: PackPlan,
    part_size: int,
    compression_level: int,
) -> tuple[list[OutputPart], str]:
    writer = SplitPartWriter(temp_dir, base_zip_name, part_size)
    try:
        with zipfile.ZipFile(
            writer,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=compression_level,
            allowZip64=True,
            strict_timestamps=False,
        ) as zf:
            total = len(plan.entries)
            for index, entry in enumerate(plan.entries, start=1):
                write_entry(zf, entry, compression_level)
                if index % 20 == 0 or index == total:
                    print_progress(index, total, "Pakowanie")
    finally:
        writer.close()
    return list(writer.parts), writer.logical_hash.hexdigest()


# -----------------------------------------------------------------------------
# Weryfikacja
# -----------------------------------------------------------------------------


def verify_zip_names(infos: Sequence[zipfile.ZipInfo]) -> None:
    seen: set[str] = set()
    for info in infos:
        relative = normalize_rel(info.filename.rstrip("/")) if info.filename.endswith("/") else normalize_rel(info.filename)
        if info.filename in seen:
            raise PackError(f"Duplikat wpisu w ZIP-ie: {info.filename}")
        seen.add(info.filename)
        if not relative:
            raise PackError("Pusty wpis ZIP.")


def verify_zip_stream(zf: zipfile.ZipFile, plan: PackPlan) -> dict[str, Any]:
    infos = [info for info in zf.infolist() if not info.is_dir()]
    verify_zip_names(infos)
    bad = zf.testzip()
    if bad:
        raise PackError(f"Błędny CRC lub nagłówek ZIP: {bad}")

    expected = {item.relative: item for item in plan.entries}
    actual_names = {info.filename for info in infos}
    missing = sorted(set(expected) - actual_names)
    unexpected = sorted(actual_names - set(expected))
    if missing or unexpected:
        raise PackError(
            "Zawartość ZIP-a różni się od kanonicznego planu. "
            f"Brakujące={missing[:10]}, nadmiarowe={unexpected[:10]}"
        )

    for index, info in enumerate(infos, start=1):
        entry = expected[info.filename]
        if info.file_size != entry.size_bytes:
            raise PackError(f"Zły rozmiar w ZIP-ie: {info.filename}")
        digest = hashlib.sha256()
        with zf.open(info, "r") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        if digest.hexdigest() != entry.sha256:
            raise PackError(f"Zły SHA-256 w ZIP-ie: {info.filename}")
        if index % 50 == 0 or index == len(infos):
            print_progress(index, len(infos), "Weryfikacja")

    version_entry = expected.get("latka_jazn/version.py")
    if version_entry:
        archived_version = archived_version_from_bytes(zf.read("latka_jazn/version.py"))
        if archived_version != plan.version.full_version:
            if re.sub(r"^v", "", archived_version, flags=re.I) != re.sub(r"^v", "", plan.version.full_version, flags=re.I):
                raise PackError(
                    f"Wersja w ZIP-ie jest inna: {archived_version!r} != {plan.version.full_version!r}"
                )

    if PACKAGE_INTEGRITY_MANIFEST in expected:
        payload = json.loads(zf.read(PACKAGE_INTEGRITY_MANIFEST).decode("utf-8-sig"))
        manifest_paths = {str(item.get("path")) for item in payload.get("files") or [] if isinstance(item, dict)}
        static_paths = {
            item.relative
            for item in plan.entries
            if item.classification == "static_project_file"
        }
        if manifest_paths != static_paths:
            raise PackError(
                "PACKAGE_INTEGRITY_MANIFEST.json nie opisuje dokładnie statycznego planu. "
                f"missing={sorted(static_paths - manifest_paths)[:10]}, extra={sorted(manifest_paths - static_paths)[:10]}"
            )

    return {
        "ok": True,
        "files_verified": len(infos),
        "crc": "ok",
        "plan_sha256": plan.plan_sha256(),
    }


def verify_outputs(temp_dir: Path, outputs: Sequence[OutputPart], archive_format: str, plan: PackPlan) -> dict[str, Any]:
    if archive_format == "independent":
        found: set[str] = set()
        total_files = 0
        for output in outputs:
            path = temp_dir / output.filename
            if not zipfile.is_zipfile(path):
                raise PackError(f"Wynik nie jest poprawnym ZIP-em: {path.name}")
            with zipfile.ZipFile(path, "r") as zf:
                infos = [info for info in zf.infolist() if not info.is_dir()]
                verify_zip_names(infos)
                bad = zf.testzip()
                if bad:
                    raise PackError(f"CRC niepoprawny w {path.name}: {bad}")
                for info in infos:
                    if info.filename in found:
                        raise PackError(f"Duplikat między woluminami: {info.filename}")
                    found.add(info.filename)
                total_files += len(infos)
        if found != set(plan.paths):
            raise PackError(
                "Zestaw niezależnych ZIP-ów różni się od planu. "
                f"missing={sorted(set(plan.paths)-found)[:10]}, extra={sorted(found-set(plan.paths))[:10]}"
            )
        # Jedna logiczna walidacja hashów przez ponowne otwarcie każdego woluminu.
        expected = {item.relative: item for item in plan.entries}
        for output in outputs:
            with zipfile.ZipFile(temp_dir / output.filename, "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    entry = expected[info.filename]
                    digest = hashlib.sha256()
                    with zf.open(info) as handle:
                        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != entry.sha256 or info.file_size != entry.size_bytes:
                        raise PackError(f"Niezgodny plik w {output.filename}: {info.filename}")
        # Manifest i wersję sprawdzamy na wirtualnym logicznym widoku.
        manifest_volume = next((temp_dir / item.filename for item in outputs if _zip_contains(temp_dir / item.filename, PACKAGE_INTEGRITY_MANIFEST)), None)
        if manifest_volume:
            with zipfile.ZipFile(manifest_volume, "r") as zf:
                archived_version = archived_version_from_bytes(_read_member_from_set(temp_dir, outputs, "latka_jazn/version.py"))
                if re.sub(r"^v", "", archived_version, flags=re.I) != re.sub(r"^v", "", plan.version.full_version, flags=re.I):
                    raise PackError("Wersja archiwalna nie zgadza się z planem.")
                payload = json.loads(zf.read(PACKAGE_INTEGRITY_MANIFEST).decode("utf-8-sig"))
                manifest_paths = {str(item.get("path")) for item in payload.get("files") or [] if isinstance(item, dict)}
                static_paths = {item.relative for item in plan.entries if item.classification == "static_project_file"}
                if manifest_paths != static_paths:
                    raise PackError("Manifest systemowy nie zgadza się ze statycznym planem.")
        return {"ok": True, "volumes": len(outputs), "files": total_files, "crc": "ok"}

    paths = [temp_dir / item.filename for item in outputs]
    with SplitPartsReader(paths) as reader:
        with zipfile.ZipFile(reader, "r") as zf:
            return verify_zip_stream(zf, plan)


def _zip_contains(path: Path, member: str) -> bool:
    with zipfile.ZipFile(path, "r") as zf:
        return member in zf.namelist()


def _read_member_from_set(temp_dir: Path, outputs: Sequence[OutputPart], member: str) -> bytes:
    for output in outputs:
        with zipfile.ZipFile(temp_dir / output.filename, "r") as zf:
            if member in zf.namelist():
                return zf.read(member)
    raise PackError(f"Brak wpisu {member} w zestawie ZIP-ów.")


# -----------------------------------------------------------------------------
# Sidecar, transakcja i nazwy
# -----------------------------------------------------------------------------


def choose_format(requested: str, plan: PackPlan, part_size: int) -> str:
    if requested in {"independent", "binary"}:
        return requested

    # Pamięć jest jednym logicznym backupem. Gdy przekracza limit części,
    # auto zachowuje zgodność z 1.2_FINAL i wybiera .zip.001/.002...
    if plan.profile == "memory" and plan.total_size > part_size:
        return "binary"

    # Pojedynczego pliku większego od limitu nie da się umieścić w
    # samodzielnym woluminie mieszczącym się w limicie.
    if any(item.size_bytes > part_size for item in plan.entries):
        return "binary"
    return "independent"


def package_set_hash(outputs: Sequence[OutputPart]) -> str:
    digest = hashlib.sha256()
    for item in sorted(outputs, key=lambda x: x.part_no):
        digest.update(f"{item.part_no}\0{item.filename}\0{item.size_bytes}\0{item.sha256}\n".encode("utf-8"))
    return digest.hexdigest()


def sidecar_payload(
    base_zip_name: str,
    plan: PackPlan,
    archive_format: str,
    part_size: int,
    compression_level: int,
    outputs: Sequence[OutputPart],
    logical_zip_sha256: str | None,
    verification: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": PACKAGE_SET_SCHEMA,
        "generator": Path(__file__).name,
        "generator_version": GENERATOR_VERSION,
        "created_at_utc": utc_now(),
        "source_root": str(plan.root),
        "package_name": base_zip_name,
        "profile": plan.profile,
        "archive_format": archive_format,
        "package_version": plan.version.full_version,
        "part_size_bytes": part_size,
        "compression": "ZIP_DEFLATED",
        "compression_level": compression_level,
        "scan_method": plan.scan_method,
        "manifest_builder": plan.manifest_builder,
        "plan_sha256": plan.plan_sha256(),
        "entry_count": plan.file_count,
        "source_total_size_bytes": plan.total_size,
        "logical_zip_sha256": logical_zip_sha256,
        "package_set_sha256": package_set_hash(outputs),
        "outputs": [
            {
                "part_no": item.part_no,
                "filename": item.filename,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "is_complete_zip": item.is_complete_zip,
            }
            for item in outputs
        ],
        "entries": [
            {
                "path": item.relative,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "classification": item.classification,
            }
            for item in plan.entries
        ],
        "excluded_count": len(plan.excluded),
        "excluded_sample": [
            {"path": path, "reason": reason}
            for path, reason in plan.excluded[:1000]
        ],
        "verification": verification,
    }


def write_join_ps1(temp_dir: Path, base_zip_name: str, outputs: Sequence[OutputPart], logical_hash: str) -> Path:
    path = temp_dir / f"{base_zip_name}.join.ps1"
    rows = "\n".join(
        f"    @{{ Name = '{item.filename.replace("'", "''")}'; Sha256 = '{item.sha256}' }}"
        for item in outputs
    )
    content = f'''# Łączy binarne części jednego ZIP-a i sprawdza SHA-256.
$ErrorActionPreference = "Stop"
$BaseZip = "{base_zip_name}"
$ExpectedFull = "{logical_hash}"
$Parts = @(
{rows}
)

foreach ($Part in $Parts) {{
    $Path = Join-Path $PSScriptRoot $Part.Name
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {{ throw "Brak części: $($Part.Name)" }}
    $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Part.Sha256) {{ throw "Zły SHA256 części: $($Part.Name)" }}
}}

$Out = Join-Path $PSScriptRoot $BaseZip
$Tmp = "$Out.joining.tmp"
if (Test-Path -LiteralPath $Tmp) {{ Remove-Item -LiteralPath $Tmp -Force }}
$Target = [System.IO.File]::Open($Tmp, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
try {{
    foreach ($Part in $Parts) {{
        $Source = [System.IO.File]::OpenRead((Join-Path $PSScriptRoot $Part.Name))
        try {{ $Source.CopyTo($Target) }} finally {{ $Source.Dispose() }}
    }}
}} finally {{ $Target.Dispose() }}
$ActualFull = (Get-FileHash -LiteralPath $Tmp -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualFull -ne $ExpectedFull) {{ Remove-Item -LiteralPath $Tmp -Force; throw "Zły SHA256 pełnego ZIP-a" }}
Move-Item -LiteralPath $Tmp -Destination $Out -Force
Write-Host "Gotowe: $Out"
'''
    path.write_text(content, encoding="utf-8")
    return path


def known_output_paths(out_dir: Path, base_zip_name: str) -> list[Path]:
    stem = re.escape(base_zip_name[:-4])
    base = re.escape(base_zip_name)
    patterns = [
        re.compile(rf"^{base}$", re.I),
        re.compile(rf"^{stem}\.part\d{{3}}\.zip$", re.I),
        re.compile(rf"^{base}\.\d{{3}}$", re.I),
        re.compile(rf"^{base}\.(?:package\.json|join\.ps1|parts\.sha256|sha256)$", re.I),
    ]
    result: list[Path] = []
    if not out_dir.exists():
        return result
    for path in out_dir.iterdir():
        if path.is_file() and any(pattern.match(path.name) for pattern in patterns):
            result.append(path)
    return sorted(result)


def create_pack_staging_dir(out_dir: Path, base_zip_name: str) -> Path:
    """Tworzy katalog roboczy bez przenoszenia restrykcyjnego ACL do wyników.

    Python 3.13+ na Windows nadaje katalogom tworzonym przez tempfile.mkdtemp()
    (mode 0o700) ACL ograniczony do bieżącego użytkownika i administratorów.
    Plik przeniesiony w obrębie tego samego woluminu zachowuje deskryptor
    bezpieczeństwa, dlatego wcześniejsza wersja mogła publikować ZIP-y i
    sidecary z nieoczekiwanymi, niedziedziczonymi uprawnieniami.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    mode = 0o777 if os.name == "nt" else 0o700
    for _ in range(128):
        candidate = out_dir / f".{base_zip_name}.packing-{uuid.uuid4().hex}"
        try:
            candidate.mkdir(mode=mode)
            return candidate
        except FileExistsError:
            continue
    raise PackError("Nie można utworzyć unikalnego katalogu roboczego paczki.")


def _clear_readonly_flag(path: Path) -> None:
    """Usuwa wyłącznie atrybut read-only; nie modyfikuje ACL/DACL."""

    if os.name != "nt":
        return
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    except OSError as exc:
        raise PackError(f"Nie można usunąć atrybutu tylko-do-odczytu: {path}: {exc}") from exc


def _publish_with_destination_acl(source: Path, target: Path) -> None:
    """Publikuje atomowo przez nowy plik utworzony bezpośrednio w out_dir.

    Nowy plik dziedziczy ACL katalogu docelowego. Nie przenosimy deskryptora
    bezpieczeństwa pliku z katalogu stagingowego. Zawartość jest kopiowana
    strumieniowo, flushowana i fsyncowana przed atomowym os.replace().
    """

    if not source.is_file():
        raise PackError(f"Brak pliku stagingowego do publikacji: {source}")
    temp_target = target.with_name(f".{target.name}.publishing-{uuid.uuid4().hex}.tmp")
    expected_size = source.stat().st_size
    copied = 0
    digest_source = hashlib.sha256()
    digest_target = hashlib.sha256()
    try:
        with source.open("rb") as input_handle, temp_target.open("xb") as output_handle:
            for chunk in iter(lambda: input_handle.read(CHUNK_SIZE), b""):
                digest_source.update(chunk)
                output_handle.write(chunk)
                digest_target.update(chunk)
                copied += len(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        if copied != expected_size or digest_target.digest() != digest_source.digest():
            raise PackError(f"Publikowany plik różni się od stagingu: {source.name}")
        _clear_readonly_flag(temp_target)
        os.replace(temp_target, target)
        _clear_readonly_flag(target)
        source.unlink()
    except Exception:
        temp_target.unlink(missing_ok=True)
        raise


def commit_transaction(temp_dir: Path, out_dir: Path, filenames: Sequence[str], base_zip_name: str, force: bool) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = known_output_paths(out_dir, base_zip_name)
    if existing and not force:
        raise PackError(
            "Istnieją wcześniejsze pliki tej paczki. Użyj --force albo zmień nazwę:\n"
            + "\n".join(f"  - {path}" for path in existing)
        )

    backup = out_dir / f".{base_zip_name}.backup-{uuid.uuid4().hex}"
    moved_existing: list[tuple[Path, Path]] = []
    committed: list[Path] = []
    try:
        if existing:
            backup.mkdir()
            for old in existing:
                target = backup / old.name
                os.replace(old, target)
                moved_existing.append((target, old))
        for filename in filenames:
            source = temp_dir / filename
            target = out_dir / filename
            _publish_with_destination_acl(source, target)
            committed.append(target)
        if backup.exists():
            shutil.rmtree(backup)
        return committed
    except Exception:
        for target in committed:
            target.unlink(missing_ok=True)
        for stored, original in reversed(moved_existing):
            if stored.exists():
                os.replace(stored, original)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        raise


def _tool_candidates(names: Sequence[str], common_paths: Sequence[str] = ()) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        for raw in common_paths:
            path = Path(os.path.expandvars(raw))
            if path.is_file():
                return str(path)
    return None


def _run_tool_check(name: str, command: Sequence[str], timeout: int = 180) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"tool": name, "status": "failed", "detail": f"{type(exc).__name__}: {exc}"}
    output = (completed.stdout or "").strip().replace("\x00", "")
    return {
        "tool": name,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "detail": output[-1200:],
        "command": [str(item) for item in command],
    }


def _joined_binary_zip(temp_dir: Path, outputs: Sequence[OutputPart]) -> Path:
    target = temp_dir / ".compatibility-joined.zip"
    with target.open("wb") as out_handle:
        for item in sorted(outputs, key=lambda value: value.part_no):
            with (temp_dir / item.filename).open("rb") as in_handle:
                shutil.copyfileobj(in_handle, out_handle, length=CHUNK_SIZE)
    return target


def run_compatibility_matrix(
    temp_dir: Path,
    outputs: Sequence[OutputPart],
    archive_format: str,
) -> dict[str, Any]:
    """Testuje standardowy ZIP w Pythonie i dostępnych zewnętrznych archiwizatorach.

    Brak zainstalowanego programu jest raportowany jako ``skipped``. Wykryty
    program, który odrzuca ZIP, zatrzymuje publikację paczki.
    """

    archive_paths: list[Path]
    joined: Path | None = None
    if archive_format == "binary":
        joined = _joined_binary_zip(temp_dir, outputs)
        archive_paths = [joined]
    else:
        archive_paths = [temp_dir / item.filename for item in outputs]

    results: list[dict[str, Any]] = []
    for archive in archive_paths:
        try:
            with zipfile.ZipFile(archive, "r") as zf:
                bad = zf.testzip()
                if bad:
                    raise zipfile.BadZipFile(f"CRC failed: {bad}")
                # Wymusza dekompresję każdego wpisu, nie tylko odczyt katalogu.
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    with zf.open(info, "r") as handle:
                        while handle.read(CHUNK_SIZE):
                            pass
            results.append({"tool": "python.zipfile", "status": "passed", "archive": archive.name})
        except Exception as exc:
            results.append({"tool": "python.zipfile", "status": "failed", "archive": archive.name, "detail": f"{type(exc).__name__}: {exc}"})

        seven = _tool_candidates(
            ("7z", "7zz", "7za"),
            (r"%ProgramFiles%\7-Zip\7z.exe", r"%ProgramFiles(x86)%\7-Zip\7z.exe"),
        )
        if seven:
            result = _run_tool_check("7-Zip", [seven, "t", "-bd", "-y", str(archive)])
            result["archive"] = archive.name
            results.append(result)
        else:
            results.append({"tool": "7-Zip", "status": "skipped", "detail": "program nie jest zainstalowany lub nie znajduje się w PATH"})

        rar = _tool_candidates(
            ("rar", "WinRAR", "winrar"),
            (
                r"%ProgramFiles%\WinRAR\Rar.exe",
                r"%ProgramFiles%\WinRAR\WinRAR.exe",
                r"%ProgramFiles(x86)%\WinRAR\Rar.exe",
                r"%ProgramFiles(x86)%\WinRAR\WinRAR.exe",
            ),
        )
        if rar:
            result = _run_tool_check("WinRAR/RAR", [rar, "t", "-inul", str(archive)])
            result["archive"] = archive.name
            results.append(result)
        else:
            results.append({"tool": "WinRAR/RAR", "status": "skipped", "detail": "program nie jest zainstalowany lub nie znajduje się w PATH"})

        unzip = _tool_candidates(("unzip",))
        if unzip:
            result = _run_tool_check("Info-ZIP unzip", [unzip, "-tqq", str(archive)])
            result["archive"] = archive.name
            results.append(result)
        else:
            results.append({"tool": "Info-ZIP unzip", "status": "skipped", "detail": "unzip nie znajduje się w PATH"})

        zip_cli = _tool_candidates(("zip",))
        if zip_cli:
            result = _run_tool_check("Info-ZIP zip", [zip_cli, "-T", str(archive)])
            result["archive"] = archive.name
            results.append(result)
        else:
            results.append({"tool": "Info-ZIP zip", "status": "skipped", "detail": "zip nie znajduje się w PATH"})

        wzunzip = _tool_candidates(
            ("wzunzip", "WZUNZIP"),
            (
                r"%ProgramFiles%\WinZip\WZUNZIP.EXE",
                r"%ProgramFiles(x86)%\WinZip\WZUNZIP.EXE",
            ),
        )
        if wzunzip:
            extract_dir = temp_dir / f".winzip-check-{uuid.uuid4().hex}"
            extract_dir.mkdir()
            result = _run_tool_check("WinZip WZUNZIP", [wzunzip, str(archive), str(extract_dir)])
            result["archive"] = archive.name
            results.append(result)
            shutil.rmtree(extract_dir, ignore_errors=True)
        else:
            results.append({"tool": "WinZip WZUNZIP", "status": "skipped", "detail": "WinZip Command Line Add-On nie jest zainstalowany"})

    if joined is not None:
        joined.unlink(missing_ok=True)
    failed = [item for item in results if item.get("status") == "failed"]
    return {
        "ok": not failed,
        "results": results,
        "failed_tools": [str(item.get("tool")) for item in failed],
    }


def write_source_manifest_from_plan(plan: PackPlan) -> Path | None:
    manifest_entry = next(
        (entry for entry in plan.entries if entry.relative == PACKAGE_INTEGRITY_MANIFEST),
        None,
    )
    if manifest_entry is None or manifest_entry.virtual_bytes is None:
        return None
    target = plan.root / PACKAGE_INTEGRITY_MANIFEST
    temp = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
    temp.write_bytes(manifest_entry.virtual_bytes)
    try:
        os.replace(temp, target)
    except PermissionError:
        target.write_bytes(manifest_entry.virtual_bytes)
        temp.unlink(missing_ok=True)
    if target.read_bytes() != manifest_entry.virtual_bytes:
        raise PackError("Zapisany PACKAGE_INTEGRITY_MANIFEST.json różni się od manifestu planu.")
    return target


def package_one(plan: PackPlan, options: PackOptions, base_zip_name: str) -> PackageResult:
    part_size = options.part_size_mb * 1024 * 1024
    archive_format = choose_format(options.archive_format, plan, part_size)
    options.out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = create_pack_staging_dir(options.out_dir, base_zip_name)
    try:
        _emit_operation_line(f"Paczka: {base_zip_name}")
        _emit_operation_line(f"Profil: {plan.profile}")
        _emit_operation_line(f"Format: {archive_format}")
        _emit_operation_line(f"Plan: {plan.file_count} plików, {human_size(plan.total_size)}")
        _emit_operation_line(f"Plan SHA-256: {plan.plan_sha256()}")

        if archive_format == "independent":
            outputs, logical_hash = write_independent(
                temp_dir, base_zip_name, plan, part_size, options.compression_level
            )
        else:
            outputs, logical_hash = write_binary(
                temp_dir, base_zip_name, plan, part_size, options.compression_level
            )

        verification = verify_outputs(temp_dir, outputs, archive_format, plan)
        if options.compatibility_checks:
            compatibility = run_compatibility_matrix(temp_dir, outputs, archive_format)
            verification["compatibility"] = compatibility
            if not compatibility.get("ok"):
                raise PackError(
                    "Test zgodności ZIP nie przeszedł: "
                    + ", ".join(compatibility.get("failed_tools") or [])
                )
        else:
            verification["compatibility"] = {"ok": None, "status": "disabled"}
        payload = sidecar_payload(
            base_zip_name,
            plan,
            archive_format,
            part_size,
            options.compression_level,
            outputs,
            logical_hash,
            verification,
        )
        sidecar_name = f"{base_zip_name}.package.json"
        sidecar_temp = temp_dir / sidecar_name
        sidecar_temp.write_bytes(serialize_json(payload))

        # package.json jest częścią kontraktu paczki i powstaje zawsze.
        # --no-sidecars wyłącza tylko dodatkowe pliki tekstowe/join.ps1.
        extra_names: list[str] = [sidecar_name]
        if options.sidecars:
            (temp_dir / f"{base_zip_name}.parts.sha256").write_text(
                "".join(f"{item.sha256}  {item.filename}\n" for item in outputs),
                encoding="ascii",
            )
            extra_names.append(f"{base_zip_name}.parts.sha256")
            if logical_hash:
                (temp_dir / f"{base_zip_name}.sha256").write_text(
                    f"{logical_hash}  {base_zip_name}\n", encoding="ascii"
                )
                extra_names.append(f"{base_zip_name}.sha256")
                join_path = write_join_ps1(temp_dir, base_zip_name, outputs, logical_hash)
                extra_names.append(join_path.name)

        filenames = [item.filename for item in outputs] + extra_names
        committed = commit_transaction(
            temp_dir,
            options.out_dir,
            filenames,
            base_zip_name,
            options.force,
        )
        sidecar_final = options.out_dir / sidecar_name
        return PackageResult(
            package_name=base_zip_name,
            profile=plan.profile,
            archive_format=archive_format,
            plan=plan,
            outputs=list(outputs),
            logical_zip_sha256=logical_hash,
            package_set_sha256=package_set_hash(outputs),
            sidecar_path=sidecar_final,
            committed_paths=committed,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def package_names(stem: str, version: VersionInfo, profile: str) -> dict[str, str]:
    stem = sanitize_archive_stem(stem)
    base = f"{stem}_v{version.filename_version}"
    if profile == "dual":
        return {
            "system": f"{base}_system.zip",
            "memory": f"{base}_memory.zip",
        }
    if profile == "memory":
        return {"memory": f"{base}_memory.zip"}
    return {profile: f"{base}.zip"}


def build_plans_for_options(options: PackOptions) -> list[PackPlan]:
    """Buduje plan tylko raz. Wynik może zostać pokazany i bezpośrednio spakowany."""

    source = options.source.expanduser().resolve()
    out_dir = options.out_dir.expanduser().resolve()
    ensure_output_outside_source(source, out_dir)
    if options.profile == "dual":
        plans = [build_plan(
            source, "system", options.custom_excludes,
            base_excludes=options.base_excludes,
            manual_excludes_enabled=options.manual_excludes_enabled,
        )]
        try:
            plans.append(build_plan(
                source, "memory", options.custom_excludes,
                base_excludes=options.base_excludes,
                manual_excludes_enabled=options.manual_excludes_enabled,
            ))
        except PackError as exc:
            _emit_operation_line(f"UWAGA: pomijam paczkę pamięci: {exc}")
        return plans
    return [build_plan(
        source, options.profile, options.custom_excludes,
        base_excludes=options.base_excludes,
        manual_excludes_enabled=options.manual_excludes_enabled,
    )]


def run_pack_with_plans(options: PackOptions, plans: Sequence[PackPlan]) -> list[PackageResult]:
    """Pakuje dokładnie zatwierdzone obiekty planu — bez ponownego skanowania."""

    source = options.source.expanduser().resolve()
    out_dir = options.out_dir.expanduser().resolve()
    ensure_output_outside_source(source, out_dir)
    version = read_version_info(source)
    names = package_names(options.archive_basename, version, options.profile)

    expected_profiles = {"system", "memory"} if options.profile == "dual" else {options.profile}
    actual_profiles = {plan.profile for plan in plans}
    if not plans or not actual_profiles.issubset(expected_profiles):
        raise PackError(
            f"Zatwierdzone plany nie pasują do profilu {options.profile!r}: {sorted(actual_profiles)}"
        )

    results: list[PackageResult] = []
    for plan in plans:
        if plan.root.resolve() != source:
            raise PackError("Zatwierdzony plan pochodzi z innego folderu źródłowego.")
        if plan.version.full_version != version.full_version:
            raise PackError(
                "Wersja zmieniła się po zatwierdzeniu planu: "
                f"plan={plan.version.full_version!r}, teraz={version.full_version!r}"
            )
        plan_hash_before = plan.plan_sha256()
        name = names.get(plan.profile)
        if not name:
            raise PackError(f"Brak nazwy wynikowej dla profilu planu: {plan.profile}")
        if options.update_source_manifest and plan.profile in {"system", "combined"}:
            manifest_path = write_source_manifest_from_plan(plan)
            if manifest_path:
                _emit_operation_line(f"Zaktualizowano manifest źródłowy: {manifest_path}")
        result = package_one(plan, options, name)
        if result.plan is not plan or plan.plan_sha256() != plan_hash_before:
            raise PackError("Plan został zmieniony podczas pakowania.")
        results.append(result)
    return results


def run_pack(options: PackOptions) -> list[PackageResult]:
    """CLI: zbuduj plany raz, a następnie spakuj dokładnie te same obiekty."""

    return run_pack_with_plans(options, build_plans_for_options(options))


# -----------------------------------------------------------------------------
# Verify/extract z sidecara
# -----------------------------------------------------------------------------


def load_sidecar(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise PackError(f"Nie można odczytać sidecara {path}: {exc}") from exc
    if payload.get("schema_version") != PACKAGE_SET_SCHEMA:
        raise PackError(f"Nieobsługiwany schema_version: {payload.get('schema_version')!r}")
    return payload


def sidecar_outputs(path: Path, payload: dict[str, Any]) -> list[OutputPart]:
    outputs: list[OutputPart] = []
    for item in payload.get("outputs") or []:
        outputs.append(OutputPart(
            filename=str(item["filename"]),
            size_bytes=int(item["size_bytes"]),
            sha256=str(item["sha256"]),
            part_no=int(item["part_no"]),
            is_complete_zip=bool(item.get("is_complete_zip")),
        ))
    outputs.sort(key=lambda x: x.part_no)
    if not outputs:
        raise PackError("Sidecar nie zawiera outputs.")
    for item in outputs:
        file_path = path.parent / item.filename
        if not file_path.is_file():
            raise PackError(f"Brak pliku paczki: {item.filename}")
        if file_path.stat().st_size != item.size_bytes:
            raise PackError(f"Zły rozmiar: {item.filename}")
        if sha256_file(file_path) != item.sha256:
            raise PackError(f"Zły SHA-256: {item.filename}")
    return outputs


def expected_entries_from_sidecar(payload: dict[str, Any]) -> dict[str, tuple[int, str]]:
    expected: dict[str, tuple[int, str]] = {}
    for item in payload.get("entries") or []:
        relative = normalize_rel(str(item["path"]))
        expected[relative] = (int(item["size_bytes"]), str(item["sha256"]))
    if not expected:
        raise PackError("Sidecar nie zawiera entries.")
    return expected


def verify_zip_against_sidecar(zf: zipfile.ZipFile, expected: dict[str, tuple[int, str]]) -> None:
    infos = [info for info in zf.infolist() if not info.is_dir()]
    verify_zip_names(infos)
    bad = zf.testzip()
    if bad:
        raise PackError(f"CRC niepoprawny: {bad}")
    names = {info.filename for info in infos}
    if names != set(expected):
        raise PackError(
            f"Zawartość różni się od sidecara: missing={sorted(set(expected)-names)[:10]}, extra={sorted(names-set(expected))[:10]}"
        )
    for info in infos:
        size, digest_expected = expected[info.filename]
        if info.file_size != size:
            raise PackError(f"Zły rozmiar wpisu: {info.filename}")
        digest = hashlib.sha256()
        with zf.open(info) as handle:
            for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        if digest.hexdigest() != digest_expected:
            raise PackError(f"Zły hash wpisu: {info.filename}")


def verify_package_sidecar(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    payload = load_sidecar(path)
    outputs = sidecar_outputs(path, payload)
    expected = expected_entries_from_sidecar(payload)
    archive_format = str(payload.get("archive_format"))

    if archive_format == "binary":
        logical_expected = str(payload.get("logical_zip_sha256") or "")
        digest = hashlib.sha256()
        paths = [path.parent / item.filename for item in outputs]
        for part in paths:
            with part.open("rb") as handle:
                for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
        if logical_expected and digest.hexdigest() != logical_expected:
            raise PackError("Zły SHA-256 logicznego ZIP-a.")
        with SplitPartsReader(paths) as reader:
            with zipfile.ZipFile(reader, "r") as zf:
                verify_zip_against_sidecar(zf, expected)
    elif archive_format == "independent":
        found: set[str] = set()
        for output in outputs:
            with zipfile.ZipFile(path.parent / output.filename, "r") as zf:
                infos = [info for info in zf.infolist() if not info.is_dir()]
                verify_zip_names(infos)
                bad = zf.testzip()
                if bad:
                    raise PackError(f"CRC niepoprawny w {output.filename}: {bad}")
                for info in infos:
                    if info.filename in found:
                        raise PackError(f"Duplikat między woluminami: {info.filename}")
                    found.add(info.filename)
                    if info.filename not in expected:
                        raise PackError(f"Nadmiarowy wpis: {info.filename}")
                    size, digest_expected = expected[info.filename]
                    if info.file_size != size:
                        raise PackError(f"Zły rozmiar: {info.filename}")
                    digest = hashlib.sha256()
                    with zf.open(info) as handle:
                        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != digest_expected:
                        raise PackError(f"Zły hash: {info.filename}")
        if found != set(expected):
            raise PackError(f"Brakujące wpisy: {sorted(set(expected)-found)[:10]}")
    else:
        raise PackError(f"Nieznany archive_format: {archive_format}")

    return {
        "ok": True,
        "package_name": payload.get("package_name"),
        "profile": payload.get("profile"),
        "archive_format": archive_format,
        "outputs": len(outputs),
        "entries": len(expected),
    }


def prepare_destination(destination: Path, clean: bool, force: bool) -> None:
    destination = destination.expanduser().resolve()
    dangerous = {Path(destination.anchor).resolve(), Path.home().resolve()}
    if clean and destination.exists():
        if destination in dangerous or len(destination.parts) < 3:
            raise PackError(f"Odmawiam --clean dla zbyt szerokiego celu: {destination}")
        shutil.rmtree(destination)
    if destination.exists() and any(destination.iterdir()) and not force:
        raise PackError("Folder docelowy nie jest pusty. Użyj --force albo --clean.")
    destination.mkdir(parents=True, exist_ok=True)


def extract_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, destination: Path, force: bool) -> None:
    target = safe_destination_path(destination, info.filename)
    if info.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        raise PackError(f"Plik docelowy istnieje: {target}")
    temp = target.with_name(target.name + f".extract-{uuid.uuid4().hex}.tmp")
    with zf.open(info, "r") as source, temp.open("xb") as output:
        shutil.copyfileobj(source, output, length=CHUNK_SIZE)
    os.replace(temp, target)


def extract_package_sidecar(path: Path, destination: Path, *, clean: bool, force: bool) -> dict[str, Any]:
    report = verify_package_sidecar(path)
    payload = load_sidecar(path)
    outputs = sidecar_outputs(path, payload)
    prepare_destination(destination, clean, force)
    archive_format = str(payload.get("archive_format"))

    if archive_format == "binary":
        paths = [path.parent / item.filename for item in outputs]
        with SplitPartsReader(paths) as reader:
            with zipfile.ZipFile(reader, "r") as zf:
                for info in zf.infolist():
                    extract_member(zf, info, destination, force=True)
    else:
        seen: set[str] = set()
        for output in outputs:
            with zipfile.ZipFile(path.parent / output.filename, "r") as zf:
                for info in zf.infolist():
                    if not info.is_dir() and info.filename in seen:
                        raise PackError(f"Duplikat przy ekstrakcji: {info.filename}")
                    if not info.is_dir():
                        seen.add(info.filename)
                    extract_member(zf, info, destination, force=True)
    report["destination"] = str(destination.resolve())
    return report


# -----------------------------------------------------------------------------
# Interfejs legacy — zachowany dla zgodności trybu tekstowego
# -----------------------------------------------------------------------------

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_CYAN = "\033[36m"
ANSI_BRIGHT_CYAN = "\033[96m"
ANSI_GREEN = "\033[32m"
ANSI_BRIGHT_GREEN = "\033[92m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_BRIGHT_BLACK = "\033[90m"


def _enable_windows_vt() -> None:
    if os.name == "nt":
        try:
            os.system("")
        except Exception:
            pass


def _color_enabled(stream: Any = None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    try:
        if not stream.isatty():
            return False
    except Exception:
        return False
    _enable_windows_vt()
    return True


def _paint(text: str, *codes: str, stream: Any = None) -> str:
    if not _color_enabled(stream):
        return text
    return "".join(codes) + text + ANSI_RESET


def _ui_width(fallback: int = 96) -> int:
    try:
        columns = shutil.get_terminal_size((fallback, 24)).columns
    except Exception:
        columns = fallback
    return max(72, min(columns, 132))


def _wrap(value: object, width: int, *, indent: str = "") -> list[str]:
    return textwrap.wrap(
        str(value), width=max(10, width), subsequent_indent=indent,
        replace_whitespace=False, drop_whitespace=True,
    ) or [""]


def ui_banner(title: str, subtitle: str = "") -> None:
    width = _ui_width()
    print()
    print(_paint("╭" + "─" * (width - 2) + "╮", ANSI_CYAN))
    print(_paint("│", ANSI_CYAN) + _paint(("  " + title)[:width - 2].ljust(width - 2), ANSI_BOLD, ANSI_BRIGHT_CYAN) + _paint("│", ANSI_CYAN))
    if subtitle:
        print(_paint("│", ANSI_CYAN) + _paint(("  " + subtitle)[:width - 2].ljust(width - 2), ANSI_DIM) + _paint("│", ANSI_CYAN))
    print(_paint("╰" + "─" * (width - 2) + "╯", ANSI_CYAN))


def ui_section(title: str) -> None:
    width = _ui_width()
    label = f" {title.strip()} "
    print("\n" + _paint(label + "─" * max(0, width - len(label)), ANSI_CYAN, ANSI_BOLD))


def ui_status(message: str, kind: str = "info") -> None:
    marker, color = {
        "ok": ("✓", ANSI_BRIGHT_GREEN), "warn": ("!", ANSI_YELLOW),
        "error": ("×", ANSI_RED), "info": ("•", ANSI_BRIGHT_CYAN),
    }.get(kind, ("•", ANSI_BRIGHT_CYAN))
    print(_paint(f"{marker} {message}", color, ANSI_BOLD if kind in {"ok", "error"} else ""))


def ui_key_value(label: str, value: object, *, label_width: int = 18) -> None:
    prefix = "  " + _paint(label.ljust(label_width), ANSI_BRIGHT_BLACK)
    available = max(12, _ui_width() - 2 - label_width)
    lines = _wrap(value, available, indent=" " * (2 + label_width))
    print(prefix + lines[0])
    for line in lines[1:]:
        print(line)


def print_plan(plan: PackPlan, show_files: bool = False) -> None:
    ui_banner(
        f"PLAN {plan.profile.upper()} — {plan.version.full_version}",
        "Zamrożony plan: ta sama lista zasila podgląd, manifest i ZIP",
    )
    ui_key_value("Źródło", plan.root)
    ui_key_value("Skanowanie", plan.scan_method)
    ui_key_value("Manifest", plan.manifest_builder)
    ui_key_value("Pliki", plan.file_count)
    ui_key_value("Rozmiar", human_size(plan.total_size))
    ui_key_value("Wykluczone", len(plan.excluded))
    ui_key_value("Plan SHA-256", plan.plan_sha256())
    if show_files:
        ui_section("PLIKI W PLANIE")
        for index, item in enumerate(plan.entries, start=1):
            print(f"  {index:>5}. [{'V' if item.is_virtual else 'F'}] {item.relative} ({human_size(item.size_bytes)})")


def settings_path() -> Path:
    return Path(__file__).resolve().with_name(SETTINGS_FILE_NAME)


def platform_default_source() -> Path:
    return Path("C:\\") if os.name == "nt" else Path("/bin/")


def default_interactive_state() -> InteractiveState:
    source = platform_default_source()
    cwd = Path.cwd().resolve()
    out_dir = cwd.parent / "packages"
    return InteractiveState(source=source, out_dir=out_dir)


def _normalize_ui_mode(value: str | None) -> str:
    aliases = {
        "plain": "tekstowy", "text": "tekstowy", "tekst": "tekstowy", "tekstowy": "tekstowy",
        "cursor": "kursorowy", "kursor": "kursorowy", "kursorowy": "kursorowy",
    }
    mode = aliases.get(str(value or "").strip().lower(), str(value or "").strip().lower())
    if mode not in UI_MODE_CHOICES:
        mode = "kursorowy" if HAS_PROMPT_TOOLKIT else "tekstowy"
    if mode == "kursorowy" and not HAS_PROMPT_TOOLKIT:
        return "tekstowy"
    return mode


def load_interactive_state() -> InteractiveState:
    state = default_interactive_state()
    path = settings_path()
    if not path.is_file():
        return state
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        ui_status(f"Nie można wczytać ustawień {path}: {exc}", "warn")
        return state
    if not isinstance(payload, dict):
        return state
    source_raw = payload.get("source") or payload.get("source_folder")
    out_raw = payload.get("out_dir") or payload.get("output_dir")
    if source_raw:
        state.source = Path(str(source_raw)).expanduser().resolve()
    if out_raw:
        state.out_dir = Path(str(out_raw)).expanduser().resolve()
    profile = str(payload.get("profile") or state.profile)
    if profile == "pelna":
        profile = "dual"
    if profile in PROFILE_CHOICES:
        state.profile = profile
    archive_format = str(payload.get("archive_format") or payload.get("format") or state.archive_format)
    if archive_format in FORMAT_CHOICES:
        state.archive_format = archive_format
    state.archive_basename = str(payload.get("archive_basename") or payload.get("name") or state.archive_basename).strip() or "jazn_latka"
    try:
        state.part_size_mb = max(1, int(payload.get("part_size_mb", state.part_size_mb)))
        level = int(payload.get("compression_level", state.compression_level))
        if 0 <= level <= 9:
            state.compression_level = level
    except (TypeError, ValueError):
        pass
    state.force = bool(payload.get("force", state.force))
    base = payload.get("base_excludes")
    if isinstance(base, list):
        state.base_excludes = [str(item).strip() for item in base if str(item).strip()]
    custom = payload.get("custom_excludes") or payload.get("exclude") or []
    if isinstance(custom, list):
        state.custom_excludes = [str(item).strip() for item in custom if str(item).strip()]
    state.manual_excludes_enabled = bool(payload.get("manual_excludes_enabled", bool(state.custom_excludes))) and bool(state.custom_excludes)
    state.sidecars = bool(payload.get("sidecars", payload.get("diagnostic_files", True)))
    state.update_source_manifest = bool(payload.get("update_source_manifest", True))
    state.compatibility_checks = bool(payload.get("compatibility_checks", True))
    state.ui_mode = _normalize_ui_mode(str(payload.get("ui_mode") or "kursorowy"))
    state.ui_auto_start = bool(payload.get("ui_auto_start", False))
    state.dirty = False
    return state


def save_interactive_state(state: InteractiveState) -> Path:
    payload = {
        "schema_version": SETTINGS_SCHEMA,
        "saved_at_utc": utc_now(),
        "generator_version": GENERATOR_VERSION,
        "source": str(state.source),
        "out_dir": str(state.out_dir),
        "profile": state.profile,
        "archive_format": state.archive_format,
        "archive_basename": state.archive_basename,
        "part_size_mb": state.part_size_mb,
        "compression_level": state.compression_level,
        "force": state.force,
        "base_excludes": list(state.base_excludes),
        "custom_excludes": list(state.custom_excludes),
        "manual_excludes_enabled": bool(state.manual_excludes_enabled and state.custom_excludes),
        "sidecars": state.sidecars,
        "update_source_manifest": state.update_source_manifest,
        "compatibility_checks": state.compatibility_checks,
        "ui_mode": state.ui_mode,
        "ui_auto_start": state.ui_auto_start,
        "appearance": "latka-cyan-v7.0.1",
    }
    path = settings_path()
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temp.write_bytes(serialize_json(payload))
    os.replace(temp, path)
    state.dirty = False
    return path


def reset_interactive_settings() -> None:
    settings_path().unlink(missing_ok=True)


def _cursor_available() -> bool:
    return bool(
        HAS_PROMPT_TOOLKIT
        and _pt_Application is not None
        and _pt_Condition is not None
        and _pt_KeyBindings is not None
        and _pt_Layout is not None
        and _pt_DynamicContainer is not None
        and _pt_HSplit is not None
        and _pt_VSplit is not None
        and _pt_Window is not None
        and _pt_FormattedTextControl is not None
        and _pt_Dimension is not None
        and _pt_ScrollablePane is not None
        and _pt_Style is not None
        and _pt_TextArea is not None
    )


def _cursor_style():
    style = APP_THEME.prompt_toolkit_style()
    if style is None:
        raise PackError("Brak prompt_toolkit.styles.Style.")
    return style


def menu_navigation_index(index: int, action: str, row_count: int, page_step: int | None = None) -> int:
    """Deterministyczna nawigacja menu używana przez klawiaturę i testy."""

    if row_count <= 0:
        return 0
    index = max(0, min(index, row_count - 1))
    step = max(1, int(page_step or APP_THEME.page_step))
    if action == "up":
        return (index - 1) % row_count
    if action == "down":
        return (index + 1) % row_count
    if action == "home":
        return 0
    if action == "end":
        return row_count - 1
    if action == "pageup":
        return max(0, index - step)
    if action == "pagedown":
        return min(row_count - 1, index + step)
    return index


def _mouse_action(mouse_event: Any) -> str | None:
    """Mapuje zdarzenie myszy na działanie niezależne od konkretnego ekranu."""

    if _pt_MouseEventType is None or _pt_MouseButton is None:
        return None
    if mouse_event.event_type == _pt_MouseEventType.SCROLL_UP:
        return "up"
    if mouse_event.event_type == _pt_MouseEventType.SCROLL_DOWN:
        return "down"
    if mouse_event.event_type == _pt_MouseEventType.MOUSE_UP:
        if mouse_event.button == _pt_MouseButton.LEFT:
            return "activate"
        if mouse_event.button == _pt_MouseButton.RIGHT:
            return "back"
    return None

def _install_right_click_back(control: Any, *, result: Any = None) -> None:
    """Dodaje PPM=wstecz do kontrolki, zachowując jej obsługę LPM i scrolla."""

    original = getattr(control, "mouse_handler", None)
    if original is None:
        return

    def _handler(mouse_event: Any):
        if _mouse_action(mouse_event) == "back":
            try:
                from prompt_toolkit.application.current import get_app
                get_app().exit(result=result)
            except Exception:
                pass
            return None
        return original(mouse_event)

    control.mouse_handler = _handler

def cursor_select(
    title: str,
    rows: Sequence[str],
    selected: int = 0,
    *,
    details: Sequence[str] | None = None,
    status_lines: Sequence[str] | None = None,
    subtitle: str = "",
    groups: dict[int, str] | None = None,
) -> int | None:
    """Pełnoekranowy wybór z działającą klawiaturą i myszą.

    LPM aktywuje wskazany wiersz, PPM wraca, kółko zmienia zaznaczenie.
    Własne eager bindings wyłączają konflikt z domyślną nawigacją stron.
    """

    if not _cursor_available():
        raise PackError("Tryb kursorowy wymaga biblioteki prompt_toolkit.")
    assert _pt_Application and _pt_KeyBindings and _pt_Layout and _pt_HSplit
    assert _pt_VSplit and _pt_Window and _pt_FormattedTextControl
    if not rows:
        return None
    index = max(0, min(selected, len(rows) - 1))
    details = list(details or [""] * len(rows))
    details.extend([""] * max(0, len(rows) - len(details)))
    status_lines = list(status_lines or [])
    groups = dict(groups or {})
    bindings = _pt_KeyBindings()

    def move(action: str, event: Any) -> None:
        nonlocal index
        index = menu_navigation_index(index, action, len(rows))
        event.app.invalidate()

    def handler_for(row_index: int):
        def _handler(mouse_event: Any):
            nonlocal index
            action = _mouse_action(mouse_event)
            if action in {"up", "down"}:
                index = menu_navigation_index(index, action, len(rows))
                try:
                    from prompt_toolkit.application.current import get_app
                    get_app().invalidate()
                except Exception:
                    pass
                return None
            if action == "back":
                try:
                    from prompt_toolkit.application.current import get_app
                    get_app().exit(result=None)
                except Exception:
                    pass
                return None
            if action == "activate":
                index = row_index
                try:
                    from prompt_toolkit.application.current import get_app
                    get_app().exit(result=row_index)
                except Exception:
                    pass
                return None
            return NotImplemented
        return _handler

    def render_header():
        parts = [("class:header.title", f"  {title}")]
        if subtitle:
            parts.append(("class:header.subtitle", f"  •  {subtitle}"))
        return parts

    def render_menu():
        fragments: list[tuple[Any, ...]] = []
        for number, row in enumerate(rows):
            section = groups.get(number)
            if section:
                if number:
                    fragments.append(("class:menu.section", "\n"))
                line = f"── {section} "
                fragments.append(("class:menu.section", "  " + line + APP_THEME.section_fill * max(3, 34 - len(line)) + "\n"))
            handler = handler_for(number)
            if number == index:
                fragments.append(("[SetCursorPosition]", ""))
                fragments.append(("class:menu.selected", "  ▶ " + row + "\n", handler))
            else:
                fragments.append(("class:menu.item", "    " + row + "\n", handler))
        return fragments

    def render_detail():
        fragments: list[tuple[str, str]] = [("class:panel.title", "  AKTUALNA KONFIGURACJA\n")]
        for line in status_lines:
            fragments.append(("class:panel.label", "  " + line + "\n"))
        fragments.append(("class:panel.rule", "\n  " + APP_THEME.section_fill * 38 + "\n"))
        fragments.append(("class:panel.title", "  WYBRANA OPCJA\n"))
        for line in _wrap(details[index] or rows[index], 64):
            fragments.append(("class:panel.text", "  " + line + "\n"))
        return fragments

    def render_footer():
        return [
            ("class:footer.key", " ↑/↓ Home/End PgUp/PgDn "), ("class:footer.text", "nawigacja  "),
            ("class:footer.key", " LPM/Enter "), ("class:footer.text", "otwórz  "),
            ("class:footer.key", " PPM/Esc "), ("class:footer.text", "wróć  "),
            ("class:footer.key", " Ctrl+X "), ("class:footer.text", "wyjście "),
        ]

    menu_control = _pt_FormattedTextControl(text=render_menu, focusable=True, show_cursor=False)
    detail_control = _pt_FormattedTextControl(text=render_detail, focusable=False, show_cursor=False)
    header_control = _pt_FormattedTextControl(text=render_header, focusable=False, show_cursor=False)
    footer_control = _pt_FormattedTextControl(text=render_footer, focusable=False, show_cursor=False)

    for key, action in (("up", "up"), ("k", "up"), ("down", "down"), ("j", "down"),
                        ("home", "home"), ("end", "end"), ("pageup", "pageup"), ("pagedown", "pagedown")):
        bindings.add(key, eager=True)(lambda event, a=action: move(a, event))

    @bindings.add("enter", eager=True)
    def _enter(event: Any) -> None:
        event.app.exit(result=index)

    @bindings.add("escape", eager=True)
    @bindings.add("q", eager=True)
    def _escape(event: Any) -> None:
        event.app.exit(result=None)

    @bindings.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=-2)

    layout = _pt_Layout(_pt_HSplit([
        _pt_Window(height=2, content=header_control, style="class:header", wrap_lines=False),
        _pt_Window(height=1, char=APP_THEME.section_fill, style="class:border"),
        _pt_VSplit([
            _pt_Window(content=menu_control, width=APP_THEME.left_dimension(), style="class:menu", wrap_lines=False, always_hide_cursor=True),
            _pt_Window(width=1, char=APP_THEME.border_char, style="class:border"),
            _pt_Window(content=detail_control, width=APP_THEME.right_dimension(), style="class:panel", wrap_lines=True, always_hide_cursor=True),
        ]),
        _pt_Window(height=1, char=APP_THEME.section_fill, style="class:border"),
        _pt_Window(height=1, content=footer_control, style="class:footer", wrap_lines=False),
    ]), focused_element=menu_control)
    app = _pt_Application(
        layout=layout, key_bindings=bindings, style=_cursor_style(),
        full_screen=True, erase_when_done=True, mouse_support=True,
        enable_page_navigation_bindings=False,
    )
    result = app.run()
    if result == -2:
        raise UserRequestedExit()
    return result


def cursor_inline_editor(title: str, label: str, current: str, *, description: str = "", path_mode: bool = False) -> str | None:
    if not _cursor_available():
        return input(f"{label}: [{current}] ").strip() or current
    assert _pt_Application and _pt_KeyBindings and _pt_Layout and _pt_HSplit and _pt_VSplit and _pt_Window and _pt_FormattedTextControl and _pt_TextArea
    bindings = _pt_KeyBindings()
    completer = _pt_PathCompleter(only_directories=True, expanduser=True) if path_mode and _pt_PathCompleter else None
    editor = _pt_TextArea(text=current, multiline=False, wrap_lines=False, completer=completer, complete_while_typing=False, style="class:input")
    _install_right_click_back(editor.control, result=None)
    header = _pt_FormattedTextControl(text=[("class:header.title", f"  {title}"), ("class:header.subtitle", "  •  edycja w trybie kursorowym")])
    detail_text = "\n".join("  " + line for line in _wrap(description, 90))
    detail = _pt_FormattedTextControl(text=[("class:panel.text", detail_text)])
    footer = _pt_FormattedTextControl(text=[
        ("class:footer.key", " Enter "), ("class:footer.text", "zatwierdź  "),
        ("class:footer.key", " Esc "), ("class:footer.text", "anuluj  "),
        ("class:footer.key", " Ctrl+X "), ("class:footer.text", "wyjście "),
    ])

    @bindings.add("enter")
    def _enter(event: Any) -> None:
        event.app.exit(result=editor.text)

    @bindings.add("escape", eager=True)
    def _escape(event: Any) -> None:
        event.app.exit(result=None)

    @bindings.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=UI_EXIT_MARKER)

    layout = _pt_Layout(_pt_HSplit([
        _pt_Window(height=2, content=header),
        _pt_Window(height=1, char="─", style="class:border"),
        _pt_VSplit([
            _pt_Window(width=max(12, len(label) + 3), content=_pt_FormattedTextControl(text=[("class:panel.title", f"  {label}: ")])),
            editor,
        ], height=1),
        _pt_Window(height=1, char="─", style="class:border"),
        _pt_Window(content=detail, wrap_lines=True),
        _pt_Window(height=1, char="─", style="class:border"),
        _pt_Window(height=1, content=footer),
    ]), focused_element=editor)
    result = _pt_Application(
        layout=layout, key_bindings=bindings, style=_cursor_style(),
        full_screen=True, erase_when_done=True, mouse_support=True,
        enable_page_navigation_bindings=False,
    ).run()
    if result == UI_EXIT_MARKER:
        raise UserRequestedExit()
    if result is None:
        return None
    value = str(result).strip()
    return value or current


def cursor_choice_editor(title: str, label: str, values: Sequence[str], labels: Sequence[str], descriptions: Sequence[str], current: int) -> int | None:
    if not _cursor_available():
        print(f"\n{title}")
        for number, item in enumerate(labels, start=1):
            marker = "*" if number - 1 == current else " "
            print(f"  {marker} {number}. {item} — {descriptions[number - 1]}")
        raw = input(f"{label} [Enter=bez zmian]: ").strip()
        if not raw:
            return None
        try:
            selected = int(raw) - 1
        except ValueError:
            return None
        return selected if 0 <= selected < len(values) else None
    assert _pt_Application and _pt_KeyBindings and _pt_Layout and _pt_HSplit and _pt_Window and _pt_FormattedTextControl
    index = max(0, min(current, len(values) - 1))
    bindings = _pt_KeyBindings()

    def line():
        return [("class:panel.title", f"  {label}: "), ("class:menu.selected", f"[{labels[index]}]")]

    def detail():
        return [("class:panel.title", "  OPIS WYBRANEJ WARTOŚCI\n\n"), ("class:panel.text", "  " + descriptions[index])]

    @bindings.add("left")
    @bindings.add("up")
    def _prev(event: Any) -> None:
        nonlocal index
        index = (index - 1) % len(values)
        event.app.invalidate()

    @bindings.add("right")
    @bindings.add("down")
    def _next(event: Any) -> None:
        nonlocal index
        index = (index + 1) % len(values)
        event.app.invalidate()

    @bindings.add("enter")
    def _enter(event: Any) -> None:
        event.app.exit(result=index)

    @bindings.add("escape", eager=True)
    def _escape(event: Any) -> None:
        event.app.exit(result=None)

    @bindings.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=-2)

    footer = _pt_FormattedTextControl(text=[
        ("class:footer.key", " ←/→ "), ("class:footer.text", "zmiana  "),
        ("class:footer.key", " Enter "), ("class:footer.text", "zatwierdź  "),
        ("class:footer.key", " Esc "), ("class:footer.text", "anuluj "),
    ])
    layout = _pt_Layout(_pt_HSplit([
        _pt_Window(height=2, content=_pt_FormattedTextControl(text=[("class:header.title", f"  {title}")])),
        _pt_Window(height=1, char="─", style="class:border"),
        _pt_Window(height=1, content=_pt_FormattedTextControl(text=line)),
        _pt_Window(height=1, char="─", style="class:border"),
        _pt_Window(content=_pt_FormattedTextControl(text=detail), wrap_lines=True),
        _pt_Window(height=1, char="─", style="class:border"),
        _pt_Window(height=1, content=footer),
    ]))
    result = _pt_Application(
        layout=layout, key_bindings=bindings, style=_cursor_style(),
        full_screen=True, erase_when_done=True, mouse_support=True,
        enable_page_navigation_bindings=False,
    ).run()
    if result == -2:
        raise UserRequestedExit()
    return result


def cursor_message_page(title: str, lines: Sequence[str] | str, *, kind: str = "info", subtitle: str = "Enter lub Esc — powrót") -> None:
    text_value = lines if isinstance(lines, str) else "\n".join(str(line) for line in lines)
    if not _cursor_available():
        ui_banner(title)
        print(text_value)
        try:
            input("\nEnter — powrót")
        except EOFError:
            pass
        return
    assert _pt_Application and _pt_KeyBindings and _pt_Layout and _pt_HSplit and _pt_Window and _pt_FormattedTextControl and _pt_TextArea
    bindings = _pt_KeyBindings()
    body = _pt_TextArea(text=text_value, read_only=True, scrollbar=True, wrap_lines=True, focusable=True)
    _install_right_click_back(body.control, result=None)

    @bindings.add("enter")
    @bindings.add("escape", eager=True)
    @bindings.add("q", eager=True)
    def _close(event: Any) -> None:
        event.app.exit(result=None)

    @bindings.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=UI_EXIT_MARKER)

    style_class = "class:message.error" if kind == "error" else "class:message.warn" if kind == "warn" else "class:message.ok" if kind == "ok" else "class:header.title"
    header = _pt_FormattedTextControl(text=[(style_class, f"  {title}"), ("class:header.subtitle", f"  •  {subtitle}")])
    footer = _pt_FormattedTextControl(text=[("class:footer.key", " Enter/Esc "), ("class:footer.text", "powrót  "), ("class:footer.key", " Ctrl+X "), ("class:footer.text", "wyjście")])
    layout = _pt_Layout(_pt_HSplit([
        _pt_Window(height=2, content=header),
        _pt_Window(height=1, char="─", style="class:border"),
        body,
        _pt_Window(height=1, char="─", style="class:border"),
        _pt_Window(height=1, content=footer),
    ]), focused_element=body)
    result = _pt_Application(
        layout=layout, key_bindings=bindings, style=_cursor_style(),
        full_screen=True, erase_when_done=True, mouse_support=True,
        enable_page_navigation_bindings=False,
    ).run()
    if result == UI_EXIT_MARKER:
        raise UserRequestedExit()


def cursor_confirm(title: str, summary: Sequence[str], *, yes_label: str = "Tak", no_label: str = "Nie") -> bool:
    rows = [yes_label, no_label]
    details = ["Kontynuuj operację.", "Wróć bez wykonania operacji."]
    choice = cursor_select(title, rows, 1, details=details, status_lines=list(summary), subtitle="Potwierdzenie")
    return choice == 0


def cursor_list_editor(title: str, items: list[str], *, description: str) -> tuple[list[str], bool]:
    changed = False
    selected = 0
    while True:
        rows = ["Dodaj nowy wzorzec", "Usuń wybrany wzorzec"]
        rows.extend(items)
        rows.append("Wróć")
        details = [
            "Dodaje nowy wpis do tej listy.",
            "Pozwala wybrać i usunąć istniejący wpis.",
        ]
        details.extend([description] * len(items))
        details.append("Powrót do poprzedniej strony.")
        choice = cursor_select(
            title, rows, min(selected, len(rows) - 1),
            details=details,
            subtitle="Dodawanie • edycja • usuwanie bez opuszczania trybu kursorowego",
            groups={0: "OPERACJE", 2: "WZORCE", len(rows) - 1: "WYJŚCIE"},
        )
        if choice is None or choice == len(rows) - 1:
            return items, changed
        selected = choice
        if choice == 0:
            value = cursor_inline_editor(title, "Nowy wzorzec", "", description=description)
            if value:
                items.append(value)
                changed = True
            continue
        if choice == 1:
            if not items:
                cursor_message_page("BRAK WPISÓW", "Lista jest pusta.", kind="warn")
                continue
            remove = cursor_select(
                f"{title} — USUWANIE", items, 0,
                details=["Usuń ten wzorzec."] * len(items),
            )
            if remove is not None:
                items.pop(remove)
                changed = True
            continue
        item_index = choice - 2
        value = cursor_inline_editor(title, "Wzorzec", items[item_index], description=description)
        if value is not None and value != items[item_index]:
            items[item_index] = value
            changed = True


def edit_exclusions_text(state: InteractiveState) -> None:
    print("\nPODSTAWOWE WYKLUCZENIA")
    print("; ".join(state.base_excludes))
    raw = input("Nowa lista podstawowa rozdzielona średnikami [Enter=bez zmian]: ").strip()
    if raw:
        state.base_excludes = [item.strip() for item in raw.split(";") if item.strip()]
        state.dirty = True
    print("\nRĘCZNE WYKLUCZENIA")
    print("; ".join(state.custom_excludes) if state.custom_excludes else "(brak)")
    raw = input("Nowa lista ręczna rozdzielona średnikami [Enter=bez zmian]: ").strip()
    if raw:
        state.custom_excludes = [item.strip() for item in raw.split(";") if item.strip()]
        state.manual_excludes_enabled = bool(state.custom_excludes)
        state.dirty = True
    if state.custom_excludes:
        toggle = input(f"Używać ręcznych? [{'T/n' if state.manual_excludes_enabled else 't/N'}]: ").strip().lower()
        if toggle in {"t", "tak", "y", "yes"}:
            state.manual_excludes_enabled = True; state.dirty = True
        elif toggle in {"n", "nie", "no"}:
            state.manual_excludes_enabled = False; state.dirty = True


def edit_exclusions_cursor(state: InteractiveState) -> None:
    selected = 0
    while True:
        manual_status = "WŁĄCZONE" if state.manual_excludes_enabled and state.custom_excludes else "WYŁĄCZONE"
        rows = [
            f"Podstawowe wykluczenia [{len(state.base_excludes)}]",
            f"Ręczne wykluczenia [{len(state.custom_excludes)}] — {manual_status}",
            "Dodaj ręczne wykluczenie",
            "Usuń wybrane ręczne wykluczenie",
            "Włącz/wyłącz ręczne wykluczenia",
            "Przywróć podstawowe domyślne",
            "Wróć",
        ]
        details = [
            "Edytowalna lista domyślnych globów. Krytyczne blokady bezpieczeństwa pozostają niezmienne.",
            "Lista ręczna jest stosowana tylko, gdy zawiera wpisy i jest włączona.",
            "Dodaje nowy glob do listy ręcznej.",
            "Otwiera listę i usuwa wskazany wpis.",
            "Zmienia aktywność listy ręcznej. Pusta lista zawsze pozostaje wyłączona.",
            "Przywraca listę podstawową dostarczoną z generatorem v7.0.1.",
            "Powrót do strony głównej.",
        ]
        choice = cursor_select("WYKLUCZENIA", rows, selected, details=details, groups={0: "LISTY", 2: "RĘCZNE", 5: "PRZYWRACANIE", 6: "WYJŚCIE"})
        if choice is None or choice == 6:
            return
        selected = choice
        if choice == 0:
            updated, changed = cursor_list_editor("PODSTAWOWE WYKLUCZENIA", list(state.base_excludes), description="Wzorce glob stosowane przed regułami profilu.")
            if changed:
                state.base_excludes = updated
                state.dirty = True
        elif choice == 1:
            updated, changed = cursor_list_editor("RĘCZNE WYKLUCZENIA", list(state.custom_excludes), description="Wzorce dodane przez użytkownika.")
            if changed:
                state.custom_excludes = updated
                state.manual_excludes_enabled = bool(updated)
                state.dirty = True
        elif choice == 2:
            value = cursor_inline_editor("DODAJ RĘCZNE WYKLUCZENIE", "Wzorzec", "", description="Przykład: docs/archive/** albo *.tmp")
            if value:
                state.custom_excludes.append(value)
                state.manual_excludes_enabled = True
                state.dirty = True
        elif choice == 3:
            if not state.custom_excludes:
                cursor_message_page("BRAK WPISÓW", "Lista ręcznych wykluczeń jest pusta.", kind="warn")
                continue
            remove = cursor_select("USUŃ RĘCZNE WYKLUCZENIE", state.custom_excludes, 0, details=["Usuń ten wzorzec."] * len(state.custom_excludes))
            if remove is not None:
                state.custom_excludes.pop(remove)
                state.manual_excludes_enabled = bool(state.custom_excludes)
                state.dirty = True
        elif choice == 4:
            if not state.custom_excludes:
                state.manual_excludes_enabled = False
                cursor_message_page("RĘCZNE WYKLUCZENIA", "Lista jest pusta, więc pozostaje wyłączona.", kind="warn")
            else:
                state.manual_excludes_enabled = not state.manual_excludes_enabled
                state.dirty = True
        elif choice == 5:
            if cursor_confirm("PRZYWRÓCIĆ DOMYŚLNE?", [f"Obecnie: {len(state.base_excludes)} wpisów", f"Domyślne: {len(DEFAULT_BASE_EXCLUDES)} wpisów"]):
                state.base_excludes = list(DEFAULT_BASE_EXCLUDES)
                state.dirty = True


def build_preview_plans(state: InteractiveState) -> list[PackPlan]:
    ui_status("Buduję kanoniczny plan i obliczam SHA-256 plików…", "info")
    plans = build_plans_for_options(state.to_options())
    return plans


def plan_summary(plans: Sequence[PackPlan]) -> list[str]:
    lines: list[str] = []
    for plan in plans:
        lines.extend([
            f"Profil: {plan.profile}",
            f"Wersja runtime: {plan.version.full_version}",
            f"Nazwa pliku: v{plan.version.filename_version}",
            f"Pliki: {plan.file_count}",
            f"Rozmiar: {human_size(plan.total_size)}",
            f"Wykluczone: {len(plan.excluded)}",
            f"Plan SHA-256: {plan.plan_sha256()}",
            "",
        ])
    return lines


def compatibility_summary(result: PackageResult) -> list[str]:
    try:
        payload = load_sidecar(result.sidecar_path)
        report = ((payload.get("verification") or {}).get("compatibility") or {})
    except Exception:
        return []
    lines = ["Zgodność archiwum:"]
    for item in report.get("results") or []:
        lines.append(f"  {item.get('tool')}: {item.get('status')}")
    return lines


def pack_from_interactive(state: InteractiveState) -> None:
    plans = build_preview_plans(state)
    if state.ui_mode == "kursorowy":
        if not cursor_confirm("URUCHOMIĆ PAKOWANIE?", plan_summary(plans), yes_label="Pakuj", no_label="Anuluj"):
            cursor_message_page("PAKOWANIE ANULOWANE", "Nie utworzono ani nie nadpisano żadnej paczki.", kind="warn")
            return
    else:
        for plan in plans:
            print_plan(plan)
        if input("Rozpocząć pakowanie? [t/N]: ").strip().lower() not in {"t", "tak", "y", "yes"}:
            return
    preview_hashes = {plan.profile: plan.plan_sha256() for plan in plans}
    results = run_pack_with_plans(state.to_options(), plans)
    for result in results:
        if result.plan.plan_sha256() != preview_hashes[result.profile]:
            raise PackError(f"Hash planu zmienił się dla profilu {result.profile}.")
    lines: list[str] = []
    for result in results:
        lines.extend([
            f"Paczka: {result.package_name}",
            f"Profil: {result.profile}",
            f"Format: {result.archive_format}",
            f"Pliki planu: {result.plan.file_count}",
            f"Woluminy: {len(result.outputs)}",
            f"Plan SHA-256: {result.plan.plan_sha256()}",
            f"Set SHA-256: {result.package_set_sha256}",
            "Pliki wynikowe:",
        ])
        lines.extend(f"  ✓ {path}" for path in result.committed_paths)
        lines.extend(compatibility_summary(result))
        lines.append("")
    if state.ui_mode == "kursorowy":
        cursor_message_page("PAKOWANIE ZAKOŃCZONE POPRAWNIE", lines, kind="ok", subtitle="Wynik pozostaje widoczny do naciśnięcia Enter lub Esc")
    else:
        print_results(results)


def show_plan_interactive(state: InteractiveState) -> None:
    plans = build_preview_plans(state)
    if state.ui_mode == "kursorowy":
        cursor_message_page("KANONICZNY PLAN", plan_summary(plans), kind="info")
    else:
        for plan in plans:
            print_plan(plan, show_files=False)


def _sidecar_candidates(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted((path for path in folder.glob("*.package.json") if path.is_file()), key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)


def resolve_sidecar_path(value: str | Path, *, fallback_folder: Path | None = None) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_file() and path.name.lower().endswith(".package.json"):
        return path
    if path.is_dir():
        matches = _sidecar_candidates(path)
        if matches:
            return matches[0]
    if fallback_folder is not None:
        matches = _sidecar_candidates(fallback_folder.expanduser().resolve())
        if len(matches) == 1:
            return matches[0]
    raise PackError(f"Nie znaleziono sidecara *.package.json: {path}")


def suggested_sidecar_path(state: InteractiveState) -> Path:
    matches = _sidecar_candidates(state.out_dir.expanduser().resolve())
    return matches[0] if matches else state.out_dir


def verify_interactive(state: InteractiveState) -> None:
    value = cursor_inline_editor("WERYFIKACJA PACZKI", "Sidecar", str(suggested_sidecar_path(state)), description="Wskaż *.package.json albo folder zawierający sidecar.", path_mode=False) if state.ui_mode == "kursorowy" else input("Sidecar: ").strip()
    if value is None:
        return
    report = verify_package_sidecar(resolve_sidecar_path(value, fallback_folder=state.out_dir))
    cursor_message_page("WERYFIKACJA ZAKOŃCZONA", json.dumps(report, ensure_ascii=False, indent=2), kind="ok") if state.ui_mode == "kursorowy" else print(json.dumps(report, ensure_ascii=False, indent=2))


def extract_interactive(state: InteractiveState) -> None:
    sidecar = cursor_inline_editor("ROZPAKOWANIE", "Sidecar", str(suggested_sidecar_path(state)), description="Wskaż *.package.json.") if state.ui_mode == "kursorowy" else input("Sidecar: ").strip()
    if sidecar is None:
        return
    destination_default = state.source.parent / "jazn_runtime_test"
    destination = cursor_inline_editor("ROZPAKOWANIE", "Cel", str(destination_default), description="Folder docelowy.", path_mode=True) if state.ui_mode == "kursorowy" else input(f"Cel [{destination_default}]: ").strip() or str(destination_default)
    if destination is None:
        return
    clean = cursor_confirm("WYCZYŚCIĆ CEL?", [str(destination)]) if state.ui_mode == "kursorowy" else False
    report = extract_package_sidecar(resolve_sidecar_path(sidecar, fallback_folder=state.out_dir), Path(destination), clean=clean, force=True)
    cursor_message_page("ROZPAKOWANIE ZAKOŃCZONE", json.dumps(report, ensure_ascii=False, indent=2), kind="ok") if state.ui_mode == "kursorowy" else print(json.dumps(report, ensure_ascii=False, indent=2))


def update_manifest_interactive(state: InteractiveState) -> None:
    options = state.to_options()
    plan = build_plan(options.source, "system", options.custom_excludes, base_excludes=options.base_excludes, manual_excludes_enabled=options.manual_excludes_enabled)
    path = write_source_manifest_from_plan(plan)
    if path is None:
        raise PackError("Plan nie zawiera wirtualnego PACKAGE_INTEGRITY_MANIFEST.json.")
    message = [f"Zapisano: {path}", f"Wersja: {plan.version.full_version}", f"Pliki statyczne: {len([e for e in plan.entries if e.classification == 'static_project_file'])}", f"SHA-256: {sha256_file(path)}"]
    cursor_message_page("MANIFEST ZAKTUALIZOWANY", message, kind="ok") if state.ui_mode == "kursorowy" else print("\n".join(message))


def _legacy_main_menu_rows_v611(state: InteractiveState) -> list[str]:
    manual = "WŁ." if state.manual_excludes_enabled and state.custom_excludes else "WYŁ."
    return [
        "Pakuj teraz",
        "Pokaż kanoniczny plan",
        f"Źródło: [{state.source}]",
        f"Wyjście: [{state.out_dir}]",
        f"Profil: [{PROFILE_DISPLAY[state.profile]}]",
        f"Format: [{state.archive_format}]",
        f"Nazwa: [{state.archive_basename}]",
        f"Limit: [{state.part_size_mb} MiB]",
        f"Kompresja: [{state.compression_level}]",
        f"Nadpisywanie: [{'TAK' if state.force else 'NIE'}]",
        f"Pliki pomocnicze: [{'TAK' if state.sidecars else 'NIE'}]",
        f"Aktualizacja manifestu: [{'TAK' if state.update_source_manifest else 'NIE'}]",
        f"Testy zgodności ZIP: [{'TAK' if state.compatibility_checks else 'NIE'}]",
        f"Wykluczenia: [podstawowe {len(state.base_excludes)} • ręczne {len(state.custom_excludes)} {manual}]",
        f"Interfejs: [{state.ui_mode}]",
        "Zapisz ustawienia",
        "Zweryfikuj istniejącą paczkę",
        "Bezpiecznie rozpakuj paczkę",
        "Aktualizuj PACKAGE_INTEGRITY_MANIFEST.json teraz",
        "Wyjdź",
    ]


def _legacy_main_menu_details_v611(state: InteractiveState) -> list[str]:
    return [
        "Buduje jeden zamrożony plan, aktualizuje manifest (gdy włączone), pakuje, weryfikuje CRC/SHA-256 i uruchamia testy zgodności.",
        "Pokazuje wersję runtime, nazwę pliku, liczbę plików, rozmiar i Plan SHA-256.",
        "Edytuj ścieżkę bez opuszczania trybu pełnoekranowego. Root musi zawierać latka_jazn/version.py.",
        "Edytuj folder wynikowy bez opuszczania trybu pełnoekranowego. Musi leżeć poza źródłem.",
        PROFILE_DESCRIPTIONS[state.profile],
        "auto: standardowe niezależne ZIP-y; binary: jeden logiczny ZIP podzielony na .001/.002.",
        "Bazowa nazwa paczki. Numer i release-name pochodzą wyłącznie z version.py.",
        "Maksymalny rozmiar woluminu. Pojedynczy większy plik wymusza format binary.",
        "Poziom DEFLATE 0–9. ZIP_DEFLATED zapewnia szeroką zgodność z archiwizatorami.",
        "Gdy wyłączone, generator nie zastąpi istniejących wyników o tej samej nazwie.",
        "Tworzy package.json, parts.sha256 oraz join.ps1 dla formatu binary.",
        "Zapisuje świeży manifest dokładnie odpowiadający kanonicznemu planowi statycznemu.",
        "Python zipfile jest obowiązkowy; dostępne 7-Zip, WinRAR, WinZip i Info-ZIP są testowane automatycznie.",
        "Osobna edycja listy podstawowej i ręcznej, dodawanie, edycja, usuwanie i przełącznik aktywności.",
        "Wybór interfejsu. Proste zmiany w trybie kursorowym nie przełączają aplikacji do trybu tekstowego.",
        "Zapisuje konfigurację v7.0.1 atomowo obok skryptu.",
        "Sprawdza sidecar, SHA-256, CRC i kompletność wpisów.",
        "Najpierw weryfikuje, potem rozpakowuje z ochroną przed path traversal.",
        "Jawnie aktualizuje źródłowy manifest bez tworzenia ZIP-a.",
        "Pokazuje podsumowanie i pozwala zapisać ustawienia albo wrócić do programu.",
    ]


def _state_status_lines(state: InteractiveState) -> list[str]:
    return [
        f"Profil       {PROFILE_DISPLAY[state.profile]}",
        f"Format       {state.archive_format}",
        f"Limit        {state.part_size_mb} MiB",
        f"Kompresja    DEFLATE {state.compression_level}",
        f"Manifest     {'auto' if state.update_source_manifest else 'bez zapisu'}",
        f"Zgodność     {'włączona' if state.compatibility_checks else 'wyłączona'}",
        f"Zmiany       {'NIEZAPISANE' if state.dirty else 'zapisane'}",
    ]


def cursor_main_screen(state: InteractiveState, selected: int = 0) -> int | None:
    """Starszy ekran kursorowy zachowany wyłącznie dla zgodności.

    Ścieżki, nazwa i limit są edytowane bezpośrednio w wybranym wierszu.
    Profil, format, kompresja i przełączniki zmieniają się klawiszami ←/→
    albo Enter. Złożone narzędzia nadal otwierają własne pełnoekranowe strony.
    """

    if not _cursor_available():
        raise PackError("Tryb kursorowy wymaga biblioteki prompt_toolkit.")
    assert _pt_Application and _pt_Condition and _pt_KeyBindings and _pt_Layout
    assert _pt_DynamicContainer and _pt_HSplit and _pt_VSplit and _pt_Window
    assert _pt_FormattedTextControl and _pt_ScrollablePane and _pt_TextArea

    # Lokalne, zawężone aliasy są używane także wewnątrz funkcji zagnieżdżonych.
    # Pyright/Pylance nie przenosi bezpiecznie zawężenia opcjonalnych globali
    # do closure, ponieważ global może teoretycznie zostać zmieniony.
    pt_vsplit = _pt_VSplit
    pt_window = _pt_Window
    pt_formatted_text_control = _pt_FormattedTextControl
    assert pt_vsplit is not None
    assert pt_window is not None
    assert pt_formatted_text_control is not None

    index = max(0, min(selected, len(main_menu_rows(state)) - 1))
    editing_index: int | None = None
    edit_original = ""
    message = ""
    bindings = _pt_KeyBindings()
    editor = _pt_TextArea(text="", multiline=False, wrap_lines=False, style="class:input")
    editable_indices = {2, 3, 6, 7}
    choice_indices = {4, 5, 8}
    toggle_indices = {9, 10, 11, 12}
    groups = {0: "GŁÓWNE", 2: "KONFIGURACJA", 15: "NARZĘDZIA", 19: "WYJŚCIE"}

    def rows() -> list[str]:
        return main_menu_rows(state)

    def values_for(row_index: int) -> tuple[list[Any], int] | None:
        if row_index == 4:
            values = list(PROFILE_CHOICES)
            return values, values.index(state.profile)
        if row_index == 5:
            values = list(FORMAT_CHOICES)
            return values, values.index(state.archive_format)
        if row_index == 8:
            values = list(range(10))
            return values, state.compression_level
        return None

    def apply_choice(row_index: int, direction: int) -> None:
        nonlocal message
        data = values_for(row_index)
        if data is None:
            return
        values, current = data
        new_value = values[(current + direction) % len(values)]
        if row_index == 4:
            state.profile = str(new_value)
        elif row_index == 5:
            state.archive_format = str(new_value)
        elif row_index == 8:
            state.compression_level = int(new_value)
        state.dirty = True
        message = "Zmieniono ustawienie w bieżącym wierszu."

    def apply_toggle(row_index: int, direction: int = 0) -> None:
        nonlocal message
        desired: bool | None = True if direction > 0 else False if direction < 0 else None
        if row_index == 9:
            state.force = (not state.force) if desired is None else desired
        elif row_index == 10:
            state.sidecars = (not state.sidecars) if desired is None else desired
        elif row_index == 11:
            state.update_source_manifest = (not state.update_source_manifest) if desired is None else desired
        elif row_index == 12:
            state.compatibility_checks = (not state.compatibility_checks) if desired is None else desired
        state.dirty = True
        message = "Przełącznik zmieniono bez opuszczania strony."

    def current_edit_value(row_index: int) -> str:
        if row_index == 2:
            return str(state.source)
        if row_index == 3:
            return str(state.out_dir)
        if row_index == 6:
            return state.archive_basename
        if row_index == 7:
            return str(state.part_size_mb)
        return ""

    def edit_label(row_index: int) -> str:
        return {2: "Źródło", 3: "Wyjście", 6: "Nazwa", 7: "Limit MiB"}.get(row_index, "Wartość")

    def focus_row(app: Any) -> None:
        try:
            app.layout.focus(row_controls[index])
        except Exception:
            pass

    def start_edit(row_index: int, app: Any) -> None:
        nonlocal editing_index, edit_original, message
        editing_index = row_index
        edit_original = current_edit_value(row_index)
        editor.buffer.text = edit_original
        editor.buffer.cursor_position = len(editor.buffer.text)
        message = "Edycja w tym samym wierszu — Enter zapisuje, Esc anuluje."
        app.layout.focus(editor)
        app.invalidate()

    def cancel_edit(app: Any) -> None:
        nonlocal editing_index, message
        editing_index = None
        message = "Zmiana anulowana."
        focus_row(app)
        app.invalidate()

    def commit_edit(app: Any) -> None:
        nonlocal editing_index, message
        if editing_index is None:
            return
        value = editor.text.strip()
        try:
            if editing_index == 2:
                if not value:
                    raise ValueError("Ścieżka źródłowa nie może być pusta.")
                state.source = Path(value).expanduser().resolve()
            elif editing_index == 3:
                if not value:
                    raise ValueError("Ścieżka wyjściowa nie może być pusta.")
                state.out_dir = Path(value).expanduser().resolve()
            elif editing_index == 6:
                state.archive_basename = sanitize_archive_stem(value)
            elif editing_index == 7:
                amount = int(value)
                if amount <= 0:
                    raise ValueError("Limit musi być większy od zera.")
                state.part_size_mb = amount
            state.dirty = True
        except (OSError, ValueError, PackError) as exc:
            message = f"Błąd: {exc}"
            app.invalidate()
            return
        editing_index = None
        message = "Wartość zapisana w bieżącym wierszu."
        focus_row(app)
        app.invalidate()

    def activate(row_index: int, app: Any) -> None:
        nonlocal index
        index = row_index
        if row_index in editable_indices:
            start_edit(row_index, app)
        elif row_index in choice_indices:
            apply_choice(row_index, 1)
            focus_row(app)
            app.invalidate()
        elif row_index in toggle_indices:
            apply_toggle(row_index)
            focus_row(app)
            app.invalidate()
        else:
            app.exit(result=row_index)

    def move(action: str, app: Any) -> None:
        nonlocal index, message
        index = menu_navigation_index(index, action, len(rows()))
        message = ""
        focus_row(app)
        app.invalidate()

    def row_mouse_handler(row_index: int):
        def _handler(mouse_event: Any):
            nonlocal index
            action = _mouse_action(mouse_event)
            try:
                from prompt_toolkit.application.current import get_app
                app = get_app()
            except Exception:
                return NotImplemented
            if action in {"up", "down"}:
                move(action, app)
                return None
            if action == "back":
                if editing_index is not None:
                    cancel_edit(app)
                else:
                    app.exit(result=None)
                return None
            if action == "activate":
                index = row_index
                activate(row_index, app)
                return None
            return NotImplemented
        return _handler

    def render_row(row_index: int):
        current_rows = rows()
        handler = row_mouse_handler(row_index)
        style = "class:menu.selected" if row_index == index else "class:menu.item"
        marker = "  ▶ " if row_index == index else "    "
        fragments: list[tuple[Any, ...]] = []
        if row_index == index:
            fragments.append(("[SetCursorPosition]", ""))
        fragments.append((style, marker + current_rows[row_index], handler))
        return fragments

    def editing_container(row_index: int):
        label = edit_label(row_index)
        return pt_vsplit([
            pt_window(width=max(13, len(label) + 5), height=1, content=pt_formatted_text_control(
                text=[("class:menu.selected", f"  ▶ {label}: [")]
            )),
            editor,
            pt_window(width=1, height=1, content=pt_formatted_text_control(text=[("class:menu.selected", "]")])),
        ], height=1)

    row_controls: list[Any] = []
    row_windows: list[Any] = []
    menu_children: list[Any] = []
    for row_index in range(len(rows())):
        section = groups.get(row_index)
        if section:
            line = f"── {section} "
            menu_children.append(_pt_Window(
                height=1,
                content=_pt_FormattedTextControl(text=[
                    ("class:menu.section", "  " + line + APP_THEME.section_fill * max(3, 26 - len(line)))
                ]),
            ))
        control = _pt_FormattedTextControl(text=lambda i=row_index: render_row(i), focusable=True, show_cursor=False)
        normal_window = _pt_Window(height=1, content=control, style="class:menu", wrap_lines=False, always_hide_cursor=True)
        row_controls.append(control)
        row_windows.append(normal_window)
        menu_children.append(_pt_DynamicContainer(
            lambda i=row_index, normal=normal_window: editing_container(i) if editing_index == i else normal
        ))

    left_pane = _pt_ScrollablePane(
        _pt_HSplit(menu_children),
        width=APP_THEME.left_dimension(),
        show_scrollbar=True,
        display_arrows=True,
        keep_cursor_visible=True,
        keep_focused_window_visible=True,
    )

    def render_header():
        return [
            ("class:header.title", f"  Jaźń / Łatka — generator paczek v{GENERATOR_VERSION}"),
            ("class:header.subtitle", "  •  edycja inline • LPM wybiera • PPM wraca"),
        ]

    def render_detail():
        current_rows = rows()
        fragments: list[tuple[str, str]] = [("class:panel.title", "  AKTUALNA KONFIGURACJA\n")]
        for line in _state_status_lines(state):
            fragments.append(("class:panel.label", "  " + line + "\n"))
        fragments.append(("class:panel.rule", "\n  " + APP_THEME.section_fill * 48 + "\n"))
        fragments.append(("class:panel.title", "  WYBRANA OPCJA\n"))
        detail = main_menu_details(state)[index]
        for line in _wrap(detail, 68):
            fragments.append(("class:panel.text", "  " + line + "\n"))
        fragments.append(("class:panel.rule", "\n  " + APP_THEME.section_fill * 48 + "\n"))
        fragments.append(("class:panel.label", "  BIEŻĄCY WIERSZ\n"))
        for line in _wrap(current_rows[index], 68):
            fragments.append(("class:panel.text", "  " + line + "\n"))
        if message:
            msg_style = "class:panel.error" if message.lower().startswith("błąd") else "class:message.ok"
            fragments.append((msg_style, "\n  " + message + "\n"))
        return fragments

    def render_footer():
        if editing_index is not None:
            return [
                ("class:footer.key", " Enter "), ("class:footer.text", "zapisz  "),
                ("class:footer.key", " Esc/PPM "), ("class:footer.text", "anuluj  "),
                ("class:footer.key", " Home/End "), ("class:footer.text", "początek/koniec tekstu  "),
                ("class:footer.key", " Ctrl+X "), ("class:footer.text", "wyjście "),
            ]
        return [
            ("class:footer.key", " ↑/↓ Home/End PgUp/PgDn "), ("class:footer.text", "nawigacja  "),
            ("class:footer.key", " ←/→ "), ("class:footer.text", "zmień wartość  "),
            ("class:footer.key", " LPM/Enter "), ("class:footer.text", "edytuj/otwórz  "),
            ("class:footer.key", " PPM/Esc "), ("class:footer.text", "wstecz "),
        ]

    header_control = _pt_FormattedTextControl(text=render_header)
    detail_control = _pt_FormattedTextControl(text=render_detail)
    footer_control = _pt_FormattedTextControl(text=render_footer)
    not_editing = _pt_Condition(lambda: editing_index is None)
    editing = _pt_Condition(lambda: editing_index is not None)

    for key, action in (("up", "up"), ("k", "up"), ("down", "down"), ("j", "down"),
                        ("home", "home"), ("end", "end"), ("pageup", "pageup"), ("pagedown", "pagedown")):
        bindings.add(key, filter=not_editing, eager=True)(lambda event, a=action: move(a, event.app))

    @bindings.add("left", filter=not_editing, eager=True)
    def _left(event: Any) -> None:
        if index in choice_indices:
            apply_choice(index, -1)
        elif index in toggle_indices:
            apply_toggle(index, -1)
        event.app.invalidate()

    @bindings.add("right", filter=not_editing, eager=True)
    def _right(event: Any) -> None:
        if index in choice_indices:
            apply_choice(index, 1)
        elif index in toggle_indices:
            apply_toggle(index, 1)
        event.app.invalidate()

    @bindings.add("enter", filter=not_editing, eager=True)
    def _enter(event: Any) -> None:
        activate(index, event.app)

    @bindings.add("enter", filter=editing, eager=True)
    def _commit(event: Any) -> None:
        commit_edit(event.app)

    @bindings.add("escape", filter=editing, eager=True)
    def _cancel(event: Any) -> None:
        cancel_edit(event.app)

    @bindings.add("escape", filter=not_editing, eager=True)
    @bindings.add("q", filter=not_editing, eager=True)
    def _back(event: Any) -> None:
        event.app.exit(result=None)

    @bindings.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=-2)

    root = _pt_HSplit([
        _pt_Window(height=2, content=header_control, style="class:header", wrap_lines=False),
        _pt_Window(height=1, char=APP_THEME.section_fill, style="class:border"),
        _pt_VSplit([
            left_pane,
            _pt_Window(width=1, char=APP_THEME.border_char, style="class:border"),
            _pt_Window(content=detail_control, width=APP_THEME.right_dimension(), style="class:panel", wrap_lines=True),
        ]),
        _pt_Window(height=1, char=APP_THEME.section_fill, style="class:border"),
        _pt_Window(height=1, content=footer_control, style="class:footer", wrap_lines=False),
    ])
    layout = _pt_Layout(root, focused_element=row_controls[index])
    app = _pt_Application(
        layout=layout,
        key_bindings=bindings,
        style=_cursor_style(),
        full_screen=True,
        erase_when_done=True,
        mouse_support=True,
        enable_page_navigation_bindings=False,
        terminal_size_polling_interval=0.25,
    )
    result = app.run()
    if result == -2:
        raise UserRequestedExit()
    return result


def handle_menu_choice(state: InteractiveState, choice: int) -> str:
    if choice == 0:
        pack_from_interactive(state)
    elif choice == 1:
        show_plan_interactive(state)
    elif choice == 2:
        value = cursor_inline_editor("EDYCJA ŹRÓDŁA", "Źródło", str(state.source), description="Domyślnie bez konfiguracji: C:\\ w Windows albo /bin/ w systemach Unix.", path_mode=True)
        if value is not None:
            state.source = Path(value).expanduser().resolve(); state.dirty = True
    elif choice == 3:
        value = cursor_inline_editor("EDYCJA WYJŚCIA", "Wyjście", str(state.out_dir), description="Folder wynikowy nie może znajdować się wewnątrz źródła.", path_mode=True)
        if value is not None:
            state.out_dir = Path(value).expanduser().resolve(); state.dirty = True
    elif choice == 4:
        values = list(PROFILE_CHOICES)
        labels = [PROFILE_DISPLAY[value] for value in values]
        descriptions = [PROFILE_DESCRIPTIONS[value] for value in values]
        selected = cursor_choice_editor("WYBÓR PROFILU", "Profil", values, labels, descriptions, values.index(state.profile))
        if selected is not None:
            state.profile = values[selected]; state.dirty = True
    elif choice == 5:
        values = list(FORMAT_CHOICES)
        descriptions = [
            "Najszersza zgodność: niezależne ZIP-y, chyba że plik wymusza binary.",
            "Każdy wolumin jest samodzielnym ZIP-em.",
            "Jeden logiczny ZIP dzielony bajtowo na .zip.001/.002; trzeba go połączyć przed użyciem w programach zewnętrznych.",
        ]
        selected = cursor_choice_editor("WYBÓR FORMATU", "Format", values, values, descriptions, values.index(state.archive_format))
        if selected is not None:
            state.archive_format = values[selected]; state.dirty = True
    elif choice == 6:
        value = cursor_inline_editor("EDYCJA NAZWY", "Nazwa", state.archive_basename, description="Bez rozszerzenia .zip; niedozwolone znaki zostaną odrzucone.")
        if value is not None:
            state.archive_basename = sanitize_archive_stem(value); state.dirty = True
    elif choice == 7:
        value = cursor_inline_editor("LIMIT WOLUMINU", "MiB", str(state.part_size_mb), description="Liczba dodatnia.")
        if value is not None:
            state.part_size_mb = max(1, int(value)); state.dirty = True
    elif choice == 8:
        values = [str(index) for index in range(10)]
        descriptions = [f"Poziom DEFLATE {index}. 0=najszybciej, 9=najmniejszy plik." for index in range(10)]
        selected = cursor_choice_editor("POZIOM KOMPRESJI", "DEFLATE", values, values, descriptions, state.compression_level)
        if selected is not None:
            state.compression_level = selected; state.dirty = True
    elif choice == 9:
        state.force = not state.force; state.dirty = True
    elif choice == 10:
        state.sidecars = not state.sidecars; state.dirty = True
    elif choice == 11:
        state.update_source_manifest = not state.update_source_manifest; state.dirty = True
    elif choice == 12:
        state.compatibility_checks = not state.compatibility_checks; state.dirty = True
    elif choice == 13:
        if state.ui_mode == "kursorowy":
            edit_exclusions_cursor(state)
        else:
            edit_exclusions_text(state)
    elif choice == 14:
        if HAS_PROMPT_TOOLKIT:
            state.ui_mode = "tekstowy" if state.ui_mode == "kursorowy" else "kursorowy"
            state.dirty = True
    elif choice == 15:
        cursor_message_page("USTAWIENIA ZAPISANE", str(save_interactive_state(state)), kind="ok")
    elif choice == 16:
        verify_interactive(state)
    elif choice == 17:
        extract_interactive(state)
    elif choice == 18:
        update_manifest_interactive(state)
    elif choice == 19:
        return "exit"
    return "continue"


def exit_dialog(state: InteractiveState) -> str:
    rows = ["Zapisz ustawienia i wyjdź", "Wyjdź bez zapisu", "Wróć do programu"]
    summary = [
        f"Źródło: {state.source}", f"Wyjście: {state.out_dir}",
        f"Profil: {PROFILE_DISPLAY[state.profile]}",
        f"Stan ustawień: {'niezapisane zmiany' if state.dirty else 'zapisane'}",
    ]
    choice = cursor_select("WYJŚCIE", rows, 2, details=["Zapis atomowy konfiguracji v7.0.1.", "Kończy bez zapisywania bieżących zmian.", "Powrót do strony głównej."], status_lines=summary, groups={0: "PODSUMOWANIE"})
    if choice == 0:
        save_interactive_state(state); return "save"
    if choice == 1:
        return "discard"
    return "cancel"


def render_text_main_menu(state: InteractiveState) -> None:
    rows = main_menu_rows(state)
    ui_banner(f"Jaźń / Łatka — generator paczek v{GENERATOR_VERSION}", "Tryb tekstowy")
    for index, row in enumerate(rows, start=1):
        print(f"  {index:>2}. {row}")


def _legacy_interactive_v611(ui_override: str | None = None) -> int:
    state = load_interactive_state()
    if ui_override:
        state.ui_mode = _normalize_ui_mode(ui_override)
    elif not state.ui_auto_start:
        state.ui_mode = "kursorowy" if _cursor_available() else "tekstowy"
    selected = 0
    try:
        while True:
            try:
                if state.ui_mode == "kursorowy":
                    rows = main_menu_rows(state)
                    choice = cursor_main_screen(state, selected)
                    if choice is None:
                        action = exit_dialog(state)
                        if action != "cancel":
                            return 0
                        continue
                    selected = choice
                else:
                    render_text_main_menu(state)
                    raw = input("Wybór: ").strip()
                    if not raw:
                        continue
                    choice = int(raw) - 1
                action = handle_menu_choice(state, choice)
                if action == "exit":
                    if state.ui_mode == "kursorowy":
                        if exit_dialog(state) != "cancel":
                            return 0
                    else:
                        if state.dirty:
                            save_interactive_state(state)
                        return 0
            except UserRequestedExit:
                if state.ui_mode == "kursorowy" and exit_dialog(state) == "cancel":
                    continue
                return 0
            except UserCancelledInput:
                continue
            except (PackError, OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
                if state.ui_mode == "kursorowy":
                    cursor_message_page("BŁĄD", [f"{type(exc).__name__}: {exc}", "", "Aplikacja pozostaje w trybie kursorowym."], kind="error")
                else:
                    ui_status(str(exc), "error")
    finally:
        pass


def print_results(results: Sequence[PackageResult]) -> None:
    ui_banner("PAKOWANIE ZAKOŃCZONE POPRAWNIE", "Wynik przeszedł kontrolę planu, SHA-256 i CRC")
    for result in results:
        ui_section(result.package_name)
        ui_key_value("Profil", result.profile)
        ui_key_value("Format", result.archive_format)
        ui_key_value("Pliki planu", result.plan.file_count)
        ui_key_value("Woluminy", len(result.outputs))
        ui_key_value("Plan SHA-256", result.plan.plan_sha256())
        ui_key_value("Set SHA-256", result.package_set_sha256)
        for path in result.committed_paths:
            print(f"    ✓ {path}")


def run_self_test() -> dict[str, Any]:
    """Regresja v7.0.1: wersja, ZIP, nawigacja, mysz, ścieżki, worker UI i przewijanie logu."""
    with tempfile.TemporaryDirectory(prefix="jazn-pack-v7-selftest-") as tmp_raw:
        temp = Path(tmp_raw)
        version = VersionInfo(
            version_file=temp / "version.py",
            package_version="v91.82.73.64",
            release_name="Reorganize agents",
            full_version="v91.82.73.64-Reorganize agents",
            filename_version="91.82.73.64-Reorganize-agents",
        )
        assert compose_runtime_version_full(version.package_version, version.release_name) == version.full_version
        assert compose_package_version_full(version.package_version, version.release_name) == version.filename_version
        assert manifest_version_matches(version.full_version, version.package_version, version.release_name)
        payloads = {
            "ascii.txt": b"standard zip compatibility\n",
            "polski/zażółć-gęślą.txt": "Pchnąć w tę łódź jeża lub ośm skrzyń fig.".encode("utf-8"),
            "empty.bin": b"",
            "nested/data.json": serialize_json({"ok": True, "version": GENERATOR_VERSION}),
            "binary/random.bin": os.urandom(256 * 1024),
        }
        entries = [virtual_entry(path, data, "static_project_file") for path, data in payloads.items()]
        plan = PackPlan(root=temp, profile="system", version=version, entries=sorted(entries, key=lambda item: item.relative))

        independent_dir = temp / "independent"; independent_dir.mkdir()
        independent, _ = write_independent(independent_dir, "selftest.zip", plan, 1024 * 1024, 6)
        verify_independent = verify_outputs(independent_dir, independent, "independent", plan)
        compat_independent = run_compatibility_matrix(independent_dir, independent, "independent")

        binary_dir = temp / "binary"; binary_dir.mkdir()
        binary, logical_hash = write_binary(binary_dir, "selftest.zip", plan, 64 * 1024, 6)
        verify_binary = verify_outputs(binary_dir, binary, "binary", plan)
        compat_binary = run_compatibility_matrix(binary_dir, binary, "binary")

        navigation = {
            "home": menu_navigation_index(7, "home", 13),
            "end": menu_navigation_index(2, "end", 13),
            "pageup": menu_navigation_index(12, "pageup", 13, 8),
            "pagedown": menu_navigation_index(3, "pagedown", 13, 8),
        }
        assert navigation == {"home": 0, "end": 12, "pageup": 4, "pagedown": 11}
        assert APP_THEME.left_panel_width == 54
        assert APP_THEME.right_min_width == 36
        responsive_layout = {
            "compact": APP_THEME.layout_metrics(80, 24),
            "wide": APP_THEME.layout_metrics(120, 32),
            "large": APP_THEME.layout_metrics(180, 50),
        }
        assert responsive_layout["compact"]["compact"] == 1
        assert responsive_layout["compact"]["left_width"] == 79
        assert responsive_layout["compact"]["right_width"] == 79
        for key in ("wide", "large"):
            metrics = responsive_layout[key]
            assert metrics["compact"] == 0
            assert metrics["left_width"] + metrics["right_width"] == metrics["columns"] - 1
            assert metrics["left_width"] == APP_THEME.left_panel_width
            assert metrics["right_text_width"] >= 12
            assert APP_THEME.info_min_height <= metrics["info_height"] <= APP_THEME.info_max_height
        assert responsive_layout["wide"]["right_width"] < responsive_layout["large"]["right_width"]

        root_fixture = temp / "jazn-root"
        version_fixture = root_fixture / "latka_jazn" / "version.py"
        version_fixture.parent.mkdir(parents=True)
        version_fixture.write_text(
            'DISTRIBUTION_VERSION = "91.82.73.64"\n'
            'PACKAGE_VERSION = "v91.82.73.64"\n'
            'PACKAGE_RELEASE_NAME = "Self test"\n',
            encoding="utf-8",
        )
        validated_root, validated_version = validate_jazn_root(root_fixture)
        assert validated_root == root_fixture.resolve()
        assert validated_version.full_version == "v91.82.73.64-Self test"

        operation_lines: list[str] = []
        _set_operation_output_sink(operation_lines.append)
        try:
            print_progress(1, 2, "Test postępu")
            _emit_operation_line("Test komunikatu")
        finally:
            _set_operation_output_sink(None)
        assert operation_lines == ["Test postępu: 1/2 ( 50%)", "Test komunikatu"]
        path_completion: dict[str, Any] = {"status": "skipped_without_prompt_toolkit"}
        if _pt_PathCompleter is not None:
            from prompt_toolkit.completion import CompleteEvent
            from prompt_toolkit.document import Document

            completion_root = temp / "completion"
            (completion_root / "alpha").mkdir(parents=True)
            completer = _pt_PathCompleter(
                only_directories=True,
                expanduser=True,
                min_input_len=0,
            )
            prefix = str(completion_root / "alp")
            completions = list(completer.get_completions(
                Document(prefix, cursor_position=len(prefix)),
                CompleteEvent(completion_requested=True),
            ))
            assert any(item.text == "ha" for item in completions)
            path_completion = {
                "status": "passed",
                "prefix": prefix,
                "completion_count": len(completions),
            }

        mouse_actions: dict[str, str] = {}
        if _pt_MouseEventType is not None and _pt_MouseButton is not None:
            from types import SimpleNamespace
            mouse_actions = {
                "lpm": str(_mouse_action(SimpleNamespace(event_type=_pt_MouseEventType.MOUSE_UP, button=_pt_MouseButton.LEFT))),
                "ppm": str(_mouse_action(SimpleNamespace(event_type=_pt_MouseEventType.MOUSE_UP, button=_pt_MouseButton.RIGHT))),
                "scroll_up": str(_mouse_action(SimpleNamespace(event_type=_pt_MouseEventType.SCROLL_UP, button=_pt_MouseButton.NONE))),
                "scroll_down": str(_mouse_action(SimpleNamespace(event_type=_pt_MouseEventType.SCROLL_DOWN, button=_pt_MouseButton.NONE))),
            }
            assert mouse_actions == {"lpm": "activate", "ppm": "back", "scroll_up": "up", "scroll_down": "down"}

        scroll_model = {"position": 0, "follow_tail": True}
        for action in ("down", "down", "pageup", "end"):
            if action == "down":
                scroll_model["position"] += 1
                scroll_model["follow_tail"] = False
            elif action == "pageup":
                scroll_model["position"] = max(0, scroll_model["position"] - APP_THEME.page_step)
                scroll_model["follow_tail"] = False
            elif action == "end":
                scroll_model["position"] = 10**9
                scroll_model["follow_tail"] = True
        assert scroll_model == {"position": 10**9, "follow_tail": True}

        ok = bool(verify_independent.get("ok") and verify_binary.get("ok") and compat_independent.get("ok") and compat_binary.get("ok") and len(binary) > 1 and logical_hash)
        return {
            "ok": ok,
            "generator_version": GENERATOR_VERSION,
            "release_name_spaces": "passed",
            "navigation_keys": navigation,
            "theme": {
                "name": APP_THEME.name,
                "left_panel_width": APP_THEME.left_panel_width,
                "right_min_width": APP_THEME.right_min_width,
                "minimum_terminal_width": APP_THEME.minimum_terminal_width,
            },
            "responsive_layout": responsive_layout,
            "mouse_actions": mouse_actions or {"status": "skipped_without_prompt_toolkit"},
            "path_completion": path_completion,
            "jazn_root_validation": {"status": "passed", "version": validated_version.full_version},
            "operation_output": {"status": "passed", "lines": operation_lines},
            "scroll_follow_model": {"status": "passed", **scroll_model},
            "independent": {"verification": verify_independent, "compatibility": compat_independent},
            "binary": {"parts": len(binary), "logical_zip_sha256": logical_hash, "verification": verify_binary, "compatibility": compat_binary},
        }


# -----------------------------------------------------------------------------
# Interfejs v7.0.1 — stabilny fokus, responsywny układ, przewijanie i bezpieczne operacje
# -----------------------------------------------------------------------------

DASHBOARD_GROUPS: dict[int, str] = {
    0: "GŁÓWNE",
    6: "NARZĘDZIA",
    8: "KONFIGURACJA",
    11: "WYJŚCIE",
}

DASHBOARD_LEFT_EDITABLE: dict[int, str] = {
    3: "source",
    4: "out_dir",
    8: "archive_basename",
}

OPTIONS_LABELS: tuple[str, ...] = (
    "Format",
    "Limit",
    "Kompresja",
    "Nadpisywanie",
    "Pliki pomocnicze",
    "Interfejs",
    "Aktualizuj mapę przy pakowaniu",
    "Testy zgodności ZIP",
)

OPTIONS_DESCRIPTIONS: tuple[str, ...] = (
    "Wybiera format przez popup: auto, independent albo binary.",
    "Maksymalny rozmiar woluminu w MiB. Enter uruchamia edycję w prawym panelu.",
    "Wybiera poziom DEFLATE 0–9 przez popup.",
    "Pozwala zastąpić istniejące wyniki o tej samej nazwie.",
    "Tworzy package.json, parts.sha256 oraz join.ps1 dla formatu binary.",
    "Wybiera preferowany interfejs przy następnym uruchomieniu.",
    "Przed pakowaniem zapisuje świeży PACKAGE_INTEGRITY_MANIFEST.json z dokładnie zatwierdzonego planu.",
    "Uruchamia Python zipfile oraz wykryte 7-Zip, WinRAR, WinZip i Info-ZIP.",
)

EXCLUSION_ACTIONS: tuple[str, ...] = (
    "Edytuj wykluczenia podstawowe",
    "Edytuj wykluczenia ręczne",
    "Dodaj ręczne wykluczenie",
    "Usuń ręczne wykluczenie",
    "Włącz/wyłącz ręczne wykluczenia",
    "Przywróć podstawowe domyślne",
)

EXCLUSION_DESCRIPTIONS: tuple[str, ...] = (
    "Otwiera listę podstawowych wzorców. Można dodawać, edytować i usuwać wpisy.",
    "Otwiera listę ręcznych wzorców. Można dodawać, edytować i usuwać wpisy.",
    "Dodaje nowy ręczny wzorzec glob, np. docs/archive/** albo *.tmp.",
    "Otwiera listę ręcznych wzorców w trybie usuwania wybranego wpisu.",
    "Pusta lista ręczna zawsze pozostaje wyłączona.",
    "Przywraca listę podstawową dostarczoną z generatorem.",
)


def validate_jazn_root(value: str | Path) -> tuple[Path, VersionInfo]:
    """Potwierdza, że wskazany katalog jest rootem systemu Jaźni."""

    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise PackError(f"Katalog Systemu Jaźni nie istnieje: {root}")
    version_file = root / "latka_jazn" / "version.py"
    if not version_file.is_file():
        raise PackError(
            "Wybrany katalog nie jest rootem systemu Jaźni — brak "
            f"latka_jazn/version.py: {version_file}"
        )
    return root, read_version_info(root)


def _middle_ellipsize(value: object, width: int) -> str:
    raw = str(value)
    width = max(1, int(width))
    if len(raw) <= width:
        return raw
    if width <= 3:
        return raw[:width]
    left = max(1, (width - 1) // 2)
    right = max(1, width - left - 1)
    return raw[:left] + "…" + raw[-right:]


def _menu_field(label: str, value: object) -> str:
    available = max(10, APP_THEME.left_panel_width - len(label) - 9)
    return f"{label}: [{_middle_ellipsize(value, available)}]"


def main_menu_rows(state: InteractiveState) -> list[str]:
    manual = "WŁ." if state.manual_excludes_enabled and state.custom_excludes else "WYŁ."
    return [
        "Pakuj teraz",
        "Pokaż kanoniczny plan",
        "Aktualizuj mapę plików",
        _menu_field("System Jaźni", state.source),
        _menu_field("Zapis archiwum", state.out_dir),
        f"Profil: [{PROFILE_DISPLAY[state.profile]}]",
        "Zweryfikuj istniejącą paczkę",
        "Bezpiecznie rozpakuj paczkę",
        _menu_field("Nazwa", state.archive_basename),
        "Opcje",
        f"Wykluczenia: [{len(state.base_excludes)} podst. • {len(state.custom_excludes)} ręcz. • {manual}]",
        "Zapisz ustawienia",
        "Wyjdź",
    ]


def main_menu_details(state: InteractiveState) -> list[str]:
    return [
        "Buduje jeden zamrożony plan, opcjonalnie aktualizuje mapę plików, pakuje i weryfikuje CRC, SHA-256 oraz zgodność ZIP.",
        "Buduje plan bez tworzenia paczki i pokazuje wersję, liczbę plików, rozmiar, źródło skanowania oraz Plan SHA-256.",
        (
            "Tworzy od nowa PACKAGE_INTEGRITY_MANIFEST.json — kanoniczną mapę statycznych plików paczki. "
            "Każdy wpis zawiera ścieżkę, rozmiar i SHA-256. Operacja nie tworzy ZIP-a."
        ),
        "Root systemu Jaźni. Zapis zostanie przyjęty tylko wtedy, gdy istnieje latka_jazn/version.py i można odczytać wersję.",
        "Katalog zapisu archiwum. Może nie istnieć — generator utworzy go podczas pakowania.",
        PROFILE_DESCRIPTIONS[state.profile] + " Wybór odbywa się po prawej stronie.",
        "Wskaż *.package.json albo katalog z sidecarem. Generator sprawdzi części, CRC, SHA-256 i kompletność wpisów.",
        "Wskaż sidecar i katalog docelowy. Paczka zostanie najpierw zweryfikowana, a następnie bezpiecznie rozpakowana.",
        "Bazowa nazwa paczki bez rozszerzenia. Wersja i nazwa wydania pochodzą wyłącznie z latka_jazn/version.py.",
        "Otwiera po prawej: Format, Limit, Kompresję, Nadpisywanie, Pliki pomocnicze, Interfejs, mapę i testy ZIP.",
        "Zarządzaj podstawowymi i ręcznymi wzorcami wykluczeń po prawej stronie.",
        "Zapisuje konfigurację atomowo obok skryptu.",
        "Pokazuje wybór: zapisz i wyjdź, wyjdź bez zapisu albo wróć.",
    ]


def _dashboard_available() -> bool:
    return bool(
        _cursor_available()
        and _pt_FloatContainer is not None
        and _pt_Float is not None
        and _pt_ConditionalContainer is not None
        and _pt_Frame is not None
        and _pt_DynamicCompleter is not None
        and _pt_PathCompleter is not None
        and _pt_ThreadedCompleter is not None
        and _pt_CompletionsMenu is not None
    )


def cursor_dashboard(
    state: InteractiveState,
    selected: int = 0,
    *,
    _input: Any = None,
    _output: Any = None,
    _debug_state: dict[str, Any] | None = None,
) -> str:
    """Uruchamia stabilny pulpit v7.0.1.

    Interfejs ma jeden stały punkt fokusu dla menu i po jednym trwałym punkcie
    fokusu dla każdego rodzaju prawego widoku. Przy małej szerokości działa jak
    interfejs jednookienkowy: menu i panel akcji są przełączane, a nie ściskane.
    """

    if not _dashboard_available():
        raise PackError("Pulpit v7.0.1 wymaga kompletnej biblioteki prompt_toolkit.")

    application_cls = cast(Any, _pt_Application)
    condition_cls = cast(Any, _pt_Condition)
    key_bindings_cls = cast(Any, _pt_KeyBindings)
    layout_cls = cast(Any, _pt_Layout)
    dynamic_container_cls = cast(Any, _pt_DynamicContainer)
    hsplit_cls = cast(Any, _pt_HSplit)
    vsplit_cls = cast(Any, _pt_VSplit)
    window_cls = cast(Any, _pt_Window)
    control_cls = cast(Any, _pt_FormattedTextControl)
    scrollable_cls = cast(Any, _pt_ScrollablePane)
    textarea_cls = cast(Any, _pt_TextArea)
    float_container_cls = cast(Any, _pt_FloatContainer)
    float_cls = cast(Any, _pt_Float)
    conditional_cls = cast(Any, _pt_ConditionalContainer)
    frame_cls = cast(Any, _pt_Frame)
    dynamic_completer_cls = cast(Any, _pt_DynamicCompleter)
    path_completer_cls = cast(Any, _pt_PathCompleter)
    threaded_completer_cls = cast(Any, _pt_ThreadedCompleter)
    completions_menu_cls = cast(Any, _pt_CompletionsMenu)

    rows_cache = main_menu_rows(state)
    index = max(0, min(selected, len(rows_cache) - 1))
    panel_mode = "overview"
    panel_title = rows_cache[index]
    panel_lines: list[str] = [main_menu_details(state)[index]]
    info_lines: list[str] = [rows_cache[index], main_menu_details(state)[index]]
    action_kind = ""
    compact_page = "menu"
    focus_zone = "menu"
    left_editing_index: int | None = None
    left_edit_kind = ""
    right_editor_kind = ""
    extract_sidecar_value = ""
    choice_key = ""
    choice_values: list[Any] = []
    choice_labels: list[str] = []
    choice_descriptions: list[str] = []
    choice_index = 0
    options_index = 0
    exclusions_index = 0
    exclusion_list_kind = ""
    exclusion_item_index = 0
    exclusion_delete_mode = False
    exclusion_edit_index: int | None = None
    popup_visible = False
    popup_kind = ""
    popup_values: list[Any] = []
    popup_labels: list[str] = []
    popup_descriptions: list[str] = []
    popup_index = 0
    busy = False
    log_follow_tail = True
    last_compact: bool | None = None
    app_box: dict[str, Any] = {}
    bindings = key_bindings_cls()
    worker_events: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()

    directory_completer = threaded_completer_cls(
        path_completer_cls(only_directories=True, expanduser=True, min_input_len=0)
    )
    sidecar_completer = threaded_completer_cls(
        path_completer_cls(
            only_directories=False,
            expanduser=True,
            min_input_len=0,
            file_filter=lambda value: os.path.isdir(value)
            or str(value).lower().endswith(".package.json"),
        )
    )

    source_editor = textarea_cls(
        text="", multiline=False, wrap_lines=False, name="jazn-source",
        completer=directory_completer, complete_while_typing=True,
        focus_on_click=True, style="class:input",
    )
    output_editor = textarea_cls(
        text="", multiline=False, wrap_lines=False, name="archive-output",
        completer=directory_completer, complete_while_typing=True,
        focus_on_click=True, style="class:input",
    )
    name_editor = textarea_cls(
        text="", multiline=False, wrap_lines=False, name="archive-name",
        focus_on_click=True, style="class:input",
    )
    left_editors_by_kind = {
        "source": source_editor,
        "out_dir": output_editor,
        "archive_basename": name_editor,
    }
    left_editors_by_row = {3: source_editor, 4: output_editor, 8: name_editor}

    def right_completer() -> Any:
        if panel_mode in {"verify_input", "extract_sidecar"}:
            return sidecar_completer
        if panel_mode == "extract_destination":
            return directory_completer
        return None

    right_editor = textarea_cls(
        text="", multiline=False, wrap_lines=False, name="right-editor",
        completer=dynamic_completer_cls(right_completer),
        complete_while_typing=True, focus_on_click=True, style="class:input",
    )

    def app() -> Any:
        return app_box.get("app")

    def terminal_columns() -> int:
        current = app()
        if current is not None:
            try:
                return int(current.output.get_size().columns)
            except Exception:
                pass
        return APP_THEME.terminal_size()[0]

    def compact_mode() -> bool:
        return APP_THEME.is_compact(terminal_columns())

    def invalidate() -> None:
        current = app()
        if current is not None:
            current.invalidate()

    def publish_debug() -> None:
        if _debug_state is None:
            return
        current = app()
        current_buffer = None
        if current is not None:
            try:
                current_buffer = current.layout.current_buffer
            except Exception:
                current_buffer = None
        _debug_state.update({
            "ready": bool(current),
            "menu_index": index,
            "panel_mode": panel_mode,
            "panel_title": panel_title,
            "focus_zone": focus_zone,
            "compact": compact_mode(),
            "compact_page": compact_page,
            "busy": busy,
            "log_follow_tail": log_follow_tail,
            "right_scroll": getattr(readonly_pane, "vertical_scroll", 0) if "readonly_pane" in locals() else 0,
            "popup_visible": popup_visible,
            "popup_kind": popup_kind,
            "left_edit_kind": left_edit_kind,
            "right_editor_kind": right_editor_kind,
            "buffer_name": getattr(current_buffer, "name", "") if current_buffer is not None else "",
            "buffer_has_completer": bool(getattr(current_buffer, "completer", None)) if current_buffer is not None else False,
            "completion_active": bool(getattr(current_buffer, "complete_state", None)) if current_buffer is not None else False,
        })

    def set_info(*lines: object) -> None:
        nonlocal info_lines
        info_lines = [str(line) for line in lines if str(line)] or ["Gotowe."]
        invalidate()

    def refresh_rows() -> list[str]:
        nonlocal rows_cache
        rows_cache = main_menu_rows(state)
        return rows_cache

    def active_left_editor() -> Any | None:
        if left_editing_index is None:
            return None
        return left_editors_by_kind.get(left_edit_kind)

    def current_left_target() -> Any:
        editor = active_left_editor()
        return editor.window if editor is not None else menu_window

    def current_right_target() -> Any:
        if panel_mode in {"verify_input", "extract_sidecar", "extract_destination", "options_limit_edit", "exclusion_edit"}:
            return right_editor.window
        if panel_mode == "action":
            return action_window
        if panel_mode in {"choice", "exit_choice"}:
            return choice_window
        if panel_mode == "options":
            return options_window
        if panel_mode in {"exclusions", "exclusion_list"}:
            return exclusions_window
        return readonly_window

    def focus_target(target: Any, zone: str, *, switch_page: bool = True) -> None:
        nonlocal focus_zone, compact_page
        focus_zone = zone
        if compact_mode() and switch_page:
            compact_page = "menu" if zone == "menu" else "detail"
        current = app()
        if current is None:
            return
        try:
            current.layout.update_parents_relations()
            current.layout.focus(target)
        except Exception as exc:
            info_lines[:] = ["Błąd routingu fokusu.", f"{type(exc).__name__}: {exc}"]
        current.invalidate()
        publish_debug()

    def focus_left() -> None:
        focus_target(current_left_target(), "menu")

    def focus_right() -> None:
        focus_target(current_right_target(), "detail")

    def panel_is_focused() -> bool:
        current = app()
        if current is None:
            return False
        try:
            return any(current.layout.has_focus(target) for target in right_focus_targets)
        except Exception:
            return focus_zone == "detail"

    def text_editor_active() -> bool:
        current = app()
        if current is None:
            return False
        try:
            editors = [source_editor, output_editor, name_editor, right_editor]
            return any(current.layout.has_focus(editor.window) for editor in editors)
        except Exception:
            return False

    def sync_menu_preview(*, force_panel: bool = True) -> None:
        nonlocal panel_mode, panel_title, panel_lines
        rows = refresh_rows()
        details = main_menu_details(state)
        selected_title = rows[index]
        selected_detail = details[index]
        info_lines[:] = [selected_title, selected_detail]
        if force_panel and not busy:
            panel_mode = "overview"
            panel_title = selected_title
            panel_lines = [selected_detail]
        invalidate()

    def show_panel(title: str, mode: str, lines: Sequence[object], *, focus: bool = True) -> None:
        nonlocal panel_title, panel_mode, panel_lines
        panel_title = title
        panel_mode = mode
        panel_lines = [str(line) for line in lines]
        if focus:
            focus_right()
        invalidate()

    def open_action(kind: str, title: str, description: str, button: str) -> None:
        nonlocal action_kind, panel_title, panel_mode, panel_lines
        action_kind = kind
        panel_title = title
        panel_mode = "action"
        panel_lines = [description, "", button]
        set_info(title, "Enter/LPM uruchamia • Esc/PPM wraca")
        focus_right()

    def set_choice(
        key: str,
        title: str,
        values: Sequence[Any],
        labels: Sequence[str],
        descriptions: Sequence[str],
        current: int,
        *,
        exit_mode: bool = False,
    ) -> None:
        nonlocal choice_key, panel_title, panel_mode
        nonlocal choice_values, choice_labels, choice_descriptions, choice_index
        choice_key = key
        panel_title = title
        panel_mode = "exit_choice" if exit_mode else "choice"
        choice_values = list(values)
        choice_labels = list(labels)
        choice_descriptions = list(descriptions)
        choice_index = max(0, min(int(current), max(0, len(choice_values) - 1)))
        set_info(title, choice_descriptions[choice_index] if choice_descriptions else "Wybierz wartość.")
        focus_right()

    def open_popup(
        kind: str,
        values: Sequence[Any],
        labels: Sequence[str],
        descriptions: Sequence[str],
        current: int,
    ) -> None:
        nonlocal popup_visible, popup_kind, popup_values, popup_labels, popup_descriptions, popup_index
        popup_kind = kind
        popup_values = list(values)
        popup_labels = list(labels)
        popup_descriptions = list(descriptions)
        popup_index = max(0, min(current, max(0, len(popup_values) - 1)))
        popup_visible = True
        set_info("Wybór w oknie modalnym.", popup_descriptions[popup_index] if popup_descriptions else "")
        focus_target(popup_window, "detail")

    def popup_move(direction: int) -> None:
        nonlocal popup_index
        if not popup_values:
            return
        popup_index = (popup_index + direction) % len(popup_values)
        set_info(popup_labels[popup_index], popup_descriptions[popup_index] if popup_descriptions else "")
        invalidate()

    def popup_commit() -> None:
        nonlocal popup_visible, panel_mode
        if not popup_values:
            return
        value = popup_values[popup_index]
        if popup_kind == "compression":
            state.compression_level = int(value)
        elif popup_kind == "format":
            state.archive_format = str(value)
        state.dirty = True
        popup_visible = False
        panel_mode = "options"
        refresh_rows()
        set_info("Zmieniono ustawienie.", popup_labels[popup_index])
        focus_right()

    def popup_cancel() -> None:
        nonlocal popup_visible
        popup_visible = False
        set_info("Wybór anulowany.")
        focus_right()

    def start_left_edit(row_index: int) -> None:
        nonlocal left_editing_index, left_edit_kind
        left_editing_index = row_index
        left_edit_kind = DASHBOARD_LEFT_EDITABLE[row_index]
        if left_edit_kind == "source":
            value = str(state.source)
        elif left_edit_kind == "out_dir":
            value = str(state.out_dir)
        else:
            value = state.archive_basename
        editor = left_editors_by_kind[left_edit_kind]
        editor.buffer.text = value
        editor.buffer.cursor_position = len(value)
        editor.buffer.cancel_completion()
        set_info("Edycja w aktywnym wierszu.", "Tab/Ctrl+Spacja pokazuje podpowiedzi • Enter zapisuje • Esc anuluje")
        focus_left()
        if editor.buffer.completer is not None:
            editor.buffer.start_completion(select_first=False)

    def finish_left_edit(*, commit: bool) -> None:
        nonlocal left_editing_index, left_edit_kind
        if left_editing_index is None:
            return
        editor = left_editors_by_kind[left_edit_kind]
        value = editor.text.strip()
        try:
            if commit:
                if left_edit_kind == "source":
                    root, version = validate_jazn_root(value)
                    state.source = root
                    set_info("System Jaźni potwierdzony.", f"version.py: {version.full_version}")
                elif left_edit_kind == "out_dir":
                    if not value:
                        raise PackError("Zapis archiwum nie może mieć pustej ścieżki.")
                    state.out_dir = Path(value).expanduser().resolve()
                    set_info("Zapis archiwum ustawiony.", str(state.out_dir))
                else:
                    state.archive_basename = sanitize_archive_stem(value)
                    set_info("Nazwa ustawiona.", state.archive_basename)
                state.dirty = True
            else:
                set_info("Edycja anulowana.")
        except Exception as exc:
            set_info("Błąd wartości.", str(exc))
            focus_left()
            return
        left_editing_index = None
        left_edit_kind = ""
        refresh_rows()
        focus_left()

    def option_value(row_index: int) -> str:
        values = (
            state.archive_format,
            f"{state.part_size_mb} MiB",
            f"DEFLATE {state.compression_level}",
            "TAK" if state.force else "NIE",
            "TAK" if state.sidecars else "NIE",
            state.ui_mode,
            "TAK" if state.update_source_manifest else "NIE",
            "TAK" if state.compatibility_checks else "NIE",
        )
        return values[row_index]

    def options_move(direction: int) -> None:
        nonlocal options_index
        options_index = (options_index + direction) % len(OPTIONS_LABELS)
        set_info(OPTIONS_LABELS[options_index], OPTIONS_DESCRIPTIONS[options_index])
        invalidate()

    def start_limit_edit() -> None:
        nonlocal panel_mode, right_editor_kind
        panel_mode = "options_limit_edit"
        right_editor_kind = "part_size_mb"
        right_editor.buffer.text = str(state.part_size_mb)
        right_editor.buffer.cursor_position = len(right_editor.buffer.text)
        set_info("Edycja limitu.", "Enter zapisuje • Esc/PPM anuluje")
        focus_right()

    def finish_limit_edit(*, commit: bool) -> None:
        nonlocal panel_mode, right_editor_kind
        try:
            if commit:
                amount = int(right_editor.text.strip())
                if amount <= 0:
                    raise ValueError("Limit musi być większy od zera.")
                state.part_size_mb = amount
                state.dirty = True
                set_info("Limit zapisany.", f"{amount} MiB")
            else:
                set_info("Edycja limitu anulowana.")
            panel_mode = "options"
            right_editor_kind = ""
            focus_right()
        except Exception as exc:
            set_info("Błąd wartości.", str(exc))
            focus_right()

    def activate_option() -> None:
        if options_index == 0:
            values = list(FORMAT_CHOICES)
            descriptions = [
                "Automatyczny wybór: independent, chyba że potrzebny jest binary.",
                "Każdy wolumin jest samodzielnym ZIP-em.",
                "Jeden logiczny ZIP dzielony bajtowo na .001, .002…",
            ]
            open_popup("format", values, values, descriptions, values.index(state.archive_format))
        elif options_index == 1:
            start_limit_edit()
        elif options_index == 2:
            values = list(range(10))
            labels = [f"DEFLATE {value}" for value in values]
            descriptions = [f"Poziom {value}: 0 = najszybciej, 9 = zwykle najmniejszy plik." for value in values]
            open_popup("compression", values, labels, descriptions, state.compression_level)
        elif options_index == 3:
            state.force = not state.force
            state.dirty = True
            set_info("Nadpisywanie zmienione.", f"Teraz: {'TAK' if state.force else 'NIE'}")
        elif options_index == 4:
            state.sidecars = not state.sidecars
            state.dirty = True
            set_info("Pliki pomocnicze zmienione.", f"Teraz: {'TAK' if state.sidecars else 'NIE'}")
        elif options_index == 5:
            state.ui_mode = "tekstowy" if state.ui_mode == "kursorowy" else "kursorowy"
            state.dirty = True
            set_info("Preferowany interfejs zmieniony.", f"Następne uruchomienie: {state.ui_mode}")
        elif options_index == 6:
            state.update_source_manifest = not state.update_source_manifest
            state.dirty = True
            set_info("Aktualizacja mapy zmieniona.", f"Teraz: {'TAK' if state.update_source_manifest else 'NIE'}")
        elif options_index == 7:
            state.compatibility_checks = not state.compatibility_checks
            state.dirty = True
            set_info("Testy zgodności ZIP zmienione.", f"Teraz: {'TAK' if state.compatibility_checks else 'NIE'}")
        refresh_rows()
        invalidate()

    def current_exclusion_list() -> list[str]:
        return state.base_excludes if exclusion_list_kind == "base" else state.custom_excludes

    def exclusions_move(direction: int) -> None:
        nonlocal exclusions_index, exclusion_item_index
        if panel_mode == "exclusions":
            exclusions_index = (exclusions_index + direction) % len(EXCLUSION_ACTIONS)
            set_info(EXCLUSION_ACTIONS[exclusions_index], EXCLUSION_DESCRIPTIONS[exclusions_index])
        elif panel_mode == "exclusion_list":
            items = current_exclusion_list()
            if items:
                exclusion_item_index = (exclusion_item_index + direction) % len(items)
                set_info(items[exclusion_item_index], "Enter edytuje • A dodaje • D usuwa")
        invalidate()

    def open_exclusion_list(kind: str, *, delete_mode: bool = False) -> None:
        nonlocal panel_mode, exclusion_list_kind, exclusion_item_index, exclusion_delete_mode
        exclusion_list_kind = kind
        exclusion_item_index = 0
        exclusion_delete_mode = delete_mode
        panel_mode = "exclusion_list"
        items = current_exclusion_list()
        set_info(
            "Lista podstawowa" if kind == "base" else "Lista ręczna",
            "Enter usuwa wybrany wpis." if delete_mode else "Enter edytuje • A dodaje • D usuwa",
            f"Wpisów: {len(items)}",
        )
        focus_right()

    def start_exclusion_edit(*, add: bool, kind: str | None = None) -> None:
        nonlocal panel_mode, right_editor_kind, exclusion_edit_index, exclusion_list_kind
        if kind is not None:
            exclusion_list_kind = kind
        items = current_exclusion_list()
        exclusion_edit_index = None if add else exclusion_item_index
        right_editor_kind = "exclusion_add" if add else "exclusion_edit"
        panel_mode = "exclusion_edit"
        value = "" if add else (items[exclusion_item_index] if items else "")
        right_editor.buffer.text = value
        right_editor.buffer.cursor_position = len(value)
        set_info("Dodawanie wzorca." if add else "Edycja wzorca.", "Enter zapisuje • Esc anuluje")
        focus_right()

    def finish_exclusion_edit(*, commit: bool) -> None:
        nonlocal panel_mode, right_editor_kind, exclusion_edit_index, exclusion_item_index
        items = current_exclusion_list()
        value = right_editor.text.strip()
        if commit:
            if not value:
                set_info("Wzorzec nie może być pusty.")
                return
            if exclusion_edit_index is None:
                items.append(value)
                exclusion_item_index = len(items) - 1
            else:
                items[exclusion_edit_index] = value
                exclusion_item_index = exclusion_edit_index
            if exclusion_list_kind == "manual":
                state.manual_excludes_enabled = bool(state.custom_excludes)
            state.dirty = True
            refresh_rows()
            set_info("Wzorzec zapisany.", value)
        else:
            set_info("Edycja wzorca anulowana.")
        panel_mode = "exclusion_list"
        right_editor_kind = ""
        exclusion_edit_index = None
        focus_right()

    def delete_selected_exclusion() -> None:
        nonlocal exclusion_item_index
        items = current_exclusion_list()
        if not items:
            set_info("Lista jest pusta.")
            return
        removed = items.pop(exclusion_item_index)
        exclusion_item_index = max(0, min(exclusion_item_index, len(items) - 1))
        if exclusion_list_kind == "manual":
            state.manual_excludes_enabled = bool(state.custom_excludes) and state.manual_excludes_enabled
        state.dirty = True
        refresh_rows()
        set_info("Usunięto wzorzec.", removed)

    def activate_exclusion() -> None:
        nonlocal panel_mode
        if panel_mode == "exclusions":
            if exclusions_index == 0:
                open_exclusion_list("base")
            elif exclusions_index == 1:
                open_exclusion_list("manual")
            elif exclusions_index == 2:
                start_exclusion_edit(add=True, kind="manual")
            elif exclusions_index == 3:
                open_exclusion_list("manual", delete_mode=True)
            elif exclusions_index == 4:
                if state.custom_excludes:
                    state.manual_excludes_enabled = not state.manual_excludes_enabled
                    state.dirty = True
                    set_info("Ręczne wykluczenia przełączone.", f"Teraz: {'WŁĄCZONE' if state.manual_excludes_enabled else 'WYŁĄCZONE'}")
                else:
                    state.manual_excludes_enabled = False
                    set_info("Lista ręczna jest pusta.")
                refresh_rows()
            elif exclusions_index == 5:
                state.base_excludes = list(DEFAULT_BASE_EXCLUDES)
                state.dirty = True
                set_info("Przywrócono wykluczenia podstawowe.", f"Wpisów: {len(state.base_excludes)}")
        elif panel_mode == "exclusion_list":
            if exclusion_delete_mode:
                delete_selected_exclusion()
            elif current_exclusion_list():
                start_exclusion_edit(add=False)
            else:
                start_exclusion_edit(add=True)

    def enqueue_worker(kind: str, payload: Any = None) -> None:
        worker_events.put((kind, payload))
        invalidate()

    def start_worker(title: str, operation: Any) -> None:
        nonlocal busy, panel_title, panel_mode, panel_lines, log_follow_tail
        if busy:
            set_info("Operacja już trwa.")
            return
        busy = True
        panel_title = title
        panel_mode = "log"
        panel_lines = ["Uruchamianie…"]
        log_follow_tail = True
        set_info("Operacja działa w tle.", "Interfejs pozostaje responsywny.")
        focus_right()

        def emit(line: str) -> None:
            enqueue_worker("line", str(line))

        def target() -> None:
            try:
                _set_operation_output_sink(emit)
                result = operation()
                enqueue_worker("done", result)
            except Exception as exc:
                enqueue_worker("error", f"{type(exc).__name__}: {exc}")
            finally:
                _set_operation_output_sink(None)

        threading.Thread(target=target, name="jazn-pack-v7-worker", daemon=True).start()

    def drain_worker_events() -> None:
        nonlocal busy, panel_mode, panel_lines, log_follow_tail
        while True:
            try:
                kind, payload = worker_events.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                panel_lines.append(str(payload))
                if len(panel_lines) > 800:
                    del panel_lines[:-800]
            elif kind == "done":
                busy = False
                panel_mode = "result"
                if payload is not None:
                    if isinstance(payload, str):
                        panel_lines.append(payload)
                    else:
                        panel_lines.extend(str(item) for item in payload)
                set_info("Operacja zakończona poprawnie.", "Wynik pozostaje widoczny po prawej stronie.")
            elif kind == "error":
                busy = False
                panel_mode = "error"
                panel_lines.extend(["", f"BŁĄD: {payload}"])
                set_info("Operacja zakończyła się błędem.", payload)

    def result_lines(results: Sequence[PackageResult]) -> list[str]:
        lines: list[str] = []
        for result in results:
            lines.extend([
                f"Paczka: {result.package_name}",
                f"Profil: {result.profile}",
                f"Format: {result.archive_format}",
                f"Pliki planu: {result.plan.file_count}",
                f"Woluminy: {len(result.outputs)}",
                f"Plan SHA-256: {result.plan.plan_sha256()}",
                f"Set SHA-256: {result.package_set_sha256}",
                "Pliki wynikowe:",
            ])
            lines.extend(f"  ✓ {path}" for path in result.committed_paths)
            lines.extend(compatibility_summary(result))
            lines.append("")
        return lines

    def execute_action() -> None:
        nonlocal panel_mode, panel_lines
        if busy:
            return
        if action_kind == "pack":
            def operation() -> list[str]:
                validate_jazn_root(state.source)
                options = state.to_options()
                plans = build_plans_for_options(options)
                results = run_pack_with_plans(options, plans)
                return result_lines(results)
            start_worker("PAKOWANIE", operation)
        elif action_kind == "plan":
            start_worker(
                "KANONICZNY PLAN",
                lambda: plan_summary(build_plans_for_options(state.to_options())),
            )
        elif action_kind == "manifest":
            def update_map() -> list[str]:
                root, _ = validate_jazn_root(state.source)
                options = state.to_options()
                plan = build_plan(
                    root, "system", options.custom_excludes,
                    base_excludes=options.base_excludes,
                    manual_excludes_enabled=options.manual_excludes_enabled,
                )
                path = write_source_manifest_from_plan(plan)
                if path is None:
                    raise PackError("Plan nie zawiera wirtualnego PACKAGE_INTEGRITY_MANIFEST.json.")
                return [
                    f"Zapisano: {path}",
                    f"Wersja: {plan.version.full_version}",
                    f"Pliki statyczne: {len([entry for entry in plan.entries if entry.classification == 'static_project_file'])}",
                    f"SHA-256: {sha256_file(path)}",
                ]
            start_worker("AKTUALIZUJ MAPĘ PLIKÓW", update_map)
        elif action_kind == "save":
            path = save_interactive_state(state)
            panel_mode = "result"
            panel_lines = ["Ustawienia zapisano atomowo.", str(path)]
            set_info("Ustawienia zapisane.", str(path))

    def commit_right_editor() -> None:
        nonlocal panel_mode, panel_title, panel_lines, extract_sidecar_value, right_editor_kind
        if panel_mode == "options_limit_edit":
            finish_limit_edit(commit=True)
            return
        if panel_mode == "exclusion_edit":
            finish_exclusion_edit(commit=True)
            return
        value = right_editor.text.strip()
        if panel_mode == "verify_input":
            start_worker(
                "WERYFIKACJA PACZKI",
                lambda: [json.dumps(
                    verify_package_sidecar(resolve_sidecar_path(value, fallback_folder=state.out_dir)),
                    ensure_ascii=False, indent=2,
                )],
            )
        elif panel_mode == "extract_sidecar":
            extract_sidecar_value = value
            panel_title = "BEZPIECZNE ROZPAKOWANIE — CEL"
            panel_mode = "extract_destination"
            panel_lines = ["Podaj katalog docelowy.", "Enter rozpoczyna weryfikację i rozpakowanie."]
            destination = state.source.parent / "jazn_runtime_test"
            right_editor.buffer.text = str(destination)
            right_editor.buffer.cursor_position = len(right_editor.buffer.text)
            right_editor_kind = "extract_destination"
            focus_right()
            right_editor.buffer.start_completion(select_first=False)
        elif panel_mode == "extract_destination":
            destination_value = value
            sidecar_value = extract_sidecar_value
            start_worker(
                "BEZPIECZNE ROZPAKOWANIE",
                lambda: [json.dumps(
                    extract_package_sidecar(
                        resolve_sidecar_path(sidecar_value, fallback_folder=state.out_dir),
                        Path(destination_value), clean=False, force=True,
                    ), ensure_ascii=False, indent=2,
                )],
            )

    def apply_selected_choice() -> None:
        nonlocal panel_mode
        if not choice_values:
            return
        value = choice_values[choice_index]
        if choice_key == "profile":
            state.profile = str(value)
            state.dirty = True
            refresh_rows()
            panel_mode = "result"
            panel_lines[:] = ["Wybrano profil:", choice_labels[choice_index]]
            set_info("Profil zmieniony.", choice_labels[choice_index])
            focus_right()
        elif choice_key == "exit":
            if value == "save":
                save_interactive_state(state)
                app().exit(result="exit")
            elif value == "discard":
                app().exit(result="exit")
            else:
                sync_menu_preview(force_panel=True)
                focus_left()

    def open_menu_action(row_index: int) -> None:
        nonlocal index, panel_title, panel_mode, panel_lines, right_editor_kind
        nonlocal exclusions_index, compact_page
        index = row_index
        refresh_rows()
        detail = main_menu_details(state)[row_index]
        if row_index in DASHBOARD_LEFT_EDITABLE:
            start_left_edit(row_index)
        elif row_index == 0:
            open_action("pack", "PAKOWANIE", detail, "Uruchom pakowanie")
        elif row_index == 1:
            open_action("plan", "KANONICZNY PLAN", detail, "Zbuduj plan")
        elif row_index == 2:
            open_action("manifest", "AKTUALIZUJ MAPĘ PLIKÓW", detail, "Zapisz mapę plików")
        elif row_index == 5:
            values = list(PROFILE_CHOICES)
            set_choice(
                "profile", "PROFIL", values,
                [PROFILE_DISPLAY[value] for value in values],
                [PROFILE_DESCRIPTIONS[value] for value in values],
                values.index(state.profile),
            )
        elif row_index == 6:
            panel_title = "WERYFIKACJA PACZKI"
            panel_mode = "verify_input"
            panel_lines = [detail, "Wpisz *.package.json albo katalog."]
            right_editor.buffer.text = str(suggested_sidecar_path(state))
            right_editor.buffer.cursor_position = len(right_editor.buffer.text)
            right_editor_kind = "verify"
            set_info("Narzędzie: weryfikacja.", "Tab/Ctrl+Spacja pokazuje ścieżki i pliki *.package.json")
            focus_right()
            right_editor.buffer.start_completion(select_first=False)
        elif row_index == 7:
            panel_title = "BEZPIECZNE ROZPAKOWANIE — SIDECAR"
            panel_mode = "extract_sidecar"
            panel_lines = [detail, "Najpierw podaj sidecar lub katalog."]
            right_editor.buffer.text = str(suggested_sidecar_path(state))
            right_editor.buffer.cursor_position = len(right_editor.buffer.text)
            right_editor_kind = "extract_sidecar"
            set_info("Narzędzie: rozpakowanie.", "Tab/Ctrl+Spacja pokazuje ścieżki i pliki *.package.json")
            focus_right()
            right_editor.buffer.start_completion(select_first=False)
        elif row_index == 9:
            panel_title = "OPCJE"
            panel_mode = "options"
            set_info(OPTIONS_LABELS[options_index], OPTIONS_DESCRIPTIONS[options_index])
            focus_right()
        elif row_index == 10:
            panel_title = "WYKLUCZENIA"
            panel_mode = "exclusions"
            exclusions_index = 0
            set_info(EXCLUSION_ACTIONS[0], EXCLUSION_DESCRIPTIONS[0])
            focus_right()
        elif row_index == 11:
            open_action("save", "ZAPISZ USTAWIENIA", detail, "Zapisz ustawienia")
        elif row_index == 12:
            set_choice(
                "exit", "WYJŚCIE", ["save", "discard", "cancel"],
                ["Zapisz i wyjdź", "Wyjdź bez zapisu", "Wróć"],
                ["Zapisuje ustawienia i kończy program.", "Kończy bez zapisu bieżących zmian.", "Wraca do menu."],
                2, exit_mode=True,
            )
        compact_page = "detail" if panel_mode != "overview" else compact_page
        invalidate()

    def menu_move(action: str) -> None:
        nonlocal index, compact_page
        index = menu_navigation_index(index, action, len(refresh_rows()))
        compact_page = "menu"
        sync_menu_preview(force_panel=True)
        focus_left()

    def choice_move(direction: int) -> None:
        nonlocal choice_index
        if choice_values:
            choice_index = (choice_index + direction) % len(choice_values)
            set_info(choice_labels[choice_index], choice_descriptions[choice_index] if choice_descriptions else "")
            invalidate()

    def execute_right() -> None:
        if panel_mode == "action":
            execute_action()
        elif panel_mode in {"choice", "exit_choice"}:
            apply_selected_choice()
        elif panel_mode == "options":
            activate_option()
        elif panel_mode in {"exclusions", "exclusion_list"}:
            activate_exclusion()

    # --- renderery i trwałe kontrolki -------------------------------------------------

    def menu_mouse_handler(row_index: int):
        def handler(mouse_event: Any):
            nonlocal index
            action = _mouse_action(mouse_event)
            if action in {"up", "down"}:
                menu_move(action)
                return None
            if action == "activate":
                index = row_index
                sync_menu_preview(force_panel=True)
                open_menu_action(row_index)
                return None
            if action == "back":
                focus_left()
                return None
            return NotImplemented
        return handler

    def render_menu_range(start: int = 0, end: int | None = None, *, omit: int | None = None):
        rows = refresh_rows()
        end = len(rows) if end is None else end
        fragments: list[tuple[Any, ...]] = []
        for row_index in range(start, end):
            section = DASHBOARD_GROUPS.get(row_index)
            if section:
                label = f"── {section} "
                fragments.append(("class:menu.section", "  " + label + APP_THEME.section_fill * max(3, APP_THEME.left_panel_width - len(label) - 4) + "\n"))
            if row_index == omit:
                continue
            if row_index == index:
                fragments.append(("[SetCursorPosition]", ""))
            style = "class:menu.selected" if row_index == index else "class:menu.item"
            marker = "  ▶ " if row_index == index else "    "
            fragments.append((style, marker + rows[row_index] + "\n", menu_mouse_handler(row_index)))
        return fragments

    menu_control = control_cls(text=lambda: render_menu_range(), focusable=True, show_cursor=False)
    menu_window = window_cls(content=menu_control, wrap_lines=False, always_hide_cursor=True)
    menu_pane_wide = scrollable_cls(
        hsplit_cls([menu_window]), width=APP_THEME.left_dimension(),
        show_scrollbar=True, display_arrows=True,
        keep_cursor_visible=True, keep_focused_window_visible=True,
    )
    menu_pane_compact = scrollable_cls(
        hsplit_cls([menu_window]),
        show_scrollbar=True, display_arrows=True,
        keep_cursor_visible=True, keep_focused_window_visible=True,
    )

    def build_left_edit_container(row_index: int, compact: bool) -> Any:
        editor = left_editors_by_row[row_index]
        label = {3: "System Jaźni", 4: "Zapis archiwum", 8: "Nazwa"}[row_index]
        before_control = control_cls(text=lambda i=row_index: render_menu_range(0, i))
        after_control = control_cls(text=lambda i=row_index: render_menu_range(i + 1, None))
        edit_row = vsplit_cls([
            window_cls(width=max(15, len(label) + 5), height=1, content=control_cls(text=[("class:menu.selected", f"  ▶ {label}: [")])),
            editor,
            window_cls(width=1, height=1, content=control_cls(text=[("class:menu.selected", "]")])),
        ], height=1)
        content = hsplit_cls([
            window_cls(content=before_control, dont_extend_height=True),
            edit_row,
            window_cls(content=after_control, dont_extend_height=True),
        ])
        width = None if compact else APP_THEME.left_dimension()
        return scrollable_cls(content, width=width, show_scrollbar=True, display_arrows=True, keep_focused_window_visible=True)

    left_edit_wide = {row: build_left_edit_container(row, False) for row in DASHBOARD_LEFT_EDITABLE}
    left_edit_compact = {row: build_left_edit_container(row, True) for row in DASHBOARD_LEFT_EDITABLE}

    def left_content_wide() -> Any:
        if left_editing_index is not None:
            return left_edit_wide[left_editing_index]
        return menu_pane_wide

    def left_content_compact() -> Any:
        if left_editing_index is not None:
            return left_edit_compact[left_editing_index]
        return menu_pane_compact

    def render_readonly():
        wrap_width = max(18, APP_THEME.current_metrics()["right_text_width"])
        fragments: list[tuple[str, str]] = [("class:panel.title", f"  {panel_title}\n")]
        fragments.append(("class:panel.rule", "  " + APP_THEME.section_fill * max(8, wrap_width - 2) + "\n"))
        source = panel_lines[-800:] if panel_mode in {"log", "result", "error"} else panel_lines
        for raw in source:
            style = "class:panel.error" if str(raw).startswith("BŁĄD") else "class:panel.text"
            for line in _wrap(raw, wrap_width):
                fragments.append((style, "  " + line + "\n"))
        if panel_mode == "overview":
            fragments.append(("[SetCursorPosition]", ""))
        return fragments

    readonly_control = control_cls(text=render_readonly, focusable=True, show_cursor=False)
    readonly_window = window_cls(content=readonly_control, wrap_lines=True, always_hide_cursor=True, dont_extend_height=True)
    readonly_pane = scrollable_cls(
        hsplit_cls([readonly_window]),
        show_scrollbar=True, display_arrows=True,
        keep_cursor_visible=False, keep_focused_window_visible=False,
    )

    def action_mouse_handler(mouse_event: Any):
        action = _mouse_action(mouse_event)
        if action == "activate":
            execute_action()
            return None
        if action == "back":
            sync_menu_preview(force_panel=True)
            focus_left()
            return None
        return NotImplemented

    def render_action():
        wrap_width = max(18, APP_THEME.current_metrics()["right_text_width"])
        fragments: list[tuple[Any, ...]] = [("class:panel.title", f"  {panel_title}\n")]
        fragments.append(("class:panel.rule", "  " + APP_THEME.section_fill * max(8, wrap_width - 2) + "\n"))
        for raw in panel_lines[:-1]:
            for line in _wrap(raw, wrap_width):
                fragments.append(("class:panel.text", "  " + line + "\n"))
        fragments.extend([
            ("class:panel.text", "\n"),
            ("[SetCursorPosition]", ""),
            ("class:menu.selected", f"  ▶ {panel_lines[-1] if panel_lines else 'Uruchom'}\n", action_mouse_handler),
        ])
        return fragments

    action_control = control_cls(text=render_action, focusable=True, show_cursor=False)
    action_window = window_cls(content=action_control, wrap_lines=True, always_hide_cursor=True, dont_extend_height=True)
    action_pane = scrollable_cls(hsplit_cls([action_window]), show_scrollbar=True, display_arrows=True, keep_cursor_visible=True)

    def list_mouse_handler(kind: str, item_index: int):
        def handler(mouse_event: Any):
            nonlocal choice_index, options_index, exclusions_index, exclusion_item_index
            action = _mouse_action(mouse_event)
            if action == "activate":
                if kind == "choice":
                    choice_index = item_index
                    apply_selected_choice()
                elif kind == "options":
                    options_index = item_index
                    activate_option()
                elif kind == "exclusions":
                    exclusions_index = item_index
                    activate_exclusion()
                elif kind == "exclusion_list":
                    exclusion_item_index = item_index
                    activate_exclusion()
                return None
            if action == "back":
                sync_menu_preview(force_panel=True)
                focus_left()
                return None
            if action == "up":
                if kind == "choice": choice_move(-1)
                elif kind == "options": options_move(-1)
                else: exclusions_move(-1)
                return None
            if action == "down":
                if kind == "choice": choice_move(1)
                elif kind == "options": options_move(1)
                else: exclusions_move(1)
                return None
            return NotImplemented
        return handler

    def render_choice():
        wrap_width = max(18, APP_THEME.current_metrics()["right_text_width"])
        fragments: list[tuple[Any, ...]] = [("class:panel.title", f"  {panel_title}\n")]
        fragments.append(("class:panel.rule", "  " + APP_THEME.section_fill * max(8, wrap_width - 2) + "\n"))
        for item_index, label in enumerate(choice_labels):
            if item_index == choice_index:
                fragments.append(("[SetCursorPosition]", ""))
            style = "class:menu.selected" if item_index == choice_index else "class:panel.text"
            marker = "  ▶ " if item_index == choice_index else "    "
            fragments.append((style, marker + label + "\n", list_mouse_handler("choice", item_index)))
        if choice_descriptions:
            fragments.append(("class:panel.rule", "\n  " + APP_THEME.section_fill * max(8, wrap_width - 2) + "\n"))
            for line in _wrap(choice_descriptions[choice_index], wrap_width):
                fragments.append(("class:panel.text", "  " + line + "\n"))
        return fragments

    choice_control = control_cls(text=render_choice, focusable=True, show_cursor=False)
    choice_window = window_cls(content=choice_control, wrap_lines=True, always_hide_cursor=True, dont_extend_height=True)
    choice_pane = scrollable_cls(hsplit_cls([choice_window]), show_scrollbar=True, display_arrows=True, keep_cursor_visible=True)

    def render_options():
        wrap_width = max(18, APP_THEME.current_metrics()["right_text_width"])
        fragments: list[tuple[Any, ...]] = [("class:panel.title", "  OPCJE\n")]
        fragments.append(("class:panel.rule", "  " + APP_THEME.section_fill * max(8, wrap_width - 2) + "\n"))
        for item_index, label in enumerate(OPTIONS_LABELS):
            if item_index == options_index:
                fragments.append(("[SetCursorPosition]", ""))
            style = "class:menu.selected" if item_index == options_index else "class:panel.text"
            marker = "  ▶ " if item_index == options_index else "    "
            text = f"{label}: [{option_value(item_index)}]"
            fragments.append((style, marker + text + "\n", list_mouse_handler("options", item_index)))
        fragments.append(("class:panel.rule", "\n  " + APP_THEME.section_fill * max(8, wrap_width - 2) + "\n"))
        for line in _wrap(OPTIONS_DESCRIPTIONS[options_index], wrap_width):
            fragments.append(("class:panel.label", "  " + line + "\n"))
        return fragments

    options_control = control_cls(text=render_options, focusable=True, show_cursor=False)
    options_window = window_cls(content=options_control, wrap_lines=True, always_hide_cursor=True, dont_extend_height=True)
    options_pane = scrollable_cls(hsplit_cls([options_window]), show_scrollbar=True, display_arrows=True, keep_cursor_visible=True)

    def render_exclusions():
        wrap_width = max(18, APP_THEME.current_metrics()["right_text_width"])
        fragments: list[tuple[Any, ...]] = [("class:panel.title", "  WYKLUCZENIA\n")]
        fragments.append(("class:panel.rule", "  " + APP_THEME.section_fill * max(8, wrap_width - 2) + "\n"))
        if panel_mode == "exclusions":
            for item_index, label in enumerate(EXCLUSION_ACTIONS):
                if item_index == exclusions_index:
                    fragments.append(("[SetCursorPosition]", ""))
                style = "class:menu.selected" if item_index == exclusions_index else "class:panel.text"
                marker = "  ▶ " if item_index == exclusions_index else "    "
                fragments.append((style, marker + label + "\n", list_mouse_handler("exclusions", item_index)))
            fragments.append(("class:panel.rule", "\n  " + APP_THEME.section_fill * max(8, wrap_width - 2) + "\n"))
            for line in _wrap(EXCLUSION_DESCRIPTIONS[exclusions_index], wrap_width):
                fragments.append(("class:panel.label", "  " + line + "\n"))
            fragments.append(("class:panel.text", f"\n  Podstawowe: {len(state.base_excludes)} • Ręczne: {len(state.custom_excludes)} • Aktywne: {'TAK' if state.manual_excludes_enabled and state.custom_excludes else 'NIE'}\n"))
        else:
            items = current_exclusion_list()
            title = "PODSTAWOWE" if exclusion_list_kind == "base" else "RĘCZNE"
            fragments.append(("class:panel.title", f"  LISTA {title}\n"))
            if not items:
                fragments.append(("[SetCursorPosition]", ""))
                fragments.append(("class:message.warn", "  Lista jest pusta. Naciśnij A, aby dodać wpis.\n"))
            for item_index, item in enumerate(items):
                if item_index == exclusion_item_index:
                    fragments.append(("[SetCursorPosition]", ""))
                style = "class:menu.selected" if item_index == exclusion_item_index else "class:panel.text"
                marker = "  ▶ " if item_index == exclusion_item_index else "    "
                fragments.append((style, marker + item + "\n", list_mouse_handler("exclusion_list", item_index)))
            fragments.append(("class:panel.rule", "\n  " + APP_THEME.section_fill * max(8, wrap_width - 2) + "\n"))
            fragments.append(("class:panel.label", "  Enter = edytuj/usuń • A = dodaj • E = edytuj • D = usuń • Esc = wróć\n"))
        return fragments

    exclusions_control = control_cls(text=render_exclusions, focusable=True, show_cursor=False)
    exclusions_window = window_cls(content=exclusions_control, wrap_lines=True, always_hide_cursor=True, dont_extend_height=True)
    exclusions_pane = scrollable_cls(hsplit_cls([exclusions_window]), show_scrollbar=True, display_arrows=True, keep_cursor_visible=True)

    def render_editor_help():
        wrap_width = max(18, APP_THEME.current_metrics()["right_text_width"])
        fragments: list[tuple[str, str]] = [("class:panel.title", f"  {panel_title}\n")]
        fragments.append(("class:panel.rule", "  " + APP_THEME.section_fill * max(8, wrap_width - 2) + "\n"))
        for raw in panel_lines:
            for line in _wrap(raw, wrap_width):
                fragments.append(("class:panel.text", "  " + line + "\n"))
        return fragments

    editor_help_control = control_cls(text=render_editor_help)
    right_editor_container = hsplit_cls([
        window_cls(content=editor_help_control, height=5, wrap_lines=True),
        right_editor,
        window_cls(height=1, content=control_cls(text=[("class:panel.label", "  Enter zapisuje • Tab/Ctrl+Spacja podpowiada • Esc/PPM anuluje")])),
    ])

    def active_right_scroll_pane() -> Any | None:
        if panel_mode == "action":
            return action_pane
        if panel_mode in {"choice", "exit_choice"}:
            return choice_pane
        if panel_mode == "options":
            return options_pane
        if panel_mode in {"exclusions", "exclusion_list"}:
            return exclusions_pane
        if panel_mode in {"overview", "log", "result", "error"}:
            return readonly_pane
        return None

    def scroll_right_content(action: str) -> None:
        """Przewija aktywny prawy panel i steruje śledzeniem końca logu."""
        nonlocal log_follow_tail
        pane = active_right_scroll_pane()
        if pane is None:
            return
        page = max(4, terminal_columns() // 12, APP_THEME.page_step)
        if action == "home":
            pane.vertical_scroll = 0
        elif action == "end":
            pane.vertical_scroll = 10**9
        elif action == "up":
            pane.vertical_scroll = max(0, int(pane.vertical_scroll) - 1)
        elif action == "down":
            pane.vertical_scroll = max(0, int(pane.vertical_scroll) + 1)
        elif action == "pageup":
            pane.vertical_scroll = max(0, int(pane.vertical_scroll) - page)
        elif action == "pagedown":
            pane.vertical_scroll = max(0, int(pane.vertical_scroll) + page)
        if panel_mode in {"log", "result", "error"}:
            log_follow_tail = action == "end"
        invalidate()

    def readonly_mouse_handler(mouse_event: Any):
        action = _mouse_action(mouse_event)
        if action == "up":
            scroll_right_content("up")
            return None
        if action == "down":
            scroll_right_content("down")
            return None
        if action == "activate":
            focus_target(readonly_window, "detail")
            return None
        if action == "back":
            sync_menu_preview(force_panel=True)
            focus_left()
            return None
        return NotImplemented

    readonly_control.mouse_handler = readonly_mouse_handler

    def right_container() -> Any:
        if panel_mode == "action":
            return action_pane
        if panel_mode in {"choice", "exit_choice"}:
            return choice_pane
        if panel_mode == "options":
            return options_pane
        if panel_mode in {"exclusions", "exclusion_list"}:
            return exclusions_pane
        if panel_mode in {"verify_input", "extract_sidecar", "extract_destination", "options_limit_edit", "exclusion_edit"}:
            return right_editor_container
        return readonly_pane

    right_dynamic = dynamic_container_cls(right_container)

    def render_info():
        wrap_width = max(18, APP_THEME.current_metrics()["right_text_width"])
        fragments: list[tuple[str, str]] = [("class:info.title", "  INFORMACJE\n")]
        for raw in info_lines[-6:]:
            for line in _wrap(raw, wrap_width):
                fragments.append(("class:info.text", "  " + line + "\n"))
        if busy:
            fragments.append(("class:message.warn", "  Operacja w toku…\n"))
        return fragments

    info_control = control_cls(text=render_info, focusable=False, show_cursor=False)
    right_column = hsplit_cls([
        right_dynamic,
        window_cls(height=1, char=APP_THEME.section_fill, style="class:border"),
        window_cls(height=APP_THEME.info_dimension(), content=info_control, style="class:info", wrap_lines=True),
    ], width=APP_THEME.right_dimension())

    left_dynamic_wide = dynamic_container_cls(left_content_wide)
    left_dynamic_compact = dynamic_container_cls(left_content_compact)
    wide_split = vsplit_cls([
        left_dynamic_wide,
        window_cls(width=1, char=APP_THEME.border_char, style="class:border"),
        right_column,
    ])
    compact_menu_container = hsplit_cls([left_dynamic_compact])
    compact_detail_container = hsplit_cls([right_column])

    def body_container() -> Any:
        if compact_mode():
            return compact_menu_container if compact_page == "menu" else compact_detail_container
        return wide_split

    body_dynamic = dynamic_container_cls(body_container)

    def render_popup():
        fragments: list[tuple[Any, ...]] = [("class:popup.title", f"  {popup_kind.upper()}\n")]
        fragments.append(("class:popup.rule", "  " + APP_THEME.section_fill * max(12, APP_THEME.popup_width() - 7) + "\n"))

        def handler_for(item_index: int):
            def handler(mouse_event: Any):
                nonlocal popup_index
                action = _mouse_action(mouse_event)
                if action == "activate":
                    popup_index = item_index
                    popup_commit()
                    return None
                if action == "back":
                    popup_cancel()
                    return None
                if action == "up": popup_move(-1); return None
                if action == "down": popup_move(1); return None
                return NotImplemented
            return handler

        for item_index, label in enumerate(popup_labels):
            if item_index == popup_index:
                fragments.append(("[SetCursorPosition]", ""))
            style = "class:popup.selected" if item_index == popup_index else "class:popup.text"
            marker = "  ▶ " if item_index == popup_index else "    "
            fragments.append((style, marker + label + "\n", handler_for(item_index)))
        if popup_descriptions:
            fragments.append(("class:popup.rule", "\n  " + APP_THEME.section_fill * max(12, APP_THEME.popup_width() - 7) + "\n"))
            for line in _wrap(popup_descriptions[popup_index], max(18, APP_THEME.popup_width() - 6)):
                fragments.append(("class:popup.footer", "  " + line + "\n"))
        return fragments

    popup_control = control_cls(text=render_popup, focusable=True, show_cursor=False)
    popup_window = window_cls(content=popup_control, wrap_lines=True, always_hide_cursor=True)
    popup_frame = frame_cls(popup_window, title="WYBÓR", style="class:popup")
    popup_condition = condition_cls(lambda: popup_visible)
    completion_menu = completions_menu_cls(
        max_height=12, scroll_offset=1, display_arrows=True,
        extra_filter=condition_cls(lambda: not popup_visible),
    )

    header_control = control_cls(text=lambda: [
        ("class:header.title", f"  Jaźń / Łatka — generator paczek v{GENERATOR_VERSION}"),
        ("class:header.subtitle", "  •  " + ("tryb jednego okna" if compact_mode() else "menu + panel akcji")),
    ])
    footer_control = control_cls(text=lambda: [
        ("class:footer.key", " ↑/↓ Home/End PgUp/PgDn "), ("class:footer.text", "nawigacja/scroll; End śledzi log  "),
        ("class:footer.key", " Enter/LPM "), ("class:footer.text", "otwórz/wykonaj  "),
        ("class:footer.key", " Tab/←/→ "), ("class:footer.text", "zmień okno  "),
        ("class:footer.key", " Esc/PPM "), ("class:footer.text", "wróć  "),
        ("class:footer.key", " Ctrl+X "), ("class:footer.text", "wyjście "),
    ])
    base = hsplit_cls([
        window_cls(height=2, content=header_control, style="class:header", wrap_lines=False),
        window_cls(height=1, char=APP_THEME.section_fill, style="class:border"),
        body_dynamic,
        window_cls(height=1, char=APP_THEME.section_fill, style="class:border"),
        window_cls(height=1, content=footer_control, style="class:footer", wrap_lines=False),
    ])
    root = float_container_cls(
        content=base,
        floats=[
            float_cls(
                content=conditional_cls(popup_frame, filter=popup_condition),
                width=APP_THEME.popup_width,
                height=APP_THEME.popup_height,
                z_index=20,
            ),
            float_cls(xcursor=True, ycursor=True, content=completion_menu, z_index=100),
        ],
    )

    right_focus_targets = [
        readonly_window, action_window, choice_window, options_window,
        exclusions_window, right_editor.window, popup_window,
    ]

    def handle_navigation(action: str) -> None:
        nonlocal choice_index, options_index, exclusions_index, exclusion_item_index, popup_index
        if popup_visible:
            if action == "home": popup_index = 0
            elif action == "end": popup_index = max(0, len(popup_values) - 1)
            elif action in {"up", "pageup"}: popup_move(-1 if action == "up" else -4)
            elif action in {"down", "pagedown"}: popup_move(1 if action == "down" else 4)
            invalidate(); return
        if focus_zone == "menu":
            menu_move(action); return
        if panel_mode in {"overview", "log", "result", "error", "action"}:
            scroll_right_content(action); return
        if panel_mode in {"choice", "exit_choice"}:
            if action == "home": choice_index = 0
            elif action == "end": choice_index = max(0, len(choice_values) - 1)
            elif action in {"up", "pageup"}: choice_move(-1)
            elif action in {"down", "pagedown"}: choice_move(1)
            invalidate(); return
        if panel_mode == "options":
            if action == "home": options_index = 0
            elif action == "end": options_index = len(OPTIONS_LABELS) - 1
            elif action in {"up", "pageup"}: options_move(-1 if action == "up" else -APP_THEME.page_step)
            elif action in {"down", "pagedown"}: options_move(1 if action == "down" else APP_THEME.page_step)
            invalidate(); return
        if panel_mode in {"exclusions", "exclusion_list"}:
            if action == "home":
                if panel_mode == "exclusions": exclusions_index = 0
                else: exclusion_item_index = 0
            elif action == "end":
                if panel_mode == "exclusions": exclusions_index = len(EXCLUSION_ACTIONS) - 1
                else: exclusion_item_index = max(0, len(current_exclusion_list()) - 1)
            elif action in {"up", "pageup"}: exclusions_move(-1)
            elif action in {"down", "pagedown"}: exclusions_move(1)
            invalidate(); return

    navigation_filter = condition_cls(lambda: popup_visible or not text_editor_active())
    plain_key_filter = condition_cls(lambda: not text_editor_active())
    editor_filter = condition_cls(text_editor_active)
    exclusion_list_filter = condition_cls(lambda: panel_mode == "exclusion_list" and focus_zone == "detail" and not popup_visible)

    for key, action in (
        ("up", "up"), ("k", "up"), ("down", "down"), ("j", "down"),
        ("home", "home"), ("end", "end"), ("pageup", "pageup"), ("pagedown", "pagedown"),
    ):
        bindings.add(key, filter=navigation_filter, eager=True)(lambda event, a=action: handle_navigation(a))

    @bindings.add("enter", eager=True)
    def on_enter(event: Any) -> None:
        if popup_visible:
            popup_commit(); return
        if event.app.layout.buffer_has_focus:
            buffer = event.app.layout.current_buffer
            if buffer is not None and buffer.complete_state is not None:
                completion = buffer.complete_state.current_completion
                if completion is not None:
                    buffer.apply_completion(completion)
                    return
        if any(event.app.layout.has_focus(editor.window) for editor in left_editors_by_kind.values()):
            finish_left_edit(commit=True); return
        if event.app.layout.has_focus(right_editor.window):
            commit_right_editor(); return
        if focus_zone == "menu":
            open_menu_action(index); return
        execute_right()

    @bindings.add("tab", filter=editor_filter, eager=True)
    def on_editor_tab(event: Any) -> None:
        buffer = event.app.layout.current_buffer
        if buffer is None or buffer.completer is None:
            return
        if buffer.complete_state is None:
            buffer.start_completion(select_first=True)
        else:
            buffer.complete_next()

    @bindings.add("s-tab", filter=editor_filter, eager=True)
    def on_editor_shift_tab(event: Any) -> None:
        buffer = event.app.layout.current_buffer
        if buffer is None or buffer.completer is None:
            return
        if buffer.complete_state is None:
            buffer.start_completion(select_last=True)
        else:
            buffer.complete_previous()

    @bindings.add("c-space", filter=editor_filter, eager=True)
    def on_editor_complete(event: Any) -> None:
        buffer = event.app.layout.current_buffer
        if buffer is not None and buffer.completer is not None:
            buffer.start_completion(select_first=False)

    def switch_window(to_detail: bool) -> None:
        nonlocal compact_page
        if to_detail:
            compact_page = "detail"
            focus_right()
        else:
            compact_page = "menu"
            focus_left()

    @bindings.add("tab", filter=plain_key_filter, eager=True)
    @bindings.add("right", filter=plain_key_filter, eager=True)
    def on_to_right(event: Any) -> None:
        if popup_visible:
            popup_move(1)
        else:
            switch_window(True)

    @bindings.add("s-tab", filter=plain_key_filter, eager=True)
    @bindings.add("left", filter=plain_key_filter, eager=True)
    def on_to_left(event: Any) -> None:
        if popup_visible:
            popup_move(-1)
        else:
            switch_window(False)

    @bindings.add("escape", eager=True)
    @bindings.add("q", filter=plain_key_filter, eager=True)
    def on_escape(event: Any) -> None:
        nonlocal panel_mode, right_editor_kind, exclusion_delete_mode
        if popup_visible:
            popup_cancel(); return
        if any(event.app.layout.has_focus(editor.window) for editor in left_editors_by_kind.values()):
            finish_left_edit(commit=False); return
        if event.app.layout.has_focus(right_editor.window):
            if panel_mode == "options_limit_edit": finish_limit_edit(commit=False)
            elif panel_mode == "exclusion_edit": finish_exclusion_edit(commit=False)
            else:
                right_editor_kind = ""
                sync_menu_preview(force_panel=True)
                focus_left()
            return
        if panel_mode == "exclusion_list" and focus_zone == "detail":
            panel_mode = "exclusions"
            exclusion_delete_mode = False
            focus_right(); return
        if focus_zone == "detail":
            sync_menu_preview(force_panel=True)
            focus_left(); return
        open_menu_action(12)

    @bindings.add("c-x", eager=True)
    def on_ctrl_x(event: Any) -> None:
        open_menu_action(12)

    @bindings.add("a", filter=exclusion_list_filter, eager=True)
    def on_exclusion_add(event: Any) -> None:
        start_exclusion_edit(add=True)

    @bindings.add("e", filter=exclusion_list_filter, eager=True)
    def on_exclusion_edit(event: Any) -> None:
        if current_exclusion_list():
            start_exclusion_edit(add=False)

    @bindings.add("d", filter=exclusion_list_filter, eager=True)
    @bindings.add("delete", filter=exclusion_list_filter, eager=True)
    def on_exclusion_delete(event: Any) -> None:
        delete_selected_exclusion()

    def install_editor_back(control: Any, cancel: Any) -> None:
        original = getattr(control, "mouse_handler", None)
        if original is None:
            return
        def handler(mouse_event: Any):
            if _mouse_action(mouse_event) == "back":
                cancel(); return None
            return original(mouse_event)
        control.mouse_handler = handler

    for editor in left_editors_by_kind.values():
        install_editor_back(editor.control, lambda: finish_left_edit(commit=False))
    install_editor_back(
        right_editor.control,
        lambda: finish_limit_edit(commit=False)
        if panel_mode == "options_limit_edit"
        else finish_exclusion_edit(commit=False)
        if panel_mode == "exclusion_edit"
        else (sync_menu_preview(force_panel=True), focus_left()),
    )

    def ensure_visible_focus(application: Any) -> None:
        nonlocal last_compact, compact_page
        drain_worker_events()
        if panel_mode in {"log", "result", "error"} and log_follow_tail:
            readonly_pane.vertical_scroll = 10**9
        compact = compact_mode()
        if last_compact is None:
            last_compact = compact
        elif last_compact != compact:
            last_compact = compact
            if compact:
                compact_page = "detail" if focus_zone == "detail" else "menu"
        target = current_left_target() if focus_zone == "menu" else current_right_target()
        try:
            application.layout.update_parents_relations()
            visible = application.layout.get_visible_focusable_windows()
            if target not in visible:
                application.layout.focus(target)
        except Exception:
            try:
                application.layout.focus(target)
            except Exception:
                pass
        publish_debug()

    layout = layout_cls(root, focused_element=menu_window)
    app_kwargs: dict[str, Any] = {
        "layout": layout,
        "key_bindings": bindings,
        "style": _cursor_style(),
        "full_screen": True,
        "erase_when_done": True,
        "mouse_support": True,
        "enable_page_navigation_bindings": False,
        "terminal_size_polling_interval": 0.20,
        "refresh_interval": 0.10,
        "before_render": ensure_visible_focus,
    }
    if _input is not None:
        app_kwargs["input"] = _input
    if _output is not None:
        app_kwargs["output"] = _output
    dashboard_app = application_cls(**app_kwargs)
    app_box["app"] = dashboard_app
    publish_debug()
    result = dashboard_app.run()
    return str(result or "exit")


def _text_options_menu(state: InteractiveState) -> None:
    while True:
        print("\nOPCJE")
        values = (
            state.archive_format,
            f"{state.part_size_mb} MiB",
            f"DEFLATE {state.compression_level}",
            "TAK" if state.force else "NIE",
            "TAK" if state.sidecars else "NIE",
            state.ui_mode,
            "TAK" if state.update_source_manifest else "NIE",
            "TAK" if state.compatibility_checks else "NIE",
        )
        for number, label in enumerate(OPTIONS_LABELS, start=1):
            print(f"  {number}. {label}: [{values[number - 1]}]")
        print(f"  {len(OPTIONS_LABELS) + 1}. Wróć")
        raw = input("Wybór: ").strip()
        if not raw:
            continue
        try:
            choice = int(raw) - 1
        except ValueError:
            continue
        if choice == len(OPTIONS_LABELS):
            return
        if choice == 0:
            raw_format = input(f"Format {list(FORMAT_CHOICES)} [{state.archive_format}]: ").strip()
            if raw_format in FORMAT_CHOICES:
                state.archive_format = raw_format
        elif choice == 1:
            state.part_size_mb = max(1, int(input(f"Limit [{state.part_size_mb}]: ").strip() or state.part_size_mb))
        elif choice == 2:
            state.compression_level = max(0, min(9, int(input(f"DEFLATE 0-9 [{state.compression_level}]: ").strip() or state.compression_level)))
        elif choice == 3:
            state.force = not state.force
        elif choice == 4:
            state.sidecars = not state.sidecars
        elif choice == 5:
            state.ui_mode = "tekstowy" if state.ui_mode == "kursorowy" else "kursorowy"
        elif choice == 6:
            state.update_source_manifest = not state.update_source_manifest
        elif choice == 7:
            state.compatibility_checks = not state.compatibility_checks
        else:
            continue
        state.dirty = True


def handle_text_menu_choice(state: InteractiveState, choice: int) -> str:
    if choice == 0:
        pack_from_interactive(state)
    elif choice == 1:
        show_plan_interactive(state)
    elif choice == 2:
        update_manifest_interactive(state)
    elif choice == 3:
        raw = input(f"System Jaźni [{state.source}]: ").strip() or str(state.source)
        root, _ = validate_jazn_root(raw)
        state.source = root; state.dirty = True
    elif choice == 4:
        state.out_dir = Path(input(f"Zapis archiwum [{state.out_dir}]: ").strip() or str(state.out_dir)).expanduser().resolve(); state.dirty = True
    elif choice == 5:
        values = list(PROFILE_CHOICES)
        raw = input(f"Profil {values} [{state.profile}]: ").strip()
        if raw in values:
            state.profile = raw; state.dirty = True
    elif choice == 6:
        verify_interactive(state)
    elif choice == 7:
        extract_interactive(state)
    elif choice == 8:
        state.archive_basename = sanitize_archive_stem(input(f"Nazwa [{state.archive_basename}]: ").strip() or state.archive_basename); state.dirty = True
    elif choice == 9:
        _text_options_menu(state)
    elif choice == 10:
        edit_exclusions_text(state)
    elif choice == 11:
        save_interactive_state(state)
    elif choice == 12:
        return "exit"
    return "continue"


def interactive(ui_override: str | None = None) -> int:
    state = load_interactive_state()
    if ui_override:
        state.ui_mode = _normalize_ui_mode(ui_override)
    elif not state.ui_auto_start:
        state.ui_mode = "kursorowy" if _dashboard_available() else "tekstowy"

    if state.ui_mode == "kursorowy":
        cursor_dashboard(state, 0)
        return 0

    while True:
        render_text_main_menu(state)
        raw = input("Wybór: ").strip()
        if not raw:
            continue
        try:
            choice = int(raw) - 1
        except ValueError:
            continue
        if not 0 <= choice < len(main_menu_rows(state)):
            continue
        try:
            if handle_text_menu_choice(state, choice) == "exit":
                if state.dirty:
                    answer = input("Zapisać ustawienia? [T/n]: ").strip().lower()
                    if answer not in {"n", "nie", "no"}:
                        save_interactive_state(state)
                return 0
        except (PackError, OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            ui_status(str(exc), "error")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Jaźń / Łatka — kanoniczny generator paczek ZIP v7.0.1")
    root.add_argument("--version", action="version", version=GENERATOR_VERSION)
    sub = root.add_subparsers(dest="command")

    pack = sub.add_parser("pack", help="Zbuduj i zweryfikuj paczkę")
    pack.add_argument("source", type=Path)
    pack.add_argument("--out", type=Path)
    pack.add_argument("--profile", choices=PROFILE_CHOICES, default=DEFAULT_PROFILE)
    pack.add_argument("--format", dest="archive_format", choices=FORMAT_CHOICES, default=DEFAULT_FORMAT)
    pack.add_argument("--name", default="jazn_latka")
    pack.add_argument("--part-size-mb", type=int, default=DEFAULT_PART_SIZE_MB)
    pack.add_argument("--compresslevel", type=int, default=DEFAULT_COMPRESSION_LEVEL)
    pack.add_argument("--force", action="store_true")
    pack.add_argument("--exclude", action="append", default=[])
    pack.add_argument("--default-exclude", action="append", default=[])
    pack.add_argument("--no-default-excludes", action="store_true")
    pack.add_argument("--no-sidecars", action="store_true")
    pack.add_argument("--no-update-manifest", action="store_true")
    pack.add_argument("--no-compatibility-checks", action="store_true")

    plan = sub.add_parser("plan", help="Pokaż kanoniczny plan bez pakowania")
    plan.add_argument("source", type=Path)
    plan.add_argument("--profile", choices=("system", "memory", "combined"), default="system")
    plan.add_argument("--exclude", action="append", default=[])
    plan.add_argument("--default-exclude", action="append", default=[])
    plan.add_argument("--no-default-excludes", action="store_true")
    plan.add_argument("--files", action="store_true")
    plan.add_argument("--json", type=Path)

    verify = sub.add_parser("verify", help="Zweryfikuj paczkę na podstawie *.package.json")
    verify.add_argument("sidecar", type=Path)

    extract = sub.add_parser("extract", help="Zweryfikuj i bezpiecznie rozpakuj paczkę")
    extract.add_argument("sidecar", type=Path)
    extract.add_argument("destination", type=Path)
    extract.add_argument("--force", action="store_true")
    extract.add_argument("--clean", action="store_true")

    manifest = sub.add_parser("manifest", help="Zaktualizuj PACKAGE_INTEGRITY_MANIFEST.json")
    manifest.add_argument("source", type=Path)
    manifest.add_argument("--exclude", action="append", default=[])

    sub.add_parser("self-test", help="Test wersji, ZIP independent/binary i zgodności archiwizatorów")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return interactive()
    if argv[0] == "--ui":
        if len(argv) != 2:
            raise PackError("Użycie: --ui tekstowy|kursorowy")
        return interactive(ui_override=argv[1])
    if argv[0] == "--reset-settings":
        reset_interactive_settings(); print(f"Usunięto ustawienia: {settings_path()}"); return 0
    if argv[0] not in {"pack", "plan", "verify", "extract", "manifest", "self-test", "-h", "--help", "--version"}:
        argv.insert(0, "pack")
    args = parser().parse_args(argv)

    if args.command == "pack":
        if args.part_size_mb <= 0 or not 0 <= args.compresslevel <= 9:
            raise PackError("Niepoprawny limit części albo poziom kompresji.")
        out_dir = args.out or (args.source.expanduser().resolve().parent / "packages")
        base = [] if args.no_default_excludes else list(DEFAULT_BASE_EXCLUDES)
        base.extend(args.default_exclude)
        options = PackOptions(
            source=args.source, out_dir=out_dir, profile=args.profile,
            archive_format=args.archive_format, archive_basename=args.name,
            part_size_mb=args.part_size_mb, compression_level=args.compresslevel,
            force=args.force, base_excludes=base, custom_excludes=list(args.exclude),
            manual_excludes_enabled=bool(args.exclude), sidecars=not args.no_sidecars,
            update_source_manifest=not args.no_update_manifest,
            compatibility_checks=not args.no_compatibility_checks,
        )
        print_results(run_pack(options)); return 0

    if args.command == "plan":
        base = [] if args.no_default_excludes else list(DEFAULT_BASE_EXCLUDES)
        base.extend(args.default_exclude)
        plan = build_plan(args.source, args.profile, args.exclude, base_excludes=base, manual_excludes_enabled=bool(args.exclude))
        print_plan(plan, show_files=args.files)
        if args.json:
            args.json.write_bytes(serialize_json({
                "generator_version": GENERATOR_VERSION, "profile": plan.profile,
                "version": plan.version.full_version, "filename_version": plan.version.filename_version,
                "scan_method": plan.scan_method, "manifest_builder": plan.manifest_builder,
                "plan_sha256": plan.plan_sha256(), "file_count": plan.file_count,
                "total_size_bytes": plan.total_size,
                "entries": [{"path": item.relative, "size_bytes": item.size_bytes, "sha256": item.sha256, "classification": item.classification} for item in plan.entries],
                "excluded": [{"path": path, "reason": reason} for path, reason in plan.excluded],
            }))
        return 0

    if args.command == "verify":
        print(json.dumps(verify_package_sidecar(args.sidecar), ensure_ascii=False, indent=2)); return 0
    if args.command == "extract":
        print(json.dumps(extract_package_sidecar(args.sidecar, args.destination, clean=args.clean, force=args.force), ensure_ascii=False, indent=2)); return 0
    if args.command == "manifest":
        plan = build_plan(args.source, "system", args.exclude, base_excludes=DEFAULT_BASE_EXCLUDES, manual_excludes_enabled=bool(args.exclude))
        path = write_source_manifest_from_plan(plan)
        print(json.dumps({"ok": bool(path), "path": str(path) if path else None, "sha256": sha256_file(path) if path else None, "version": plan.version.full_version}, ensure_ascii=False, indent=2)); return 0
    if args.command == "self-test":
        report = run_self_test(); print(json.dumps(report, ensure_ascii=False, indent=2)); return 0 if report.get("ok") else 2
    parser().print_help(); return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(_paint("\nPrzerwano przez użytkownika.", ANSI_YELLOW, stream=sys.stderr), file=sys.stderr)
        raise SystemExit(130)
    except PackError as exc:
        print(_paint(f"BŁĄD: {exc}", ANSI_RED, ANSI_BOLD, stream=sys.stderr), file=sys.stderr)
        raise SystemExit(2)
