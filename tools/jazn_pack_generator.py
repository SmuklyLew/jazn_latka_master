#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jaźń / Łatka — generator paczek v2.2.CANONICAL-PLAN-CURSOR-UI-POLISHED-FINAL

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
    from prompt_toolkit.completion import PathCompleter as _pt_PathCompleter
    from prompt_toolkit.key_binding import KeyBindings as _pt_KeyBindings
    from prompt_toolkit.layout import Layout as _pt_Layout
    from prompt_toolkit.layout.containers import HSplit as _pt_HSplit
    from prompt_toolkit.layout.containers import VSplit as _pt_VSplit
    from prompt_toolkit.layout.containers import Window as _pt_Window
    from prompt_toolkit.layout.controls import FormattedTextControl as _pt_FormattedTextControl
    from prompt_toolkit.styles import Style as _pt_Style
    HAS_PROMPT_TOOLKIT = True
except Exception:  # pragma: no cover
    _pt_prompt = None
    _pt_Application = None
    _pt_PathCompleter = None
    _pt_KeyBindings = None
    _pt_Layout = None
    _pt_HSplit = None
    _pt_VSplit = None
    _pt_Window = None
    _pt_FormattedTextControl = None
    _pt_Style = None
    HAS_PROMPT_TOOLKIT = False

GENERATOR_VERSION = "2.2.2.WINDOWS-ACL-ZIP-PUBLISH-FIX"
CHUNK_SIZE = 1024 * 1024
DEFAULT_PART_SIZE_MB = 400
DEFAULT_COMPRESSION_LEVEL = 6
DEFAULT_PROFILE = "dual"
DEFAULT_FORMAT = "auto"

SETTINGS_FILE_NAME = "__jazn_pack_generator_settings.json"
SETTINGS_SCHEMA = "jazn_pack_generator_settings/v4"
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
    custom_excludes: list[str] = field(default_factory=list)
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
    custom_excludes: list[str] = field(default_factory=list)
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
            custom_excludes=list(self.custom_excludes),
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
    """Normalizuje opcjonalny sufiks ``PACKAGE_RELEASE_NAME``."""

    release = str(value or "").strip().strip('"\'')
    if not release:
        return ""
    if any(ch in release for ch in '\\/:*?"<>|'):
        raise ValueError(
            f"PACKAGE_RELEASE_NAME zawiera znaki niedozwolone w nazwie pliku: {release!r}"
        )
    release = re.sub(r"\s+", "-", release)
    return release.strip("-_.")


