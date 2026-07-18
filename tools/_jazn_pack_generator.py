#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jaźń / Łatka — generator paczek v24.0.0

Najważniejsza zasada: podgląd, manifest i ZIP korzystają z jednego,
zamrożonego planu plików. Edytowalne reguły podstawowe domyślnie pomijają
archiwa, cache, runtime i bazy systemowe; sekrety oraz WAL/SHM pozostają
zawsze chronione. W repozytorium plan respektuje pliki ignorowane przez Git.

Interfejs:
  kursorowy — edycja wierszy, kompaktowe modalne wybory i błędy,
               klikanie LPM, przewijanie i PPM=wstecz, strzałki, Enter, Esc i Ctrl+X
  tekstowy  — pełne menu numerowane, działające bez bibliotek dodatkowych
  ustawienia obu trybów są zapisywane obok skryptu i migrowane ze starszego formatu
  wykluczenia — osobna lista podstawowa i ręczna z przełącznikiem używania

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
  py _jazn_pack_generator.py self-test
"""

from __future__ import annotations

import argparse
import ast
import bisect
import datetime as dt
import fnmatch
import hashlib
import inspect
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Iterator, Sequence

# prompt_toolkit jest opcjonalny. Tryb tekstowy zawsze działa bez zależności.
try:  # pragma: no cover - zależne od terminala użytkownika
    from prompt_toolkit import prompt as _pt_prompt
    from prompt_toolkit.application import Application as _pt_Application
    from prompt_toolkit.application.current import get_app as _pt_get_app
    from prompt_toolkit.completion import PathCompleter as _pt_PathCompleter
    from prompt_toolkit.key_binding import KeyBindings as _pt_KeyBindings
    from prompt_toolkit.filters import Condition as _pt_Condition
    from prompt_toolkit.layout import Dimension as _pt_Dimension
    from prompt_toolkit.layout import Layout as _pt_Layout
    from prompt_toolkit.layout.containers import HSplit as _pt_HSplit
    from prompt_toolkit.layout.containers import Float as _pt_Float
    from prompt_toolkit.layout.containers import FloatContainer as _pt_FloatContainer
    from prompt_toolkit.layout.containers import ConditionalContainer as _pt_ConditionalContainer
    from prompt_toolkit.layout.containers import DynamicContainer as _pt_DynamicContainer
    from prompt_toolkit.layout.scrollable_pane import ScrollablePane as _pt_ScrollablePane
    from prompt_toolkit.layout.containers import VSplit as _pt_VSplit
    from prompt_toolkit.layout.containers import Window as _pt_Window
    from prompt_toolkit.layout.controls import FormattedTextControl as _pt_FormattedTextControl
    from prompt_toolkit.styles import Style as _pt_Style
    from prompt_toolkit.widgets import TextArea as _pt_TextArea
    from prompt_toolkit.shortcuts import message_dialog as _pt_message_dialog
    from prompt_toolkit.mouse_events import MouseButton as _pt_MouseButton
    from prompt_toolkit.mouse_events import MouseEventType as _pt_MouseEventType
    HAS_PROMPT_TOOLKIT = True
except Exception:  # pragma: no cover
    _pt_prompt = None
    _pt_Application = None
    _pt_get_app = None
    _pt_PathCompleter = None
    _pt_KeyBindings = None
    _pt_Condition = None
    _pt_Dimension = None
    _pt_Layout = None
    _pt_HSplit = None
    _pt_Float = None
    _pt_FloatContainer = None
    _pt_ConditionalContainer = None
    _pt_DynamicContainer = None
    _pt_ScrollablePane = None
    _pt_VSplit = None
    _pt_Window = None
    _pt_FormattedTextControl = None
    _pt_Style = None
    _pt_TextArea = None
    _pt_message_dialog = None
    _pt_MouseButton = None
    _pt_MouseEventType = None
    HAS_PROMPT_TOOLKIT = False

GENERATOR_VERSION = "24.0.0"
CHUNK_SIZE = 1024 * 1024
DEFAULT_PART_SIZE_MB = 400
DEFAULT_COMPRESSION_LEVEL = 6
DEFAULT_PROFILE = "dual"
DEFAULT_FORMAT = "auto"

SETTINGS_FILE_NAME = "__jazn_pack_generator_settings.json"
SETTINGS_SCHEMA = "jazn_pack_generator_settings/v5"
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

PROFILE_CHOICES = ("system", "memory", "combined", "dual")
FORMAT_CHOICES = ("auto", "independent", "binary")
COMPRESSION_CHOICES = tuple(range(10))
COMPRESSION_UI_LABELS = (
    "0 — bez kompresji",
    "1 — najszybsza kompresja",
    "2 — bardzo szybka",
    "3 — szybka",
    "4 — umiarkowanie szybka",
    "5 — umiarkowana",
    "6 — zrównoważona (zalecana)",
    "7 — wysoka",
    "8 — bardzo wysoka",
    "9 — maksymalna kompresja",
)
COMPRESSION_UI_DETAILS = (
    "ZIP_DEFLATED zapisuje dane bez kompresowania. Najszybsze, ale zwykle tworzy największy plik.",
    "Najniższy poziom kompresji z priorytetem szybkości.",
    "Bardzo szybkie pakowanie przy niewielkiej redukcji rozmiaru.",
    "Szybkie pakowanie z umiarkowaną redukcją rozmiaru.",
    "Poziom pośredni z przewagą szybkości.",
    "Poziom pośredni między szybkością i rozmiarem.",
    "Domyślny kompromis między czasem pakowania i rozmiarem paczki.",
    "Wyższa kompresja kosztem dłuższego pakowania.",
    "Bardzo wysoka kompresja, zwykle wolniejsza od poziomu 7.",
    "Najwyższy poziom DEFLATE. Może działać najdłużej.",
)

MENU_PACK = 0
MENU_PLAN = 1
MENU_NAME = 2
MENU_PROFILE = 3
MENU_SOURCE = 4
MENU_OUTPUT = 5
MENU_SIDECARS = 6
MENU_FORMAT = 7
MENU_LIMIT = 8
MENU_COMPRESSION = 9
MENU_FORCE = 10
MENU_INTERFACE = 11
MENU_EXCLUDES = 12
MENU_SAVE = 13
MENU_VERIFY = 14
MENU_EXTRACT = 15
MENU_SELF_TEST = 16
MENU_EXIT = 17

MAIN_MENU_GROUPS = {
    MENU_PACK: "GŁÓWNE",
    MENU_NAME: "KONFIGURACJA",
    MENU_FORMAT: "USTAWIENIA",
    MENU_VERIFY: "NARZĘDZIA",
    MENU_EXIT: "WYJŚCIE",
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


@dataclass(slots=True)
class ExclusionRule:
    """Edytowalna reguła podstawowa używana przed budowaniem planu."""

    scope: str
    pattern: str
    enabled: bool = True

    def normalized(self) -> "ExclusionRule":
        scope = str(self.scope or "common").strip().lower()
        if scope not in {"common", "system", "memory"}:
            scope = "common"
        pattern = str(self.pattern or "").strip().replace("\\", "/")
        return ExclusionRule(scope=scope, pattern=pattern, enabled=bool(self.enabled))


def default_exclusion_rules() -> list[ExclusionRule]:
    rules: list[ExclusionRule] = []
    for name in sorted(COMMON_FORBIDDEN_DIR_NAMES):
        rules.append(ExclusionRule("common", f"**/{name}/"))
    for suffix in COMMON_FORBIDDEN_SUFFIXES:
        rules.append(ExclusionRule("common", f"*{suffix}"))
    rules.extend([
        ExclusionRule("common", "*.zip.*"),
        ExclusionRule("common", "*.before.py"),
    ])
    for root_name in sorted(SYSTEM_FORBIDDEN_ROOTS):
        rules.append(ExclusionRule("system", f"{root_name}/"))
    for name in sorted(SYSTEM_FORBIDDEN_FILE_NAMES):
        rules.append(ExclusionRule("system", name))
    for suffix in SYSTEM_DATABASE_SUFFIXES:
        rules.append(ExclusionRule("system", f"*{suffix}"))
    return rules


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
    base_excludes: list[ExclusionRule] = field(default_factory=default_exclusion_rules)
    custom_excludes: list[str] = field(default_factory=list)
    custom_excludes_enabled: bool = False
    sidecars: bool = True


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
    base_excludes: list[ExclusionRule] = field(default_factory=default_exclusion_rules)
    custom_excludes: list[str] = field(default_factory=list)
    custom_excludes_enabled: bool = False
    sidecars: bool = True
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
            base_excludes=[ExclusionRule(item.scope, item.pattern, item.enabled) for item in self.base_excludes],
            custom_excludes=list(self.custom_excludes),
            custom_excludes_enabled=bool(self.custom_excludes and self.custom_excludes_enabled),
            sidecars=self.sidecars,
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


def print_progress(done: int, total: int, label: str) -> None:
    total = max(total, 1)
    done = max(0, min(done, total))
    width = 28
    filled = int(width * done / total)
    bar = "█" * filled + "░" * (width - filled)
    percent = int(done * 100 / total)
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
    release = values.get("PACKAGE_RELEASE_NAME", "").strip().strip("-_. ")
    full = package_version
    if release and not package_version.lower().endswith(f"-{release}".lower()):
        full = f"{package_version}-{release}"
    filename_version = re.sub(r"^v", "", full, flags=re.IGNORECASE)
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
    release = values.get("PACKAGE_RELEASE_NAME", "").strip().strip("-_. ")
    if release and not base.lower().endswith(f"-{release}".lower()):
        return f"{base}-{release}"
    return base


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


def matches_exclude_pattern(relative: str, patterns: Iterable[str]) -> str | None:
    name = PurePosixPath(relative).name
    relative_cmp = relative.lower()
    name_cmp = name.lower()
    parts = tuple(part.lower() for part in PurePosixPath(relative).parts)
    for raw in patterns:
        pattern = str(raw).strip().replace("\\", "/")
        if not pattern:
            continue
        pattern = pattern.lstrip("/")
        pattern_cmp = pattern.lower()
        if pattern_cmp.startswith("**/") and pattern_cmp.endswith("/"):
            folder = pattern_cmp[3:-1]
            if folder and folder in parts[:-1]:
                return raw
        if fnmatch.fnmatch(relative_cmp, pattern_cmp) or fnmatch.fnmatch(name_cmp, pattern_cmp):
            return raw
        if pattern_cmp.endswith("/"):
            folder = pattern_cmp.rstrip("/")
            if relative_cmp == folder or relative_cmp.startswith(folder + "/"):
                return raw
    return None


def common_forbidden_reason(relative: str) -> str | None:
    """Nienaruszalna granica bezpieczeństwa, niezależna od ustawień UI."""

    relative = normalize_rel(relative)
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    name = parts[-1]

    if name in SECRET_EXACT_NAMES and name != ".env.example":
        return "secret_file"
    if any(token in name for token in SECRET_NAME_TOKENS):
        return "secret_name"
    if name.endswith(TRANSIENT_DATABASE_SUFFIXES):
        return "transient_database_file"
    return None


def profile_forbidden_reason(relative: str, profile: str) -> str | None:
    relative = normalize_rel(relative)
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    root_name = parts[0]

    if profile == "memory":
        if root_name != "memory":
            return "outside_memory_profile"
        if relative == MEMORY_PACKAGE_MANIFEST:
            return "virtual_manifest_replaces_source"
        return common_forbidden_reason(relative)

    return common_forbidden_reason(relative)


def matching_base_exclude(relative: str, profile: str, rules: Iterable[ExclusionRule]) -> ExclusionRule | None:
    for raw_rule in rules:
        rule = raw_rule.normalized()
        if not rule.enabled or not rule.pattern:
            continue
        if rule.scope not in {"common", profile}:
            continue
        if matches_exclude_pattern(relative, [rule.pattern]) is not None:
            return rule
    return None


def filter_candidates(
    candidates: Iterable[str],
    *,
    profile: str,
    base_excludes: Iterable[ExclusionRule],
    custom_excludes: Iterable[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    selected: list[str] = []
    excluded: list[tuple[str, str]] = []
    for relative in candidates:
        base = matching_base_exclude(relative, profile, base_excludes)
        if base is not None:
            excluded.append((relative, f"base:{base.scope}:{base.pattern}"))
            continue
        custom = matches_exclude_pattern(relative, custom_excludes)
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
    base_excludes: Sequence[ExclusionRule] | None = None,
) -> PackPlan:
    root = root.expanduser().resolve()
    if profile not in {"system", "memory", "combined"}:
        raise PackError(f"Niepoprawny profil planu: {profile}")
    version = read_version_info(root)
    active_base_excludes = list(base_excludes) if base_excludes is not None else default_exclusion_rules()

    if profile == "system":
        system_candidates, system_scan_method = discover_candidates(root)
        selected, excluded = filter_candidates(
            system_candidates,
            profile="system",
            base_excludes=active_base_excludes,
            custom_excludes=custom_excludes,
        )
        return build_system_plan(root, version, selected, excluded, system_scan_method)

    if profile == "memory":
        memory_candidates, memory_scan_method = discover_memory_candidates(root)
        selected, excluded = filter_candidates(
            memory_candidates,
            profile="memory",
            base_excludes=active_base_excludes,
            custom_excludes=custom_excludes,
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
        base_excludes=active_base_excludes,
        custom_excludes=custom_excludes,
    )
    memory_selected, memory_excluded = filter_candidates(
        memory_candidates,
        profile="memory",
        base_excludes=active_base_excludes,
        custom_excludes=custom_excludes,
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


def existing_package_error(base_zip_name: str, existing: Sequence[Path]) -> PackError:
    """Buduje jeden spójny komunikat kolizji dla CLI i interfejsu."""

    return PackError(
        "Istnieją wcześniejsze pliki tej paczki. Włącz nadpisywanie (--force) "
        "albo zmień nazwę:\n"
        + "\n".join(f"  - {path}" for path in existing)
    )


def preflight_output_collisions(
    out_dir: Path,
    base_zip_names: Iterable[str],
    *,
    force: bool,
) -> None:
    """Odrzuca kolizje przed kompresją i przed częściowym wynikiem profilu dual.

    Kontrola w commit_transaction pozostaje celowo jako druga bariera przed
    wyścigiem między preflightem i atomową publikacją plików.
    """

    if force:
        return
    collisions: list[tuple[str, list[Path]]] = []
    for base_zip_name in base_zip_names:
        existing = known_output_paths(out_dir, base_zip_name)
        if existing:
            collisions.append((base_zip_name, existing))
    if not collisions:
        return
    if len(collisions) == 1:
        name, existing = collisions[0]
        raise existing_package_error(name, existing)
    lines = [
        "Istnieją wcześniejsze pliki co najmniej jednej planowanej paczki. "
        "Włącz nadpisywanie (--force) albo zmień nazwę:"
    ]
    for name, existing in collisions:
        lines.append(f"\n{name}")
        lines.extend(f"  - {path}" for path in existing)
    raise PackError("\n".join(lines))


def commit_transaction(temp_dir: Path, out_dir: Path, filenames: Sequence[str], base_zip_name: str, force: bool) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = known_output_paths(out_dir, base_zip_name)
    if existing and not force:
        raise existing_package_error(base_zip_name, existing)

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


def package_one(plan: PackPlan, options: PackOptions, base_zip_name: str) -> PackageResult:
    part_size = options.part_size_mb * 1024 * 1024
    archive_format = choose_format(options.archive_format, plan, part_size)
    options.out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = create_pack_staging_dir(options.out_dir, base_zip_name)
    try:
        print(f"\nPaczka: {base_zip_name}")
        print(f"Profil: {plan.profile}")
        print(f"Format: {archive_format}")
        print(f"Plan: {plan.file_count} plików, {human_size(plan.total_size)}")
        print(f"Plan SHA-256: {plan.plan_sha256()}")

        if archive_format == "independent":
            outputs, logical_hash = write_independent(
                temp_dir, base_zip_name, plan, part_size, options.compression_level
            )
        else:
            outputs, logical_hash = write_binary(
                temp_dir, base_zip_name, plan, part_size, options.compression_level
            )

        verification = verify_outputs(temp_dir, outputs, archive_format, plan)
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


def build_plans_for_options(
    options: PackOptions,
    *,
    notice: Any | None = None,
) -> list[PackPlan]:
    """Buduje plan tylko raz. Wynik może zostać pokazany i bezpośrednio spakowany."""

    source = options.source.expanduser().resolve()
    out_dir = options.out_dir.expanduser().resolve()
    ensure_output_outside_source(source, out_dir)
    manual_excludes = options.custom_excludes if options.custom_excludes_enabled else []
    if options.profile == "dual":
        plans = [build_plan(source, "system", manual_excludes, options.base_excludes)]
        try:
            plans.append(build_plan(source, "memory", manual_excludes, options.base_excludes))
        except PackError as exc:
            message = f"Pomijam paczkę pamięci: {exc}"
            if notice is None:
                print(f"UWAGA: {message}")
            else:
                notice(message, "warn")
        return plans
    return [build_plan(source, options.profile, manual_excludes, options.base_excludes)]


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

    planned_names: list[str] = []
    for plan in plans:
        name = names.get(plan.profile)
        if not name:
            raise PackError(f"Brak nazwy wynikowej dla profilu planu: {plan.profile}")
        planned_names.append(name)
    preflight_output_collisions(out_dir, planned_names, force=options.force)

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
        name = names[plan.profile]
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


def _assert_outputs_are_deflated(result: PackageResult) -> None:
    """Potwierdza, że wpisy testowej paczki faktycznie używają ZIP_DEFLATED."""

    if result.archive_format == "independent":
        for output in result.outputs:
            with zipfile.ZipFile(result.sidecar_path.parent / output.filename, "r") as zf:
                for info in zf.infolist():
                    if not info.is_dir() and info.compress_type != zipfile.ZIP_DEFLATED:
                        raise PackError(f"Self-test: wpis nie używa DEFLATE: {info.filename}")
        return
    paths = [result.sidecar_path.parent / item.filename for item in result.outputs]
    with SplitPartsReader(paths) as reader:
        with zipfile.ZipFile(reader, "r") as zf:
            for info in zf.infolist():
                if not info.is_dir() and info.compress_type != zipfile.ZIP_DEFLATED:
                    raise PackError(f"Self-test: wpis nie używa DEFLATE: {info.filename}")


def run_compression_self_test() -> dict[str, Any]:
    """Pełny test regresji kompresji: independent i binary, verify oraz extract."""

    with tempfile.TemporaryDirectory(prefix="jazn-pack-self-test-") as temp_raw:
        temp = Path(temp_raw)
        source = temp / "source"
        source.mkdir()
        (source / "latka_jazn").mkdir()
        (source / "latka_jazn" / "__init__.py").write_text("", encoding="utf-8")
        (source / "latka_jazn" / "version.py").write_text(
            'PACKAGE_VERSION = "v0.0.0"\nPACKAGE_RELEASE_NAME = "COMPRESSION-SELF-TEST"\n',
            encoding="utf-8",
        )
        (source / "run.py").write_text('print("self-test")\n', encoding="utf-8")
        (source / "main.py").write_text('print("self-test")\n', encoding="utf-8")
        (source / "SOURCE_PROVENANCE.json").write_text('{"status":"self-test"}\n', encoding="utf-8")
        (source / "README.md").write_text("DEFLATE regression test\n" * 256, encoding="utf-8")
        (source / "memory").mkdir()
        binary_payload = os.urandom(2_600_000)
        (source / "memory" / "large.bin").write_bytes(binary_payload)
        (source / "memory" / "small.json").write_text('{"self_test":true}\n', encoding="utf-8")

        base_rules = default_exclusion_rules()
        independent_options = PackOptions(
            source=source,
            out_dir=temp / "independent",
            profile="system",
            archive_format="independent",
            archive_basename="self_test_independent",
            part_size_mb=1,
            compression_level=6,
            base_excludes=base_rules,
            sidecars=True,
        )
        independent_results = run_pack(independent_options)
        collision_guard_ok = False
        try:
            run_pack(independent_options)
        except PackError as exc:
            collision_guard_ok = "Istnieją wcześniejsze pliki tej paczki" in str(exc)
        if not collision_guard_ok:
            raise PackError("Self-test: ochrona istniejącej paczki nie zgłosiła oczekiwanego błędu.")
        independent_options.force = True
        forced_replace_results = run_pack(independent_options)
        forced_replace_verify = verify_package_sidecar(forced_replace_results[0].sidecar_path)

        binary_results = run_pack(PackOptions(
            source=source,
            out_dir=temp / "binary",
            profile="memory",
            archive_format="binary",
            archive_basename="self_test_binary",
            part_size_mb=1,
            compression_level=6,
            base_excludes=base_rules,
            sidecars=True,
        ))
        independent = independent_results[0]
        binary = binary_results[0]
        _assert_outputs_are_deflated(independent)
        _assert_outputs_are_deflated(binary)
        independent_verify = verify_package_sidecar(independent.sidecar_path)
        binary_verify = verify_package_sidecar(binary.sidecar_path)
        independent_extract = temp / "extract-independent"
        binary_extract = temp / "extract-binary"
        extract_package_sidecar(independent.sidecar_path, independent_extract, clean=True, force=True)
        extract_package_sidecar(binary.sidecar_path, binary_extract, clean=True, force=True)
        if (independent_extract / "run.py").read_text(encoding="utf-8") != 'print("self-test")\n':
            raise PackError("Self-test: zawartość independent zmieniła się po ekstrakcji.")
        if (binary_extract / "memory" / "large.bin").read_bytes() != binary_payload:
            raise PackError("Self-test: zawartość binary zmieniła się po ekstrakcji.")
        return {
            "ok": True,
            "generator_version": GENERATOR_VERSION,
            "compression": "ZIP_DEFLATED",
            "compression_level": 6,
            "independent": {
                "volumes": len(independent.outputs),
                "entries": independent.plan.file_count,
                "verified": bool(independent_verify.get("ok")),
                "collision_guard": collision_guard_ok,
                "force_replace_verified": bool(forced_replace_verify.get("ok")),
            },
            "binary": {
                "volumes": len(binary.outputs),
                "entries": binary.plan.file_count,
                "verified": bool(binary_verify.get("ok")),
                "logical_zip_sha256": binary.logical_zip_sha256,
            },
        }


# -----------------------------------------------------------------------------
# Plan i interfejs — warstwa 2.2 POLISHED
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
    """Włącza ANSI w nowoczesnym Windows Terminal/PowerShell, gdy to możliwe."""
    if os.name != "nt":
        return
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


def _ui_width(fallback: int = 88) -> int:
    try:
        columns = shutil.get_terminal_size((fallback, 24)).columns
    except Exception:
        columns = fallback
    return max(72, min(columns, 116))


def _detail_panel_columns(*, ratio: float = 0.28, minimum: int = 28, maximum: int = 64) -> int:
    """Zwraca stabilną szerokość prawego panelu jako część szerokości terminala."""

    try:
        columns = shutil.get_terminal_size((100, 30)).columns
    except Exception:
        columns = 100
    preferred = int(round(max(1, columns) * ratio))
    return max(minimum, min(preferred, maximum, max(minimum, columns - 42)))


def _ellipsize(value: object, width: int) -> str:
    raw = str(value)
    if width <= 1:
        return raw[:width]
    return raw if len(raw) <= width else raw[: max(1, width - 1)] + "…"


def _wrap(value: object, width: int, *, indent: str = "") -> list[str]:
    raw = str(value)
    return textwrap.wrap(
        raw,
        width=max(10, width),
        subsequent_indent=indent,
        replace_whitespace=False,
        drop_whitespace=True,
    ) or [""]


def ui_rule(char: str = "─", *, width: int | None = None) -> str:
    return _paint(char * int(width or _ui_width()), ANSI_CYAN)


def ui_banner(title: str, subtitle: str = "") -> None:
    width = _ui_width()
    print()
    print(_paint("╭" + "─" * (width - 2) + "╮", ANSI_CYAN))
    title_line = f"  {title}"
    print(_paint("│", ANSI_CYAN) + _paint(_ellipsize(title_line, width - 2).ljust(width - 2), ANSI_BOLD, ANSI_BRIGHT_CYAN) + _paint("│", ANSI_CYAN))
    if subtitle:
        print(_paint("│", ANSI_CYAN) + _paint(_ellipsize("  " + subtitle, width - 2).ljust(width - 2), ANSI_DIM) + _paint("│", ANSI_CYAN))
    print(_paint("╰" + "─" * (width - 2) + "╯", ANSI_CYAN))


def ui_section(title: str) -> None:
    width = _ui_width()
    label = f" {title.strip()} "
    rest = max(0, width - len(label))
    print("\n" + _paint(label + "─" * rest, ANSI_CYAN, ANSI_BOLD))


def ui_status(message: str, kind: str = "info") -> None:
    palette = {
        "ok": ("✓", ANSI_BRIGHT_GREEN),
        "warn": ("!", ANSI_YELLOW),
        "error": ("×", ANSI_RED),
        "info": ("•", ANSI_BRIGHT_CYAN),
    }
    marker, color = palette.get(kind, palette["info"])
    print(_paint(f"{marker} {message}", color, ANSI_BOLD if kind in {"ok", "error"} else ""))


def cursor_message_popup(message: object, kind: str = "info", *, title: str | None = None) -> None:
    """Modalne okno komunikatu używane przez interfejs kursorowy.

    Błędy i ostrzeżenia nie wypadają już do zwykłego logu terminala.
    Użytkownik zamyka okno Enterem albo Esc, po czym wraca do menu.
    """

    if not HAS_PROMPT_TOOLKIT or _pt_message_dialog is None:
        ui_status(str(message), kind)
        return
    titles = {
        "ok": "Zakończono poprawnie",
        "warn": "Ostrzeżenie",
        "error": "Błąd",
        "info": "Informacja",
    }
    style = _pt_Style.from_dict({
        "dialog": "bg:ansiblack",
        "dialog frame.label": "ansibrightcyan bold",
        "dialog.body": "bg:ansiblack fg:ansiwhite",
        "dialog shadow": "bg:ansiblack",
        "button": "bg:ansicyan fg:ansiblack",
        "button.focused": "bg:ansibrightcyan fg:ansiblack bold",
    }) if _pt_Style is not None else None
    dialog = _pt_message_dialog(
        title=title or titles.get(kind, titles["info"]),
        text=str(message),
        ok_text="OK",
        style=style,
    )
    dialog.run()


def ui_message(ui_mode: str, message: object, kind: str = "info", *, title: str | None = None) -> None:
    """Pokazuje komunikat zgodnie z aktywnym trybem interfejsu."""

    if ui_mode == "kursorowy":
        cursor_message_popup(message, kind, title=title)
    else:
        ui_status(str(message), kind)


def ui_key_value(label: str, value: object, *, label_width: int = 18, indent: str = "  ") -> None:
    width = _ui_width()
    prefix = indent + _paint(label.ljust(label_width), ANSI_BRIGHT_BLACK)
    available = max(12, width - len(indent) - label_width)
    lines = _wrap(value, available, indent=" " * (len(indent) + label_width))
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
            marker = "V" if item.is_virtual else "F"
            print(f"  {_paint(f'{index:>5}.', ANSI_BRIGHT_BLACK)} [{marker}] {item.relative} {_paint(f'({human_size(item.size_bytes)})', ANSI_BRIGHT_BLACK)}")
        ui_section("PRZYKŁADOWE WYKLUCZENIA")
        for path, reason in plan.excluded[:100]:
            print(f"  {_paint('–', ANSI_YELLOW)} {path}  {_paint(f'[{reason}]', ANSI_BRIGHT_BLACK)}")


def _suggested_source() -> Path:
    # Bez pliku konfiguracji pokazujemy neutralny, przewidywalny root systemu.
    # Użytkownik od razu edytuje go w pełnoekranowym wierszu interfejsu.
    return Path("C:\\") if os.name == "nt" else Path("/bin")


def _display_path(path: Path) -> str:
    value = str(path)
    if os.name != "nt" and value == "/bin":
        return "/bin/"
    return value


def settings_path() -> Path:
    return Path(__file__).resolve().with_name(SETTINGS_FILE_NAME)


def _normalize_ui_mode(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "plain": "tekstowy",
        "text": "tekstowy",
        "txt": "tekstowy",
        "tekst": "tekstowy",
        "tekstowy": "tekstowy",
        "cursor": "kursorowy",
        "curses": "kursorowy",
        "kursor": "kursorowy",
        "kursorowy": "kursorowy",
    }
    mode = aliases.get(raw, raw)
    if mode not in UI_MODE_CHOICES:
        return "tekstowy"
    if mode == "kursorowy" and not HAS_PROMPT_TOOLKIT:
        return "tekstowy"
    return mode


def default_interactive_state() -> InteractiveState:
    source = _suggested_source()
    return InteractiveState(source=source, out_dir=Path(__file__).resolve().parent / "packages")


def _load_exclusion_rules(value: object) -> list[ExclusionRule]:
    if not isinstance(value, list):
        return default_exclusion_rules()
    result: list[ExclusionRule] = []
    for item in value:
        if isinstance(item, dict):
            rule = ExclusionRule(
                scope=str(item.get("scope") or "common"),
                pattern=str(item.get("pattern") or ""),
                enabled=bool(item.get("enabled", True)),
            ).normalized()
        elif isinstance(item, str):
            rule = ExclusionRule("common", item, True).normalized()
        else:
            continue
        if rule.pattern:
            result.append(rule)
    return result


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
    else:
        state.out_dir = state.source.parent / "packages"

    profile = str(payload.get("profile") or payload.get("pack_profile") or state.profile)
    if profile == "pelna":
        profile = "dual"
    if profile in PROFILE_CHOICES:
        state.profile = profile
    archive_format = str(payload.get("archive_format") or payload.get("format") or state.archive_format)
    if archive_format in FORMAT_CHOICES:
        state.archive_format = archive_format
    state.archive_basename = str(
        payload.get("archive_basename")
        or payload.get("archive_basename_requested")
        or payload.get("name")
        or state.archive_basename
    ).strip() or "jazn_latka"
    try:
        state.part_size_mb = max(1, int(payload.get("part_size_mb", state.part_size_mb)))
    except (TypeError, ValueError):
        pass
    try:
        level = int(payload.get("compression_level", state.compression_level))
        if 0 <= level <= 9:
            state.compression_level = level
    except (TypeError, ValueError):
        pass
    state.force = bool(payload.get("force", state.force))
    state.base_excludes = _load_exclusion_rules(payload.get("base_excludes"))
    custom = payload.get("manual_excludes") or payload.get("custom_excludes") or payload.get("exclude") or []
    if isinstance(custom, list):
        state.custom_excludes = [str(item).strip() for item in custom if str(item).strip()]
    state.custom_excludes_enabled = bool(
        state.custom_excludes
        and payload.get("manual_excludes_enabled", payload.get("custom_excludes_enabled", True))
    )
    state.sidecars = bool(payload.get("sidecars", payload.get("diagnostic_files", True)))
    state.ui_mode = _normalize_ui_mode(str(payload.get("ui_mode") or "tekstowy"))
    state.ui_auto_start = bool(payload.get("ui_auto_start", False))
    state.dirty = False
    return state


def save_interactive_state(state: InteractiveState) -> Path:
    payload = {
        "schema_version": SETTINGS_SCHEMA,
        "saved_at_utc": utc_now(),
        "generator_version": GENERATOR_VERSION,
        "source": str(state.source.resolve()),
        "out_dir": str(state.out_dir.resolve()),
        "profile": state.profile,
        "archive_format": state.archive_format,
        "archive_basename": state.archive_basename,
        "part_size_mb": state.part_size_mb,
        "compression_level": state.compression_level,
        "force": state.force,
        "base_excludes": [
            {"scope": item.scope, "pattern": item.pattern, "enabled": item.enabled}
            for item in state.base_excludes
        ],
        "manual_excludes": list(state.custom_excludes),
        "manual_excludes_enabled": bool(state.custom_excludes and state.custom_excludes_enabled),
        "custom_excludes": list(state.custom_excludes),
        "sidecars": state.sidecars,
        "ui_mode": state.ui_mode,
        "ui_auto_start": state.ui_auto_start,
        "appearance": "latka-cyan-polished",
    }
    path = settings_path()
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temp.write_bytes(serialize_json(payload))
    os.replace(temp, path)
    state.dirty = False
    return path


def reset_interactive_settings() -> None:
    settings_path().unlink(missing_ok=True)


def _prompt_key_bindings():
    if not HAS_PROMPT_TOOLKIT or _pt_KeyBindings is None:
        return None
    bindings = _pt_KeyBindings()

    @bindings.add("escape", eager=True)
    def _cancel(event: Any) -> None:
        event.app.exit(result=UI_CANCEL_MARKER)

    @bindings.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=UI_EXIT_MARKER)

    return bindings


def _prompt_style():
    if not HAS_PROMPT_TOOLKIT or _pt_Style is None:
        return None
    return _pt_Style.from_dict({
        "prompt": "ansibrightcyan bold",
        "rprompt": "ansibrightblack",
        "completion-menu.completion": "bg:ansiblack fg:ansiwhite",
        "completion-menu.completion.current": "bg:ansicyan fg:ansiblack bold",
        "scrollbar.background": "bg:ansiblack",
        "scrollbar.button": "bg:ansicyan",
    })


def _control_result(value: str) -> str:
    raw = value.strip().lower()
    if raw in {"esc", "escape", "anuluj", "cancel", "wróć", "wroc"}:
        raise UserCancelledInput()
    if raw in {"ctrl+x", "ctrlx", "^x", "exit", "quit", "zamknij"}:
        raise UserRequestedExit()
    return value


def cursor_edit_value(
    title: str,
    label: str,
    current: str,
    *,
    path_mode: bool = False,
    help_text: str = "",
) -> str:
    """Edytuje wartość w tym samym pełnoekranowym stylu co menu kursorowe."""

    if not HAS_PROMPT_TOOLKIT or any(item is None for item in (
        _pt_Application,
        _pt_KeyBindings,
        _pt_Layout,
        _pt_HSplit,
        _pt_VSplit,
        _pt_Window,
        _pt_FormattedTextControl,
        _pt_Style,
        _pt_TextArea,
        _pt_get_app,
        _pt_MouseButton,
        _pt_MouseEventType,
    )):
        raise PackError("Pełnoekranowa edycja wymaga kompletnej biblioteki prompt_toolkit.")

    assert _pt_Application is not None
    assert _pt_KeyBindings is not None
    assert _pt_Layout is not None
    assert _pt_HSplit is not None
    assert _pt_VSplit is not None
    assert _pt_Window is not None
    assert _pt_FormattedTextControl is not None
    assert _pt_Style is not None
    assert _pt_TextArea is not None
    assert _pt_get_app is not None
    assert _pt_MouseButton is not None
    assert _pt_MouseEventType is not None
    pt_get_app = _pt_get_app
    mouse_button = _pt_MouseButton
    mouse_event_type = _pt_MouseEventType

    completer = None
    if path_mode and _pt_PathCompleter is not None:
        completer = _pt_PathCompleter(only_directories=True, expanduser=True)
    def _accept_buffer(buffer: Any) -> bool:
        pt_get_app().exit(result=buffer.text)
        return True

    editor = _pt_TextArea(
        text=current,
        multiline=False,
        wrap_lines=False,
        completer=completer,
        complete_while_typing=False,
        accept_handler=_accept_buffer,
        style="class:input",
    )
    editor.buffer.cursor_position = len(current)
    original_editor_mouse_handler = editor.control.mouse_handler

    def editor_mouse_handler(mouse_event: Any) -> object:
        if (
            mouse_event.event_type == mouse_event_type.MOUSE_UP
            and mouse_event.button == mouse_button.RIGHT
        ):
            pt_get_app().exit(result=UI_CANCEL_MARKER)
            return None
        return original_editor_mouse_handler(mouse_event)

    editor.control.mouse_handler = editor_mouse_handler
    bindings = _pt_KeyBindings()

    @bindings.add("c-a", eager=True)
    def _clear_field(event: Any) -> None:
        editor.buffer.text = ""

    @bindings.add("escape", eager=True)
    def _cancel(event: Any) -> None:
        event.app.exit(result=UI_CANCEL_MARKER)

    @bindings.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=UI_EXIT_MARKER)

    header = _pt_FormattedTextControl([
        ("class:header.title", f"  {title}"),
        ("class:header.subtitle", "  •  edycja wiersza"),
    ])
    help_control = _pt_FormattedTextControl(
        [("class:help", "  " + (help_text or "Wpisz wartość i zatwierdź Enterem."))]
    )
    footer = _pt_FormattedTextControl([
        ("class:footer.key", " Enter "), ("class:footer.text", "zapisz  "),
        ("class:footer.key", " Ctrl+A "), ("class:footer.text", "wyczyść pole  "),
        ("class:footer.key", " Esc/PPM "), ("class:footer.text", "anuluj  "),
        ("class:footer.key", " Ctrl+X "), ("class:footer.text", "wyjdź bez zapisu "),
    ])
    layout = _pt_Layout(
        _pt_HSplit([
            _pt_Window(height=2, content=header, style="class:header", wrap_lines=False),
            _pt_Window(height=1, char="─", style="class:border"),
            _pt_Window(height=2, content=help_control, style="class:help", wrap_lines=True),
            _pt_Window(height=1),
            _pt_VSplit([
                _pt_Window(width=max(12, len(label) + 4), content=_pt_FormattedTextControl(
                    [("class:field.label", f"  {label}: ")]
                )),
                _pt_Window(width=1, content=_pt_FormattedTextControl([("class:field.bracket", "[")])),
                editor,
                _pt_Window(width=1, content=_pt_FormattedTextControl([("class:field.bracket", "]")])),
                _pt_Window(width=2),
            ]),
            _pt_Window(),
            _pt_Window(height=1, char="─", style="class:border"),
            _pt_Window(height=1, content=footer, style="class:footer", wrap_lines=False),
        ]),
        focused_element=editor,
    )
    style = _pt_Style.from_dict({
        "root": "bg:ansiblack fg:ansiwhite",
        "header": "bg:ansiblack",
        "header.title": "ansibrightcyan bold",
        "header.subtitle": "ansibrightblack",
        "border": "ansicyan",
        "help": "ansibrightblack",
        "field.label": "ansibrightcyan bold",
        "field.bracket": "ansicyan bold",
        "input": "bg:ansiwhite fg:ansiblack",
        "footer": "bg:ansiblack",
        "footer.key": "bg:ansicyan fg:ansiblack bold",
        "footer.text": "ansibrightblack",
    })
    app = _pt_Application(
        layout=layout,
        key_bindings=bindings,
        style=style,
        full_screen=True,
        erase_when_done=True,
        mouse_support=True,
    )
    result = app.run()
    if result == UI_CANCEL_MARKER:
        raise UserCancelledInput()
    if result == UI_EXIT_MARKER:
        raise UserRequestedExit()
    value = str(result or "").strip()
    return value or current


def ask_text_value(label: str, current: str, *, ui_mode: str, path_mode: bool = False) -> str:
    if ui_mode == "kursorowy":
        return cursor_edit_value(
            f"Edycja: {label}",
            label,
            current,
            path_mode=path_mode,
            help_text=(
                "Ścieżka pozostaje edytowana w tym samym interfejsie. "
                "Możesz użyć uzupełniania katalogów klawiszem Tab."
                if path_mode else "Wartość jest zapisywana dopiero po naciśnięciu Enter."
            ),
        )
    prompt_label = _paint(label, ANSI_BRIGHT_CYAN, ANSI_BOLD)
    value = input(f"{prompt_label} [{current}]: ").strip()
    _control_result(value)
    return value or current


def ask_int_value(label: str, current: int, minimum: int, maximum: int, *, ui_mode: str) -> int:
    while True:
        raw = ask_text_value(label, str(current), ui_mode=ui_mode)
        try:
            value = int(raw)
        except ValueError:
            ui_status("Podaj liczbę całkowitą.", "warn")
            continue
        if minimum <= value <= maximum:
            return value
        ui_status(f"Wartość musi być w zakresie {minimum}-{maximum}.", "warn")


def ask_yes_no(label: str, current: bool, *, ui_mode: str, explicit: bool = False) -> bool:
    if ui_mode == "kursorowy":
        labels = ["TAK", "NIE"]
        details = [f"Potwierdź: {label}.", f"Odrzuć: {label}."]
        selected = 0 if current else 1
        choice = cursor_select(
            label,
            labels,
            selected,
            details=details,
            subtitle="Potwierdzenie",
        )
        if choice is None:
            raise UserCancelledInput()
        return choice == 0
    suffix = "T/N" if explicit else ("T/n" if current else "t/N")
    while True:
        raw = ask_text_value(f"{label} [{suffix}]", "", ui_mode=ui_mode).strip().lower()
        if not raw and not explicit:
            return current
        if raw in {"t", "tak", "y", "yes", "1", "true"}:
            return True
        if raw in {"n", "nie", "no", "0", "false"}:
            return False
        ui_status("Wpisz T/Tak albo N/Nie.", "warn")


def _cursor_requirements_available() -> bool:
    return HAS_PROMPT_TOOLKIT and all(
        item is not None
        for item in (
            _pt_Application,
            _pt_KeyBindings,
            _pt_Layout,
            _pt_HSplit,
            _pt_VSplit,
            _pt_Window,
            _pt_FormattedTextControl,
            _pt_Style,
        )
    )


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
    """Pełnoekranowe menu kursorowe z panelem szczegółów i paskiem skrótów."""
    # Bezpośrednie kontrole są celowe: Pylance/Pyright nie zawęża typów
    # opcjonalnych aliasów modułowych na podstawie wyniku pomocniczej funkcji
    # _cursor_requirements_available(). Po tych warunkach każdy symbol ma typ
    # nieopcjonalny, a tryb tekstowy nadal działa bez prompt_toolkit.
    if not HAS_PROMPT_TOOLKIT:
        raise PackError("Tryb kursorowy wymaga biblioteki prompt_toolkit.")
    if _pt_Application is None:
        raise PackError("Brak prompt_toolkit.application.Application.")
    if _pt_KeyBindings is None:
        raise PackError("Brak prompt_toolkit.key_binding.KeyBindings.")
    if _pt_Dimension is None:
        raise PackError("Brak prompt_toolkit.layout.Dimension.")
    if _pt_Layout is None:
        raise PackError("Brak prompt_toolkit.layout.Layout.")
    if _pt_HSplit is None:
        raise PackError("Brak prompt_toolkit.layout.containers.HSplit.")
    if _pt_VSplit is None:
        raise PackError("Brak prompt_toolkit.layout.containers.VSplit.")
    if _pt_Window is None:
        raise PackError("Brak prompt_toolkit.layout.containers.Window.")
    if _pt_FormattedTextControl is None:
        raise PackError("Brak prompt_toolkit.layout.controls.FormattedTextControl.")
    if _pt_Style is None:
        raise PackError("Brak prompt_toolkit.styles.Style.")
    if _pt_get_app is None:
        raise PackError("Brak prompt_toolkit.application.current.get_app.")
    if _pt_MouseButton is None:
        raise PackError("Brak prompt_toolkit.mouse_events.MouseButton.")
    if _pt_MouseEventType is None:
        raise PackError("Brak prompt_toolkit.mouse_events.MouseEventType.")
    pt_get_app = _pt_get_app
    pt_dimension = _pt_Dimension
    mouse_button = _pt_MouseButton
    mouse_event_type = _pt_MouseEventType
    if not rows:
        return None

    index = max(0, min(selected, len(rows) - 1))
    details = list(details or [""] * len(rows))
    if len(details) < len(rows):
        details.extend([""] * (len(rows) - len(details)))
    status_lines = list(status_lines or [])
    groups = dict(groups or {})
    bindings = _pt_KeyBindings()

    def render_header() -> list[tuple[str, str]]:
        result = [("class:header.title", f"  {title}")]
        if subtitle:
            result.append(("class:header.subtitle", f"  •  {subtitle}"))
        return result

    def row_mouse_handler(row_index: int):
        def _handler(mouse_event: Any) -> object:
            nonlocal index
            app = pt_get_app()
            event_type = mouse_event.event_type
            if (
                event_type == mouse_event_type.MOUSE_UP
                and mouse_event.button == mouse_button.RIGHT
            ):
                app.exit(result=None)
                return None
            if event_type == mouse_event_type.MOUSE_MOVE:
                if index != row_index:
                    index = row_index
                    app.invalidate()
                return None
            if event_type == mouse_event_type.MOUSE_UP:
                index = row_index
                app.invalidate()
                app.exit(result=index)
                return None
            if event_type == mouse_event_type.SCROLL_UP:
                index = (index - 1) % len(rows)
                app.invalidate()
                return None
            if event_type == mouse_event_type.SCROLL_DOWN:
                index = (index + 1) % len(rows)
                app.invalidate()
                return None
            return NotImplemented
        return _handler

    def render_menu() -> list[Any]:
        fragments: list[Any] = []
        for number, row in enumerate(rows):
            section_name = groups.get(number)
            if section_name:
                line_width = 45
                prefix = f"── {section_name} "
                fragments.append((
                    "class:menu.section",
                    ("\n" if number else "") + "  " + prefix + "─" * max(2, line_width - len(prefix)) + "\n",
                ))
            if number == index:
                fragments.append(("[SetCursorPosition]", ""))
                handler = row_mouse_handler(number)
                fragments.append(("class:menu.selected", "  ▶ ", handler))
                fragments.append(("class:menu.selected", row + "\n", handler))
            else:
                fragments.append(("class:menu.item", "    " + row + "\n", row_mouse_handler(number)))
        return fragments

    def render_detail() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = [("class:panel.title", "  AKTUALNA KONFIGURACJA\n")]
        for line in status_lines:
            fragments.append(("class:panel.label", "  " + line + "\n"))
        fragments.append(("class:panel.rule", "\n  " + "─" * max(8, _detail_panel_columns() - 4) + "\n"))
        fragments.append(("class:panel.title", "  WYBRANA OPCJA\n"))
        detail = details[index] if index < len(details) else ""
        for line in _wrap(detail or rows[index], max(20, _detail_panel_columns() - 4), indent="  "):
            fragments.append(("class:panel.text", "  " + line.strip() + "\n"))
        return fragments

    def render_footer() -> list[tuple[str, str]]:
        return [
            ("class:footer.key", " ↑/↓ "), ("class:footer.text", "wybór  "),
            ("class:footer.key", " Mysz "), ("class:footer.text", "wskaż/kliknij  "),
            ("class:footer.key", " Enter "), ("class:footer.text", "zatwierdź  "),
            ("class:footer.key", " Esc/Q/PPM "), ("class:footer.text", "wróć  "),
            ("class:footer.key", " Ctrl+X "), ("class:footer.text", "wyjdź bez zapisu "),
        ]

    menu_control = _pt_FormattedTextControl(text=render_menu, focusable=True, show_cursor=False)
    detail_control = _pt_FormattedTextControl(text=render_detail, focusable=False, show_cursor=False)
    header_control = _pt_FormattedTextControl(text=render_header, focusable=False, show_cursor=False)
    footer_control = _pt_FormattedTextControl(text=render_footer, focusable=False, show_cursor=False)

    @bindings.add("up")
    @bindings.add("k")
    def _up(event: Any) -> None:
        nonlocal index
        index = (index - 1) % len(rows)
        event.app.invalidate()

    @bindings.add("down")
    @bindings.add("j")
    def _down(event: Any) -> None:
        nonlocal index
        index = (index + 1) % len(rows)
        event.app.invalidate()

    @bindings.add("pageup")
    def _page_up(event: Any) -> None:
        nonlocal index
        index = max(0, index - 8)
        event.app.invalidate()

    @bindings.add("pagedown")
    def _page_down(event: Any) -> None:
        nonlocal index
        index = min(len(rows) - 1, index + 8)
        event.app.invalidate()

    @bindings.add("home")
    def _home(event: Any) -> None:
        nonlocal index
        index = 0
        event.app.invalidate()

    @bindings.add("end")
    def _end(event: Any) -> None:
        nonlocal index
        index = len(rows) - 1
        event.app.invalidate()

    @bindings.add("enter")
    def _enter(event: Any) -> None:
        event.app.exit(result=index)

    @bindings.add("escape", eager=True)
    @bindings.add("q", eager=True)
    def _escape(event: Any) -> None:
        event.app.exit(result=None)

    @bindings.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=-2)

    style = _pt_Style.from_dict({
        "root": "bg:ansiblack fg:ansiwhite",
        "header": "bg:ansiblack",
        "header.title": "ansibrightcyan bold",
        "header.subtitle": "ansibrightblack",
        "border": "ansicyan",
        "menu": "bg:ansiblack",
        "menu.item": "ansiwhite",
        "menu.section": "ansibrightblack bold",
        "menu.selected": "bg:ansicyan fg:ansiblack bold",
        "panel": "bg:ansiblack",
        "panel.title": "ansibrightcyan bold",
        "panel.label": "ansibrightblack",
        "panel.rule": "ansicyan",
        "panel.text": "ansiwhite",
        "footer": "bg:ansiblack",
        "footer.key": "bg:ansicyan fg:ansiblack bold",
        "footer.text": "ansibrightblack",
    })

    layout = _pt_Layout(
        _pt_HSplit([
            _pt_Window(height=2, content=header_control, style="class:header", wrap_lines=False),
            _pt_Window(height=1, char="─", style="class:border"),
            _pt_VSplit([
                _pt_Window(content=menu_control, width=50, style="class:menu", wrap_lines=False, always_hide_cursor=True),
                _pt_Window(width=1, char="│", style="class:border"),
                _pt_Window(
                    content=detail_control,
                    width=lambda: pt_dimension.exact(_detail_panel_columns()),
                    style="class:panel",
                    wrap_lines=True,
                    always_hide_cursor=True,
                ),
            ], padding=1, padding_char=" "),
            _pt_Window(height=1, char="─", style="class:border"),
            _pt_Window(height=1, content=footer_control, style="class:footer", wrap_lines=False),
        ])
    )
    app = _pt_Application(
        layout=layout,
        key_bindings=bindings,
        style=style,
        full_screen=True,
        erase_when_done=True,
        mouse_support=True,
    )
    result = app.run()
    if result == -2:
        raise UserRequestedExit()
    return result


def choose_value(title: str, labels: Sequence[str], *, current: int, ui_mode: str, details: Sequence[str] | None = None) -> int | None:
    if ui_mode == "kursorowy":
        return cursor_select(title, labels, current, details=details, subtitle="Wybierz wartość")
    ui_section(title.upper())
    for index, label in enumerate(labels, start=1):
        current_marker = _paint("●", ANSI_BRIGHT_CYAN) if index - 1 == current else " "
        print(f"  {current_marker} {_paint(f'{index:>2}.', ANSI_BRIGHT_BLACK)} {label}")
    raw = input(_paint("Wybór [Enter=powrót]: ", ANSI_BRIGHT_CYAN)).strip()
    _control_result(raw)
    if not raw:
        return None
    try:
        value = int(raw) - 1
    except ValueError:
        return None
    return value if 0 <= value < len(labels) else None


def build_preview_plans(state: InteractiveState, *, print_summaries: bool = True) -> list[PackPlan]:
    if state.ui_mode != "kursorowy":
        ui_status("Buduję kanoniczny plan i obliczam SHA-256 plików…", "info")
    options = state.to_options()
    plans = build_plans_for_options(
        options,
        notice=lambda message, kind: ui_message(state.ui_mode, message, kind),
    )
    if print_summaries:
        for plan in plans:
            print_plan(plan, show_files=False)
    return plans


def cursor_plan_file_browser(plans: Sequence[PackPlan]) -> None:
    """Pełnoekranowa lista wszystkich plików planu z szybkim przewijaniem."""

    if not HAS_PROMPT_TOOLKIT:
        raise PackError("Pełnoekranowa lista plików wymaga biblioteki prompt_toolkit.")
    required = (
        _pt_Application,
        _pt_KeyBindings,
        _pt_Dimension,
        _pt_Layout,
        _pt_HSplit,
        _pt_VSplit,
        _pt_Window,
        _pt_FormattedTextControl,
        _pt_Style,
        _pt_get_app,
        _pt_MouseButton,
        _pt_MouseEventType,
    )
    if any(item is None for item in required):
        raise PackError("Niepełna instalacja prompt_toolkit — brak kontrolek listy plików.")

    assert _pt_Application is not None
    assert _pt_KeyBindings is not None
    assert _pt_Dimension is not None
    assert _pt_Layout is not None
    assert _pt_HSplit is not None
    assert _pt_VSplit is not None
    assert _pt_Window is not None
    assert _pt_FormattedTextControl is not None
    assert _pt_Style is not None
    assert _pt_get_app is not None
    assert _pt_MouseButton is not None
    assert _pt_MouseEventType is not None
    pt_get_app = _pt_get_app
    pt_dimension = _pt_Dimension
    mouse_button = _pt_MouseButton
    mouse_event_type = _pt_MouseEventType

    grouped_entries: dict[str, list[tuple[str, PackPlan, PlanEntry]]] = {"system": [], "memory": []}
    for plan in plans:
        for item in plan.entries:
            section = "memory" if (
                item.relative.startswith("memory/")
                or item.classification.startswith("memory_")
            ) else "system"
            grouped_entries[section].append((section, plan, item))
    entries = grouped_entries["system"] + grouped_entries["memory"]
    if not entries:
        ui_status("Plan nie zawiera plików.", "warn")
        return

    index = 0
    terminal_rows = shutil.get_terminal_size((100, 30)).lines
    page_step = max(6, terminal_rows - 9)
    bindings = _pt_KeyBindings()

    section_labels = {
        "system": "SYSTEM:",
        "memory": "PAMIĘĆ:",
        "combined": "SYSTEM + PAMIĘĆ:",
    }

    def render_header() -> list[tuple[str, str]]:
        total_size = sum(plan.total_size for plan in plans)
        return [
            ("class:header.title", "  PEŁNA LISTA PLIKÓW PLANU"),
            ("class:header.subtitle", f"  •  {len(entries)} plików  •  {human_size(total_size)}"),
        ]

    def file_mouse_handler(row_index: int):
        def _handler(mouse_event: Any) -> object:
            nonlocal index
            app = pt_get_app()
            event_type = mouse_event.event_type
            if (
                event_type == mouse_event_type.MOUSE_UP
                and mouse_event.button == mouse_button.RIGHT
            ):
                app.exit(result=None)
                return None
            if event_type == mouse_event_type.MOUSE_MOVE:
                if index != row_index:
                    index = row_index
                    app.invalidate()
                return None
            if event_type == mouse_event_type.MOUSE_UP:
                index = row_index
                app.invalidate()
                return None
            if event_type == mouse_event_type.SCROLL_UP:
                index = max(0, index - 1)
                app.invalidate()
                return None
            if event_type == mouse_event_type.SCROLL_DOWN:
                index = min(len(entries) - 1, index + 1)
                app.invalidate()
                return None
            return NotImplemented
        return _handler

    def render_list() -> list[Any]:
        fragments: list[Any] = []
        previous_section: str | None = None
        for number, (section, plan, item) in enumerate(entries):
            if section != previous_section:
                if fragments:
                    fragments.append(("class:list.section", "\n"))
                fragments.append((
                    "class:list.section",
                    f"  {section_labels[section]}\n",
                ))
                previous_section = section
            marker = "V" if item.is_virtual else "F"
            number_text = f"{number + 1:>5}."
            size_text = human_size(item.size_bytes)
            row = f"[{marker}] {item.relative}  ({size_text})"
            if number == index:
                fragments.append(("[SetCursorPosition]", ""))
                fragments.append(("class:list.selected", f"  ▶ {number_text} {row}\n", file_mouse_handler(number)))
            else:
                fragments.append(("class:list.item", f"    {number_text} {row}\n", file_mouse_handler(number)))
        return fragments

    def render_detail() -> list[tuple[str, str]]:
        section, plan, item = entries[index]
        fragments: list[tuple[str, str]] = [
            ("class:panel.title", "  WYBRANY PLIK\n"),
            ("class:panel.label", f"  Pozycja      {index + 1}/{len(entries)}\n"),
            ("class:panel.label", f"  Sekcja       {section_labels[section]}\n"),
            ("class:panel.label", f"  Typ          {'wirtualny' if item.is_virtual else 'plik źródłowy'}\n"),
            ("class:panel.label", f"  Rozmiar      {human_size(item.size_bytes)}\n"),
            ("class:panel.label", f"  Klasyfikacja {item.classification}\n"),
            ("class:panel.rule", "\n  " + "─" * max(8, _detail_panel_columns() - 4) + "\n"),
            ("class:panel.title", "  ŚCIEŻKA\n"),
        ]
        for line in _wrap(item.relative, max(20, _detail_panel_columns() - 4), indent="  "):
            fragments.append(("class:panel.text", "  " + line.strip() + "\n"))
        fragments.extend([
            ("class:panel.rule", "\n  " + "─" * max(8, _detail_panel_columns() - 4) + "\n"),
            ("class:panel.title", "  SHA-256\n"),
        ])
        for line in _wrap(item.sha256, max(20, _detail_panel_columns() - 4), indent="  "):
            fragments.append(("class:panel.hash", "  " + line.strip() + "\n"))
        return fragments

    def render_footer() -> list[tuple[str, str]]:
        return [
            ("class:footer.key", " ↑/↓ "), ("class:footer.text", "plik  "),
            ("class:footer.key", " Mysz "), ("class:footer.text", "wskaż/kliknij/przewiń  "),
            ("class:footer.key", " Enter/PgDn "), ("class:footer.text", "następna strona  "),
            ("class:footer.key", " PgUp "), ("class:footer.text", "poprzednia strona  "),
            ("class:footer.key", " Home/End "), ("class:footer.text", "początek/koniec  "),
            ("class:footer.key", " Esc/Q/PPM "), ("class:footer.text", "wróć "),
        ]

    list_control = _pt_FormattedTextControl(text=render_list, focusable=True, show_cursor=False)
    detail_control = _pt_FormattedTextControl(text=render_detail, focusable=False, show_cursor=False)
    header_control = _pt_FormattedTextControl(text=render_header, focusable=False, show_cursor=False)
    footer_control = _pt_FormattedTextControl(text=render_footer, focusable=False, show_cursor=False)

    def _move(event: Any, target: int) -> None:
        nonlocal index
        index = max(0, min(len(entries) - 1, target))
        event.app.invalidate()

    @bindings.add("up")
    @bindings.add("k")
    def _up(event: Any) -> None:
        _move(event, index - 1)

    @bindings.add("down")
    @bindings.add("j")
    def _down(event: Any) -> None:
        _move(event, index + 1)

    @bindings.add("enter")
    @bindings.add("pagedown")
    def _page_down(event: Any) -> None:
        _move(event, index + page_step)

    @bindings.add("pageup")
    def _page_up(event: Any) -> None:
        _move(event, index - page_step)

    @bindings.add("home")
    def _home(event: Any) -> None:
        _move(event, 0)

    @bindings.add("end")
    def _end(event: Any) -> None:
        _move(event, len(entries) - 1)

    @bindings.add("escape", eager=True)
    @bindings.add("q", eager=True)
    def _close(event: Any) -> None:
        event.app.exit(result=None)

    @bindings.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=UI_EXIT_MARKER)

    style = _pt_Style.from_dict({
        "root": "bg:ansiblack fg:ansiwhite",
        "header": "bg:ansiblack",
        "header.title": "ansibrightcyan bold",
        "header.subtitle": "ansibrightblack",
        "border": "ansicyan",
        "list": "bg:ansiblack",
        "list.item": "ansiwhite",
        "list.section": "ansibrightcyan bold",
        "list.selected": "bg:ansicyan fg:ansiblack bold",
        "panel": "bg:ansiblack",
        "panel.title": "ansibrightcyan bold",
        "panel.label": "ansibrightblack",
        "panel.rule": "ansicyan",
        "panel.text": "ansiwhite",
        "panel.hash": "ansibrightgreen",
        "footer": "bg:ansiblack",
        "footer.key": "bg:ansicyan fg:ansiblack bold",
        "footer.text": "ansibrightblack",
    })

    layout = _pt_Layout(
        _pt_HSplit([
            _pt_Window(height=2, content=header_control, style="class:header", wrap_lines=False),
            _pt_Window(height=1, char="─", style="class:border"),
            _pt_VSplit([
                _pt_Window(content=list_control, width=72, style="class:list", wrap_lines=False, always_hide_cursor=True),
                _pt_Window(width=1, char="│", style="class:border"),
                _pt_Window(
                    content=detail_control,
                    width=lambda: pt_dimension.exact(_detail_panel_columns()),
                    style="class:panel",
                    wrap_lines=True,
                    always_hide_cursor=True,
                ),
            ], padding=1, padding_char=" "),
            _pt_Window(height=1, char="─", style="class:border"),
            _pt_Window(height=1, content=footer_control, style="class:footer", wrap_lines=False),
        ])
    )
    app = _pt_Application(
        layout=layout,
        key_bindings=bindings,
        style=style,
        full_screen=True,
        erase_when_done=True,
        mouse_support=True,
    )
    result = app.run()
    if result == UI_EXIT_MARKER:
        raise UserRequestedExit()


def pack_from_interactive(state: InteractiveState) -> None:
    options = state.to_options()
    plans = build_preview_plans(state, print_summaries=state.ui_mode != "kursorowy")
    preview_hashes = {plan.profile: plan.plan_sha256() for plan in plans}
    if not ask_yes_no("Rozpocząć pakowanie dokładnie tego planu", False, ui_mode=state.ui_mode, explicit=True):
        ui_message(state.ui_mode, "Pakowanie anulowane.", "warn")
        return
    if state.ui_mode != "kursorowy":
        ui_status("Plan zatwierdzony. Pakuję bez ponownego skanowania.", "info")
    results = run_pack_with_plans(options, plans)
    for result in results:
        if result.plan.plan_sha256() != preview_hashes[result.profile]:
            raise PackError(f"Hash planu zmienił się dla profilu {result.profile}.")
    print_results(results)


def show_plan_interactive(state: InteractiveState) -> None:
    cursor_mode = state.ui_mode == "kursorowy"
    plans = build_preview_plans(state, print_summaries=not cursor_mode)
    if cursor_mode:
        cursor_plan_file_browser(plans)
        return
    show_files = ask_yes_no("Pokazać pełną listę plików", True, ui_mode=state.ui_mode)
    if show_files:
        for plan in plans:
            print_plan(plan, show_files=True)

def _sidecar_candidates(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    candidates = [path for path in folder.glob("*.package.json") if path.is_file()]
    return sorted(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)


def resolve_sidecar_path(value: str | Path, *, fallback_folder: Path | None = None) -> Path:
    """Akceptuje plik sidecar albo katalog i wybiera najnowszy package.json."""

    path = Path(value).expanduser().resolve()
    if path.is_file():
        if not path.name.lower().endswith(".package.json"):
            raise PackError(f"Wybrany plik nie jest sidecarem *.package.json: {path}")
        return path
    if path.is_dir():
        matches = _sidecar_candidates(path)
        if not matches:
            raise PackError(f"Folder nie zawiera pliku *.package.json: {path}")
        if len(matches) > 1:
            ui_status(
                f"Folder zawiera {len(matches)} sidecarów; używam najnowszego: {matches[0].name}",
                "info",
            )
        return matches[0]
    if fallback_folder is not None:
        fallback_matches = _sidecar_candidates(fallback_folder.expanduser().resolve())
        if len(fallback_matches) == 1:
            return fallback_matches[0]
    raise PackError(f"Nie znaleziono pliku ani folderu sidecara: {path}")


def suggested_sidecar_path(state: InteractiveState) -> Path:
    matches = _sidecar_candidates(state.out_dir.expanduser().resolve())
    return matches[0] if matches else state.out_dir


def verify_interactive(state: InteractiveState) -> None:
    default = suggested_sidecar_path(state)
    raw = ask_text_value("Plik lub folder *.package.json", str(default), ui_mode=state.ui_mode, path_mode=False)
    path = resolve_sidecar_path(raw, fallback_folder=state.out_dir)
    report = verify_package_sidecar(path)
    ui_banner("WERYFIKACJA ZAKOŃCZONA", "CRC, SHA-256 i zgodność planu")
    ui_status("Paczka jest poprawna.", "ok")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def extract_interactive(state: InteractiveState) -> None:
    sidecar_default = suggested_sidecar_path(state)
    sidecar_raw = ask_text_value(
        "Plik lub folder *.package.json",
        str(sidecar_default),
        ui_mode=state.ui_mode,
    )
    sidecar_path = resolve_sidecar_path(sidecar_raw, fallback_folder=state.out_dir)
    destination_default = state.source.parent / "jazn_runtime_test"
    destination_raw = ask_text_value("Folder docelowy", str(destination_default), ui_mode=state.ui_mode, path_mode=True)
    clean = ask_yes_no("Wyczyścić folder docelowy", False, ui_mode=state.ui_mode)
    force = ask_yes_no("Pozwolić na nadpisanie plików", False, ui_mode=state.ui_mode)
    report = extract_package_sidecar(
        sidecar_path,
        Path(destination_raw).expanduser().resolve(),
        clean=clean,
        force=force,
    )
    ui_banner("ROZPAKOWANIE ZAKOŃCZONE", str(report.get("destination") or destination_raw))
    ui_status("Paczka została zweryfikowana i bezpiecznie rozpakowana.", "ok")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def compression_self_test_interactive() -> None:
    ui_banner("TEST KOMPRESJI", "Independent + binary + CRC + SHA-256 + extract")
    ui_status("Uruchamiam izolowany test regresji funkcji ZIP…", "info")
    report = run_compression_self_test()
    ui_status("Funkcje kompresji i dzielenia paczek są poprawne.", "ok")
    print(json.dumps(report, ensure_ascii=False, indent=2))


EXCLUSION_SCOPE_LABELS = {
    "common": "wspólne",
    "system": "system",
    "memory": "pamięć",
}


def _choose_exclusion_scope(current: str, *, ui_mode: str) -> str | None:
    values = ["common", "system", "memory"]
    labels = ["wspólne — system i pamięć", "system — tylko paczka systemowa", "pamięć — tylko paczka pamięci"]
    details = [
        "Reguła działa w każdym profilu planu.",
        "Reguła jest stosowana tylko do planu systemowego.",
        "Reguła jest stosowana tylko do planu pamięci.",
    ]
    selected = values.index(current) if current in values else 0
    choice = choose_value("Zakres wykluczenia", labels, current=selected, ui_mode=ui_mode, details=details)
    return None if choice is None else values[choice]


def _edit_base_rule(state: InteractiveState, index: int) -> None:
    while 0 <= index < len(state.base_excludes):
        rule = state.base_excludes[index]
        labels = [
            f"Wzorzec: [{rule.pattern}]",
            f"Zakres: [{EXCLUSION_SCOPE_LABELS.get(rule.scope, rule.scope)}]",
            f"Używanie reguły: [{'WŁĄCZONE' if rule.enabled else 'WYŁĄCZONE'}]",
            "Usuń regułę",
            "Wróć",
        ]
        details = [
            "Edytuj wzorzec fnmatch, np. *.zip, memory/ albo **/__pycache__/.",
            "Wybierz, czy reguła dotyczy obu profili, tylko systemu, czy tylko pamięci.",
            "Wyłączona reguła pozostaje zapisana, ale nie wpływa na plan.",
            "Usuń tę regułę z listy podstawowej.",
            "Powrót do listy reguł podstawowych.",
        ]
        choice = choose_value("Reguła podstawowa", labels, current=0, ui_mode=state.ui_mode, details=details)
        if choice is None or choice == 4:
            return
        if choice == 0:
            value = ask_text_value("Wzorzec", rule.pattern, ui_mode=state.ui_mode)
            if value.strip() and value.strip() != rule.pattern:
                rule.pattern = value.strip().replace("\\", "/")
                state.dirty = True
        elif choice == 1:
            scope = _choose_exclusion_scope(rule.scope, ui_mode=state.ui_mode)
            if scope and scope != rule.scope:
                rule.scope = scope
                state.dirty = True
        elif choice == 2:
            rule.enabled = not rule.enabled
            state.dirty = True
        elif choice == 3:
            if ask_yes_no("Usunąć wybraną regułę podstawową", False, ui_mode=state.ui_mode, explicit=True):
                del state.base_excludes[index]
                state.dirty = True
                return


def edit_base_excludes(state: InteractiveState) -> None:
    selected = 0
    while True:
        rows = [
            f"[{'ON ' if item.enabled else 'OFF'}] {EXCLUSION_SCOPE_LABELS.get(item.scope, item.scope)}: {item.pattern}"
            for item in state.base_excludes
        ]
        rows.extend(["+ Dodaj regułę podstawową", "Przywróć domyślne reguły", "Wróć"])
        details = [
            "Wybierz regułę, aby edytować wzorzec, zakres, stan albo ją usunąć. "
            "Ochrona sekretów i plików WAL/SHM pozostaje stała."
            for _ in state.base_excludes
        ]
        details.extend([
            "Dodaj nową edytowalną regułę do listy podstawowej.",
            "Odtwórz kompletny zestaw reguł dostarczony z generatorem.",
            "Powrót do menu wykluczeń.",
        ])
        choice = choose_value(
            "Wykluczenia podstawowe",
            rows,
            current=min(selected, max(0, len(rows) - 1)),
            ui_mode=state.ui_mode,
            details=details,
        )
        if choice is None or choice == len(rows) - 1:
            return
        selected = choice
        if choice < len(state.base_excludes):
            _edit_base_rule(state, choice)
            continue
        if choice == len(state.base_excludes):
            scope = _choose_exclusion_scope("common", ui_mode=state.ui_mode)
            if scope is None:
                continue
            pattern = ask_text_value("Nowy wzorzec", "*", ui_mode=state.ui_mode).strip().replace("\\", "/")
            if pattern:
                state.base_excludes.append(ExclusionRule(scope, pattern, True))
                state.dirty = True
            continue
        if choice == len(state.base_excludes) + 1:
            if ask_yes_no("Przywrócić wszystkie domyślne reguły podstawowe", False, ui_mode=state.ui_mode, explicit=True):
                state.base_excludes = default_exclusion_rules()
                state.dirty = True


def _edit_manual_rule(state: InteractiveState, index: int) -> None:
    while 0 <= index < len(state.custom_excludes):
        pattern = state.custom_excludes[index]
        labels = [f"Wzorzec: [{pattern}]", "Usuń wzorzec", "Wróć"]
        details = [
            "Edytuj ręcznie dołączony wzorzec fnmatch.",
            "Usuń ten wzorzec z listy ręcznej.",
            "Powrót do listy ręcznej.",
        ]
        choice = choose_value("Wykluczenie ręczne", labels, current=0, ui_mode=state.ui_mode, details=details)
        if choice is None or choice == 2:
            return
        if choice == 0:
            value = ask_text_value("Wzorzec ręczny", pattern, ui_mode=state.ui_mode).strip().replace("\\", "/")
            if value and value != pattern:
                state.custom_excludes[index] = value
                state.dirty = True
        elif choice == 1:
            if ask_yes_no("Usunąć wybrane wykluczenie ręczne", False, ui_mode=state.ui_mode, explicit=True):
                del state.custom_excludes[index]
                if not state.custom_excludes:
                    state.custom_excludes_enabled = False
                state.dirty = True
                return


def edit_manual_excludes(state: InteractiveState) -> None:
    selected = 0
    while True:
        rows = [f"[{index + 1}] {pattern}" for index, pattern in enumerate(state.custom_excludes)]
        rows.extend(["+ Dodaj wykluczenie ręczne", "Wróć"])
        details = ["Wybierz wzorzec, aby go edytować lub usunąć." for _ in state.custom_excludes]
        details.extend([
            "Dodaj nowy wzorzec. Po pierwszym dodaniu używanie listy zostanie automatycznie włączone.",
            "Powrót do menu wykluczeń.",
        ])
        choice = choose_value(
            "Lista wykluczeń ręcznych",
            rows,
            current=min(selected, max(0, len(rows) - 1)),
            ui_mode=state.ui_mode,
            details=details,
        )
        if choice is None or choice == len(rows) - 1:
            return
        selected = choice
        if choice < len(state.custom_excludes):
            _edit_manual_rule(state, choice)
            continue
        pattern = ask_text_value("Nowy wzorzec ręczny", "*.tmp", ui_mode=state.ui_mode).strip().replace("\\", "/")
        if pattern:
            state.custom_excludes.append(pattern)
            state.custom_excludes_enabled = True
            state.dirty = True


def edit_excludes(state: InteractiveState) -> None:
    selected = 0
    while True:
        active_base = sum(1 for item in state.base_excludes if item.enabled)
        manual_status = "BRAK — OFF"
        if state.custom_excludes:
            manual_status = "WŁĄCZONE" if state.custom_excludes_enabled else "WYŁĄCZONE"
        labels = [
            f"Podstawowe: [{active_base}/{len(state.base_excludes)} aktywnych]",
            f"Ręczne używanie: [{manual_status}]",
            f"Lista ręczna: [{len(state.custom_excludes)}]",
            "Wróć",
        ]
        details = [
            "Lista domyślnych reguł z możliwością dodawania, edycji, usuwania, włączania i wyłączania.",
            "Włącza lub wyłącza stosowanie ręcznie dołączonych wzorców. Bez wpisów stan zawsze pozostaje OFF.",
            "Dodawaj, edytuj i usuwaj osobne wzorce ręczne.",
            "Powrót do strony głównej programu.",
        ]
        choice = choose_value("Wykluczenia", labels, current=selected, ui_mode=state.ui_mode, details=details)
        if choice is None or choice == 3:
            return
        selected = choice
        if choice == 0:
            edit_base_excludes(state)
        elif choice == 1:
            if not state.custom_excludes:
                state.custom_excludes_enabled = False
                ui_message(state.ui_mode, "Lista ręczna jest pusta — używanie pozostaje wyłączone.", "warn")
            else:
                state.custom_excludes_enabled = not state.custom_excludes_enabled
                state.dirty = True
        elif choice == 2:
            edit_manual_excludes(state)


def edit_interface(state: InteractiveState) -> None:
    labels = [
        "Tekstowy — kolorowe, pogrupowane menu",
        "Kursorowy — pełnoekranowy panel" + ("" if HAS_PROMPT_TOOLKIT else " — niedostępny: brak prompt_toolkit"),
        f"Automatyczny start zapisanym trybem: {'TAK' if state.ui_auto_start else 'NIE'}",
    ]
    details = [
        "Działa bez dodatkowych bibliotek. Kolory można wyłączyć przez NO_COLOR=1.",
        "LPM, PPM=wstecz, scroll, strzałki, Enter, Esc/Q, Ctrl+X, panel szczegółów i stały pasek skrótów.",
        "Po włączeniu generator nie pyta o interfejs przy następnym uruchomieniu.",
    ]
    selected = 1 if state.ui_mode == "kursorowy" else 0
    choice = choose_value("Interfejs", labels, current=selected, ui_mode=state.ui_mode, details=details)
    if choice is None:
        return
    if choice == 0:
        state.ui_mode = "tekstowy"
    elif choice == 1:
        if not HAS_PROMPT_TOOLKIT:
            ui_status("Zainstaluj: py -m pip install prompt_toolkit", "warn")
            return
        state.ui_mode = "kursorowy"
    elif choice == 2:
        state.ui_auto_start = not state.ui_auto_start
    state.dirty = True


PROFILE_UI_LABELS = {
    "dual": "system + pamięć",
    "system": "system",
    "memory": "pamięć",
    "combined": "system + pamięć razem",
}

PROFILE_UI_DETAILS = {
    "dual": "Tworzy dwie oddzielne paczki: jedną z systemem i drugą z katalogiem memory/.",
    "system": "Pakuje kod i pliki statyczne. Pomija memory/, workspace_runtime/ i bazy poza pamięcią.",
    "memory": "Pakuje wyłącznie katalog memory/, łącznie z SQLite, bez WAL/SHM i zagnieżdżonych archiwów.",
    "combined": "Umieszcza system i pamięć w jednej wspólnej paczce.",
}


def main_menu_rows(state: InteractiveState) -> list[str]:
    active_base = sum(1 for item in state.base_excludes if item.enabled)
    if state.custom_excludes:
        manual = f"{len(state.custom_excludes)} {'ON' if state.custom_excludes_enabled else 'OFF'}"
    else:
        manual = "0 OFF"
    return [
        "Pakuj teraz",
        "Pokaż plan i pełną listę plików",
        f"Nazwa: [{state.archive_basename}]",
        f"Profil: [{PROFILE_UI_LABELS.get(state.profile, state.profile)}]",
        f"Źródło: [{_display_path(state.source)}]",
        f"Wyjście: [{_display_path(state.out_dir)}]",
        f"Pliki pomocnicze: [{'TAK' if state.sidecars else 'NIE'}]",
        f"Format: [{state.archive_format}]",
        f"Limit: [{state.part_size_mb} MiB]",
        f"Kompresja: [{state.compression_level}]",
        f"Nadpisywanie: [{'TAK' if state.force else 'NIE'}]",
        f"Interfejs: [{state.ui_mode}]",
        f"Wykluczenia: [podstawowe {active_base}/{len(state.base_excludes)}; ręczne {manual}]",
        "Zapisz ustawienia",
        "Zweryfikuj istniejącą paczkę",
        "Bezpiecznie rozpakuj paczkę",
        "Test kompresji generatora",
        "Wyjdź",
    ]


def main_menu_details(state: InteractiveState) -> list[str]:
    return [
        "Zbuduj podgląd, zatwierdź zamrożony plan i utwórz paczkę z pełną weryfikacją.",
        "Otwórz pełnoekranową listę wszystkich plików w sekcjach SYSTEM: i PAMIĘĆ:, z nawigacją Enter, PgDn, PgUp, Home i End.",
        "Bazowa nazwa. Numer i release-name zostaną pobrane z version.py.",
        PROFILE_UI_DETAILS.get(state.profile, state.profile),
        f"Edytowany katalog źródłowy: {_display_path(state.source)}. Musi zawierać latka_jazn/version.py.",
        f"Edytowany folder wynikowy: {_display_path(state.out_dir)}. Musi leżeć poza katalogiem źródłowym.",
        "Twórz package.json, parts.sha256 oraz join.ps1 dla formatu binary.",
        "auto wybiera independent, a przy zbyt dużym pliku binary .zip.001.",
        "Maksymalny rozmiar woluminu. Pojedynczy większy plik może wymusić binary.",
        "Wybierz poziom DEFLATE 0–9 z listy. Poziom 6 jest zalecanym kompromisem.",
        "Gdy wyłączone, istniejąca paczka o tej samej nazwie nie zostanie ruszona.",
        "Wybór trybu tekstowego/kursorowego i automatycznego startu.",
        "Edytuj reguły podstawowe oraz osobną listę ręczną z niezależnym przełącznikiem używania.",
        "Zapisz konfigurację atomowo obok skryptu.",
        "Sprawdź sidecar, SHA-256, CRC, kompletność oraz wersję bez rozpakowywania.",
        "Najpierw zweryfikuj, potem rozpakuj z ochroną przed path traversal.",
        "Uruchom izolowany test ZIP_DEFLATED dla paczek independent i binary, wraz z verify i extract.",
        "Zakończ. Przy zmianach generator zapyta, czy zapisać ustawienia.",
    ]


def cursor_main_menu(state: InteractiveState, selected: int = 0) -> tuple[int | None, int]:
    """Stały ekran główny z edycją pól i modalnymi wyborami opcji."""

    required = (
        _pt_Application,
        _pt_KeyBindings,
        _pt_Dimension,
        _pt_Layout,
        _pt_HSplit,
        _pt_VSplit,
        _pt_Window,
        _pt_FormattedTextControl,
        _pt_Style,
        _pt_TextArea,
        _pt_DynamicContainer,
        _pt_ScrollablePane,
        _pt_get_app,
        _pt_Float,
        _pt_FloatContainer,
        _pt_ConditionalContainer,
        _pt_Condition,
        _pt_MouseButton,
        _pt_MouseEventType,
    )
    if not HAS_PROMPT_TOOLKIT or any(item is None for item in required):
        raise PackError("Tryb kursorowy wymaga kompletnej biblioteki prompt_toolkit.")

    assert _pt_Application is not None
    assert _pt_KeyBindings is not None
    assert _pt_Dimension is not None
    assert _pt_Layout is not None
    assert _pt_HSplit is not None
    assert _pt_VSplit is not None
    assert _pt_Window is not None
    assert _pt_FormattedTextControl is not None
    assert _pt_Style is not None
    assert _pt_TextArea is not None
    assert _pt_DynamicContainer is not None
    assert _pt_ScrollablePane is not None
    assert _pt_get_app is not None
    assert _pt_Float is not None
    assert _pt_FloatContainer is not None
    assert _pt_ConditionalContainer is not None
    assert _pt_Condition is not None
    assert _pt_MouseButton is not None
    assert _pt_MouseEventType is not None

    # Lokalne aliasy są celowe: Pylance nie przenosi zawsze zawężenia typu
    # opcjonalnych aliasów modułowych do funkcji zagnieżdżonych.
    pt_vsplit = _pt_VSplit
    pt_window = _pt_Window
    pt_formatted_text_control = _pt_FormattedTextControl
    pt_dynamic_container = _pt_DynamicContainer
    pt_scrollable_pane = _pt_ScrollablePane
    pt_get_app = _pt_get_app
    pt_dimension = _pt_Dimension
    mouse_button = _pt_MouseButton
    mouse_event_type = _pt_MouseEventType

    row_count = len(main_menu_rows(state))
    index = max(0, min(selected, row_count - 1))
    editing = False
    edit_index: int | None = None
    edit_original = ""
    status_message = ""

    popup_active = False
    popup_title = ""
    popup_labels: list[str] = []
    popup_details: list[str] = []
    popup_values: list[Any] = []
    popup_index = 0
    popup_row_index: int | None = None

    bindings = _pt_KeyBindings()
    path_completer = _pt_PathCompleter(only_directories=True, expanduser=True) if _pt_PathCompleter else None

    inline_fields = {
        MENU_NAME: ("Nazwa", "", False),
        MENU_SOURCE: ("Źródło", "", True),
        MENU_OUTPUT: ("Wyjście", "", True),
        MENU_LIMIT: ("Limit", " MiB", False),
    }

    def current_inline_value(row_index: int) -> str:
        if row_index == MENU_NAME:
            return state.archive_basename
        if row_index == MENU_SOURCE:
            return _display_path(state.source)
        if row_index == MENU_OUTPUT:
            return _display_path(state.out_dir)
        if row_index == MENU_LIMIT:
            return str(state.part_size_mb)
        raise PackError(f"Wiersz {row_index} nie jest polem edycyjnym.")

    def option_spec(row_index: int) -> tuple[str, list[Any], Sequence[str], Sequence[str], int]:
        if row_index == MENU_PROFILE:
            values: list[Any] = ["dual", "system", "memory", "combined"]
            labels = [
                "system + pamięć osobno",
                "tylko system",
                "tylko pamięć",
                "system + pamięć razem",
            ]
            details = [
                "Tworzy dwie niezależne paczki: systemową i pamięciową.",
                "Pakuje kod i pliki statyczne bez katalogu memory/.",
                "Pakuje wyłącznie katalog memory/, w tym pliki SQLite.",
                "Umieszcza system i pamięć w jednej wspólnej paczce.",
            ]
            return "Wybierz profil", values, labels, details, values.index(state.profile)
        if row_index == MENU_SIDECARS:
            values = [False, True]
            labels = ["NIE — tylko package.json", "TAK — pełny zestaw pomocniczy"]
            details = [
                "Zostanie utworzony obowiązkowy package.json bez dodatkowych sum i skryptu join.",
                "Powstaną także parts.sha256, pełny SHA-256 oraz join.ps1 dla formatu binary.",
            ]
            return "Pliki pomocnicze", values, labels, details, 1 if state.sidecars else 0
        if row_index == MENU_FORMAT:
            values = ["auto", "independent", "binary"]
            labels = [
                "auto — dobór automatyczny",
                "independent — samodzielne ZIP-y",
                "binary — .zip.001/.002…",
            ]
            details = [
                "Wybiera independent, chyba że rozmiar pliku wymaga podziału binarnego.",
                "Każdy wolumin jest kompletnym archiwum ZIP.",
                "Jeden logiczny ZIP jest dzielony bajtowo na kolejne części.",
            ]
            return "Wybierz format", values, labels, details, values.index(state.archive_format)
        if row_index == MENU_COMPRESSION:
            values = list(COMPRESSION_CHOICES)
            labels = list(COMPRESSION_UI_LABELS)
            details = list(COMPRESSION_UI_DETAILS)
            return "Poziom kompresji", values, labels, details, values.index(state.compression_level)
        if row_index == MENU_FORCE:
            values = [False, True]
            labels = ["NIE — chroń istniejące paczki", "TAK — zastąp istniejące paczki"]
            details = [
                "Generator przerwie operację, gdy znajdzie pliki wynikowe o tej samej nazwie.",
                "Generator wykona transakcyjną podmianę istniejących plików wynikowych.",
            ]
            return "Nadpisywanie", values, labels, details, 1 if state.force else 0
        raise PackError(f"Wiersz {row_index} nie ma modalnego wyboru.")

    editor: Any = None
    popup_menu_window: Any = None
    row_windows: list[Any] = []

    def finish_edit(app: Any, raw_value: str) -> bool:
        nonlocal editing, edit_index, status_message
        if edit_index is None:
            return True
        value = str(raw_value).strip() or edit_original
        try:
            if edit_index == MENU_NAME:
                new_value = sanitize_archive_stem(value)
                changed = new_value != state.archive_basename
                state.archive_basename = new_value
            elif edit_index == MENU_SOURCE:
                new_value = Path(value).expanduser().resolve()
                changed = new_value != state.source
                state.source = new_value
            elif edit_index == MENU_OUTPUT:
                new_value = Path(value).expanduser().resolve()
                changed = new_value != state.out_dir
                state.out_dir = new_value
            elif edit_index == MENU_LIMIT:
                new_value = int(value)
                if not 1 <= new_value <= 1024 * 1024:
                    raise ValueError("Limit musi być w zakresie 1–1048576 MiB.")
                changed = new_value != state.part_size_mb
                state.part_size_mb = new_value
            else:
                changed = False
        except (OSError, ValueError, PackError) as exc:
            status_message = f"BŁĄD: {exc}"
            app.invalidate()
            return False

        if changed:
            state.dirty = True
            status_message = "Wartość została zmieniona."
        else:
            status_message = "Wartość bez zmian."
        editing = False
        edit_index = None
        app.layout.focus(row_windows[index])
        app.invalidate()
        return True

    def accept_editor(buffer: Any) -> bool:
        return finish_edit(pt_get_app(), buffer.text)

    editor = _pt_TextArea(
        text="",
        multiline=False,
        wrap_lines=False,
        complete_while_typing=False,
        accept_handler=accept_editor,
        style="class:inline.input",
        height=1,
    )
    original_inline_mouse_handler = editor.control.mouse_handler

    def inline_editor_mouse_handler(mouse_event: Any) -> object:
        if (
            mouse_event.event_type == mouse_event_type.MOUSE_UP
            and mouse_event.button == mouse_button.RIGHT
        ):
            if editing:
                cancel_edit(pt_get_app())
            return None
        return original_inline_mouse_handler(mouse_event)

    editor.control.mouse_handler = inline_editor_mouse_handler

    def start_edit(app: Any, row_index: int) -> None:
        nonlocal editing, edit_index, edit_original, status_message
        edit_index = row_index
        edit_original = current_inline_value(row_index)
        editing = True
        status_message = "Edycja w aktywnym wierszu — Enter zapisuje, Esc anuluje."
        editor.buffer.completer = path_completer if inline_fields[row_index][2] else None
        editor.buffer.text = edit_original
        editor.buffer.cursor_position = len(edit_original)
        app.layout.focus(editor)
        app.invalidate()

    def cancel_edit(app: Any) -> None:
        nonlocal editing, edit_index, status_message
        editing = False
        edit_index = None
        status_message = "Edycję anulowano."
        app.layout.focus(row_windows[index])
        app.invalidate()

    def open_option_popup(app: Any, row_index: int) -> None:
        nonlocal popup_active, popup_title, popup_labels, popup_details
        nonlocal popup_values, popup_index, popup_row_index, status_message
        title, values, labels, details, current = option_spec(row_index)
        popup_active = True
        popup_title = title
        popup_values = list(values)
        popup_labels = list(labels)
        popup_details = list(details)
        popup_index = current
        popup_row_index = row_index
        status_message = ""
        if popup_menu_window is not None:
            app.layout.focus(popup_menu_window)
        app.invalidate()

    def close_option_popup(app: Any, *, cancelled: bool = False) -> None:
        nonlocal popup_active, popup_row_index, status_message
        popup_active = False
        popup_row_index = None
        if cancelled:
            status_message = "Wybór anulowano — wartość bez zmian."
        app.layout.focus(row_windows[index])
        app.invalidate()

    def apply_option_popup(app: Any) -> None:
        nonlocal status_message
        if not popup_active or popup_row_index is None or not popup_values:
            return
        value = popup_values[popup_index]
        changed = False
        if popup_row_index == MENU_PROFILE:
            changed = value != state.profile
            state.profile = str(value)
            status_message = f"Profil: {PROFILE_UI_LABELS.get(state.profile, state.profile)}"
        elif popup_row_index == MENU_SIDECARS:
            changed = bool(value) != state.sidecars
            state.sidecars = bool(value)
            status_message = f"Pliki pomocnicze: {'TAK' if state.sidecars else 'NIE'}"
        elif popup_row_index == MENU_FORMAT:
            changed = value != state.archive_format
            state.archive_format = str(value)
            status_message = f"Format: {state.archive_format}"
        elif popup_row_index == MENU_COMPRESSION:
            changed = int(value) != state.compression_level
            state.compression_level = int(value)
            status_message = f"Kompresja: {state.compression_level}"
        elif popup_row_index == MENU_FORCE:
            changed = bool(value) != state.force
            state.force = bool(value)
            status_message = f"Nadpisywanie: {'TAK' if state.force else 'NIE'}"
        if changed:
            state.dirty = True
        else:
            status_message += " — bez zmian"
        close_option_popup(app)

    def render_header() -> list[tuple[str, str]]:
        return [
            ("class:header.title", f"Narzędzia Jaźni — v{GENERATOR_VERSION} "),
            ("class:header.subtitle", "Interaktywny generator paczek"),
        ]

    def activate_row(app: Any, row_index: int) -> None:
        nonlocal index, status_message
        if editing or popup_active:
            return
        index = max(0, min(row_count - 1, row_index))
        status_message = ""
        app.layout.focus(row_windows[index])
        app.invalidate()
        if index in inline_fields:
            start_edit(app, index)
        elif index in {MENU_PROFILE, MENU_SIDECARS, MENU_FORMAT, MENU_COMPRESSION, MENU_FORCE}:
            open_option_popup(app, index)
        else:
            app.exit(result=index)

    def menu_row_mouse_handler(row_index: int):
        def _handler(mouse_event: Any) -> object:
            nonlocal index, status_message
            app = pt_get_app()
            event_type = mouse_event.event_type
            if (
                event_type == mouse_event_type.MOUSE_UP
                and mouse_event.button == mouse_button.RIGHT
            ):
                if popup_active:
                    close_option_popup(app, cancelled=True)
                elif editing:
                    cancel_edit(app)
                else:
                    app.exit(result=None)
                return None
            if event_type == mouse_event_type.MOUSE_MOVE and not editing and not popup_active:
                if index != row_index:
                    index = row_index
                    status_message = ""
                    app.layout.focus(row_windows[index])
                    app.invalidate()
                return None
            if event_type == mouse_event_type.MOUSE_UP:
                activate_row(app, row_index)
                return None
            if event_type == mouse_event_type.SCROLL_UP and not editing and not popup_active:
                index = max(0, index - 1)
                status_message = ""
                app.layout.focus(row_windows[index])
                app.invalidate()
                return None
            if event_type == mouse_event_type.SCROLL_DOWN and not editing and not popup_active:
                index = min(row_count - 1, index + 1)
                status_message = ""
                app.layout.focus(row_windows[index])
                app.invalidate()
                return None
            return NotImplemented
        return _handler

    def render_row(row_index: int) -> list[Any]:
        rows = main_menu_rows(state)
        handler = menu_row_mouse_handler(row_index)
        if row_index == index:
            return [
                ("[SetCursorPosition]", ""),
                ("class:menu.selected", "  ▶ ", handler),
                ("class:menu.selected", rows[row_index], handler),
            ]
        return [("class:menu.item", "    " + rows[row_index], handler)]

    def make_edit_row(row_index: int) -> Any:
        label, suffix, _ = inline_fields[row_index]
        prefix_text = f"  ▶ {label}: ["
        return pt_vsplit([
            pt_window(
                width=len(prefix_text),
                content=pt_formatted_text_control([("class:inline.label", prefix_text)]),
                dont_extend_width=True,
            ),
            editor,
            pt_window(
                width=len(suffix) + 1,
                content=pt_formatted_text_control([("class:inline.label", suffix + "]")]),
                dont_extend_width=True,
            ),
        ], height=1)

    for row_index in range(row_count):
        control = pt_formatted_text_control(
            text=lambda row_index=row_index: render_row(row_index),
            focusable=True,
            show_cursor=False,
        )
        row_windows.append(pt_window(
            height=1,
            content=control,
            style="class:menu",
            wrap_lines=False,
            always_hide_cursor=True,
        ))

    def row_container(row_index: int) -> Any:
        if editing and edit_index == row_index and row_index in inline_fields:
            return make_edit_row(row_index)
        return row_windows[row_index]

    group_names = MAIN_MENU_GROUPS
    menu_children: list[Any] = []
    for row_index in range(row_count):
        group = group_names.get(row_index)
        if group:
            if menu_children:
                menu_children.append(_pt_Window(height=1, style="class:menu"))
            prefix = f"── {group} "
            menu_children.append(_pt_Window(
                height=1,
                content=_pt_FormattedTextControl([
                    ("class:menu.section", "  " + prefix + "─" * max(2, 54 - len(prefix)))
                ]),
                style="class:menu",
                wrap_lines=False,
            ))
        menu_children.append(pt_dynamic_container(lambda row_index=row_index: row_container(row_index)))

    menu_pane = pt_scrollable_pane(
        _pt_HSplit(menu_children),
        keep_cursor_visible=True,
        keep_focused_window_visible=True,
        show_scrollbar=True,
    )

    def render_detail() -> list[tuple[str, str]]:
        details = main_menu_details(state)
        fragments: list[tuple[str, str]] = [("class:panel.title", "  AKTUALNA KONFIGURACJA\n")]
        for line in _state_status_lines(state):
            fragments.append(("class:panel.label", "  " + line + "\n"))
        fragments.append(("class:panel.rule", "\n  " + "─" * max(8, _detail_panel_columns() - 4) + "\n"))
        fragments.append(("class:panel.title", "  WYBRANA OPCJA\n"))
        for line in _wrap(details[index], max(20, _detail_panel_columns() - 4), indent="  "):
            fragments.append(("class:panel.text", "  " + line.strip() + "\n"))
        if index in inline_fields and not editing and not popup_active:
            fragments.append(("class:panel.hint", "\n  Enter: edytuj pole w tym wierszu.\n"))
        elif index in {MENU_PROFILE, MENU_SIDECARS, MENU_FORMAT, MENU_COMPRESSION, MENU_FORCE} and not editing and not popup_active:
            fragments.append(("class:panel.hint", "\n  Enter: otwórz okno wyboru.\n"))
        if status_message:
            style_name = "class:panel.error" if status_message.startswith("BŁĄD:") else "class:panel.hint"
            fragments.append((style_name, "\n  " + status_message + "\n"))
        return fragments

    def render_footer() -> list[tuple[str, str]]:
        if popup_active:
            return [
                ("class:footer.key", " ↑/↓ "), ("class:footer.text", "wybór  "),
                ("class:footer.key", " Mysz "), ("class:footer.text", "wskaż/kliknij  "),
                ("class:footer.key", " Enter "), ("class:footer.text", "zastosuj  "),
                ("class:footer.key", " Esc/Q/PPM "), ("class:footer.text", "anuluj "),
            ]
        if editing:
            return [
                ("class:footer.key", " Enter "), ("class:footer.text", "zapisz  "),
                ("class:footer.key", " Ctrl+A "), ("class:footer.text", "wyczyść  "),
                ("class:footer.key", " Esc "), ("class:footer.text", "anuluj  "),
                ("class:footer.key", " Tab "), ("class:footer.text", "uzupełnij ścieżkę "),
            ]
        return [
            ("class:footer.key", " ↑/↓ "), ("class:footer.text", "wybór  "),
            ("class:footer.key", " Mysz "), ("class:footer.text", "wskaż/kliknij/przewiń  "),
            ("class:footer.key", " PgUp/PgDn "), ("class:footer.text", "strona  "),
            ("class:footer.key", " Enter "), ("class:footer.text", "edytuj/wybierz/otwórz  "),
            ("class:footer.key", " Esc/PPM "), ("class:footer.text", "wstecz/wyjście  "),
            ("class:footer.key", " Ctrl+X "), ("class:footer.text", "bez zapisu "),
        ]

    def popup_width() -> int:
        terminal_width = shutil.get_terminal_size((100, 30)).columns
        available = max(32, terminal_width - 8)
        title_width = len(popup_title) + 6
        label_width = max((len(label) for label in popup_labels), default=24) + 8
        detail_width = min(
            max((len(line) for detail in popup_details for line in str(detail).splitlines()), default=36) + 4,
            72,
        )
        return min(max(38, title_width, label_width, detail_width), available, 76)

    def popup_detail_height() -> int:
        if not popup_details:
            return 2
        detail = popup_details[popup_index] if popup_index < len(popup_details) else ""
        return max(2, min(5, len(_wrap(detail, max(24, popup_width() - 4)))))

    def popup_height() -> int:
        terminal_height = shutil.get_terminal_size((100, 30)).lines
        desired = max(2, len(popup_labels)) + popup_detail_height() + 6
        return min(max(10, desired), max(10, terminal_height - 4))

    def popup_row_mouse_handler(row_index: int):
        def _handler(mouse_event: Any) -> object:
            nonlocal popup_index
            app = pt_get_app()
            event_type = mouse_event.event_type
            if (
                event_type == mouse_event_type.MOUSE_UP
                and mouse_event.button == mouse_button.RIGHT
            ):
                close_option_popup(app, cancelled=True)
                return None
            if event_type == mouse_event_type.MOUSE_MOVE:
                if popup_index != row_index:
                    popup_index = row_index
                    app.invalidate()
                return None
            if event_type == mouse_event_type.MOUSE_UP:
                popup_index = row_index
                app.invalidate()
                apply_option_popup(app)
                return None
            if event_type == mouse_event_type.SCROLL_UP:
                popup_index = (popup_index - 1) % max(1, len(popup_labels))
                app.invalidate()
                return None
            if event_type == mouse_event_type.SCROLL_DOWN:
                popup_index = (popup_index + 1) % max(1, len(popup_labels))
                app.invalidate()
                return None
            return NotImplemented
        return _handler

    def render_popup_rows() -> list[Any]:
        fragments: list[Any] = []
        for number, label in enumerate(popup_labels):
            if number == popup_index:
                fragments.append(("[SetCursorPosition]", ""))
                fragments.append(("class:popup.selected", f"  ▶ {label}\n", popup_row_mouse_handler(number)))
            else:
                fragments.append(("class:popup.item", f"    {label}\n", popup_row_mouse_handler(number)))
        return fragments

    def render_popup_detail() -> list[tuple[str, str]]:
        detail = popup_details[popup_index] if popup_index < len(popup_details) else ""
        fragments: list[tuple[str, str]] = []
        for line in _wrap(detail, max(24, popup_width() - 4), indent="  "):
            fragments.append(("class:popup.detail", "  " + line.strip() + "\n"))
        return fragments

    popup_menu_control = _pt_FormattedTextControl(
        text=render_popup_rows,
        focusable=True,
        show_cursor=False,
    )
    popup_detail_control = _pt_FormattedTextControl(
        text=render_popup_detail,
        focusable=False,
        show_cursor=False,
    )
    popup_menu_window = _pt_Window(
        content=popup_menu_control,
        height=lambda: max(2, len(popup_labels)),
        style="class:popup",
        wrap_lines=False,
        always_hide_cursor=True,
    )

    popup_frame = _pt_HSplit([
        _pt_Window(height=1, char="─", style="class:popup.border"),
        _pt_Window(
            height=2,
            content=_pt_FormattedTextControl(
                text=lambda: [
                    ("class:popup.title", f"  {popup_title}\n"),
                    ("class:popup.subtitle", "  Wybierz wartość i zatwierdź Enterem."),
                ]
            ),
            style="class:popup",
            wrap_lines=False,
        ),
        _pt_Window(height=1, char="─", style="class:popup.border"),
        popup_menu_window,
        _pt_Window(height=1, char="─", style="class:popup.border"),
        _pt_Window(
            content=popup_detail_control,
            height=popup_detail_height,
            style="class:popup",
            wrap_lines=True,
        ),
        _pt_Window(height=1, char="─", style="class:popup.border"),
    ], style="class:popup", modal=True)
    popup_visible = _pt_Condition(lambda: popup_active)
    popup_layer = _pt_ConditionalContainer(
        content=popup_frame,
        filter=popup_visible,
    )

    header_control = _pt_FormattedTextControl(text=render_header, focusable=False, show_cursor=False)
    detail_control = _pt_FormattedTextControl(text=render_detail, focusable=False, show_cursor=False)
    footer_control = _pt_FormattedTextControl(text=render_footer, focusable=False, show_cursor=False)

    def focus_index(event: Any, target: int) -> None:
        nonlocal index, status_message
        if editing or popup_active:
            return
        index = max(0, min(row_count - 1, target))
        status_message = ""
        event.app.layout.focus(row_windows[index])
        event.app.invalidate()

    def popup_move(event: Any, delta: int) -> None:
        nonlocal popup_index
        if not popup_labels:
            return
        popup_index = (popup_index + delta) % len(popup_labels)
        event.app.invalidate()

    @bindings.add("up")
    def _up(event: Any) -> None:
        if popup_active:
            popup_move(event, -1)
        elif not editing:
            focus_index(event, index - 1)

    @bindings.add("k")
    def _key_k(event: Any) -> None:
        if popup_active:
            popup_move(event, -1)
        elif editing:
            editor.buffer.insert_text("k")
        else:
            focus_index(event, index - 1)

    @bindings.add("down")
    def _down(event: Any) -> None:
        if popup_active:
            popup_move(event, 1)
        elif not editing:
            focus_index(event, index + 1)

    @bindings.add("j")
    def _key_j(event: Any) -> None:
        if popup_active:
            popup_move(event, 1)
        elif editing:
            editor.buffer.insert_text("j")
        else:
            focus_index(event, index + 1)

    @bindings.add("pageup")
    def _page_up(event: Any) -> None:
        if popup_active:
            popup_move(event, -1)
        else:
            focus_index(event, index - 8)

    @bindings.add("pagedown")
    def _page_down(event: Any) -> None:
        if popup_active:
            popup_move(event, 1)
        else:
            focus_index(event, index + 8)

    @bindings.add("home")
    def _home(event: Any) -> None:
        nonlocal popup_index
        if popup_active:
            popup_index = 0
            event.app.invalidate()
        elif editing:
            editor.buffer.cursor_position = 0
        else:
            focus_index(event, 0)

    @bindings.add("end")
    def _end(event: Any) -> None:
        nonlocal popup_index
        if popup_active:
            popup_index = max(0, len(popup_labels) - 1)
            event.app.invalidate()
        elif editing:
            editor.buffer.cursor_position = len(editor.buffer.text)
        else:
            focus_index(event, row_count - 1)

    @bindings.add("c-a", eager=True)
    def _clear_or_home(event: Any) -> None:
        if popup_active:
            return
        if editing:
            editor.buffer.text = ""
        else:
            focus_index(event, 0)

    @bindings.add("enter", eager=True)
    def _enter(event: Any) -> None:
        if popup_active:
            apply_option_popup(event.app)
            return
        if editing:
            finish_edit(event.app, editor.buffer.text)
            return
        if index in inline_fields:
            start_edit(event.app, index)
            return
        if index in {MENU_PROFILE, MENU_SIDECARS, MENU_FORMAT, MENU_COMPRESSION, MENU_FORCE}:
            open_option_popup(event.app, index)
            return
        event.app.exit(result=index)

    @bindings.add("escape", eager=True)
    def _escape(event: Any) -> None:
        if popup_active:
            close_option_popup(event.app, cancelled=True)
        elif editing:
            cancel_edit(event.app)
        else:
            event.app.exit(result=None)

    @bindings.add("q", eager=True)
    def _q(event: Any) -> None:
        if popup_active:
            close_option_popup(event.app, cancelled=True)
        elif editing:
            editor.buffer.insert_text("q")
        else:
            event.app.exit(result=None)

    @bindings.add("c-x", eager=True)
    def _exit(event: Any) -> None:
        event.app.exit(result=-2)

    style = _pt_Style.from_dict({
        "root": "bg:ansiblack fg:ansiwhite",
        "header": "bg:ansiblack",
        "header.title": "ansibrightcyan bold",
        "header.subtitle": "ansibrightblack",
        "border": "ansicyan",
        "menu": "bg:ansiblack",
        "menu.item": "ansiwhite",
        "menu.section": "ansibrightblack bold",
        "menu.selected": "bg:ansicyan fg:ansiblack bold",
        "inline.label": "bg:ansicyan fg:ansiblack bold",
        "inline.input": "bg:ansiwhite fg:ansiblack",
        "panel": "bg:ansiblack",
        "panel.title": "ansibrightcyan bold",
        "panel.label": "ansibrightblack",
        "panel.rule": "ansicyan",
        "panel.text": "ansiwhite",
        "panel.hint": "ansibrightgreen",
        "panel.error": "ansibrightred bold",
        "popup": "bg:ansiwhite fg:ansiblack",
        "popup.border": "bg:ansiwhite fg:ansicyan bold",
        "popup.title": "bg:ansiwhite fg:ansicyan bold",
        "popup.subtitle": "bg:ansiwhite fg:ansibrightblack",
        "popup.item": "bg:ansiwhite fg:ansiblack",
        "popup.selected": "bg:ansicyan fg:ansiblack bold",
        "popup.detail": "bg:ansiwhite fg:ansiblack",
        "footer": "bg:ansiblack",
        "footer.key": "bg:ansicyan fg:ansiblack bold",
        "footer.text": "ansibrightblack",
    })

    base_layout = _pt_HSplit([
        _pt_Window(height=2, content=header_control, style="class:header", wrap_lines=False),
        _pt_Window(height=1, char="─", style="class:border"),
        _pt_VSplit([
            menu_pane,
            _pt_Window(width=1, char="│", style="class:border"),
            _pt_Window(
                content=detail_control,
                width=lambda: pt_dimension.exact(_detail_panel_columns()),
                style="class:panel",
                wrap_lines=True,
                always_hide_cursor=True,
            ),
        ], padding=1, padding_char=" "),
        _pt_Window(height=1, char="─", style="class:border"),
        _pt_Window(height=1, content=footer_control, style="class:footer", wrap_lines=False),
    ])

    root_container = _pt_FloatContainer(
        content=base_layout,
        floats=[
            _pt_Float(
                width=popup_width,
                height=popup_height,
                content=popup_layer,
            )
        ],
    )

    layout = _pt_Layout(root_container, focused_element=row_windows[index])
    app = _pt_Application(
        layout=layout,
        key_bindings=bindings,
        style=style,
        full_screen=True,
        erase_when_done=True,
        mouse_support=True,
    )
    result = app.run()
    if result == -2:
        raise UserRequestedExit()
    return result, index

def _state_status_lines(state: InteractiveState) -> list[str]:
    return [
        f"Profil      {PROFILE_UI_LABELS.get(state.profile, state.profile)}",
        f"Format      {state.archive_format}",
        f"Limit       {state.part_size_mb} MiB",
        f"Kompresja   {state.compression_level}",
        f"Sidecary    {'TAK' if state.sidecars else 'NIE'}",
        f"Force       {'TAK' if state.force else 'NIE'}",
        f"Zmiany      {'NIEZAPISANE' if state.dirty else 'zapisane'}",
    ]


def _text_menu_group(index: int) -> str | None:
    return MAIN_MENU_GROUPS.get(index)


def render_text_main_menu(state: InteractiveState, rows: Sequence[str]) -> None:
    ui_banner(
        f"Jaźń / Łatka — generator paczek v{GENERATOR_VERSION}",
        "Tryb tekstowy • jeden kanoniczny plan • CRC i SHA-256",
    )
    ui_key_value("Profil / format", f"{state.profile} / {state.archive_format}")
    ui_key_value("Źródło", _display_path(state.source))
    ui_key_value("Wyjście", _display_path(state.out_dir))
    ui_key_value("Nazwa bazowa", state.archive_basename)
    ui_key_value("Stan ustawień", "niezapisane zmiany" if state.dirty else "zapisane")
    for index, row in enumerate(rows):
        group = _text_menu_group(index)
        if group:
            if index != MENU_PACK:
                print()
            ui_section(group)
        accent = index in {MENU_PACK, MENU_PLAN, MENU_VERIFY, MENU_EXTRACT, MENU_SELF_TEST, MENU_EXIT}
        number = _paint(f"{index + 1:>2}.", ANSI_BRIGHT_CYAN if accent else ANSI_BRIGHT_BLACK, ANSI_BOLD if accent else "")
        print(f"  {number} {row}")
    print("\n" + _paint("  Esc/Q = wyjście  •  Ctrl+X = wyjście bez zapisu", ANSI_BRIGHT_BLACK))


def handle_menu_choice(state: InteractiveState, choice: int) -> bool:
    if choice == MENU_PACK:
        pack_from_interactive(state)
    elif choice == MENU_PLAN:
        show_plan_interactive(state)
    elif choice == MENU_NAME:
        state.archive_basename = sanitize_archive_stem(
            ask_text_value("Bazowa nazwa", state.archive_basename, ui_mode=state.ui_mode)
        )
        state.dirty = True
    elif choice == MENU_PROFILE:
        labels = ["dual — system i pamięć osobno", "system", "memory", "combined"]
        details = [
            "Dwie niezależne paczki: statyczny system i osobna pamięć.",
            "Kod i pliki statyczne bez memory/ i workspace_runtime/.",
            "Tylko memory/, w tym SQLite, ale bez WAL/SHM i archiwów.",
            "System i pamięć w jednej paczce.",
        ]
        values = ["dual", "system", "memory", "combined"]
        selected_new = choose_value("Profil", labels, current=values.index(state.profile), ui_mode=state.ui_mode, details=details)
        if selected_new is not None:
            state.profile = values[selected_new]
            state.dirty = True
    elif choice == MENU_SOURCE:
        state.source = Path(ask_text_value("Folder źródłowy", _display_path(state.source), ui_mode=state.ui_mode, path_mode=True)).expanduser().resolve()
        state.dirty = True
    elif choice == MENU_OUTPUT:
        state.out_dir = Path(ask_text_value("Folder wyjściowy", _display_path(state.out_dir), ui_mode=state.ui_mode, path_mode=True)).expanduser().resolve()
        state.dirty = True
    elif choice == MENU_SIDECARS:
        state.sidecars = not state.sidecars
        state.dirty = True
    elif choice == MENU_FORMAT:
        labels = ["auto", "independent — samodzielne ZIP-y", "binary — .zip.001/.002"]
        details = [
            "Independent, chyba że pojedynczy plik przekracza limit części.",
            "Każdy wolumin jest pełnym ZIP-em i można go otworzyć osobno.",
            "Jeden logiczny ZIP dzielony bajtowo; przed rozpakowaniem trzeba połączyć części.",
        ]
        values = ["auto", "independent", "binary"]
        selected_new = choose_value("Format", labels, current=values.index(state.archive_format), ui_mode=state.ui_mode, details=details)
        if selected_new is not None:
            state.archive_format = values[selected_new]
            state.dirty = True
    elif choice == MENU_LIMIT:
        state.part_size_mb = ask_int_value("Limit części MiB", state.part_size_mb, 1, 1024 * 1024, ui_mode=state.ui_mode)
        state.dirty = True
    elif choice == MENU_COMPRESSION:
        selected_new = choose_value(
            "Poziom kompresji",
            COMPRESSION_UI_LABELS,
            current=COMPRESSION_CHOICES.index(state.compression_level),
            ui_mode=state.ui_mode,
            details=COMPRESSION_UI_DETAILS,
        )
        if selected_new is not None:
            state.compression_level = COMPRESSION_CHOICES[selected_new]
            state.dirty = True
    elif choice == MENU_FORCE:
        state.force = not state.force
        state.dirty = True
    elif choice == MENU_INTERFACE:
        edit_interface(state)
    elif choice == MENU_EXCLUDES:
        edit_excludes(state)
    elif choice == MENU_SAVE:
        ui_message(state.ui_mode, f"Zapisano ustawienia: {save_interactive_state(state)}", "ok")
    elif choice == MENU_VERIFY:
        verify_interactive(state)
    elif choice == MENU_EXTRACT:
        extract_interactive(state)
    elif choice == MENU_SELF_TEST:
        compression_self_test_interactive()
    elif choice == MENU_EXIT:
        return False
    return True


def startup_ui_choice(state: InteractiveState, ui_override: str | None = None) -> str:
    if ui_override:
        mode = _normalize_ui_mode(ui_override)
        if ui_override.strip().lower() in {"kursorowy", "cursor", "kursor"} and mode != "kursorowy":
            raise PackError("Tryb kursorowy wymaga biblioteki prompt_toolkit.")
        return mode
    if state.ui_auto_start:
        return _normalize_ui_mode(state.ui_mode)
    labels = ["Kursorowy — pełnoekranowy panel", "Tekstowy — kolorowe menu"]
    details = [
        "Najwygodniejszy w Windows Terminal: LPM, PPM=wstecz, scroll, strzałki, Enter, Esc/Q i Ctrl+X.",
        "Działa bez prompt_toolkit; zachowuje ramki, grupy i czytelne statusy.",
    ]
    if HAS_PROMPT_TOOLKIT:
        choice = cursor_select(
            f"Jaźń / Łatka — v{GENERATOR_VERSION}",
            labels,
            0,
            details=details,
            subtitle="Wybierz interfejs",
            status_lines=["Silnik      canonical plan", "Weryfikacja CRC + SHA-256", "Wygląd      polished cyan"],
        )
        return "kursorowy" if choice == 0 else "tekstowy"

    ui_banner(f"Jaźń / Łatka — generator paczek v{GENERATOR_VERSION}")
    ui_status("Tryb kursorowy jest niedostępny: brak prompt_toolkit.", "warn")
    print("  Instalacja: py -m pip install prompt_toolkit")
    print("  Uruchamiam tryb tekstowy.")
    return "tekstowy"


def cursor_exit_summary(state: InteractiveState) -> bool:
    """Pokazuje wyjście w trybie kursorowym; True oznacza zakończenie programu."""

    active_base = sum(1 for item in state.base_excludes if item.enabled)
    manual = "OFF"
    if state.custom_excludes:
        manual = "ON" if state.custom_excludes_enabled else "OFF"
    status = [
        f"Źródło      {_ellipsize(_display_path(state.source), 42)}",
        f"Wyjście     {_ellipsize(_display_path(state.out_dir), 42)}",
        f"Profil      {PROFILE_UI_LABELS.get(state.profile, state.profile)}",
        f"Format      {state.archive_format}",
        f"Wykluczenia podstawowe {active_base}/{len(state.base_excludes)}",
        f"Wykluczenia ręczne     {len(state.custom_excludes)} {manual}",
        f"Ustawienia  {'NIEZAPISANE ZMIANY' if state.dirty else 'zapisane'}",
    ]
    if state.dirty:
        labels = ["Zapisz ustawienia i wyjdź", "Wyjdź bez zapisu", "Wróć do programu"]
        details = [
            "Zapisz bieżącą konfigurację atomowo obok skryptu i zakończ.",
            "Zakończ program, pozostawiając ostatnio zapisany plik konfiguracji bez zmian.",
            "Anuluj wyjście i wróć do strony głównej.",
        ]
    else:
        labels = ["Wyjdź", "Wróć do programu"]
        details = [
            "Zakończ program. Wszystkie ustawienia są już zapisane.",
            "Anuluj wyjście i wróć do strony głównej.",
        ]
    choice = cursor_select(
        "Wyjście — podsumowanie",
        labels,
        0,
        details=details,
        status_lines=status,
        subtitle="Sprawdź konfigurację przed zamknięciem",
    )
    if choice is None:
        return False
    if state.dirty:
        if choice == 0:
            save_interactive_state(state)
            return True
        if choice == 1:
            return True
        return False
    return choice == 0


def interactive(ui_override: str | None = None) -> int:
    state = load_interactive_state()
    selected = 0
    try:
        state.ui_mode = startup_ui_choice(state, ui_override)
        while True:
            rows = main_menu_rows(state)
            if state.ui_mode == "kursorowy":
                choice, selected = cursor_main_menu(state, selected)
                if choice is None or choice == MENU_EXIT:
                    if cursor_exit_summary(state):
                        return 0
                    continue
            else:
                render_text_main_menu(state, rows)
                raw = input(_paint("Wybór: ", ANSI_BRIGHT_CYAN, ANSI_BOLD)).strip()
                _control_result(raw)
                try:
                    choice = int(raw) - 1
                except ValueError:
                    continue
                if not 0 <= choice < len(rows):
                    continue
            try:
                if not handle_menu_choice(state, choice):
                    break
            except UserCancelledInput:
                continue
            except PackError as exc:
                ui_message(state.ui_mode, str(exc), "error")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                ui_message(state.ui_mode, str(exc), "error")
    except UserRequestedExit:
        ui_status("Zamknięto bez automatycznego zapisu ustawień.", "warn")
        return 0

    if state.ui_mode == "tekstowy" and state.dirty:
        try:
            if ask_yes_no("Zapisać zmienione ustawienia", True, ui_mode=state.ui_mode):
                ui_status(f"Zapisano ustawienia: {save_interactive_state(state)}", "ok")
        except (UserCancelledInput, UserRequestedExit):
            pass
    return 0


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
        if result.logical_zip_sha256:
            ui_key_value("ZIP SHA-256", result.logical_zip_sha256)
        if result.archive_format == "binary":
            ui_status(
                "Części .zip.001/.002 nie są samodzielnymi ZIP-ami. Użyj pliku .join.ps1 "
                "albo polecenia extract/verify z plikiem .package.json.",
                "warn",
            )
        print(_paint("  Pliki wynikowe:", ANSI_BRIGHT_CYAN, ANSI_BOLD))
        for path in result.committed_paths:
            print(f"    {_paint('✓', ANSI_BRIGHT_GREEN)} {path}")
    print()
    ui_status("Paczka jest gotowa do przeniesienia, weryfikacji lub rozpakowania.", "ok")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Jaźń / Łatka — kanoniczny generator paczek ZIP")
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
    pack.add_argument("--no-sidecars", action="store_true")

    plan = sub.add_parser("plan", help="Pokaż kanoniczny plan bez pakowania")
    plan.add_argument("source", type=Path)
    plan.add_argument("--profile", choices=("system", "memory", "combined"), default="system")
    plan.add_argument("--exclude", action="append", default=[])
    plan.add_argument("--files", action="store_true")
    plan.add_argument("--json", type=Path)

    verify = sub.add_parser("verify", help="Zweryfikuj paczkę na podstawie *.package.json")
    verify.add_argument("sidecar", type=Path)

    extract = sub.add_parser("extract", help="Zweryfikuj i bezpiecznie rozpakuj paczkę")
    extract.add_argument("sidecar", type=Path)
    extract.add_argument("destination", type=Path)
    extract.add_argument("--force", action="store_true")
    extract.add_argument("--clean", action="store_true")

    sub.add_parser("self-test", help="Przetestuj kompresję independent i binary")
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
        reset_interactive_settings()
        print(f"Usunięto ustawienia: {settings_path()}")
        return 0
    # Łagodna kompatybilność: pierwszy argument będący ścieżką oznacza pack.
    if argv[0] not in {"pack", "plan", "verify", "extract", "self-test", "-h", "--help", "--version"}:
        argv.insert(0, "pack")
    args = parser().parse_args(argv)

    if args.command == "pack":
        if args.part_size_mb <= 0:
            raise PackError("--part-size-mb musi być > 0")
        if not 0 <= args.compresslevel <= 9:
            raise PackError("--compresslevel musi być w zakresie 0-9")
        out_dir = args.out or (args.source.expanduser().resolve().parent / "packages")
        options = PackOptions(
            source=args.source,
            out_dir=out_dir,
            profile=args.profile,
            archive_format=args.archive_format,
            archive_basename=args.name,
            part_size_mb=args.part_size_mb,
            compression_level=args.compresslevel,
            force=args.force,
            custom_excludes=list(args.exclude),
            custom_excludes_enabled=bool(args.exclude),
            sidecars=not args.no_sidecars,
        )
        results = run_pack(options)
        print_results(results)
        return 0

    if args.command == "plan":
        plan = build_plan(args.source, args.profile, args.exclude)
        print_plan(plan, show_files=args.files)
        if args.json:
            payload = {
                "generator_version": GENERATOR_VERSION,
                "profile": plan.profile,
                "version": plan.version.full_version,
                "scan_method": plan.scan_method,
                "manifest_builder": plan.manifest_builder,
                "plan_sha256": plan.plan_sha256(),
                "file_count": plan.file_count,
                "total_size_bytes": plan.total_size,
                "entries": [
                    {
                        "path": item.relative,
                        "size_bytes": item.size_bytes,
                        "sha256": item.sha256,
                        "classification": item.classification,
                    }
                    for item in plan.entries
                ],
                "excluded": [{"path": p, "reason": r} for p, r in plan.excluded],
            }
            args.json.write_bytes(serialize_json(payload))
        return 0

    if args.command == "verify":
        print(json.dumps(verify_package_sidecar(args.sidecar), ensure_ascii=False, indent=2))
        return 0

    if args.command == "extract":
        print(json.dumps(
            extract_package_sidecar(args.sidecar, args.destination, clean=args.clean, force=args.force),
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    if args.command == "self-test":
        print(json.dumps(run_compression_self_test(), ensure_ascii=False, indent=2))
        return 0

    parser().print_help()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(_paint("\nPrzerwano przez użytkownika.", ANSI_YELLOW, stream=sys.stderr), file=sys.stderr)
        raise SystemExit(130)
    except PackError as exc:
        print(_paint(f"BŁĄD: {exc}", ANSI_RED, ANSI_BOLD, stream=sys.stderr), file=sys.stderr)
        raise SystemExit(2)