def compose_package_version_full(
    package_version: str,
    package_release_name: str | None = None,
) -> str:
    """Zwraca pełną wersję bez dublowania sufiksu wydania.

    Wynik nie ma początkowego ``v``. To zachowuje kontrakt nazw plików
    generatora oraz zgodność z testami release-version.
    """

    version = normalize_version(package_version)
    release = normalize_release_name(package_release_name)
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
    """Porównuje wersję manifestu z kanoniczną pełną wersją pakietu."""

    return normalize_version(manifest_version) == compose_package_version_full(
        package_version,
        package_release_name,
    )


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
    filename_version = compose_package_version_full(package_version, release)
    full = f"v{filename_version}" if package_version.lower().startswith("v") else filename_version
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
    release = normalize_release_name(values.get("PACKAGE_RELEASE_NAME", ""))
    full = compose_package_version_full(base, release)
    return f"v{full}" if base.lower().startswith("v") else full


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
    relative = normalize_rel(relative)
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    name = parts[-1]

    if any(part in COMMON_FORBIDDEN_DIR_NAMES for part in parts[:-1]):
        return "common_forbidden_directory"
    if name in SECRET_EXACT_NAMES and name != ".env.example":
        return "secret_file"
    if any(token in name for token in SECRET_NAME_TOKENS):
        return "secret_name"
    if name.endswith(TRANSIENT_DATABASE_SUFFIXES):
        return "transient_database_file"
    if ".zip." in name:
        return "split_or_nested_archive"
    if any(name.endswith(suffix) for suffix in COMMON_FORBIDDEN_SUFFIXES):
        return "archive_or_generated_artifact"
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
    custom_excludes: Iterable[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    selected: list[str] = []
    excluded: list[tuple[str, str]] = []
    for relative in candidates:
        custom = matches_custom_exclude(relative, custom_excludes)
        if custom is not None:
            excluded.append((relative, f"custom:{custom}"))
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


def build_plan(root: Path, profile: str, custom_excludes: Sequence[str]) -> PackPlan:
    root = root.expanduser().resolve()
    if profile not in {"system", "memory", "combined"}:
        raise PackError(f"Niepoprawny profil planu: {profile}")
    version = read_version_info(root)

    if profile == "system":
        system_candidates, system_scan_method = discover_candidates(root)
        selected, excluded = filter_candidates(
            system_candidates,
            profile="system",
            custom_excludes=custom_excludes,
        )
        return build_system_plan(root, version, selected, excluded, system_scan_method)

    if profile == "memory":
        memory_candidates, memory_scan_method = discover_memory_candidates(root)
        selected, excluded = filter_candidates(
            memory_candidates,
            profile="memory",
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
        custom_excludes=custom_excludes,
    )
    memory_selected, memory_excluded = filter_candidates(
        memory_candidates,
        profile="memory",
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


def build_plans_for_options(options: PackOptions) -> list[PackPlan]:
    """Buduje plan tylko raz. Wynik może zostać pokazany i bezpośrednio spakowany."""

    source = options.source.expanduser().resolve()
    out_dir = options.out_dir.expanduser().resolve()
    ensure_output_outside_source(source, out_dir)
    if options.profile == "dual":
        plans = [build_plan(source, "system", options.custom_excludes)]
        try:
            plans.append(build_plan(source, "memory", options.custom_excludes))
        except PackError as exc:
            print(f"UWAGA: pomijam paczkę pamięci: {exc}")
        return plans
    return [build_plan(source, options.profile, options.custom_excludes)]


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
    script = Path(__file__).resolve()
    for candidate in (script.parent.parent, Path.cwd()):
        if (candidate / "latka_jazn" / "version.py").is_file():
            return candidate.resolve()
    return Path.cwd().resolve()


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
    return InteractiveState(source=source, out_dir=source.parent / "packages")


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
    custom = payload.get("custom_excludes") or payload.get("exclude") or []
    if isinstance(custom, list):
        state.custom_excludes = [str(item).strip() for item in custom if str(item).strip()]
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


def ask_text_value(label: str, current: str, *, ui_mode: str, path_mode: bool = False) -> str:
    if ui_mode == "kursorowy" and HAS_PROMPT_TOOLKIT and _pt_prompt is not None:
        kwargs: dict[str, Any] = {
            "default": current,
            "key_bindings": _prompt_key_bindings(),
            "rprompt": "Esc wróć  •  Ctrl+X wyjdź bez zapisu  •  Enter zatwierdź",
            "wrap_lines": False,
            "style": _prompt_style(),
        }
        if path_mode and _pt_PathCompleter is not None:
            kwargs["completer"] = _pt_PathCompleter(only_directories=True, expanduser=True)
            kwargs["complete_while_typing"] = False
        value = str(_pt_prompt([("class:prompt", f"{label}: ")], **kwargs)).strip()
        if value == UI_CANCEL_MARKER:
            raise UserCancelledInput()
        if value == UI_EXIT_MARKER:
            raise UserRequestedExit()
        return value or current
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

    def render_menu() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        for number, row in enumerate(rows):
            section_name = groups.get(number)
            if section_name:
                if number:
                    fragments.append(("class:menu.section", "\n"))
                fragments.append(("class:menu.section", f"  {section_name}\n"))
            if number == index:
                fragments.append(("[SetCursorPosition]", ""))
                fragments.append(("class:menu.selected", "  ▶ "))
                fragments.append(("class:menu.selected", row + "\n"))
            else:
                fragments.append(("class:menu.item", "    " + row + "\n"))
        return fragments

    def render_detail() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = [("class:panel.title", "  AKTUALNA KONFIGURACJA\n")]
        for line in status_lines:
            fragments.append(("class:panel.label", "  " + line + "\n"))
        fragments.append(("class:panel.rule", "\n  " + "─" * 34 + "\n"))
        fragments.append(("class:panel.title", "  WYBRANA OPCJA\n"))
        detail = details[index] if index < len(details) else ""
        for line in _wrap(detail or rows[index], 42, indent="  "):
            fragments.append(("class:panel.text", "  " + line.strip() + "\n"))
        return fragments

    def render_footer() -> list[tuple[str, str]]:
        return [
            ("class:footer.key", " ↑/↓ "), ("class:footer.text", "wybór  "),
            ("class:footer.key", " Enter "), ("class:footer.text", "zatwierdź  "),
            ("class:footer.key", " Esc/Q "), ("class:footer.text", "wróć  "),
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
                _pt_Window(content=detail_control, style="class:panel", wrap_lines=True, always_hide_cursor=True),
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
        mouse_support=False,
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


def build_preview_plans(state: InteractiveState) -> list[PackPlan]:
    ui_status("Buduję kanoniczny plan i obliczam SHA-256 plików…", "info")
    options = state.to_options()
    plans = build_plans_for_options(options)
    for plan in plans:
        print_plan(plan, show_files=False)
    return plans


def pack_from_interactive(state: InteractiveState) -> None:
    options = state.to_options()
    plans = build_preview_plans(state)
    preview_hashes = {plan.profile: plan.plan_sha256() for plan in plans}
    if not ask_yes_no("Rozpocząć pakowanie dokładnie tego planu", False, ui_mode=state.ui_mode, explicit=True):
        ui_status("Pakowanie anulowane.", "warn")
        return
    ui_status("Plan zatwierdzony. Pakuję bez ponownego skanowania.", "info")
    results = run_pack_with_plans(options, plans)
    for result in results:
        if result.plan.plan_sha256() != preview_hashes[result.profile]:
            raise PackError(f"Hash planu zmienił się dla profilu {result.profile}.")
    print_results(results)


def show_plan_interactive(state: InteractiveState) -> None:
    plans = build_preview_plans(state)
    show_files = ask_yes_no("Pokazać pełną listę plików", False, ui_mode=state.ui_mode)
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


def edit_excludes(state: InteractiveState) -> None:
    current = "; ".join(state.custom_excludes)
    raw = ask_text_value("Wykluczenia rozdzielone średnikami", current, ui_mode=state.ui_mode)
    state.custom_excludes = [item.strip() for item in raw.split(";") if item.strip()]
    state.dirty = True


def edit_interface(state: InteractiveState) -> None:
    labels = [
        "Tekstowy — kolorowe, pogrupowane menu",
        "Kursorowy — pełnoekranowy panel" + ("" if HAS_PROMPT_TOOLKIT else " — niedostępny: brak prompt_toolkit"),
        f"Automatyczny start zapisanym trybem: {'TAK' if state.ui_auto_start else 'NIE'}",
    ]
    details = [
        "Działa bez dodatkowych bibliotek. Kolory można wyłączyć przez NO_COLOR=1.",
        "Strzałki, Enter, Esc/Q, Ctrl+X, panel szczegółów i stały pasek skrótów.",
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


def main_menu_rows(state: InteractiveState) -> list[str]:
    excludes = f"{len(state.custom_excludes)} wzorców" if state.custom_excludes else "brak"
    return [
        "Pakuj teraz",
        "Pokaż kanoniczny plan",
        f"Źródło: {state.source}",
        f"Wyjście: {state.out_dir}",
        f"Profil: {state.profile}",
        f"Format: {state.archive_format}",
        f"Nazwa: {state.archive_basename}",
        f"Limit: {state.part_size_mb} MiB",
        f"Kompresja: {state.compression_level}",
        f"Nadpisywanie: {'TAK' if state.force else 'NIE'}",
        f"Pliki pomocnicze: {'TAK' if state.sidecars else 'NIE'}",
        f"Wykluczenia: {excludes}",
        f"Interfejs: {state.ui_mode}",
        "Zapisz ustawienia",
        "Zweryfikuj istniejącą paczkę",
        "Bezpiecznie rozpakuj paczkę",
        "Wyjdź",
    ]


def main_menu_details(state: InteractiveState) -> list[str]:
    return [
        "Zbuduj podgląd, zatwierdź zamrożony plan i utwórz paczkę z pełną weryfikacją.",
        "Pokaż liczbę plików, rozmiar, źródło skanowania, manifest i Plan SHA-256.",
        "Root projektu/runtime. Musi zawierać latka_jazn/version.py.",
        "Folder wynikowy musi leżeć poza katalogiem źródłowym.",
        "dual: system i memory osobno; system; memory; combined.",
        "auto wybiera independent, a przy zbyt dużym pliku binary .zip.001.",
        "Bazowa nazwa. Numer i release-name zostaną pobrane z version.py.",
        "Maksymalny rozmiar woluminu. Pojedynczy większy plik może wymusić binary.",
        "Poziom DEFLATE 0–9. Wartość 6 jest dobrym kompromisem.",
        "Gdy wyłączone, istniejąca paczka o tej samej nazwie nie zostanie ruszona.",
        "Twórz package.json, parts.sha256 oraz join.ps1 dla formatu binary.",
        "Dodatkowe wzorce użytkownika ponad centralną politykę bezpieczeństwa.",
        "Wybór trybu tekstowego/kursorowego i automatycznego startu.",
        "Zapisz konfigurację atomowo obok skryptu.",
        "Sprawdź sidecar, SHA-256, CRC, kompletność oraz wersję bez rozpakowywania.",
        "Najpierw zweryfikuj, potem rozpakuj z ochroną przed path traversal.",
        "Zakończ. Przy zmianach generator zapyta, czy zapisać ustawienia.",
    ]


def _state_status_lines(state: InteractiveState) -> list[str]:
    return [
        f"Profil      {state.profile}",
        f"Format      {state.archive_format}",
        f"Limit       {state.part_size_mb} MiB",
        f"Kompresja   {state.compression_level}",
        f"Sidecary    {'TAK' if state.sidecars else 'NIE'}",
        f"Force       {'TAK' if state.force else 'NIE'}",
        f"Zmiany      {'NIEZAPISANE' if state.dirty else 'zapisane'}",
    ]


def _text_menu_group(index: int) -> str | None:
    return {
        0: "GŁÓWNE",
        2: "KONFIGURACJA PACZKI",
        13: "USTAWIENIA I NARZĘDZIA",
        16: "WYJŚCIE",
    }.get(index)


def render_text_main_menu(state: InteractiveState, rows: Sequence[str]) -> None:
    ui_banner(
        f"Jaźń / Łatka — generator paczek {GENERATOR_VERSION}",
        "Tryb tekstowy • jeden kanoniczny plan • CRC i SHA-256",
    )
    ui_key_value("Profil / format", f"{state.profile} / {state.archive_format}")
    ui_key_value("Źródło", state.source)
    ui_key_value("Wyjście", state.out_dir)
    ui_key_value("Nazwa bazowa", state.archive_basename)
    ui_key_value("Stan ustawień", "niezapisane zmiany" if state.dirty else "zapisane")
    for index, row in enumerate(rows):
        group = _text_menu_group(index)
        if group:
            ui_section(group)
        accent = index in {0, 1, 13, 14, 15, 16}
        number = _paint(f"{index + 1:>2}.", ANSI_BRIGHT_CYAN if accent else ANSI_BRIGHT_BLACK, ANSI_BOLD if accent else "")
        print(f"  {number} {row}")
    print("\n" + _paint("  Esc/Q = wyjście  •  Ctrl+X = wyjście bez zapisu", ANSI_BRIGHT_BLACK))


def handle_menu_choice(state: InteractiveState, choice: int) -> bool:
    if choice == 0:
        pack_from_interactive(state)
    elif choice == 1:
        show_plan_interactive(state)
    elif choice == 2:
        state.source = Path(ask_text_value("Folder źródłowy", str(state.source), ui_mode=state.ui_mode, path_mode=True)).expanduser().resolve()
        state.dirty = True
    elif choice == 3:
        state.out_dir = Path(ask_text_value("Folder wyjściowy", str(state.out_dir), ui_mode=state.ui_mode, path_mode=True)).expanduser().resolve()
        state.dirty = True
    elif choice == 4:
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
    elif choice == 5:
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
    elif choice == 6:
        state.archive_basename = sanitize_archive_stem(
            ask_text_value("Bazowa nazwa", state.archive_basename, ui_mode=state.ui_mode)
        )
        state.dirty = True
    elif choice == 7:
        state.part_size_mb = ask_int_value("Limit części MiB", state.part_size_mb, 1, 1024 * 1024, ui_mode=state.ui_mode)
        state.dirty = True
    elif choice == 8:
        state.compression_level = ask_int_value("Kompresja", state.compression_level, 0, 9, ui_mode=state.ui_mode)
        state.dirty = True
    elif choice == 9:
        state.force = not state.force
        state.dirty = True
    elif choice == 10:
        state.sidecars = not state.sidecars
        state.dirty = True
    elif choice == 11:
        edit_excludes(state)
    elif choice == 12:
        edit_interface(state)
    elif choice == 13:
        ui_status(f"Zapisano ustawienia: {save_interactive_state(state)}", "ok")
    elif choice == 14:
        verify_interactive(state)
    elif choice == 15:
        extract_interactive(state)
    elif choice == 16:
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
        "Najwygodniejszy w Windows Terminal: strzałki, Enter, Esc/Q i Ctrl+X.",
        "Działa bez prompt_toolkit; zachowuje ramki, grupy i czytelne statusy.",
    ]
    if HAS_PROMPT_TOOLKIT:
        choice = cursor_select(
            f"Jaźń / Łatka — {GENERATOR_VERSION}",
            labels,
            0,
            details=details,
            subtitle="Wybierz interfejs",
            status_lines=["Silnik      canonical plan", "Weryfikacja CRC + SHA-256", "Wygląd      polished cyan"],
        )
        return "kursorowy" if choice == 0 else "tekstowy"

    ui_banner(f"Jaźń / Łatka — generator paczek {GENERATOR_VERSION}")
    ui_status("Tryb kursorowy jest niedostępny: brak prompt_toolkit.", "warn")
    print("  Instalacja: py -m pip install prompt_toolkit")
    print("  Uruchamiam tryb tekstowy.")
    return "tekstowy"


def interactive(ui_override: str | None = None) -> int:
    state = load_interactive_state()
    selected = 0
    try:
        state.ui_mode = startup_ui_choice(state, ui_override)
        while True:
            rows = main_menu_rows(state)
            if state.ui_mode == "kursorowy":
                choice = cursor_select(
                    f"Jaźń / Łatka — {GENERATOR_VERSION}",
                    rows,
                    selected,
                    details=main_menu_details(state),
                    status_lines=_state_status_lines(state),
                    subtitle="Kanoniczny plan pakowania",
                    groups={0: "GŁÓWNE", 2: "KONFIGURACJA", 13: "NARZĘDZIA", 16: "WYJŚCIE"},
                )
                if choice is None:
                    break
                selected = choice
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
                ui_status(str(exc), "error")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                ui_status(str(exc), "error")
    except UserRequestedExit:
        ui_status("Zamknięto bez automatycznego zapisu ustawień.", "warn")
        return 0

    if state.dirty:
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
    if argv[0] not in {"pack", "plan", "verify", "extract", "-h", "--help", "--version"}:
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
