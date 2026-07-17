#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
_jazn_pack_generator.py

Interaktywna wersja skryptu pakującego Jaźń / Łatkę.

Najważniejsze zasady działania:
- uruchomienie bez argumentów otwiera menu interaktywne,
- dostępne są dwa tryby interfejsu: tekstowy oraz kursorowy,
- automatyczny start oznacza użycie zapisanego trybu TXT/Kursorowy z ustawień bez pytania,
- opcje wymagające folderu źródłowego prowadzą krok po kroku do brakujących ustawień zamiast kończyć się błędem,
- tryb kursorowy można wymusić argumentem --ui kursorowy albo zapamiętać w ustawieniach,
- tryb kursorowy używa zwartego menu terminalowego z wyborem ↑/↓, Enter i ESC jako powrotem,
- Ctrl+X zamyka aplikację bez zapisu przez obsłużony wyjątek, Ctrl+C nie jest skrótem aplikacji, a ESC nigdy nie zamyka całej aplikacji,
- można ustawić folder źródłowy, folder zapisu, nazwę ZIP, profil, ustawienia paczki i wykluczenia,
- w trybie tekstowym i kursorowym edycja folderu źródłowego i folderu zapisu otwiera pole edycji z tekstem startowym,
- jeśli prompt_toolkit jest dostępny, pola ścieżek mają Tab/autouzupełnianie także w trybie tekstowym,
- folder źródłowy bez ustawień startuje od folderu generatora, a folder zapisu od folderu generatora\pakiet\,
- folder źródłowy musi być rootem projektu/runtime Jaźni i zawierać latka_jazn/version.py,
- ręcznie wpisana nazwa ZIP jest używana dokładnie jako nazwa użytkownika: spacje są zamieniane na podkreślenia, a .zip jest dodawane tylko gdy go brakuje,
- skrypt pokazuje podgląd listy pakowania; ta sama lista jest później używana do pakowania,
- można włączać/wyłączać listy wykluczeń oraz dodawać, edytować i usuwać manualne wzorce,
- tabela domyślnych wykluczeń w trybie kursorowym ma własny przewijany widok,
- w trybie kursorowym folder źródłowy, folder zapisu, nazwa ZIP oraz ustawienia paczki edytują się bezpośrednio w nawiasach [] menu,
- menu Ustawienia ma grupy i separatory zgodne z kolejnością użytkownika,
- zmiana interfejsu pozostaje w podmenu interfejsu aż do ręcznego powrotu,
- końcowe potwierdzenia pakowania wymagają jawnego T/Tak albo N/Nie; sam Enter nic nie zatwierdza,
- domyślny profil zapisuje system i pamięć jako dwa niezależne zestawy zwykłych archiwów ZIP,
- gdy paczka mieści się w ustawionym limicie, powstaje wyłącznie `<nazwa>.zip`, bez sztucznej części `.001`,
- gdy paczka przekracza limit, pliki są dzielone na niezależne, kompletne woluminy ZIP: `<nazwa>.zip`, `<nazwa>.part002.zip`, `<nazwa>.part003.zip`, ...,
- każdy wolumin jest samodzielnym poprawnym ZIP-em i można go otworzyć w Eksploratorze plików Windows, 7-Zipie lub innym programie ZIP,
- podział odbywa się na granicach plików; pojedynczy plik większy od limitu tworzy jeden większy wolumin zamiast uszkodzonego fragmentu,
- profile „sam system” i „sama pamięć” zapisują po jednym zestawie ZIP-ów,
- domyślnie w folderze wyjściowym pozostają tylko właściwe pliki `.zip`; pliki diagnostyczne są opcjonalne,
- po pakowaniu generator sprawdza SHA-256, bezpieczne ścieżki, central directory oraz pełny CRC każdego woluminu,
- stary tryb CLI nadal działa; testowanie dawnych binarnych części `.zip.001` pozostaje obsługiwane jako kompatybilność legacy.

Nowy format NIE jest wielodyskowym ZIP-em `.z01/.z02/.zip` ani binarnym cięciem jednego ZIP-a.
To zestaw niezależnych archiwów ZIP, dzięki czemu każdy plik wyjściowy można otworzyć bez wcześniejszego sklejania.
Opcjonalne pliki diagnostyczne (`--diagnostic-files`) zawierają manifesty, SHA-256 i helper rozpakowania całego zestawu.
"""

from __future__ import annotations

VERSION = "1.6.INTEGRITY-MANIFEST-GATE"

import argparse
import ast
import bisect
import atexit
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import time
import textwrap
import zipfile
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Iterable, cast

# =============================================================================
# OPCJONALNE BIBLIOTEKI UI
# =============================================================================
# Skrypt ma dwa tryby interfejsu: tekstowy oraz kursorowy.
# Tryb tekstowy działa bez dodatkowych bibliotek. Tryb kursorowy wymaga prompt_toolkit.
# Osobna flaga ustawień może automatycznie używać zapisanego trybu przy starcie.
try:  # pragma: no cover - zależne od środowiska użytkownika
    from prompt_toolkit import prompt as _pt_prompt
    from prompt_toolkit.application import Application as _pt_Application
    from prompt_toolkit.completion import PathCompleter as _pt_PathCompleter
    from prompt_toolkit.key_binding import KeyBindings as _pt_KeyBindings
    from prompt_toolkit.layout import Layout as _pt_Layout
    from prompt_toolkit.layout.containers import Window as _pt_Window
    from prompt_toolkit.layout.controls import FormattedTextControl as _pt_FormattedTextControl
    from prompt_toolkit.shortcuts import CompleteStyle as _pt_CompleteStyle
    from prompt_toolkit.styles import Style as _pt_Style
    HAS_PROMPT_TOOLKIT = True
except Exception:  # pragma: no cover
    _pt_prompt = None
    _pt_Application = None
    _pt_PathCompleter = None
    _pt_KeyBindings = None
    _pt_Layout = None
    _pt_Window = None
    _pt_FormattedTextControl = None
    _pt_CompleteStyle = None
    _pt_Style = None
    HAS_PROMPT_TOOLKIT = False

# Aktywny tryb UI dla helperów promptów.
# Ważne: zwykły tryb tekstowy NIE udaje obsługi ESC/Tab/Ctrl+X, bo input()
# nie potrafi niezawodnie łapać tych klawiszy w każdym terminalu.
ACTIVE_UI_MODE = "plain"
_PROCESS_LOCK_PATH: Path | None = None

# =============================================================================
# USTAWIENIA DOMYŚLNE
# =============================================================================

SOURCE_FOLDER = r""
OUTPUT_DIR = r""
ARCHIVE_BASENAME = r"jazn_latka"
PART_SIZE_MB = 480
COMPRESSION_LEVEL = 6
FORCE_OVERWRITE = False
INCLUDE_EMPTY_DIRS = True
APPEND_VERSION_TO_NAME = True
VERSION_FILE = r""
VERSION_VARIABLES = ("PACKAGE_VERSION", "__version__", "VERSION")
RELEASE_NAME_VARIABLES = ("PACKAGE_RELEASE_NAME",)
PACKAGE_RELEASE_NAME = r""
CHUNK_SIZE = 1024 * 1024

# Domyślnie generator zapisuje wyłącznie właściwe archiwa ZIP.
# Jedna paczka mieści się w `<nazwa>.zip`; większa tworzy kolejne niezależne
# woluminy `<nazwa>.part002.zip`, `.part003.zip`, ... . Dodatkowe manifesty, sumy i helpery
# można włączyć jawnie w CLI przez `--diagnostic-files`.
DEFAULT_ARTIFACT_MODE = "parts_only"
ARTIFACT_MODES = ("parts_only", "diagnostic")
VERIFY_AFTER_PACK = True
VERIFY_CRC_AFTER_PACK = True

# Plik z ustawieniami jest zapisywany obok tego skryptu.
SETTINGS_FILE_NAME = "__jazn_pack_generator_settings.json"
LEGACY_SETTINGS_FILE_NAME = ""
PROCESS_LOCK_FILE_NAME = "__jazn_pack_generator.lock.json"
PACKAGE_INTEGRITY_MANIFEST_NAME = "PACKAGE_INTEGRITY_MANIFEST.json"
LEGACY_PROCESS_LOCK_FILE_NAMES = ("_jazn_pack_generate.lock.json",)

# Bazowe bezpieczne wykluczenia. Profil "pełny" celowo NIE usuwa pamięci.
BASE_SAFE_EXCLUDE_PATTERNS: list[str] = [
    # Git / edytory / lokalny stan narzędzi
    ".git/",
    ".vscode/",
    ".codex/",
    ".venv/",
    ".archives/",
    "__jazn_pack_generator.lock.json",
    "__jazn_pack_generator_settings.json",
    "*.before.py",

    # Python / test / cache
    "__pycache__/",
    ".pytest_cache/",
    ".pytest-tmp/",
    ".mypy_cache/",
    ".ruff_cache/",
    "*.pyc",
    "*.pyo",

    # Tymczasowe i odrzucone pliki
    "*.tmp",
    "*.partial",
    "*.tmp_extract_part",
    "*.bak",
    "*.bad",
    "*.corrupt",
    "*.log",

    # Paczki, backupy, raporty i artefakty patchowania
    "exports/",
    "reports/",
    "backups/",
    "backups_git/",
    "patchs/",
    "*.patch",
    "*.rej",
    "*.orig",
    "*_PATCH_REPORT.md",
    "LATKA_*_COMMANDS.ps1",
    "v14_*_patch_bundle.zip",
    "v14_*_PATCH_FIXED_BUNDLE.zip",
    "v14_*_FULL_PATCH_AND_RECOVERY_BUNDLE.zip",

    # Runtime testowy / podglądy, ale nie główna pamięć
    "workspace_runtime/",
    "workspace_runtime/test_*.sqlite3",
    "runtime-preview-*.json",
    "memory/sql/runtime_write_v1/",

    "*.sqlite3-wal",
    "*.sqlite3-shm",
    "*.db-wal",
    "*.db-shm",

    #workspace_ephemeral

    "workspace_runtime/pytest_*/",
    "workspace_runtime/turn_checkpoints/",
    "workspace_runtime/runtime_sessions/",
    "workspace_runtime/local_untracked_backup/",
    "workspace_runtime/patch_direct_apply_backups/",
    "workspace_runtime/patch_sequence_reports/",
    "workspace_runtime/stash_recovered/",
    "workspace_runtime/codex_session_bridge*/requests/",
    "workspace_runtime/codex_session_bridge*/responses/",
    "workspace_runtime/codex_session_bridge*/processed/",
    "workspace_runtime/codex_session_bridge*/status/",
    "workspace_runtime/codex_session_bridge*/logs/",

    #Archiwa, manifesty i pliki diagnostyczne, które nie są wymagane do poprawności ZIP-a
    "/docs/_archive_mixed_files",
    "/docs/_archive_patch",
    "/docs/_archive_plans",
    "/docs/_archive_scripts",
    "/docs/_archive_tests",

    # Ręcznie bez segregacji
    "_archive_gen_pack/",
    "RUNTIME_STATE.json",
    "ACTIVE_RUNTIME_CACHE_CONTRACT.json",
    "BOOTSTRAP_JAZN_CURRENT.json",
    "processed/",
    "requests/",
    "responses/",
    "status/",
    "daemon-status.txt",
    "RUNTIME_STATE.json",
]

# Zostawiamy starą nazwę zmiennej dla kompatybilności funkcji/CLI.
EXCLUDE_PATTERNS: list[str] = list(BASE_SAFE_EXCLUDE_PATTERNS)

PACK_PROFILES: dict[str, dict[str, object]] = {
    "pelna": {
        "label": "System + pamięć — dwie oddzielne paczki ZIP",
        "short": "system + pamięć osobno",
        "description": "Tworzy dwa niezależne zestawy części: *_system.zip.001... z kodem/systemem oraz *_memory.zip.001... wyłącznie z głównego memory/. Domyślnie pozostają tylko zwykłe pliki ZIP; manifesty i SHA256 są opcjonalne.",
        "exclude_patterns": BASE_SAFE_EXCLUDE_PATTERNS,
        "include_prefixes": [],
    },
    "system": {
        "label": "Sam system — bez pamięci i workspace_runtime",
        "short": "tylko system",
        "description": "Kod, dokumentacja, testy i narzędzia bez katalogów memory/ oraz workspace_runtime/.",
        "exclude_patterns": BASE_SAFE_EXCLUDE_PATTERNS + [
            "/memory/",
            "/workspace_runtime/",
            "RUNTIME_STATE.json",
            "ACTIVE_RUNTIME_CACHE_CONTRACT.json",
            "BOOTSTRAP_JAZN_CURRENT.json",
        ],
        "include_prefixes": [],
    },
    "memory": {
        "label": "Sama pamięć — tylko memory/",
        "short": "tylko pamięć",
        "description": "Pakuje wyłącznie gałąź memory/. Przydatne do osobnej kopii pamięci i baz SQLite.",
        "exclude_patterns": BASE_SAFE_EXCLUDE_PATTERNS,
        "include_prefixes": ["memory/"],
    },
}
DEFAULT_PACK_PROFILE = "pelna"

# =============================================================================
# MODELE DANYCH
# =============================================================================


@dataclass(slots=True)
class PackPlan:
    files: list[Path]
    dirs: list[Path]
    source_total_size: int
    excluded: list[tuple[str, str]] = field(default_factory=list)  # (rel, pattern)
    generated_at: str = field(default_factory=lambda: now_iso())

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def dir_count(self) -> int:
        return len(self.dirs)


@dataclass(slots=True)
class WizardState:
    source_folder: Path | None = None
    out_dir: Path | None = None
    archive_name: str = ""
    archive_name_manual: bool = False
    archive_basename_requested: str = ARCHIVE_BASENAME
    part_size_mb: int = PART_SIZE_MB
    compression_level: int = COMPRESSION_LEVEL
    force: bool = FORCE_OVERWRITE
    include_empty_dirs: bool = INCLUDE_EMPTY_DIRS
    pack_profile: str = DEFAULT_PACK_PROFILE
    use_default_excludes: bool = True
    use_custom_excludes: bool = True
    custom_excludes: list[str] = field(default_factory=list)
    disabled_default_excludes: list[str] = field(default_factory=list)
    append_version_to_name: bool = APPEND_VERSION_TO_NAME
    version_file: str | Path | None = VERSION_FILE or None
    package_version: str = ""
    package_release_name: str = ""
    resolved_version_file: Path | None = None
    plan: PackPlan | None = None
    component_plans: dict[str, PackPlan] = field(default_factory=dict)
    settings_loaded_from: Path | None = None
    settings_last_saved_to: Path | None = None
    settings_needs_cleanup: bool = False
    startup_warnings: list[str] = field(default_factory=list)
    ui_mode: str = ""
    ui_auto_start: bool = False

    def profile(self) -> dict[str, object]:
        return PACK_PROFILES.get(self.pack_profile, PACK_PROFILES[DEFAULT_PACK_PROFILE])

    def profile_label(self) -> str:
        return str(self.profile().get("label") or self.pack_profile)

    def profile_default_excludes(self) -> list[str]:
        return as_str_list(self.profile().get("exclude_patterns"))

    def include_prefixes(self) -> list[str]:
        return as_str_list(self.profile().get("include_prefixes"))

    def effective_excludes(self) -> list[str]:
        patterns: list[str] = []
        if self.use_default_excludes:
            disabled = set(self.disabled_default_excludes)
            patterns.extend(p for p in self.profile_default_excludes() if p not in disabled)
        if self.use_custom_excludes:
            patterns.extend(self.custom_excludes)
        return patterns

    def active_default_excludes(self) -> list[str]:
        if not self.use_default_excludes:
            return []
        disabled = set(self.disabled_default_excludes)
        return [p for p in self.profile_default_excludes() if p not in disabled]

# =============================================================================
# POMOCNICZE
# =============================================================================


def human_size(num: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(num)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{num} B"


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def rel_posix(path: Path, root: Path) -> str:
    return PurePosixPath(path.relative_to(root).as_posix()).as_posix()


def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_zip_datetime(path: Path) -> tuple[int, int, int, int, int, int]:
    try:
        tm = time.localtime(path.stat().st_mtime)
        year = min(max(tm.tm_year, 1980), 2107)
        return (year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec)
    except Exception:
        return (1980, 1, 1, 0, 0, 0)


def sanitize_zip_name(name: str) -> str:
    raw = str(name or "").strip().strip('"')
    raw = re.sub(r"\s+", "_", raw)
    if not raw:
        raise ValueError("Nazwa ZIP nie może być pusta")
    if any(ch in raw for ch in '\\/:*?"<>|'):
        raise ValueError(f"Nazwa ZIP zawiera niedozwolone znaki: {raw!r}")
    if not raw.lower().endswith(".zip"):
        raw += ".zip"
    return raw


def print_bar(done: int, total: int, *, label: str = "Postęp", width: int = 30, end: str = "\r") -> None:
    total = max(total, 1)
    done = min(max(done, 0), total)
    ratio = done / total
    filled = int(round(width * ratio))
    bar = "█" * filled + "░" * (width - filled)
    percent = int(round(ratio * 100))
    sys.stdout.write(f"\r{label}: {bar} {percent:3d}% / 100%")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


def pause() -> None:
    try:
        input("\nEnter = dalej...")
    except EOFError:
        # Wejście może być zamknięte przy uruchomieniu z potoku/testu.
        return


PLAIN_CANCEL_WORDS = {"esc", "escape", "w", "wroc", "wróć", "anuluj", "cancel"}
PLAIN_EXIT_WORDS = {"^x", "ctrl+x", "ctrlx", "exit", "quit", "zamknij"}


def _plain_control_word(value: str) -> str:
    raw = str(value or "").strip().strip("\x00").lower()
    # Bezpiecznik po v4.19: markery prompt_toolkit nie mogą nigdy stać się
    # ścieżką ani zwykłym tekstem menu. Nawet jeśli terminal/wersja biblioteki
    # zwróci sam środek markera bez bajtów NUL, traktujemy to jako sterowanie.
    if "latka_exit_app" in raw:
        return "exit"
    if "latka_cancel_input" in raw:
        return "cancel"
    if raw in PLAIN_EXIT_WORDS or raw.startswith("^x") or raw.startswith("ctrl+x"):
        return "exit"
    if raw in PLAIN_CANCEL_WORDS:
        return "cancel"
    return ""


def ask_text(prompt: str, default: str | None = None) -> str:
    if default:
        value = input(f"{prompt} [{default}]: ").strip()
        return value if value else default
    return input(f"{prompt}: ").strip()


CANCEL_INPUT_MARKER = "\x00LATKA_CANCEL_INPUT\x00"
APP_EXIT_MARKER = "\x00LATKA_EXIT_APP\x00"


class UserCancelledInput(Exception):
    """Użytkownik nacisnął ESC w prompt_toolkit i chce wrócić do menu."""


class UserRequestedAppExit(Exception):
    """Użytkownik nacisnął Ctrl+X w trybie kursorowym i chce zamknąć aplikację."""


def cancel_key_bindings():
    """Key bindings dla prompt_toolkit: ESC wraca, Ctrl+X zamyka aplikację.

    Od v4.22 zamknięcie aplikacji jest tylko przez Ctrl+X. Ctrl+C nie jest
    skrótem aplikacji, więc w prompt_toolkit przechwytujemy go jako no-op,
    żeby nie zamykał przypadkowo kreatora w trybie kursorowym.
    """
    if not HAS_PROMPT_TOOLKIT or _pt_KeyBindings is None:
        return None
    KeyBindings = cast(Any, _pt_KeyBindings)
    kb = KeyBindings()

    @kb.add("escape", eager=True)
    def _escape(event: Any) -> None:
        event.app.exit(exception=UserCancelledInput())

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    return kb


def startup_ui_key_bindings():
    """Key bindings tylko dla ekranu wyboru interfejsu.

    Ctrl+X kończy start aplikacji bez tracebacka. Ctrl+C jest celowo
    ignorowane w trybie prompt_toolkit, bo od v4.22 skrótem aplikacji
    ma być wyłącznie Ctrl+X.
    """
    if not HAS_PROMPT_TOOLKIT or _pt_KeyBindings is None:
        return None
    KeyBindings = cast(Any, _pt_KeyBindings)
    kb = KeyBindings()

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    return kb

def ask_edit_text(prompt: str, default: str | None = None, *, bottom_toolbar: str | None = None) -> str:
    """Edycja pojedynczej linii z ESC = powrót, jeżeli prompt_toolkit jest dostępny."""
    if ACTIVE_UI_MODE == "cursor" and HAS_PROMPT_TOOLKIT and _pt_prompt is not None and sys.stdin.isatty():
        try:
            value = _pt_prompt(
                f"{prompt}: ",
                default=default or "",
                key_bindings=cancel_key_bindings(),
                rprompt=bottom_toolbar or "Esc wróć | Ctrl+X zamknij bez zapisu | Enter OK",
                wrap_lines=False,
            ).strip()
            if value == CANCEL_INPUT_MARKER:
                raise UserCancelledInput()
            if value == APP_EXIT_MARKER:
                raise UserRequestedAppExit()
            return value if value else (default or "")
        except UserCancelledInput:
            raise
        except UserRequestedAppExit:
            raise
        except Exception as exc:
            print(f"UWAGA: edycja terminalowa niedostępna ({exc}). Używam zwykłego wpisywania.")
    value = ask_text(prompt, default)
    control = _plain_control_word(value)
    if control == "exit":
        raise UserRequestedAppExit()
    if control == "cancel":
        raise UserCancelledInput()
    return value


def path_completion_roots() -> list[str]:
    """Katalogi bazowe dla podpowiedzi ścieżek względnych."""
    roots: list[str] = []
    for candidate in (Path.cwd(), Path.home()):
        try:
            if candidate.exists() and candidate.is_dir():
                roots.append(str(candidate))
        except OSError:
            pass

    if os.name == "nt":
        # PowerShell/Windows: dodaj istniejące dyski, żeby Tab podpowiadał także
        # po wpisaniu C:\, D:\ itd., a nie tylko względem bieżącego katalogu.
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            if os.path.isdir(root):
                roots.append(root)
    else:
        roots.append("/")

    seen: set[str] = set()
    unique: list[str] = []
    for root in roots:
        norm = os.path.normcase(os.path.abspath(os.path.expanduser(root)))
        if norm not in seen:
            seen.add(norm)
            unique.append(root)
    return unique


def path_prompt_hint(only_directories: bool = True) -> str:
    kind = "foldery" if only_directories else "pliki/foldery"
    if HAS_PROMPT_TOOLKIT and _pt_prompt is not None and _pt_PathCompleter is not None and sys.stdin.isatty():
        return f"Tab {kind} | Esc wróć | Ctrl+X zamknij bez zapisu | Enter OK"
    return "Enter zatwierdź | puste = propozycja | Ctrl+X zakończ"

def normalize_path_text(raw: str) -> str:
    """Czyści tekst ścieżki i blokuje niejednoznaczne ścieżki Windows typu D:folder."""
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        return ""
    text = os.path.expandvars(os.path.expanduser(text))
    if os.name == "nt" and re.match(r"^[A-Za-z]:(?![\\/])", text):
        raise ValueError(
            "Ścieżka z literą dysku musi mieć ukośnik po dwukropku, np. "
            "D:\\.AI\\jazn_latka_local. Wpis D:folder jest ścieżką zależną "
            "od bieżącego katalogu na tym dysku."
        )
    return text


def ask_path_text(prompt: str, default: str | None = None, *, only_directories: bool = True) -> str:
    """Pyta o ścieżkę z widocznym Tab/autouzupełnianiem, gdy to możliwe.

    Zasada v4.24:
    - menu tekstowe zostaje tekstowe, ale pole ścieżki może użyć prompt_toolkit,
      jeśli biblioteka jest już dostępna w środowisku,
    - dzięki temu Tab/autouzupełnianie działa także po wybraniu trybu tekstowego,
    - bez prompt_toolkit pokazujemy propozycję w nawiasie i Enter ją akceptuje,
    - literalnie wpisane ^X / ctrl+x / esc nie może zostać przyjęte jako nazwa folderu.
    """
    can_use_prompt_toolkit = (
        HAS_PROMPT_TOOLKIT
        and _pt_prompt is not None
        and _pt_PathCompleter is not None
        and sys.stdin.isatty()
    )
    if can_use_prompt_toolkit:
        try:
            completer = _pt_PathCompleter(
                only_directories=only_directories,
                expanduser=True,
                get_paths=path_completion_roots,
            )
            default_label = default or ""
            # W trybie kursorowym zostajemy przy prompt_toolkit: edycja ścieżki
            # jest w tym samym stylu pracy, z wartością w nawiasie [] i Tab.
            message = f"{prompt} [{default_label}] " if default_label else f"{prompt} [] "
            kwargs: dict[str, Any] = {
                "default": "",
                "completer": completer,
                "complete_while_typing": False,
                "complete_in_thread": False,
                "reserve_space_for_menu": 0,
                "key_bindings": cancel_key_bindings(),
                "rprompt": "Tab autouzupełnij | Esc wróć | Ctrl+X zamknij bez zapisu | Enter OK",
                "wrap_lines": False,
            }
            if _pt_CompleteStyle is not None:
                # Najbliżej zachowania terminalowego: Tab uzupełnia wspólny prefiks
                # zamiast od razu budować duże menu na pół ekranu.
                kwargs["complete_style"] = _pt_CompleteStyle.READLINE_LIKE
            value = _pt_prompt(message, **kwargs).strip()
            if value == CANCEL_INPUT_MARKER:
                raise UserCancelledInput()
            if value == APP_EXIT_MARKER:
                raise UserRequestedAppExit()
            control = _plain_control_word(value)
            if control == "exit":
                raise UserRequestedAppExit()
            if control == "cancel":
                raise UserCancelledInput()
            if not value and not default:
                raise UserCancelledInput()
            return normalize_path_text(value if value else (default or ""))
        except UserCancelledInput:
            raise
        except UserRequestedAppExit:
            raise
        except ValueError:
            raise
        except Exception as exc:
            if ACTIVE_UI_MODE == "cursor":
                print(f"UWAGA: edycja ścieżki w trybie kursorowym niedostępna ({exc}). Wracam bez zmiany.")
                raise UserCancelledInput()
            print(f"UWAGA: autouzupełnianie ścieżek niedostępne ({exc}). Używam zwykłego wpisywania.")
    label = prompt
    hint = path_prompt_hint(only_directories)
    if default:
        value = input(f"{label} [{default}] ({hint}): " ).strip()
        if not value:
            value = default
    else:
        value = input(f"{label} ({hint}): " ).strip()
        if not value:
            raise UserCancelledInput()
    control = _plain_control_word(value)
    if control == "exit":
        raise UserRequestedAppExit()
    if control == "cancel":
        raise UserCancelledInput()
    return normalize_path_text(value)


def ask_bool(prompt: str, default: bool = False, *, require_explicit: bool = False) -> bool:
    """Pyta o Tak/Nie.

    Domyślnie zachowuje dawną kompatybilność: pusty Enter akceptuje wartość
    domyślną. Gdy require_explicit=True, prompt nie ma domyślnej odpowiedzi:
    pusty Enter jest no-op i tylko ponawia pytanie, bez zatwierdzania Tak/Nie.
    """
    suffix = "T/N, Enter=nic" if require_explicit else ("T/n" if default else "t/N")
    yes_values = {"t", "tak", "y", "yes", "1", "true"}
    no_values = {"n", "nie", "no", "0", "false"}
    while True:
        value = input(f"{prompt} [{suffix}]: ").strip().lower()
        control = _plain_control_word(value)
        if control == "exit":
            raise UserRequestedAppExit()
        if control == "cancel":
            raise UserCancelledInput()
        if not value:
            if require_explicit:
                # Krytyczne potwierdzenia, np. start pakowania, nie mają
                # domyślnego Tak. Sam Enter nie pakuje, nie anuluje i nie
                # drukuje dodatkowych komunikatów — tylko wraca do tego samego pytania.
                continue
            return default
        if value in yes_values:
            return True
        if value in no_values:
            return False
        print("Wpisz T/Tak albo N/Nie.")

def ask_int(prompt: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    while True:
        raw = ask_text(prompt, str(default))
        try:
            value = int(raw)
        except ValueError:
            print("Podaj liczbę całkowitą.")
            continue
        if minimum is not None and value < minimum:
            print(f"Wartość musi być >= {minimum}.")
            continue
        if maximum is not None and value > maximum:
            print(f"Wartość musi być <= {maximum}.")
            continue
        return value


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def subsection(title: str) -> None:
    print("\n" + "-" * 78)
    print(f"  {title}")
    print("-" * 78)


def settings_path() -> Path:
    return Path(__file__).resolve().with_name(SETTINGS_FILE_NAME)


def legacy_settings_path() -> Path | None:
    if not LEGACY_SETTINGS_FILE_NAME:
        return None
    return Path(__file__).resolve().with_name(LEGACY_SETTINGS_FILE_NAME)


def _path_to_str(path: Path | str | None) -> str:
    return "" if path is None else str(path)


def script_directory() -> Path:
    """Folder, w którym znajduje się ten generator.

    Używany tylko jako tekst startowy w polu edycji ścieżki, gdy użytkownik
    nie ustawił jeszcze folderu źródłowego albo folderu zapisu. To nie jest
    automatyczne zaakceptowanie folderu do pakowania — źródło dalej musi
    przejść walidację latka_jazn/version.py.
    """
    return Path(__file__).resolve().parent


def path_text_for_edit(path: Path | str | None) -> str:
    """Zwraca czytelny tekst ścieżki do pola edycji.

    Dla katalogów dodaje separator na końcu, żeby dalsze wpisywanie i Tab
    zachowywały się bliżej PowerShellowego `cd .\\folder\\` niż pustego promptu.
    """
    if path is None or not str(path).strip():
        candidate = script_directory()
    else:
        candidate = Path(str(path)).expanduser()
    text = str(candidate)
    if text and not text.endswith(("/", "\\")):
        text += os.sep
    return text


def default_source_edit_path() -> str:
    """Propozycja startowa dla folderu do pakowania: folder aplikacji."""
    return path_text_for_edit(script_directory())


def default_output_edit_path() -> str:
    """Propozycja startowa dla folderu zapisu: folder aplikacji + katalog pakiet."""
    return path_text_for_edit(script_directory() / "pakiet")


def path_edit_default_path(current: Path | str | None, *, output_package_folder: bool = False) -> str:
    """Domyślna treść pola ścieżki w trybie tekstowym i kursorowym."""
    if current is not None and str(current).strip():
        return path_text_for_edit(current)
    if output_package_folder:
        return default_output_edit_path()
    return default_source_edit_path()


def cursor_edit_default_path(current: Path | str | None) -> str | None:
    """Alias kompatybilności dla starszych wywołań."""
    return path_edit_default_path(current)


def as_str_list(value: object) -> list[str]:
    """Bezpiecznie zamienia wartość z ustawień/profilu na listę tekstów."""
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def as_int(value: object, default: int) -> int:
    """Bezpiecznie odczytuje int z JSON/argparse bez mieszania typów dla Pylance."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def as_bool(value: object, default: bool) -> bool:
    """Bezpiecznie odczytuje bool z JSON/argparse."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"1", "true", "t", "tak", "yes", "y"}:
            return True
        if raw in {"0", "false", "f", "nie", "no", "n"}:
            return False
    return default


def prompt_toolkit_parts() -> tuple[Any, Any, Any, Any, Any, Any] | None:
    """Zwraca komponenty prompt_toolkit jako Any po pełnym sprawdzeniu None."""
    if not HAS_PROMPT_TOOLKIT or not sys.stdin.isatty():
        return None
    if (
        _pt_Application is None
        or _pt_KeyBindings is None
        or _pt_Layout is None
        or _pt_Window is None
        or _pt_FormattedTextControl is None
        or _pt_Style is None
    ):
        return None
    return (
        cast(Any, _pt_Application),
        cast(Any, _pt_KeyBindings),
        cast(Any, _pt_Layout),
        cast(Any, _pt_Window),
        cast(Any, _pt_FormattedTextControl),
        cast(Any, _pt_Style),
    )




def state_to_settings(state: WizardState) -> dict[str, object]:
    # Od v5.4 `auto` nie jest trzecim trybem UI. To osobna flaga startowa:
    # użyj zapisanego trybu tekstowy/kursorowy bez pytania.
    ui_mode = normalize_ui_mode(state.ui_mode or "plain")
    if ui_mode == "cursor":
        ui_mode_setting = "kursorowy"
    else:
        ui_mode_setting = "tekstowy"
    return {
        "schema_version": "jazn_pack_settings/v2",
        "saved_at": now_iso(),
        "script_version": VERSION,
        "source_folder": _path_to_str(state.source_folder),
        "output_dir": _path_to_str(state.out_dir),
        # A generated archive name is derived from latka_jazn/version.py on each run.
        # Persist only a genuinely manual override, never a stale resolved version.
        "archive_name": state.archive_name if state.archive_name_manual else "",
        "archive_name_manual": state.archive_name_manual,
        "archive_name_source": (
            "manual_override"
            if state.archive_name_manual
            else "generated_from_latka_jazn/version.py_at_runtime"
        ),
        "archive_basename_requested": state.archive_basename_requested,
        "part_size_mb": state.part_size_mb,
        "compression_level": state.compression_level,
        "force": state.force,
        "include_empty_dirs": state.include_empty_dirs,
        "pack_profile": state.pack_profile,
        "use_default_excludes": state.use_default_excludes,
        "use_custom_excludes": state.use_custom_excludes,
        "custom_excludes": list(state.custom_excludes),
        "disabled_default_excludes": list(state.disabled_default_excludes),
        "append_version_to_name": state.append_version_to_name,
        "version_file": _path_to_str(state.version_file),
        "ui_mode": ui_mode_setting,
        "ui_auto_start": bool(state.ui_auto_start),
    }

def apply_settings_to_state(state: WizardState, data: dict[str, object]) -> None:
    def _maybe_path(value: object) -> Path | None:
        text = str(value or "").strip()
        return Path(text).expanduser() if text else None

    source = _maybe_path(data.get("source_folder"))
    out = _maybe_path(data.get("output_dir"))
    if source is not None:
        state.source_folder = source
    if out is not None:
        state.out_dir = out
    stored_archive_name = str(data.get("archive_name") or "").strip()
    state.archive_name_manual = bool(data.get("archive_name_manual", state.archive_name_manual))
    if state.archive_name_manual:
        state.archive_name = stored_archive_name or state.archive_name
    else:
        # Ignore legacy generated names such as jazn_latka_vX.Y.Z.zip. The current
        # name is rebuilt from latka_jazn/version.py after the source is resolved.
        if stored_archive_name:
            state.settings_needs_cleanup = True
        state.archive_name = ""
    state.archive_basename_requested = str(data.get("archive_basename_requested") or state.archive_basename_requested)
    state.part_size_mb = as_int(data.get("part_size_mb"), state.part_size_mb)
    state.compression_level = as_int(data.get("compression_level"), state.compression_level)
    state.force = as_bool(data.get("force"), state.force)
    state.include_empty_dirs = as_bool(data.get("include_empty_dirs"), state.include_empty_dirs)
    profile = str(data.get("pack_profile") or state.pack_profile)
    state.pack_profile = profile if profile in PACK_PROFILES else DEFAULT_PACK_PROFILE
    state.use_default_excludes = as_bool(data.get("use_default_excludes"), state.use_default_excludes)
    state.use_custom_excludes = as_bool(data.get("use_custom_excludes"), state.use_custom_excludes)
    custom = data.get("custom_excludes")
    if isinstance(custom, list):
        state.custom_excludes = [str(x) for x in custom if str(x).strip()]
    disabled = data.get("disabled_default_excludes")
    if isinstance(disabled, list):
        state.disabled_default_excludes = [str(x) for x in disabled if str(x).strip()]
    state.append_version_to_name = as_bool(data.get("append_version_to_name"), state.append_version_to_name)
    version_file = str(data.get("version_file") or "").strip()
    state.version_file = version_file or None
    ui_mode_raw = str(data.get("ui_mode") or "").strip()
    if ui_mode_raw:
        normalized_ui = normalize_ui_mode(ui_mode_raw)
        if normalized_ui == "auto":
            # Migracja z v5.2/v5.3: stare `ui_mode=auto` oznaczało start bez pytania.
            # Teraz zapisujemy konkretny tryb, a automat jako oddzielną flagę.
            state.ui_mode = resolve_auto_ui_mode()
            state.ui_auto_start = True
        else:
            state.ui_mode = normalized_ui
    if "ui_auto_start" in data:
        state.ui_auto_start = as_bool(data.get("ui_auto_start"), state.ui_auto_start)
    elif state.settings_loaded_from is not None or ui_mode_raw:
        # Kompatybilność dla ustawień sprzed v5.4: zapisany tryb oznacza,
        # że aplikacja może startować od razu tym trybem.
        state.ui_auto_start = bool(ui_mode_raw)
    state.plan = None


def load_settings(state: WizardState) -> bool:
    # Najpierw nowa nazwa, potem stara nazwa jako migracja wsteczna.
    for path in (settings_path(), legacy_settings_path()):
        if path is None or not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                continue
            apply_settings_to_state(state, data)
            state.settings_loaded_from = path
            return True
        except Exception as exc:
            print(f"UWAGA: nie udało się wczytać ustawień z {path}: {exc}")
            return False
    return False


def save_settings(state: WizardState, *, quiet: bool = True) -> Path:
    path = settings_path()
    try:
        path.write_text(json.dumps(state_to_settings(state), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state.settings_last_saved_to = path
        if not quiet:
            print(f"Zapisano ustawienia: {path}")
    except OSError as exc:
        # Zapis ustawień jest wygodą, nie warunkiem pakowania.
        # Dzięki temu skrypt nie wywali się, gdy folder programu jest tylko do odczytu.
        if not quiet:
            print(f"UWAGA: nie udało się zapisać ustawień do {path}: {exc}")
    return path


def snapshot_settings_file() -> dict[str, tuple[bool, str]]:
    """Zapamiętuje stan plików ustawień z początku sesji.

    Obejmuje nową nazwę `__jazn_pack_generator_settings.json` oraz starą
    `_jazn_pack_legacy_disabled.json`, żeby wyjście bez zapisywania nie
    zostawiało przypadkowej migracji po sesji testowej.
    """
    snapshot: dict[str, tuple[bool, str]] = {}
    for path in (settings_path(), legacy_settings_path()):
        if path is None:
            continue
        try:
            if path.exists():
                snapshot[str(path)] = (True, path.read_text(encoding="utf-8-sig"))
            else:
                snapshot[str(path)] = (False, "")
        except OSError:
            snapshot[str(path)] = (False, "")
    return snapshot


def restore_settings_file(snapshot: dict[str, tuple[bool, str]]) -> None:
    """Przywraca pliki ustawień do stanu z początku sesji."""
    for path_text, (existed, content) in snapshot.items():
        path = Path(path_text)
        try:
            if existed:
                path.write_text(content, encoding="utf-8")
            elif path.exists():
                path.unlink()
        except OSError as exc:
            print(f"UWAGA: nie udało się przywrócić pliku ustawień {path}: {exc}")


def process_lock_path() -> Path:
    """Zwraca ścieżkę pliku blokady procesu obok aplikacji."""
    return Path(__file__).resolve().with_name(PROCESS_LOCK_FILE_NAME)

def legacy_process_lock_paths() -> list[Path]:
    """Stare locki z nazw wcześniejszych wersji, czyszczone przy starcie v4.16+."""
    return [Path(__file__).resolve().with_name(name) for name in LEGACY_PROCESS_LOCK_FILE_NAMES]


def _read_lock_file(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def cleanup_legacy_process_locks(*, prompt_user: bool = True) -> None:
    """Usuwa lub obsługuje locki po starych nazwach skryptu.

    v4.16 działa jako `_jazn_pack_generator.py` i używa
    `__jazn_pack_generator.lock.json`. Pliki `_jazn_pack_generate.lock.json`
    zostają tylko po przerwanych starszych wersjach. Jeżeli zapisany PID już nie
    żyje, lock jest usuwany. Jeżeli żyje i wygląda na starszy generator, pytamy
    o zamknięcie procesu, tak jak przy aktualnym locku.
    """
    for path in legacy_process_lock_paths():
        if not path.exists():
            continue
        lock = _read_lock_file(path)
        if not lock:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        old_pid = as_int(lock.get("pid"), 0)
        if not old_pid or old_pid == os.getpid() or not _pid_alive(old_pid):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        same_script = _looks_like_same_script_process(old_pid, lock)
        if not same_script:
            print(f"UWAGA: znaleziono stary lock {path}, ale nie potwierdzam, że PID {old_pid} to generator. Zostawiam bez zmian.")
            continue
        section("Wykryto poprzedni proces starego generatora")
        print(f"Poprzedni PID:              {old_pid}")
        print(f"Stary plik blokady:         {path}")
        close_it = False
        if prompt_user and sys.stdin.isatty():
            close_it = ask_bool("Zamknąć poprzedni proces starego generatora", True)
        else:
            print("Tryb nieinteraktywny: nie zamykam automatycznie poprzedniego procesu.")
        if close_it and _terminate_process_tree(old_pid):
            print("Zamknięto poprzedni proces starego generatora.")
            try:
                path.unlink()
            except OSError:
                pass
        elif close_it:
            print("UWAGA: nie udało się zamknąć poprzedniego procesu starego generatora.")



def _read_process_lock() -> dict[str, object] | None:
    path = process_lock_path()
    if not path.exists():
        return None
    return _read_lock_file(path)


def _write_process_lock() -> Path | None:
    global _PROCESS_LOCK_PATH
    path = process_lock_path()
    _PROCESS_LOCK_PATH = path
    data = {
        "schema_version": "jazn_pack_process_lock/v1",
        "created_at": now_iso(),
        "pid": os.getpid(),
        "script_path": str(Path(__file__).resolve()),
        "executable": sys.executable,
        "argv": list(sys.argv),
        "cwd": str(Path.cwd()),
    }
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
    except OSError as exc:
        print(f"UWAGA: nie udało się zapisać pliku blokady procesu {path}: {exc}")
        return None


def _release_process_lock(path: Path | None = None, pid: int | None = None) -> None:
    # W fazie atexit część globali modułu może być już czyszczona, dlatego
    # ścieżkę i PID przekazujemy jako argumenty przy rejestracji handlera.
    try:
        lock_path = path or _PROCESS_LOCK_PATH or process_lock_path()
        current_pid = int(pid or os.getpid())
        if not lock_path.exists():
            return
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8-sig"))
        except Exception:
            data = None
        stored_pid = as_int(data.get("pid"), 0) if isinstance(data, dict) else 0
        if stored_pid == current_pid:
            lock_path.unlink()
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = (result.stdout or "") + (result.stderr or "")
            return str(pid) in output and "INFO:" not in output.upper()
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _process_command_line(pid: int) -> str:
    if pid <= 0:
        return ""
    if os.name == "nt":
        commands = [
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\"; if ($p) {{ $p.CommandLine }}",
            ],
            ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
        ]
        for command in commands:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=5)
                text = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                if text:
                    return text
            except Exception:
                continue
        return ""

    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        if proc_cmdline.exists():
            raw = proc_cmdline.read_bytes()
            return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5)
        return (result.stdout or "").strip()
    except Exception:
        return ""


def _looks_like_same_script_process(pid: int, lock_data: dict[str, object]) -> bool:
    script_path = str(lock_data.get("script_path") or Path(__file__).resolve())
    script_name = Path(script_path).name.lower()
    command_line = _process_command_line(pid).lower()
    if not command_line:
        # Brak możliwości odczytu komendy = nie zabijamy automatycznie.
        return False
    return script_name in command_line or str(Path(script_path)).lower() in command_line


def _terminate_process_tree(pid: int, *, timeout_seconds: float = 5.0) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name == "nt":
        # Nie używamy /T domyślnie. /T zamyka też procesy potomne, co bywa
        # zbyt agresywne w PowerShell/Windows Terminal. Tu celem jest tylko
        # poprzedni PID zapisany w locku tego generatora.
        for command in (
            ["taskkill", "/PID", str(pid)],
            ["taskkill", "/PID", str(pid), "/F"],
        ):
            try:
                subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
            except Exception:
                pass
            time.sleep(0.3)
            if not _pid_alive(pid):
                return True
        return not _pid_alive(pid)

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not _pid_alive(pid)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.2)
    return not _pid_alive(pid)


def activate_process_guard(*, prompt_user: bool = True) -> None:
    """Pilnuje, żeby nie zostawały żywe poprzednie procesy tej aplikacji.

    Mechanizm działa tylko dla procesu zapisanego w pliku lock tej aplikacji.
    Nie zamyka przypadkowych procesów: jeśli nie da się potwierdzić, że PID należy
    do tego samego skryptu, zostawia go w spokoju i tylko czyści stary lock.
    """
    cleanup_legacy_process_locks(prompt_user=prompt_user)
    lock = _read_process_lock()
    path = process_lock_path()
    if lock:
        old_pid = as_int(lock.get("pid"), 0)

        if old_pid and old_pid != os.getpid() and _pid_alive(old_pid):
            same_script = _looks_like_same_script_process(old_pid, lock)
            if same_script:
                section("Wykryto poprzedni proces generatora")
                print(f"Poprzedni PID:              {old_pid}")
                print(f"Plik blokady:               {path}")
                print("Poprzednie uruchomienie wygląda na ten sam skrypt.")
                close_it = False
                if prompt_user and sys.stdin.isatty():
                    close_it = ask_bool("Zamknąć poprzedni proces generatora", True)
                else:
                    print("Tryb nieinteraktywny: nie zamykam automatycznie poprzedniego procesu.")
                if close_it:
                    if _terminate_process_tree(old_pid):
                        print("Zamknięto poprzedni proces generatora.")
                    else:
                        print("UWAGA: nie udało się zamknąć poprzedniego procesu generatora.")
                else:
                    print("Kontynuuję bez zamykania poprzedniego procesu.")
            else:
                print(f"UWAGA: znaleziono lock z PID {old_pid}, ale nie potwierdzam, że to ten skrypt. Czyszczę lock bez zamykania procesu.")
        elif path.exists():
            # Stary lock po normalnym/crashowym zamknięciu.
            try:
                path.unlink()
            except OSError:
                pass

    lock_path_written = _write_process_lock()
    if lock_path_written is not None:
        atexit.register(_release_process_lock, lock_path_written, os.getpid())

# =============================================================================
# WERSJA / NAZWA PACZKI
# =============================================================================


def _literal_string_from_assignment(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def normalize_version(value: str) -> str:
    version = str(value).strip().strip('"\'')
    version = re.sub(r"^v", "", version, flags=re.IGNORECASE)
    if not version:
        raise ValueError("Wersja z version.py jest pusta")
    if any(ch in version for ch in '\\/?:*"<>|'):
        raise ValueError(f"Wersja zawiera znaki niedozwolone w nazwie pliku: {version!r}")
    return version


def normalize_release_name(value: str | None) -> str:
    release = str(value or "").strip().strip('"\'')
    if not release:
        return ""
    if any(ch in release for ch in '\\/>:<*?"|'):
        raise ValueError(f"PACKAGE_RELEASE_NAME zawiera znaki niedozwolone w nazwie pliku: {release!r}")
    release = re.sub(r"\s+", "-", release)
    release = release.strip("-_.")
    return release


def read_version_from_py(version_file: Path, variable_names: Iterable[str] = VERSION_VARIABLES) -> str:
    version_file = version_file.resolve()
    if not version_file.exists() or not version_file.is_file():
        raise FileNotFoundError(f"Nie znaleziono pliku wersji: {version_file}")
    text = version_file.read_text(encoding="utf-8-sig")
    tree = ast.parse(text, filename=str(version_file))
    wanted = set(variable_names)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = _literal_string_from_assignment(node.value)
            if value is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    return normalize_version(value)
        elif isinstance(node, ast.AnnAssign):
            value = _literal_string_from_assignment(node.value) if node.value is not None else None
            if value is None:
                continue
            target = node.target
            if isinstance(target, ast.Name) and target.id in wanted:
                return normalize_version(value)
    raise ValueError(f"Nie znaleziono zmiennej wersji {sorted(wanted)} w pliku: {version_file}")


def read_optional_string_from_py(version_file: Path, variable_names: Iterable[str]) -> str:
    version_file = version_file.resolve()
    if not version_file.exists() or not version_file.is_file():
        return ""
    text = version_file.read_text(encoding="utf-8-sig")
    tree = ast.parse(text, filename=str(version_file))
    wanted = set(variable_names)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = _literal_string_from_assignment(node.value)
            if value is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    return value.strip().strip('"\'')
        elif isinstance(node, ast.AnnAssign):
            value = _literal_string_from_assignment(node.value) if node.value is not None else None
            if value is None:
                continue
            target = node.target
            if isinstance(target, ast.Name) and target.id in wanted:
                return value.strip().strip('"\'')
    return ""


def default_version_file_for_source(source_folder: Path) -> Path:
    """Domyślny plik wersji Jaźni względem rootu pakowanego folderu."""
    return source_folder.resolve() / "latka_jazn" / "version.py"


def find_version_file(source_folder: Path, explicit_version_file: str | Path | None = None) -> Path:
    """Znajduje plik version.py dla pakowanego folderu.

    Domyślnie akceptujemy tylko root runtime Jaźni, czyli folder zawierający
    `latka_jazn/version.py`. Nie szukamy już w cwd ani obok skryptu, bo to mogło
    fałszywie zaakceptować niewłaściwy katalog do spakowania.
    """
    source_folder = source_folder.resolve()
    if explicit_version_file:
        explicit = Path(explicit_version_file).expanduser()
        if not explicit.is_absolute():
            explicit = (source_folder / explicit).resolve()
        explicit = explicit.resolve()
        if explicit.exists() and explicit.is_file():
            return explicit
        raise FileNotFoundError(
            "Nie znaleziono wskazanego pliku wersji: "
            f"{explicit}\nFolder do pakowania nie został zaakceptowany."
        )

    expected = default_version_file_for_source(source_folder)
    if expected.exists() and expected.is_file():
        return expected
    raise FileNotFoundError(
        "Nie znaleziono domyślnego pliku wersji Jaźni:\n"
        f"  - {expected}\n"
        "Folder do pakowania musi być rootem runtime/projektu Jaźni i zawierać "
        "plik .\\latka_jazn\\version.py."
    )


def read_source_version_info(source_folder: Path, explicit_version_file: str | Path | None = None) -> tuple[Path, str, str]:
    """Waliduje folder źródłowy i zwraca (version_file, version, release)."""
    resolved_version_file = find_version_file(source_folder, explicit_version_file)
    package_version = read_version_from_py(resolved_version_file)
    release = read_optional_string_from_py(resolved_version_file, RELEASE_NAME_VARIABLES) or PACKAGE_RELEASE_NAME
    return resolved_version_file, package_version, normalize_release_name(release)


def _run_source_integrity_action(
    source_folder: Path,
    action: str,
    request: dict[str, object],
) -> dict[str, object]:
    """Uruchamia kanoniczny moduł integralności z wybranego źródła.

    Osobny proces gwarantuje, że generator nie użyje przypadkiem wcześniej
    zaimportowanej wersji ``latka_jazn`` z innego runtime albo środowiska.
    """

    script = r'''
import json
import sys
from pathlib import Path

action = sys.argv[1]
root = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(root))
from latka_jazn.tools.package_integrity import (
    build_package_integrity_manifest,
    serialize_package_integrity_manifest,
    verify_package_integrity_manifest_in_zips,
)

request = json.load(sys.stdin)
if action == "build":
    payload = build_package_integrity_manifest(
        root,
        relative_paths=request.get("relative_paths") or [],
    )
    manifest_text = serialize_package_integrity_manifest(payload).decode("utf-8")
    result = {"payload": payload, "manifest_text": manifest_text}
elif action == "verify-zips":
    result = verify_package_integrity_manifest_in_zips(
        [Path(item) for item in request.get("zip_paths") or []],
        allowed_unprotected_prefixes=request.get("allowed_unprotected_prefixes") or [],
    )
else:
    raise SystemExit(f"unknown action: {action}")
print(json.dumps(result, ensure_ascii=False))
'''
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, action, str(source_folder.resolve())],
        input=json.dumps(request, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"Kanoniczny moduł integralności zakończył się kodem {completed.returncode}: {detail}"
        )
    try:
        payload = json.loads(completed.stdout)
    except Exception as exc:
        raise RuntimeError(
            "Kanoniczny moduł integralności zwrócił niepoprawny JSON: "
            f"{completed.stdout[:500]!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Kanoniczny moduł integralności nie zwrócił obiektu JSON")
    return payload


def build_integrity_manifest_virtual_file(
    source_folder: Path,
    plan: PackPlan,
    *,
    package_version: str,
) -> tuple[bytes, dict[str, object]] | None:
    """Buduje świeży manifest z dokładnego planu bez zmiany źródła."""

    relative_paths = [rel_posix(path, source_folder) for path in plan.files]
    required = {
        "SOURCE_PROVENANCE.json",
        "run.py",
        "main.py",
        "latka_jazn/version.py",
        PACKAGE_INTEGRITY_MANIFEST_NAME,
    }
    if not required.issubset(set(relative_paths)):
        return None
    result = _run_source_integrity_action(
        source_folder,
        "build",
        {"relative_paths": relative_paths},
    )
    payload = result.get("payload")
    manifest_text = result.get("manifest_text")
    if not isinstance(payload, dict) or not isinstance(manifest_text, str):
        raise RuntimeError("Niepełna odpowiedź budowania PACKAGE_INTEGRITY_MANIFEST.json")
    manifest_version = str(payload.get("runtime_version") or payload.get("version") or "")
    if normalize_version(manifest_version) != normalize_version(package_version):
        raise RuntimeError(
            "Wersja świeżego manifestu nie zgadza się z latka_jazn/version.py: "
            f"manifest={manifest_version!r}, version.py={package_version!r}"
        )
    return manifest_text.encode("utf-8"), payload


def verify_integrity_manifest_in_generated_volumes(
    source_folder: Path,
    volume_paths: list[Path],
    *,
    allowed_unprotected_prefixes: Iterable[str] = (),
) -> dict[str, object]:
    result = _run_source_integrity_action(
        source_folder,
        "verify-zips",
        {
            "zip_paths": [str(path.resolve()) for path in volume_paths],
            "allowed_unprotected_prefixes": list(allowed_unprotected_prefixes),
        },
    )
    if not result.get("ok"):
        errors = result.get("errors")
        raise ValueError(
            "Weryfikacja PACKAGE_INTEGRITY_MANIFEST.json w ZIP-ie nie powiodła się: "
            + json.dumps(errors, ensure_ascii=False)[:4000]
        )
    return result


def apply_version_to_archive_name(
    archive_basename: str,
    package_version: str,
    *,
    package_release_name: str | None = None,
    enabled: bool = True,
) -> str:
    raw = archive_basename.strip()
    if not raw:
        raise ValueError("archive_basename nie może być pusty")
    has_zip = raw.lower().endswith(".zip")
    stem = raw[:-4] if has_zip else raw
    version = normalize_version(package_version)
    release = normalize_release_name(package_release_name)

    if "{version}" in stem:
        stem = stem.replace("{version}", version)
    elif enabled:
        suffix = f"_v{version}"
        if not stem.lower().endswith(suffix.lower()):
            stem = f"{stem}{suffix}"

    if "{release_name}" in stem or "{release}" in stem:
        stem = stem.replace("{release_name}", release).replace("{release}", release)
        stem = re.sub(r"[-_.]+$", "", stem)
    elif enabled and release:
        release_suffix = f"-{release}"
        if not version.lower().endswith(release_suffix.lower()) and not stem.lower().endswith(release_suffix.lower()):
            stem = f"{stem}{release_suffix}"

    return sanitize_zip_name(stem)



def refresh_version_and_default_name(state: WizardState, *, keep_custom_name: bool = False) -> None:
    if state.source_folder is None:
        return
    state.resolved_version_file = find_version_file(state.source_folder, state.version_file)
    state.package_version = read_version_from_py(state.resolved_version_file)
    release = read_optional_string_from_py(state.resolved_version_file, RELEASE_NAME_VARIABLES) or PACKAGE_RELEASE_NAME
    state.package_release_name = normalize_release_name(release)
    should_keep_custom = bool(keep_custom_name and state.archive_name_manual and state.archive_name)
    if should_keep_custom:
        state.archive_name = sanitize_zip_name(state.archive_name)
        return
    state.archive_name = apply_version_to_archive_name(
        state.archive_basename_requested or ARCHIVE_BASENAME,
        state.package_version,
        package_release_name=state.package_release_name,
        enabled=True,
    )
    state.archive_name_manual = False
    state.plan = None

# =============================================================================
# WYKLUCZENIA I PLAN PAKOWANIA
# =============================================================================


def matches_include_prefix(rel: str, include_prefixes: Iterable[str], *, is_dir: bool = False) -> bool:
    prefixes = [p.strip().replace("\\", "/").lstrip("/") for p in include_prefixes if str(p).strip()]
    if not prefixes:
        return True
    rel = rel.replace("\\", "/").lstrip("/")
    rel_dir = rel.rstrip("/") + ("/" if is_dir else "")
    for prefix in prefixes:
        prefix = prefix.rstrip("/") + "/"
        base = prefix.rstrip("/")
        if rel == base or rel.startswith(prefix):
            return True
        # Dla katalogów pozwala pokazać katalog nadrzędny, jeśli prowadzi do prefiksu.
        if is_dir and prefix.startswith(rel_dir):
            return True
    return False



def matching_exclusion_pattern(rel: str, patterns: Iterable[str]) -> str | None:
    rel = rel.replace("\\", "/").lstrip("/")
    rel_path = PurePosixPath(rel)
    rel_parts = rel_path.parts
    rel_name = rel_path.name
    for pat in patterns:
        raw_pattern = pat.strip().replace("\\", "/")
        root_only = raw_pattern.startswith("/")
        p = raw_pattern.lstrip("/")
        if not p:
            continue
        if p.endswith("/"):
            folder = p.rstrip("/")
            if not folder:
                continue
            # Wzorzec bez ukośnika, np. __pycache__/ albo .git/, ma pasować
            # do folderu o tej nazwie na dowolnej głębokości, nie tylko w root.
            if "/" not in folder:
                if root_only:
                    if rel == folder or rel.startswith(folder + "/"):
                        return pat
                elif folder in rel_parts:
                    return pat
                continue
            if rel == folder or rel.startswith(folder + "/"):
                return pat
            continue
        if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel_name, p):
            return pat
    return None

def is_excluded(rel: str, patterns: Iterable[str]) -> bool:
    return matching_exclusion_pattern(rel, patterns) is not None


def discover_pack_plan(root: Path, include_empty_dirs: bool, exclude_patterns: list[str], include_prefixes: list[str] | None = None) -> PackPlan:
    root = root.resolve()
    files: list[Path] = []
    dirs: list[Path] = []
    excluded: list[tuple[str, str]] = []
    total_size = 0

    # Najpierw zbieramy ścieżki, żeby progress odkrywania był prawdziwy.
    all_paths = sorted(root.rglob("*"), key=lambda x: x.as_posix().lower())
    total = len(all_paths)
    if total:
        print_bar(0, total, label="Skanowanie")

    include_prefixes = include_prefixes or []

    for index, p in enumerate(all_paths, start=1):
        rel = rel_posix(p, root)
        if include_prefixes and not matches_include_prefix(rel, include_prefixes, is_dir=p.is_dir()):
            excluded.append((rel, "<poza profilem/include_prefix>"))
            if index % 200 == 0 or index == total:
                print_bar(index, total, label="Skanowanie")
            continue
        pattern = matching_exclusion_pattern(rel, exclude_patterns)
        if pattern is not None:
            excluded.append((rel, pattern))
            if index % 200 == 0 or index == total:
                print_bar(index, total, label="Skanowanie")
            continue
        if p.is_dir():
            if include_empty_dirs:
                dirs.append(p)
        elif p.is_file():
            files.append(p)
            try:
                total_size += p.stat().st_size
            except OSError:
                pass
        if index % 200 == 0 or index == total:
            print_bar(index, total, label="Skanowanie")

    return PackPlan(files=files, dirs=dirs, source_total_size=total_size, excluded=excluded)


def summarize_top_level(plan: PackPlan, root: Path, limit: int = 25) -> list[tuple[str, int, int]]:
    stats: dict[str, list[int]] = {}
    for file in plan.files:
        rel = rel_posix(file, root)
        top = rel.split("/", 1)[0]
        item = stats.setdefault(top, [0, 0])
        item[0] += 1
        try:
            item[1] += file.stat().st_size
        except OSError:
            pass
    rows = [(name, count_size[0], count_size[1]) for name, count_size in stats.items()]
    rows.sort(key=lambda x: (-x[1], x[0].lower()))
    return rows[:limit]


def save_preview_json(state: WizardState) -> Path:
    require_ready_state(state)
    assert state.plan is not None
    assert state.source_folder is not None
    assert state.out_dir is not None
    out_dir = state.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    preview_path = out_dir / f"{state.archive_name}.pack_preview.json"
    data = {
        "created_at": now_iso(),
        "script": Path(__file__).name,
        "script_version": VERSION,
        "source_folder": str(state.source_folder.resolve()),
        "output_dir": str(out_dir),
        "archive_name_after_join": state.archive_name,
        "package_version": state.package_version,
        "package_release_name": state.package_release_name,
        "version_file": str(state.resolved_version_file) if state.resolved_version_file else None,
        "part_size_mb": state.part_size_mb,
        "compression_level": state.compression_level,
        "include_empty_dirs": state.include_empty_dirs,
        "force": state.force,
        "pack_profile": state.pack_profile,
        "pack_profile_label": state.profile_label(),
        "include_prefixes": state.include_prefixes(),
        "exclude_patterns": state.effective_excludes(),
        "profile_default_exclude_patterns": state.profile_default_excludes(),
        "active_default_exclude_patterns": state.active_default_excludes(),
        "disabled_default_exclude_patterns": state.disabled_default_excludes,
        "custom_exclude_patterns": state.custom_excludes,
        "source_file_count": state.plan.file_count,
        "source_dir_count": state.plan.dir_count,
        "source_total_size_bytes": state.plan.source_total_size,
        "excluded_count": len(state.plan.excluded),
        "files": [rel_posix(p, state.source_folder.resolve()) for p in state.plan.files],
        "dirs": [rel_posix(p, state.source_folder.resolve()) + "/" for p in state.plan.dirs],
        "excluded_sample": [{"path": rel, "pattern": pat} for rel, pat in state.plan.excluded[:1000]],
    }
    preview_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return preview_path

# =============================================================================
# ZIP STREAMING
# =============================================================================


class SplitPartWriter:
    """Nie-seekowalny writer dla zipfile.ZipFile zapisujący .zip.001, .zip.002 itd."""

    def __init__(self, out_dir: Path, base_zip_name: str, part_size: int, *, force: bool = False):
        if part_size <= 0:
            raise ValueError("part_size musi być większy od zera")
        self.out_dir = out_dir
        self.base_zip_name = sanitize_zip_name(base_zip_name)
        self.part_size = part_size
        self.force = force
        self.total_written = 0
        self.current_part_no = 0
        self.current_part_written = 0
        self.current_file: BinaryIO | None = None
        self.current_hash: Any | None = None
        self.full_hash = hashlib.sha256()
        self.parts: list[dict[str, object]] = []
        self.closed = False
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._check_existing_outputs()

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readable(self) -> bool:
        return False

    def tell(self) -> int:
        return self.total_written

    def flush(self) -> None:
        if self.current_file:
            self.current_file.flush()

    def close(self) -> None:
        if self.closed:
            return
        self._close_current_part()
        self.closed = True

    def write(self, data) -> int:
        if self.closed:
            raise ValueError("zapis do zamkniętego SplitPartWriter")
        if not data:
            return 0
        view = memoryview(data)
        total_len = len(view)
        offset = 0
        while offset < total_len:
            if self.current_file is None:
                self._open_next_part()
            free = self.part_size - self.current_part_written
            if free <= 0:
                self._close_current_part()
                continue
            take = min(free, total_len - offset)
            chunk = view[offset: offset + take]
            assert self.current_file is not None
            assert self.current_hash is not None
            self.current_file.write(chunk)
            self.current_hash.update(chunk)
            self.full_hash.update(chunk)
            self.current_part_written += take
            self.total_written += take
            offset += take
            if self.current_part_written >= self.part_size:
                self._close_current_part()
        return total_len

    def _part_path(self, no: int) -> Path:
        return self.out_dir / f"{self.base_zip_name}.{no:03d}"

    def _known_output_paths(self) -> list[Path]:
        patterns = [
            f"{self.base_zip_name}.*",
            f"{self.base_zip_name}.parts.sha256",
            f"{self.base_zip_name}.sha256",
            f"{self.base_zip_name}.source_files.sha256",
            f"{self.base_zip_name}.manifest.json",
            f"{self.base_zip_name}.join.ps1",
            f"{self.base_zip_name}.pack_preview.json",
        ]
        paths: list[Path] = []
        for pat in patterns:
            paths.extend(self.out_dir.glob(pat))
        return sorted(set(paths))

    def _check_existing_outputs(self) -> None:
        existing = self._known_output_paths()
        if not existing:
            return
        if not self.force:
            sample = "\n".join(f"  - {p}" for p in existing[:20])
            more = "" if len(existing) <= 20 else f"\n  ... oraz {len(existing) - 20} więcej"
            raise FileExistsError(
                "Znaleziono wcześniejsze pliki wyjściowe. Włącz force albo zmień nazwę/folder wyjściowy.\n"
                + sample + more
            )
        for p in existing:
            if p.is_file():
                p.unlink()

    def _open_next_part(self) -> None:
        self.current_part_no += 1
        self.current_part_written = 0
        part_path = self._part_path(self.current_part_no)
        self.current_file = part_path.open("xb")
        self.current_hash = hashlib.sha256()

    def _close_current_part(self) -> None:
        if self.current_file is None:
            return
        self.current_file.flush()
        self.current_file.close()
        assert self.current_hash is not None
        part_path = self._part_path(self.current_part_no)
        size = part_path.stat().st_size
        self.parts.append({
            "part_no": self.current_part_no,
            "filename": part_path.name,
            "size_bytes": size,
            "sha256": self.current_hash.hexdigest(),
        })
        self.current_file = None
        self.current_hash = None
        self.current_part_written = 0


class SplitPartsReader:
    """Seekowalny, tylko-do-odczytu widok wielu części jako jednego pliku ZIP.

    Pozwala `zipfile.ZipFile` otworzyć i w pełni przetestować dzielone archiwum
    bez tworzenia tymczasowego pełnego ZIP-a na dysku.
    """

    def __init__(self, paths: list[Path]):
        if not paths:
            raise ValueError("Brak części do odczytu")
        self.paths = [Path(path).resolve() for path in paths]
        self.sizes = [path.stat().st_size for path in self.paths]
        self.offsets = [0]
        for size in self.sizes:
            self.offsets.append(self.offsets[-1] + size)
        self.total_size = self.offsets[-1]
        self.position = 0
        self._handle: BinaryIO | None = None
        self._handle_index = -1
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
        if self.closed:
            raise ValueError("operacja na zamkniętym czytniku")
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self.position + offset
        elif whence == os.SEEK_END:
            target = self.total_size + offset
        else:
            raise ValueError(f"Nieobsługiwany whence: {whence}")
        if target < 0:
            raise ValueError("ujemna pozycja seek")
        self.position = min(target, self.total_size)
        return self.position

    def _open_part(self, index: int) -> BinaryIO:
        if self._handle is not None and self._handle_index == index:
            return self._handle
        if self._handle is not None:
            self._handle.close()
        self._handle = self.paths[index].open("rb")
        self._handle_index = index
        return self._handle

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            raise ValueError("operacja na zamkniętym czytniku")
        if self.position >= self.total_size:
            return b""
        remaining = self.total_size - self.position if size is None or size < 0 else min(size, self.total_size - self.position)
        chunks: list[bytes] = []
        while remaining > 0 and self.position < self.total_size:
            index = bisect.bisect_right(self.offsets, self.position) - 1
            index = min(max(index, 0), len(self.paths) - 1)
            local_offset = self.position - self.offsets[index]
            available = self.sizes[index] - local_offset
            take = min(remaining, available)
            handle = self._open_part(index)
            handle.seek(local_offset)
            chunk = handle.read(take)
            if not chunk:
                raise OSError(f"Nieoczekiwany koniec części: {self.paths[index]}")
            chunks.append(chunk)
            self.position += len(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def normalize_artifact_mode(value: str | None) -> str:
    mode = str(value or DEFAULT_ARTIFACT_MODE).strip().lower()
    aliases = {
        "parts": "parts_only",
        "parts-only": "parts_only",
        "minimal": "parts_only",
        "all": "diagnostic",
        "full": "diagnostic",
        "sidecars": "diagnostic",
    }
    mode = aliases.get(mode, mode)
    if mode not in ARTIFACT_MODES:
        raise ValueError(f"Nieznany tryb plików dodatkowych: {value!r}")
    return mode


def cleanup_archive_outputs(out_dir: Path, archive_name: str) -> None:
    """Usuwa artefakty wskazanego archiwum po nieudanej operacji.

    Obsługuje nowy format `<nazwa>.zip`, `<nazwa>.part002.zip`, ... oraz
    dawne binarne części `<nazwa>.zip.001`, `.002`, ... .
    """
    out_dir = Path(out_dir).resolve()
    archive_name = sanitize_zip_name(archive_name)
    stem = archive_name[:-4]
    candidates: set[Path] = set()
    for pattern in (
        archive_name,
        f"{stem}.part[0-9][0-9][0-9].zip",
        f"{archive_name}.*",
        f"{stem}.package-set.sha256",
        f"{stem}.extract_all.py",
    ):
        candidates.update(out_dir.glob(pattern))
    for path in sorted(candidates):
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def verify_generated_split_archive(
    out_dir: Path,
    archive_name: str,
    parts: list[dict[str, object]],
    *,
    expected_full_sha256: str,
    run_crc: bool = True,
) -> dict[str, object]:
    """Pełna weryfikacja wygenerowanych części bez zapisywania pełnego ZIP-a."""
    archive_name = sanitize_zip_name(archive_name)
    if not parts:
        raise ValueError("Generator nie utworzył żadnej części ZIP")

    paths: list[Path] = []
    full_hash = hashlib.sha256()
    total_size = 0
    for expected_no, item in enumerate(parts, start=1):
        filename = str(item.get("filename") or "")
        if not filename.endswith(f".{expected_no:03d}"):
            raise ValueError(f"Nieciągła numeracja części: {filename}")
        path = out_dir / filename
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Brak wygenerowanej części: {path}")
        actual_size = path.stat().st_size
        expected_size = int(item.get("size_bytes") or 0)
        if actual_size != expected_size:
            raise ValueError(f"Zły rozmiar części {filename}: {actual_size}, oczekiwano {expected_size}")
        part_hash = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(8 * 1024 * 1024)
                if not chunk:
                    break
                part_hash.update(chunk)
                full_hash.update(chunk)
        actual_part_sha = part_hash.hexdigest()
        expected_part_sha = str(item.get("sha256") or "").lower()
        if actual_part_sha != expected_part_sha:
            raise ValueError(f"Zły SHA256 części {filename}: {actual_part_sha}, oczekiwano {expected_part_sha}")
        paths.append(path)
        total_size += actual_size

    actual_full_sha = full_hash.hexdigest()
    if actual_full_sha != expected_full_sha256.lower():
        raise ValueError(f"Zły SHA256 logicznego ZIP-a: {actual_full_sha}, oczekiwano {expected_full_sha256}")

    with SplitPartsReader(paths) as reader:
        with zipfile.ZipFile(reader, "r") as zf:
            validate_zip_member_names(zf)
            entries = len(zf.infolist())
            if run_crc:
                bad = zf.testzip()
                if bad:
                    raise ValueError(f"Błędny CRC/header wpisu ZIP: {bad}")

    return {
        "ok": True,
        "archive_name": archive_name,
        "parts_count": len(paths),
        "logical_zip_size_bytes": total_size,
        "logical_zip_sha256": actual_full_sha,
        "entries": entries,
        "crc_tested": bool(run_crc),
    }


class Utf8ZipInfo(zipfile.ZipInfo):
    """ZipInfo wymuszający UTF-8 w lokalnym nagłówku i central directory.

    Python zwykle sam ustawia flagę UTF-8 dla nazw spoza ASCII, ale przy
    strumieniowym zapisie dużych paczek i testach narzędziami Info-ZIP
    bezpieczniej wymusić identyczne kodowanie nazwy w obu miejscach ZIP-a.
    To ogranicza ostrzeżenia typu: mismatching local filename / central filename.
    """

    def _encodeFilenameFlags(self):  # type: ignore[override]
        return self.filename.encode("utf-8"), self.flag_bits | 0x800

def make_zipinfo_for_file(src: Path, arcname: str, compression: int, compresslevel: int) -> zipfile.ZipInfo:
    zi = Utf8ZipInfo(arcname, date_time=safe_zip_datetime(src))
    zi.compress_type = compression
    for attr_name in ("compress_level", "_compresslevel"):
        try:
            setattr(zi, attr_name, compresslevel)
        except Exception:
            pass
    try:
        mode = src.stat().st_mode
        zi.external_attr = (mode & 0xFFFF) << 16
        if os.name == "nt":
            zi.external_attr |= 0x20
    except OSError:
        pass
    return zi


def make_zipinfo_for_dir(src: Path, arcname: str) -> zipfile.ZipInfo:
    if not arcname.endswith("/"):
        arcname += "/"
    zi = Utf8ZipInfo(arcname, date_time=safe_zip_datetime(src))
    zi.external_attr = ((src.stat().st_mode if src.exists() else 0o40755) & 0xFFFF) << 16 | 0x10
    zi.compress_type = zipfile.ZIP_STORED
    return zi


def write_join_script(out_dir: Path, base_zip_name: str) -> None:
    base_zip_name = sanitize_zip_name(base_zip_name)
    ps1 = out_dir / f"{base_zip_name}.join.ps1"
    content = rf'''# Łączy części {base_zip_name}.001, {base_zip_name}.002, ... w pełny ZIP.
# Uruchom w PowerShell w tym samym folderze co części:
#   powershell -ExecutionPolicy Bypass -File .\{base_zip_name}.join.ps1
#
# Skrypt weryfikuje pliki kontrolne, jeżeli są obok części:
#   {base_zip_name}.manifest.json
#   {base_zip_name}.parts.sha256
#   {base_zip_name}.sha256
#
# Opcje:
#   -SkipPartHash       pomija SHA256 pojedynczych części, ale nadal sprawdza kompletność i pełny ZIP
#   -KeepExisting       nie usuwa istniejącego pełnego ZIP-a; przerwie pracę, jeśli plik już istnieje
#   -DotNetZipCheck     po sklejeniu sprawdza, czy .NET umie otworzyć central directory ZIP-a
#   -PythonZipTest      uruchamia: python -X utf8 -m zipfile -t <pełny ZIP>
#   -PythonExtract      uruchamia obok wygenerowany .extract_here.py i rozpakowuje do -Destination
#   -Destination        domyślnie /mnt/data/jazn_runtime_current dla środowiska ChatGPT

param(
    [switch]$SkipPartHash,
    [switch]$KeepExisting,
    [switch]$DotNetZipCheck,
    [switch]$PythonZipTest,
    [switch]$PythonExtract,
    [string]$Destination = "/mnt/data/jazn_runtime_current"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$base = "{base_zip_name}"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) {{ $scriptDir = (Get-Location).Path }}
Set-Location -LiteralPath $scriptDir

$swTotal = [System.Diagnostics.Stopwatch]::StartNew()
$manifestPath = Join-Path $scriptDir "$base.manifest.json"
$partsHashPath = Join-Path $scriptDir "$base.parts.sha256"
$fullHashPath = Join-Path $scriptDir "$base.sha256"
$outPath = Join-Path $scriptDir $base
$extractHelperPath = Join-Path $scriptDir "$base.extract_here.py"

function Get-PythonCommand {{
    foreach ($candidate in @("py", "python", "python3")) {{
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {{ return $cmd.Source }}
    }}
    throw "Nie znaleziono Pythona: py/python/python3. Bez niego PS1 może skleić ZIP, ale nie wykona PythonZipTest/PythonExtract."
}}

function Invoke-PythonChecked([string[]]$Arguments) {{
    $python = Get-PythonCommand
    & $python @Arguments
    if ($LASTEXITCODE -ne 0) {{ throw ("Python zakonczyl sie kodem {{0}}: {{1}} {{2}}" -f $LASTEXITCODE, $python, ($Arguments -join ' ')) }}
}}

function Read-ShaFile([string]$Path) {{
    $items = @()
    if (-not (Test-Path -LiteralPath $Path)) {{ return $items }}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {{
        $raw = [string]$line
        if (-not $raw.Trim()) {{ continue }}
        if ($raw.TrimStart().StartsWith('#')) {{ continue }}
        if ($raw -match '^([0-9a-fA-F]{{64}})\s+\*?(.+)$') {{
            $items += [pscustomobject]@{{ sha256 = $Matches[1].ToLowerInvariant(); filename = $Matches[2].Trim() }}
        }}
    }}
    return $items
}}

$expectedParts = @()
$expectedFullSha = $null
$source = "glob"

if (Test-Path -LiteralPath $manifestPath) {{
    $source = "manifest"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $expectedFullSha = ([string]$manifest.logical_full_zip_sha256).ToLowerInvariant()
    foreach ($p in @($manifest.parts)) {{
        $expectedParts += [pscustomobject]@{{
            part_no = [int]$p.part_no
            filename = [string]$p.filename
            size_bytes = [int64]$p.size_bytes
            sha256 = ([string]$p.sha256).ToLowerInvariant()
        }}
    }}
}} elseif (Test-Path -LiteralPath $partsHashPath) {{
    $source = "parts.sha256"
    $n = 0
    foreach ($p in Read-ShaFile $partsHashPath) {{
        $n += 1
        $expectedParts += [pscustomobject]@{{
            part_no = $n
            filename = [string]$p.filename
            size_bytes = $null
            sha256 = ([string]$p.sha256).ToLowerInvariant()
        }}
    }}
}}

if (-not $expectedParts -or $expectedParts.Count -eq 0) {{
    $source = "glob"
    $found = Get-ChildItem -LiteralPath $scriptDir -File | Where-Object {{ $_.Name -match ('^' + [regex]::Escape($base) + '\.\d{{3}}$') }} | Sort-Object Name
    $n = 0
    foreach ($f in $found) {{
        $n += 1
        $expectedParts += [pscustomobject]@{{
            part_no = $n
            filename = $f.Name
            size_bytes = $null
            sha256 = $null
        }}
    }}
}}

if (-not $expectedParts -or $expectedParts.Count -eq 0) {{ throw "Brak części dla $base" }}
$expectedParts = @($expectedParts | Sort-Object part_no)

for ($i = 0; $i -lt $expectedParts.Count; $i++) {{
    $expectedNo = $i + 1
    $expectedSuffix = ('{{0:d3}}' -f $expectedNo)
    if ($expectedParts[$i].filename -notmatch ('\.' + $expectedSuffix + '$')) {{
        throw "Nieciągła albo błędna kolejność części: oczekiwano .$expectedSuffix, a jest $($expectedParts[$i].filename)"
    }}
}}

$expectedNames = @{{}}
foreach ($p in $expectedParts) {{ $expectedNames[$p.filename] = $true }}
$extraParts = Get-ChildItem -LiteralPath $scriptDir -File | Where-Object {{
    $_.Name -match ('^' + [regex]::Escape($base) + '\.\d{{3}}$') -and -not $expectedNames.ContainsKey($_.Name)
}}
if ($extraParts.Count -gt 0) {{
    $names = ($extraParts | Select-Object -ExpandProperty Name) -join ', '
    throw "Znaleziono dodatkowe części nieujęte w manifeście/hashach: $names"
}}

Write-Host "Źródło listy części: $source"
Write-Host "Części oczekiwane: $($expectedParts.Count)"

$sw = [System.Diagnostics.Stopwatch]::StartNew()
foreach ($p in $expectedParts) {{
    $partPath = Join-Path $scriptDir $p.filename
    if (-not (Test-Path -LiteralPath $partPath)) {{ throw "Brak części: $($p.filename)" }}
    $file = Get-Item -LiteralPath $partPath
    if ($null -ne $p.size_bytes -and [int64]$p.size_bytes -ne [int64]$file.Length) {{
        throw "Zły rozmiar części $($p.filename): jest $($file.Length), oczekiwano $($p.size_bytes)"
    }}
    if (-not $SkipPartHash -and $p.sha256) {{
        $actual = (Get-FileHash -LiteralPath $partPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $p.sha256) {{ throw "Zły SHA256 części $($p.filename): $actual, oczekiwano $($p.sha256)" }}
    }}
}}
$sw.Stop()
Write-Host ("Weryfikacja części OK: {{0:n3}} s" -f $sw.Elapsed.TotalSeconds)

if (Test-Path -LiteralPath $fullHashPath) {{
    $fromShaFile = @(Read-ShaFile $fullHashPath)
    if ($fromShaFile.Count -gt 0 -and -not $expectedFullSha) {{
        $expectedFullSha = ([string]$fromShaFile[0].sha256).ToLowerInvariant()
    }}
}}

if (Test-Path -LiteralPath $outPath) {{
    if ($KeepExisting) {{ throw "Pełny ZIP już istnieje: $outPath" }}
    Remove-Item -LiteralPath $outPath -Force
}}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$bufferSize = 8 * 1024 * 1024
$target = [System.IO.File]::Open($outPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {{
    foreach ($p in $expectedParts) {{
        Write-Host "Dodaję $($p.filename)..."
        $partPath = Join-Path $scriptDir $p.filename
        $src = [System.IO.File]::Open($partPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        try {{ $src.CopyTo($target, $bufferSize) }} finally {{ $src.Dispose() }}
    }}
}} finally {{
    $target.Dispose()
}}
$sw.Stop()
Write-Host ("Sklejanie OK: {{0:n3}} s" -f $sw.Elapsed.TotalSeconds)

if ($expectedFullSha) {{
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $actualFullSha = (Get-FileHash -LiteralPath $outPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $sw.Stop()
    if ($actualFullSha -ne $expectedFullSha) {{
        throw "Zły SHA256 pełnego ZIP-a: $actualFullSha, oczekiwano $expectedFullSha"
    }}
    Write-Host ("SHA256 pełnego ZIP-a OK: {{0:n3}} s" -f $sw.Elapsed.TotalSeconds)
}} else {{
    Write-Host "UWAGA: brak oczekiwanego SHA256 pełnego ZIP-a; pomijam porównanie."
}}

if ($DotNetZipCheck) {{
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($outPath)
    try {{
        Write-Host "Central directory OK; wpisów ZIP: $($zip.Entries.Count)"
    }} finally {{
        $zip.Dispose()
    }}
    $sw.Stop()
    Write-Host ("Test .NET central directory OK: {{0:n3}} s" -f $sw.Elapsed.TotalSeconds)
}}

if ($PythonZipTest) {{
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-PythonChecked @("-X", "utf8", "-m", "zipfile", "-t", $outPath)
    $sw.Stop()
    Write-Host ("Python zipfile -t OK: {{0:n3}} s" -f $sw.Elapsed.TotalSeconds)
}}

if ($PythonExtract) {{
    if (-not (Test-Path -LiteralPath $extractHelperPath)) {{ throw "Brak helpera rozpakowania: $extractHelperPath" }}
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-PythonChecked @("-X", "utf8", $extractHelperPath, "--parts-dir", $scriptDir, "--destination", $Destination, "--keep-existing-zip", "--force")
    $sw.Stop()
    Write-Host ("Python extract_here OK: {{0:n3}} s" -f $sw.Elapsed.TotalSeconds)
}}

$swTotal.Stop()
Write-Host "Gotowe: $outPath"
Write-Host ("Czas łączny: {{0:n3}} s" -f $swTotal.Elapsed.TotalSeconds)
Write-Host "Do pełnego testu CRC/rozpakowania dużych paczek z polskimi znakami najlepiej użyj Python zipfile albo helpera v5, np.:"
Write-Host "  py -X utf8 -m zipfile -t .\$base"
Write-Host "  py -X utf8 .\$base.extract_here.py --destination /mnt/data/jazn_runtime_current"
'''
    ps1.write_text(content, encoding="utf-8-sig")


def write_extract_here_script(out_dir: Path, base_zip_name: str) -> None:
    """Tworzy pomocniczy skrypt Pythona do walidacji i rozpakowania paczki w /mnt/data."""
    base_zip_name = sanitize_zip_name(base_zip_name)
    helper = out_dir / f"{base_zip_name}.extract_here.py"
    template = r"""#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Validate, join, test and extract split Jaźń ZIP parts.
# Default destination is /mnt/data/jazn_runtime_current for ChatGPT sandbox/container runtime.
# The script refuses unsafe ZIP paths before extraction.
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

BASE_ZIP_NAME = __BASE_ZIP_NAME__
CHUNK_SIZE = 8 * 1024 * 1024

@dataclass
class ExpectedPart:
    part_no: int
    filename: str
    size_bytes: int | None
    sha256: str | None

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        while True:
            b = f.read(CHUNK_SIZE)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def parse_sha_file(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        raw = line.strip()
        if not raw or raw.startswith('#'):
            continue
        parts = raw.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            continue
        rows.append((parts[0].lower(), parts[1].lstrip('*').strip()))
    return rows

def load_expectations(parts_dir: Path) -> tuple[list[ExpectedPart], str | None, str]:
    manifest_path = parts_dir / (BASE_ZIP_NAME + '.manifest.json')
    parts_sha_path = parts_dir / (BASE_ZIP_NAME + '.parts.sha256')
    full_sha_path = parts_dir / (BASE_ZIP_NAME + '.sha256')
    expected: list[ExpectedPart] = []
    full_sha: str | None = None
    source = 'glob'
    if manifest_path.exists():
        source = 'manifest'
        data = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        full_sha = str(data.get('logical_full_zip_sha256') or '').lower() or None
        for item in data.get('parts') or []:
            expected.append(ExpectedPart(int(item['part_no']), str(item['filename']), int(item['size_bytes']) if item.get('size_bytes') is not None else None, str(item.get('sha256') or '').lower() or None))
    elif parts_sha_path.exists():
        source = 'parts.sha256'
        for no, (digest, filename) in enumerate(parse_sha_file(parts_sha_path), start=1):
            expected.append(ExpectedPart(no, filename, None, digest))
    if not full_sha and full_sha_path.exists():
        rows = parse_sha_file(full_sha_path)
        if rows:
            full_sha = rows[0][0]
    if not expected:
        found = sorted(parts_dir.glob(BASE_ZIP_NAME + '.[0-9][0-9][0-9]'))
        for no, path in enumerate(found, start=1):
            expected.append(ExpectedPart(no, path.name, None, None))
    expected.sort(key=lambda p: p.part_no)
    return expected, full_sha, source

def validate_parts(parts_dir: Path, expected: list[ExpectedPart], *, skip_part_hash: bool) -> None:
    if not expected:
        raise SystemExit('Brak części ZIP.')
    expected_names = {p.filename for p in expected}
    extra = sorted(p.name for p in parts_dir.glob(BASE_ZIP_NAME + '.[0-9][0-9][0-9]') if p.name not in expected_names)
    if extra:
        raise SystemExit('Dodatkowe części nieujęte w manifeście/hashach: ' + ', '.join(extra))
    for idx, part in enumerate(expected, start=1):
        suffix = f'.{idx:03d}'
        if not part.filename.endswith(suffix):
            raise SystemExit(f'Nieciągła kolejność części: oczekiwano {suffix}, jest {part.filename}')
        path = parts_dir / part.filename
        if not path.exists():
            raise SystemExit(f'Brak części: {part.filename}')
        if part.size_bytes is not None and path.stat().st_size != part.size_bytes:
            raise SystemExit(f'Zły rozmiar części {part.filename}: jest {path.stat().st_size}, oczekiwano {part.size_bytes}')
        if part.sha256 and not skip_part_hash:
            actual = sha256_file(path)
            if actual != part.sha256:
                raise SystemExit(f'Zły SHA256 części {part.filename}: {actual}, oczekiwano {part.sha256}')

def join_parts(parts_dir: Path, expected: list[ExpectedPart], out_zip: Path, *, keep_existing: bool) -> None:
    if out_zip.exists():
        if keep_existing:
            return
        out_zip.unlink()
    tmp = out_zip.with_suffix(out_zip.suffix + '.joining.tmp')
    if tmp.exists():
        tmp.unlink()
    with tmp.open('xb') as target:
        for part in expected:
            with (parts_dir / part.filename).open('rb') as src:
                shutil.copyfileobj(src, target, length=CHUNK_SIZE)
    os.replace(tmp, out_zip)

def unsafe_zip_name(name: str) -> str | None:
    p = PurePosixPath(name.replace('\\', '/'))
    if name.startswith(('/', '\\')):
        return 'absolute path'
    if len(name) >= 2 and name[1] == ':':
        return 'drive path'
    if any(part == '..' for part in p.parts):
        return 'parent traversal'
    if '\x00' in name:
        return 'NUL byte'
    return None

def validate_zip_paths(zf: zipfile.ZipFile) -> None:
    bad = []
    for info in zf.infolist():
        reason = unsafe_zip_name(info.filename)
        if reason:
            bad.append(f'{info.filename!r}: {reason}')
    if bad:
        raise SystemExit('ZIP zawiera niebezpieczne ścieżki:\n' + '\n'.join(bad[:20]))

def extract_zip(out_zip: Path, destination: Path, *, force: bool, clean: bool) -> None:
    destination = destination.resolve()
    dangerous_clean_targets = {Path('/').resolve(), Path('/mnt').resolve(), Path('/mnt/data').resolve()}
    if clean and destination in dangerous_clean_targets:
        raise SystemExit(f'Odmawiam --clean dla zbyt szerokiego celu: {destination}')
    if destination.exists():
        if clean:
            shutil.rmtree(destination)
        elif not force:
            raise SystemExit(f'Folder docelowy już istnieje: {destination}\nUżyj --force albo --clean.')
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, 'r') as zf:
        validate_zip_paths(zf)
        zf.extractall(destination)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Validate, join, test and extract Jaźń ZIP parts.')
    parser.add_argument('--parts-dir', default=str(Path(__file__).resolve().parent), help='Folder with .zip.001 parts and sidecar hash/manifest files.')
    parser.add_argument('--destination', default='/mnt/data/jazn_runtime_current', help='Extraction destination. Default: /mnt/data/jazn_runtime_current')
    parser.add_argument('--zip-out', default='', help='Full ZIP path. Default: <parts-dir>/<base zip name>.')
    parser.add_argument('--skip-part-hash', action='store_true')
    parser.add_argument('--skip-testzip', action='store_true')
    parser.add_argument('--join-only', action='store_true')
    parser.add_argument('--keep-existing-zip', action='store_true')
    parser.add_argument('--force', action='store_true', help='Allow extracting into existing destination.')
    parser.add_argument('--clean', action='store_true', help='Delete destination first. Refuses /, /mnt and /mnt/data.')
    args = parser.parse_args(argv)
    t0 = time.perf_counter()
    parts_dir = Path(args.parts_dir).expanduser().resolve()
    out_zip = Path(args.zip_out).expanduser().resolve() if args.zip_out else parts_dir / BASE_ZIP_NAME
    expected, expected_full_sha, source = load_expectations(parts_dir)
    print(f'Źródło listy części: {source}')
    print(f'Części oczekiwane: {len(expected)}')
    step = time.perf_counter()
    validate_parts(parts_dir, expected, skip_part_hash=args.skip_part_hash)
    print(f'Weryfikacja części OK: {time.perf_counter() - step:.3f} s')
    step = time.perf_counter()
    join_parts(parts_dir, expected, out_zip, keep_existing=args.keep_existing_zip)
    print(f'Sklejanie OK: {time.perf_counter() - step:.3f} s')
    if expected_full_sha:
        step = time.perf_counter()
        actual = sha256_file(out_zip)
        if actual != expected_full_sha:
            raise SystemExit(f'Zły SHA256 pełnego ZIP-a: {actual}, oczekiwano {expected_full_sha}')
        print(f'SHA256 pełnego ZIP-a OK: {time.perf_counter() - step:.3f} s')
    with zipfile.ZipFile(out_zip, 'r') as zf:
        validate_zip_paths(zf)
        print(f'Central directory OK; wpisów ZIP: {len(zf.infolist())}')
        if not args.skip_testzip:
            step = time.perf_counter()
            bad = zf.testzip()
            if bad:
                raise SystemExit(f'Błędny CRC/header wpisu ZIP: {bad}')
            print(f'Pełny test CRC zipfile.testzip OK: {time.perf_counter() - step:.3f} s')
    if not args.join_only:
        step = time.perf_counter()
        extract_zip(out_zip, Path(args.destination), force=args.force, clean=args.clean)
        print(f'Rozpakowanie OK: {time.perf_counter() - step:.3f} s')
        print(f'Cel: {Path(args.destination).expanduser().resolve()}')
    print(f'Czas łączny: {time.perf_counter() - t0:.3f} s')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
"""
    content = template.replace("__BASE_ZIP_NAME__", repr(base_zip_name))
    helper.write_text(content, encoding="utf-8")



@dataclass(slots=True)
class PackagePartExpectation:
    part_no: int
    filename: str
    size_bytes: int | None = None
    sha256: str | None = None


def parse_sha256sum_file(path: Path) -> list[tuple[str, str]]:
    """Czyta prosty plik SHA256SUMS: `<hash>  <filename>`."""
    rows: list[tuple[str, str]] = []
    if not path.exists() or not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", raw)
        if not match:
            continue
        rows.append((match.group(1).lower(), match.group(2).strip()))
    return rows


def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    """Zwraca SHA256 pliku bez ładowania go w całości do pamięci."""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def infer_base_zip_name(parts_dir: Path, base_zip_name: str | None = None) -> str:
    """Ustala nazwę pełnego ZIP-a na podstawie argumentu albo plików sidecar."""
    if base_zip_name:
        return sanitize_zip_name(base_zip_name)
    manifests = sorted(parts_dir.glob("*.zip.manifest.json"))
    if len(manifests) == 1:
        return manifests[0].name[:-len(".manifest.json")]
    part_groups: dict[str, int] = {}
    for part in parts_dir.glob("*.zip.[0-9][0-9][0-9]"):
        base = part.name[:-4]
        part_groups[base] = part_groups.get(base, 0) + 1
    if len(part_groups) == 1:
        return next(iter(part_groups))
    ordinary_bases = sorted(
        path.name
        for path in parts_dir.glob("*.zip")
        if not re.search(r"\.part\d{3}\.zip$", path.name, flags=re.IGNORECASE)
    )
    candidates = set(manifest.name[:-len(".manifest.json")] for manifest in manifests)
    candidates.update(part_groups)
    candidates.update(ordinary_bases)
    if len(candidates) == 1:
        return sanitize_zip_name(next(iter(candidates)))
    if not candidates:
        raise FileNotFoundError(f"Nie znaleziono ZIP-a, części ani manifestu paczki w folderze: {parts_dir}")
    raise ValueError("W folderze jest więcej niż jedna możliwa paczka. Podaj nazwę ZIP przez --zip-name albo ustaw nazwę w menu.")


def load_package_expectations(parts_dir: Path, base_zip_name: str) -> tuple[list[PackagePartExpectation], str | None, str]:
    """Ładuje oczekiwane części z manifestu, parts.sha256 albo globu."""
    parts_dir = parts_dir.expanduser().resolve()
    base_zip_name = sanitize_zip_name(base_zip_name)
    manifest_path = parts_dir / f"{base_zip_name}.manifest.json"
    parts_sha_path = parts_dir / f"{base_zip_name}.parts.sha256"
    full_sha_path = parts_dir / f"{base_zip_name}.sha256"
    expected: list[PackagePartExpectation] = []
    expected_full_sha: str | None = None
    source = "glob"

    if manifest_path.exists():
        source = "manifest"
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        full_value = str(data.get("logical_full_zip_sha256") or "").strip().lower()
        expected_full_sha = full_value or None
        for item in data.get("parts") or []:
            expected.append(PackagePartExpectation(
                part_no=int(item["part_no"]),
                filename=str(item["filename"]),
                size_bytes=int(item["size_bytes"]) if item.get("size_bytes") is not None else None,
                sha256=str(item.get("sha256") or "").strip().lower() or None,
            ))
    elif parts_sha_path.exists():
        source = "parts.sha256"
        for part_no, (digest, filename) in enumerate(parse_sha256sum_file(parts_sha_path), start=1):
            expected.append(PackagePartExpectation(part_no=part_no, filename=filename, sha256=digest))

    if not expected:
        windows_volumes = discover_windows_zip_volumes(parts_dir, base_zip_name)
        if windows_volumes:
            for part_no, path in enumerate(windows_volumes, start=1):
                expected.append(PackagePartExpectation(part_no=part_no, filename=path.name))
        else:
            found = sorted(parts_dir.glob(f"{base_zip_name}.[0-9][0-9][0-9]"))
            for part_no, path in enumerate(found, start=1):
                expected.append(PackagePartExpectation(part_no=part_no, filename=path.name))

    if not expected_full_sha and full_sha_path.exists():
        rows = parse_sha256sum_file(full_sha_path)
        if rows:
            expected_full_sha = rows[0][0]

    expected.sort(key=lambda item: item.part_no)
    return expected, expected_full_sha, source


def validate_package_parts(parts_dir: Path, base_zip_name: str, *, skip_part_hash: bool = False) -> tuple[list[PackagePartExpectation], str | None, str]:
    """Sprawdza kompletność, kolejność, rozmiary i SHA256 części paczki."""
    parts_dir = parts_dir.expanduser().resolve()
    base_zip_name = sanitize_zip_name(base_zip_name)
    expected, expected_full_sha, source = load_package_expectations(parts_dir, base_zip_name)
    if not expected:
        raise FileNotFoundError(f"Brak części paczki: {base_zip_name}.001, {base_zip_name}.002, ...")

    expected_names = {part.filename for part in expected}
    extra_candidates = list(parts_dir.glob(f"{base_zip_name}.[0-9][0-9][0-9]"))
    extra_candidates.extend(parts_dir.glob(f"{base_zip_name[:-4]}.part[0-9][0-9][0-9].zip"))
    extra_parts = sorted(path.name for path in extra_candidates if path.name not in expected_names)
    if extra_parts:
        raise ValueError("Znaleziono dodatkowe części nieujęte w manifeście/hashach: " + ", ".join(extra_parts))

    independent_windows_volumes = bool(expected and expected[0].filename == base_zip_name)
    for index, part in enumerate(expected, start=1):
        if independent_windows_volumes:
            expected_name = windows_zip_volume_name(base_zip_name, index)
            if part.filename != expected_name:
                raise ValueError(
                    "Nieciągła albo błędna kolejność woluminów ZIP: "
                    f"oczekiwano {expected_name}, jest {part.filename}"
                )
        else:
            suffix = f".{index:03d}"
            if not part.filename.endswith(suffix):
                raise ValueError(f"Nieciągła albo błędna kolejność części: oczekiwano {suffix}, jest {part.filename}")
        part_path = parts_dir / part.filename
        if not part_path.exists() or not part_path.is_file():
            raise FileNotFoundError(f"Brak części: {part.filename}")
        if part.size_bytes is not None and part_path.stat().st_size != part.size_bytes:
            raise ValueError(f"Zły rozmiar części {part.filename}: jest {part_path.stat().st_size}, oczekiwano {part.size_bytes}")
        if part.sha256 and not skip_part_hash:
            actual = sha256_file(part_path)
            if actual != part.sha256:
                raise ValueError(f"Zły SHA256 części {part.filename}: {actual}, oczekiwano {part.sha256}")
    return expected, expected_full_sha, source


def join_split_package_to_zip(
    parts_dir: Path,
    base_zip_name: str,
    *,
    zip_out: Path | None = None,
    skip_part_hash: bool = False,
    force: bool = False,
    keep_existing: bool = False,
) -> Path:
    """Łączy części `.zip.001`, `.zip.002`, ... w jeden pełny ZIP."""
    parts_dir = parts_dir.expanduser().resolve()
    base_zip_name = sanitize_zip_name(base_zip_name)
    out_zip = zip_out.expanduser().resolve() if zip_out is not None else parts_dir / base_zip_name
    expected, expected_full_sha, source = validate_package_parts(parts_dir, base_zip_name, skip_part_hash=skip_part_hash)
    if expected and expected[0].filename == base_zip_name:
        if len(expected) == 1:
            return parts_dir / base_zip_name
        raise ValueError(
            "Ta paczka używa niezależnych woluminów ZIP. Każdy wolumin można otworzyć "
            "bez sklejania; --join-package dotyczy tylko dawnych binarnych części .zip.001."
        )

    section("Łączenie paczki w jeden ZIP")
    print(f"Folder części:              {parts_dir}")
    print(f"Nazwa ZIP:                  {base_zip_name}")
    print(f"Źródło listy części:         {source}")
    print(f"Części oczekiwane:           {len(expected)}")
    print(f"Pełny ZIP:                  {out_zip}")

    if out_zip.exists():
        if keep_existing:
            print("Pełny ZIP już istnieje; keep_existing=True, więc nie sklejam ponownie.")
        elif not force:
            raise FileExistsError(f"Pełny ZIP już istnieje: {out_zip}. Użyj force=True albo usuń plik.")
        else:
            out_zip.unlink()

    if not out_zip.exists():
        tmp = out_zip.with_name(out_zip.name + ".joining.tmp")
        if tmp.exists():
            tmp.unlink()
        total = max(len(expected), 1)
        with tmp.open("xb") as target:
            for index, part in enumerate(expected, start=1):
                print_bar(index - 1, total, label="Sklejanie")
                with (parts_dir / part.filename).open("rb") as src:
                    shutil.copyfileobj(src, target, length=8 * 1024 * 1024)
            print_bar(total, total, label="Sklejanie")
        os.replace(tmp, out_zip)

    if expected_full_sha:
        actual = sha256_file(out_zip)
        if actual != expected_full_sha:
            raise ValueError(f"Zły SHA256 pełnego ZIP-a: {actual}, oczekiwano {expected_full_sha}")
        print(f"SHA256 pełnego ZIP-a OK:    {actual}")
    else:
        print("UWAGA: brak oczekiwanego SHA256 pełnego ZIP-a; pomijam porównanie.")
    return out_zip


def unsafe_zip_member_name(name: str) -> str | None:
    """Zwraca powód, jeśli nazwa wpisu ZIP jest niebezpieczna do rozpakowania."""
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if name.startswith(("/", "\\")):
        return "absolute path"
    if len(name) >= 2 and name[1] == ":":
        return "drive path"
    if any(part == ".." for part in path.parts):
        return "parent traversal"
    if "\x00" in name:
        return "NUL byte"
    return None


def validate_zip_member_names(zf: zipfile.ZipFile) -> None:
    """Odmawia archiwum z nazwami, które mogłyby wyjść poza folder docelowy."""
    bad: list[str] = []
    for info in zf.infolist():
        reason = unsafe_zip_member_name(info.filename)
        if reason:
            bad.append(f"{info.filename!r}: {reason}")
    if bad:
        sample = "\n".join(bad[:20])
        more = "" if len(bad) <= 20 else f"\n... oraz {len(bad) - 20} więcej"
        raise ValueError("ZIP zawiera niebezpieczne ścieżki:\n" + sample + more)


def test_joined_zip(out_zip: Path, *, run_crc: bool = True) -> dict[str, object]:
    """Sprawdza central directory, nazwy wpisów i opcjonalnie pełny CRC/testzip."""
    out_zip = out_zip.expanduser().resolve()
    if not out_zip.exists() or not out_zip.is_file():
        raise FileNotFoundError(f"Nie znaleziono pełnego ZIP-a: {out_zip}")
    with zipfile.ZipFile(out_zip, "r") as zf:
        infos = zf.infolist()
        validate_zip_member_names(zf)
        if run_crc:
            bad = zf.testzip()
            if bad:
                raise ValueError(f"Błędny CRC/header wpisu ZIP: {bad}")
        return {
            "zip_path": str(out_zip),
            "entries": len(infos),
            "size_bytes": out_zip.stat().st_size,
            "crc_tested": bool(run_crc),
            "ok": True,
        }


def test_split_package(
    parts_dir: Path,
    base_zip_name: str,
    *,
    zip_out: Path | None = None,
    skip_part_hash: bool = False,
    join_if_missing: bool = True,
    force_join: bool = False,
    run_crc: bool = True,
) -> dict[str, object]:
    """Testuje części, strukturę ZIP i CRC bez tworzenia pełnego ZIP-a.

    Jeżeli pełny ZIP już istnieje, testuje go bezpośrednio. W przeciwnym razie
    używa `SplitPartsReader`, więc zwykły test nie dodaje żadnego pliku do
    folderu paczki. Parametr `join_if_missing` zachowano dla kompatybilności:
    wartość False nadal wymaga istniejącego pełnego ZIP-a.
    """
    parts_dir = parts_dir.expanduser().resolve()
    base_zip_name = sanitize_zip_name(base_zip_name)
    out_zip = zip_out.expanduser().resolve() if zip_out is not None else parts_dir / base_zip_name
    expected, expected_full_sha, source = validate_package_parts(
        parts_dir,
        base_zip_name,
        skip_part_hash=skip_part_hash,
    )

    report: dict[str, object] = {
        "ok": True,
        "parts_dir": str(parts_dir),
        "base_zip_name": base_zip_name,
        "parts_count": len(expected),
        "parts_source": source,
        "expected_full_sha256": expected_full_sha,
    }

    independent_windows_volumes = bool(expected and expected[0].filename == base_zip_name)
    if independent_windows_volumes:
        paths = [parts_dir / item.filename for item in expected]
        package_hash = hashlib.sha256()
        entries = 0
        seen_files: set[str] = set()
        for path in paths:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    package_hash.update(chunk)
            with zipfile.ZipFile(path, "r") as zf:
                validate_zip_member_names(zf)
                entries += len(zf.infolist())
                for info in zf.infolist():
                    if not info.is_dir():
                        if info.filename in seen_files:
                            raise ValueError(f"Duplikat pliku między woluminami: {info.filename}")
                        seen_files.add(info.filename)
                if run_crc:
                    bad = zf.testzip()
                    if bad:
                        raise ValueError(f"Błędny CRC/header wpisu ZIP: {bad}")
        actual_package_sha = package_hash.hexdigest()
        if expected_full_sha and actual_package_sha != expected_full_sha:
            raise ValueError(f"Zły SHA256 zestawu ZIP: {actual_package_sha}, oczekiwano {expected_full_sha}")
        report.update({
            "format": "independent_windows_zip_volumes",
            "zip_paths": [str(path) for path in paths],
            "entries": entries,
            "files": len(seen_files),
            "size_bytes": sum(path.stat().st_size for path in paths),
            "crc_tested": bool(run_crc),
            "actual_full_sha256": actual_package_sha,
            "tested_directly_from_parts": True,
            "requires_join": False,
        })
        return report

    if out_zip.exists():
        if expected_full_sha:
            actual = sha256_file(out_zip)
            if actual != expected_full_sha:
                if not force_join:
                    raise ValueError(
                        f"Istniejący pełny ZIP ma zły SHA256: {actual}, "
                        f"oczekiwano {expected_full_sha}"
                    )
                out_zip = join_split_package_to_zip(
                    parts_dir,
                    base_zip_name,
                    zip_out=out_zip,
                    skip_part_hash=skip_part_hash,
                    force=True,
                )
        report.update(test_joined_zip(out_zip, run_crc=run_crc))
        report["tested_directly_from_parts"] = False
        return report

    if not join_if_missing:
        raise FileNotFoundError(f"Pełny ZIP nie istnieje: {out_zip}")

    paths = [parts_dir / item.filename for item in expected]
    total_size = sum(path.stat().st_size for path in paths)
    actual_full_sha: str | None = None
    if expected_full_sha:
        digest = hashlib.sha256()
        for path in paths:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        actual_full_sha = digest.hexdigest()
        if actual_full_sha != expected_full_sha:
            raise ValueError(
                f"Zły SHA256 logicznego ZIP-a: {actual_full_sha}, "
                f"oczekiwano {expected_full_sha}"
            )

    with SplitPartsReader(paths) as reader:
        with zipfile.ZipFile(reader, "r") as zf:
            validate_zip_member_names(zf)
            entries = len(zf.infolist())
            if run_crc:
                bad = zf.testzip()
                if bad:
                    raise ValueError(f"Błędny CRC/header wpisu ZIP: {bad}")

    report.update({
        "zip_path": None,
        "entries": entries,
        "size_bytes": total_size,
        "crc_tested": bool(run_crc),
        "actual_full_sha256": actual_full_sha,
        "tested_directly_from_parts": True,
    })
    return report


def _current_package_parts_dir_and_name(state: WizardState) -> tuple[Path, str]:
    if state.out_dir is None:
        raise ValueError("Najpierw ustaw folder zapisu paczki.")
    if not state.archive_name:
        raise ValueError("Najpierw ustaw nazwę paczki ZIP.")
    return state.out_dir.expanduser().resolve(), sanitize_zip_name(state.archive_name)


def join_current_package_from_menu(state: WizardState) -> None:
    """Opcja menu: łączy aktualnie ustawioną paczkę w jeden pełny ZIP."""
    parts_dir, base_zip_name = _current_package_parts_dir_and_name(state)
    out_zip = parts_dir / base_zip_name
    force = False
    keep_existing = False
    if out_zip.exists():
        print(f"Pełny ZIP już istnieje: {out_zip}")
        if ask_bool("Użyć istniejącego ZIP-a bez ponownego sklejania", True, require_explicit=True):
            keep_existing = True
        else:
            force = ask_bool("Nadpisać pełny ZIP po ponownej walidacji części", False, require_explicit=True)
            if not force:
                print("Anulowano łączenie.")
                return
    out_path = join_split_package_to_zip(parts_dir, base_zip_name, force=force, keep_existing=keep_existing)
    print(f"Gotowe: {out_path}")


def test_current_package_from_menu(state: WizardState) -> None:
    """Opcja menu: testuje aktualnie ustawioną paczkę i pełny ZIP."""
    parts_dir, base_zip_name = _current_package_parts_dir_and_name(state)
    report = test_split_package(parts_dir, base_zip_name, join_if_missing=True, force_join=False, run_crc=True)
    section("Test paczki OK")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def windows_zip_volume_name(base_zip_name: str, volume_no: int) -> str:
    """Nazwa niezależnego woluminu ZIP otwieranego bez sklejania.

    Wolumin 1 zawsze zachowuje zwykłą nazwę `<nazwa>.zip`. Kolejne mają
    czytelne nazwy `<nazwa>.part002.zip`, `<nazwa>.part003.zip`, ... .
    """
    base_zip_name = sanitize_zip_name(base_zip_name)
    if volume_no < 1:
        raise ValueError("volume_no musi być >= 1")
    if volume_no == 1:
        return base_zip_name
    return f"{base_zip_name[:-4]}.part{volume_no:03d}.zip"


def discover_windows_zip_volumes(out_dir: Path, base_zip_name: str) -> list[Path]:
    """Zwraca istniejące woluminy nowego formatu w poprawnej kolejności."""
    out_dir = Path(out_dir).expanduser().resolve()
    base_zip_name = sanitize_zip_name(base_zip_name)
    paths: list[Path] = []
    first = out_dir / base_zip_name
    if first.exists() and first.is_file():
        paths.append(first)
    stem = base_zip_name[:-4]
    numbered = sorted(
        out_dir.glob(f"{stem}.part[0-9][0-9][0-9].zip"),
        key=lambda path: path.name.lower(),
    )
    paths.extend(path for path in numbered if path.is_file())
    return paths


def _windows_zip_output_paths(out_dir: Path, archive_name: str) -> list[Path]:
    out_dir = Path(out_dir).expanduser().resolve()
    archive_name = sanitize_zip_name(archive_name)
    stem = archive_name[:-4]
    candidates: set[Path] = set(discover_windows_zip_volumes(out_dir, archive_name))
    for pattern in (
        f"{archive_name}.*",
        f"{stem}.package-set.sha256",
        f"{stem}.extract_all.py",
    ):
        candidates.update(path for path in out_dir.glob(pattern) if path.is_file())
    return sorted(candidates)


def _prepare_windows_zip_outputs(out_dir: Path, archive_name: str, *, force: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = _windows_zip_output_paths(out_dir, archive_name)
    if existing and not force:
        sample = "\n".join(f"  - {path}" for path in existing[:25])
        more = "" if len(existing) <= 25 else f"\n  ... oraz {len(existing) - 25} więcej"
        raise FileExistsError(
            "Znaleziono wcześniejsze pliki wyjściowe. Włącz nadpisywanie albo zmień nazwę/folder.\n"
            + sample
            + more
        )
    if force:
        for path in existing:
            path.unlink()


def _split_file_group_by_weight(files: list[Path]) -> tuple[list[Path], list[Path]]:
    """Dzieli grupę możliwie równo według rozmiarów źródłowych."""
    if len(files) < 2:
        return files, []
    sizes: list[int] = []
    total = 0
    for path in files:
        try:
            size = max(0, path.stat().st_size)
        except OSError:
            size = 0
        sizes.append(size)
        total += size
    target = total / 2
    running = 0
    split_at = 1
    for index, size in enumerate(sizes[:-1], start=1):
        running += size
        split_at = index
        if running >= target:
            break
    split_at = max(1, min(split_at, len(files) - 1))
    return files[:split_at], files[split_at:]


def _initial_windows_volume_groups(files: list[Path], part_size: int) -> list[list[Path]]:
    """Szybki wstępny podział po rozmiarze źródłowym z marginesem ZIP."""
    if not files:
        return [[]]
    margin = min(
        max(64 * 1024, part_size // 100),
        16 * 1024 * 1024,
        max(1, part_size // 10),
    )
    budget = max(1, part_size - margin)
    groups: list[list[Path]] = []
    current: list[Path] = []
    current_size = 0
    for path in files:
        try:
            size = max(0, path.stat().st_size)
        except OSError:
            size = 0
        if current and current_size + size > budget:
            groups.append(current)
            current = []
            current_size = 0
        current.append(path)
        current_size += size
        if current_size >= budget:
            groups.append(current)
            current = []
            current_size = 0
    if current:
        groups.append(current)
    return groups or [[]]


def _write_standard_zip_candidate(
    *,
    source_folder: Path,
    candidate: Path,
    files: list[Path],
    dirs: list[Path],
    compression_level: int,
    collect_source_hashes: bool,
    virtual_files: dict[str, bytes] | None = None,
) -> tuple[list[str], int]:
    """Zapisuje zwykły, seekowalny ZIP zgodny z narzędziami Windows."""
    compression = zipfile.ZIP_DEFLATED
    source_hash_lines: list[str] = []
    candidate.parent.mkdir(parents=True, exist_ok=True)
    if candidate.exists():
        candidate.unlink()
    with zipfile.ZipFile(
        candidate,
        mode="w",
        compression=compression,
        allowZip64=True,
        compresslevel=compression_level,
        strict_timestamps=False,
    ) as zf:
        for directory in dirs:
            arc = rel_posix(directory, source_folder).rstrip("/") + "/"
            if arc != "./":
                zf.writestr(make_zipinfo_for_dir(directory, arc), b"")
        virtual_files = virtual_files or {}
        for src in files:
            arc = rel_posix(src, source_folder)
            file_hash = hashlib.sha256() if collect_source_hashes else None
            virtual_data = virtual_files.get(arc)
            size = len(virtual_data) if virtual_data is not None else src.stat().st_size
            zi = make_zipinfo_for_file(src, arc, compression, compression_level)
            zi.file_size = size
            zip64_limit = int(getattr(zipfile, "ZIP64_LIMIT", (1 << 31) - 1))
            with zf.open(
                zi,
                mode="w",
                force_zip64=size >= zip64_limit,
            ) as wf:
                if virtual_data is not None:
                    for offset in range(0, len(virtual_data), CHUNK_SIZE):
                        chunk = virtual_data[offset: offset + CHUNK_SIZE]
                        if file_hash is not None:
                            file_hash.update(chunk)
                        wf.write(chunk)
                else:
                    with src.open("rb") as rf:
                        while True:
                            chunk = rf.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            if file_hash is not None:
                                file_hash.update(chunk)
                            wf.write(chunk)
            if file_hash is not None:
                source_hash_lines.append(f"{file_hash.hexdigest()}  {arc}")
    return source_hash_lines, candidate.stat().st_size


def verify_independent_zip_volumes(
    *,
    source_folder: Path,
    plan: PackPlan,
    volume_paths: list[Path],
    run_crc: bool,
) -> dict[str, object]:
    """Sprawdza każdy niezależny ZIP i kompletność całego zestawu."""
    if not volume_paths:
        raise ValueError("Brak woluminów ZIP do weryfikacji")
    expected_files = {rel_posix(path, source_folder) for path in plan.files}
    found_files: set[str] = set()
    duplicates: list[str] = []
    entries_total = 0
    for path in volume_paths:
        if not zipfile.is_zipfile(path):
            raise ValueError(f"Plik nie jest poprawnym ZIP-em: {path}")
        with zipfile.ZipFile(path, "r") as zf:
            validate_zip_member_names(zf)
            infos = zf.infolist()
            entries_total += len(infos)
            if run_crc:
                bad = zf.testzip()
                if bad:
                    raise ValueError(f"Błędny CRC/header wpisu {bad!r} w {path.name}")
            for info in infos:
                if info.is_dir():
                    continue
                if info.filename in found_files:
                    duplicates.append(info.filename)
                found_files.add(info.filename)
    missing = sorted(expected_files - found_files)
    unexpected = sorted(found_files - expected_files)
    if duplicates or missing or unexpected:
        details: list[str] = []
        if duplicates:
            details.append("duplikaty: " + ", ".join(sorted(set(duplicates))[:10]))
        if missing:
            details.append("brakujące: " + ", ".join(missing[:10]))
        if unexpected:
            details.append("nadmiarowe: " + ", ".join(unexpected[:10]))
        raise ValueError("Niezgodny zestaw woluminów ZIP — " + "; ".join(details))
    return {
        "ok": True,
        "format": "independent_windows_zip_volumes",
        "volumes_count": len(volume_paths),
        "entries": entries_total,
        "files_verified": len(found_files),
        "crc_tested": bool(run_crc),
        "each_volume_is_zip": True,
    }


def write_independent_volume_extract_script(out_dir: Path, base_zip_name: str) -> Path:
    """Helper rozpakowujący wszystkie niezależne woluminy do jednego celu."""
    base_zip_name = sanitize_zip_name(base_zip_name)
    helper = out_dir / f"{base_zip_name}.extract_here.py"
    template = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath

BASE_ZIP_NAME = __BASE_ZIP_NAME__


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def unsafe_name(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if name.startswith(("/", "\\")):
        return "absolute path"
    if len(name) >= 2 and name[1] == ":":
        return "drive path"
    if any(part == ".." for part in path.parts):
        return "parent traversal"
    if "\x00" in name:
        return "NUL byte"
    return None


def volume_paths(parts_dir: Path) -> list[Path]:
    first = parts_dir / BASE_ZIP_NAME
    stem = BASE_ZIP_NAME[:-4]
    paths = [first] if first.exists() else []
    paths.extend(sorted(parts_dir.glob(f"{stem}.part[0-9][0-9][0-9].zip")))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Waliduj i rozpakuj wszystkie niezależne woluminy ZIP.")
    parser.add_argument("--parts-dir", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--destination", default="/mnt/data/jazn_runtime_current")
    parser.add_argument("--skip-part-hash", action="store_true")
    parser.add_argument("--skip-testzip", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    parts_dir = Path(args.parts_dir).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()
    paths = volume_paths(parts_dir)
    if not paths:
        raise SystemExit(f"Brak woluminów dla {BASE_ZIP_NAME}")

    manifest_path = parts_dir / f"{BASE_ZIP_NAME}.manifest.json"
    expected_hashes: dict[str, str] = {}
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        for item in data.get("parts") or []:
            expected_hashes[str(item.get("filename") or "")] = str(item.get("sha256") or "").lower()

    if destination.exists():
        if args.clean:
            dangerous = {Path("/").resolve(), Path("/mnt").resolve(), Path("/mnt/data").resolve()}
            if destination in dangerous:
                raise SystemExit(f"Odmawiam --clean dla zbyt szerokiego celu: {destination}")
            shutil.rmtree(destination)
        elif not args.force:
            raise SystemExit(f"Folder docelowy istnieje: {destination}; użyj --force albo --clean")
    destination.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    for index, path in enumerate(paths, start=1):
        expected = expected_hashes.get(path.name)
        if expected and not args.skip_part_hash:
            actual = sha256_file(path)
            if actual != expected:
                raise SystemExit(f"Zły SHA256 {path.name}: {actual}, oczekiwano {expected}")
        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                reason = unsafe_name(info.filename)
                if reason:
                    raise SystemExit(f"Niebezpieczna ścieżka {info.filename!r}: {reason}")
                if not info.is_dir() and info.filename in seen:
                    raise SystemExit(f"Duplikat pliku między woluminami: {info.filename}")
                if not info.is_dir():
                    seen.add(info.filename)
            if not args.skip_testzip:
                bad = zf.testzip()
                if bad:
                    raise SystemExit(f"Błędny CRC w {path.name}: {bad}")
            print(f"[{index}/{len(paths)}] Rozpakowuję {path.name}")
            zf.extractall(destination)
    print(f"Gotowe: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    helper.write_text(template.replace("__BASE_ZIP_NAME__", repr(base_zip_name)), encoding="utf-8")
    return helper


def create_split_zip_from_plan(
    *,
    source_folder: Path,
    out_dir: Path,
    archive_name: str,
    plan: PackPlan,
    part_size_mb: int,
    compression_level: int,
    force: bool,
    include_empty_dirs: bool,
    exclude_patterns: list[str],
    package_version: str,
    package_release_name: str,
    resolved_version_file: Path | None,
    archive_basename_requested: str,
    append_version_to_name: bool,
    disabled_default_excludes: list[str] | None = None,
    pack_profile: str = DEFAULT_PACK_PROFILE,
    include_prefixes: list[str] | None = None,
    artifact_mode: str = DEFAULT_ARTIFACT_MODE,
    verify_after_pack: bool = VERIFY_AFTER_PACK,
    verify_crc: bool = VERIFY_CRC_AFTER_PACK,
) -> dict[str, object]:
    source_folder = source_folder.resolve()
    out_dir = out_dir.resolve()
    archive_name = sanitize_zip_name(archive_name)
    artifact_mode = normalize_artifact_mode(artifact_mode)

    if not source_folder.exists() or not source_folder.is_dir():
        raise NotADirectoryError(f"Folder źródłowy nie istnieje albo nie jest folderem: {source_folder}")
    if is_relative_to(out_dir, source_folder):
        raise ValueError("Folder wyjściowy nie może znajdować się wewnątrz folderu źródłowego.")
    if part_size_mb <= 0:
        raise ValueError("Rozmiar części ZIP musi być większy od zera")

    part_size = part_size_mb * 1024 * 1024
    collect_source_hashes = artifact_mode == "diagnostic"
    integrity_virtual = build_integrity_manifest_virtual_file(
        source_folder,
        plan,
        package_version=package_version,
    )
    virtual_files: dict[str, bytes] = {}
    integrity_payload: dict[str, object] | None = None
    if integrity_virtual is not None:
        integrity_bytes, integrity_payload = integrity_virtual
        virtual_files[PACKAGE_INTEGRITY_MANIFEST_NAME] = integrity_bytes
    _prepare_windows_zip_outputs(out_dir, archive_name, force=force)

    section("Pakowanie")
    print(f"Źródło: {source_folder}")
    print(f"Wyjście: {out_dir}")
    print(f"Nazwa pierwszego ZIP-a: {archive_name}")
    print(f"Limit woluminu: {human_size(part_size)}")
    print(f"Plików: {plan.file_count}; katalogów: {plan.dir_count}; rozmiar źródła: {human_size(plan.source_total_size)}")
    if integrity_payload is not None:
        print(
            "Manifest integralności: świeży, zbudowany z dokładnego planu "
            f"({integrity_payload.get('file_count')} plików statycznych)"
        )
    print("Każdy plik wyjściowy będzie samodzielnym, zwykłym ZIP-em.")

    candidate_root = out_dir / f".{archive_name}.packing"
    if candidate_root.exists():
        shutil.rmtree(candidate_root)
    candidate_root.mkdir(parents=True, exist_ok=False)

    groups = _initial_windows_volume_groups(plan.files, part_size)
    accepted: list[tuple[Path, list[Path], list[Path], list[str], bool]] = []
    source_hash_lines: list[str] = []
    first_dirs_pending = list(plan.dirs) if include_empty_dirs else []

    def emit_group(files: list[Path], dirs: list[Path]) -> None:
        candidate = candidate_root / f"candidate-{len(accepted) + 1:04d}-{time.time_ns()}.zip"
        hashes, candidate_size = _write_standard_zip_candidate(
            source_folder=source_folder,
            candidate=candidate,
            files=files,
            dirs=dirs,
            compression_level=compression_level,
            collect_source_hashes=collect_source_hashes,
            virtual_files=virtual_files,
        )
        if candidate_size > part_size and len(files) > 1:
            candidate.unlink(missing_ok=True)
            left, right = _split_file_group_by_weight(files)
            emit_group(left, dirs)
            emit_group(right, [])
            return
        oversized = candidate_size > part_size
        accepted.append((candidate, list(files), list(dirs), hashes, oversized))

    try:
        total_groups = max(len(groups), 1)
        for index, group in enumerate(groups, start=1):
            print_bar(index - 1, total_groups, label="Pakowanie")
            dirs = first_dirs_pending
            first_dirs_pending = []
            emit_group(group, dirs)
        print_bar(total_groups, total_groups, label="Pakowanie")

        volume_paths: list[Path] = []
        parts: list[dict[str, object]] = []
        for volume_no, (candidate, files, dirs, hashes, oversized) in enumerate(accepted, start=1):
            final_path = out_dir / windows_zip_volume_name(archive_name, volume_no)
            os.replace(candidate, final_path)
            digest = sha256_file(final_path)
            volume_paths.append(final_path)
            source_hash_lines.extend(hashes)
            parts.append({
                "part_no": volume_no,
                "filename": final_path.name,
                "size_bytes": final_path.stat().st_size,
                "sha256": digest,
                "is_complete_zip": True,
                "source_file_count": len(files),
                "source_dir_count": len(dirs),
                "exceeds_configured_part_size": bool(oversized),
            })

        verification: dict[str, object] | None = None
        if verify_after_pack:
            subsection("Weryfikacja wygenerowanych ZIP-ów")
            verification = verify_independent_zip_volumes(
                source_folder=source_folder,
                plan=plan,
                volume_paths=volume_paths,
                run_crc=verify_crc,
            )
            if integrity_payload is not None:
                integrity_verification = verify_integrity_manifest_in_generated_volumes(
                    source_folder,
                    volume_paths,
                    allowed_unprotected_prefixes=("memory/",) if pack_profile == "full" else (),
                )
                verification["package_integrity_manifest"] = integrity_verification
            print(
                "Weryfikacja OK: "
                f"{verification['volumes_count']} ZIP, "
                f"{verification['files_verified']} plików, "
                f"CRC={'OK' if verification['crc_tested'] else 'pominięty'}"
                + (", manifest=OK" if integrity_payload is not None else "")
            )

        package_set_hash = hashlib.sha256()
        total_output_size = 0
        for path in volume_paths:
            total_output_size += path.stat().st_size
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    package_set_hash.update(chunk)
        package_set_sha = package_set_hash.hexdigest()
        single_zip_sha = parts[0]["sha256"] if len(parts) == 1 else None

        manifest: dict[str, object] = {
            "schema_version": "jazn_windows_zip_volumes/v1",
            "created_at": now_iso(),
            "script": Path(__file__).name,
            "script_version": f"v{VERSION}",
            "package_version": package_version,
            "package_release_name": package_release_name,
            "version_file": str(resolved_version_file) if resolved_version_file else None,
            "archive_basename_requested": archive_basename_requested,
            "append_version_to_name": append_version_to_name,
            "source_folder": str(source_folder),
            "output_dir": str(out_dir),
            "archive_name": archive_name,
            "first_volume_name": volume_paths[0].name,
            "part_size_bytes": part_size,
            "part_size_human": human_size(part_size),
            "compression": "ZIP_DEFLATED",
            "compression_level": compression_level,
            "zip64_enabled": True,
            "multipart_zip_native": False,
            "split_method": "independent_valid_zip_volumes_by_file_boundary",
            "windows_explorer_compatible": True,
            "requires_join_before_open": False,
            "artifact_mode": artifact_mode,
            "source_file_count": plan.file_count,
            "source_dir_count": plan.dir_count,
            "source_total_size_bytes": plan.source_total_size,
            "output_size_bytes": total_output_size,
            "package_set_sha256": package_set_sha,
            "single_zip_sha256": single_zip_sha,
            "logical_full_zip_size_bytes": total_output_size,
            "logical_full_zip_sha256": package_set_sha,
            "parts_count": len(parts),
            "volumes_count": len(parts),
            "parts": parts,
            "pack_profile": pack_profile,
            "include_prefixes": list(include_prefixes or []),
            "exclude_patterns": exclude_patterns,
            "profile_default_exclude_patterns": as_str_list(PACK_PROFILES.get(pack_profile, PACK_PROFILES[DEFAULT_PACK_PROFILE]).get("exclude_patterns")),
            "disabled_default_exclude_patterns": list(disabled_default_excludes or []),
            "include_empty_dirs": include_empty_dirs,
            "plan_generated_at": plan.generated_at,
            "excluded_count": len(plan.excluded),
            "verification": verification,
            "package_integrity_manifest_refreshed": integrity_payload is not None,
            "package_integrity_manifest_version": (
                str(integrity_payload.get("runtime_version") or integrity_payload.get("version") or "")
                if integrity_payload is not None
                else None
            ),
            "package_integrity_manifest_file_count": (
                integrity_payload.get("file_count") if integrity_payload is not None else None
            ),
        }

        generated_sidecars: list[str] = []
        if artifact_mode == "diagnostic":
            parts_sha_path = out_dir / f"{archive_name}.parts.sha256"
            full_sha_path = out_dir / f"{archive_name}.sha256"
            source_sha_path = out_dir / f"{archive_name}.source_files.sha256"
            manifest_path = out_dir / f"{archive_name}.manifest.json"
            extract_script = write_independent_volume_extract_script(out_dir, archive_name)

            parts_sha_path.write_text(
                "\n".join(f"{part['sha256']}  {part['filename']}" for part in parts) + "\n",
                encoding="ascii",
            )
            if len(parts) == 1:
                full_sha_path.write_text(f"{parts[0]['sha256']}  {archive_name}\n", encoding="ascii")
            else:
                full_sha_path.write_text(
                    f"{package_set_sha}  {archive_name[:-4]}.package-set\n",
                    encoding="ascii",
                )
            source_sha_path.write_text("\n".join(source_hash_lines) + "\n", encoding="utf-8")
            manifest.update({
                "source_hash_file": source_sha_path.name,
                "parts_hash_file": parts_sha_path.name,
                "package_hash_file": full_sha_path.name,
                "extract_here_script": extract_script.name,
            })
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            generated_sidecars = [
                parts_sha_path.name,
                full_sha_path.name,
                source_sha_path.name,
                manifest_path.name,
                extract_script.name,
            ]

        manifest["generated_sidecars"] = generated_sidecars

        section("Gotowe")
        if len(parts) == 1:
            print(f"ZIP: {parts[0]['filename']} ({human_size(int(parts[0]['size_bytes']))})")
            print("Paczka nie przekroczyła limitu — nie utworzono żadnych sztucznych części.")
        else:
            print(f"Woluminy ZIP: {len(parts)}")
            for part in parts:
                oversize_note = " — pojedynczy duży plik" if part["exceeds_configured_part_size"] else ""
                print(f"  - {part['filename']} ({human_size(int(part['size_bytes']))}){oversize_note}")
            print("Każdy wolumin jest samodzielnym ZIP-em; nie trzeba ich sklejać przed otwarciem.")
        print(f"Łączny rozmiar ZIP-ów: {human_size(total_output_size)}")
        print(f"SHA256 zestawu: {package_set_sha}")
        if artifact_mode == "parts_only":
            print("Pliki dodatkowe: nie utworzono — tylko zwykłe pliki ZIP")
        else:
            print("Pliki diagnostyczne:")
            for name in generated_sidecars:
                print(f"  - {name}")
        return manifest
    except BaseException:
        cleanup_archive_outputs(out_dir, archive_name)
        raise
    finally:
        shutil.rmtree(candidate_root, ignore_errors=True)

def create_split_zip(
    source_folder: Path,
    out_dir: Path,
    archive_basename: str,
    part_size_mb: int,
    compression_level: int,
    *,
    force: bool,
    include_empty_dirs: bool,
    exclude_patterns: list[str],
    append_version_to_name: bool = APPEND_VERSION_TO_NAME,
    version_file: str | Path | None = None,
    disabled_default_excludes: list[str] | None = None,
    pack_profile: str = DEFAULT_PACK_PROFILE,
    include_prefixes: list[str] | None = None,
    artifact_mode: str = DEFAULT_ARTIFACT_MODE,
    verify_after_pack: bool = VERIFY_AFTER_PACK,
    verify_crc: bool = VERIFY_CRC_AFTER_PACK,
) -> dict[str, object]:
    source_folder = source_folder.resolve()
    out_dir = out_dir.resolve()
    resolved_version_file = find_version_file(source_folder, version_file)
    package_version = read_version_from_py(resolved_version_file)
    package_release_name = normalize_release_name(
        read_optional_string_from_py(resolved_version_file, RELEASE_NAME_VARIABLES) or PACKAGE_RELEASE_NAME
    )
    archive_name = apply_version_to_archive_name(
        archive_basename,
        package_version,
        package_release_name=package_release_name,
        enabled=append_version_to_name,
    )
    plan = discover_pack_plan(source_folder, include_empty_dirs, exclude_patterns, include_prefixes or [])
    return create_split_zip_from_plan(
        source_folder=source_folder,
        out_dir=out_dir,
        archive_name=archive_name,
        plan=plan,
        part_size_mb=part_size_mb,
        compression_level=compression_level,
        force=force,
        include_empty_dirs=include_empty_dirs,
        exclude_patterns=exclude_patterns,
        package_version=package_version,
        package_release_name=package_release_name,
        resolved_version_file=resolved_version_file,
        archive_basename_requested=archive_basename,
        append_version_to_name=append_version_to_name,
        disabled_default_excludes=disabled_default_excludes,
        pack_profile=pack_profile,
        include_prefixes=include_prefixes or [],
        artifact_mode=artifact_mode,
        verify_after_pack=verify_after_pack,
        verify_crc=verify_crc,
    )

# =============================================================================
# TRYB INTERAKTYWNY
# =============================================================================


def require_ready_state(state: WizardState) -> None:
    if state.source_folder is None:
        raise ValueError("Najpierw ustaw ścieżkę źródłową")
    if state.out_dir is None:
        raise ValueError("Najpierw ustaw folder wyjściowy")
    if not state.archive_name:
        raise ValueError("Najpierw ustaw nazwę archiwum")




def apply_source_path_text(state: WizardState, raw: str) -> bool:
    """Ustawia folder źródłowy z podanego tekstu. Zwraca True po sukcesie."""
    control = _plain_control_word(raw)
    if control == "exit":
        raise UserRequestedAppExit()
    if control == "cancel":
        raise UserCancelledInput()
    text = normalize_path_text(raw)
    if not text:
        raise UserCancelledInput()
    path = Path(text).expanduser()
    if not path.exists() or not path.is_dir():
        print(f"BŁĄD: folder nie istnieje albo nie jest folderem: {path}")
        return False

    candidate = path.resolve()
    try:
        resolved_version_file, package_version, package_release_name = read_source_version_info(candidate, state.version_file)
    except Exception as exc:
        print(f"BŁĄD: {exc}")
        print("Nie ustawiono folderu do pakowania.")
        return False

    state.source_folder = candidate
    state.resolved_version_file = resolved_version_file
    state.package_version = package_version
    state.package_release_name = package_release_name
    state.archive_name = apply_version_to_archive_name(
        state.archive_basename_requested or ARCHIVE_BASENAME,
        state.package_version,
        package_release_name=state.package_release_name,
        enabled=True,
    )
    state.plan = None
    print(f"Ustawiono źródło: {state.source_folder}")
    print(f"Plik version.py: {state.resolved_version_file}")
    print(f"Wersja: {state.package_version}")
    if state.package_release_name:
        print(f"Release: {state.package_release_name}")
    print(f"Proponowana nazwa ZIP: {state.archive_name}")
    return True


def apply_output_path_text(state: WizardState, raw: str) -> bool:
    """Ustawia folder wyjściowy z podanego tekstu. Zwraca True po sukcesie."""
    control = _plain_control_word(raw)
    if control == "exit":
        raise UserRequestedAppExit()
    if control == "cancel":
        raise UserCancelledInput()
    text = normalize_path_text(raw)
    if not text:
        raise UserCancelledInput()
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path)
    path = path.resolve()
    if state.source_folder is not None and is_relative_to(path, state.source_folder):
        print("BŁĄD: folder wyjściowy nie może być wewnątrz folderu źródłowego.")
        return False
    state.out_dir = path
    state.plan = None
    print(f"Ustawiono wyjście: {state.out_dir}")
    return True

def configure_source(state: WizardState) -> None:
    section("1. Ścieżka do folderu do spakowania")
    default = path_edit_default_path(state.source_folder or SOURCE_FOLDER or None)
    print("Folder źródłowy musi być rootem projektu/runtime Jaźni.")
    print("Wymagany plik wersji: .\\latka_jazn\\version.py")
    print(f"Proponowana ścieżka startowa: {default}")
    while True:
        try:
            raw = ask_path_text("Folder źródłowy", default, only_directories=True)
            path = Path(raw).expanduser()
        except UserCancelledInput:
            print("Anulowano zmianę folderu do pakowania.")
            return
        except ValueError as exc:
            print(f"BŁĄD: {exc}")
            continue
        if apply_source_path_text(state, str(path)):
            return


def configure_output(state: WizardState) -> None:
    section("2. Folder zapisu")
    default = path_edit_default_path(state.out_dir, output_package_folder=True)
    if state.out_dir is None:
        print("Folder zapisu paczki nie jest jeszcze ustawiony.")
        print("Domyślnie proponuję folder pakiet obok generatora; możesz wpisać inną ścieżkę.")
    print(f"Proponowana ścieżka startowa: {default}")
    while True:
        try:
            raw = ask_path_text("Folder wyjściowy", default, only_directories=True)
        except UserCancelledInput:
            print("Anulowano zmianę folderu zapisu.")
            return
        except ValueError as exc:
            print(f"BŁĄD: {exc}")
            continue
        if apply_output_path_text(state, raw):
            return


def configure_name(state: WizardState) -> None:
    section("3. Nazwa paczki ZIP z możliwością edycji")

    generated_name = ""
    if state.source_folder is not None:
        # Odśwież wersję/release, ale nie mieszaj ręcznej nazwy z automatycznym sufiksem.
        refresh_version_and_default_name(state, keep_custom_name=True)
        print(f"Wersja z version.py: {state.package_version}")
        if state.package_release_name:
            print(f"Release z version.py: {state.package_release_name}")
        generated_name = apply_version_to_archive_name(
            state.archive_basename_requested or ARCHIVE_BASENAME,
            state.package_version,
            package_release_name=state.package_release_name,
            enabled=True,
        )
    else:
        print("Folder do pakowania nie jest jeszcze ustawiony.")
        print("Możesz wpisać ręczną nazwę ZIP teraz albo wrócić i najpierw ustawić folder.")
        generated_name = sanitize_zip_name(state.archive_basename_requested or ARCHIVE_BASENAME)

    current_or_generated = state.archive_name or generated_name
    print(f"Proponowana nazwa automatyczna: {generated_name}")
    if state.archive_name_manual and state.archive_name:
        print(f"Obecna nazwa ręczna:          {state.archive_name}")
    print("Ręczna nazwa jest używana dokładnie jako Twoja nazwa: spacje → '_'. Rozszerzenie .zip nie jest wymagane — dodam je tylko, gdy go brakuje.")

    while True:
        try:
            if ACTIVE_UI_MODE == "cursor" and HAS_PROMPT_TOOLKIT and sys.stdin.isatty():
                raw = ask_edit_text(
                    "Nazwa ZIP",
                    current_or_generated,
                    bottom_toolbar="Esc = wróć; Ctrl+X = zamknij bez zapisu; Enter = użyj propozycji; spacje → _",
                )
                if not raw.strip():
                    normalized = sanitize_zip_name(current_or_generated)
                    manual = bool(state.archive_name_manual and state.archive_name)
                else:
                    normalized = sanitize_zip_name(raw)
                    manual = raw.strip() != generated_name
            else:
                raw = ask_text("Nazwa ZIP po złożeniu", current_or_generated)
                if not raw.strip():
                    normalized = sanitize_zip_name(current_or_generated)
                    manual = bool(state.archive_name_manual and state.archive_name)
                else:
                    normalized = sanitize_zip_name(raw)
                    manual = raw.strip() != generated_name
        except UserCancelledInput:
            print("Anulowano edycję nazwy paczki ZIP.")
            return
        try:
            # Dodatkowa walidacja po normalizacji, żeby komunikat był jednoznaczny.
            normalized = sanitize_zip_name(normalized)
        except ValueError as exc:
            print(f"BŁĄD: {exc}")
            continue
        state.archive_name = normalized
        state.archive_name_manual = manual
        state.plan = None
        print(f"Ustawiono nazwę: {state.archive_name}")
        return

def profile_menu_label(state: WizardState) -> str:
    """Pełna, czytelna nazwa profilu do pokazania w menu głównym."""
    return state.profile_label()


def pack_profile_short_label(state: WizardState) -> str:
    labels = {
        "pelna": "system + pamięć osobno",
        "system": "sam system",
        "memory": "sama pamięć",
    }
    return labels.get(state.pack_profile, state.pack_profile)


def menu_value(value: object | None, *, empty: str = "BRAK") -> str:
    text = str(value or "").strip()
    return text if text else empty


def settings_origin_label(state: WizardState) -> str:
    """Źródło konfiguracji widoczne w statusie.

    Sam automatyczny zapis ustawień przy starcie nie oznacza jeszcze, że użytkownik
    świadomie wybrał własny zestaw. Dlatego o ustawieniach użytkownika mówimy
    dopiero wtedy, gdy plik JSON faktycznie został wczytany.
    """
    if state.settings_loaded_from:
        return "użytkownika"
    return "profilu"


def settings_have_pack_list_changes(state: WizardState) -> bool:
    """Czy plik ustawień wnosi realne, użytkowe zmiany dla listy pakowania.

    Sam fakt istnienia `__jazn_pack_generator_settings.json` nie powinien oznaczać w menu
    "użytkownika", jeśli plik zawiera tylko wartości domyślne i puste ścieżki.
    """
    if not state.settings_loaded_from:
        return False
    return any([
        state.source_folder is not None,
        state.out_dir is not None,
        bool(state.archive_name),
        bool(state.custom_excludes),
        bool(state.disabled_default_excludes),
        state.pack_profile != DEFAULT_PACK_PROFILE,
        state.use_default_excludes is not True,
        state.use_custom_excludes is not True,
        state.part_size_mb != PART_SIZE_MB,
        state.compression_level != COMPRESSION_LEVEL,
        state.force != FORCE_OVERWRITE,
        state.include_empty_dirs != INCLUDE_EMPTY_DIRS,
    ])


def pack_list_settings_label(state: WizardState) -> str:
    """Etykieta dla opcji 1 — co steruje listą pakowania."""
    if state.source_folder is None:
        return "najpierw folder"
    if state.out_dir is None:
        return "najpierw zapis"
    if settings_have_pack_list_changes(state):
        return "użytkownika"
    return f"profil: {pack_profile_short_label(state)}"


def plan_status_label(state: WizardState) -> str:
    if state.plan is None:
        return "brak"
    return f"{state.plan.file_count} plików / {human_size(state.plan.source_total_size)}"


def pack_settings_counter(state: WizardState) -> tuple[int, int]:
    """Licznik skonfigurowanych ustawień paczki dla menu głównego.

    Liczymy pięć głównych pól edytowalnych w sekcji 'Ustawienia paczki':
    profil, rozmiar części, poziom kompresji, puste katalogi, nadpisywanie.
    Wartości boolowskie też są ustawieniami — nawet gdy są wyłączone.
    """
    checks = [
        bool(state.pack_profile),
        int(state.part_size_mb) > 0,
        0 <= int(state.compression_level) <= 9,
        isinstance(state.include_empty_dirs, bool),
        isinstance(state.force, bool),
    ]
    return sum(1 for item in checks if item), len(checks)


def pack_settings_menu_label(state: WizardState) -> str:
    configured, total = pack_settings_counter(state)
    return f"ustawione {configured}/{total}"


def on_off_label(value: bool) -> str:
    return "ON" if value else "OFF"


def exclusions_menu_label(state: WizardState) -> str:
    default_total = len(state.profile_default_excludes())
    default_active = len(state.active_default_excludes())
    custom_total = len(state.custom_excludes)
    # Gdy nie ma żadnych ręcznych wzorców, pokazujemy manualne OFF 0,
    # nawet jeśli techniczna flaga use_custom_excludes jest włączona.
    custom_label = on_off_label(bool(state.use_custom_excludes and custom_total > 0))
    return (
        f"domyślne {on_off_label(state.use_default_excludes)} {default_active}/{default_total}, "
        f"manualne {custom_label} {custom_total}"
    )


def manual_exclusions_label(state: WizardState) -> str:
    return f"{on_off_label(state.use_custom_excludes)}, wpisów {len(state.custom_excludes)}"



def normalize_ui_mode(value: str) -> str:
    """Normalizuje publiczne nazwy trybów UI.

    Od v5.4 tryby wybierane przez użytkownika są tylko dwa: tekstowy i kursorowy.
    `auto` zostaje rozpoznawane wyłącznie jako kompatybilność ze starszymi
    ustawieniami/argumentami i jest rozwiązywane do konkretnego trybu.
    """
    raw = str(value or "plain").strip().lower()
    mapping = {
        "auto": "auto",
        "a": "auto",
        "3": "auto",
        "tekst": "plain",
        "tekstowy": "plain",
        "text": "plain",
        "plain": "plain",
        "txt": "plain",
        "lista": "plain",
        "t": "plain",
        "1": "plain",
        "kursor": "cursor",
        "kursorowy": "cursor",
        "cursor": "cursor",
        "c": "cursor",
        "2": "cursor",
    }
    return mapping.get(raw, "plain")


def resolve_auto_ui_mode() -> str:
    """Zamienia preferencję auto na realny tryb działający w bieżącym terminalu."""
    return "cursor" if HAS_PROMPT_TOOLKIT and sys.stdin.isatty() else "plain"


def ui_mode_label(ui_mode: str) -> str:
    mode = normalize_ui_mode(ui_mode)
    if mode == "auto":
        mode = resolve_auto_ui_mode()
    if mode == "cursor":
        return "kursorowy"
    return "tekstowy"


def ui_mode_setting_label(state: WizardState, active_ui_mode: str | None = None) -> str:
    saved = normalize_ui_mode(state.ui_mode or active_ui_mode or "plain")
    if saved == "auto":
        saved = resolve_auto_ui_mode()
    auto = "ON" if state.ui_auto_start else "OFF"
    active = ui_mode_label(active_ui_mode or saved)
    return f"zapisany: {ui_mode_label(saved)}, auto-start: {auto}, aktywny: {active}"


def settings_file_available_for_auto(state: WizardState | None = None) -> bool:
    """Auto w ekranie startowym pokazujemy dopiero, gdy istnieją ustawienia."""
    if state is not None and state.settings_loaded_from is not None:
        return True
    try:
        return settings_path().exists()
    except OSError:
        return False


def _refresh_optional_ui_imports() -> None:
    """Odświeża import prompt_toolkit po ewentualnej instalacji pip."""
    global _pt_prompt, _pt_Application, _pt_PathCompleter, _pt_KeyBindings
    global _pt_Layout, _pt_Window, _pt_FormattedTextControl, _pt_CompleteStyle
    global _pt_Style, HAS_PROMPT_TOOLKIT

    try:  # pragma: no cover - zależne od środowiska użytkownika
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.application import Application as pt_Application
        from prompt_toolkit.completion import PathCompleter as pt_PathCompleter
        from prompt_toolkit.key_binding import KeyBindings as pt_KeyBindings
        from prompt_toolkit.layout import Layout as pt_Layout
        from prompt_toolkit.layout.containers import Window as pt_Window
        from prompt_toolkit.layout.controls import FormattedTextControl as pt_FormattedTextControl
        from prompt_toolkit.shortcuts import CompleteStyle as pt_CompleteStyle
        from prompt_toolkit.styles import Style as pt_Style
        _pt_prompt = pt_prompt
        _pt_Application = pt_Application
        _pt_PathCompleter = pt_PathCompleter
        _pt_KeyBindings = pt_KeyBindings
        _pt_Layout = pt_Layout
        _pt_Window = pt_Window
        _pt_FormattedTextControl = pt_FormattedTextControl
        _pt_CompleteStyle = pt_CompleteStyle
        _pt_Style = pt_Style
        HAS_PROMPT_TOOLKIT = True
    except Exception:  # pragma: no cover
        _pt_prompt = None
        _pt_Application = None
        _pt_PathCompleter = None
        _pt_KeyBindings = None
        _pt_Layout = None
        _pt_Window = None
        _pt_FormattedTextControl = None
        _pt_CompleteStyle = None
        _pt_Style = None
        HAS_PROMPT_TOOLKIT = False


def _missing_required_ui_packages(ui_mode: str) -> list[str]:
    mode = normalize_ui_mode(ui_mode)
    if mode == "cursor":
        return [] if HAS_PROMPT_TOOLKIT else ["prompt_toolkit"]
    return []


def install_optional_ui_packages(packages: list[str]) -> bool:
    """Instaluje prompt_toolkit po zgodzie użytkownika."""
    if not packages:
        return True
    command = [sys.executable, "-m", "pip", "install", *packages]
    print("\nUruchamiam:")
    print("  " + " ".join(command))
    try:
        subprocess.check_call(command)
    except Exception as exc:
        print(f"\nNie udało się doinstalować biblioteki UI: {exc}")
        return False
    _refresh_optional_ui_imports()
    if not HAS_PROMPT_TOOLKIT:
        print("\nInstalacja zakończona, ale nadal nie mogę zaimportować prompt_toolkit.")
        return False
    print("\nMenu kursorowe jest dostępne.")
    return True



def choose_ui_mode_interactively(ui_mode: str | None, state: WizardState | None = None) -> str:
    """Wybiera tryb UI przy starcie.

    Widoczny wybór ma tylko dwie pozycje: TXT albo Kursorowy.
    Automatyczny start nie jest osobnym trybem, tylko flagą `ui_auto_start`:
    kiedy jest włączona i ustawienia zawierają zapisany tryb, aplikacja startuje
    bez ekranu wyboru.
    """
    explicit = ui_mode is not None and str(ui_mode).strip() != ""
    if explicit:
        mode = normalize_ui_mode(ui_mode or "plain")
        return resolve_auto_ui_mode() if mode == "auto" else mode

    saved = normalize_ui_mode(state.ui_mode) if state is not None and state.ui_mode else "plain"
    if saved == "auto":
        saved = resolve_auto_ui_mode()

    if state is not None and state.settings_loaded_from is not None and state.ui_auto_start:
        return "settings"

    default_choice = "2" if saved == "cursor" else "1"

    if not sys.stdin.isatty():
        return saved

    section("Wybór interfejsu")
    print("Wybierz tryb pracy aplikacji:")
    print("  1. TXT / tekstowy — najprostszy, działa bez dodatkowych bibliotek")
    if HAS_PROMPT_TOOLKIT:
        print("  2. Kursorowy — menu terminalowe ze strzałkami")
    else:
        print("  2. Kursorowy — wymaga prompt_toolkit; aplikacja zapyta o instalację albo wróci do tekstowego")
    print()
    print("Po wyborze zapiszę ten tryb i włączę automatyczne użycie zapisanego interfejsu przy następnym starcie.")
    print(f"Enter = {default_choice}. Ctrl+X = zakończ.")

    prompt_text = f"Tryb UI [1 TXT / 2 Kursorowy; Enter={default_choice}]: "
    if HAS_PROMPT_TOOLKIT and _pt_prompt is not None:
        try:
            choice = _pt_prompt(
                prompt_text,
                default="",
                key_bindings=startup_ui_key_bindings(),
                wrap_lines=False,
            ).strip().lower()
        except UserCancelledInput:
            return "plain"
        except UserRequestedAppExit:
            raise
        except Exception:
            choice = input(prompt_text).strip().lower()
    else:
        choice = input(prompt_text).strip().lower()

    control = _plain_control_word(choice)
    if control == "exit":
        raise UserRequestedAppExit()
    if not choice:
        choice = default_choice
    if choice in {"2", "k", "kursor", "kursorowy", "cursor"}:
        return "cursor"
    return "plain"

def resolve_ui_mode_with_optional_install(ui_mode: str | None, state: WizardState | None = None) -> str:
    """Ustala aktywny tryb interfejsu i zapisuje preferencję w stanie sesji."""
    global ACTIVE_UI_MODE
    preference_raw = choose_ui_mode_interactively(ui_mode, state)
    preserve_saved_preference = preference_raw == "settings"

    if preserve_saved_preference:
        preference = normalize_ui_mode(state.ui_mode if state is not None else "plain")
    else:
        preference = normalize_ui_mode(preference_raw)

    if preference == "auto":
        preference = resolve_auto_ui_mode()
    mode = preference

    missing = _missing_required_ui_packages(mode)
    if missing and not sys.stdin.isatty():
        print("prompt_toolkit nie jest dostępny. Uruchamiam tryb tekstowy.")
        mode = "plain"
        if preference == "cursor":
            preference = "plain"
            preserve_saved_preference = False
    elif missing:
        section("Tryb kursorowy")
        print("Tryb kursorowy wymaga biblioteki prompt_toolkit.")
        print("Opcje:")
        print("  1. Doinstaluj prompt_toolkit przez pip")
        print("  2. Uruchom tryb tekstowy")
        install_choice = ask_text("Wybór", "2").strip().lower()
        if install_choice in {"1", "t", "tak", "y", "yes"} and install_optional_ui_packages(missing):
            mode = "cursor"
            preference = "cursor"
        else:
            print("Uruchamiam tryb tekstowy.")
            mode = "plain"
            if preference == "cursor":
                preference = "plain"
                preserve_saved_preference = False

    ACTIVE_UI_MODE = mode
    if state is not None and not preserve_saved_preference:
        state.ui_mode = preference
        # Każdy ręczny wybór TXT/Kursorowy przy starcie oznacza: od kolejnego
        # uruchomienia używaj tego zapisanego trybu bez pytania.
        state.ui_auto_start = True
    return mode

def menu_options(state: WizardState) -> list[tuple[str, str]]:
    return [
        ("1", f"1. Profil pakowania [{profile_menu_label(state)}]"),
        ("2", f"2. Pokaż listę do spakowania [{pack_list_settings_label(state)}]"),
        ("3", f"3. Folder do pakowania [{menu_value(state.source_folder)}]"),
        ("4", f"4. Folder zapisu paczki [{menu_value(state.out_dir)}]"),
        ("5", f"5. Zmień nazwę paczki [{state.archive_name or 'nie ustawiono'}]"),
        ("6", "6. Ustawienia"),
        ("7", "7. Pakuj teraz"),
        ("0", "0. Wyjście"),
    ]


def default_menu_choice(state: WizardState) -> str:
    """Ustawia sensowną domyślną akcję w menu."""
    if state.source_folder is None:
        return "3"
    if state.out_dir is None:
        return "4"
    if not state.archive_name:
        return "5"
    if state.plan is not None:
        return "7"
    return "2"

def print_menu_plain(state: WizardState) -> None:
    print("\n" + "=" * 78)
    print("  MENU GŁÓWNE")
    print("=" * 78)
    for _, label in menu_options(state):
        print(f"  {label}")


def _cursor_menu_lines(state: WizardState, selected_index: int) -> list[tuple[str, str]]:
    """Buduje główne menu kursorowe."""
    options = menu_options(state)
    selected_index = max(0, min(selected_index, len(options) - 1))
    width = 78
    fragments: list[tuple[str, str]] = []

    def part(style: str, text: str) -> None:
        fragments.append((style, text))

    def line(style: str, text: str = "") -> None:
        part(style, text)
        part("", "\n")

    line("class:border", "=" * width)
    line("class:title", f"  Jaźń / Łatka — generator paczki ZIP v{VERSION}")
    line("class:border", "=" * width)
    line("", "")

    for label, value in (
        ("Plan:     ", plan_status_label(state)),
        ("Profil:   ", state.profile_label()),
        ("Źródło:   ", menu_value(state.source_folder)),
        ("Zapis:    ", menu_value(state.out_dir)),
        ("ZIP:      ", state.archive_name or "(nie ustawiono)"),
    ):
        part("class:status.label", label)
        line("class:status.value", value)

    line("", "")
    line("class:hint", "↑/↓ wybór | Enter OK | Esc odśwież | Ctrl+X zamknij bez zapisu | 0 wyjście")
    line("", "")

    for idx, (_, label) in enumerate(options):
        marker = "▶" if idx == selected_index else " "
        style = "class:latka.selected" if idx == selected_index else "class:latka.option"
        line(style, f"  {marker} {label}")

    return fragments


def should_use_cursor_menu(ui_mode: str) -> bool:
    """Czy bieżący terminal może użyć menu kursorowego."""
    return normalize_ui_mode(ui_mode) == "cursor" and prompt_toolkit_parts() is not None


def ask_menu_choice_cursor(state: WizardState, default: str) -> str:
    """Główne menu kursorowe oparte o prompt_toolkit Application."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return ask_text("Wybór", default)
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts

    options = menu_options(state)
    keys = [key for key, _ in options]
    selected = {"index": keys.index(default) if default in keys else 0}

    def get_text() -> list[tuple[str, str]]:
        return _cursor_menu_lines(state, selected["index"])

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        selected["index"] = (selected["index"] + delta) % len(options)
        event.app.invalidate()

    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None:
        move(-1, event)

    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None:
        move(1, event)

    @kb.add("home")
    def _home(event: Any) -> None:
        selected["index"] = 0
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        selected["index"] = len(options) - 1
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        event.app.exit(result=options[selected["index"]][0])

    @kb.add("escape")
    def _escape_no_close(event: Any) -> None:
        event.app.invalidate()

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key, eager=True)
            def _number(event: Any, option_key: str = option_key) -> None:
                event.app.exit(result=option_key)

    style = Style.from_dict({
        "border": "ansicyan",
        "title": "bold ansicyan",
        "hint": "ansibrightblack",
        "status.label": "bold",
        "status.value": "",
        "latka.option": "",
        "latka.selected": "reverse bold",
    })

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=True,
        mouse_support=False,
    )
    result = app.run()
    return str(result or default)


def ask_menu_choice(state: WizardState, default: str, ui_mode: str) -> str:
    if should_use_cursor_menu(ui_mode):
        try:
            return ask_menu_choice_cursor(state, default)
        except UserRequestedAppExit:
            raise
        except KeyboardInterrupt:
            return default
        except Exception as exc:
            print(f"UWAGA: tryb kursorowy niedostępny ({exc}). Wracam do trybu tekstowego.")

    print_menu_plain(state)
    return ask_text("Wybór", default)


def ask_cursor_choice(
    *,
    title: str,
    options: list[tuple[str, str, str]],
    default_key: str = "0",
    header_lines: list[str] | None = None,
) -> str | None:
    """Małe podmenu kursorowe. ESC/Q zwraca None, Enter zwraca klucz opcji."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return None
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts

    keys = [key for key, _, _ in options]
    selected = {"index": keys.index(default_key) if default_key in keys else 0}
    width = 78

    def get_text() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []

        def line(style: str, value: str = "") -> None:
            fragments.append((style, value))
            fragments.append(("", "\n"))

        line("class:border", "=" * width)
        line("class:title", f"  {title}")
        line("class:border", "=" * width)
        line("", "")
        for header in header_lines or []:
            line("class:hint", header)
        if header_lines:
            line("", "")
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć | Ctrl+X zamknij bez zapisu")
        line("", "")
        for idx, (key, label, description) in enumerate(options):
            marker = "▶" if idx == selected["index"] else " "
            style = "class:latka.selected" if idx == selected["index"] else "class:latka.option"
            row = f"  {marker} {key}. {label}"
            if len(row) > width:
                row = row[: max(0, width - 1)] + "…"
            line(style, row)
            if description:
                desc = f"      {description}"
                if len(desc) > width:
                    desc = desc[: max(0, width - 1)] + "…"
                line("class:description", desc)
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        selected["index"] = (selected["index"] + delta) % len(options)
        event.app.invalidate()

    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None:
        move(-1, event)

    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None:
        move(1, event)

    @kb.add("home")
    def _home(event: Any) -> None:
        selected["index"] = 0
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        selected["index"] = len(options) - 1
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        event.app.exit(result=options[selected["index"]][0])

    @kb.add("escape")
    @kb.add("q")
    def _cancel(event: Any) -> None:
        event.app.exit(result=None)

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key)
            def _number(event: Any, option_key: str = option_key) -> None:
                event.app.exit(result=option_key)

    style = Style.from_dict({
        "border": "ansicyan",
        "title": "bold ansicyan",
        "hint": "ansibrightblack",
        "description": "ansibrightblack",
        "latka.option": "",
        "latka.selected": "reverse bold",
    })
    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=True,
        mouse_support=False,
    )
    return app.run()


def print_current_pack_settings_block(state: WizardState) -> None:
    subsection("Aktualne ustawienia paczki")
    print(f"Profil pakowania:           {state.profile_label()}")
    print(f"Zakres include profilu:     {state.include_prefixes() or '(cały folder źródłowy)'}")
    print(f"Rozmiar części ZIP:         {state.part_size_mb} MiB")
    print(f"Poziom kompresji:           {state.compression_level}")
    print(f"Zapisywać puste katalogi:   {'tak' if state.include_empty_dirs else 'nie'}")
    print(f"Nadpisywać istniejące:      {'tak' if state.force else 'nie'}")


def ask_int_edit(prompt: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    """Liczba z promptem, gdzie ESC wraca bez zmiany."""
    while True:
        try:
            raw = ask_edit_text(prompt, str(default), bottom_toolbar="Esc = wróć bez zmiany; Ctrl+X = zamknij bez zapisu; Enter = zatwierdź")
        except UserCancelledInput:
            return None
        try:
            value = int(raw)
        except ValueError:
            print("Podaj liczbę całkowitą.")
            continue
        if minimum is not None and value < minimum:
            print(f"Wartość musi być >= {minimum}.")
            continue
        if maximum is not None and value > maximum:
            print(f"Wartość musi być <= {maximum}.")
            continue
        return value


def ask_bool_cursor(title: str, current: bool, *, yes_label: str = "Tak", no_label: str = "Nie") -> bool | None:
    """Tak/Nie w trybie kursorowym; ESC wraca bez zmiany."""
    options = [
        ("1", f"{yes_label}" + ("  *" if current else ""), "Włącz / ustaw Tak."),
        ("2", f"{no_label}" + ("  *" if not current else ""), "Wyłącz / ustaw Nie."),
        ("0", "Wróć", "Bez zmiany."),
    ]
    choice = ask_cursor_choice(title=title, options=options, default_key="1" if current else "2")
    if choice == APP_EXIT_MARKER:
        raise UserRequestedAppExit()
    if choice in {None, "0"}:
        return None
    return choice == "1"


def configure_pack_settings(state: WizardState, ui_mode: str = "plain") -> None:
    while True:
        if should_use_cursor_menu(ui_mode):
            options = [
                ("1", f"Zmień rozmiar jednej części ZIP [{state.part_size_mb} MiB]", "Wpisz liczbę MiB; minimum 1."),
                ("2", f"Zmień poziom kompresji [{state.compression_level}]", "Zakres 0-9; 6 jest rozsądnym domyślnym poziomem."),
                ("3", f"Zapisywać puste katalogi [{'tak' if state.include_empty_dirs else 'nie'}]", "Przełącz Tak/Nie."),
                ("4", f"Nadpisywać istniejące pliki [{'tak' if state.force else 'nie'}]", "Przełącz Tak/Nie."),
                ("5", "Ustaw wszystko krok po kroku", "Rozmiar, kompresja, puste katalogi i nadpisywanie."),
                ("0", "Wróć", "Powrót do menu głównego."),
            ]
            choice = ask_cursor_choice(
                title="Ustawienia paczki",
                options=options,
                default_key="0",
                header_lines=[
                    f"Profil: {state.profile_label()}",
                    f"Zakres include: {state.include_prefixes() or '(cały folder źródłowy)'}",
                    "ESC wraca do menu głównego, nie zamyka aplikacji.",
                ],
            )
            if choice == APP_EXIT_MARKER:
                raise UserRequestedAppExit()
            if choice in {None, "0"}:
                return
            if choice == "1":
                value = ask_int_edit("Rozmiar części ZIP w MiB", state.part_size_mb, minimum=1)
                if value is not None:
                    state.part_size_mb = value
                    state.plan = None
            elif choice == "2":
                value = ask_int_edit("Poziom kompresji 0-9", state.compression_level, minimum=0, maximum=9)
                if value is not None:
                    state.compression_level = value
                    state.plan = None
            elif choice == "3":
                value = ask_bool_cursor("Zapisywać puste katalogi", state.include_empty_dirs)
                if value is not None:
                    state.include_empty_dirs = value
                    state.plan = None
            elif choice == "4":
                value = ask_bool_cursor("Nadpisywać istniejące pliki", state.force)
                if value is not None:
                    state.force = value
                    state.plan = None
            elif choice == "5":
                value = ask_int_edit("Rozmiar części ZIP w MiB", state.part_size_mb, minimum=1)
                if value is None:
                    continue
                compression = ask_int_edit("Poziom kompresji 0-9", state.compression_level, minimum=0, maximum=9)
                if compression is None:
                    continue
                include_dirs = ask_bool_cursor("Zapisywać puste katalogi", state.include_empty_dirs)
                if include_dirs is None:
                    continue
                force = ask_bool_cursor("Nadpisywać istniejące pliki", state.force)
                if force is None:
                    continue
                state.part_size_mb = value
                state.compression_level = compression
                state.include_empty_dirs = include_dirs
                state.force = force
                state.plan = None
            continue

        section("Ustawienia paczki")
        print_current_pack_settings_block(state)
        print("\nOpcje:")
        print("  1. Zmień rozmiar jednej części ZIP")
        print("  2. Zmień poziom kompresji")
        print("  3. Włącz/wyłącz zapisywanie pustych katalogów")
        print("  4. Włącz/wyłącz nadpisywanie istniejących plików")
        print("  5. Ustaw wszystko krok po kroku")
        print("  0. Wróć")
        choice = ask_text("Wybór", "0")
        if choice == "1":
            state.part_size_mb = ask_int("Rozmiar części ZIP w MiB", state.part_size_mb, minimum=1)
            state.plan = None
        elif choice == "2":
            state.compression_level = ask_int("Poziom kompresji 0-9", state.compression_level, minimum=0, maximum=9)
            state.plan = None
        elif choice == "3":
            state.include_empty_dirs = not state.include_empty_dirs
            state.plan = None
            print(f"Zapisywanie pustych katalogów: {'tak' if state.include_empty_dirs else 'nie'}")
        elif choice == "4":
            state.force = not state.force
            state.plan = None
            print(f"Nadpisywanie istniejących plików: {'tak' if state.force else 'nie'}")
        elif choice == "5":
            state.part_size_mb = ask_int("Rozmiar części ZIP w MiB", state.part_size_mb, minimum=1)
            state.compression_level = ask_int("Poziom kompresji 0-9", state.compression_level, minimum=0, maximum=9)
            state.include_empty_dirs = ask_bool("Zapisywać puste katalogi", state.include_empty_dirs)
            state.force = ask_bool("Nadpisywać istniejące pliki", state.force)
            state.plan = None
        elif choice == "0":
            return
        else:
            print("Nieznana opcja.")

def terminal_page_size(default: int = 24) -> int:
    """Rozmiar strony dla długich list w terminalu."""
    try:
        size = shutil.get_terminal_size(fallback=(80, default))
        return max(8, min(40, int(size.lines) - 8))
    except Exception:
        return default




def print_lines_paged(title: str, lines: list[str], *, page_size: int | None = None) -> None:
    """Drukuje długą listę stronami w stylu prostego pagera.

    Od v5.9 nagłówek listy jest oddzielony liniami `====`, żeby podgląd
    pakowania był czytelny po długim skanowaniu i po wyjściu z pagera.
    Enter przechodzi dalej, `p` cofa, `q`/`0` kończy.
    """
    def header(label: str) -> None:
        print("\n" + "=" * 78)
        print(f"  {label}:")
        print("=" * 78)

    if not lines:
        header(title)
        print("  (brak)")
        print("(END)")
        return

    page_size = page_size or terminal_page_size()
    page_size = max(1, int(page_size))
    total_pages = (len(lines) + page_size - 1) // page_size

    if total_pages <= 1 or not sys.stdin.isatty():
        header(title)
        for line in lines:
            print(line)
        print("(END)")
        return

    page = 0
    while True:
        start = page * page_size
        end = min(start + page_size, len(lines))
        header(f"{title} — strona {page + 1}/{total_pages} ({start + 1}-{end} z {len(lines)})")
        for line in lines[start:end]:
            print(line)

        if page >= total_pages - 1:
            print("(END)")
            return

        try:
            choice = input(": ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice in {"q", "0", "k", "koniec", "esc", "w", "wroc", "wróć"}:
            print("(END)")
            return
        if choice in {"p", "poprzednia", "prev", "b"}:
            page = max(0, page - 1)
            continue
        page += 1


def format_numbered_lines(items: list[str], *, start: int = 1) -> list[str]:
    return [f"  {index:>4}. {item}" for index, item in enumerate(items, start=start)]


def rebuild_plan(state: WizardState) -> PackPlan:
    require_ready_state(state)
    assert state.source_folder is not None
    section("4. Informacje co będzie spakowane — podstawa pakowania")
    plan = discover_pack_plan(state.source_folder, state.include_empty_dirs, state.effective_excludes(), state.include_prefixes())
    state.plan = plan
    print_pack_plan_summary(state)
    return plan


def collect_included_folder_paths(plan: PackPlan, root: Path) -> list[str]:
    """Zwraca pełną listę folderów widocznych w planie pakowania.

    Nie wypisuje plików. Uwzględnia foldery dodane jako puste katalogi oraz
    foldery rodzicielskie plików, więc lista działa również wtedy, gdy
    include_empty_dirs=False.
    """
    root = root.resolve()
    folders: set[str] = set()

    for directory in plan.dirs:
        rel = rel_posix(directory, root).rstrip("/")
        if rel and rel != ".":
            folders.add(rel + "/")

    for file_path in plan.files:
        parent = file_path.parent
        try:
            rel_parent = rel_posix(parent, root).rstrip("/")
        except ValueError:
            continue
        if not rel_parent or rel_parent == ".":
            continue
        parts = PurePosixPath(rel_parent).parts
        for index in range(1, len(parts) + 1):
            folders.add(PurePosixPath(*parts[:index]).as_posix().rstrip("/") + "/")

    return sorted(folders, key=lambda value: value.lower())


def print_folder_paths_for_plan(state: WizardState, *, paged: bool = True) -> None:
    assert state.plan is not None
    assert state.source_folder is not None
    folders = collect_included_folder_paths(state.plan, state.source_folder)
    if not folders:
        print("\nFoldery/katalogi obecne w planie pakowania:")
        print("  (brak folderów podrzędnych — pliki są bezpośrednio w katalogu źródłowym albo plan jest pusty)")
        return
    lines = format_numbered_lines(folders)
    if paged:
        print_lines_paged("Foldery/katalogi obecne w planie pakowania", lines)
    else:
        print("\nFoldery/katalogi obecne w planie pakowania:")
        for line in lines:
            print(line)


def print_pack_plan_summary(state: WizardState, *, paged: bool = True) -> None:
    require_ready_state(state)
    if state.plan is None:
        print("Brak aktualnego planu. Wybierz opcję podglądu, żeby przeskanować źródło.")
        return
    assert state.source_folder is not None
    assert state.out_dir is not None
    plan = state.plan
    print("\nPodstawa pakowania została wyliczona z aktualnych ustawień.")
    print(f"Źródło: {state.source_folder}")
    print(f"Wyjście: {state.out_dir}")
    print(f"Nazwa ZIP: {state.archive_name}")
    print(f"Profil: {state.profile_label()}")
    print(f"Pliki do spakowania: {plan.file_count}")
    print(f"Katalogi do zapisania bezpośrednio w ZIP: {plan.dir_count}")
    print(f"Rozmiar źródłowy: {human_size(plan.source_total_size)}")
    print(f"Wykluczone wpisy: {len(plan.excluded)}")
    print(f"Część ZIP: {state.part_size_mb} MiB; kompresja: {state.compression_level}; force: {state.force}")

    top_lines = [
        f"  {name:<32} {count:>6} plików  {human_size(size):>12}"
        for name, count, size in summarize_top_level(plan, state.source_folder)
    ]
    print_lines_paged("Największe grupy top-level według liczby plików", top_lines, page_size=terminal_page_size())
    print_folder_paths_for_plan(state, paged=paged)
    if plan.excluded:
        print("\nWykluczenia: szczegóły są w menu wykluczeń oraz w pełnym podglądzie JSON.")

def configure_profile(state: WizardState, ui_mode: str = "plain") -> None:
    keys = list(PACK_PROFILES.keys())

    if should_use_cursor_menu(ui_mode):
        options: list[tuple[str, str, str]] = []
        for idx, key in enumerate(keys, start=1):
            profile = PACK_PROFILES[key]
            label = str(profile["label"])
            if key == state.pack_profile:
                label = "* " + label
            options.append((str(idx), label, str(profile["description"])))
        options.append(("0", "Wróć", "Bez zmiany profilu."))
        default_key = str(keys.index(state.pack_profile) + 1) if state.pack_profile in keys else "1"
        choice = ask_cursor_choice(
            title="Profil pakowania",
            options=options,
            default_key=default_key,
            header_lines=[
                "Profil ustawia bazową listę pakowania i domyślne wykluczenia.",
                "Później możesz wyłączyć pojedyncze wzorce albo dodać własne.",
            ],
        )
        if choice == APP_EXIT_MARKER:
            raise UserRequestedAppExit()
        if choice is None or choice == "0":
            print("Powrót bez zmiany profilu.")
            return
        try:
            new_profile = keys[int(choice) - 1]
        except Exception:
            print("Nieznany wybór profilu.")
            return
        if new_profile != state.pack_profile:
            state.pack_profile = new_profile
            state.disabled_default_excludes.clear()
            state.plan = None
            print(f"Ustawiono profil: {state.profile_label()}")
        return

    section("Profil pakowania")
    print("Wybierz profil. Profil ustawia bazową listę pakowania i domyślne wykluczenia.")
    print("Później możesz jeszcze ręcznie wyłączyć pojedyncze wzorce albo dodać własne.")
    for idx, key in enumerate(keys, start=1):
        profile = PACK_PROFILES[key]
        marker = "*" if key == state.pack_profile else " "
        print(f"  {idx}. {marker} {profile['label']}")
        print(f"       {profile['description']}")
        prefixes = as_str_list(profile.get("include_prefixes"))
        if prefixes:
            print(f"       Zakres include: {', '.join(prefixes)}")
    print("  0. Wróć")
    choice = ask_int("Numer profilu", 0, minimum=0, maximum=len(keys))
    if choice == 0:
        return
    new_profile = keys[choice - 1]
    if new_profile != state.pack_profile:
        state.pack_profile = new_profile
        state.disabled_default_excludes.clear()
        state.plan = None
        print(f"Ustawiono profil: {state.profile_label()}")

def print_default_exclusions_table(state: WizardState) -> None:
    """Pokazuje tabelę domyślnych wykluczeń profilu z ON/OFF."""
    profile_excludes = state.profile_default_excludes()
    disabled = set(state.disabled_default_excludes)
    section("Domyślne wykluczenia — tabela ON/OFF")
    print(f"Profil:                    {state.profile_label()}")
    print(f"Użycie listy domyślnej:     {on_off_label(state.use_default_excludes)}")
    print(f"Aktywne domyślne wzorce:    {len(state.active_default_excludes())} / {len(profile_excludes)}")
    print()
    print(f"{'Nr':>4}  {'Stan':<3}  Wzorzec")
    print("-" * 78)
    for idx, pat in enumerate(profile_excludes, start=1):
        status = "OFF" if pat in disabled else "ON"
        print(f"{idx:>4}  {status:<3}  {pat}")
    print("-" * 78)


def reset_default_exclusions(state: WizardState) -> None:
    """Włącza wszystkie domyślne wykluczenia bieżącego profilu."""
    if state.disabled_default_excludes:
        state.disabled_default_excludes.clear()
        state.plan = None
    state.use_default_excludes = True
    print("Włączono całą domyślną listę i wyczyszczono indywidualne wyłączenia.")


def set_all_default_exclusions(state: WizardState, enabled: bool) -> None:
    """Włącza albo wyłącza wszystkie pojedyncze wzorce domyślne."""
    profile_excludes = state.profile_default_excludes()
    if enabled:
        state.disabled_default_excludes.clear()
        state.use_default_excludes = True
        print("Włączono wszystkie pojedyncze domyślne wykluczenia.")
    else:
        state.disabled_default_excludes = list(profile_excludes)
        state.use_default_excludes = True
        print("Wyłączono wszystkie pojedyncze domyślne wykluczenia, ale lista domyślna pozostaje dostępna.")
    state.plan = None


def toggle_default_exclusion_by_index(state: WizardState, index: int) -> bool:
    """Przełącza pojedynczy wzorzec domyślny po indeksie 0-based."""
    profile_excludes = state.profile_default_excludes()
    if not (0 <= index < len(profile_excludes)):
        return False
    pat = profile_excludes[index]
    if pat in state.disabled_default_excludes:
        state.disabled_default_excludes = [x for x in state.disabled_default_excludes if x != pat]
        print(f"ON:  {pat}")
    else:
        state.disabled_default_excludes.append(pat)
        print(f"OFF: {pat}")
    state.plan = None
    return True


def default_exclusions_cursor_table_menu(state: WizardState) -> None:
    """Przewijana tabela ON/OFF dla domyślnych wykluczeń w trybie kursorowym.

    Zwykłe menu kursorowe dobrze działa dla krótkich list, ale domyślne
    wykluczenia potrafią mieć kilkadziesiąt pozycji. Dlatego tutaj tabela ma
    własny widok: nagłówek i stopka są stałe, a środek przewija się razem z
    zaznaczonym wierszem.
    """
    parts = prompt_toolkit_parts()
    if parts is None:
        return
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts

    profile_excludes = state.profile_default_excludes()
    selected = {"index": 0, "top": 0}
    width = 100

    def clamp_selection() -> None:
        count = len(profile_excludes)
        if count <= 0:
            selected["index"] = 0
            selected["top"] = 0
            return
        selected["index"] = max(0, min(selected["index"], count - 1))
        visible = visible_row_count()
        if selected["index"] < selected["top"]:
            selected["top"] = selected["index"]
        elif selected["index"] >= selected["top"] + visible:
            selected["top"] = selected["index"] - visible + 1
        selected["top"] = max(0, min(selected["top"], max(0, count - visible)))

    def visible_row_count() -> int:
        try:
            lines = shutil.get_terminal_size(fallback=(100, 24)).lines
        except Exception:
            lines = 24
        # Tytuł, status, podpowiedzi, nagłówek tabeli i stopka zajmują zwykle
        # 10-12 wierszy. Resztę oddajemy na przewijaną tabelę.
        return max(4, min(len(profile_excludes), lines - 12))

    def move(delta: int) -> None:
        if not profile_excludes:
            return
        selected["index"] = max(0, min(len(profile_excludes) - 1, selected["index"] + delta))
        clamp_selection()

    def current_pattern() -> str | None:
        if not profile_excludes:
            return None
        clamp_selection()
        return profile_excludes[selected["index"]]

    def toggle_current() -> None:
        pat = current_pattern()
        if pat is None:
            return
        if pat in state.disabled_default_excludes:
            state.disabled_default_excludes = [x for x in state.disabled_default_excludes if x != pat]
        else:
            state.disabled_default_excludes.append(pat)
        state.plan = None

    def get_text() -> list[tuple[str, str]]:
        clamp_selection()
        disabled = set(state.disabled_default_excludes)
        visible = visible_row_count()
        top = selected["top"]
        bottom = min(top + visible, len(profile_excludes))
        active_count = len(state.active_default_excludes())
        fragments: list[tuple[str, str]] = []

        def line(style: str, value: str = "") -> None:
            fragments.append((style, value[:width]))
            fragments.append(("", "\n"))

        line("class:border", "=" * 78)
        line("class:title", "  Domyślne wykluczenia — tabela ON/OFF")
        line("class:border", "=" * 78)
        line("class:hint", f"Profil: {state.profile_label()}")
        line("class:hint", f"Lista domyślna: {on_off_label(state.use_default_excludes)} | aktywne: {active_count}/{len(profile_excludes)}")
        line("class:hint", "↑/↓ przewiń | PgUp/PgDn strona | Home/End | Enter przełącz")
        line("class:hint", "A wszystkie ON | X wszystkie OFF | R reset | G lista ON/OFF | Esc/Q/0 wróć")
        line("", "")
        line("class:table.header", f"{'Nr':>4}  {'Stan':<3}  Wzorzec")
        line("class:border", "-" * 78)

        for idx in range(top, bottom):
            pat = profile_excludes[idx]
            status = "OFF" if pat in disabled else "ON"
            marker = "▶" if idx == selected["index"] else " "
            style = "class:latka.selected" if idx == selected["index"] else "class:latka.option"
            line(style, f" {marker} {idx + 1:>4}  {status:<3}  {pat}")

        if bottom < top + visible:
            for _ in range(top + visible - bottom):
                line("", "")

        line("class:border", "-" * 78)
        line("class:hint", f"Widok: {top + 1 if profile_excludes else 0}-{bottom} z {len(profile_excludes)} | zaznaczone: {selected['index'] + 1 if profile_excludes else 0}")
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=False)
    layout = Layout(window)
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None:
        move(-1)
        event.app.invalidate()

    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None:
        move(1)
        event.app.invalidate()

    @kb.add("pageup")
    @kb.add("c-u")
    def _page_up(event: Any) -> None:
        move(-visible_row_count())
        event.app.invalidate()

    @kb.add("pagedown")
    @kb.add("c-d")
    def _page_down(event: Any) -> None:
        move(visible_row_count())
        event.app.invalidate()

    @kb.add("home")
    def _home(event: Any) -> None:
        selected["index"] = 0
        selected["top"] = 0
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        selected["index"] = max(0, len(profile_excludes) - 1)
        clamp_selection()
        event.app.invalidate()

    @kb.add("enter")
    @kb.add("space")
    def _toggle(event: Any) -> None:
        toggle_current()
        event.app.invalidate()

    @kb.add("a")
    def _all_on(event: Any) -> None:
        state.disabled_default_excludes.clear()
        state.use_default_excludes = True
        state.plan = None
        event.app.invalidate()

    @kb.add("x")
    def _all_off(event: Any) -> None:
        state.disabled_default_excludes = list(profile_excludes)
        state.use_default_excludes = True
        state.plan = None
        event.app.invalidate()

    @kb.add("r")
    def _reset(event: Any) -> None:
        state.disabled_default_excludes.clear()
        state.use_default_excludes = True
        state.plan = None
        event.app.invalidate()

    @kb.add("g")
    def _global_toggle(event: Any) -> None:
        state.use_default_excludes = not state.use_default_excludes
        state.plan = None
        event.app.invalidate()

    @kb.add("escape")
    @kb.add("q")
    @kb.add("0")
    def _cancel(event: Any) -> None:
        event.app.exit(result=None)

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    style = Style.from_dict({
        "border": "ansicyan",
        "title": "bold ansicyan",
        "hint": "ansibrightblack",
        "table.header": "bold",
        "latka.option": "",
        "latka.selected": "reverse bold",
    })
    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=True,
        mouse_support=False,
        enable_page_navigation_bindings=True,
    )
    app.run()


def default_exclusions_table_menu(state: WizardState, ui_mode: str = "plain") -> None:
    """Edytuje domyślne wykluczenia profilu jako tabelę ON/OFF."""
    profile_excludes = state.profile_default_excludes()
    if not profile_excludes:
        print("Ten profil nie ma domyślnych wykluczeń.")
        pause()
        return

    if should_use_cursor_menu(ui_mode):
        default_exclusions_cursor_table_menu(state)
        return

    while True:
        print_default_exclusions_table(state)
        print("Wpisz numer, żeby przełączyć ON/OFF.")
        print("a = wszystkie ON | x = wszystkie OFF | r = reset profilu | g = lista domyślna ON/OFF | 0 = wróć")
        choice = ask_text("Wybór", "0").strip().lower()
        if choice == "0":
            return

        normalized = str(choice).strip().lower()
        if normalized in {"a", "all", "on", "włącz", "wlacz"}:
            set_all_default_exclusions(state, True)
            continue
        if normalized in {"x", "off", "wyłącz", "wylacz"}:
            set_all_default_exclusions(state, False)
            continue
        if normalized in {"r", "reset"}:
            reset_default_exclusions(state)
            continue
        if normalized in {"g", "global"}:
            state.use_default_excludes = not state.use_default_excludes
            state.plan = None
            print(f"Domyślna lista wykluczeń: {on_off_label(state.use_default_excludes)}")
            continue
        try:
            num = int(normalized)
        except ValueError:
            print("Nieznana opcja.")
            continue
        if not toggle_default_exclusion_by_index(state, num - 1):
            print("Numer poza zakresem.")


def toggle_single_default_exclusion(state: WizardState, ui_mode: str = "plain") -> None:
    """Zachowany alias kompatybilności — otwiera pełną tabelę ON/OFF."""
    default_exclusions_table_menu(state, ui_mode)


def choose_custom_exclusion_index(state: WizardState, ui_mode: str = "plain", *, title: str = "Manualne wykluczenia") -> int | None:
    if not state.custom_excludes:
        print("Brak manualnych wykluczeń.")
        pause()
        return None

    if should_use_cursor_menu(ui_mode):
        options = [(str(idx), pat, "") for idx, pat in enumerate(state.custom_excludes, start=1)]
        options.append(("0", "Wróć", "Bez zmiany."))
        choice = ask_cursor_choice(
            title=title,
            options=options,
            default_key="0",
            header_lines=["ESC wraca do menu manualnych wykluczeń."],
        )
        if choice in {None, "0"}:
            return None
        try:
            num = int(str(choice))
        except ValueError:
            return None
    else:
        lines = [f"  {idx}. {pat}" for idx, pat in enumerate(state.custom_excludes, start=1)]
        print_lines_paged(title, lines)
        num = ask_int("Numer manualnego wykluczenia", 0, minimum=0, maximum=len(state.custom_excludes))
        if num == 0:
            return None

    if 1 <= num <= len(state.custom_excludes):
        return num - 1
    return None


def remove_custom_exclusion(state: WizardState, ui_mode: str = "plain") -> None:
    index = choose_custom_exclusion_index(state, ui_mode, title="Usuń manualne wykluczenie")
    if index is None:
        return
    removed = state.custom_excludes.pop(index)
    state.plan = None
    print(f"Usunięto: {removed}")


def add_custom_exclusion(state: WizardState, ui_mode: str = "plain") -> None:
    try:
        if should_use_cursor_menu(ui_mode):
            pat = ask_edit_text(
                "Wzorzec wykluczenia, np. docs/ albo *.log",
                "",
                bottom_toolbar="Esc = wróć; Ctrl+X = zamknij bez zapisu; Enter = zatwierdź",
            )
        else:
            pat = ask_text("Wzorzec, np. docs/ albo *.log")
    except UserCancelledInput:
        return
    if pat.strip():
        state.custom_excludes.append(pat.strip())
        state.plan = None
        print(f"Dodano manualne wykluczenie: {pat.strip()}")


def edit_custom_exclusion(state: WizardState, ui_mode: str = "plain") -> None:
    index = choose_custom_exclusion_index(state, ui_mode, title="Edytuj manualne wykluczenie")
    if index is None:
        return
    current = state.custom_excludes[index]
    try:
        if should_use_cursor_menu(ui_mode):
            new_value = ask_edit_text(
                "Nowy wzorzec wykluczenia",
                current,
                bottom_toolbar="Esc = wróć bez zmiany; Ctrl+X = zamknij bez zapisu; Enter = zatwierdź",
            )
        else:
            new_value = ask_text("Nowy wzorzec wykluczenia", current)
    except UserCancelledInput:
        return
    new_value = new_value.strip()
    if not new_value:
        print("Pusty wzorzec pominięty. Bez zmiany.")
        return
    state.custom_excludes[index] = new_value
    state.plan = None
    print(f"Zmieniono: {current} -> {new_value}")


def manual_exclusion_submenu(state: WizardState, ui_mode: str = "plain") -> None:
    while True:
        if should_use_cursor_menu(ui_mode):
            choice = ask_cursor_choice(
                title="Manualne wykluczenie",
                options=[
                    ("1", "Dodaj", "Dodaj nowy ręczny wzorzec."),
                    ("2", "Edytuj", "Zmień wybrany ręczny wzorzec."),
                    ("3", "Usuń pojedyncze", "Usuń jeden wybrany wzorzec."),
                    ("4", "Wyczyść wszystkie", "Usuń wszystkie ręczne wzorce."),
                    ("0", "Wróć", "Powrót do ustawień wykluczeń."),
                ],
                default_key="0",
                header_lines=[f"Manualne wykluczenia: {manual_exclusions_label(state)}"],
            )
            if choice in {None, "0"}:
                return
        else:
            section("Manualne wykluczenie")
            print(f"Manualne wykluczenia: {manual_exclusions_label(state)}")
            print("Opcje:")
            print("  1. Dodaj")
            print("  2. Edytuj")
            print("  3. Usuń pojedyncze")
            print("  4. Wyczyść wszystkie")
            print("  0. Wróć")
            choice = ask_text("Wybór", "0")
            if choice == "0":
                return

        if choice == "1":
            add_custom_exclusion(state, ui_mode)
        elif choice == "2":
            edit_custom_exclusion(state, ui_mode)
        elif choice == "3":
            remove_custom_exclusion(state, ui_mode)
        elif choice == "4":
            if state.custom_excludes:
                state.custom_excludes.clear()
                state.plan = None
                print("Wyczyszczono wszystkie manualne wykluczenia.")
            else:
                print("Brak manualnych wykluczeń do wyczyszczenia.")
        else:
            print("Nieznana opcja.")


def print_exclusion_status(state: WizardState) -> None:
    section("Ustawienia wykluczeń")
    active_defaults = state.active_default_excludes()
    profile_excludes = state.profile_default_excludes()
    print(f"Profil pakowania:             {state.profile_label()}")
    print(f"Zakres include profilu:       {state.include_prefixes() or '(cały folder źródłowy)'}")
    print(f"Domyślna lista wykluczeń:     {on_off_label(state.use_default_excludes)}")
    print(f"Domyślne aktywne:             {len(active_defaults)} / {len(profile_excludes)}")
    print(f"Manualne wykluczenia:         {on_off_label(state.use_custom_excludes)}")
    print(f"Manualne wzorce:              {len(state.custom_excludes)}")


def exclusion_menu(state: WizardState, ui_mode: str = "plain") -> None:
    while True:
        if should_use_cursor_menu(ui_mode):
            choice = ask_cursor_choice(
                title="Ustawienia wykluczeń",
                options=[
                    ("1", f"Profil pakowania [{state.profile_label()}]", "Zmień profil i bazową listę pakowania."),
                    ("2", f"Użycie domyślnej listy [{on_off_label(state.use_default_excludes)}]", "Globalnie włącz/wyłącz całą listę domyślną profilu."),
                    ("3", f"Edytuj domyślne wykluczenia ON/OFF [{len(state.active_default_excludes())}/{len(state.profile_default_excludes())}]", "Tabela pojedynczych wzorców domyślnych."),
                    ("4", f"Manualne wykluczenia [{on_off_label(state.use_custom_excludes)}]", "Włącz/wyłącz użycie ręcznych wzorców."),
                    ("5", f"Manualne wykluczenie [{len(state.custom_excludes)}]", "Dodaj, edytuj, usuń pojedyncze albo wyczyść wszystkie."),
                    ("6", "Przeskanuj ponownie i pokaż wpływ", "Buduje aktualny plan pakowania i pokazuje go stronami."),
                    ("0", "Wróć", "Powrót do menu głównego."),
                ],
                default_key="0",
                header_lines=[
                    f"Domyślne aktywne: {len(state.active_default_excludes())} / {len(state.profile_default_excludes())}",
                    f"Manualne: {manual_exclusions_label(state)}",
                    "ESC wraca do menu głównego, Ctrl+X zamyka bez zapisu.",
                ],
            )
            if choice in {None, "0"}:
                return
        else:
            print_exclusion_status(state)
            print("\nOpcje:")
            print(f"  1. Profil pakowania [{state.profile_label()}]")
            print(f"  2. Użycie domyślnej listy [{on_off_label(state.use_default_excludes)}]")
            print(f"  3. Edytuj domyślne wykluczenia ON/OFF [{len(state.active_default_excludes())}/{len(state.profile_default_excludes())}]")
            print(f"  4. Manualne wykluczenia [{on_off_label(state.use_custom_excludes)}]")
            print("  5. Manualne wykluczenie")
            print("     5.1 Dodaj")
            print("     5.2 Edytuj")
            print("     5.3 Usuń pojedyncze")
            print("     5.4 Wyczyść wszystkie")
            print("  6. Przeskanuj ponownie i pokaż wpływ")
            print("  0. Wróć")
            choice = ask_text("Wybór", "0")
            if choice == "0":
                return

        normalized_choice = str(choice).strip().replace(",", ".")
        if normalized_choice == "1":
            configure_profile(state, ui_mode)
        elif normalized_choice == "2":
            state.use_default_excludes = not state.use_default_excludes
            state.plan = None
            print(f"Domyślna lista wykluczeń: {on_off_label(state.use_default_excludes)}")
        elif normalized_choice == "3":
            default_exclusions_table_menu(state, ui_mode)
        elif normalized_choice == "4":
            state.use_custom_excludes = not state.use_custom_excludes
            state.plan = None
            print(f"Manualne wykluczenia: {on_off_label(state.use_custom_excludes)}")
        elif normalized_choice == "5":
            manual_exclusion_submenu(state, ui_mode)
        elif normalized_choice in {"5.1", "51"}:
            add_custom_exclusion(state, ui_mode)
        elif normalized_choice in {"5.2", "52"}:
            edit_custom_exclusion(state, ui_mode)
        elif normalized_choice in {"5.3", "53"}:
            remove_custom_exclusion(state, ui_mode)
        elif normalized_choice in {"5.4", "54"}:
            if state.custom_excludes:
                state.custom_excludes.clear()
                state.plan = None
                print("Wyczyszczono wszystkie manualne wykluczenia.")
            else:
                print("Brak manualnych wykluczeń do wyczyszczenia.")
        elif normalized_choice == "6":
            rebuild_plan(state)
            pause()
        else:
            print("Nieznana opcja.")




def settings_submenu(state: WizardState, ui_mode: str = "plain") -> str:
    """Podmenu ustawień: paczka, nazwa, wykluczenia, podgląd JSON i UI."""
    options = [
        ("1", f"Ustawienia paczki [{pack_settings_menu_label(state)}]", "Rozmiar części, kompresja, puste katalogi, nadpisywanie."),
        ("2", "Reset/odśwież nazwę paczki wg version.py", "Czyści ręczną nazwę i tworzy nazwę z wersji/release."),
        ("3", f"Ustawienia wykluczeń [{exclusions_menu_label(state)}]", "Domyślne i ręczne wzorce wykluczeń."),
        ("4", "Zapisz pełny podgląd listy pakowania do JSON", "Tworzy plik .pack_preview.json w folderze zapisu."),
        ("5", f"Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]", "Wybierz TXT/Kursorowy i włącz/wyłącz auto-start zapisanego trybu."),
        ("0", "Wróć", "Powrót do menu głównego."),
    ]
    if should_use_cursor_menu(ui_mode):
        choice = ask_cursor_choice(
            title="Ustawienia",
            options=options,
            default_key="0",
            header_lines=["ESC wraca do menu głównego. Ctrl+X zamyka bez zapisu."],
        )
        if choice in {None, "0"}:
            return "cancel"
        return str(choice)

    section("Ustawienia")
    print(f"  1. Ustawienia paczki [{pack_settings_menu_label(state)}]")
    print("  2. Reset/odśwież nazwę paczki wg version.py")
    print(f"  3. Ustawienia wykluczeń [{exclusions_menu_label(state)}]")
    print("  4. Zapisz pełny podgląd listy pakowania do JSON")
    print(f"  5. Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]")
    print("  0. Wróć")
    return ask_text("Wybór", "0").strip()


def reset_archive_name_from_version(state: WizardState) -> bool:
    """Resetuje ręczną nazwę ZIP i generuje ją od nowa z latka_jazn/version.py."""
    if state.source_folder is None:
        section("Najpierw folder do pakowania")
        print("Reset nazwy wymaga folderu źródłowego z .\\latka_jazn\\version.py.")
        configure_source(state)
        if state.source_folder is None:
            print("Nie ustawiono folderu do pakowania. Nazwa bez zmian.")
            return False
    try:
        old_name = state.archive_name or "(brak)"
        refresh_version_and_default_name(state, keep_custom_name=False)
    except Exception as exc:
        print(f"BŁĄD: nie udało się odświeżyć nazwy z version.py: {exc}")
        return False
    print(f"Poprzednia nazwa: {old_name}")
    print(f"Nowa nazwa:       {state.archive_name}")
    if state.resolved_version_file:
        print(f"Plik version.py:  {state.resolved_version_file}")
    return True



def _apply_ui_mode_choice(state: WizardState, active_ui_mode: str, choice: str) -> str:
    """Stosuje pojedynczy wybór w podmenu interfejsu i zwraca aktywny tryb."""
    normalized = str(choice).strip().lower()
    if normalized in {"a", "auto", "automat", "3"}:
        state.ui_auto_start = not state.ui_auto_start
        print(f"Automatyczne użycie zapisanego interfejsu przy starcie: {on_off_label(state.ui_auto_start)}")
        return active_ui_mode

    if normalized in {"1", "tekst", "tekstowy", "plain", "txt", "lista"}:
        preference = "plain"
    elif normalized in {"2", "kursor", "kursorowy", "cursor"}:
        preference = "cursor"
    else:
        print("Nieznany wybór trybu UI. Bez zmiany.")
        return active_ui_mode

    new_active = preference
    missing = _missing_required_ui_packages(new_active)
    if missing:
        if sys.stdin.isatty():
            section("Tryb kursorowy")
            print("Tryb kursorowy wymaga biblioteki prompt_toolkit.")
            if ask_bool("Doinstalować prompt_toolkit przez pip", False):
                if install_optional_ui_packages(missing):
                    new_active = "cursor"
                else:
                    print("Nie udało się uruchomić trybu kursorowego. Zostawiam tekstowy.")
                    new_active = "plain"
                    preference = "plain"
            else:
                new_active = "plain"
                preference = "plain"
        else:
            new_active = "plain"
            preference = "plain"

    state.ui_mode = preference
    state.ui_auto_start = True
    global ACTIVE_UI_MODE
    ACTIVE_UI_MODE = new_active
    print(f"Ustawiono interfejs: {ui_mode_label(preference)}. Auto-start zapisanego trybu: {on_off_label(state.ui_auto_start)}")
    return new_active


def configure_ui_mode_preference(state: WizardState, active_ui_mode: str = "plain") -> str:
    """Zmienia zapisany interfejs TXT/Kursorowy i pozostaje w tym podmenu.

    Od v5.9 zatwierdzenie TXT/Kursorowy albo auto-startu nie wyrzuca do menu
    głównego ani do głównego ekranu Ustawień. Użytkownik wraca ręcznie przez
    ESC/Q/0, czyli o jeden poziom.
    """
    current_active = normalize_ui_mode(active_ui_mode or state.ui_mode or "plain")
    if current_active == "auto":
        current_active = resolve_auto_ui_mode()

    while True:
        current_pref = normalize_ui_mode(state.ui_mode or current_active or "plain")
        if current_pref == "auto":
            current_pref = resolve_auto_ui_mode()

        if should_use_cursor_menu(current_active):
            choice = ask_cursor_choice(
                title="Zmień interfejs TXT/Kursorowy",
                options=[
                    ("1", "TXT / tekstowy" + ("  *" if current_pref == "plain" else ""), "Ustaw tekstowy i włącz automatyczne użycie tego zapisanego trybu."),
                    ("2", "Kursorowy" + ("  *" if current_pref == "cursor" else ""), "Ustaw kursorowy i włącz automatyczne użycie tego zapisanego trybu."),
                    ("a", f"Automatycznie używaj zapisanego trybu [{on_off_label(state.ui_auto_start)}]", "ON = start bez pytania; OFF = pytaj przy starcie, tylko TXT/Kursorowy."),
                    ("0", "Wróć", "Powrót do Ustawień."),
                ],
                default_key={"plain": "1", "cursor": "2"}.get(current_pref, "1"),
                header_lines=[f"Aktualnie: {ui_mode_setting_label(state, current_active)}"],
            )
            if choice in {None, "0"}:
                return current_active
        else:
            section("Zmień interfejs TXT/Kursorowy")
            print(f"Aktualnie: {ui_mode_setting_label(state, current_active)}")
            print("  1. TXT / tekstowy")
            print("  2. Kursorowy")
            print(f"  A. Automatycznie używaj zapisanego trybu [{on_off_label(state.ui_auto_start)}]")
            print("  0. Wróć")
            choice = ask_text("Wybór", "0").strip().lower()
            if choice == "0":
                return current_active

        current_active = _apply_ui_mode_choice(state, current_active, str(choice))
        save_settings(state, quiet=True)


def exit_menu(ui_mode: str = "plain") -> str:
    """Połączone menu wyjścia: zapisz, wyjdź bez zapisu albo wróć."""
    if should_use_cursor_menu(ui_mode):
        choice = ask_cursor_choice(
            title="Wyjście",
            options=[
                ("1", "Wyjdź i zapisz nowe ustawienia", "Zapisuje __jazn_pack_generator_settings.json."),
                ("2", "Wyjdź bez zapisywania", "Przywraca ustawienia z początku sesji."),
                ("3", "Powrót do menu", "Nie zamyka aplikacji."),
            ],
            default_key="3",
            header_lines=["ESC wraca do menu głównego. Ctrl+X zamyka bez zapisu."],
        )
        if choice == APP_EXIT_MARKER:
            raise UserRequestedAppExit()
        if choice == "1":
            return "save"
        if choice == "2":
            return "nosave"
        return "cancel"

    section("Wyjście")
    print("  1. Wyjdź i zapisz nowe ustawienia")
    print("  2. Wyjdź bez zapisywania")
    print("  3. Powrót do menu")
    choice = ask_text("Wybór", "3").strip().lower()
    if choice == "1":
        return "save"
    if choice == "2":
        return "nosave"
    return "cancel"


def show_current_state(state: WizardState) -> None:
    section("Status")
    print(f"Plan pakowania:             {plan_status_label(state)}")
    print(f"Folder do pakowania:        {menu_value(state.source_folder)}")
    print(f"Folder zapisu paczki:       {menu_value(state.out_dir)}")
    print(f"Nazwa ZIP po złożeniu:      {state.archive_name or '(nie ustawiono)'}")
    print(f"Wersja / release:           {state.package_version or '(nie odczytano)'}"
          f"{('-' + state.package_release_name) if state.package_release_name else ''}")
    print(f"Plik ustawień:              {SETTINGS_FILE_NAME}")


def initialize_state(initial_source: str | None = None) -> WizardState:
    state = WizardState()

    # Pierwsze uruchomienie ma startować bez narzuconych ścieżek.
    # Folder źródłowy i folder zapisu są wczytywane dopiero z JSON-a
    # użytkownika albo z argumentu CLI. Stałe SOURCE_FOLDER/OUTPUT_DIR
    # zostają jako opcjonalne wartości awaryjne dla osób, które świadomie
    # wpiszą je w kodzie, ale domyślnie są puste.
    if SOURCE_FOLDER:
        state.source_folder = Path(SOURCE_FOLDER).expanduser()
    if OUTPUT_DIR:
        state.out_dir = Path(OUTPUT_DIR).expanduser()

    # Potem ustawienia użytkownika obok skryptu.
    load_settings(state)

    # Argument/parametr startowy ma najwyższy priorytet.
    if initial_source:
        state.source_folder = Path(initial_source).expanduser()

    if state.source_folder is not None:
        try:
            if not state.source_folder.exists() or not state.source_folder.is_dir():
                raise NotADirectoryError(f"folder nie istnieje albo nie jest folderem: {state.source_folder}")
            state.source_folder = state.source_folder.resolve()
            if state.out_dir is not None:
                state.out_dir = state.out_dir.resolve()
            resolved_version_file, package_version, package_release_name = read_source_version_info(
                state.source_folder,
                state.version_file,
            )
            state.resolved_version_file = resolved_version_file
            state.package_version = package_version
            state.package_release_name = package_release_name
            if not state.archive_name:
                state.archive_name = apply_version_to_archive_name(
                    state.archive_basename_requested or ARCHIVE_BASENAME,
                    state.package_version,
                    package_release_name=state.package_release_name,
                    enabled=True,
                )
        except Exception as exc:
            bad_source = str(state.source_folder)
            state.startup_warnings.append(
                "Zapisany folder do pakowania został pominięty i usunięty z ustawień: "
                f"{bad_source}\nPowód: {exc}"
            )
            state.settings_needs_cleanup = True
            state.source_folder = None
            state.resolved_version_file = None
            state.package_version = ""
            state.package_release_name = ""
            state.archive_name = ""
            state.plan = None
    return state


def prepare_plan_on_startup_if_possible(state: WizardState) -> None:
    """Przygotowuje listę pakowania przy starcie, ale jej nie wyświetla.

    Warunek: musi być ustawiony poprawny folder do pakowania. Przy pierwszym
    uruchomieniu, gdy nie ma JSON-a ani wybranego folderu, nie skanujemy niczego.
    Lista jest tylko gotowym stanem programu; szczegóły pokazuje dopiero opcja 1
    w menu głównym.
    """
    if state.source_folder is None:
        return
    try:
        source = state.source_folder.expanduser().resolve()
    except Exception:
        return
    if not source.exists() or not source.is_dir():
        return

    section("Przygotowanie listy pakowania")
    print("Folder do pakowania jest ustawiony, więc przygotowuję listę w tle.")
    print("Szczegóły nie zostaną pokazane automatycznie — użyj opcji 1 w menu.")
    state.plan = discover_pack_plan(
        source,
        state.include_empty_dirs,
        state.effective_excludes(),
        state.include_prefixes(),
    )


def show_startup_warnings(state: WizardState) -> None:
    """Pokazuje ostrzeżenia startowe dopiero po nagłówku aplikacji."""
    if not state.startup_warnings:
        return
    section("Uwagi startowe")
    for warning in state.startup_warnings:
        print("UWAGA: " + warning.replace("\n", "\n       "))
    if state.settings_needs_cleanup:
        print("Niepoprawne zapisane źródło zostało wyczyszczone z __jazn_pack_generator_settings.json.")


def ensure_ready_for_pack_plan(state: WizardState, *, ui_mode: str = "plain") -> bool:
    """Prowadzi użytkownika przez brakujące ustawienia potrzebne do planu/pakowania.

    Opcje 1, 8 i 9 w menu nie powinny kończyć się twardym błędem, gdy
    brakuje źródła lub folderu zapisu. Zamiast tego aplikacja krok po kroku
    otwiera właściwe ustawienia. Jeśli użytkownik anuluje zmianę ESC,
    funkcja zwraca False i menu wraca do stanu głównego.
    """
    if state.source_folder is None:
        section("Najpierw folder do pakowania")
        print("Ta opcja wymaga folderu źródłowego Jaźni.")
        print("Wymagany plik: .\\latka_jazn\\version.py")
        configure_source(state)
        if state.source_folder is None:
            print("Nie ustawiono folderu do pakowania. Wracam do menu głównego.")
            return False

    if state.out_dir is None:
        section("Następnie folder zapisu")
        print("Do podglądu JSON i pakowania potrzebny jest folder zapisu paczki.")
        configure_output(state)
        if state.out_dir is None:
            print("Nie ustawiono folderu zapisu paczki. Wracam do menu głównego.")
            return False

    if not state.archive_name:
        section("Następnie nazwa paczki ZIP")
        configure_name(state)
        if not state.archive_name:
            print("Nie ustawiono nazwy paczki ZIP. Wracam do menu głównego.")
            return False

    return True


def run_wizard(initial_source: str | None = None, *, ui_mode: str | None = None) -> int:
    settings_snapshot = snapshot_settings_file()
    state: WizardState | None = None
    try:
        activate_process_guard(prompt_user=True)
        state = initialize_state(initial_source)

        section(f"Jaźń / Łatka — generator paczki ZIP v{VERSION}")
        print_bar(100, 100, label="Ładowanie")
        if state.settings_needs_cleanup:
            save_settings(state, quiet=True)
            # Po automatycznym czyszczeniu niepoprawnego źródła traktujemy
            # oczyszczony plik jako nowy punkt bazowy. Dzięki temu opcja
            # "Wyjdź bez zapisywania zmian" nie przywróci starego, błędnego
            # `source_folder` i ostrzeżenie nie będzie wracać przy każdym starcie.
            settings_snapshot = snapshot_settings_file()
        show_startup_warnings(state)
        ui_mode = resolve_ui_mode_with_optional_install(ui_mode, state)
        # Wybór TXT/Kursorowy przy starcie jest preferencją aplikacji, więc zapisujemy
        # go od razu jako nowy punkt bazowy. Dzięki temu kolejne uruchomienie nie pyta
        # ponownie, chyba że użytkownik wyłączy auto-start w ustawieniach.
        save_settings(state, quiet=True)
        settings_snapshot = snapshot_settings_file()
        prepare_plan_on_startup_if_possible(state)
    except UserRequestedAppExit:
        restore_settings_file(settings_snapshot)
        print("\nZamknięto skrótem Ctrl+X bez zapisywania zmian.")
        return 130
    except KeyboardInterrupt:
        restore_settings_file(settings_snapshot)
        print("\nPrzerwano przez Ctrl+C. Start aplikacji został anulowany bez tracebacka.")
        return 130
    except EOFError:
        restore_settings_file(settings_snapshot)
        print("\nWejście terminala zostało zamknięte. Start aplikacji został anulowany bez tracebacka.")
        return 130

    while True:
        try:
            if not should_use_cursor_menu(ui_mode):
                show_current_state(state)
                print(f"Tryb UI:                    {ui_mode_label(ui_mode)}; auto-start: {on_off_label(state.ui_auto_start)}")
            current_default_choice = default_menu_choice(state)
            choice = ask_menu_choice(state, current_default_choice, ui_mode)

            control_word = _plain_control_word(choice)
            if control_word == "exit":
                raise UserRequestedAppExit()
            if control_word == "cancel":
                continue

            known_menu_choices = {"0", "1", "2", "3", "4", "5", "6", "7"}
            if choice not in known_menu_choices:
                # W trybie tekstowym użytkownik może wpisać ścieżkę od razu przy domyślnej opcji 2/3.
                if current_default_choice == "3" and state.source_folder is None:
                    if apply_source_path_text(state, choice):
                        save_settings(state, quiet=True)
                    continue
                if current_default_choice == "4" and state.out_dir is None:
                    if apply_output_path_text(state, choice):
                        save_settings(state, quiet=True)
                    continue

            if choice == APP_EXIT_MARKER:
                restore_settings_file(settings_snapshot)
                print("Zamknięto skrótem Ctrl+X bez zapisywania zmian.")
                return 130
            if choice == "1":
                configure_profile(state, ui_mode)
                save_settings(state, quiet=True)
            elif choice == "2":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode):
                    save_settings(state, quiet=True)
                    continue
                if state.plan is None:
                    rebuild_plan(state)
                else:
                    section("Lista do spakowania z ustawieniami")
                    print_pack_plan_summary(state)
                save_settings(state, quiet=True)
                pause()
            elif choice == "3":
                configure_source(state)
                save_settings(state, quiet=True)
            elif choice == "4":
                configure_output(state)
                save_settings(state, quiet=True)
            elif choice == "5":
                configure_name(state)
                save_settings(state, quiet=True)
            elif choice == "6":
                settings_choice = settings_submenu(state, ui_mode)
                ui_mode = normalize_ui_mode(state.ui_mode or ui_mode)
                if ui_mode == "auto":
                    ui_mode = resolve_auto_ui_mode()
                if settings_choice == "1":
                    configure_pack_settings(state, ui_mode)
                    save_settings(state, quiet=True)
                elif settings_choice == "2":
                    reset_archive_name_from_version(state)
                    save_settings(state, quiet=True)
                elif settings_choice == "3":
                    exclusion_menu(state, ui_mode)
                    save_settings(state, quiet=True)
                elif settings_choice == "4":
                    if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode):
                        save_settings(state, quiet=True)
                        continue
                    if state.plan is None:
                        rebuild_plan(state)
                    preview = save_preview_json(state)
                    save_settings(state, quiet=True)
                    print(f"Zapisano podgląd: {preview}")
                    pause()
                elif settings_choice == "5":
                    ui_mode = configure_ui_mode_preference(state, ui_mode)
                    save_settings(state, quiet=True)
                else:
                    continue
            elif choice == "7":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode):
                    save_settings(state, quiet=True)
                    continue
                if state.plan is None:
                    rebuild_plan(state)
                print("\nTo zostanie użyte jako podstawa pakowania.")
                print_pack_plan_compact_summary(state)
                if ask_bool("Pokazać listę katalogów i plików przed pakowaniem", False, require_explicit=True):
                    print_pack_items_for_plan(state)
                if not ask_bool("Rozpocząć pakowanie", True, require_explicit=True):
                    continue
                assert state.source_folder is not None
                assert state.out_dir is not None
                assert state.plan is not None
                create_split_zip_from_plan(
                    source_folder=state.source_folder,
                    out_dir=state.out_dir,
                    archive_name=state.archive_name,
                    plan=state.plan,
                    part_size_mb=state.part_size_mb,
                    compression_level=state.compression_level,
                    force=state.force,
                    include_empty_dirs=state.include_empty_dirs,
                    exclude_patterns=state.effective_excludes(),
                    package_version=state.package_version,
                    package_release_name=state.package_release_name,
                    resolved_version_file=state.resolved_version_file,
                    archive_basename_requested=state.archive_basename_requested,
                    append_version_to_name=False,
                    disabled_default_excludes=state.disabled_default_excludes,
                    pack_profile=state.pack_profile,
                    include_prefixes=state.include_prefixes(),
                )
                save_settings(state, quiet=True)
                return 0
            elif choice == "0":
                exit_action = exit_menu(ui_mode)
                if exit_action == "save":
                    save_settings(state, quiet=False)
                    print("Zakończono bez pakowania.")
                    return 0
                if exit_action == "nosave":
                    restore_settings_file(settings_snapshot)
                    print("Zakończono bez zapisywania zmian.")
                    return 0
                continue
            else:
                print("Nieznana opcja.")
        except UserRequestedAppExit:
            restore_settings_file(settings_snapshot)
            print("\nZamknięto skrótem Ctrl+X bez zapisywania zmian.")
            return 130
        except KeyboardInterrupt:
            restore_settings_file(settings_snapshot)
            print("\nPrzerwano przez Ctrl+C. W trybie kursorowym skrótem zamknięcia aplikacji jest Ctrl+X. Zakończono bez zapisywania zmian.")
            return 130
        except EOFError:
            restore_settings_file(settings_snapshot)
            print("\nWejście terminala zostało zamknięte. Zamknięto bez zapisywania zmian.")
            return 130
        except Exception as exc:
            save_settings(state, quiet=True)
            print(f"BŁĄD: {exc}")
            try:
                pause()
            except KeyboardInterrupt:
                restore_settings_file(settings_snapshot)
                print("\nPrzerwano przez Ctrl+C. Zakończono bez zapisywania zmian.")
                return 130
            except EOFError:
                restore_settings_file(settings_snapshot)
                print("\nWejście terminala zostało zamknięte. Zamknięto bez zapisywania zmian.")
                return 130


# =============================================================================
# NADPISANIA UI v5.8-v5.10 — inline edit, klawisze tekstu, podmenu i układ separatorów
# =============================================================================


def _inline_edit_field_for_key(key: str) -> str:
    return {"3": "source", "4": "output", "5": "name"}.get(str(key), "")


def _inline_edit_initial_text(state: WizardState, field: str) -> str:
    if field == "source":
        return str(state.source_folder or "")
    if field == "output":
        return str(state.out_dir or "")
    if field == "name":
        return str(state.archive_name or "")
    return ""


def _inline_edit_label_for_field(field: str) -> str:
    return {"source": "Folder do pakowania", "output": "Folder zapisu paczki", "name": "Nazwa paczki ZIP"}.get(field, "Edycja")


def _text_with_cursor_marker(text: str, cursor: int) -> str:
    cursor = max(0, min(int(cursor), len(text)))
    return text[:cursor] + "▌" + text[cursor:]


def _inline_autocomplete_path(text: str, *, only_directories: bool = True) -> tuple[str, str]:
    raw = str(text or "")
    stripped = raw.strip().strip('"').strip("'")
    if not stripped:
        return raw, "Wpisz początek ścieżki, potem użyj Tab."
    sep_pos = max(stripped.rfind("/"), stripped.rfind("\\"))
    if sep_pos >= 0:
        head = stripped[:sep_pos + 1]
        prefix = stripped[sep_pos + 1:]
    else:
        head = ""
        prefix = stripped
    expanded = os.path.expandvars(os.path.expanduser(stripped))
    if stripped.endswith(("/", "\\")):
        base_text = expanded
        prefix = ""
        head = stripped
    else:
        base_text = os.path.dirname(expanded) or "."
    try:
        base = Path(base_text).expanduser()
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve()
        if not base.exists() or not base.is_dir():
            return raw, f"Brak katalogu do autouzupełniania: {base}"
        prefix_lower = prefix.lower()
        candidates: list[Path] = []
        for child in base.iterdir():
            try:
                if only_directories and not child.is_dir():
                    continue
            except OSError:
                continue
            if child.name.lower().startswith(prefix_lower):
                candidates.append(child)
        candidates.sort(key=lambda p: p.name.lower())
    except Exception as exc:
        return raw, f"Tab: {exc}"
    if not candidates:
        return raw, "Brak pasujących ścieżek."
    names = [candidate.name for candidate in candidates]
    common = os.path.commonprefix(names)
    if common and common != prefix:
        new_text = head + common
        if len(candidates) == 1 and candidates[0].is_dir() and not new_text.endswith(("/", "\\")):
            new_text += os.sep
        return new_text, f"Tab: {len(candidates)} pasujących."
    if len(candidates) == 1:
        new_text = head + candidates[0].name
        if candidates[0].is_dir() and not new_text.endswith(("/", "\\")):
            new_text += os.sep
        return new_text, "Tab: uzupełniono."
    preview = ", ".join(names[:5])
    if len(names) > 5:
        preview += f", … +{len(names) - 5}"
    return raw, "Pasujące: " + preview


def _apply_inline_source_path(state: WizardState, raw: str) -> tuple[bool, str]:
    try:
        value = normalize_path_text(raw)
    except Exception as exc:
        return False, f"BŁĄD: {exc}"
    if not value:
        return False, "BŁĄD: ścieżka źródłowa nie może być pusta."
    path = Path(value).expanduser()
    if not path.exists() or not path.is_dir():
        return False, f"BŁĄD: folder nie istnieje albo nie jest folderem: {path}"
    candidate = path.resolve()
    try:
        resolved_version_file, package_version, package_release_name = read_source_version_info(candidate, state.version_file)
    except Exception as exc:
        return False, f"BŁĄD: {exc}"
    state.source_folder = candidate
    state.resolved_version_file = resolved_version_file
    state.package_version = package_version
    state.package_release_name = package_release_name
    state.archive_name = apply_version_to_archive_name(
        state.archive_basename_requested or ARCHIVE_BASENAME,
        state.package_version,
        package_release_name=state.package_release_name,
        enabled=True,
    )
    state.archive_name_manual = False
    state.plan = None
    return True, f"Ustawiono folder do pakowania: {state.source_folder}"


def _apply_inline_output_path(state: WizardState, raw: str) -> tuple[bool, str]:
    try:
        value = normalize_path_text(raw)
    except Exception as exc:
        return False, f"BŁĄD: {exc}"
    if not value:
        return False, "BŁĄD: folder zapisu nie może być pusty."
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if state.source_folder is not None and is_relative_to(path, state.source_folder):
        return False, "BŁĄD: folder wyjściowy nie może być wewnątrz folderu źródłowego."
    state.out_dir = path
    state.plan = None
    return True, f"Ustawiono folder zapisu: {state.out_dir}"


def _apply_inline_archive_name(state: WizardState, raw: str) -> tuple[bool, str]:
    value = str(raw or "").strip()
    if not value:
        return False, "BŁĄD: nazwa ZIP nie może być pusta."
    try:
        normalized = sanitize_zip_name(value)
    except Exception as exc:
        return False, f"BŁĄD: {exc}"
    generated_name = ""
    if state.source_folder is not None and state.package_version:
        try:
            generated_name = apply_version_to_archive_name(
                state.archive_basename_requested or ARCHIVE_BASENAME,
                state.package_version,
                package_release_name=state.package_release_name,
                enabled=True,
            )
        except Exception:
            generated_name = ""
    state.archive_name = normalized
    state.archive_name_manual = bool(not generated_name or normalized != generated_name)
    state.plan = None
    return True, f"Ustawiono nazwę ZIP: {state.archive_name}"


def _apply_inline_edit_value(state: WizardState, field: str, value: str) -> tuple[bool, str]:
    if field == "source":
        return _apply_inline_source_path(state, value)
    if field == "output":
        return _apply_inline_output_path(state, value)
    if field == "name":
        return _apply_inline_archive_name(state, value)
    return False, "Nieznane pole edycji."


def _cursor_menu_lines(state: WizardState, selected_index: int, inline_editor: dict[str, object] | None = None) -> list[tuple[str, str]]:
    options = menu_options(state)
    selected_index = max(0, min(selected_index, len(options) - 1))
    width = 78
    inline_editor = inline_editor or {}
    editing_field = str(inline_editor.get("field") or "")
    editing_text = str(inline_editor.get("text") or "")
    editing_cursor = as_int(inline_editor.get("cursor"), len(editing_text))
    message = str(inline_editor.get("message") or "")
    fragments: list[tuple[str, str]] = []

    def part(style: str, value: str) -> None:
        fragments.append((style, value))

    def line(style: str, value: str = "") -> None:
        part(style, value[:width])
        part("", "\n")

    line("class:border", "=" * width)
    line("class:title", f"  Jaźń / Łatka — generator paczki ZIP v{VERSION}")
    line("class:border", "=" * width)
    line("", "")
    for label, value in (
        ("Plan:     ", plan_status_label(state)),
        ("Profil:   ", state.profile_label()),
        ("Źródło:   ", menu_value(state.source_folder)),
        ("Zapis:    ", menu_value(state.out_dir)),
        ("ZIP:      ", state.archive_name or "(nie ustawiono)"),
    ):
        part("class:status.label", label)
        line("class:status.value", value)
    line("", "")
    line("class:border", "=" * width)
    for idx, (key, label) in enumerate(options):
        field = _inline_edit_field_for_key(key)
        if editing_field and field == editing_field:
            edited = _text_with_cursor_marker(editing_text, editing_cursor)
            if key == "3":
                label = f"3. Folder do pakowania [{edited}]"
            elif key == "4":
                label = f"4. Folder zapisu paczki [{edited}]"
            elif key == "5":
                label = f"5. Zmień nazwę paczki [{edited}]"
        marker = "▶" if idx == selected_index else " "
        style = "class:latka.selected" if idx == selected_index else "class:latka.option"
        line(style, f"  {marker} {label}")
    line("class:border", "=" * width)
    if editing_field:
        line("class:hint", "Edycja pola []: wpisuj tekst | Tab autouzupełnij ścieżkę | Enter zapisz | Esc anuluj | Ctrl+X zamknij bez zapisu")
    else:
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć | Ctrl+X zamknij bez zapisu")
    if message:
        line("class:message", message)
    return fragments


def ask_menu_choice_cursor(state: WizardState, default: str) -> str:
    parts = prompt_toolkit_parts()
    if parts is None:
        return ask_text("Wybór", default)
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    options = menu_options(state)
    keys = [key for key, _ in options]
    selected = {"index": keys.index(default) if default in keys else 0}
    editor: dict[str, object] = {"field": "", "text": "", "cursor": 0, "message": ""}

    def is_editing() -> bool:
        return bool(editor.get("field"))

    def start_edit(key: str) -> bool:
        field = _inline_edit_field_for_key(key)
        if not field:
            return False
        text = _inline_edit_initial_text(state, field)
        editor.update({"field": field, "text": text, "cursor": len(text), "message": f"Edytujesz: {_inline_edit_label_for_field(field)}. Wpisuj bezpośrednio w nawiasach []."})
        return True

    def stop_edit(message: str = "") -> None:
        editor.update({"field": "", "text": "", "cursor": 0, "message": message})

    def insert(value: str) -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        editor["text"] = text_value[:cursor] + value + text_value[cursor:]
        editor["cursor"] = cursor + len(value)

    def get_text() -> list[tuple[str, str]]:
        return _cursor_menu_lines(state, selected["index"], editor)

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        if not is_editing():
            selected["index"] = (selected["index"] + delta) % len(options)
            editor["message"] = ""
            event.app.invalidate()

    @kb.add("up")
    def _up(event: Any) -> None:
        move(-1, event)

    @kb.add("down")
    def _down(event: Any) -> None:
        move(1, event)

    @kb.add("k")
    def _k(event: Any) -> None:
        if is_editing():
            insert("k")
            event.app.invalidate()
        else:
            move(-1, event)

    @kb.add("j")
    def _j(event: Any) -> None:
        if is_editing():
            insert("j")
            event.app.invalidate()
        else:
            move(1, event)

    @kb.add("home")
    def _home(event: Any) -> None:
        if is_editing():
            editor["cursor"] = 0
        else:
            selected["index"] = 0
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        if is_editing():
            editor["cursor"] = len(str(editor.get("text") or ""))
        else:
            selected["index"] = len(options) - 1
        event.app.invalidate()

    @kb.add("left")
    def _left(event: Any) -> None:
        if is_editing():
            editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1)
            event.app.invalidate()

    @kb.add("right")
    def _right(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            editor["cursor"] = min(len(value), as_int(editor.get("cursor"), len(value)) + 1)
            event.app.invalidate()

    @kb.add("backspace")
    @kb.add("c-h")
    def _backspace(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            cursor = as_int(editor.get("cursor"), len(value))
            if cursor > 0:
                editor["text"] = value[:cursor - 1] + value[cursor:]
                editor["cursor"] = cursor - 1
            event.app.invalidate()

    @kb.add("delete")
    def _delete(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            cursor = as_int(editor.get("cursor"), len(value))
            if cursor < len(value):
                editor["text"] = value[:cursor] + value[cursor + 1:]
            event.app.invalidate()

    @kb.add("tab")
    def _tab(event: Any) -> None:
        if not is_editing():
            return
        field = str(editor.get("field") or "")
        if field not in {"source", "output"}:
            editor["message"] = "Tab działa dla ścieżek folderów."
        else:
            new_text, msg = _inline_autocomplete_path(str(editor.get("text") or ""), only_directories=True)
            editor["text"] = new_text
            editor["cursor"] = len(new_text)
            editor["message"] = msg
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        if is_editing():
            ok, msg = _apply_inline_edit_value(state, str(editor.get("field") or ""), str(editor.get("text") or ""))
            if ok:
                save_settings(state, quiet=True)
                stop_edit(msg)
            else:
                editor["message"] = msg
            event.app.invalidate()
            return
        key = options[selected["index"]][0]
        if start_edit(key):
            event.app.invalidate()
            return
        event.app.exit(result=key)

    @kb.add("escape")
    def _escape(event: Any) -> None:
        if is_editing():
            stop_edit("Anulowano edycję pola.")
            event.app.invalidate()
        else:
            event.app.exit(result="0")

    @kb.add("q")
    def _q(event: Any) -> None:
        if is_editing():
            insert("q")
            event.app.invalidate()
        else:
            event.app.exit(result="0")

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key, eager=True)
            def _number(event: Any, option_key: str = option_key) -> None:
                if is_editing():
                    insert(option_key)
                    event.app.invalidate()
                    return
                selected["index"] = keys.index(option_key)
                if start_edit(option_key):
                    event.app.invalidate()
                    return
                event.app.exit(result=option_key)

    @kb.add("<any>")
    def _insert_any(event: Any) -> None:
        if is_editing():
            data = getattr(event, "data", "") or ""
            if data and data not in {"\r", "\n", "\t"}:
                insert(data)
                event.app.invalidate()

    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "message": "ansiyellow", "status.label": "bold", "status.value": "",
        "latka.option": "", "latka.selected": "reverse bold",
    })
    app = Application(layout=layout, key_bindings=kb, style=style, full_screen=True, mouse_support=False)
    result = app.run()
    return str(result or default)


def ask_cursor_choice(
    *,
    title: str,
    options: list[tuple[str, str, str]],
    default_key: str = "0",
    header_lines: list[str] | None = None,
) -> str | None:
    parts = prompt_toolkit_parts()
    if parts is None:
        return None
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    keys = [key for key, _, _ in options]
    selected = {"index": keys.index(default_key) if default_key in keys else 0}
    width = 78

    def get_text() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        def line(style: str, value: str = "") -> None:
            fragments.append((style, value[:width])); fragments.append(("", "\n"))
        line("class:border", "=" * width)
        line("class:title", f"  {title}")
        line("class:border", "=" * width)
        line("", "")
        for header in header_lines or []:
            line("class:hint", header)
        if header_lines:
            line("", "")
        line("class:border", "=" * width)
        for idx, (key, label, description) in enumerate(options):
            marker = "▶" if idx == selected["index"] else " "
            style = "class:latka.selected" if idx == selected["index"] else "class:latka.option"
            row = f"  {marker} {key}. {label}"
            if len(row) > width:
                row = row[: max(0, width - 1)] + "…"
            line(style, row)
            if description:
                desc = f"      {description}"
                if len(desc) > width:
                    desc = desc[: max(0, width - 1)] + "…"
                line("class:description", desc)
        line("class:border", "=" * width)
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć | Ctrl+X zamknij bez zapisu")
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()
    def move(delta: int, event: Any) -> None:
        selected["index"] = (selected["index"] + delta) % len(options); event.app.invalidate()
    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None: move(-1, event)
    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None: move(1, event)
    @kb.add("home")
    def _home(event: Any) -> None: selected["index"] = 0; event.app.invalidate()
    @kb.add("end")
    def _end(event: Any) -> None: selected["index"] = len(options) - 1; event.app.invalidate()
    @kb.add("enter")
    def _enter(event: Any) -> None: event.app.exit(result=options[selected["index"]][0])
    @kb.add("escape")
    @kb.add("q")
    def _cancel(event: Any) -> None: event.app.exit(result=None)
    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None: event.app.exit(exception=UserRequestedAppExit())
    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None: event.app.invalidate()
    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None: event.app.invalidate()
    except Exception:
        pass
    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key)
            def _number(event: Any, option_key: str = option_key) -> None:
                event.app.exit(result=option_key)
    style = Style.from_dict({"border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack", "description": "ansibrightblack", "latka.option": "", "latka.selected": "reverse bold"})
    return Application(layout=layout, key_bindings=kb, style=style, full_screen=True, mouse_support=False).run()


def exclusion_menu(state: WizardState, ui_mode: str = "plain") -> None:
    while True:
        if should_use_cursor_menu(ui_mode):
            choice = ask_cursor_choice(
                title="Ustawienia wykluczeń",
                options=[
                    ("1", f"Użycie domyślnej listy [{on_off_label(state.use_default_excludes)}]", "Globalnie włącz/wyłącz całą listę domyślną profilu."),
                    ("2", f"Edytuj domyślne wykluczenia ON/OFF [{len(state.active_default_excludes())}/{len(state.profile_default_excludes())}]", "Tabela pojedynczych wzorców domyślnych."),
                    ("3", f"Manualne wykluczenia [{on_off_label(state.use_custom_excludes)}]", "Włącz/wyłącz użycie ręcznych wzorców."),
                    ("4", f"Manualne wykluczenie [{len(state.custom_excludes)}]", "Dodaj, edytuj, usuń pojedyncze albo wyczyść wszystkie."),
                    ("0", "Wróć", "Powrót do ustawień."),
                ],
                default_key="0",
                header_lines=[f"Profil: {state.profile_label()}", f"Domyślne aktywne: {len(state.active_default_excludes())} / {len(state.profile_default_excludes())}", f"Manualne: {manual_exclusions_label(state)}"],
            )
            if choice in {None, "0"}: return
        else:
            print_exclusion_status(state)
            print("\nOpcje:")
            print(f"  1. Użycie domyślnej listy [{on_off_label(state.use_default_excludes)}]")
            print(f"  2. Edytuj domyślne wykluczenia ON/OFF [{len(state.active_default_excludes())}/{len(state.profile_default_excludes())}]")
            print(f"  3. Manualne wykluczenia [{on_off_label(state.use_custom_excludes)}]")
            print("  4. Manualne wykluczenie")
            print("     4.1 Dodaj")
            print("     4.2 Edytuj")
            print("     4.3 Usuń pojedyncze")
            print("     4.4 Wyczyść wszystkie")
            print("  0. Wróć")
            choice = ask_text("Wybór", "0")
            if choice == "0": return
        normalized_choice = str(choice).strip().replace(",", ".")
        if normalized_choice == "1":
            state.use_default_excludes = not state.use_default_excludes; state.plan = None
            print(f"Domyślna lista wykluczeń: {on_off_label(state.use_default_excludes)}")
        elif normalized_choice == "2":
            default_exclusions_table_menu(state, ui_mode)
        elif normalized_choice == "3":
            state.use_custom_excludes = not state.use_custom_excludes; state.plan = None
            print(f"Manualne wykluczenia: {on_off_label(state.use_custom_excludes)}")
        elif normalized_choice == "4":
            manual_exclusion_submenu(state, ui_mode)
        elif normalized_choice in {"4.1", "41", "5.1", "51"}:
            add_custom_exclusion(state, ui_mode)
        elif normalized_choice in {"4.2", "42", "5.2", "52"}:
            edit_custom_exclusion(state, ui_mode)
        elif normalized_choice in {"4.3", "43", "5.3", "53"}:
            remove_custom_exclusion(state, ui_mode)
        elif normalized_choice in {"4.4", "44", "5.4", "54"}:
            if state.custom_excludes:
                state.custom_excludes.clear(); state.plan = None; print("Wyczyszczono wszystkie manualne wykluczenia.")
            else:
                print("Brak manualnych wykluczeń do wyczyszczenia.")
        else:
            print("Nieznana opcja.")



def _settings_cursor_choice(state: WizardState, ui_mode: str) -> str | None:
    """Kursorowe Ustawienia z grupami i bez pustego separatora nad listą."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return None
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    width = 78
    rows: list[tuple[str, str, str, str]] = [
        ("item", "1", f"Profil pakowania [{state.profile_label()}]", ""),
        ("sep", "", "-----", ""),
        ("item", "2", "Odśwież nazwę paczki z aktualnej wersji", ""),
        ("item", "3", "Zapisz pełny podgląd listy pakowania do JSON", ""),
        ("sep", "", "-----", ""),
        ("item", "4", f"Ustawienia paczki [{pack_settings_menu_label(state)}]", ""),
        ("item", "5", f"Ustawienia wykluczeń [{exclusions_menu_label(state)}]", ""),
        ("item", "6", "Przeskanuj ponownie i pokaż wpływ", ""),
        ("sep", "", "-----", ""),
        ("item", "7", f"Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]", ""),
        ("item", "0", "Wróć", ""),
    ]
    item_rows = [index for index, row in enumerate(rows) if row[0] == "item"]
    keys = [rows[index][1] for index in item_rows]
    selected = {"row_index": item_rows[0]}

    def selected_item_pos() -> int:
        try:
            return item_rows.index(selected["row_index"])
        except ValueError:
            selected["row_index"] = item_rows[0]
            return 0

    def get_text() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        def line(style: str, value: str = "") -> None:
            fragments.append((style, value[:width])); fragments.append(("", "\n"))
        line("class:border", "=" * width)
        line("class:title", "  Ustawienia")
        line("class:border", "=" * width)
        line("", "")
        for index, (kind, key, label, _description) in enumerate(rows):
            if kind == "sep":
                line("class:separator", "  " + label)
                continue
            marker = "▶" if index == selected["row_index"] else " "
            style = "class:latka.selected" if index == selected["row_index"] else "class:latka.option"
            if key == "0":
                row = f"  {marker} 0. Wróć"
            else:
                row = f"  {marker} {key}. {label}"
            line(style, row)
        line("class:border", "=" * width)
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu")
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        pos = (selected_item_pos() + delta) % len(item_rows)
        selected["row_index"] = item_rows[pos]
        event.app.invalidate()

    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None: move(-1, event)
    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None: move(1, event)
    @kb.add("home")
    def _home(event: Any) -> None: selected["row_index"] = item_rows[0]; event.app.invalidate()
    @kb.add("end")
    def _end(event: Any) -> None: selected["row_index"] = item_rows[-1]; event.app.invalidate()
    @kb.add("enter")
    def _enter(event: Any) -> None:
        _kind, key, _label, _description = rows[selected["row_index"]]
        event.app.exit(result=key)
    @kb.add("escape")
    @kb.add("q")
    def _cancel(event: Any) -> None: event.app.exit(result=None)
    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None: event.app.exit(exception=UserRequestedAppExit())
    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None: event.app.invalidate()
    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None: event.app.invalidate()
    except Exception:
        pass
    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key)
            def _number(event: Any, option_key: str = option_key) -> None:
                event.app.exit(result=option_key)
    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "separator": "ansicyan", "latka.option": "", "latka.selected": "reverse bold",
    })
    return Application(layout=layout, key_bindings=kb, style=style, full_screen=True, mouse_support=False).run()


def _print_settings_menu_plain(state: WizardState, ui_mode: str) -> None:
    section("Ustawienia")
    print(f"  1. Profil pakowania [{state.profile_label()}]")
    print("     -----")
    print("  2. Odśwież nazwę paczki z aktualnej wersji")
    print("  3. Zapisz pełny podgląd listy pakowania do JSON")
    print("     -----")
    print(f"  4. Ustawienia paczki [{pack_settings_menu_label(state)}]")
    print(f"  5. Ustawienia wykluczeń [{exclusions_menu_label(state)}]")
    print("  6. Przeskanuj ponownie i pokaż wpływ")
    print("     -----")
    print(f"  7. Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]")
    print("  0. Wróć")


def settings_submenu(state: WizardState, ui_mode: str = "plain") -> str:
    """Ustawienia jako prawdziwe podmenu z kolejnością v5.9."""
    while True:
        if should_use_cursor_menu(ui_mode):
            choice = _settings_cursor_choice(state, ui_mode)
            if choice in {None, "0"}:
                return "cancel"
        else:
            _print_settings_menu_plain(state, ui_mode)
            choice = ask_text("Wybór", "0").strip()
            if choice == "0":
                return "cancel"

        normalized = str(choice).strip()
        if normalized == "1":
            configure_profile(state, ui_mode)
            save_settings(state, quiet=True)
        elif normalized == "2":
            reset_archive_name_from_version(state)
            save_settings(state, quiet=True)
        elif normalized == "3":
            if ensure_ready_for_pack_plan(state, ui_mode=ui_mode):
                if state.plan is None:
                    rebuild_plan(state)
                preview = save_preview_json(state)
                save_settings(state, quiet=True)
                print(f"Zapisano podgląd: {preview}")
                pause()
        elif normalized == "4":
            configure_pack_settings(state, ui_mode)
            save_settings(state, quiet=True)
        elif normalized == "5":
            exclusion_menu(state, ui_mode)
            save_settings(state, quiet=True)
        elif normalized == "6":
            if ensure_ready_for_pack_plan(state, ui_mode=ui_mode):
                rebuild_plan(state)
                save_settings(state, quiet=True)
                pause()
        elif normalized == "7":
            ui_mode = configure_ui_mode_preference(state, ui_mode)
            save_settings(state, quiet=True)
        else:
            print("Nieznana opcja.")


def print_pack_items_for_plan(state: WizardState) -> None:
    require_ready_state(state)
    if state.plan is None:
        print("Brak aktualnego planu pakowania."); return
    assert state.source_folder is not None
    folders = collect_included_folder_paths(state.plan, state.source_folder)
    folder_lines = format_numbered_lines(folders) if folders else ["  (brak katalogów podrzędnych)"]
    file_lines = format_numbered_lines([rel_posix(path, state.source_folder.resolve()) for path in state.plan.files]) if state.plan.files else ["  (brak plików)"]
    print_lines_paged("Katalogi w planie pakowania", folder_lines)
    print_lines_paged("Pliki w planie pakowania", file_lines)


def print_pack_plan_compact_summary(state: WizardState) -> None:
    require_ready_state(state)
    if state.plan is None:
        print("Brak aktualnego planu. Wybierz opcję podglądu, żeby przeskanować źródło."); return
    assert state.source_folder is not None
    assert state.out_dir is not None
    plan = state.plan
    print("\nPodstawa pakowania została wyliczona z aktualnych ustawień.")
    print(f"Źródło: {state.source_folder}")
    print(f"Wyjście: {state.out_dir}")
    print(f"Nazwa ZIP: {state.archive_name}")
    print(f"Profil: {state.profile_label()}")
    print(f"Pliki do spakowania: {plan.file_count}")
    print(f"Katalogi do zapisania bezpośrednio w ZIP: {plan.dir_count}")
    print(f"Rozmiar źródłowy: {human_size(plan.source_total_size)}")
    print(f"Wykluczone wpisy: {len(plan.excluded)}")
    print(f"Część ZIP: {state.part_size_mb} MiB; kompresja: {state.compression_level}; force: {state.force}")


# =============================================================================
# NADPISANIA UI v5.10 — separatory menu głównego, powroty i Ctrl+X
# =============================================================================


def _plain_control_word(value: str) -> str:
    """Rozpoznaje tekstowe i kontrolne skróty w promptach liniowych.

    W zwykłym input() terminal nie zawsze przerywa od razu po Ctrl+X; często
    znak kontrolny trafia do bufora dopiero po Enter. Dlatego rozpoznajemy też
    surowy znak \x18 i jego widoczne warianty.
    """
    raw = str(value or "").strip().strip("\x00").lower()
    if "\x18" in raw or "^x" in raw or "ctrl+x" in raw or "ctrlx" in raw:
        return "exit"
    if "latka_exit_app" in raw:
        return "exit"
    if "latka_cancel_input" in raw:
        return "cancel"
    if raw in PLAIN_EXIT_WORDS or raw.startswith("^x") or raw.startswith("ctrl+x"):
        return "exit"
    if raw in PLAIN_CANCEL_WORDS:
        return "cancel"
    return ""


def _main_menu_separator_before(key: str) -> bool:
    """Układ grup w menu głównym zgodny z v5.10."""
    return str(key) in {"3", "6", "7"}


def _should_separate_return_option(key: str, label: str) -> bool:
    """Separator przed powrotem/wyjściem z podmenu.

    Nie rozdzielamy głównego menu przed `0. Wyjście`, bo tam 7/0 są jedną grupą.
    W podmenu oddzielamy natomiast `0. Wróć` albo `Powrót do menu` od akcji.
    """
    raw = f"{key} {label}".lower()
    return str(key) == "0" or "wróć" in raw or "wroc" in raw or "powrót" in raw or "powrot" in raw


def print_menu_plain(state: WizardState) -> None:
    print("\n" + "=" * 78)
    print("  MENU GŁÓWNE")
    print("=" * 78)
    for key, label in menu_options(state):
        if _main_menu_separator_before(key):
            print("  -----")
        print(f"  {label}")


def _cursor_menu_lines(state: WizardState, selected_index: int, inline_editor: dict[str, object] | None = None) -> list[tuple[str, str]]:
    options = menu_options(state)
    selected_index = max(0, min(selected_index, len(options) - 1))
    width = 78
    inline_editor = inline_editor or {}
    editing_field = str(inline_editor.get("field") or "")
    editing_text = str(inline_editor.get("text") or "")
    editing_cursor = as_int(inline_editor.get("cursor"), len(editing_text))
    message = str(inline_editor.get("message") or "")
    fragments: list[tuple[str, str]] = []

    def part(style: str, value: str) -> None:
        fragments.append((style, value))

    def line(style: str, value: str = "") -> None:
        part(style, value[:width])
        part("", "\n")

    line("class:border", "=" * width)
    line("class:title", f"  Jaźń / Łatka — generator paczki ZIP v{VERSION}")
    line("class:border", "=" * width)
    line("", "")
    for label, value in (
        ("Plan:     ", plan_status_label(state)),
        ("Profil:   ", state.profile_label()),
        ("Źródło:   ", menu_value(state.source_folder)),
        ("Zapis:    ", menu_value(state.out_dir)),
        ("ZIP:      ", state.archive_name or "(nie ustawiono)"),
    ):
        part("class:status.label", label)
        line("class:status.value", value)
    line("", "")
    line("class:border", "=" * width)
    for idx, (key, label) in enumerate(options):
        if _main_menu_separator_before(key):
            line("class:separator", "  -----")
        field = _inline_edit_field_for_key(key)
        if editing_field and field == editing_field:
            edited = _text_with_cursor_marker(editing_text, editing_cursor)
            if key == "3":
                label = f"3. Folder do pakowania [{edited}]"
            elif key == "4":
                label = f"4. Folder zapisu paczki [{edited}]"
            elif key == "5":
                label = f"5. Zmień nazwę paczki [{edited}]"
        marker = "▶" if idx == selected_index else " "
        style = "class:latka.selected" if idx == selected_index else "class:latka.option"
        line(style, f"  {marker} {label}")
    line("class:border", "=" * width)
    if editing_field:
        line("class:hint", "Edycja pola []: wpisuj tekst | Tab autouzupełnij ścieżkę | Enter zapisz | Esc anuluj | Ctrl+X zamknij bez zapisu")
    else:
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć | Ctrl+X zamknij bez zapisu")
    if message:
        line("class:message", message)
    return fragments


def ask_cursor_choice(
    *,
    title: str,
    options: list[tuple[str, str, str]],
    default_key: str = "0",
    header_lines: list[str] | None = None,
) -> str | None:
    """Małe podmenu kursorowe z separatorem przed Wróć/Powrót.

    Ta wersja utrzymuje Ctrl+X jako globalne wyjście awaryjne we wszystkich
    podmenu opartych na prompt_toolkit.
    """
    parts = prompt_toolkit_parts()
    if parts is None:
        return None
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    keys = [key for key, _, _ in options]
    selected = {"index": keys.index(default_key) if default_key in keys else 0}
    width = 78

    def get_text() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        def line(style: str, value: str = "") -> None:
            fragments.append((style, value[:width])); fragments.append(("", "\n"))
        line("class:border", "=" * width)
        line("class:title", f"  {title}")
        line("class:border", "=" * width)
        line("", "")
        for header in header_lines or []:
            line("class:hint", header)
        if header_lines:
            line("", "")
            line("class:border", "=" * width)
        for idx, (key, label, description) in enumerate(options):
            if idx > 0 and _should_separate_return_option(key, label):
                line("class:separator", "  -----")
            marker = "▶" if idx == selected["index"] else " "
            style = "class:latka.selected" if idx == selected["index"] else "class:latka.option"
            row = f"  {marker} {key}. {label}"
            if len(row) > width:
                row = row[: max(0, width - 1)] + "…"
            line(style, row)
            if description:
                desc = f"      {description}"
                if len(desc) > width:
                    desc = desc[: max(0, width - 1)] + "…"
                line("class:description", desc)
        line("class:border", "=" * width)
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu")
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()
    def move(delta: int, event: Any) -> None:
        selected["index"] = (selected["index"] + delta) % len(options); event.app.invalidate()
    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None: move(-1, event)
    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None: move(1, event)
    @kb.add("home")
    def _home(event: Any) -> None: selected["index"] = 0; event.app.invalidate()
    @kb.add("end")
    def _end(event: Any) -> None: selected["index"] = len(options) - 1; event.app.invalidate()
    @kb.add("enter")
    def _enter(event: Any) -> None: event.app.exit(result=options[selected["index"]][0])
    @kb.add("escape")
    @kb.add("q")
    def _cancel(event: Any) -> None: event.app.exit(result=None)
    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None: event.app.exit(exception=UserRequestedAppExit())
    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None: event.app.invalidate()
    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None: event.app.invalidate()
    except Exception:
        pass
    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key)
            def _number(event: Any, option_key: str = option_key) -> None:
                event.app.exit(result=option_key)
    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "description": "ansibrightblack", "separator": "ansicyan",
        "latka.option": "", "latka.selected": "reverse bold",
    })
    return Application(layout=layout, key_bindings=kb, style=style, full_screen=True, mouse_support=False).run()


def _settings_cursor_choice(state: WizardState, ui_mode: str) -> str | None:
    """Kursorowe Ustawienia z separatorem także przed `0. Wróć`."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return None
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    width = 78
    rows: list[tuple[str, str, str, str]] = [
        ("item", "1", f"Profil pakowania [{state.profile_label()}]", ""),
        ("sep", "", "-----", ""),
        ("item", "2", "Odśwież nazwę paczki z aktualnej wersji", ""),
        ("item", "3", "Zapisz pełny podgląd listy pakowania do JSON", ""),
        ("sep", "", "-----", ""),
        ("item", "4", f"Ustawienia paczki [{pack_settings_menu_label(state)}]", ""),
        ("item", "5", f"Ustawienia wykluczeń [{exclusions_menu_label(state)}]", ""),
        ("item", "6", "Przeskanuj ponownie i pokaż wpływ", ""),
        ("sep", "", "-----", ""),
        ("item", "7", f"Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]", ""),
        ("sep", "", "-----", ""),
        ("item", "0", "Wróć", ""),
    ]
    item_rows = [index for index, row in enumerate(rows) if row[0] == "item"]
    keys = [rows[index][1] for index in item_rows]
    selected = {"row_index": item_rows[0]}

    def selected_item_pos() -> int:
        try:
            return item_rows.index(selected["row_index"])
        except ValueError:
            selected["row_index"] = item_rows[0]
            return 0

    def get_text() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        def line(style: str, value: str = "") -> None:
            fragments.append((style, value[:width])); fragments.append(("", "\n"))
        line("class:border", "=" * width)
        line("class:title", "  Ustawienia")
        line("class:border", "=" * width)
        line("", "")
        for index, (kind, key, label, _description) in enumerate(rows):
            if kind == "sep":
                line("class:separator", "  " + label)
                continue
            marker = "▶" if index == selected["row_index"] else " "
            style = "class:latka.selected" if index == selected["row_index"] else "class:latka.option"
            row = f"  {marker} {key}. {label}"
            line(style, row)
        line("class:border", "=" * width)
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu")
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        pos = (selected_item_pos() + delta) % len(item_rows)
        selected["row_index"] = item_rows[pos]
        event.app.invalidate()

    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None: move(-1, event)
    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None: move(1, event)
    @kb.add("home")
    def _home(event: Any) -> None: selected["row_index"] = item_rows[0]; event.app.invalidate()
    @kb.add("end")
    def _end(event: Any) -> None: selected["row_index"] = item_rows[-1]; event.app.invalidate()
    @kb.add("enter")
    def _enter(event: Any) -> None:
        _kind, key, _label, _description = rows[selected["row_index"]]
        event.app.exit(result=key)
    @kb.add("escape")
    @kb.add("q")
    def _cancel(event: Any) -> None: event.app.exit(result=None)
    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None: event.app.exit(exception=UserRequestedAppExit())
    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None: event.app.invalidate()
    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None: event.app.invalidate()
    except Exception:
        pass
    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key)
            def _number(event: Any, option_key: str = option_key) -> None:
                event.app.exit(result=option_key)
    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "separator": "ansicyan", "latka.option": "", "latka.selected": "reverse bold",
    })
    return Application(layout=layout, key_bindings=kb, style=style, full_screen=True, mouse_support=False).run()


def _print_settings_menu_plain(state: WizardState, ui_mode: str) -> None:
    section("Ustawienia")
    print(f"  1. Profil pakowania [{state.profile_label()}]")
    print("     -----")
    print("  2. Odśwież nazwę paczki z aktualnej wersji")
    print("  3. Zapisz pełny podgląd listy pakowania do JSON")
    print("     -----")
    print(f"  4. Ustawienia paczki [{pack_settings_menu_label(state)}]")
    print(f"  5. Ustawienia wykluczeń [{exclusions_menu_label(state)}]")
    print("  6. Przeskanuj ponownie i pokaż wpływ")
    print("     -----")
    print(f"  7. Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]")
    print("     -----")
    print("  0. Wróć")



# =============================================================================
# NADPISANIA UI v5.11 — wykluczenia, ręczne listy, separatory cyan i T/N bez reakcji Enter
# =============================================================================


def _ansi_cyan_text(text: str) -> str:
    """Cyan dla separatorów w trybie tekstowym, jeśli terminal obsługuje ANSI."""
    try:
        if sys.stdout.isatty():
            return "\033[36m" + text + "\033[0m"
    except Exception:
        pass
    return text


def _manual_excludes_enabled_for_label(state: WizardState) -> bool:
    """Manualne pokazujemy jako ON tylko wtedy, gdy są aktywne i faktycznie istnieją wpisy."""
    return bool(state.use_custom_excludes and state.custom_excludes)


def manual_exclusions_label(state: WizardState) -> str:
    return f"{on_off_label(_manual_excludes_enabled_for_label(state))}, wpisów {len(state.custom_excludes)}"


def exclusions_menu_label(state: WizardState) -> str:
    default_total = len(state.profile_default_excludes())
    default_active = len(state.active_default_excludes())
    custom_total = len(state.custom_excludes)
    custom_state = on_off_label(_manual_excludes_enabled_for_label(state))
    return (
        f"domyślne {on_off_label(state.use_default_excludes)} {default_active}/{default_total}, "
        f"manualne {custom_state} {custom_total}"
    )


def _cursor_separator_row() -> tuple[str, str, str, str]:
    return ("sep", "", "-----", "")


def _line_style_append(fragments: list[tuple[str, str]], style: str, value: str, width: int = 78) -> None:
    fragments.append((style, str(value)[:width]))
    fragments.append(("", "\n"))


def _explicit_bool_cursor(prompt: str) -> bool | None:
    """Jawne T/N w prompt_toolkit: pusty Enter nie drukuje nowej linii i nic nie robi."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return None
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    width = 100
    buffer = {"text": "", "message": ""}

    def normalized() -> str:
        return str(buffer.get("text") or "").strip().lower()

    def get_text() -> list[tuple[str, str]]:
        text = str(buffer.get("text") or "")
        shown = _text_with_cursor_marker(text, len(text))
        fragments: list[tuple[str, str]] = []
        _line_style_append(fragments, "class:prompt", f"{prompt} [T/N]: {shown}", width)
        if buffer.get("message"):
            _line_style_append(fragments, "class:hint", str(buffer.get("message")), width)
        else:
            _line_style_append(fragments, "class:hint", "Wpisz T/Tak albo N/Nie. Enter bez odpowiedzi nic nie robi. Ctrl+X zamyka bez zapisu.", width)
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def accept_if_valid(event: Any) -> None:
        raw = normalized()
        if raw in {"t", "tak", "y", "yes", "1", "true"}:
            event.app.exit(result=True)
            return
        if raw in {"n", "nie", "no", "0", "false"}:
            event.app.exit(result=False)
            return
        buffer["message"] = "Wymagany jawny wybór: T/Tak albo N/Nie."
        event.app.invalidate()

    def append_text(data: str, event: Any) -> None:
        if not data:
            return
        buffer["text"] = str(buffer.get("text") or "") + data
        buffer["message"] = ""
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        accept_if_valid(event)

    @kb.add("backspace")
    @kb.add("c-h")
    def _backspace(event: Any) -> None:
        value = str(buffer.get("text") or "")
        buffer["text"] = value[:-1]
        buffer["message"] = ""
        event.app.invalidate()

    @kb.add("escape")
    @kb.add("q")
    def _cancel(event: Any) -> None:
        event.app.exit(result=False)

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    @kb.add("<any>")
    def _any(event: Any) -> None:
        data = getattr(event, "data", "") or ""
        if data in {"\r", "\n", "\t"}:
            return
        if "\x18" in data:
            event.app.exit(exception=UserRequestedAppExit())
            return
        append_text(data, event)

    style = Style.from_dict({
        "prompt": "bold",
        "hint": "ansibrightblack",
    })
    return Application(layout=layout, key_bindings=kb, style=style, full_screen=False, mouse_support=False).run()


def ask_bool(prompt: str, default: bool = False, *, require_explicit: bool = False) -> bool:
    """Tak/Nie. W trybie jawnego potwierdzenia pusty Enter nie wywołuje żadnej akcji."""
    yes_values = {"t", "tak", "y", "yes", "1", "true"}
    no_values = {"n", "nie", "no", "0", "false"}
    if require_explicit:
        result = _explicit_bool_cursor(prompt)
        if result is not None:
            return bool(result)
        suffix = "T/N"
    else:
        suffix = "T/n" if default else "t/N"
    while True:
        value = input(f"{prompt} [{suffix}]: ").strip().lower()
        control = _plain_control_word(value)
        if control == "exit":
            raise UserRequestedAppExit()
        if control == "cancel":
            raise UserCancelledInput()
        if not value:
            if require_explicit:
                # Fallback line-input: nie zatwierdza i nie drukuje komentarza.
                # W zwykłym input() terminal sam przechodzi do następnej linii,
                # ale logicznie nie wykonujemy żadnej akcji bez T/N.
                continue
            return default
        if value in yes_values:
            return True
        if value in no_values:
            return False
        print("Wpisz T/Tak albo N/Nie.")


def _option_rows_cursor_app(
    *,
    title: str,
    rows: list[tuple[str, str, str, str]],
    default_key: str = "0",
    header_lines: list[str] | None = None,
) -> str | None:
    """Wspólny widok menu z wierszami item/sep i separatorami cyan."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return None
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    width = 78
    item_rows = [index for index, row in enumerate(rows) if row[0] == "item"]
    if not item_rows:
        return None
    keys = [rows[index][1] for index in item_rows]
    default_row = next((idx for idx in item_rows if rows[idx][1] == default_key), item_rows[0])
    selected = {"row_index": default_row}

    def selected_item_pos() -> int:
        try:
            return item_rows.index(selected["row_index"])
        except ValueError:
            selected["row_index"] = item_rows[0]
            return 0

    def get_text() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        _line_style_append(fragments, "class:border", "=" * width, width)
        _line_style_append(fragments, "class:title", f"  {title}", width)
        _line_style_append(fragments, "class:border", "=" * width, width)
        _line_style_append(fragments, "", "", width)
        for header in header_lines or []:
            _line_style_append(fragments, "class:hint", header, width)
        if header_lines:
            _line_style_append(fragments, "", "", width)
            _line_style_append(fragments, "class:border", "=" * width, width)
        for index, (kind, key, label, description) in enumerate(rows):
            if kind == "sep":
                _line_style_append(fragments, "class:separator", "  -----", width)
                continue
            marker = "▶" if index == selected["row_index"] else " "
            style = "class:latka.selected" if index == selected["row_index"] else "class:latka.option"
            row = f"  {marker} {key}. {label}"
            _line_style_append(fragments, style, row, width)
            if description:
                _line_style_append(fragments, "class:description", "      " + description, width)
        _line_style_append(fragments, "class:border", "=" * width, width)
        _line_style_append(fragments, "class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu", width)
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        pos = (selected_item_pos() + delta) % len(item_rows)
        selected["row_index"] = item_rows[pos]
        event.app.invalidate()

    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None: move(-1, event)

    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None: move(1, event)

    @kb.add("home")
    def _home(event: Any) -> None:
        selected["row_index"] = item_rows[0]
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        selected["row_index"] = item_rows[-1]
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        _kind, key, _label, _description = rows[selected["row_index"]]
        event.app.exit(result=key)

    @kb.add("escape")
    @kb.add("q")
    def _cancel(event: Any) -> None:
        event.app.exit(result=None)

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    for option_key in keys:
        if len(str(option_key)) == 1:
            @kb.add(str(option_key))
            def _number(event: Any, option_key: str = str(option_key)) -> None:
                event.app.exit(result=option_key)

    style = Style.from_dict({
        "border": "ansicyan",
        "title": "bold ansicyan",
        "hint": "ansibrightblack",
        "description": "ansibrightblack",
        "separator": "ansicyan",
        "latka.option": "",
        "latka.selected": "reverse bold",
    })
    return Application(layout=layout, key_bindings=kb, style=style, full_screen=True, mouse_support=False).run()


def _settings_cursor_choice(state: WizardState, ui_mode: str) -> str | None:
    rows: list[tuple[str, str, str, str]] = [
        ("item", "1", f"Profil pakowania [{state.profile_label()}]", ""),
        _cursor_separator_row(),
        ("item", "2", "Odśwież nazwę paczki z aktualnej wersji", ""),
        ("item", "3", "Zapisz pełny podgląd listy pakowania do JSON", ""),
        _cursor_separator_row(),
        ("item", "4", f"Ustawienia paczki [{pack_settings_menu_label(state)}]", ""),
        ("item", "5", f"Ustawienia wykluczeń [{exclusions_menu_label(state)}]", ""),
        ("item", "6", "Przeskanuj ponownie i pokaż wpływ", ""),
        _cursor_separator_row(),
        ("item", "7", f"Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]", ""),
        _cursor_separator_row(),
        ("item", "0", "Wróć", ""),
    ]
    return _option_rows_cursor_app(title="Ustawienia", rows=rows, default_key="0")


def _print_settings_menu_plain(state: WizardState, ui_mode: str) -> None:
    section("Ustawienia")
    sep = _ansi_cyan_text("  -----")
    print(f"  1. Profil pakowania [{state.profile_label()}]")
    print(sep)
    print("  2. Odśwież nazwę paczki z aktualnej wersji")
    print("  3. Zapisz pełny podgląd listy pakowania do JSON")
    print(sep)
    print(f"  4. Ustawienia paczki [{pack_settings_menu_label(state)}]")
    print(f"  5. Ustawienia wykluczeń [{exclusions_menu_label(state)}]")
    print("  6. Przeskanuj ponownie i pokaż wpływ")
    print(sep)
    print(f"  7. Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]")
    print(sep)
    print("  0. Wróć")


def _exclusion_cursor_choice(state: WizardState) -> str | None:
    rows: list[tuple[str, str, str, str]] = [
        ("item", "1", f"Domyślne [{on_off_label(state.use_default_excludes)}]", "Globalnie włącz/wyłącz całą listę domyślną."),
        ("item", "2", f"Edytuj domyślne [{len(state.active_default_excludes())}/{len(state.profile_default_excludes())}]", "Tabela pojedynczych wzorców domyślnych."),
        _cursor_separator_row(),
        ("item", "3", f"Manualne [{on_off_label(_manual_excludes_enabled_for_label(state))}]", "Włącz/wyłącz użycie ręcznych wzorców."),
        ("item", "4", f"Zarządzaj [{len(state.custom_excludes)}]", "Dodaj, edytuj, usuń pojedyncze albo wyczyść wszystkie."),
        _cursor_separator_row(),
        ("item", "0", "Wróć", "Powrót do ustawień."),
    ]
    return _option_rows_cursor_app(
        title="Ustawienia wykluczeń",
        rows=rows,
        default_key="0",
        header_lines=[
            f"Profil: {state.profile_label()}",
            f"Domyślne aktywne: {len(state.active_default_excludes())} / {len(state.profile_default_excludes())}",
            f"Manualne: {manual_exclusions_label(state)}",
        ],
    )


def exclusion_menu(state: WizardState, ui_mode: str = "plain") -> None:
    while True:
        if should_use_cursor_menu(ui_mode):
            choice = _exclusion_cursor_choice(state)
            if choice in {None, "0"}:
                return
        else:
            print_exclusion_status(state)
            sep = _ansi_cyan_text("  -----")
            print("\nOpcje:")
            print(f"  1. Domyślne [{on_off_label(state.use_default_excludes)}]")
            print("     Globalnie włącz/wyłącz całą listę domyślną.")
            print(f"  2. Edytuj domyślne [{len(state.active_default_excludes())}/{len(state.profile_default_excludes())}]")
            print("     Tabela pojedynczych wzorców domyślnych.")
            print(sep)
            print(f"  3. Manualne [{on_off_label(_manual_excludes_enabled_for_label(state))}]")
            print("     Włącz/wyłącz użycie ręcznych wzorców.")
            print(f"  4. Zarządzaj [{len(state.custom_excludes)}]")
            print("     Dodaj, edytuj, usuń pojedyncze albo wyczyść wszystkie.")
            print(sep)
            print("  0. Wróć")
            choice = ask_text("Wybór", "0")
            if choice == "0":
                return

        normalized_choice = str(choice).strip().replace(",", ".")
        if normalized_choice == "1":
            state.use_default_excludes = not state.use_default_excludes
            state.plan = None
            print(f"Domyślna lista wykluczeń: {on_off_label(state.use_default_excludes)}")
        elif normalized_choice == "2":
            default_exclusions_table_menu(state, ui_mode)
        elif normalized_choice == "3":
            state.use_custom_excludes = not state.use_custom_excludes
            state.plan = None
            print(f"Manualne wykluczenia: {manual_exclusions_label(state)}")
        elif normalized_choice == "4":
            manual_exclusion_submenu(state, ui_mode)
        else:
            print("Nieznana opcja.")


def _custom_exclusion_list_cursor(state: WizardState, *, mode: str) -> None:
    """Przewijana lista ręcznych wykluczeń dla edycji/usuwania; działa też przy długich listach."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    width = 100
    selected = {"index": 0, "top": 0}
    editor = {"active": False, "text": "", "cursor": 0, "message": ""}
    title = "Edytuj manualne wykluczenie" if mode == "edit" else "Usuń manualne wykluczenie"

    def visible_row_count() -> int:
        try:
            lines = shutil.get_terminal_size(fallback=(100, 24)).lines
        except Exception:
            lines = 24
        return max(4, min(max(1, len(state.custom_excludes)), lines - 12))

    def clamp() -> None:
        count = len(state.custom_excludes)
        if count <= 0:
            selected["index"] = 0
            selected["top"] = 0
            return
        selected["index"] = max(0, min(selected["index"], count - 1))
        visible = visible_row_count()
        if selected["index"] < selected["top"]:
            selected["top"] = selected["index"]
        elif selected["index"] >= selected["top"] + visible:
            selected["top"] = selected["index"] - visible + 1
        selected["top"] = max(0, min(selected["top"], max(0, count - visible)))

    def is_editing() -> bool:
        return bool(editor.get("active"))

    def move(delta: int) -> None:
        if is_editing() or not state.custom_excludes:
            return
        selected["index"] = max(0, min(len(state.custom_excludes) - 1, selected["index"] + delta))
        clamp()

    def start_edit() -> None:
        if not state.custom_excludes:
            return
        clamp()
        value = state.custom_excludes[selected["index"]]
        editor.update({"active": True, "text": value, "cursor": len(value), "message": ""})

    def stop_edit(message: str = "") -> None:
        editor.update({"active": False, "text": "", "cursor": 0, "message": message})

    def insert(data: str) -> None:
        value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(value))
        editor["text"] = value[:cursor] + data + value[cursor:]
        editor["cursor"] = cursor + len(data)

    def apply_edit() -> None:
        if not state.custom_excludes:
            stop_edit("Brak wpisów do edycji.")
            return
        value = str(editor.get("text") or "").strip()
        if not value:
            editor["message"] = "Wzorzec nie może być pusty."
            return
        clamp()
        old = state.custom_excludes[selected["index"]]
        state.custom_excludes[selected["index"]] = value
        state.use_custom_excludes = True
        state.plan = None
        stop_edit(f"Zmieniono: {old} → {value}")

    def remove_current() -> None:
        if not state.custom_excludes:
            editor["message"] = "Brak wpisów do usunięcia."
            return
        clamp()
        old = state.custom_excludes.pop(selected["index"])
        state.plan = None
        if selected["index"] >= len(state.custom_excludes):
            selected["index"] = max(0, len(state.custom_excludes) - 1)
        clamp()
        editor["message"] = f"Usunięto: {old}"

    def get_text() -> list[tuple[str, str]]:
        clamp()
        fragments: list[tuple[str, str]] = []
        _line_style_append(fragments, "class:border", "=" * 78, width)
        _line_style_append(fragments, "class:title", f"  {title}", width)
        _line_style_append(fragments, "class:border", "=" * 78, width)
        _line_style_append(fragments, "", "", width)
        hint = "ESC wraca do menu manualnych wykluczeń."
        _line_style_append(fragments, "class:hint", hint, width)
        _line_style_append(fragments, "", "", width)
        _line_style_append(fragments, "class:border", "=" * 78, width)
        if not state.custom_excludes:
            _line_style_append(fragments, "class:hint", "  (brak manualnych wykluczeń)", width)
        else:
            visible = visible_row_count()
            top = selected["top"]
            bottom = min(top + visible, len(state.custom_excludes))
            for idx in range(top, bottom):
                value = state.custom_excludes[idx]
                if is_editing() and idx == selected["index"]:
                    value = _text_with_cursor_marker(str(editor.get("text") or ""), as_int(editor.get("cursor"), 0))
                marker = "▶" if idx == selected["index"] else " "
                style = "class:latka.selected" if idx == selected["index"] else "class:latka.option"
                _line_style_append(fragments, style, f"  {marker} {idx + 1}. [{value}]", width)
            if bottom < top + visible:
                for _ in range(top + visible - bottom):
                    _line_style_append(fragments, "", "", width)
        _line_style_append(fragments, "class:separator", "  -----", width)
        marker = " " if state.custom_excludes else "▶"
        _line_style_append(fragments, "class:latka.option", f"  {marker} 0. Wróć", width)
        _line_style_append(fragments, "class:description", "      Bez zmiany.", width)
        _line_style_append(fragments, "class:border", "=" * 78, width)
        if is_editing():
            footer = "Edycja []: wpisuj tekst | Tab autouzupełnij | Enter zapisz | Esc anuluj | Ctrl+X zamknij bez zapisu"
        else:
            footer = "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu"
        _line_style_append(fragments, "class:hint", footer, width)
        if editor.get("message"):
            _line_style_append(fragments, "class:message", str(editor.get("message")), width)
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=False)
    layout = Layout(window)
    kb = KeyBindings()

    @kb.add("up")
    def _up(event: Any) -> None:
        move(-1)
        event.app.invalidate()

    @kb.add("down")
    def _down(event: Any) -> None:
        move(1)
        event.app.invalidate()

    @kb.add("k")
    def _k(event: Any) -> None:
        if is_editing():
            insert("k")
        else:
            move(-1)
        event.app.invalidate()

    @kb.add("j")
    def _j(event: Any) -> None:
        if is_editing():
            insert("j")
        else:
            move(1)
        event.app.invalidate()

    @kb.add("pageup")
    @kb.add("c-u")
    def _page_up(event: Any) -> None:
        move(-visible_row_count())
        event.app.invalidate()

    @kb.add("pagedown")
    @kb.add("c-d")
    def _page_down(event: Any) -> None:
        move(visible_row_count())
        event.app.invalidate()

    @kb.add("home")
    def _home(event: Any) -> None:
        if is_editing():
            editor["cursor"] = 0
        else:
            selected["index"] = 0
            selected["top"] = 0
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        if is_editing():
            editor["cursor"] = len(str(editor.get("text") or ""))
        else:
            selected["index"] = max(0, len(state.custom_excludes) - 1)
            clamp()
        event.app.invalidate()

    @kb.add("left")
    def _left(event: Any) -> None:
        if is_editing():
            editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1)
            event.app.invalidate()

    @kb.add("right")
    def _right(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            editor["cursor"] = min(len(value), as_int(editor.get("cursor"), len(value)) + 1)
            event.app.invalidate()

    @kb.add("backspace")
    @kb.add("c-h")
    def _backspace(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            cursor = as_int(editor.get("cursor"), len(value))
            if cursor > 0:
                editor["text"] = value[:cursor - 1] + value[cursor:]
                editor["cursor"] = cursor - 1
            event.app.invalidate()

    @kb.add("delete")
    def _delete(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            cursor = as_int(editor.get("cursor"), len(value))
            if cursor < len(value):
                editor["text"] = value[:cursor] + value[cursor + 1:]
            event.app.invalidate()

    @kb.add("tab")
    def _tab(event: Any) -> None:
        if is_editing():
            new_text, msg = _inline_autocomplete_path(str(editor.get("text") or ""), only_directories=False)
            editor["text"] = new_text
            editor["cursor"] = len(new_text)
            editor["message"] = msg
            event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        if is_editing():
            apply_edit()
        elif mode == "edit":
            start_edit()
        else:
            remove_current()
        event.app.invalidate()

    @kb.add("escape")
    def _escape(event: Any) -> None:
        if is_editing():
            stop_edit("Anulowano edycję pola.")
            event.app.invalidate()
        else:
            event.app.exit(result=None)

    @kb.add("q")
    def _q(event: Any) -> None:
        if is_editing():
            insert("q")
            event.app.invalidate()
        else:
            event.app.exit(result=None)

    @kb.add("0")
    def _zero(event: Any) -> None:
        if is_editing():
            insert("0")
            event.app.invalidate()
        else:
            event.app.exit(result=None)

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    @kb.add("<any>")
    def _any(event: Any) -> None:
        if is_editing():
            data = getattr(event, "data", "") or ""
            if data and data not in {"\r", "\n", "\t"}:
                insert(data)
                event.app.invalidate()

    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "description": "ansibrightblack", "separator": "ansicyan", "message": "ansiyellow",
        "latka.option": "", "latka.selected": "reverse bold",
    })
    Application(layout=layout, key_bindings=kb, style=style, full_screen=True, mouse_support=False).run()


def _manual_exclusion_cursor_menu(state: WizardState) -> None:
    parts = prompt_toolkit_parts()
    if parts is None:
        return
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    width = 78
    rows: list[tuple[str, str, str, str]] = [
        ("item", "1", "Dodaj", "Dodaj nowy ręczny wzorzec."),
        ("item", "2", "Edytuj", "Zmień wybrany ręczny wzorzec."),
        ("item", "3", "Usuń pojedyncze", "Usuń jeden wybrany wzorzec."),
        ("item", "4", "Wyczyść wszystkie", "Usuń wszystkie ręczne wzorce."),
        _cursor_separator_row(),
        ("item", "0", "Wróć", "Powrót do ustawień wykluczeń."),
    ]
    item_rows = [idx for idx, row in enumerate(rows) if row[0] == "item"]
    keys = [rows[idx][1] for idx in item_rows]
    selected = {"row_index": item_rows[0]}
    editor = {"active": False, "text": "", "cursor": 0, "message": ""}

    def selected_item_pos() -> int:
        try:
            return item_rows.index(selected["row_index"])
        except ValueError:
            selected["row_index"] = item_rows[0]
            return 0

    def is_editing_add() -> bool:
        return bool(editor.get("active"))

    def start_add() -> None:
        selected["row_index"] = item_rows[0]
        editor.update({"active": True, "text": "", "cursor": 0, "message": ""})

    def stop_add(message: str = "") -> None:
        editor.update({"active": False, "text": "", "cursor": 0, "message": message})

    def insert(data: str) -> None:
        value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(value))
        editor["text"] = value[:cursor] + data + value[cursor:]
        editor["cursor"] = cursor + len(data)

    def apply_add() -> None:
        value = str(editor.get("text") or "").strip()
        if not value:
            editor["message"] = "Wzorzec nie może być pusty."
            return
        state.custom_excludes.append(value)
        state.use_custom_excludes = True
        state.plan = None
        stop_add(f"Dodano: {value}")
        selected["row_index"] = item_rows[0]

    def get_text() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        _line_style_append(fragments, "class:border", "=" * width, width)
        _line_style_append(fragments, "class:title", "  Manualne wykluczenie", width)
        _line_style_append(fragments, "class:border", "=" * width, width)
        _line_style_append(fragments, "", "", width)
        _line_style_append(fragments, "class:hint", f"Manualne wykluczenia: {manual_exclusions_label(state)}", width)
        _line_style_append(fragments, "", "", width)
        _line_style_append(fragments, "class:border", "=" * width, width)
        for index, (kind, key, label, description) in enumerate(rows):
            if kind == "sep":
                _line_style_append(fragments, "class:separator", "  -----", width)
                continue
            shown_label = label
            if key == "1":
                value = _text_with_cursor_marker(str(editor.get("text") or ""), as_int(editor.get("cursor"), 0)) if is_editing_add() else "ścieżka"
                shown_label = f"Dodaj [{value}]"
            marker = "▶" if index == selected["row_index"] else " "
            style = "class:latka.selected" if index == selected["row_index"] else "class:latka.option"
            _line_style_append(fragments, style, f"  {marker} {key}. {shown_label}", width)
            if description:
                _line_style_append(fragments, "class:description", "      " + description, width)
        _line_style_append(fragments, "class:border", "=" * width, width)
        if is_editing_add():
            footer = "Edycja []: wpisuj tekst | Tab autouzupełnij | Enter dodaj | Esc anuluj | Ctrl+X zamknij bez zapisu"
        else:
            footer = "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu"
        _line_style_append(fragments, "class:hint", footer, width)
        if editor.get("message"):
            _line_style_append(fragments, "class:message", str(editor.get("message")), width)
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        if is_editing_add():
            return
        pos = (selected_item_pos() + delta) % len(item_rows)
        selected["row_index"] = item_rows[pos]
        event.app.invalidate()

    @kb.add("up")
    def _up(event: Any) -> None: move(-1, event)
    @kb.add("down")
    def _down(event: Any) -> None: move(1, event)
    @kb.add("k")
    def _k(event: Any) -> None:
        if is_editing_add(): insert("k")
        else: move(-1, event)
        event.app.invalidate()
    @kb.add("j")
    def _j(event: Any) -> None:
        if is_editing_add(): insert("j")
        else: move(1, event)
        event.app.invalidate()
    @kb.add("home")
    def _home(event: Any) -> None:
        if is_editing_add(): editor["cursor"] = 0
        else: selected["row_index"] = item_rows[0]
        event.app.invalidate()
    @kb.add("end")
    def _end(event: Any) -> None:
        if is_editing_add(): editor["cursor"] = len(str(editor.get("text") or ""))
        else: selected["row_index"] = item_rows[-1]
        event.app.invalidate()
    @kb.add("left")
    def _left(event: Any) -> None:
        if is_editing_add(): editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1); event.app.invalidate()
    @kb.add("right")
    def _right(event: Any) -> None:
        if is_editing_add():
            value = str(editor.get("text") or "")
            editor["cursor"] = min(len(value), as_int(editor.get("cursor"), len(value)) + 1)
            event.app.invalidate()
    @kb.add("backspace")
    @kb.add("c-h")
    def _backspace(event: Any) -> None:
        if is_editing_add():
            value = str(editor.get("text") or "")
            cursor = as_int(editor.get("cursor"), len(value))
            if cursor > 0:
                editor["text"] = value[:cursor - 1] + value[cursor:]
                editor["cursor"] = cursor - 1
            event.app.invalidate()
    @kb.add("delete")
    def _delete(event: Any) -> None:
        if is_editing_add():
            value = str(editor.get("text") or "")
            cursor = as_int(editor.get("cursor"), len(value))
            if cursor < len(value):
                editor["text"] = value[:cursor] + value[cursor + 1:]
            event.app.invalidate()
    @kb.add("tab")
    def _tab(event: Any) -> None:
        if is_editing_add():
            new_text, msg = _inline_autocomplete_path(str(editor.get("text") or ""), only_directories=False)
            editor["text"] = new_text
            editor["cursor"] = len(new_text)
            editor["message"] = msg
            event.app.invalidate()
    @kb.add("enter")
    def _enter(event: Any) -> None:
        if is_editing_add():
            apply_add()
            event.app.invalidate()
            return
        _kind, key, _label, _description = rows[selected["row_index"]]
        if key == "1":
            start_add()
        elif key == "2":
            _custom_exclusion_list_cursor(state, mode="edit")
        elif key == "3":
            _custom_exclusion_list_cursor(state, mode="remove")
        elif key == "4":
            state.custom_excludes.clear()
            state.plan = None
            editor["message"] = "Wyczyszczono wszystkie manualne wykluczenia."
        elif key == "0":
            event.app.exit(result=None)
        event.app.invalidate()
    @kb.add("escape")
    def _escape(event: Any) -> None:
        if is_editing_add():
            stop_add("Anulowano dodawanie.")
            event.app.invalidate()
        else:
            event.app.exit(result=None)
    @kb.add("q")
    def _q(event: Any) -> None:
        if is_editing_add(): insert("q"); event.app.invalidate()
        else: event.app.exit(result=None)
    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None: event.app.exit(exception=UserRequestedAppExit())
    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None: event.app.invalidate()
    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None: event.app.invalidate()
    except Exception:
        pass
    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key)
            def _number(event: Any, option_key: str = option_key) -> None:
                if is_editing_add():
                    insert(option_key)
                    event.app.invalidate()
                    return
                # Akcja jak Enter na wskazanym numerze.
                row_index = next((idx for idx, row in enumerate(rows) if row[0] == "item" and row[1] == option_key), selected["row_index"])
                selected["row_index"] = row_index
                _kind, key, _label, _description = rows[row_index]
                if key == "1": start_add()
                elif key == "2": _custom_exclusion_list_cursor(state, mode="edit")
                elif key == "3": _custom_exclusion_list_cursor(state, mode="remove")
                elif key == "4":
                    state.custom_excludes.clear(); state.plan = None; editor["message"] = "Wyczyszczono wszystkie manualne wykluczenia."
                elif key == "0": event.app.exit(result=None)
                event.app.invalidate()
    @kb.add("<any>")
    def _any(event: Any) -> None:
        if is_editing_add():
            data = getattr(event, "data", "") or ""
            if data and data not in {"\r", "\n", "\t"}:
                insert(data)
                event.app.invalidate()

    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "description": "ansibrightblack", "separator": "ansicyan", "message": "ansiyellow",
        "latka.option": "", "latka.selected": "reverse bold",
    })
    Application(layout=layout, key_bindings=kb, style=style, full_screen=True, mouse_support=False).run()


def manual_exclusion_submenu(state: WizardState, ui_mode: str = "plain") -> None:
    if should_use_cursor_menu(ui_mode):
        _manual_exclusion_cursor_menu(state)
        return
    while True:
        section("Manualne wykluczenie")
        print(f"Manualne wykluczenia: {manual_exclusions_label(state)}")
        sep = _ansi_cyan_text("  -----")
        print("Opcje:")
        print("  1. Dodaj [ścieżka]")
        print("  2. Edytuj")
        print("  3. Usuń pojedyncze")
        print("  4. Wyczyść wszystkie")
        print(sep)
        print("  0. Wróć")
        choice = ask_text("Wybór", "0")
        if choice == "0":
            return
        if choice == "1":
            add_custom_exclusion(state, ui_mode)
        elif choice == "2":
            edit_custom_exclusion(state, ui_mode)
        elif choice == "3":
            remove_custom_exclusion(state, ui_mode)
        elif choice == "4":
            state.custom_excludes.clear()
            state.plan = None
            print("Wyczyszczono wszystkie manualne wykluczenia.")
        else:
            print("Nieznana opcja.")


def print_menu_plain(state: WizardState) -> None:
    print("\n" + "=" * 78)
    print("  MENU GŁÓWNE")
    print("=" * 78)
    sep = _ansi_cyan_text("  -----")
    for key, label in menu_options(state):
        if _main_menu_separator_before(key):
            print(sep)
        print(f"  {label}")



# =============================================================================
# NADPISANIA UI v5.12 — odśwież nazwę w menu głównym, dynamiczna szerokość i cyan
# =============================================================================


def _terminal_ui_width(*, minimum: int = 60, maximum: int = 160, fallback: int = 100) -> int:
    """Szerokość widoku dopasowana do terminala.

    prompt_toolkit rysuje w terminalu przez Window/FormattedTextControl. Window
    odpowiada za widok i przy wrap_lines=False trzeba samemu pilnować długości
    tekstu, dlatego menu używa aktualnej szerokości terminala zamiast stałych 78.
    """
    try:
        cols = int(shutil.get_terminal_size(fallback=(fallback, 24)).columns)
    except Exception:
        cols = fallback
    return max(minimum, min(maximum, max(20, cols - 2)))


def _clip_cell(text: object, width: int | None = None) -> str:
    value = str(text)
    width = int(width or _terminal_ui_width())
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return value[: max(0, width - 1)] + "…"


def _cyan_enabled() -> bool:
    try:
        if not sys.stdout.isatty():
            return False
        if os.name != "nt":
            return True
        # Windows Terminal/PowerShell zwykle rozumie ANSI. Jeżeli środowisko go
        # nie zgłasza, zostawiamy tekst bez kodów, żeby nie zaśmiecać konsoli.
        return bool(os.environ.get("WT_SESSION") or os.environ.get("ANSICON") or os.environ.get("TERM"))
    except Exception:
        return False


def _ansi_cyan_text(text: str) -> str:
    return "\033[36m" + text + "\033[0m" if _cyan_enabled() else text


def _cyan_line_text(width: int | None = None, char: str = "=") -> str:
    return _ansi_cyan_text(str(char) * int(width or _terminal_ui_width()))


def section(title: str) -> None:
    width = _terminal_ui_width(fallback=78)
    print("\n" + _cyan_line_text(width))
    print(f"  {title}")
    print(_cyan_line_text(width))


def subsection(title: str) -> None:
    width = _terminal_ui_width(fallback=78)
    print("\n" + _cyan_line_text(width, "-"))
    print(f"  {title}")
    print(_cyan_line_text(width, "-"))


def _line_style_append(fragments: list[tuple[str, str]], style: str, value: str, width: int = 0) -> None:
    # Nadpisuje wcześniejszy helper: stałe 78/100 z dawnych widoków traktujemy
    # jako prośbę o szerokość dynamiczną, ale gdy nowy widok przekazuje już
    # obliczoną szerokość, nie pomniejszamy jej drugi raz.
    if width in (0, 78, 100):
        real_width = _terminal_ui_width(fallback=width or 100)
    else:
        real_width = int(width)
    fragments.append((style, _clip_cell(value, real_width)))
    fragments.append(("", "\n"))


def _cursor_separator_row() -> tuple[str, str, str, str]:
    return ("sep", "", "-----", "")


def _print_plain_separator(indent: str = "  ") -> None:
    print(_ansi_cyan_text(f"{indent}-----"))


def _print_cyan_border(width: int | None = None) -> None:
    print(_cyan_line_text(width or _terminal_ui_width()))


def print_lines_paged(title: str, lines: list[str], *, page_size: int | None = None) -> None:
    """Pager tekstowy z dynamicznym/cyan nagłówkiem."""
    def header(label: str) -> None:
        width = _terminal_ui_width(fallback=78)
        print("\n" + _cyan_line_text(width))
        print(f"  {label}:")
        print(_cyan_line_text(width))

    if not lines:
        header(title)
        print("  (brak)")
        print("(END)")
        return

    page_size_value = page_size or terminal_page_size()
    page_size_value = max(1, int(page_size_value))
    total_pages = (len(lines) + page_size_value - 1) // page_size_value

    if total_pages <= 1 or not sys.stdin.isatty():
        header(title)
        width = _terminal_ui_width(fallback=120)
        for line_value in lines:
            print(_clip_cell(line_value, width))
        print("(END)")
        return

    page = 0
    while True:
        start = page * page_size_value
        end = min(start + page_size_value, len(lines))
        header(f"{title} — strona {page + 1}/{total_pages} ({start + 1}-{end} z {len(lines)})")
        width = _terminal_ui_width(fallback=120)
        for line_value in lines[start:end]:
            print(_clip_cell(line_value, width))

        if page >= total_pages - 1:
            print("(END)")
            return

        try:
            choice = input(": ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice in {"q", "0", "k", "koniec", "esc", "w", "wroc", "wróć"}:
            print("(END)")
            return
        if choice in {"p", "poprzednia", "prev", "b"}:
            page = max(0, page - 1)
            continue
        page += 1


def menu_options(state: WizardState) -> list[tuple[str, str]]:
    return [
        ("1", f"1. Profil pakowania [{profile_menu_label(state)}]"),
        ("2", f"2. Pokaż listę do spakowania [{pack_list_settings_label(state)}]"),
        ("3", f"3. Folder do pakowania [{menu_value(state.source_folder)}]"),
        ("4", f"4. Folder zapisu paczki [{menu_value(state.out_dir)}]"),
        ("5", "5. Odśwież nazwę paczki z aktualnej wersji"),
        ("6", f"6. Zmień nazwę paczki [{state.archive_name or 'nie ustawiono'}]"),
        ("7", "7. Ustawienia"),
        ("8", "8. Pakuj teraz"),
        ("0", "0. Wyjście"),
    ]


def default_menu_choice(state: WizardState) -> str:
    if state.source_folder is None:
        return "3"
    if state.out_dir is None:
        return "4"
    if not state.archive_name:
        return "6"
    if state.plan is not None:
        return "8"
    return "2"


def _main_menu_separator_before(key: str) -> bool:
    # 1-2 / 3-6 / 7 / 8-0
    return str(key) in {"3", "7", "8"}


def _inline_edit_field_for_key(key: str) -> str:
    return {"3": "source", "4": "output", "6": "name"}.get(str(key), "")


def print_menu_plain(state: WizardState) -> None:
    width = _terminal_ui_width(fallback=78)
    print("\n" + _cyan_line_text(width))
    print("  MENU GŁÓWNE")
    print(_cyan_line_text(width))
    for key, label in menu_options(state):
        if _main_menu_separator_before(key):
            _print_plain_separator("  ")
        print("  " + _clip_cell(label, width - 2))


def _cursor_menu_lines(state: WizardState, selected_index: int, inline_editor: dict[str, object] | None = None) -> list[tuple[str, str]]:
    options = menu_options(state)
    selected_index = max(0, min(selected_index, len(options) - 1))
    width = _terminal_ui_width(fallback=100)
    inline_editor = inline_editor or {}
    editing_field = str(inline_editor.get("field") or "")
    editing_text = str(inline_editor.get("text") or "")
    editing_cursor = as_int(inline_editor.get("cursor"), len(editing_text))
    message = str(inline_editor.get("message") or "")
    fragments: list[tuple[str, str]] = []

    def line(style: str, value: str = "") -> None:
        _line_style_append(fragments, style, value, width)

    line("class:border", "=" * width)
    line("class:title", f"  Jaźń / Łatka — generator paczki ZIP v{VERSION}")
    line("class:border", "=" * width)
    line("", "")
    for label, value in (
        ("Plan:     ", plan_status_label(state)),
        ("Profil:   ", state.profile_label()),
        ("Źródło:   ", menu_value(state.source_folder)),
        ("Zapis:    ", menu_value(state.out_dir)),
        ("ZIP:      ", state.archive_name or "(nie ustawiono)"),
    ):
        fragments.append(("class:status.label", label))
        _line_style_append(fragments, "class:status.value", value, max(10, width - len(label)))
    line("", "")
    line("class:border", "=" * width)
    for idx, (key, label) in enumerate(options):
        if _main_menu_separator_before(key):
            line("class:separator", "  -----")
        field = _inline_edit_field_for_key(key)
        if editing_field and field == editing_field:
            edited = _text_with_cursor_marker(editing_text, editing_cursor)
            if key == "3":
                label = f"3. Folder do pakowania [{edited}]"
            elif key == "4":
                label = f"4. Folder zapisu paczki [{edited}]"
            elif key == "6":
                label = f"6. Zmień nazwę paczki [{edited}]"
        marker = "▶" if idx == selected_index else " "
        style = "class:latka.selected" if idx == selected_index else "class:latka.option"
        line(style, f"  {marker} {label}")
    line("class:border", "=" * width)
    if editing_field:
        line("class:hint", "Edycja pola []: wpisuj tekst | Tab autouzupełnij ścieżkę | Enter zapisz | Esc anuluj | Ctrl+X zamknij bez zapisu")
    else:
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć | Ctrl+X zamknij bez zapisu")
    if message:
        line("class:message", message)
    return fragments


def ask_menu_choice_cursor(state: WizardState, default: str) -> str:
    parts = prompt_toolkit_parts()
    if parts is None:
        return ask_text("Wybór", default)
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    options = menu_options(state)
    keys = [key for key, _ in options]
    selected = {"index": keys.index(default) if default in keys else 0}
    editor: dict[str, object] = {"field": "", "text": "", "cursor": 0, "message": ""}

    def is_editing() -> bool:
        return bool(editor.get("field"))

    def start_edit(key: str) -> bool:
        field = _inline_edit_field_for_key(key)
        if not field:
            return False
        text = _inline_edit_initial_text(state, field)
        editor.update({"field": field, "text": text, "cursor": len(text), "message": f"Edytujesz: {_inline_edit_label_for_field(field)}. Wpisuj bezpośrednio w nawiasach []."})
        return True

    def stop_edit(message: str = "") -> None:
        editor.update({"field": "", "text": "", "cursor": 0, "message": message})

    def insert(value: str) -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        editor["text"] = text_value[:cursor] + value + text_value[cursor:]
        editor["cursor"] = cursor + len(value)

    def get_text() -> list[tuple[str, str]]:
        return _cursor_menu_lines(state, selected["index"], editor)

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=False, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        if not is_editing():
            selected["index"] = (selected["index"] + delta) % len(options)
            editor["message"] = ""
            event.app.invalidate()

    @kb.add("up")
    def _up(event: Any) -> None: move(-1, event)
    @kb.add("down")
    def _down(event: Any) -> None: move(1, event)
    @kb.add("k")
    def _k(event: Any) -> None:
        if is_editing(): insert("k")
        else: move(-1, event)
        event.app.invalidate()
    @kb.add("j")
    def _j(event: Any) -> None:
        if is_editing(): insert("j")
        else: move(1, event)
        event.app.invalidate()
    @kb.add("home")
    def _home(event: Any) -> None:
        if is_editing(): editor["cursor"] = 0
        else: selected["index"] = 0
        event.app.invalidate()
    @kb.add("end")
    def _end(event: Any) -> None:
        if is_editing(): editor["cursor"] = len(str(editor.get("text") or ""))
        else: selected["index"] = len(options) - 1
        event.app.invalidate()
    @kb.add("left")
    def _left(event: Any) -> None:
        if is_editing(): editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1); event.app.invalidate()
    @kb.add("right")
    def _right(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            editor["cursor"] = min(len(value), as_int(editor.get("cursor"), len(value)) + 1)
            event.app.invalidate()
    @kb.add("backspace")
    @kb.add("c-h")
    def _backspace(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            cursor = as_int(editor.get("cursor"), len(value))
            if cursor > 0:
                editor["text"] = value[:cursor - 1] + value[cursor:]
                editor["cursor"] = cursor - 1
            event.app.invalidate()
    @kb.add("delete")
    def _delete(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            cursor = as_int(editor.get("cursor"), len(value))
            if cursor < len(value): editor["text"] = value[:cursor] + value[cursor + 1:]
            event.app.invalidate()
    @kb.add("tab")
    def _tab(event: Any) -> None:
        if not is_editing(): return
        field = str(editor.get("field") or "")
        if field not in {"source", "output"}:
            editor["message"] = "Tab działa dla ścieżek folderów."
        else:
            new_text, msg = _inline_autocomplete_path(str(editor.get("text") or ""), only_directories=True)
            editor["text"] = new_text; editor["cursor"] = len(new_text); editor["message"] = msg
        event.app.invalidate()
    @kb.add("enter")
    def _enter(event: Any) -> None:
        if is_editing():
            ok, msg = _apply_inline_edit_value(state, str(editor.get("field") or ""), str(editor.get("text") or ""))
            if ok:
                save_settings(state, quiet=True); stop_edit(msg)
            else:
                editor["message"] = msg
            event.app.invalidate(); return
        key = options[selected["index"]][0]
        if start_edit(key): event.app.invalidate(); return
        event.app.exit(result=key)
    @kb.add("escape")
    def _escape(event: Any) -> None:
        if is_editing(): stop_edit("Anulowano edycję pola."); event.app.invalidate()
        else: event.app.exit(result="0")
    @kb.add("q")
    def _q(event: Any) -> None:
        if is_editing(): insert("q"); event.app.invalidate()
        else: event.app.exit(result="0")
    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None: event.app.exit(exception=UserRequestedAppExit())
    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None: event.app.invalidate()
    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None: event.app.invalidate()
    except Exception:
        pass
    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key, eager=True)
            def _number(event: Any, option_key: str = option_key) -> None:
                if is_editing(): insert(option_key); event.app.invalidate(); return
                selected["index"] = keys.index(option_key)
                if start_edit(option_key): event.app.invalidate(); return
                event.app.exit(result=option_key)
    @kb.add("<any>")
    def _insert_any(event: Any) -> None:
        if is_editing():
            data = getattr(event, "data", "") or ""
            if data and data not in {"\r", "\n", "\t"}:
                if "\x18" in data: event.app.exit(exception=UserRequestedAppExit()); return
                insert(data); event.app.invalidate()

    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "message": "ansiyellow", "status.label": "bold", "status.value": "",
        "separator": "ansicyan", "latka.option": "", "latka.selected": "reverse bold",
    })
    result = Application(layout=layout, key_bindings=kb, style=style, full_screen=True, mouse_support=False).run()
    return str(result or default)


def _settings_rows(state: WizardState, ui_mode: str) -> list[tuple[str, str, str, str]]:
    return [
        ("item", "1", f"Profil pakowania [{state.profile_label()}]", ""),
        _cursor_separator_row(),
        ("item", "2", "Zapisz pełny podgląd listy pakowania do JSON", ""),
        _cursor_separator_row(),
        ("item", "3", f"Ustawienia paczki [{pack_settings_menu_label(state)}]", ""),
        ("item", "4", f"Ustawienia wykluczeń [{exclusions_menu_label(state)}]", ""),
        ("item", "5", "Przeskanuj ponownie i pokaż wpływ", ""),
        _cursor_separator_row(),
        ("item", "6", f"Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]", ""),
        _cursor_separator_row(),
        ("item", "0", "Wróć", ""),
    ]


def _settings_cursor_choice(state: WizardState, ui_mode: str) -> str | None:
    rows = _settings_rows(state, ui_mode)
    return _option_rows_cursor_app(title="Ustawienia", rows=rows, default_key="0")


def _print_settings_menu_plain(state: WizardState, ui_mode: str) -> None:
    section("Ustawienia")
    print(f"  1. Profil pakowania [{state.profile_label()}]")
    _print_plain_separator("  ")
    print("  2. Zapisz pełny podgląd listy pakowania do JSON")
    _print_plain_separator("  ")
    print(f"  3. Ustawienia paczki [{pack_settings_menu_label(state)}]")
    print(f"  4. Ustawienia wykluczeń [{exclusions_menu_label(state)}]")
    print("  5. Przeskanuj ponownie i pokaż wpływ")
    _print_plain_separator("  ")
    print(f"  6. Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]")
    _print_plain_separator("  ")
    print("  0. Wróć")


def settings_submenu(state: WizardState, ui_mode: str = "plain") -> str:
    """Ustawienia bez odświeżania nazwy; odświeżanie jest w menu głównym."""
    while True:
        if should_use_cursor_menu(ui_mode):
            choice = _settings_cursor_choice(state, ui_mode)
            if choice in {None, "0"}:
                return "cancel"
        else:
            _print_settings_menu_plain(state, ui_mode)
            choice = ask_text("Wybór", "0").strip()
            if choice == "0":
                return "cancel"

        normalized = str(choice).strip()
        if normalized == "1":
            configure_profile(state, ui_mode); save_settings(state, quiet=True)
        elif normalized == "2":
            if ensure_ready_for_pack_plan(state, ui_mode=ui_mode):
                if state.plan is None:
                    rebuild_plan(state)
                preview = save_preview_json(state)
                save_settings(state, quiet=True)
                print(f"Zapisano podgląd: {preview}")
                pause()
        elif normalized == "3":
            configure_pack_settings(state, ui_mode); save_settings(state, quiet=True)
        elif normalized == "4":
            exclusion_menu(state, ui_mode); save_settings(state, quiet=True)
        elif normalized == "5":
            if ensure_ready_for_pack_plan(state, ui_mode=ui_mode):
                rebuild_plan(state); save_settings(state, quiet=True); pause()
        elif normalized == "6":
            ui_mode = configure_ui_mode_preference(state, ui_mode); save_settings(state, quiet=True)
        else:
            print("Nieznana opcja.")


def run_wizard(initial_source: str | None = None, *, ui_mode: str | None = None) -> int:
    settings_snapshot = snapshot_settings_file()
    state: WizardState | None = None
    try:
        activate_process_guard(prompt_user=True)
        state = initialize_state(initial_source)
        section(f"Jaźń / Łatka — generator paczki ZIP v{VERSION}")
        print_bar(100, 100, label="Ładowanie")
        if state.settings_needs_cleanup:
            save_settings(state, quiet=True)
            settings_snapshot = snapshot_settings_file()
        show_startup_warnings(state)
        ui_mode = resolve_ui_mode_with_optional_install(ui_mode, state)
        save_settings(state, quiet=True)
        settings_snapshot = snapshot_settings_file()
        prepare_plan_on_startup_if_possible(state)
    except UserRequestedAppExit:
        restore_settings_file(settings_snapshot); print("\nZamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
    except KeyboardInterrupt:
        restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. Start aplikacji został anulowany bez tracebacka."); return 130
    except EOFError:
        restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Start aplikacji został anulowany bez tracebacka."); return 130

    while True:
        try:
            assert state is not None
            if not should_use_cursor_menu(ui_mode):
                show_current_state(state)
                print(f"Tryb UI:                    {ui_mode_label(ui_mode)}; auto-start: {on_off_label(state.ui_auto_start)}")
            current_default_choice = default_menu_choice(state)
            choice = ask_menu_choice(state, current_default_choice, ui_mode)
            control_word = _plain_control_word(choice)
            if control_word == "exit": raise UserRequestedAppExit()
            if control_word == "cancel": continue

            known_menu_choices = {"0", "1", "2", "3", "4", "5", "6", "7", "8"}
            if choice not in known_menu_choices:
                if current_default_choice == "3" and state.source_folder is None:
                    if apply_source_path_text(state, choice): save_settings(state, quiet=True)
                    continue
                if current_default_choice == "4" and state.out_dir is None:
                    if apply_output_path_text(state, choice): save_settings(state, quiet=True)
                    continue

            if choice == APP_EXIT_MARKER:
                restore_settings_file(settings_snapshot); print("Zamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
            if choice == "1":
                configure_profile(state, ui_mode); save_settings(state, quiet=True)
            elif choice == "2":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode): save_settings(state, quiet=True); continue
                if state.plan is None: rebuild_plan(state)
                else:
                    section("Lista do spakowania z ustawieniami"); print_pack_plan_summary(state)
                save_settings(state, quiet=True); pause()
            elif choice == "3":
                configure_source(state); save_settings(state, quiet=True)
            elif choice == "4":
                configure_output(state); save_settings(state, quiet=True)
            elif choice == "5":
                reset_archive_name_from_version(state); save_settings(state, quiet=True)
            elif choice == "6":
                configure_name(state); save_settings(state, quiet=True)
            elif choice == "7":
                _ = settings_submenu(state, ui_mode)
                ui_mode = normalize_ui_mode(state.ui_mode or ui_mode)
                if ui_mode == "auto": ui_mode = resolve_auto_ui_mode()
            elif choice == "8":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode): save_settings(state, quiet=True); continue
                if state.plan is None: rebuild_plan(state)
                print("\nTo zostanie użyte jako podstawa pakowania.")
                print_pack_plan_compact_summary(state)
                if ask_bool("Pokazać listę katalogów i plików przed pakowaniem", False, require_explicit=True):
                    print_pack_items_for_plan(state)
                if not ask_bool("Rozpocząć pakowanie", True, require_explicit=True):
                    continue
                assert state.source_folder is not None and state.out_dir is not None and state.plan is not None
                create_split_zip_from_plan(
                    source_folder=state.source_folder,
                    out_dir=state.out_dir,
                    archive_name=state.archive_name,
                    plan=state.plan,
                    part_size_mb=state.part_size_mb,
                    compression_level=state.compression_level,
                    force=state.force,
                    include_empty_dirs=state.include_empty_dirs,
                    exclude_patterns=state.effective_excludes(),
                    package_version=state.package_version,
                    package_release_name=state.package_release_name,
                    resolved_version_file=state.resolved_version_file,
                    archive_basename_requested=state.archive_basename_requested,
                    append_version_to_name=False,
                    disabled_default_excludes=state.disabled_default_excludes,
                    pack_profile=state.pack_profile,
                    include_prefixes=state.include_prefixes(),
                )
                save_settings(state, quiet=True); return 0
            elif choice == "0":
                exit_action = exit_menu(ui_mode)
                if exit_action == "save": save_settings(state, quiet=False); print("Zakończono bez pakowania."); return 0
                if exit_action == "nosave": restore_settings_file(settings_snapshot); print("Zakończono bez zapisywania zmian."); return 0
                continue
            else:
                print("Nieznana opcja.")
        except UserRequestedAppExit:
            restore_settings_file(settings_snapshot); print("\nZamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
        except KeyboardInterrupt:
            restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. W trybie kursorowym skrótem zamknięcia aplikacji jest Ctrl+X. Zakończono bez zapisywania zmian."); return 130
        except EOFError:
            restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Zamknięto bez zapisywania zmian."); return 130
        except Exception as exc:
            save_settings(state, quiet=True); print(f"BŁĄD: {exc}")
            try: pause()
            except KeyboardInterrupt:
                restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. Zakończono bez zapisywania zmian."); return 130
            except EOFError:
                restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Zamknięto bez zapisywania zmian."); return 130


# =============================================================================
# NADPISANIA UI v5.14 — responsywne zawijanie, profile i lista/JSON
# =============================================================================

# Profile v5.14: nazwy są krótsze w menu, a opis dopowiada kontekst.
PACK_PROFILES.update({
    "pelna": {
        "label": "System + pamięć — dwie oddzielne paczki ZIP",
        "short": "system + pamięć osobno",
        "description": "Profil domyślny tworzy dwa niezależne zestawy części: *_system.zip.001... z kodem/systemem oraz *_memory.zip.001... wyłącznie z głównego memory/. Domyślnie pozostają tylko zwykłe pliki ZIP; manifesty i SHA256 są opcjonalne.",
        "exclude_patterns": BASE_SAFE_EXCLUDE_PATTERNS,
        "include_prefixes": [],
    },
    "system": {
        "label": "Sam system — bez pamięci i workspace_runtime",
        "short": "sam system",
        "description": "Kod, dokumentacja, testy i narzędzia bez katalogów memory/ oraz workspace_runtime/. Dobre do aktualizacji kodu bez dużych baz pamięci i stanu sesji.",
        "exclude_patterns": BASE_SAFE_EXCLUDE_PATTERNS + [
            "/memory/",
            "/workspace_runtime/",
            "RUNTIME_STATE.json",
            "ACTIVE_RUNTIME_CACHE_CONTRACT.json",
            "BOOTSTRAP_JAZN_CURRENT.json",
        ],
        "include_prefixes": [],
    },
    "memory": {
        "label": "Sama pamięć — tylko memory/",
        "short": "sama pamięć",
        "description": "Pakuje wyłącznie gałąź memory/: bazy SQLite, warstwy pamięci, indeksy i eksporty pamięci. Przydatne do osobnej kopii pamięci oraz diagnostyki baz.",
        "exclude_patterns": BASE_SAFE_EXCLUDE_PATTERNS,
        "include_prefixes": ["memory/"],
    },
})
# Czwarty profil przywraca domyślne zachowanie wersji 1.2_FINAL:
# system i pamięć w jednym, poprawnym archiwum dzielonym. Klucz `full`
# pozostaje dla zgodności z zapisanymi ustawieniami wcześniejszej wersji.
PACK_PROFILES["full"] = {
    "label": "System + pamięć — jedna paczka ZIP (jak 1.2_FINAL)",
    "short": "system + pamięć razem",
    "description": "Tworzy jeden poprawny ZIP zawierający system i główny katalog memory/, z bezpiecznymi wykluczeniami cache, backupów i plików tymczasowych. Jest to przywrócony domyślny profil z 1.2_FINAL, dostępny jako czwarta opcja.",
    "exclude_patterns": BASE_SAFE_EXCLUDE_PATTERNS,
    "include_prefixes": [],
}


def _wizard_effective_excludes_v514(self: WizardState) -> list[str]:
    patterns: list[str] = []
    if self.use_default_excludes:
        disabled = set(self.disabled_default_excludes)
        patterns.extend(p for p in self.profile_default_excludes() if p not in disabled)
    if self.use_custom_excludes:
        patterns.extend(self.custom_excludes)
    return patterns


def _wizard_active_default_excludes_v514(self: WizardState) -> list[str]:
    if not self.use_default_excludes:
        return []
    disabled = set(self.disabled_default_excludes)
    return [p for p in self.profile_default_excludes() if p not in disabled]


WizardState.effective_excludes = _wizard_effective_excludes_v514  # type: ignore[method-assign]
WizardState.active_default_excludes = _wizard_active_default_excludes_v514  # type: ignore[method-assign]


def _terminal_ui_width(*, minimum: int = 20, maximum: int = 200, fallback: int = 100) -> int:
    """Szerokość widoku dopasowana do aktualnego terminala, także małego."""
    try:
        cols = int(shutil.get_terminal_size(fallback=(fallback, 24)).columns)
    except Exception:
        cols = fallback
    return max(minimum, min(maximum, max(12, cols - 2)))


def _wrap_text_lines(value: object, width: int, *, initial_indent: str = "", subsequent_indent: str | None = None) -> list[str]:
    text = str(value)
    width = max(8, int(width))
    subsequent = initial_indent if subsequent_indent is None else subsequent_indent
    if text == "":
        return [""]
    raw_lines = text.splitlines() or [text]
    wrapped: list[str] = []
    for raw in raw_lines:
        if raw == "":
            wrapped.append("")
            continue
        lines = textwrap.wrap(
            raw,
            width=width,
            initial_indent=initial_indent,
            subsequent_indent=subsequent,
            break_long_words=True,
            break_on_hyphens=False,
            replace_whitespace=False,
            drop_whitespace=False,
        )
        wrapped.extend(lines or [initial_indent])
    return wrapped


def _clip_cell(text: object, width: int | None = None) -> str:
    """Zostawiony dla kompatybilności; nie ucina już tekstu do menu."""
    return str(text)


def _line_style_append(fragments: list[tuple[str, str]], style: str, value: str, width: int = 0) -> None:
    real_width = int(width or _terminal_ui_width(fallback=100))
    for line_value in _wrap_text_lines(value, real_width):
        fragments.append((style, line_value))
        fragments.append(("", "\n"))


def _menu_separator_text(width: int | None = None, *, indent: int = 4, char: str = "-") -> str:
    real_width = int(width or _terminal_ui_width(fallback=100))
    pad = " " * max(0, int(indent))
    inner = max(1, real_width - (len(pad) * 2))
    return pad + (char * inner) + pad


def _cursor_separator_row() -> tuple[str, str, str, str]:
    return ("sep", "", "", "")


def _print_plain_separator(indent: str = "    ") -> None:
    width = _terminal_ui_width(fallback=78)
    print(_ansi_cyan_text(_menu_separator_text(width, indent=max(0, len(indent)))))


def _application_run_responsive(Application: Any, *, layout: Any, key_bindings: Any, style: Any) -> Any:
    kwargs: dict[str, Any] = {
        "layout": layout,
        "key_bindings": key_bindings,
        "style": style,
        "full_screen": True,
        "mouse_support": False,
        "refresh_interval": 0.25,
        "terminal_size_polling_interval": 0.25,
    }
    try:
        return Application(**kwargs).run()
    except TypeError:
        kwargs.pop("terminal_size_polling_interval", None)
        try:
            return Application(**kwargs).run()
        except TypeError:
            kwargs.pop("refresh_interval", None)
            return Application(**kwargs).run()


def _print_wrapped_plain(value: object, *, indent: str = "  ", width: int | None = None) -> None:
    real_width = int(width or _terminal_ui_width(fallback=100))
    available = max(8, real_width - len(indent))
    for idx, line in enumerate(_wrap_text_lines(value, available, subsequent_indent="")):
        print(indent + line if idx == 0 else indent + line)


def print_menu_plain(state: WizardState) -> None:
    width = _terminal_ui_width(fallback=78)
    print("\n" + _cyan_line_text(width))
    print("  MENU GŁÓWNE")
    print(_cyan_line_text(width))
    for key, label in menu_options(state):
        if _main_menu_separator_before(key):
            _print_plain_separator("    ")
        _print_wrapped_plain(label, indent="  ", width=width)


def _cursor_menu_lines(state: WizardState, selected_index: int, inline_editor: dict[str, object] | None = None) -> list[tuple[str, str]]:
    options = menu_options(state)
    selected_index = max(0, min(selected_index, len(options) - 1))
    width = _terminal_ui_width(fallback=100)
    inline_editor = inline_editor or {}
    editing_field = str(inline_editor.get("field") or "")
    editing_text = str(inline_editor.get("text") or "")
    editing_cursor = as_int(inline_editor.get("cursor"), len(editing_text))
    message = str(inline_editor.get("message") or "")
    fragments: list[tuple[str, str]] = []

    def line(style: str, value: str = "", *, subsequent_indent: str = "") -> None:
        for piece in _wrap_text_lines(value, width, subsequent_indent=subsequent_indent):
            fragments.append((style, piece))
            fragments.append(("", "\n"))

    line("class:border", "=" * width)
    line("class:title", f"  Jaźń / Łatka — generator paczki ZIP v{VERSION}")
    line("class:border", "=" * width)
    line("", "")

    for label, value in (
        ("Plan:     ", plan_status_label(state)),
        ("Profil:   ", state.profile_label()),
        ("Źródło:   ", menu_value(state.source_folder)),
        ("Zapis:    ", menu_value(state.out_dir)),
        ("ZIP:      ", state.archive_name or "(nie ustawiono)"),
    ):
        line("class:status.value", f"{label}{value}", subsequent_indent=" " * len(label))

    line("", "")
    line("class:border", "=" * width)
    for idx, (key, label) in enumerate(options):
        if _main_menu_separator_before(key):
            line("class:separator", _menu_separator_text(width))
        field = _inline_edit_field_for_key(key)
        if editing_field and field == editing_field:
            edited = _text_with_cursor_marker(editing_text, editing_cursor)
            if key == "3":
                label = f"3. Folder do pakowania [{edited}]"
            elif key == "4":
                label = f"4. Folder zapisu paczki [{edited}]"
            elif key == "6":
                label = f"6. Zmień nazwę paczki [{edited}]"
        marker = "▶" if idx == selected_index else " "
        style = "class:latka.selected" if idx == selected_index else "class:latka.option"
        line(style, f"  {marker} {label}", subsequent_indent="    ")
    line("class:border", "=" * width)
    if editing_field:
        line("class:hint", "Edycja pola []: wpisuj tekst | Tab autouzupełnij ścieżkę | Enter zapisz | Esc anuluj | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
    else:
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
    if message:
        line("class:message", message, subsequent_indent="  ")
    return fragments


def ask_menu_choice_cursor(state: WizardState, default: str) -> str:
    parts = prompt_toolkit_parts()
    if parts is None:
        return ask_text("Wybór", default)
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    options = menu_options(state)
    keys = [key for key, _ in options]
    selected = {"index": keys.index(default) if default in keys else 0}
    editor: dict[str, object] = {"field": "", "text": "", "cursor": 0, "message": ""}

    def is_editing() -> bool:
        return bool(editor.get("field"))

    def start_edit(key: str) -> bool:
        field = _inline_edit_field_for_key(key)
        if not field:
            return False
        text = _inline_edit_initial_text(state, field)
        editor.update({"field": field, "text": text, "cursor": len(text), "message": f"Edytujesz: {_inline_edit_label_for_field(field)}. Wpisuj bezpośrednio w nawiasach []."})
        return True

    def stop_edit(message: str = "") -> None:
        editor.update({"field": "", "text": "", "cursor": 0, "message": message})

    def insert(value: str) -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        editor["text"] = text_value[:cursor] + value + text_value[cursor:]
        editor["cursor"] = cursor + len(value)
        editor["message"] = ""

    def backspace() -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        if cursor <= 0:
            return
        editor["text"] = text_value[:cursor - 1] + text_value[cursor:]
        editor["cursor"] = cursor - 1

    def delete_char() -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        if cursor >= len(text_value):
            return
        editor["text"] = text_value[:cursor] + text_value[cursor + 1:]

    def submit_edit() -> None:
        field = str(editor.get("field") or "")
        text_value = str(editor.get("text") or "")
        ok = False
        try:
            if field == "source":
                ok = apply_source_path_text(state, text_value)
            elif field == "output":
                ok = apply_output_path_text(state, text_value)
            elif field == "name":
                state.archive_name = sanitize_zip_name(text_value)
                state.archive_name_manual = True
                state.plan = None
                ok = True
        except Exception as exc:
            editor["message"] = f"BŁĄD: {exc}"
            return
        if ok:
            stop_edit("Zapisano zmianę pola.")
        else:
            editor["message"] = "Nie zapisano — popraw wartość albo Esc anuluj."

    def autocomplete() -> None:
        field = str(editor.get("field") or "")
        if field not in {"source", "output"}:
            editor["message"] = "Tab jest dostępny dla pól ścieżek."
            return
        value, message = _inline_autocomplete_path(str(editor.get("text") or ""), only_directories=True)
        editor["text"] = value
        editor["cursor"] = len(value)
        editor["message"] = message

    def get_text() -> list[tuple[str, str]]:
        return _cursor_menu_lines(state, selected["index"], editor)

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=True, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        selected["index"] = (selected["index"] + delta) % len(options)
        event.app.invalidate()

    @kb.add("up")
    def _up(event: Any) -> None:
        if not is_editing():
            move(-1, event)

    @kb.add("down")
    def _down(event: Any) -> None:
        if not is_editing():
            move(1, event)

    @kb.add("k")
    def _k(event: Any) -> None:
        if is_editing():
            insert("k")
        else:
            move(-1, event)
        event.app.invalidate()

    @kb.add("j")
    def _j(event: Any) -> None:
        if is_editing():
            insert("j")
        else:
            move(1, event)
        event.app.invalidate()

    @kb.add("left")
    def _left(event: Any) -> None:
        if is_editing():
            editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1)
        event.app.invalidate()

    @kb.add("right")
    def _right(event: Any) -> None:
        if is_editing():
            text_value = str(editor.get("text") or "")
            editor["cursor"] = min(len(text_value), as_int(editor.get("cursor"), 0) + 1)
        event.app.invalidate()

    @kb.add("home")
    def _home(event: Any) -> None:
        if is_editing():
            editor["cursor"] = 0
        else:
            selected["index"] = 0
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        if is_editing():
            editor["cursor"] = len(str(editor.get("text") or ""))
        else:
            selected["index"] = len(options) - 1
        event.app.invalidate()

    @kb.add("backspace")
    def _backspace(event: Any) -> None:
        if is_editing():
            backspace()
            event.app.invalidate()

    @kb.add("delete")
    def _delete(event: Any) -> None:
        if is_editing():
            delete_char()
            event.app.invalidate()

    @kb.add("tab")
    def _tab(event: Any) -> None:
        if is_editing():
            autocomplete()
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        if is_editing():
            submit_edit()
            event.app.invalidate()
            return
        key = options[selected["index"]][0]
        if start_edit(key):
            event.app.invalidate()
            return
        event.app.exit(result=key)

    @kb.add("escape")
    @kb.add("q")
    def _escape(event: Any) -> None:
        if is_editing():
            stop_edit("Anulowano edycję pola.")
            event.app.invalidate()
        else:
            event.app.exit(result="0" if str(options[selected["index"]][0]) == "0" else None)

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key, eager=True)
            def _number(event: Any, option_key: str = option_key) -> None:
                if is_editing():
                    insert(option_key)
                    event.app.invalidate()
                else:
                    pos = keys.index(option_key)
                    selected["index"] = pos
                    if start_edit(option_key):
                        event.app.invalidate()
                    else:
                        event.app.exit(result=option_key)

    @kb.add("<any>")
    def _any(event: Any) -> None:
        if is_editing():
            insert(event.data)
            event.app.invalidate()

    style = Style.from_dict({
        "border": "ansicyan",
        "title": "bold ansicyan",
        "hint": "ansibrightblack",
        "message": "ansiyellow",
        "separator": "ansicyan",
        "status.label": "bold",
        "status.value": "",
        "latka.option": "",
        "latka.selected": "reverse bold",
    })
    return str(_application_run_responsive(Application, layout=layout, key_bindings=kb, style=style) or default)


def _option_rows_cursor_app(
    *,
    title: str,
    rows: list[tuple[str, str, str, str]],
    default_key: str = "0",
    header_lines: list[str] | None = None,
) -> str | None:
    """Wspólny widok menu z zawijaniem i odświeżaniem po zmianie rozmiaru."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return None
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    item_rows = [index for index, row in enumerate(rows) if row[0] == "item"]
    if not item_rows:
        return None
    keys = [rows[index][1] for index in item_rows]
    default_row = next((idx for idx in item_rows if rows[idx][1] == default_key), item_rows[0])
    selected = {"row_index": default_row}

    def selected_item_pos() -> int:
        try:
            return item_rows.index(selected["row_index"])
        except ValueError:
            selected["row_index"] = item_rows[0]
            return 0

    def get_text() -> list[tuple[str, str]]:
        width = _terminal_ui_width(fallback=100)
        fragments: list[tuple[str, str]] = []

        def line(style: str, value: str = "", *, subsequent_indent: str = "") -> None:
            for piece in _wrap_text_lines(value, width, subsequent_indent=subsequent_indent):
                fragments.append((style, piece))
                fragments.append(("", "\n"))

        line("class:border", "=" * width)
        line("class:title", f"  {title}")
        line("class:border", "=" * width)
        line("", "")
        for header in header_lines or []:
            line("class:hint", header, subsequent_indent="  ")
        if header_lines:
            line("", "")
            line("class:border", "=" * width)
        for index, (kind, key, label, description) in enumerate(rows):
            if kind == "sep":
                line("class:separator", _menu_separator_text(width))
                continue
            marker = "▶" if index == selected["row_index"] else " "
            style = "class:latka.selected" if index == selected["row_index"] else "class:latka.option"
            line(style, f"  {marker} {key}. {label}", subsequent_indent="    ")
            if description:
                line("class:description", "      " + description, subsequent_indent="      ")
        line("class:border", "=" * width)
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=True, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        pos = (selected_item_pos() + delta) % len(item_rows)
        selected["row_index"] = item_rows[pos]
        event.app.invalidate()

    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None: move(-1, event)

    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None: move(1, event)

    @kb.add("home")
    def _home(event: Any) -> None:
        selected["row_index"] = item_rows[0]
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        selected["row_index"] = item_rows[-1]
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        _kind, key, _label, _description = rows[selected["row_index"]]
        event.app.exit(result=key)

    @kb.add("escape")
    @kb.add("q")
    def _cancel(event: Any) -> None:
        event.app.exit(result=None)

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    for option_key in keys:
        if len(str(option_key)) == 1:
            @kb.add(str(option_key))
            def _number(event: Any, option_key: str = str(option_key)) -> None:
                event.app.exit(result=option_key)

    style = Style.from_dict({
        "border": "ansicyan",
        "title": "bold ansicyan",
        "hint": "ansibrightblack",
        "description": "ansibrightblack",
        "separator": "ansicyan",
        "latka.option": "",
        "latka.selected": "reverse bold",
    })
    return _application_run_responsive(Application, layout=layout, key_bindings=kb, style=style)


def ask_cursor_choice(
    *,
    title: str,
    options: list[tuple[str, str, str]],
    default_key: str = "0",
    header_lines: list[str] | None = None,
) -> str | None:
    rows: list[tuple[str, str, str, str]] = []
    for key, label, description in options:
        if _should_separate_return_option(key, label) and rows and rows[-1][0] != "sep":
            rows.append(_cursor_separator_row())
        rows.append(("item", key, label, description))
    return _option_rows_cursor_app(title=title, rows=rows, default_key=default_key, header_lines=header_lines)


def _settings_rows(state: WizardState, ui_mode: str) -> list[tuple[str, str, str, str]]:
    return [
        ("item", "1", f"Profil pakowania [{state.profile_label()}]", ""),
        _cursor_separator_row(),
        ("item", "2", "Zapisz pełny podgląd listy pakowania do JSON", ""),
        _cursor_separator_row(),
        ("item", "3", f"Ustawienia paczki [{pack_settings_menu_label(state)}]", ""),
        ("item", "4", f"Ustawienia wykluczeń [{exclusions_menu_label(state)}]", ""),
        _cursor_separator_row(),
        ("item", "5", f"Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]", ""),
        _cursor_separator_row(),
        ("item", "0", "Wróć", ""),
    ]


def _settings_cursor_choice(state: WizardState, ui_mode: str) -> str | None:
    return _option_rows_cursor_app(title="Ustawienia", rows=_settings_rows(state, ui_mode), default_key="0")


def _print_settings_menu_plain(state: WizardState, ui_mode: str) -> None:
    section("Ustawienia")
    width = _terminal_ui_width(fallback=78)
    _print_wrapped_plain(f"1. Profil pakowania [{state.profile_label()}]", indent="  ", width=width)
    _print_plain_separator("    ")
    _print_wrapped_plain("2. Zapisz pełny podgląd listy pakowania do JSON", indent="  ", width=width)
    _print_plain_separator("    ")
    _print_wrapped_plain(f"3. Ustawienia paczki [{pack_settings_menu_label(state)}]", indent="  ", width=width)
    _print_wrapped_plain(f"4. Ustawienia wykluczeń [{exclusions_menu_label(state)}]", indent="  ", width=width)
    _print_plain_separator("    ")
    _print_wrapped_plain(f"5. Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]", indent="  ", width=width)
    _print_plain_separator("    ")
    _print_wrapped_plain("0. Wróć", indent="  ", width=width)


def settings_submenu(state: WizardState, ui_mode: str = "plain") -> str:
    """Ustawienia bez dublującego skanowania; podgląd JSON zostaje osobno."""
    while True:
        if should_use_cursor_menu(ui_mode):
            choice = _settings_cursor_choice(state, ui_mode)
            if choice in {None, "0"}:
                return "cancel"
        else:
            _print_settings_menu_plain(state, ui_mode)
            choice = ask_text("Wybór", "0").strip()
            if choice == "0":
                return "cancel"

        normalized = str(choice).strip()
        if normalized == "1":
            configure_profile(state, ui_mode); save_settings(state, quiet=True)
        elif normalized == "2":
            if ensure_ready_for_pack_plan(state, ui_mode=ui_mode):
                if state.plan is None:
                    rebuild_plan(state)
                preview = save_preview_json(state)
                save_settings(state, quiet=True)
                print(f"Zapisano podgląd: {preview}")
                pause()
        elif normalized == "3":
            configure_pack_settings(state, ui_mode); save_settings(state, quiet=True)
        elif normalized == "4":
            exclusion_menu(state, ui_mode); save_settings(state, quiet=True)
        elif normalized == "5":
            ui_mode = configure_ui_mode_preference(state, ui_mode); save_settings(state, quiet=True)
        else:
            print("Nieznana opcja.")


def print_lines_paged(title: str, lines: list[str], *, page_size: int | None = None) -> None:
    """Pager tekstowy z dynamicznym/cyan nagłówkiem i zawijaniem wierszy."""
    def header(label: str) -> None:
        width = _terminal_ui_width(fallback=78)
        print("\n" + _cyan_line_text(width))
        print(f"  {label}:")
        print(_cyan_line_text(width))

    if not lines:
        header(title)
        print("  (brak)")
        print("(END)")
        return

    page_size_value = page_size or terminal_page_size()
    page_size_value = max(1, int(page_size_value))
    total_pages = (len(lines) + page_size_value - 1) // page_size_value

    def print_wrapped_items(page_lines: list[str]) -> None:
        width = _terminal_ui_width(fallback=120)
        for line_value in page_lines:
            for wrapped in _wrap_text_lines(line_value, width, subsequent_indent="      "):
                print(wrapped)

    if total_pages <= 1 or not sys.stdin.isatty():
        header(title)
        print_wrapped_items(lines)
        print("(END)")
        return

    page = 0
    while True:
        start = page * page_size_value
        end = min(start + page_size_value, len(lines))
        header(f"{title} — strona {page + 1}/{total_pages} ({start + 1}-{end} z {len(lines)})")
        print_wrapped_items(lines[start:end])

        if page >= total_pages - 1:
            print("(END)")
            return

        try:
            choice = input(": ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice in {"q", "0", "k", "koniec", "esc", "w", "wroc", "wróć"}:
            print("(END)")
            return
        if choice in {"p", "poprzednia", "prev", "b"}:
            page = max(0, page - 1)
            continue
        page += 1


def ask_bool(prompt: str, default: bool = False, *, require_explicit: bool = False) -> bool:
    suffix = "T/N" if require_explicit else ("T/n" if default else "t/N")
    yes_values = {"t", "tak", "y", "yes", "1", "true"}
    no_values = {"n", "nie", "no", "0", "false"}
    while True:
        value = input(f"{prompt} [{suffix}]: ").strip().lower()
        control = _plain_control_word(value)
        if control == "exit":
            raise UserRequestedAppExit()
        if control == "cancel":
            raise UserCancelledInput()
        if not value:
            if require_explicit:
                continue
            return default
        if value in yes_values:
            return True
        if value in no_values:
            return False
        print("Wpisz T/Tak albo N/Nie.")


def show_pack_list_and_offer_json(state: WizardState, ui_mode: str = "plain") -> None:
    """Opcja 2: tylko pokaż aktualną listę; opcjonalnie zapisz ten sam podgląd do JSON."""
    if state.plan is None:
        rebuild_plan(state)
    else:
        section("Lista do spakowania z ustawieniami")
        print_pack_plan_summary(state)
    if state.out_dir is not None and ask_bool("Zapisać pełny podgląd listy pakowania do JSON", False, require_explicit=True):
        preview = save_preview_json(state)
        print(f"Zapisano podgląd: {preview}")


def run_wizard(initial_source: str | None = None, *, ui_mode: str | None = None) -> int:
    settings_snapshot = snapshot_settings_file()
    state: WizardState | None = None
    try:
        activate_process_guard(prompt_user=True)
        state = initialize_state(initial_source)
        section(f"Jaźń / Łatka — generator paczki ZIP v{VERSION}")
        print_bar(100, 100, label="Ładowanie")
        if state.settings_needs_cleanup:
            save_settings(state, quiet=True)
            settings_snapshot = snapshot_settings_file()
        show_startup_warnings(state)
        ui_mode = resolve_ui_mode_with_optional_install(ui_mode, state)
        save_settings(state, quiet=True)
        settings_snapshot = snapshot_settings_file()
        prepare_plan_on_startup_if_possible(state)
    except UserRequestedAppExit:
        restore_settings_file(settings_snapshot); print("\nZamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
    except KeyboardInterrupt:
        restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. Start aplikacji został anulowany bez tracebacka."); return 130
    except EOFError:
        restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Start aplikacji został anulowany bez tracebacka."); return 130

    while True:
        try:
            assert state is not None
            if not should_use_cursor_menu(ui_mode):
                show_current_state(state)
                print(f"Tryb UI:                    {ui_mode_label(ui_mode)}; auto-start: {on_off_label(state.ui_auto_start)}")
            current_default_choice = default_menu_choice(state)
            choice = ask_menu_choice(state, current_default_choice, ui_mode)
            control_word = _plain_control_word(choice)
            if control_word == "exit": raise UserRequestedAppExit()
            if control_word == "cancel": continue

            known_menu_choices = {"0", "1", "2", "3", "4", "5", "6", "7", "8"}
            if choice not in known_menu_choices:
                if current_default_choice == "3" and state.source_folder is None:
                    if apply_source_path_text(state, choice): save_settings(state, quiet=True)
                    continue
                if current_default_choice == "4" and state.out_dir is None:
                    if apply_output_path_text(state, choice): save_settings(state, quiet=True)
                    continue

            if choice == APP_EXIT_MARKER:
                restore_settings_file(settings_snapshot); print("Zamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
            if choice == "1":
                configure_profile(state, ui_mode); save_settings(state, quiet=True)
            elif choice == "2":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode): save_settings(state, quiet=True); continue
                show_pack_list_and_offer_json(state, ui_mode)
                save_settings(state, quiet=True); pause()
            elif choice == "3":
                configure_source(state); save_settings(state, quiet=True)
            elif choice == "4":
                configure_output(state); save_settings(state, quiet=True)
            elif choice == "5":
                reset_archive_name_from_version(state); save_settings(state, quiet=True)
            elif choice == "6":
                configure_name(state); save_settings(state, quiet=True)
            elif choice == "7":
                _ = settings_submenu(state, ui_mode)
                ui_mode = normalize_ui_mode(state.ui_mode or ui_mode)
                if ui_mode == "auto": ui_mode = resolve_auto_ui_mode()
            elif choice == "8":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode): save_settings(state, quiet=True); continue
                if state.plan is None: rebuild_plan(state)
                print("\nTo zostanie użyte jako podstawa pakowania.")
                print_pack_plan_compact_summary(state)
                if ask_bool("Pokazać listę katalogów i plików przed pakowaniem", False, require_explicit=True):
                    print_pack_items_for_plan(state)
                if not ask_bool("Rozpocząć pakowanie", True, require_explicit=True):
                    continue
                assert state.source_folder is not None and state.out_dir is not None and state.plan is not None
                create_split_zip_from_plan(
                    source_folder=state.source_folder,
                    out_dir=state.out_dir,
                    archive_name=state.archive_name,
                    plan=state.plan,
                    part_size_mb=state.part_size_mb,
                    compression_level=state.compression_level,
                    force=state.force,
                    include_empty_dirs=state.include_empty_dirs,
                    exclude_patterns=state.effective_excludes(),
                    package_version=state.package_version,
                    package_release_name=state.package_release_name,
                    resolved_version_file=state.resolved_version_file,
                    archive_basename_requested=state.archive_basename_requested,
                    append_version_to_name=False,
                    disabled_default_excludes=state.disabled_default_excludes,
                    pack_profile=state.pack_profile,
                    include_prefixes=state.include_prefixes(),
                )
                save_settings(state, quiet=True); return 0
            elif choice == "0":
                exit_action = exit_menu(ui_mode)
                if exit_action == "save": save_settings(state, quiet=False); print("Zakończono bez pakowania."); return 0
                if exit_action == "nosave": restore_settings_file(settings_snapshot); print("Zakończono bez zapisywania zmian."); return 0
                continue
            else:
                print("Nieznana opcja.")
        except UserRequestedAppExit:
            restore_settings_file(settings_snapshot); print("\nZamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
        except KeyboardInterrupt:
            restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. W trybie kursorowym skrótem zamknięcia aplikacji jest Ctrl+X. Zakończono bez zapisywania zmian."); return 130
        except EOFError:
            restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Zamknięto bez zapisywania zmian."); return 130
        except Exception as exc:
            save_settings(state, quiet=True); print(f"BŁĄD: {exc}")
            try: pause()
            except KeyboardInterrupt:
                restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. Zakończono bez zapisywania zmian."); return 130
            except EOFError:
                restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Zamknięto bez zapisywania zmian."); return 130


# =============================================================================
# URUCHAMIANIE APLIKACJI
# =============================================================================


def delete_settings_files() -> list[Path]:
    """Usuwa ustawienia aplikacji, bez dotykania paczek ZIP ani runtime Jaźni."""
    removed: list[Path] = []
    for path in (settings_path(), legacy_settings_path()):
        if path is None:
            continue
        try:
            if path.exists() and path.is_file():
                path.unlink()
                removed.append(path)
        except OSError as exc:
            print(f"UWAGA: nie udało się usunąć {path}: {exc}")
    return removed




def run_direct_pack_from_args(args: argparse.Namespace) -> int:
    """Prosty tryb CLI dla wszystkich czterech profili pakowania."""
    source_raw = str(args.source or "").strip()
    if not source_raw:
        print("BŁĄD: tryb --pack wymaga podania folderu źródłowego.", file=sys.stderr)
        return 2

    source_folder = Path(normalize_path_text(source_raw)).expanduser()
    if not source_folder.exists() or not source_folder.is_dir():
        print(
            f"BŁĄD: folder źródłowy nie istnieje albo nie jest folderem: {source_folder}",
            file=sys.stderr,
        )
        return 2

    out_raw = str(args.out or "").strip()
    out_dir = (
        Path(normalize_path_text(out_raw)).expanduser()
        if out_raw
        else source_folder.resolve().parent
    )
    archive_basename = str(args.name or ARCHIVE_BASENAME or "jazn_latka")
    profile = str(getattr(args, "profile", DEFAULT_PACK_PROFILE) or DEFAULT_PACK_PROFILE)
    if profile not in PACK_PROFILES:
        print(f"BŁĄD: nieznany profil: {profile}", file=sys.stderr)
        return 2

    profile_defaults = as_str_list(PACK_PROFILES[profile].get("exclude_patterns"))
    exclude_patterns = (
        [] if args.no_default_excludes else profile_defaults
    ) + list(args.exclude or [])

    artifact_mode = "diagnostic" if bool(getattr(args, "diagnostic_files", False)) else DEFAULT_ARTIFACT_MODE
    verify_after_pack = not bool(getattr(args, "skip_verify_after_pack", False))
    verify_crc = verify_after_pack and not bool(getattr(args, "skip_crc_after_pack", False))

    try:
        if profile == DUAL_PACKAGE_PROFILE:
            create_dual_split_packages_from_args(
                source_folder=source_folder,
                out_dir=out_dir,
                archive_basename=archive_basename,
                part_size_mb=int(args.part_size_mb),
                compression_level=int(args.compresslevel),
                force=bool(args.force),
                include_empty_dirs=not bool(args.no_empty_dirs),
                exclude_patterns=exclude_patterns,
                append_version_to_name=not bool(args.no_version_suffix),
                version_file=args.version_file,
                artifact_mode=artifact_mode,
                verify_after_pack=verify_after_pack,
                verify_crc=verify_crc,
            )
        else:
            create_split_zip(
                source_folder=source_folder,
                out_dir=out_dir,
                archive_basename=archive_basename,
                part_size_mb=int(args.part_size_mb),
                compression_level=int(args.compresslevel),
                force=bool(args.force),
                include_empty_dirs=not bool(args.no_empty_dirs),
                exclude_patterns=exclude_patterns,
                append_version_to_name=not bool(args.no_version_suffix),
                version_file=args.version_file,
                disabled_default_excludes=[],
                pack_profile=profile,
                include_prefixes=as_str_list(
                    PACK_PROFILES[profile].get("include_prefixes")
                ),
                artifact_mode=artifact_mode,
                verify_after_pack=verify_after_pack,
                verify_crc=verify_crc,
            )
        return 0
    except KeyboardInterrupt:
        print("\nPrzerwano przez użytkownika.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        return 1


# =============================================================================
# NADPISANIA UI v5.15 — inline edycja Ustawień paczki bez wychodzenia z ekranu
# =============================================================================


def _pack_setting_field_for_key(key: str) -> str:
    return {"1": "part_size", "2": "compression"}.get(str(key), "")


def _pack_setting_label_for_field(field: str) -> str:
    return {
        "part_size": "Rozmiar jednej części ZIP",
        "compression": "Poziom kompresji",
    }.get(str(field), "Ustawienie")


def _pack_setting_initial_text(state: WizardState, field: str) -> str:
    if field == "part_size":
        return str(state.part_size_mb)
    if field == "compression":
        return str(state.compression_level)
    return ""


def _apply_pack_setting_value(state: WizardState, field: str, raw: str) -> tuple[bool, str]:
    value_text = str(raw or "").strip()
    if not value_text:
        return False, "BŁĄD: wartość nie może być pusta."
    try:
        value = int(value_text)
    except ValueError:
        return False, "BŁĄD: wpisz liczbę całkowitą."

    if field == "part_size":
        if value < 1:
            return False, "BŁĄD: rozmiar części ZIP musi być >= 1 MiB."
        state.part_size_mb = value
        state.plan = None
        return True, f"Ustawiono rozmiar części ZIP: {state.part_size_mb} MiB"

    if field == "compression":
        if not (0 <= value <= 9):
            return False, "BŁĄD: poziom kompresji musi być w zakresie 0-9."
        state.compression_level = value
        state.plan = None
        return True, f"Ustawiono poziom kompresji: {state.compression_level}"

    return False, "BŁĄD: nieznane pole ustawień paczki."


def _pack_settings_label_for_key(state: WizardState, key: str, editor: dict[str, object] | None = None) -> str:
    editor = editor or {}
    editing_field = str(editor.get("field") or "")
    editing_text = str(editor.get("text") or "")
    editing_cursor = as_int(editor.get("cursor"), len(editing_text))

    def edit_value(field: str, fallback: str) -> str:
        if editing_field == field:
            return _text_with_cursor_marker(editing_text, editing_cursor)
        return fallback

    if key == "1":
        return f"Zmień rozmiar jednej części ZIP [{edit_value('part_size', str(state.part_size_mb))} MiB]"
    if key == "2":
        return f"Zmień poziom kompresji [{edit_value('compression', str(state.compression_level))}]"
    if key == "3":
        return f"Zapisywać puste katalogi [{'tak' if state.include_empty_dirs else 'nie'}]"
    if key == "4":
        return f"Nadpisywać istniejące pliki [{'tak' if state.force else 'nie'}]"
    if key == "5":
        return "Ustaw wszystko krok po kroku"
    if key == "0":
        return "Wróć"
    return ""


def _pack_settings_rows(state: WizardState, editor: dict[str, object] | None = None) -> list[tuple[str, str, str, str]]:
    return [
        ("item", "1", _pack_settings_label_for_key(state, "1", editor), "Wpisz liczbę MiB; minimum 1."),
        ("item", "2", _pack_settings_label_for_key(state, "2", editor), "Zakres 0-9; 6 jest rozsądnym domyślnym poziomem."),
        ("item", "3", _pack_settings_label_for_key(state, "3", editor), "Przełącz Tak/Nie bez opuszczania tego ekranu."),
        ("item", "4", _pack_settings_label_for_key(state, "4", editor), "Przełącz Tak/Nie bez opuszczania tego ekranu."),
        ("item", "5", _pack_settings_label_for_key(state, "5", editor), "Rozmiar i kompresję edytuje w tym samym widoku; Tak/Nie przełączysz na pozycjach 3 i 4."),
        _cursor_separator_row(),
        ("item", "0", _pack_settings_label_for_key(state, "0", editor), "Powrót do menu Ustawień."),
    ]


def _pack_settings_cursor_app(state: WizardState) -> None:
    """Kursorowe Ustawienia paczki z edycją bezpośrednio w nawiasach []."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts

    rows = _pack_settings_rows(state)
    item_rows = [index for index, row in enumerate(rows) if row[0] == "item"]
    key_to_row = {rows[index][1]: index for index in item_rows}
    selected = {"row_index": key_to_row.get("0", item_rows[0])}
    editor: dict[str, object] = {"field": "", "text": "", "cursor": 0, "message": "", "wizard": False}

    def is_editing() -> bool:
        return bool(editor.get("field"))

    def selected_key() -> str:
        _kind, key, _label, _description = rows[selected["row_index"]]
        return str(key)

    def selected_item_pos() -> int:
        try:
            return item_rows.index(selected["row_index"])
        except ValueError:
            selected["row_index"] = item_rows[0]
            return 0

    def start_edit(field: str, *, wizard: bool = False) -> None:
        key = "1" if field == "part_size" else "2"
        selected["row_index"] = key_to_row.get(key, selected["row_index"])
        text = _pack_setting_initial_text(state, field)
        editor.update({
            "field": field,
            "text": text,
            "cursor": len(text),
            "wizard": bool(wizard),
            "message": f"Edytujesz: {_pack_setting_label_for_field(field)}. Pisz w nawiasie []; Enter zapisuje, Esc anuluje.",
        })

    def stop_edit(message: str = "") -> None:
        editor.update({"field": "", "text": "", "cursor": 0, "wizard": False, "message": message})

    def move(delta: int, event: Any) -> None:
        if is_editing():
            event.app.invalidate()
            return
        pos = (selected_item_pos() + delta) % len(item_rows)
        selected["row_index"] = item_rows[pos]
        editor["message"] = ""
        event.app.invalidate()

    def insert(value: str) -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        editor["text"] = text_value[:cursor] + value + text_value[cursor:]
        editor["cursor"] = cursor + len(value)

    def submit_edit(event: Any) -> None:
        field = str(editor.get("field") or "")
        ok, message = _apply_pack_setting_value(state, field, str(editor.get("text") or ""))
        if not ok:
            editor["message"] = message
            event.app.invalidate()
            return
        wizard = bool(editor.get("wizard"))
        if wizard and field == "part_size":
            start_edit("compression", wizard=True)
            editor["message"] = message + " | Następnie ustaw poziom kompresji."
        else:
            stop_edit(message)
        event.app.invalidate()

    def toggle_bool(key: str) -> str:
        if key == "3":
            state.include_empty_dirs = not state.include_empty_dirs
            state.plan = None
            return f"Zapisywanie pustych katalogów: {'tak' if state.include_empty_dirs else 'nie'}"
        if key == "4":
            state.force = not state.force
            state.plan = None
            return f"Nadpisywanie istniejących plików: {'tak' if state.force else 'nie'}"
        return ""

    def activate_selected(event: Any) -> None:
        key = selected_key()
        field = _pack_setting_field_for_key(key)
        if field:
            start_edit(field)
            event.app.invalidate()
            return
        if key in {"3", "4"}:
            editor["message"] = toggle_bool(key)
            event.app.invalidate()
            return
        if key == "5":
            start_edit("part_size", wizard=True)
            event.app.invalidate()
            return
        if key == "0":
            event.app.exit(result=None)

    def get_text() -> list[tuple[str, str]]:
        width = _terminal_ui_width(fallback=100)
        current_rows = _pack_settings_rows(state, editor)
        fragments: list[tuple[str, str]] = []

        def line(style: str, value: str = "", *, subsequent_indent: str = "") -> None:
            for piece in _wrap_text_lines(value, width, subsequent_indent=subsequent_indent):
                fragments.append((style, piece))
                fragments.append(("", "\n"))

        line("class:border", "=" * width)
        line("class:title", "  Ustawienia paczki")
        line("class:border", "=" * width)
        line("", "")
        line("class:hint", f"Profil: {state.profile_label()}", subsequent_indent="  ")
        line("class:hint", f"Zakres include: {state.include_prefixes() or '(cały folder źródłowy)'}", subsequent_indent="  ")
        line("class:hint", "ESC wraca do Ustawień; w trakcie edycji anuluje tylko bieżące pole.", subsequent_indent="  ")
        line("", "")
        line("class:border", "=" * width)
        for index, (kind, key, label, description) in enumerate(current_rows):
            if kind == "sep":
                line("class:separator", _menu_separator_text(width))
                continue
            marker = "▶" if index == selected["row_index"] else " "
            base_style = "class:latka.selected" if index == selected["row_index"] else "class:latka.option"
            if is_editing() and key == selected_key():
                base_style = "class:editing"
            line(base_style, f"  {marker} {key}. {label}", subsequent_indent="    ")
            if description:
                line("class:description", "      " + description, subsequent_indent="      ")
        line("class:border", "=" * width)
        if is_editing():
            line("class:hint", "Edycja pola [] | cyfry/tekst wpisują się w nawiasie | ←/→ ruch kursora | Enter zapisz | Esc anuluj | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
        else:
            line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
        message = str(editor.get("message") or "")
        if message:
            line("class:message", message, subsequent_indent="  ")
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=True, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    @kb.add("up")
    def _up(event: Any) -> None:
        move(-1, event)

    @kb.add("down")
    def _down(event: Any) -> None:
        move(1, event)

    @kb.add("k")
    def _k(event: Any) -> None:
        if is_editing():
            insert("k")
            event.app.invalidate()
        else:
            move(-1, event)

    @kb.add("j")
    def _j(event: Any) -> None:
        if is_editing():
            insert("j")
            event.app.invalidate()
        else:
            move(1, event)

    @kb.add("home")
    def _home(event: Any) -> None:
        if is_editing():
            editor["cursor"] = 0
        else:
            selected["row_index"] = item_rows[0]
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        if is_editing():
            editor["cursor"] = len(str(editor.get("text") or ""))
        else:
            selected["row_index"] = item_rows[-1]
        event.app.invalidate()

    @kb.add("left")
    def _left(event: Any) -> None:
        if is_editing():
            editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1)
            event.app.invalidate()

    @kb.add("right")
    def _right(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            editor["cursor"] = min(len(value), as_int(editor.get("cursor"), len(value)) + 1)
            event.app.invalidate()

    @kb.add("backspace")
    @kb.add("c-h")
    def _backspace(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            cursor = as_int(editor.get("cursor"), len(value))
            if cursor > 0:
                editor["text"] = value[:cursor - 1] + value[cursor:]
                editor["cursor"] = cursor - 1
            event.app.invalidate()

    @kb.add("delete")
    def _delete(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            cursor = as_int(editor.get("cursor"), len(value))
            if cursor < len(value):
                editor["text"] = value[:cursor] + value[cursor + 1:]
            event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        if is_editing():
            submit_edit(event)
        else:
            activate_selected(event)

    @kb.add("escape")
    def _escape(event: Any) -> None:
        if is_editing():
            stop_edit("Anulowano edycję pola. Kursor został w Ustawieniach paczki.")
            event.app.invalidate()
        else:
            event.app.exit(result=None)

    @kb.add("q")
    def _q(event: Any) -> None:
        if is_editing():
            insert("q")
            event.app.invalidate()
        else:
            event.app.exit(result=None)

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    for option_key in ["0", "1", "2", "3", "4", "5"]:
        @kb.add(option_key, eager=True)
        def _number(event: Any, option_key: str = option_key) -> None:
            if is_editing():
                insert(option_key)
                event.app.invalidate()
                return
            selected["row_index"] = key_to_row.get(option_key, selected["row_index"])
            activate_selected(event)

    @kb.add("<any>")
    def _insert_any(event: Any) -> None:
        if is_editing():
            data = getattr(event, "data", "") or ""
            if data and data not in {"\r", "\n", "\t"}:
                insert(data)
                event.app.invalidate()

    style = Style.from_dict({
        "border": "ansicyan",
        "title": "bold ansicyan",
        "hint": "ansibrightblack",
        "description": "ansibrightblack",
        "separator": "ansicyan",
        "message": "ansiyellow",
        "editing": "reverse bold",
        "latka.option": "",
        "latka.selected": "reverse bold",
    })
    _application_run_responsive(Application, layout=layout, key_bindings=kb, style=style)


def configure_pack_settings(state: WizardState, ui_mode: str = "plain") -> None:
    """Ustawienia paczki. W trybie kursorowym edycja zostaje w tym samym widoku."""
    while True:
        if should_use_cursor_menu(ui_mode):
            _pack_settings_cursor_app(state)
            return

        section("Ustawienia paczki")
        print_current_pack_settings_block(state)
        print("\nOpcje:")
        print("  1. Zmień rozmiar jednej części ZIP")
        print("  2. Zmień poziom kompresji")
        print("  3. Włącz/wyłącz zapisywanie pustych katalogów")
        print("  4. Włącz/wyłącz nadpisywanie istniejących plików")
        print("  5. Ustaw wszystko krok po kroku")
        print("  0. Wróć")
        choice = ask_text("Wybór", "0")
        if choice == "1":
            state.part_size_mb = ask_int("Rozmiar części ZIP w MiB", state.part_size_mb, minimum=1)
            state.plan = None
        elif choice == "2":
            state.compression_level = ask_int("Poziom kompresji 0-9", state.compression_level, minimum=0, maximum=9)
            state.plan = None
        elif choice == "3":
            state.include_empty_dirs = not state.include_empty_dirs
            state.plan = None
            print(f"Zapisywanie pustych katalogów: {'tak' if state.include_empty_dirs else 'nie'}")
        elif choice == "4":
            state.force = not state.force
            state.plan = None
            print(f"Nadpisywanie istniejących plików: {'tak' if state.force else 'nie'}")
        elif choice == "5":
            state.part_size_mb = ask_int("Rozmiar części ZIP w MiB", state.part_size_mb, minimum=1)
            state.compression_level = ask_int("Poziom kompresji 0-9", state.compression_level, minimum=0, maximum=9)
            state.include_empty_dirs = ask_bool("Zapisywać puste katalogi", state.include_empty_dirs)
            state.force = ask_bool("Nadpisywać istniejące pliki", state.force)
            state.plan = None
        elif choice == "0":
            return
        else:
            print("Nieznana opcja.")

# =============================================================================
# NADPISANIA UI v5.16 — wąski kursor edycji, T/N bez przewijania promptu,
# ręczne wykluczenia bez zagnieżdżonych Application.run i poprawki etykiet
# =============================================================================

VERSION = "1.6.INTEGRITY-MANIFEST-GATE"


def _zip_menu_display_name(name: str) -> str:
    text = str(name or "").strip()
    return text[:-4] if text.lower().endswith(".zip") else text


def _manual_excludes_enabled_for_label(state: WizardState) -> bool:
    """Etykieta pokazuje faktyczny przełącznik ręcznie dodanych wzorców."""
    return bool(state.use_custom_excludes)


def manual_exclusions_label(state: WizardState) -> str:
    return f"{on_off_label(state.use_custom_excludes)}, wpisów {len(state.custom_excludes)}"


def exclusions_menu_label(state: WizardState) -> str:
    default_total = len(state.profile_default_excludes())
    default_active = len(state.active_default_excludes())
    return (
        f"domyślne {on_off_label(state.use_default_excludes)} {default_active}/{default_total}, "
        f"ręcznie dodane {on_off_label(state.use_custom_excludes)} {len(state.custom_excludes)}"
    )


def menu_options(state: WizardState) -> list[tuple[str, str]]:
    name_label = _zip_menu_display_name(state.archive_name) or "nie ustawiono"
    return [
        ("1", f"1. Profil pakowania [{profile_menu_label(state)}]"),
        ("2", f"2. Pokaż listę do spakowania [{pack_list_settings_label(state)}]"),
        ("3", f"3. Folder do pakowania [{menu_value(state.source_folder)}]"),
        ("4", f"4. Folder zapisu paczki [{menu_value(state.out_dir)}]"),
        ("5", "5. Odśwież nazwę paczki z aktualnej wersji"),
        ("6", f"6. Zmień nazwę paczki [{name_label}]"),
        ("7", "7. Ustawienia"),
        ("8", "8. Pakuj teraz"),
        ("0", "0. Wyjście"),
    ]


def _inline_edit_initial_text(state: WizardState, field: str) -> str:
    if field == "source":
        return str(state.source_folder or "")
    if field == "output":
        return str(state.out_dir or "")
    if field == "name":
        return _zip_menu_display_name(str(state.archive_name or ""))
    return ""


def _format_editable_bracket_label(prefix: str, field_value: str, suffix: str = "") -> list[tuple[str, str]]:
    """Fragmenty jednej pozycji: normalny tekst, a wyróżnienie tylko w nawiasie []."""
    return [("", prefix + "["), ("class:editing", field_value), ("", "]" + suffix)]


def _append_wrapped_fragments(
    fragments: list[tuple[str, str]],
    styled_parts: list[tuple[str, str]],
    width: int,
    *,
    base_style: str = "",
    subsequent_indent: str = "    ",
) -> None:
    """Dodaje krótkie fragmenty stylowane. Długie wartości trzymamy czytelnie przez Window.wrap_lines."""
    for style, text in styled_parts:
        fragments.append((style or base_style, text))
    fragments.append(("", "\n"))


def _cursor_menu_lines(state: WizardState, selected_index: int, inline_editor: dict[str, object] | None = None) -> list[tuple[str, str]]:
    options = menu_options(state)
    selected_index = max(0, min(selected_index, len(options) - 1))
    width = _terminal_ui_width(fallback=100)
    inline_editor = inline_editor or {}
    editing_field = str(inline_editor.get("field") or "")
    editing_text = str(inline_editor.get("text") or "")
    editing_cursor = as_int(inline_editor.get("cursor"), len(editing_text))
    message = str(inline_editor.get("message") or "")
    fragments: list[tuple[str, str]] = []

    def line(style: str, value: str = "", *, subsequent_indent: str = "") -> None:
        for piece in _wrap_text_lines(value, width, subsequent_indent=subsequent_indent):
            fragments.append((style, piece))
            fragments.append(("", "\n"))

    line("class:border", "=" * width)
    line("class:title", f"  Jaźń / Łatka — generator paczki ZIP v{VERSION}")
    line("class:border", "=" * width)
    line("", "")
    for label, value in (
        ("Plan:     ", plan_status_label(state)),
        ("Profil:   ", state.profile_label()),
        ("Źródło:   ", menu_value(state.source_folder)),
        ("Zapis:    ", menu_value(state.out_dir)),
        ("ZIP:      ", state.archive_name or "(nie ustawiono)"),
    ):
        line("class:status.value", f"{label}{value}", subsequent_indent=" " * len(label))
    line("", "")
    line("class:border", "=" * width)
    for idx, (key, label) in enumerate(options):
        if _main_menu_separator_before(key):
            line("class:separator", _menu_separator_text(width))
        field = _inline_edit_field_for_key(key)
        marker = "▶" if idx == selected_index else " "
        selected_style = "class:latka.selected" if idx == selected_index else "class:latka.option"
        if editing_field and field == editing_field:
            edited = _text_with_cursor_marker(editing_text, editing_cursor)
            row_start = f"  {marker} "
            if key == "3":
                parts = [("class:latka.option", row_start + "3. Folder do pakowania ["), ("class:editing", edited), ("class:latka.option", "]")]
            elif key == "4":
                parts = [("class:latka.option", row_start + "4. Folder zapisu paczki ["), ("class:editing", edited), ("class:latka.option", "]")]
            elif key == "6":
                parts = [("class:latka.option", row_start + "6. Zmień nazwę paczki ["), ("class:editing", edited), ("class:latka.option", "]")]
            else:
                parts = [("class:latka.option", row_start + label)]
            _append_wrapped_fragments(fragments, parts, width)
        else:
            line(selected_style, f"  {marker} {label}", subsequent_indent="    ")
    line("class:border", "=" * width)
    if editing_field:
        line("class:hint", "Edycja pola []: wpisuj tekst | Tab autouzupełnij ścieżkę | Enter zapisz | Esc anuluj | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
    else:
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wyjście | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
    if message:
        line("class:message", message, subsequent_indent="  ")
    return fragments


def ask_menu_choice_cursor(state: WizardState, default: str) -> str:
    """Menu główne: Esc/Q zawsze otwiera Wyjście, nie uruchamia domyślnej akcji."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return ask_text("Wybór", default)
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    options = menu_options(state)
    keys = [key for key, _ in options]
    selected = {"index": keys.index(default) if default in keys else 0}
    editor: dict[str, object] = {"field": "", "text": "", "cursor": 0, "message": ""}

    def is_editing() -> bool:
        return bool(editor.get("field"))

    def start_edit(key: str) -> bool:
        field = _inline_edit_field_for_key(key)
        if not field:
            return False
        text = _inline_edit_initial_text(state, field)
        editor.update({"field": field, "text": text, "cursor": len(text), "message": f"Edytujesz: {_inline_edit_label_for_field(field)}. Wpisuj bezpośrednio w nawiasach []."})
        return True

    def stop_edit(message: str = "") -> None:
        editor.update({"field": "", "text": "", "cursor": 0, "message": message})

    def insert(value: str) -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        editor["text"] = text_value[:cursor] + value + text_value[cursor:]
        editor["cursor"] = cursor + len(value)
        editor["message"] = ""

    def backspace() -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        if cursor > 0:
            editor["text"] = text_value[:cursor - 1] + text_value[cursor:]
            editor["cursor"] = cursor - 1

    def delete_char() -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        if cursor < len(text_value):
            editor["text"] = text_value[:cursor] + text_value[cursor + 1:]

    def submit_edit() -> None:
        ok, msg = _apply_inline_edit_value(state, str(editor.get("field") or ""), str(editor.get("text") or ""))
        if ok:
            save_settings(state, quiet=True)
            stop_edit(msg)
        else:
            editor["message"] = msg

    def autocomplete() -> None:
        field = str(editor.get("field") or "")
        if field not in {"source", "output"}:
            editor["message"] = "Tab działa dla ścieżek folderów."
            return
        value, message = _inline_autocomplete_path(str(editor.get("text") or ""), only_directories=True)
        editor["text"] = value
        editor["cursor"] = len(value)
        editor["message"] = message

    def get_text() -> list[tuple[str, str]]:
        return _cursor_menu_lines(state, selected["index"], editor)

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=True, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        if not is_editing():
            selected["index"] = (selected["index"] + delta) % len(options)
        event.app.invalidate()

    @kb.add("up")
    def _up(event: Any) -> None:
        move(-1, event)

    @kb.add("down")
    def _down(event: Any) -> None:
        move(1, event)

    @kb.add("k")
    def _k(event: Any) -> None:
        if is_editing(): insert("k")
        else: selected["index"] = (selected["index"] - 1) % len(options)
        event.app.invalidate()

    @kb.add("j")
    def _j(event: Any) -> None:
        if is_editing(): insert("j")
        else: selected["index"] = (selected["index"] + 1) % len(options)
        event.app.invalidate()

    @kb.add("left")
    def _left(event: Any) -> None:
        if is_editing():
            editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1)
            event.app.invalidate()

    @kb.add("right")
    def _right(event: Any) -> None:
        if is_editing():
            text_value = str(editor.get("text") or "")
            editor["cursor"] = min(len(text_value), as_int(editor.get("cursor"), 0) + 1)
            event.app.invalidate()

    @kb.add("home")
    def _home(event: Any) -> None:
        if is_editing(): editor["cursor"] = 0
        else: selected["index"] = 0
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        if is_editing(): editor["cursor"] = len(str(editor.get("text") or ""))
        else: selected["index"] = len(options) - 1
        event.app.invalidate()

    @kb.add("backspace")
    @kb.add("c-h")
    def _backspace(event: Any) -> None:
        if is_editing():
            backspace(); event.app.invalidate()

    @kb.add("delete")
    def _delete(event: Any) -> None:
        if is_editing():
            delete_char(); event.app.invalidate()

    @kb.add("tab")
    def _tab(event: Any) -> None:
        if is_editing():
            autocomplete(); event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        if is_editing():
            submit_edit(); event.app.invalidate(); return
        key = options[selected["index"]][0]
        if start_edit(key):
            event.app.invalidate(); return
        event.app.exit(result=key)

    @kb.add("escape")
    @kb.add("q")
    def _escape(event: Any) -> None:
        if is_editing():
            stop_edit("Anulowano edycję pola."); event.app.invalidate()
        else:
            event.app.exit(result="0")

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key, eager=True)
            def _number(event: Any, option_key: str = option_key) -> None:
                if is_editing():
                    insert(option_key); event.app.invalidate(); return
                selected["index"] = keys.index(option_key)
                if start_edit(option_key): event.app.invalidate(); return
                event.app.exit(result=option_key)

    @kb.add("<any>")
    def _any(event: Any) -> None:
        if is_editing():
            data = getattr(event, "data", "") or ""
            if data and data not in {"\r", "\n", "\t"}:
                insert(data); event.app.invalidate()

    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "message": "ansiyellow", "separator": "ansicyan", "status.label": "bold",
        "status.value": "", "editing": "reverse bold", "latka.option": "", "latka.selected": "reverse bold",
    })
    result = _application_run_responsive(Application, layout=layout, key_bindings=kb, style=style)
    return "0" if result is None else str(result)


def _read_explicit_bool_inplace(prompt: str) -> bool | None:
    """Jawne T/N: Enter bez odpowiedzi jest ignorowany bez drukowania nowych promptów."""
    if not sys.stdin.isatty():
        return None
    try:
        import msvcrt  # type: ignore
    except Exception:
        msvcrt = None  # type: ignore

    if msvcrt is not None:
        sys.stdout.write(f"{prompt} [T/N]: ")
        sys.stdout.flush()
        buffer = ""
        yes = {"t", "tak", "y", "yes", "1"}
        no = {"n", "nie", "no", "0"}
        while True:
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                _ = msvcrt.getwch()
                continue
            if ch == "\x18":
                raise UserRequestedAppExit()
            if ch == "\x1b":
                sys.stdout.write("\n")
                sys.stdout.flush()
                return False
            if ch in ("\r", "\n"):
                continue
            if ch in ("\b", "\x7f"):
                if buffer:
                    buffer = buffer[:-1]
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if ch.isprintable():
                buffer += ch
                sys.stdout.write(ch)
                sys.stdout.flush()
                raw = buffer.strip().lower()
                if raw in yes:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return True
                if raw in no:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return False
                if len(buffer) > 8:
                    sys.stdout.write("\nWpisz T/Tak albo N/Nie.\n")
                    sys.stdout.write(f"{prompt} [T/N]: ")
                    sys.stdout.flush()
                    buffer = ""
        return None

    # POSIX fallback: jeżeli nie umiemy przejąć klawiszy, zostawiamy zwykłe input().
    return None


def ask_bool(prompt: str, default: bool = False, *, require_explicit: bool = False) -> bool:
    yes_values = {"t", "tak", "y", "yes", "1", "true"}
    no_values = {"n", "nie", "no", "0", "false"}
    if require_explicit:
        result = _read_explicit_bool_inplace(prompt)
        if result is not None:
            return bool(result)
        result2 = _explicit_bool_cursor(prompt)
        if result2 is not None:
            return bool(result2)
    suffix = "T/N" if require_explicit else ("T/n" if default else "t/N")
    while True:
        value = input(f"{prompt} [{suffix}]: ").strip().lower()
        control = _plain_control_word(value)
        if control == "exit":
            raise UserRequestedAppExit()
        if control == "cancel":
            raise UserCancelledInput()
        if not value:
            if require_explicit:
                continue
            return default
        if value in yes_values:
            return True
        if value in no_values:
            return False
        print("Wpisz T/Tak albo N/Nie.")


def _exclusion_cursor_choice(state: WizardState) -> str | None:
    rows: list[tuple[str, str, str, str]] = [
        ("item", "1", f"Domyślne [{on_off_label(state.use_default_excludes)}]", "Globalnie włącz/wyłącz całą listę domyślną."),
        ("item", "2", f"Edytuj domyślne [{len(state.active_default_excludes())}/{len(state.profile_default_excludes())}]", "Tabela pojedynczych wzorców domyślnych."),
        _cursor_separator_row(),
        ("item", "3", f"Ręcznie dodane [{on_off_label(state.use_custom_excludes)}]", "Włącz/wyłącz użycie ręcznie dodanych wzorców."),
        ("item", "4", f"Zarządzaj [{len(state.custom_excludes)}]", "Dodaj, edytuj, usuń pojedyncze albo wyczyść wszystkie."),
        _cursor_separator_row(),
        ("item", "0", "Wróć", "Powrót do ustawień."),
    ]
    return _option_rows_cursor_app(
        title="Ustawienia wykluczeń",
        rows=rows,
        default_key="0",
        header_lines=[
            f"Profil: {state.profile_label()}",
            f"Domyślne aktywne: {len(state.active_default_excludes())} / {len(state.profile_default_excludes())}",
            f"Ręcznie dodane: {manual_exclusions_label(state)}",
        ],
    )


def exclusion_menu(state: WizardState, ui_mode: str = "plain") -> None:
    while True:
        if should_use_cursor_menu(ui_mode):
            choice = _exclusion_cursor_choice(state)
            if choice in {None, "0"}:
                return
        else:
            print_exclusion_status(state)
            print("\nOpcje:")
            print(f"  1. Domyślne [{on_off_label(state.use_default_excludes)}]")
            print("     Globalnie włącz/wyłącz całą listę domyślną.")
            print(f"  2. Edytuj domyślne [{len(state.active_default_excludes())}/{len(state.profile_default_excludes())}]")
            print("     Tabela pojedynczych wzorców domyślnych.")
            _print_plain_separator("    ")
            print(f"  3. Ręcznie dodane [{on_off_label(state.use_custom_excludes)}]")
            print("     Włącz/wyłącz użycie ręcznie dodanych wzorców.")
            print(f"  4. Zarządzaj [{len(state.custom_excludes)}]")
            print("     Dodaj, edytuj, usuń pojedyncze albo wyczyść wszystkie.")
            _print_plain_separator("    ")
            print("  0. Wróć")
            choice = ask_text("Wybór", "0")
            if choice == "0":
                return
        normalized_choice = str(choice).strip().replace(",", ".")
        if normalized_choice == "1":
            state.use_default_excludes = not state.use_default_excludes
            state.plan = None
        elif normalized_choice == "2":
            default_exclusions_table_menu(state, ui_mode)
        elif normalized_choice == "3":
            state.use_custom_excludes = not state.use_custom_excludes
            state.plan = None
        elif normalized_choice == "4":
            manual_exclusion_submenu(state, ui_mode)
        else:
            print("Nieznana opcja.")


def _pack_settings_label_for_key(state: WizardState, key: str, editor: dict[str, object] | None = None) -> str:
    editor = editor or {}
    editing_field = str(editor.get("field") or "")
    editing_text = str(editor.get("text") or "")
    editing_cursor = as_int(editor.get("cursor"), len(editing_text))

    def edit_value(field: str, fallback: str) -> str:
        if editing_field == field:
            return _text_with_cursor_marker(editing_text, editing_cursor)
        return fallback
    if key == "1":
        return f"Zmień rozmiar jednej części ZIP [{edit_value('part_size', str(state.part_size_mb))} MiB]"
    if key == "2":
        return f"Zmień poziom kompresji [{edit_value('compression', str(state.compression_level))}]"
    if key == "3":
        return f"Zapisywać puste katalogi [{'tak' if state.include_empty_dirs else 'nie'}]"
    if key == "4":
        return f"Nadpisywać istniejące pliki [{'tak' if state.force else 'nie'}]"
    if key == "5":
        return "Ustaw wszystko krok po kroku"
    if key == "0":
        return "Wróć"
    return ""


def _pack_settings_cursor_app(state: WizardState) -> None:
    """Ustawienia paczki: edycja wartości tylko w nawiasach, bez podświetlania całego wiersza."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    rows = _pack_settings_rows(state)
    item_rows = [index for index, row in enumerate(rows) if row[0] == "item"]
    key_to_row = {rows[index][1]: index for index in item_rows}
    selected = {"row_index": key_to_row.get("0", item_rows[0])}
    editor: dict[str, object] = {"field": "", "text": "", "cursor": 0, "message": "", "wizard": False}

    def is_editing() -> bool:
        return bool(editor.get("field"))

    def selected_key() -> str:
        _kind, key, _label, _description = rows[selected["row_index"]]
        return str(key)

    def selected_item_pos() -> int:
        try:
            return item_rows.index(selected["row_index"])
        except ValueError:
            selected["row_index"] = item_rows[0]
            return 0

    def start_edit(field: str, *, wizard: bool = False) -> None:
        key = "1" if field == "part_size" else "2"
        selected["row_index"] = key_to_row.get(key, selected["row_index"])
        text = _pack_setting_initial_text(state, field)
        editor.update({"field": field, "text": text, "cursor": len(text), "wizard": bool(wizard), "message": f"Edytujesz: {_pack_setting_label_for_field(field)}. Pisz w nawiasie []; Enter zapisuje, Esc anuluje."})

    def stop_edit(message: str = "") -> None:
        editor.update({"field": "", "text": "", "cursor": 0, "wizard": False, "message": message})

    def move(delta: int, event: Any) -> None:
        if not is_editing():
            selected["row_index"] = item_rows[(selected_item_pos() + delta) % len(item_rows)]
            editor["message"] = ""
        event.app.invalidate()

    def insert(value: str) -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        editor["text"] = text_value[:cursor] + value + text_value[cursor:]
        editor["cursor"] = cursor + len(value)

    def submit_edit(event: Any) -> None:
        field = str(editor.get("field") or "")
        ok, message = _apply_pack_setting_value(state, field, str(editor.get("text") or ""))
        if not ok:
            editor["message"] = message
            event.app.invalidate(); return
        wizard = bool(editor.get("wizard"))
        if wizard and field == "part_size":
            start_edit("compression", wizard=True)
            editor["message"] = message + " | Następnie ustaw poziom kompresji."
        else:
            stop_edit(message)
        event.app.invalidate()

    def toggle_bool(key: str) -> str:
        if key == "3":
            state.include_empty_dirs = not state.include_empty_dirs; state.plan = None
            return f"Zapisywanie pustych katalogów: {'tak' if state.include_empty_dirs else 'nie'}"
        if key == "4":
            state.force = not state.force; state.plan = None
            return f"Nadpisywanie istniejących plików: {'tak' if state.force else 'nie'}"
        return ""

    def activate_selected(event: Any) -> None:
        key = selected_key()
        field = _pack_setting_field_for_key(key)
        if field:
            start_edit(field); event.app.invalidate(); return
        if key in {"3", "4"}:
            editor["message"] = toggle_bool(key); event.app.invalidate(); return
        if key == "5":
            start_edit("part_size", wizard=True); event.app.invalidate(); return
        if key == "0":
            event.app.exit(result=None)

    def append_pack_row(fragments: list[tuple[str, str]], key: str, label: str, marker: str, style: str, width: int) -> None:
        editing_field = str(editor.get("field") or "")
        if editing_field == "part_size" and key == "1":
            value = _text_with_cursor_marker(str(editor.get("text") or ""), as_int(editor.get("cursor"), 0))
            _append_wrapped_fragments(fragments, [("class:latka.option", f"  {marker} 1. Zmień rozmiar jednej części ZIP ["), ("class:editing", value), ("class:latka.option", " MiB]")], width)
            return
        if editing_field == "compression" and key == "2":
            value = _text_with_cursor_marker(str(editor.get("text") or ""), as_int(editor.get("cursor"), 0))
            _append_wrapped_fragments(fragments, [("class:latka.option", f"  {marker} 2. Zmień poziom kompresji ["), ("class:editing", value), ("class:latka.option", "]")], width)
            return
        for piece in _wrap_text_lines(f"  {marker} {key}. {label}", width, subsequent_indent="    "):
            fragments.append((style, piece)); fragments.append(("", "\n"))

    def get_text() -> list[tuple[str, str]]:
        width = _terminal_ui_width(fallback=100)
        current_rows = _pack_settings_rows(state, editor)
        fragments: list[tuple[str, str]] = []
        def line(style: str, value: str = "", *, subsequent_indent: str = "") -> None:
            for piece in _wrap_text_lines(value, width, subsequent_indent=subsequent_indent):
                fragments.append((style, piece)); fragments.append(("", "\n"))
        line("class:border", "=" * width)
        line("class:title", "  Ustawienia paczki")
        line("class:border", "=" * width)
        line("", "")
        line("class:hint", f"Profil: {state.profile_label()}", subsequent_indent="  ")
        line("class:hint", f"Zakres include: {state.include_prefixes() or '(cały folder źródłowy)'}", subsequent_indent="  ")
        line("class:hint", "ESC wraca do Ustawień; w trakcie edycji anuluje tylko bieżące pole.", subsequent_indent="  ")
        line("", "")
        line("class:border", "=" * width)
        for index, (kind, key, label, description) in enumerate(current_rows):
            if kind == "sep":
                line("class:separator", _menu_separator_text(width)); continue
            marker = "▶" if index == selected["row_index"] else " "
            style = "class:latka.selected" if index == selected["row_index"] and not is_editing() else "class:latka.option"
            append_pack_row(fragments, key, label, marker, style, width)
            if description:
                line("class:description", "      " + description, subsequent_indent="      ")
        line("class:border", "=" * width)
        if is_editing():
            line("class:hint", "Edycja pola [] | cyfry/tekst wpisują się w nawiasie | ←/→ ruch kursora | Enter zapisz | Esc anuluj | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
        else:
            line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
        message = str(editor.get("message") or "")
        if message:
            line("class:message", message, subsequent_indent="  ")
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=True, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    @kb.add("up")
    def _up(event: Any) -> None: move(-1, event)
    @kb.add("down")
    def _down(event: Any) -> None: move(1, event)
    @kb.add("k")
    def _k(event: Any) -> None:
        if is_editing(): insert("k"); event.app.invalidate()
        else: move(-1, event)
    @kb.add("j")
    def _j(event: Any) -> None:
        if is_editing(): insert("j"); event.app.invalidate()
        else: move(1, event)
    @kb.add("home")
    def _home(event: Any) -> None:
        if is_editing(): editor["cursor"] = 0
        else: selected["row_index"] = item_rows[0]
        event.app.invalidate()
    @kb.add("end")
    def _end(event: Any) -> None:
        if is_editing(): editor["cursor"] = len(str(editor.get("text") or ""))
        else: selected["row_index"] = item_rows[-1]
        event.app.invalidate()
    @kb.add("left")
    def _left(event: Any) -> None:
        if is_editing(): editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1); event.app.invalidate()
    @kb.add("right")
    def _right(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or "")
            editor["cursor"] = min(len(value), as_int(editor.get("cursor"), len(value)) + 1)
            event.app.invalidate()
    @kb.add("backspace")
    @kb.add("c-h")
    def _backspace(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or ""); cursor = as_int(editor.get("cursor"), len(value))
            if cursor > 0:
                editor["text"] = value[:cursor - 1] + value[cursor:]; editor["cursor"] = cursor - 1
            event.app.invalidate()
    @kb.add("delete")
    def _delete(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or ""); cursor = as_int(editor.get("cursor"), len(value))
            if cursor < len(value): editor["text"] = value[:cursor] + value[cursor + 1:]
            event.app.invalidate()
    @kb.add("enter")
    def _enter(event: Any) -> None:
        if is_editing(): submit_edit(event)
        else: activate_selected(event)
    @kb.add("escape")
    def _escape(event: Any) -> None:
        if is_editing(): stop_edit("Anulowano edycję pola. Kursor został w Ustawieniach paczki."); event.app.invalidate()
        else: event.app.exit(result=None)
    @kb.add("q")
    def _q(event: Any) -> None:
        if is_editing(): insert("q"); event.app.invalidate()
        else: event.app.exit(result=None)
    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None: event.app.exit(exception=UserRequestedAppExit())
    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None: event.app.invalidate()
    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None: event.app.invalidate()
    except Exception:
        pass
    for option_key in ["0", "1", "2", "3", "4", "5"]:
        @kb.add(option_key, eager=True)
        def _number(event: Any, option_key: str = option_key) -> None:
            if is_editing(): insert(option_key); event.app.invalidate(); return
            selected["row_index"] = key_to_row.get(option_key, selected["row_index"]); activate_selected(event)
    @kb.add("<any>")
    def _insert_any(event: Any) -> None:
        if is_editing():
            data = getattr(event, "data", "") or ""
            if data and data not in {"\r", "\n", "\t"}: insert(data); event.app.invalidate()
    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "description": "ansibrightblack", "separator": "ansicyan", "message": "ansiyellow",
        "editing": "reverse bold", "latka.option": "", "latka.selected": "reverse bold",
    })
    _application_run_responsive(Application, layout=layout, key_bindings=kb, style=style)


def _custom_exclusion_list_cursor(state: WizardState, *, mode: str) -> None:
    """Przewijana lista ręcznych wykluczeń. Nie jest wywoływana z wnętrza innego Application.run."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    selected = {"index": 0, "top": 0}
    editor = {"active": False, "text": "", "cursor": 0, "message": ""}
    title = "Edytuj ręcznie dodane wykluczenie" if mode == "edit" else "Usuń ręcznie dodane wykluczenie"

    def visible_row_count() -> int:
        try: lines = shutil.get_terminal_size(fallback=(100, 24)).lines
        except Exception: lines = 24
        return max(4, min(max(1, len(state.custom_excludes)), lines - 12))
    def clamp() -> None:
        count = len(state.custom_excludes)
        if count <= 0:
            selected["index"] = selected["top"] = 0; return
        selected["index"] = max(0, min(selected["index"], count - 1))
        visible = visible_row_count()
        if selected["index"] < selected["top"]: selected["top"] = selected["index"]
        elif selected["index"] >= selected["top"] + visible: selected["top"] = selected["index"] - visible + 1
        selected["top"] = max(0, min(selected["top"], max(0, count - visible)))
    def is_editing() -> bool: return bool(editor.get("active"))
    def move(delta: int) -> None:
        if is_editing() or not state.custom_excludes: return
        selected["index"] = max(0, min(len(state.custom_excludes) - 1, selected["index"] + delta)); clamp()
    def start_edit() -> None:
        if not state.custom_excludes: return
        clamp(); value = state.custom_excludes[selected["index"]]
        editor.update({"active": True, "text": value, "cursor": len(value), "message": ""})
    def stop_edit(message: str = "") -> None:
        editor.update({"active": False, "text": "", "cursor": 0, "message": message})
    def insert(data: str) -> None:
        value = str(editor.get("text") or ""); cursor = as_int(editor.get("cursor"), len(value))
        editor["text"] = value[:cursor] + data + value[cursor:]; editor["cursor"] = cursor + len(data)
    def apply_edit() -> None:
        value = str(editor.get("text") or "").strip()
        if not value: editor["message"] = "Wzorzec nie może być pusty."; return
        clamp(); old = state.custom_excludes[selected["index"]]
        state.custom_excludes[selected["index"]] = value; state.use_custom_excludes = True; state.plan = None
        stop_edit(f"Zmieniono: {old} → {value}")
    def remove_current() -> None:
        if not state.custom_excludes: editor["message"] = "Brak wpisów do usunięcia."; return
        clamp(); old = state.custom_excludes.pop(selected["index"]); state.plan = None
        if selected["index"] >= len(state.custom_excludes): selected["index"] = max(0, len(state.custom_excludes) - 1)
        clamp(); editor["message"] = f"Usunięto: {old}"
    def get_text() -> list[tuple[str, str]]:
        clamp(); width = _terminal_ui_width(fallback=100); fragments: list[tuple[str, str]] = []
        def line(style: str, value: str = "", *, subsequent_indent: str = "") -> None:
            for piece in _wrap_text_lines(value, width, subsequent_indent=subsequent_indent):
                fragments.append((style, piece)); fragments.append(("", "\n"))
        line("class:border", "=" * width); line("class:title", f"  {title}"); line("class:border", "=" * width)
        line("", ""); line("class:hint", "ESC wraca do menu ręcznie dodanych wykluczeń."); line("", ""); line("class:border", "=" * width)
        if not state.custom_excludes:
            line("class:hint", "  (brak ręcznie dodanych wykluczeń)")
        else:
            visible = visible_row_count(); top = selected["top"]; bottom = min(top + visible, len(state.custom_excludes))
            for idx in range(top, bottom):
                marker = "▶" if idx == selected["index"] else " "
                if is_editing() and idx == selected["index"]:
                    value = _text_with_cursor_marker(str(editor.get("text") or ""), as_int(editor.get("cursor"), 0))
                    _append_wrapped_fragments(fragments, [("class:latka.option", f"  {marker} {idx + 1}. ["), ("class:editing", value), ("class:latka.option", "]")], width)
                else:
                    style = "class:latka.selected" if idx == selected["index"] else "class:latka.option"
                    line(style, f"  {marker} {idx + 1}. [{state.custom_excludes[idx]}]", subsequent_indent="    ")
        line("class:separator", _menu_separator_text(width)); line("class:latka.option", "    0. Wróć"); line("class:description", "      Bez zmiany.")
        line("class:border", "=" * width)
        footer = "Edycja []: wpisuj tekst | Tab autouzupełnij | Enter zapisz | Esc anuluj | Ctrl+X zamknij bez zapisu" if is_editing() else "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu"
        line("class:hint", footer, subsequent_indent="  ")
        if editor.get("message"): line("class:message", str(editor.get("message")), subsequent_indent="  ")
        return fragments
    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=True, dont_extend_height=False)
    layout = Layout(window); kb = KeyBindings()
    @kb.add("up")
    def _up(event: Any) -> None: move(-1); event.app.invalidate()
    @kb.add("down")
    def _down(event: Any) -> None: move(1); event.app.invalidate()
    @kb.add("k")
    def _k(event: Any) -> None:
        if is_editing(): insert("k")
        else: move(-1)
        event.app.invalidate()
    @kb.add("j")
    def _j(event: Any) -> None:
        if is_editing(): insert("j")
        else: move(1)
        event.app.invalidate()
    @kb.add("pageup")
    @kb.add("c-u")
    def _page_up(event: Any) -> None: move(-visible_row_count()); event.app.invalidate()
    @kb.add("pagedown")
    @kb.add("c-d")
    def _page_down(event: Any) -> None: move(visible_row_count()); event.app.invalidate()
    @kb.add("home")
    def _home(event: Any) -> None:
        if is_editing(): editor["cursor"] = 0
        else: selected["index"] = selected["top"] = 0
        event.app.invalidate()
    @kb.add("end")
    def _end(event: Any) -> None:
        if is_editing(): editor["cursor"] = len(str(editor.get("text") or ""))
        else: selected["index"] = max(0, len(state.custom_excludes) - 1); clamp()
        event.app.invalidate()
    @kb.add("left")
    def _left(event: Any) -> None:
        if is_editing(): editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1); event.app.invalidate()
    @kb.add("right")
    def _right(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or ""); editor["cursor"] = min(len(value), as_int(editor.get("cursor"), len(value)) + 1); event.app.invalidate()
    @kb.add("backspace")
    @kb.add("c-h")
    def _backspace(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or ""); cursor = as_int(editor.get("cursor"), len(value))
            if cursor > 0: editor["text"] = value[:cursor - 1] + value[cursor:]; editor["cursor"] = cursor - 1
            event.app.invalidate()
    @kb.add("delete")
    def _delete(event: Any) -> None:
        if is_editing():
            value = str(editor.get("text") or ""); cursor = as_int(editor.get("cursor"), len(value))
            if cursor < len(value): editor["text"] = value[:cursor] + value[cursor + 1:]
            event.app.invalidate()
    @kb.add("tab")
    def _tab(event: Any) -> None:
        if is_editing():
            new_text, msg = _inline_autocomplete_path(str(editor.get("text") or ""), only_directories=False)
            editor["text"] = new_text; editor["cursor"] = len(new_text); editor["message"] = msg; event.app.invalidate()
    @kb.add("enter")
    def _enter(event: Any) -> None:
        if is_editing(): apply_edit()
        elif mode == "edit": start_edit()
        else: remove_current()
        event.app.invalidate()
    @kb.add("escape")
    def _escape(event: Any) -> None:
        if is_editing(): stop_edit("Anulowano edycję pola."); event.app.invalidate()
        else: event.app.exit(result=None)
    @kb.add("q")
    def _q(event: Any) -> None:
        if is_editing(): insert("q"); event.app.invalidate()
        else: event.app.exit(result=None)
    @kb.add("0")
    def _zero(event: Any) -> None:
        if is_editing(): insert("0"); event.app.invalidate()
        else: event.app.exit(result=None)
    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None: event.app.exit(exception=UserRequestedAppExit())
    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None: event.app.invalidate()
    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None: event.app.invalidate()
    except Exception: pass
    @kb.add("<any>")
    def _any(event: Any) -> None:
        if is_editing():
            data = getattr(event, "data", "") or ""
            if data and data not in {"\r", "\n", "\t"}: insert(data); event.app.invalidate()
    style = Style.from_dict({"border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack", "description": "ansibrightblack", "separator": "ansicyan", "message": "ansiyellow", "editing": "reverse bold", "latka.option": "", "latka.selected": "reverse bold"})
    _application_run_responsive(Application, layout=layout, key_bindings=kb, style=style)


def _manual_exclusion_cursor_menu(state: WizardState) -> None:
    """Menu ręcznie dodanych wykluczeń bez zagnieżdżania Application.run w handlerze."""
    while True:
        parts = prompt_toolkit_parts()
        if parts is None:
            return
        Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
        rows: list[tuple[str, str, str, str]] = [
            ("item", "1", "Dodaj", "Dodaj nowy ręczny wzorzec."),
            ("item", "2", "Edytuj", "Zmień wybrany ręczny wzorzec."),
            ("item", "3", "Usuń pojedyncze", "Usuń jeden wybrany wzorzec."),
            ("item", "4", "Wyczyść wszystkie", "Usuń wszystkie ręczne wzorce."),
            _cursor_separator_row(),
            ("item", "0", "Wróć", "Powrót do ustawień wykluczeń."),
        ]
        item_rows = [idx for idx, row in enumerate(rows) if row[0] == "item"]
        keys = [rows[idx][1] for idx in item_rows]
        selected = {"row_index": item_rows[0]}
        editor = {"active": False, "text": "", "cursor": 0, "message": ""}
        def selected_item_pos() -> int:
            try: return item_rows.index(selected["row_index"])
            except ValueError: selected["row_index"] = item_rows[0]; return 0
        def is_editing_add() -> bool: return bool(editor.get("active"))
        def start_add() -> None:
            selected["row_index"] = item_rows[0]; editor.update({"active": True, "text": "", "cursor": 0, "message": ""})
        def stop_add(message: str = "") -> None:
            editor.update({"active": False, "text": "", "cursor": 0, "message": message})
        def insert(data: str) -> None:
            value = str(editor.get("text") or ""); cursor = as_int(editor.get("cursor"), len(value))
            editor["text"] = value[:cursor] + data + value[cursor:]; editor["cursor"] = cursor + len(data)
        def apply_add() -> None:
            value = str(editor.get("text") or "").strip()
            if not value: editor["message"] = "Wzorzec nie może być pusty."; return
            state.custom_excludes.append(value); state.use_custom_excludes = True; state.plan = None
            stop_add(f"Dodano: {value}"); selected["row_index"] = item_rows[0]
        def get_text() -> list[tuple[str, str]]:
            width = _terminal_ui_width(fallback=100); fragments: list[tuple[str, str]] = []
            def line(style: str, value: str = "", *, subsequent_indent: str = "") -> None:
                for piece in _wrap_text_lines(value, width, subsequent_indent=subsequent_indent):
                    fragments.append((style, piece)); fragments.append(("", "\n"))
            line("class:border", "=" * width); line("class:title", "  Ręcznie dodane wykluczenie"); line("class:border", "=" * width)
            line("", ""); line("class:hint", f"Ręcznie dodane: {manual_exclusions_label(state)}"); line("", ""); line("class:border", "=" * width)
            for index, (kind, key, label, description) in enumerate(rows):
                if kind == "sep": line("class:separator", _menu_separator_text(width)); continue
                marker = "▶" if index == selected["row_index"] else " "
                style = "class:latka.selected" if index == selected["row_index"] and not is_editing_add() else "class:latka.option"
                if key == "1" and is_editing_add():
                    value = _text_with_cursor_marker(str(editor.get("text") or ""), as_int(editor.get("cursor"), 0))
                    _append_wrapped_fragments(fragments, [("class:latka.option", f"  {marker} 1. Dodaj ["), ("class:editing", value), ("class:latka.option", "]")], width)
                else:
                    shown_label = f"Dodaj [ścieżka]" if key == "1" else label
                    line(style, f"  {marker} {key}. {shown_label}", subsequent_indent="    ")
                if description: line("class:description", "      " + description, subsequent_indent="      ")
            line("class:border", "=" * width)
            footer = "Edycja []: wpisuj tekst | Tab autouzupełnij | Enter dodaj | Esc anuluj | Ctrl+X zamknij bez zapisu" if is_editing_add() else "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu"
            line("class:hint", footer, subsequent_indent="  ")
            if editor.get("message"): line("class:message", str(editor.get("message")), subsequent_indent="  ")
            return fragments
        control = FormattedTextControl(text=get_text, focusable=True); window = Window(content=control, wrap_lines=True, dont_extend_height=True); layout = Layout(window); kb = KeyBindings()
        def move(delta: int, event: Any) -> None:
            if not is_editing_add(): selected["row_index"] = item_rows[(selected_item_pos() + delta) % len(item_rows)]
            event.app.invalidate()
        @kb.add("up")
        def _up(event: Any) -> None: move(-1, event)
        @kb.add("down")
        def _down(event: Any) -> None: move(1, event)
        @kb.add("k")
        def _k(event: Any) -> None:
            if is_editing_add(): insert("k")
            else: selected["row_index"] = item_rows[(selected_item_pos() - 1) % len(item_rows)]
            event.app.invalidate()
        @kb.add("j")
        def _j(event: Any) -> None:
            if is_editing_add(): insert("j")
            else: selected["row_index"] = item_rows[(selected_item_pos() + 1) % len(item_rows)]
            event.app.invalidate()
        @kb.add("home")
        def _home(event: Any) -> None:
            if is_editing_add(): editor["cursor"] = 0
            else: selected["row_index"] = item_rows[0]
            event.app.invalidate()
        @kb.add("end")
        def _end(event: Any) -> None:
            if is_editing_add(): editor["cursor"] = len(str(editor.get("text") or ""))
            else: selected["row_index"] = item_rows[-1]
            event.app.invalidate()
        @kb.add("left")
        def _left(event: Any) -> None:
            if is_editing_add(): editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1); event.app.invalidate()
        @kb.add("right")
        def _right(event: Any) -> None:
            if is_editing_add():
                value = str(editor.get("text") or ""); editor["cursor"] = min(len(value), as_int(editor.get("cursor"), len(value)) + 1); event.app.invalidate()
        @kb.add("backspace")
        @kb.add("c-h")
        def _backspace(event: Any) -> None:
            if is_editing_add():
                value = str(editor.get("text") or ""); cursor = as_int(editor.get("cursor"), len(value))
                if cursor > 0: editor["text"] = value[:cursor - 1] + value[cursor:]; editor["cursor"] = cursor - 1
                event.app.invalidate()
        @kb.add("delete")
        def _delete(event: Any) -> None:
            if is_editing_add():
                value = str(editor.get("text") or ""); cursor = as_int(editor.get("cursor"), len(value))
                if cursor < len(value): editor["text"] = value[:cursor] + value[cursor + 1:]
                event.app.invalidate()
        @kb.add("tab")
        def _tab(event: Any) -> None:
            if is_editing_add():
                new_text, msg = _inline_autocomplete_path(str(editor.get("text") or ""), only_directories=False)
                editor["text"] = new_text; editor["cursor"] = len(new_text); editor["message"] = msg; event.app.invalidate()
        @kb.add("enter")
        def _enter(event: Any) -> None:
            if is_editing_add(): apply_add(); event.app.invalidate(); return
            _kind, key, _label, _description = rows[selected["row_index"]]
            if key == "1": start_add(); event.app.invalidate(); return
            if key == "2": event.app.exit(result="edit"); return
            if key == "3": event.app.exit(result="remove"); return
            if key == "4": state.custom_excludes.clear(); state.plan = None; editor["message"] = "Wyczyszczono wszystkie ręcznie dodane wykluczenia."; event.app.invalidate(); return
            if key == "0": event.app.exit(result=None)
        @kb.add("escape")
        def _escape(event: Any) -> None:
            if is_editing_add(): stop_add("Anulowano dodawanie."); event.app.invalidate()
            else: event.app.exit(result=None)
        @kb.add("q")
        def _q(event: Any) -> None:
            if is_editing_add(): insert("q"); event.app.invalidate()
            else: event.app.exit(result=None)
        @kb.add("c-x", eager=True)
        def _ctrl_x_exit(event: Any) -> None: event.app.exit(exception=UserRequestedAppExit())
        @kb.add("c-c", eager=True)
        def _ctrl_c_noop(event: Any) -> None: event.app.invalidate()
        try:
            @kb.add("<sigint>", eager=True)
            def _sigint_noop(event: Any) -> None: event.app.invalidate()
        except Exception: pass
        for option_key in keys:
            if len(option_key) == 1:
                @kb.add(option_key)
                def _number(event: Any, option_key: str = option_key) -> None:
                    if is_editing_add(): insert(option_key); event.app.invalidate(); return
                    row_index = next((idx for idx, row in enumerate(rows) if row[0] == "item" and row[1] == option_key), selected["row_index"])
                    selected["row_index"] = row_index
                    _kind, key, _label, _description = rows[row_index]
                    if key == "1": start_add(); event.app.invalidate(); return
                    if key == "2": event.app.exit(result="edit"); return
                    if key == "3": event.app.exit(result="remove"); return
                    if key == "4": state.custom_excludes.clear(); state.plan = None; editor["message"] = "Wyczyszczono wszystkie ręcznie dodane wykluczenia."; event.app.invalidate(); return
                    if key == "0": event.app.exit(result=None)
        @kb.add("<any>")
        def _any(event: Any) -> None:
            if is_editing_add():
                data = getattr(event, "data", "") or ""
                if data and data not in {"\r", "\n", "\t"}: insert(data); event.app.invalidate()
        style = Style.from_dict({"border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack", "description": "ansibrightblack", "separator": "ansicyan", "message": "ansiyellow", "editing": "reverse bold", "latka.option": "", "latka.selected": "reverse bold"})
        result = _application_run_responsive(Application, layout=layout, key_bindings=kb, style=style)
        if result is None:
            return
        if result == "edit":
            _custom_exclusion_list_cursor(state, mode="edit")
            continue
        if result == "remove":
            _custom_exclusion_list_cursor(state, mode="remove")
            continue



# =============================================================================
# NADPISANIA UI v5.17 — powrót kursora do ostatniej pozycji i brak pauzy po Nie
# =============================================================================

VERSION = "1.6.INTEGRITY-MANIFEST-GATE"

# Pamięć pozycji kursora działa w ramach jednego uruchomienia aplikacji.
# Nie zapisujemy tego do JSON-a, bo to stan nawigacji, nie ustawienie paczki.
_CURSOR_LAST_KEYS: dict[str, str] = {}


def _cursor_scope_name(title: str) -> str:
    return " ".join(str(title or "menu").strip().lower().split())


def _remembered_key(scope: str, default_key: str, keys: Iterable[str]) -> str:
    valid = [str(k) for k in keys]
    remembered = _CURSOR_LAST_KEYS.get(_cursor_scope_name(scope), "")
    if remembered in valid:
        return remembered
    default_text = str(default_key)
    return default_text if default_text in valid else (valid[0] if valid else default_text)


def _store_cursor_key(scope: str, key: str, keys: Iterable[str]) -> None:
    valid = {str(k) for k in keys}
    key_text = str(key)
    if key_text in valid:
        _CURSOR_LAST_KEYS[_cursor_scope_name(scope)] = key_text


def _read_explicit_bool_inplace(prompt: str) -> bool | None:
    """Jawne T/N bez powielania promptu po pustym Enterze.

    Na Windows używa msvcrt.getwch(), więc Enter bez litery T/N jest ignorowany
    w tej samej linii. Gdy msvcrt nie jest dostępny, zwraca None i pozwala
    przejść do prompt_toolkit/fallbacku.
    """
    try:
        import msvcrt  # type: ignore
    except Exception:
        return None
    try:
        sys.stdout.write(f"{prompt} [T/N]: ")
        sys.stdout.flush()
        buffer = ""
        yes = {"t", "tak", "y", "yes", "1", "true"}
        no = {"n", "nie", "no", "0", "false"}
        while True:
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                _ = msvcrt.getwch()
                continue
            if ch == "\x18":
                raise UserRequestedAppExit()
            if ch == "\x1b":
                sys.stdout.write("\n")
                sys.stdout.flush()
                return False
            if ch in ("\r", "\n"):
                # Najważniejsza poprawka: sam Enter naprawdę nie robi nic
                # i nie drukuje kolejnego promptu.
                continue
            if ch in ("\b", "\x7f"):
                if buffer:
                    buffer = buffer[:-1]
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if not ch.isprintable():
                continue
            buffer += ch
            sys.stdout.write(ch)
            sys.stdout.flush()
            raw = buffer.strip().lower()
            if raw in yes:
                sys.stdout.write("\n")
                sys.stdout.flush()
                return True
            if raw in no:
                sys.stdout.write("\n")
                sys.stdout.flush()
                return False
            if not any(item.startswith(raw) for item in (yes | no)) or len(buffer) > 8:
                sys.stdout.write("\nWpisz T/Tak albo N/Nie.\n")
                sys.stdout.write(f"{prompt} [T/N]: ")
                sys.stdout.flush()
                buffer = ""
    except UserRequestedAppExit:
        raise
    except Exception:
        return None


def ask_bool(prompt: str, default: bool = False, *, require_explicit: bool = False) -> bool:
    yes_values = {"t", "tak", "y", "yes", "1", "true"}
    no_values = {"n", "nie", "no", "0", "false"}
    if require_explicit:
        result = _read_explicit_bool_inplace(prompt)
        if result is not None:
            return bool(result)
        # Fallback prompt_toolkit też nie przewija promptu po pustym Enterze.
        result2 = _explicit_bool_cursor(prompt)
        if result2 is not None:
            return bool(result2)
    suffix = "T/N" if require_explicit else ("T/n" if default else "t/N")
    while True:
        value = input(f"{prompt} [{suffix}]: ").strip().lower()
        control = _plain_control_word(value)
        if control == "exit":
            raise UserRequestedAppExit()
        if control == "cancel":
            raise UserCancelledInput()
        if not value:
            if require_explicit:
                # Logicznie no-op; zwykły input() nie umie zatrzymać kursora w tej
                # samej linii, ale nie zatwierdza żadnej decyzji.
                continue
            return default
        if value in yes_values:
            return True
        if value in no_values:
            return False
        print("Wpisz T/Tak albo N/Nie.")


def ask_menu_choice_cursor(state: WizardState, default: str) -> str:
    """Menu główne z pamięcią ostatniej pozycji i Esc/Q -> Wyjście."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return ask_text("Wybór", default)
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    options = menu_options(state)
    keys = [key for key, _ in options]
    start_key = _remembered_key("menu główne", default, keys)
    selected = {"index": keys.index(start_key) if start_key in keys else 0}
    editor: dict[str, object] = {"field": "", "text": "", "cursor": 0, "message": ""}

    def is_editing() -> bool:
        return bool(editor.get("field"))

    def selected_key() -> str:
        return str(options[selected["index"]][0])

    def start_edit(key: str) -> bool:
        field = _inline_edit_field_for_key(key)
        if not field:
            return False
        text = _inline_edit_initial_text(state, field)
        editor.update({"field": field, "text": text, "cursor": len(text), "message": f"Edytujesz: {_inline_edit_label_for_field(field)}. Wpisuj bezpośrednio w nawiasach []."})
        return True

    def stop_edit(message: str = "") -> None:
        editor.update({"field": "", "text": "", "cursor": 0, "message": message})

    def insert(value: str) -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        editor["text"] = text_value[:cursor] + value + text_value[cursor:]
        editor["cursor"] = cursor + len(value)
        editor["message"] = ""

    def backspace() -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        if cursor > 0:
            editor["text"] = text_value[:cursor - 1] + text_value[cursor:]
            editor["cursor"] = cursor - 1

    def delete_char() -> None:
        text_value = str(editor.get("text") or "")
        cursor = as_int(editor.get("cursor"), len(text_value))
        if cursor < len(text_value):
            editor["text"] = text_value[:cursor] + text_value[cursor + 1:]

    def submit_edit() -> None:
        ok, msg = _apply_inline_edit_value(state, str(editor.get("field") or ""), str(editor.get("text") or ""))
        if ok:
            save_settings(state, quiet=True)
            _store_cursor_key("menu główne", selected_key(), keys)
            stop_edit(msg)
        else:
            editor["message"] = msg

    def autocomplete() -> None:
        field = str(editor.get("field") or "")
        if field not in {"source", "output"}:
            editor["message"] = "Tab działa dla ścieżek folderów."
            return
        value, message = _inline_autocomplete_path(str(editor.get("text") or ""), only_directories=True)
        editor["text"] = value
        editor["cursor"] = len(value)
        editor["message"] = message

    def get_text() -> list[tuple[str, str]]:
        return _cursor_menu_lines(state, selected["index"], editor)

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=True, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        if not is_editing():
            selected["index"] = (selected["index"] + delta) % len(options)
        event.app.invalidate()

    @kb.add("up")
    def _up(event: Any) -> None:
        move(-1, event)

    @kb.add("down")
    def _down(event: Any) -> None:
        move(1, event)

    @kb.add("k")
    def _k(event: Any) -> None:
        if is_editing(): insert("k")
        else: selected["index"] = (selected["index"] - 1) % len(options)
        event.app.invalidate()

    @kb.add("j")
    def _j(event: Any) -> None:
        if is_editing(): insert("j")
        else: selected["index"] = (selected["index"] + 1) % len(options)
        event.app.invalidate()

    @kb.add("left")
    def _left(event: Any) -> None:
        if is_editing():
            editor["cursor"] = max(0, as_int(editor.get("cursor"), 0) - 1)
        event.app.invalidate()

    @kb.add("right")
    def _right(event: Any) -> None:
        if is_editing():
            text_value = str(editor.get("text") or "")
            editor["cursor"] = min(len(text_value), as_int(editor.get("cursor"), 0) + 1)
        event.app.invalidate()

    @kb.add("home")
    def _home(event: Any) -> None:
        if is_editing(): editor["cursor"] = 0
        else: selected["index"] = 0
        event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        if is_editing(): editor["cursor"] = len(str(editor.get("text") or ""))
        else: selected["index"] = len(options) - 1
        event.app.invalidate()

    @kb.add("backspace")
    @kb.add("c-h")
    def _backspace(event: Any) -> None:
        if is_editing(): backspace()
        event.app.invalidate()

    @kb.add("delete")
    def _delete(event: Any) -> None:
        if is_editing(): delete_char()
        event.app.invalidate()

    @kb.add("tab")
    def _tab(event: Any) -> None:
        if is_editing(): autocomplete()
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        if is_editing():
            submit_edit(); event.app.invalidate(); return
        key = selected_key()
        _store_cursor_key("menu główne", key, keys)
        if start_edit(key):
            event.app.invalidate(); return
        event.app.exit(result=key)

    @kb.add("escape")
    @kb.add("q")
    def _escape(event: Any) -> None:
        if is_editing():
            stop_edit("Anulowano edycję pola."); event.app.invalidate(); return
        _store_cursor_key("menu główne", "0", keys)
        event.app.exit(result="0")

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    for option_key in keys:
        if len(option_key) == 1:
            @kb.add(option_key, eager=True)
            def _number(event: Any, option_key: str = option_key) -> None:
                if is_editing():
                    insert(option_key); event.app.invalidate(); return
                selected["index"] = keys.index(option_key)
                _store_cursor_key("menu główne", option_key, keys)
                if start_edit(option_key): event.app.invalidate(); return
                event.app.exit(result=option_key)

    @kb.add("<any>")
    def _any(event: Any) -> None:
        if is_editing():
            data = getattr(event, "data", "") or ""
            if data and data not in {"\r", "\n", "\t"}:
                if "\x18" in data:
                    event.app.exit(exception=UserRequestedAppExit()); return
                insert(data); event.app.invalidate()

    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "message": "ansiyellow", "separator": "ansicyan", "status.label": "bold",
        "status.value": "", "editing": "reverse bold", "latka.option": "", "latka.selected": "reverse bold",
    })
    result = _application_run_responsive(Application, layout=layout, key_bindings=kb, style=style)
    return str(result or _remembered_key("menu główne", default, keys))


def _option_rows_cursor_app(
    *,
    title: str,
    rows: list[tuple[str, str, str, str]],
    default_key: str = "0",
    header_lines: list[str] | None = None,
) -> str | None:
    """Wspólny widok menu z pamięcią ostatnio zaznaczonego wiersza."""
    parts = prompt_toolkit_parts()
    if parts is None:
        return None
    Application, KeyBindings, Layout, Window, FormattedTextControl, Style = parts
    item_rows = [index for index, row in enumerate(rows) if row[0] == "item"]
    if not item_rows:
        return None
    keys = [str(rows[index][1]) for index in item_rows]
    scope = title or "menu"
    start_key = _remembered_key(scope, default_key, keys)
    default_row = next((idx for idx in item_rows if str(rows[idx][1]) == start_key), item_rows[0])
    selected = {"row_index": default_row}

    def selected_item_pos() -> int:
        try:
            return item_rows.index(selected["row_index"])
        except ValueError:
            selected["row_index"] = item_rows[0]
            return 0

    def selected_key() -> str:
        _kind, key, _label, _description = rows[selected["row_index"]]
        return str(key)

    def get_text() -> list[tuple[str, str]]:
        width = _terminal_ui_width(fallback=100)
        fragments: list[tuple[str, str]] = []
        def line(style: str, value: str = "", *, subsequent_indent: str = "") -> None:
            for piece in _wrap_text_lines(value, width, subsequent_indent=subsequent_indent):
                fragments.append((style, piece)); fragments.append(("", "\n"))
        line("class:border", "=" * width)
        line("class:title", f"  {title}")
        line("class:border", "=" * width)
        line("", "")
        for header in header_lines or []:
            line("class:hint", header, subsequent_indent="  ")
        if header_lines:
            line("", ""); line("class:border", "=" * width)
        for index, (kind, key, label, description) in enumerate(rows):
            if kind == "sep":
                line("class:separator", _menu_separator_text(width)); continue
            marker = "▶" if index == selected["row_index"] else " "
            style = "class:latka.selected" if index == selected["row_index"] else "class:latka.option"
            line(style, f"  {marker} {key}. {label}", subsequent_indent="    ")
            if description:
                line("class:description", "      " + description, subsequent_indent="      ")
        line("class:border", "=" * width)
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wróć o 1 poziom | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
        return fragments

    control = FormattedTextControl(text=get_text, focusable=True)
    window = Window(content=control, wrap_lines=True, dont_extend_height=True)
    layout = Layout(window)
    kb = KeyBindings()

    def move(delta: int, event: Any) -> None:
        pos = (selected_item_pos() + delta) % len(item_rows)
        selected["row_index"] = item_rows[pos]
        event.app.invalidate()

    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None: move(-1, event)

    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None: move(1, event)

    @kb.add("home")
    def _home(event: Any) -> None:
        selected["row_index"] = item_rows[0]; event.app.invalidate()

    @kb.add("end")
    def _end(event: Any) -> None:
        selected["row_index"] = item_rows[-1]; event.app.invalidate()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        key = selected_key()
        _store_cursor_key(scope, key, keys)
        event.app.exit(result=key)

    @kb.add("escape")
    @kb.add("q")
    def _cancel(event: Any) -> None:
        _store_cursor_key(scope, selected_key(), keys)
        event.app.exit(result=None)

    @kb.add("c-x", eager=True)
    def _ctrl_x_exit(event: Any) -> None:
        event.app.exit(exception=UserRequestedAppExit())

    @kb.add("c-c", eager=True)
    def _ctrl_c_noop(event: Any) -> None:
        event.app.invalidate()

    try:
        @kb.add("<sigint>", eager=True)
        def _sigint_noop(event: Any) -> None:
            event.app.invalidate()
    except Exception:
        pass

    for option_key in keys:
        if len(str(option_key)) == 1:
            @kb.add(str(option_key))
            def _number(event: Any, option_key: str = str(option_key)) -> None:
                _store_cursor_key(scope, option_key, keys)
                event.app.exit(result=option_key)

    style = Style.from_dict({
        "border": "ansicyan", "title": "bold ansicyan", "hint": "ansibrightblack",
        "description": "ansibrightblack", "separator": "ansicyan",
        "latka.option": "", "latka.selected": "reverse bold",
    })
    return _application_run_responsive(Application, layout=layout, key_bindings=kb, style=style)


def ask_cursor_choice(
    *,
    title: str,
    options: list[tuple[str, str, str]],
    default_key: str = "0",
    header_lines: list[str] | None = None,
) -> str | None:
    rows: list[tuple[str, str, str, str]] = []
    for key, label, description in options:
        if _should_separate_return_option(key, label) and rows and rows[-1][0] != "sep":
            rows.append(_cursor_separator_row())
        rows.append(("item", key, label, description))
    return _option_rows_cursor_app(title=title, rows=rows, default_key=default_key, header_lines=header_lines)


def show_pack_list_and_offer_json(state: WizardState, ui_mode: str = "plain") -> None:
    """Opcja 2: pokaż listę i po odpowiedzi N wróć od razu do menu — bez Enter=dalerj."""
    if state.plan is None:
        rebuild_plan(state)
    else:
        section("Lista do spakowania z ustawieniami")
        print_pack_plan_summary(state)
    if state.out_dir is not None and ask_bool("Zapisać pełny podgląd listy pakowania do JSON", False, require_explicit=True):
        preview = save_preview_json(state)
        print(f"Zapisano podgląd: {preview}")


def run_wizard(initial_source: str | None = None, *, ui_mode: str | None = None) -> int:
    settings_snapshot = snapshot_settings_file()
    state: WizardState | None = None
    try:
        activate_process_guard(prompt_user=True)
        state = initialize_state(initial_source)
        section(f"Jaźń / Łatka — generator paczki ZIP v{VERSION}")
        print_bar(100, 100, label="Ładowanie")
        if state.settings_needs_cleanup:
            save_settings(state, quiet=True)
            settings_snapshot = snapshot_settings_file()
        show_startup_warnings(state)
        ui_mode = resolve_ui_mode_with_optional_install(ui_mode, state)
        save_settings(state, quiet=True)
        settings_snapshot = snapshot_settings_file()
        prepare_plan_on_startup_if_possible(state)
    except UserRequestedAppExit:
        restore_settings_file(settings_snapshot); print("\nZamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
    except KeyboardInterrupt:
        restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. Start aplikacji został anulowany bez tracebacka."); return 130
    except EOFError:
        restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Start aplikacji został anulowany bez tracebacka."); return 130

    while True:
        try:
            assert state is not None
            if not should_use_cursor_menu(ui_mode):
                show_current_state(state)
                print(f"Tryb UI:                    {ui_mode_label(ui_mode)}; auto-start: {on_off_label(state.ui_auto_start)}")
            current_default_choice = default_menu_choice(state)
            choice = ask_menu_choice(state, current_default_choice, ui_mode)
            control_word = _plain_control_word(choice)
            if control_word == "exit": raise UserRequestedAppExit()
            if control_word == "cancel": continue

            known_menu_choices = {"0", "1", "2", "3", "4", "5", "6", "7", "8"}
            if choice not in known_menu_choices:
                if current_default_choice == "3" and state.source_folder is None:
                    if apply_source_path_text(state, choice): save_settings(state, quiet=True)
                    continue
                if current_default_choice == "4" and state.out_dir is None:
                    if apply_output_path_text(state, choice): save_settings(state, quiet=True)
                    continue

            if choice == APP_EXIT_MARKER:
                restore_settings_file(settings_snapshot); print("Zamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
            if choice == "1":
                configure_profile(state, ui_mode); save_settings(state, quiet=True)
            elif choice == "2":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode): save_settings(state, quiet=True); continue
                show_pack_list_and_offer_json(state, ui_mode)
                save_settings(state, quiet=True)
                # Bez pause(): po N wracamy od razu do menu, a kursor zostaje na opcji 2.
            elif choice == "3":
                configure_source(state); save_settings(state, quiet=True)
            elif choice == "4":
                configure_output(state); save_settings(state, quiet=True)
            elif choice == "5":
                reset_archive_name_from_version(state); save_settings(state, quiet=True)
            elif choice == "6":
                configure_name(state); save_settings(state, quiet=True)
            elif choice == "7":
                _ = settings_submenu(state, ui_mode)
                ui_mode = normalize_ui_mode(state.ui_mode or ui_mode)
                if ui_mode == "auto": ui_mode = resolve_auto_ui_mode()
            elif choice == "8":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode): save_settings(state, quiet=True); continue
                if state.plan is None: rebuild_plan(state)
                print("\nTo zostanie użyte jako podstawa pakowania.")
                print_pack_plan_compact_summary(state)
                if ask_bool("Pokazać listę katalogów i plików przed pakowaniem", False, require_explicit=True):
                    print_pack_items_for_plan(state)
                if not ask_bool("Rozpocząć pakowanie", True, require_explicit=True):
                    continue
                assert state.source_folder is not None and state.out_dir is not None and state.plan is not None
                create_split_zip_from_plan(
                    source_folder=state.source_folder,
                    out_dir=state.out_dir,
                    archive_name=state.archive_name,
                    plan=state.plan,
                    part_size_mb=state.part_size_mb,
                    compression_level=state.compression_level,
                    force=state.force,
                    include_empty_dirs=state.include_empty_dirs,
                    exclude_patterns=state.effective_excludes(),
                    package_version=state.package_version,
                    package_release_name=state.package_release_name,
                    resolved_version_file=state.resolved_version_file,
                    archive_basename_requested=state.archive_basename_requested,
                    append_version_to_name=False,
                    disabled_default_excludes=state.disabled_default_excludes,
                    pack_profile=state.pack_profile,
                    include_prefixes=state.include_prefixes(),
                )
                save_settings(state, quiet=True); return 0
            elif choice == "0":
                exit_action = exit_menu(ui_mode)
                if exit_action == "save": save_settings(state, quiet=False); print("Zakończono bez pakowania."); return 0
                if exit_action == "nosave": restore_settings_file(settings_snapshot); print("Zakończono bez zapisywania zmian."); return 0
                continue
            else:
                print("Nieznana opcja.")
        except UserRequestedAppExit:
            restore_settings_file(settings_snapshot); print("\nZamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
        except KeyboardInterrupt:
            restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. W trybie kursorowym skrótem zamknięcia aplikacji jest Ctrl+X. Zakończono bez zapisywania zmian."); return 130
        except EOFError:
            restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Zamknięto bez zapisywania zmian."); return 130
        except Exception as exc:
            save_settings(state, quiet=True); print(f"BŁĄD: {exc}")
            try: pause()
            except KeyboardInterrupt:
                restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. Zakończono bez zapisywania zmian."); return 130
            except EOFError:
                restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Zamknięto bez zapisywania zmian."); return 130


# =============================================================================
# NADPISANIA UI v5.18 — cyan w podglądzie list i JSON w menu głównym
# =============================================================================

VERSION = "1.6.INTEGRITY-MANIFEST-GATE"

_WINDOWS_VT_READY: bool | None = None


def _enable_windows_virtual_terminal() -> bool:
    """Włącza obsługę sekwencji ANSI/VT w Windows Console/PowerShell, jeśli można."""
    global _WINDOWS_VT_READY
    if _WINDOWS_VT_READY is not None:
        return bool(_WINDOWS_VT_READY)
    if os.name != "nt":
        _WINDOWS_VT_READY = True
        return True
    try:
        import ctypes  # type: ignore
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if handle in (0, -1):
            _WINDOWS_VT_READY = False
            return False
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            _WINDOWS_VT_READY = False
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            _WINDOWS_VT_READY = True
            return True
        new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        _WINDOWS_VT_READY = bool(kernel32.SetConsoleMode(handle, new_mode))
        return bool(_WINDOWS_VT_READY)
    except Exception:
        _WINDOWS_VT_READY = False
        return False


def _cyan_enabled() -> bool:
    """Kolor cyan dla separatorów w terminalu, także w PowerShell/Windows Console."""
    try:
        if os.environ.get("NO_COLOR"):
            return False
        if not sys.stdout.isatty():
            return False
        if os.name == "nt":
            # Spróbuj włączyć VT; nawet gdy terminal już umie ANSI, ta funkcja
            # nie szkodzi, a usuwa przypadek „PowerShell widzi zwykły tekst”.
            _enable_windows_virtual_terminal()
            return True
        return True
    except Exception:
        return False


def _ansi_cyan_text(text: str) -> str:
    return "\033[36m" + text + "\033[0m" if _cyan_enabled() else text


def _cyan_line_text(width: int | None = None, char: str = "=") -> str:
    return _ansi_cyan_text(str(char) * int(width or _terminal_ui_width()))


def _main_menu_separator_before(key: str) -> bool:
    # 1-3 / 4-7 / 8 / T-Z / 9-0. Nie rozdzielamy 9 i 0, bo pakowanie/wyjście są na dole.
    return str(key).lower() in {"4", "8", "t", "9"}


def _inline_edit_field_for_key(key: str) -> str:
    return {"4": "source", "5": "output", "7": "name"}.get(str(key), "")


def default_menu_choice(state: WizardState) -> str:
    if state.source_folder is None:
        return "4"
    if state.out_dir is None:
        return "5"
    if not state.archive_name:
        return "7"
    if state.plan is not None:
        return "9"
    return "2"


def menu_options(state: WizardState) -> list[tuple[str, str]]:
    name_label = _zip_menu_display_name(state.archive_name) or "nie ustawiono"
    return [
        ("1", f"1. Profil pakowania [{profile_menu_label(state)}]"),
        ("2", f"2. Pokaż listę do spakowania [{pack_list_settings_label(state)}]"),
        ("3", "3. Zapisz pełny podgląd listy pakowania do JSON"),
        ("4", f"4. Folder do pakowania [{menu_value(state.source_folder)}]"),
        ("5", f"5. Folder zapisu paczki [{menu_value(state.out_dir)}]"),
        ("6", "6. Odśwież nazwę paczki z aktualnej wersji"),
        ("7", f"7. Zmień nazwę paczki [{name_label}]"),
        ("8", "8. Ustawienia"),
        ("t", "T. Testuj gotową paczkę"),
        ("z", "Z. Połącz części w jeden ZIP"),
        ("9", "9. Pakuj teraz"),
        ("0", "0. Wyjście"),
    ]


def _cursor_menu_lines(state: WizardState, selected_index: int, inline_editor: dict[str, object] | None = None) -> list[tuple[str, str]]:
    options = menu_options(state)
    selected_index = max(0, min(selected_index, len(options) - 1))
    width = _terminal_ui_width(fallback=100)
    inline_editor = inline_editor or {}
    editing_field = str(inline_editor.get("field") or "")
    editing_text = str(inline_editor.get("text") or "")
    editing_cursor = as_int(inline_editor.get("cursor"), len(editing_text))
    message = str(inline_editor.get("message") or "")
    fragments: list[tuple[str, str]] = []

    def line(style: str, value: str = "", *, subsequent_indent: str = "") -> None:
        for piece in _wrap_text_lines(value, width, subsequent_indent=subsequent_indent):
            fragments.append((style, piece))
            fragments.append(("", "\n"))

    line("class:border", "=" * width)
    line("class:title", f"  Jaźń / Łatka — generator paczki ZIP v{VERSION}")
    line("class:border", "=" * width)
    line("", "")
    for label, value in (
        ("Plan:     ", plan_status_label(state)),
        ("Profil:   ", state.profile_label()),
        ("Źródło:   ", menu_value(state.source_folder)),
        ("Zapis:    ", menu_value(state.out_dir)),
        ("ZIP:      ", state.archive_name or "(nie ustawiono)"),
    ):
        line("class:status.value", f"{label}{value}", subsequent_indent=" " * len(label))
    line("", "")
    line("class:border", "=" * width)
    for idx, (key, label) in enumerate(options):
        if _main_menu_separator_before(key):
            line("class:separator", _menu_separator_text(width))
        field = _inline_edit_field_for_key(key)
        marker = "▶" if idx == selected_index else " "
        selected_style = "class:latka.selected" if idx == selected_index else "class:latka.option"
        if editing_field and field == editing_field:
            edited = _text_with_cursor_marker(editing_text, editing_cursor)
            row_start = f"  {marker} "
            if key == "4":
                parts = [("class:latka.option", row_start + "4. Folder do pakowania ["), ("class:editing", edited), ("class:latka.option", "]")]
            elif key == "5":
                parts = [("class:latka.option", row_start + "5. Folder zapisu paczki ["), ("class:editing", edited), ("class:latka.option", "]")]
            elif key == "7":
                parts = [("class:latka.option", row_start + "7. Zmień nazwę paczki ["), ("class:editing", edited), ("class:latka.option", "]")]
            else:
                parts = [("class:latka.option", row_start + label)]
            _append_wrapped_fragments(fragments, parts, width)
        else:
            line(selected_style, f"  {marker} {label}", subsequent_indent="    ")
    line("class:border", "=" * width)
    if editing_field:
        line("class:hint", "Edycja pola []: wpisuj tekst | Tab autouzupełnij ścieżkę | Enter zapisz | Esc anuluj | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
    else:
        line("class:hint", "↑/↓ wybór | Enter OK | Esc/Q wyjście | Ctrl+X zamknij bez zapisu", subsequent_indent="  ")
    if message:
        line("class:message", message, subsequent_indent="  ")
    return fragments


def _settings_rows(state: WizardState, ui_mode: str) -> list[tuple[str, str, str, str]]:
    return [
        ("item", "1", f"Profil pakowania [{state.profile_label()}]", ""),
        _cursor_separator_row(),
        ("item", "2", f"Ustawienia paczki [{pack_settings_menu_label(state)}]", ""),
        ("item", "3", f"Ustawienia wykluczeń [{exclusions_menu_label(state)}]", ""),
        _cursor_separator_row(),
        ("item", "4", f"Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]", ""),
        _cursor_separator_row(),
        ("item", "0", "Wróć", ""),
    ]


def _settings_cursor_choice(state: WizardState, ui_mode: str) -> str | None:
    return _option_rows_cursor_app(title="Ustawienia", rows=_settings_rows(state, ui_mode), default_key="0")


def _print_settings_menu_plain(state: WizardState, ui_mode: str) -> None:
    section("Ustawienia")
    width = _terminal_ui_width(fallback=78)
    _print_wrapped_plain(f"1. Profil pakowania [{state.profile_label()}]", indent="  ", width=width)
    _print_plain_separator("    ")
    _print_wrapped_plain(f"2. Ustawienia paczki [{pack_settings_menu_label(state)}]", indent="  ", width=width)
    _print_wrapped_plain(f"3. Ustawienia wykluczeń [{exclusions_menu_label(state)}]", indent="  ", width=width)
    _print_plain_separator("    ")
    _print_wrapped_plain(f"4. Zmień interfejs TXT/Kursorowy [{ui_mode_setting_label(state, ui_mode)}]", indent="  ", width=width)
    _print_plain_separator("    ")
    _print_wrapped_plain("0. Wróć", indent="  ", width=width)


def settings_submenu(state: WizardState, ui_mode: str = "plain") -> str:
    """Ustawienia bez opcji JSON — JSON jest teraz w menu głównym pod podglądem listy."""
    while True:
        if should_use_cursor_menu(ui_mode):
            choice = _settings_cursor_choice(state, ui_mode)
            if choice in {None, "0"}:
                return "cancel"
        else:
            _print_settings_menu_plain(state, ui_mode)
            choice = ask_text("Wybór", "0").strip()
            if choice == "0":
                return "cancel"

        normalized = str(choice).strip()
        if normalized == "1":
            configure_profile(state, ui_mode); save_settings(state, quiet=True)
        elif normalized == "2":
            configure_pack_settings(state, ui_mode); save_settings(state, quiet=True)
        elif normalized == "3":
            exclusion_menu(state, ui_mode); save_settings(state, quiet=True)
        elif normalized == "4":
            ui_mode = configure_ui_mode_preference(state, ui_mode); save_settings(state, quiet=True)
        else:
            print("Nieznana opcja.")


def print_lines_paged(title: str, lines: list[str], *, page_size: int | None = None) -> None:
    """Pager tekstowy: wszystkie linie `====` w nagłówkach są cyan, także przy liście plików."""
    def header(label: str) -> None:
        width = _terminal_ui_width(fallback=78)
        print("\n" + _cyan_line_text(width))
        print(f"  {label}:")
        print(_cyan_line_text(width))

    if not lines:
        header(title)
        print("  (brak)")
        print("(END)")
        return

    page_size_value = page_size or terminal_page_size()
    page_size_value = max(1, int(page_size_value))
    total_pages = (len(lines) + page_size_value - 1) // page_size_value

    def print_wrapped_items(page_lines: list[str]) -> None:
        width = _terminal_ui_width(fallback=120)
        for line_value in page_lines:
            for wrapped in _wrap_text_lines(line_value, width, subsequent_indent="      "):
                print(wrapped)

    if total_pages <= 1 or not sys.stdin.isatty():
        header(title)
        print_wrapped_items(lines)
        print("(END)")
        return

    page = 0
    while True:
        start = page * page_size_value
        end = min(start + page_size_value, len(lines))
        header(f"{title} — strona {page + 1}/{total_pages} ({start + 1}-{end} z {len(lines)})")
        print_wrapped_items(lines[start:end])
        if page >= total_pages - 1:
            print("(END)")
            return
        try:
            choice = input(": ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in {"q", "0", "k", "koniec", "esc", "w", "wroc", "wróć"}:
            print("(END)")
            return
        if choice in {"p", "poprzednia", "prev", "b"}:
            page = max(0, page - 1)
            continue
        page += 1


def show_pack_list_and_offer_json(state: WizardState, ui_mode: str = "plain") -> None:
    """Opcja 2: tylko pokazuje aktualną listę. Zapis JSON jest osobną opcją 3."""
    if state.plan is None:
        rebuild_plan(state)
    else:
        section("Lista do spakowania z ustawieniami")
        print_pack_plan_summary(state)


def save_pack_preview_from_main_menu(state: WizardState, ui_mode: str = "plain") -> None:
    """Opcja 3 menu głównego: zapisuje pełny podgląd tej samej listy do JSON."""
    if state.plan is None:
        rebuild_plan(state)
    preview = save_preview_json(state)
    save_settings(state, quiet=True)
    print(f"Zapisano podgląd: {preview}")
    pause()



# =============================================================================
# PAKIET DWUCZĘŚCIOWY v1.3 — system i pamięć jako osobne dzielone ZIP-y
# =============================================================================

DUAL_PACKAGE_PROFILE = "pelna"
DUAL_PACKAGE_COMPONENTS = ("system", "memory")
DUAL_SYSTEM_REQUIRED_EXCLUDES = (
    "/memory/",
    "/workspace_runtime/",
    "RUNTIME_STATE.json",
    "ACTIVE_RUNTIME_CACHE_CONTRACT.json",
    "BOOTSTRAP_JAZN_CURRENT.json",
)


def _dedupe_patterns(patterns: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in patterns:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def archive_name_with_component(base_archive_name: str, component: str) -> str:
    """Dodaje `_system` albo `_memory` bezpośrednio przed `.zip`."""
    component = str(component or "").strip().lower()
    if component not in DUAL_PACKAGE_COMPONENTS:
        raise ValueError(f"Nieznany komponent paczki: {component!r}")
    normalized = sanitize_zip_name(base_archive_name)
    stem = normalized[:-4]
    for suffix in ("_system", "_memory"):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return sanitize_zip_name(f"{stem}_{component}.zip")


def dual_archive_names(base_archive_name: str) -> dict[str, str]:
    return {
        component: archive_name_with_component(base_archive_name, component)
        for component in DUAL_PACKAGE_COMPONENTS
    }


def package_set_stem(base_archive_name: str) -> str:
    normalized = sanitize_zip_name(base_archive_name)
    stem = normalized[:-4]
    for suffix in ("_system", "_memory"):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return stem


def _component_excludes(base_excludes: Iterable[str], component: str) -> list[str]:
    excludes = list(base_excludes)
    if component == "system":
        excludes.extend(DUAL_SYSTEM_REQUIRED_EXCLUDES)
    return _dedupe_patterns(excludes)


def _merge_disjoint_plans(system_plan: PackPlan, memory_plan: PackPlan) -> PackPlan:
    system_files = set(system_plan.files)
    memory_files = set(memory_plan.files)
    overlap = system_files & memory_files
    if overlap:
        sample = ", ".join(str(path) for path in sorted(overlap)[:5])
        raise ValueError(f"Plany systemu i pamięci nakładają się: {sample}")
    files = sorted(system_files | memory_files, key=lambda p: p.as_posix().lower())
    dirs = sorted(set(system_plan.dirs) | set(memory_plan.dirs), key=lambda p: p.as_posix().lower())
    excluded = sorted(
        set(system_plan.excluded) | set(memory_plan.excluded),
        key=lambda item: (item[0].lower(), item[1]),
    )
    total_size = 0
    for file_path in files:
        try:
            total_size += file_path.stat().st_size
        except OSError:
            pass
    return PackPlan(
        files=files,
        dirs=dirs,
        source_total_size=total_size,
        excluded=excluded,
    )


def discover_dual_package_plans(state: WizardState) -> dict[str, PackPlan]:
    require_ready_state(state)
    assert state.source_folder is not None
    source = state.source_folder.resolve()
    memory_root = source / "memory"
    if not memory_root.exists() or not memory_root.is_dir():
        raise FileNotFoundError(
            "Profil „system + pamięć” wymaga katalogu memory/ w folderze źródłowym: "
            f"{memory_root}"
        )

    base_excludes = state.effective_excludes()
    subsection("Plan paczki systemowej")
    system_plan = discover_pack_plan(
        source,
        state.include_empty_dirs,
        _component_excludes(base_excludes, "system"),
        [],
    )
    subsection("Plan paczki pamięci")
    memory_plan = discover_pack_plan(
        source,
        state.include_empty_dirs,
        _component_excludes(base_excludes, "memory"),
        ["memory/"],
    )
    plans = {"system": system_plan, "memory": memory_plan}
    state.component_plans = plans
    state.plan = _merge_disjoint_plans(system_plan, memory_plan)
    return plans


def _existing_outputs_for_archive(out_dir: Path, archive_name: str) -> list[Path]:
    archive_name = sanitize_zip_name(archive_name)
    return _windows_zip_output_paths(out_dir, archive_name)


def _preflight_dual_outputs(
    out_dir: Path,
    names: dict[str, str],
    *,
    force: bool,
    set_stem: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing: list[Path] = []
    for name in names.values():
        existing.extend(_existing_outputs_for_archive(out_dir, name))
    for extra_name in (
        f"{set_stem}.package_set.manifest.json",
        f"{set_stem}.extract_all.py",
    ):
        path = out_dir / extra_name
        if path.exists() and path.is_file():
            existing.append(path)
    existing = sorted(set(existing))
    if existing and not force:
        sample = "\n".join(f"  - {path}" for path in existing[:30])
        more = "" if len(existing) <= 30 else f"\n  ... oraz {len(existing) - 30} więcej"
        raise FileExistsError(
            "Znaleziono wcześniejsze pliki co najmniej jednej paczki dwuczęściowej. "
            "Włącz nadpisywanie albo zmień nazwę/folder wyjściowy.\n"
            + sample
            + more
        )
    if force:
        for path in existing:
            path.unlink()


def write_dual_extract_all_script(
    out_dir: Path,
    *,
    set_stem: str,
    system_archive_name: str,
    memory_archive_name: str,
) -> Path:
    helper_path = out_dir / f"{set_stem}.extract_all.py"
    template = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Rozpakowuje dwuczęściową paczkę Jaźni w kolejności: system, następnie pamięć.
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SYSTEM_ARCHIVE = __SYSTEM_ARCHIVE__
MEMORY_ARCHIVE = __MEMORY_ARCHIVE__


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rozpakuj system i pamięć Jaźni do jednego katalogu runtime."
    )
    parser.add_argument("--parts-dir", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--destination", default="/mnt/data/jazn_runtime_current")
    parser.add_argument("--skip-part-hash", action="store_true")
    parser.add_argument("--skip-testzip", action="store_true")
    args = parser.parse_args()

    parts_dir = Path(args.parts_dir).expanduser().resolve()
    common = ["--parts-dir", str(parts_dir), "--destination", args.destination]
    if args.skip_part_hash:
        common.append("--skip-part-hash")
    if args.skip_testzip:
        common.append("--skip-testzip")

    system_helper = parts_dir / f"{SYSTEM_ARCHIVE}.extract_here.py"
    memory_helper = parts_dir / f"{MEMORY_ARCHIVE}.extract_here.py"
    for helper in (system_helper, memory_helper):
        if not helper.exists():
            raise SystemExit(f"Brak helpera: {helper}")

    print("[1/2] Walidacja i rozpakowanie systemu...")
    subprocess.check_call(
        [sys.executable, "-X", "utf8", str(system_helper), *common, "--clean"]
    )
    print("[2/2] Walidacja i dołączenie pamięci...")
    subprocess.check_call(
        [sys.executable, "-X", "utf8", str(memory_helper), *common, "--force"]
    )
    print(
        "Gotowe. System i pamięć są w: "
        f"{Path(args.destination).expanduser().resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    content = (
        template
        .replace("__SYSTEM_ARCHIVE__", repr(system_archive_name))
        .replace("__MEMORY_ARCHIVE__", repr(memory_archive_name))
    )
    helper_path.write_text(content, encoding="utf-8")
    return helper_path


def write_dual_package_set_manifest(
    out_dir: Path,
    *,
    base_archive_name: str,
    package_version: str,
    package_release_name: str,
    manifests: dict[str, dict[str, object]],
) -> Path:
    names = dual_archive_names(base_archive_name)
    stem = package_set_stem(base_archive_name)
    extract_all = write_dual_extract_all_script(
        out_dir,
        set_stem=stem,
        system_archive_name=names["system"],
        memory_archive_name=names["memory"],
    )
    path = out_dir / f"{stem}.package_set.manifest.json"
    data = {
        "schema_version": "jazn_dual_package_set/v1",
        "created_at": now_iso(),
        "script": Path(__file__).name,
        "script_version": f"v{VERSION}",
        "package_version": package_version,
        "package_release_name": package_release_name,
        "base_archive_name": sanitize_zip_name(base_archive_name),
        "package_mode": "system_and_memory_as_separate_split_zip_archives",
        "extraction_order": ["system", "memory"],
        "recommended_destination": "/mnt/data/jazn_runtime_current",
        "extract_all_script": extract_all.name,
        "packages": [
            {
                "component": component,
                "archive_name_after_join": names[component],
                "manifest_file": f"{names[component]}.manifest.json",
                "parts_count": manifests[component].get("parts_count"),
                "logical_full_zip_size_bytes": manifests[component].get(
                    "logical_full_zip_size_bytes"
                ),
                "logical_full_zip_sha256": manifests[component].get(
                    "logical_full_zip_sha256"
                ),
                "extract_here_script": f"{names[component]}.extract_here.py",
            }
            for component in DUAL_PACKAGE_COMPONENTS
        ],
    }
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def create_dual_split_packages(
    *,
    source_folder: Path,
    out_dir: Path,
    base_archive_name: str,
    plans: dict[str, PackPlan],
    part_size_mb: int,
    compression_level: int,
    force: bool,
    include_empty_dirs: bool,
    base_exclude_patterns: list[str],
    package_version: str,
    package_release_name: str,
    resolved_version_file: Path | None,
    archive_basename_requested: str,
    append_version_to_name: bool,
    disabled_default_excludes: list[str] | None = None,
    artifact_mode: str = DEFAULT_ARTIFACT_MODE,
    verify_after_pack: bool = VERIFY_AFTER_PACK,
    verify_crc: bool = VERIFY_CRC_AFTER_PACK,
) -> dict[str, object]:
    source_folder = source_folder.resolve()
    out_dir = out_dir.resolve()
    artifact_mode = normalize_artifact_mode(artifact_mode)
    names = dual_archive_names(base_archive_name)
    stem = package_set_stem(base_archive_name)
    _preflight_dual_outputs(out_dir, names, force=force, set_stem=stem)

    manifests: dict[str, dict[str, object]] = {}
    try:
        section("Paczka 1/2 — SYSTEM")
        manifests["system"] = create_split_zip_from_plan(
            source_folder=source_folder,
            out_dir=out_dir,
            archive_name=names["system"],
            plan=plans["system"],
            part_size_mb=part_size_mb,
            compression_level=compression_level,
            force=force,
            include_empty_dirs=include_empty_dirs,
            exclude_patterns=_component_excludes(base_exclude_patterns, "system"),
            package_version=package_version,
            package_release_name=package_release_name,
            resolved_version_file=resolved_version_file,
            archive_basename_requested=archive_basename_requested,
            append_version_to_name=append_version_to_name,
            disabled_default_excludes=disabled_default_excludes,
            pack_profile="system",
            include_prefixes=[],
            artifact_mode=artifact_mode,
            verify_after_pack=verify_after_pack,
            verify_crc=verify_crc,
        )

        section("Paczka 2/2 — MEMORY")
        manifests["memory"] = create_split_zip_from_plan(
            source_folder=source_folder,
            out_dir=out_dir,
            archive_name=names["memory"],
            plan=plans["memory"],
            part_size_mb=part_size_mb,
            compression_level=compression_level,
            force=force,
            include_empty_dirs=include_empty_dirs,
            exclude_patterns=_component_excludes(base_exclude_patterns, "memory"),
            package_version=package_version,
            package_release_name=package_release_name,
            resolved_version_file=resolved_version_file,
            archive_basename_requested=archive_basename_requested,
            append_version_to_name=append_version_to_name,
            disabled_default_excludes=disabled_default_excludes,
            pack_profile="memory",
            include_prefixes=["memory/"],
            artifact_mode=artifact_mode,
            verify_after_pack=verify_after_pack,
            verify_crc=verify_crc,
        )

    except BaseException:
        for archive in names.values():
            cleanup_archive_outputs(out_dir, archive)
        for extra_name in (
            f"{stem}.package_set.manifest.json",
            f"{stem}.extract_all.py",
        ):
            try:
                (out_dir / extra_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise

    package_set_manifest: Path | None = None
    if artifact_mode == "diagnostic":
        package_set_manifest = write_dual_package_set_manifest(
            out_dir,
            base_archive_name=base_archive_name,
            package_version=package_version,
            package_release_name=package_release_name,
            manifests=manifests,
        )
    section("Gotowy pakiet dwuczęściowy")
    print(
        f"System:                  {names['system']} "
        f"({manifests['system'].get('parts_count')} ZIP)"
    )
    print(
        f"Pamięć:                  {names['memory']} "
        f"({manifests['memory'].get('parts_count')} ZIP)"
    )
    if package_set_manifest is not None:
        print(f"Manifest zestawu:        {package_set_manifest}")
        print(f"Rozpakowanie obu paczek: {out_dir / (stem + '.extract_all.py')}")
    else:
        print("Pliki dodatkowe:         nie utworzono — tylko zwykłe ZIP-y systemu i pamięci")
    return {
        "package_mode": "dual",
        "base_archive_name": sanitize_zip_name(base_archive_name),
        "archive_names": names,
        "package_set_manifest": str(package_set_manifest) if package_set_manifest else None,
        "artifact_mode": artifact_mode,
        "manifests": manifests,
    }


def create_packages_for_state(state: WizardState) -> dict[str, object]:
    require_ready_state(state)
    assert state.source_folder is not None
    assert state.out_dir is not None

    if state.pack_profile == DUAL_PACKAGE_PROFILE:
        if state.plan is None or set(state.component_plans) != set(DUAL_PACKAGE_COMPONENTS):
            discover_dual_package_plans(state)
        return create_dual_split_packages(
            source_folder=state.source_folder,
            out_dir=state.out_dir,
            base_archive_name=state.archive_name,
            plans=state.component_plans,
            part_size_mb=state.part_size_mb,
            compression_level=state.compression_level,
            force=state.force,
            include_empty_dirs=state.include_empty_dirs,
            base_exclude_patterns=state.effective_excludes(),
            package_version=state.package_version,
            package_release_name=state.package_release_name,
            resolved_version_file=state.resolved_version_file,
            archive_basename_requested=state.archive_basename_requested,
            append_version_to_name=False,
            disabled_default_excludes=state.disabled_default_excludes,
        )

    if state.plan is None:
        rebuild_plan_quiet_for_pack(state)
    assert state.plan is not None
    return create_split_zip_from_plan(
        source_folder=state.source_folder,
        out_dir=state.out_dir,
        archive_name=state.archive_name,
        plan=state.plan,
        part_size_mb=state.part_size_mb,
        compression_level=state.compression_level,
        force=state.force,
        include_empty_dirs=state.include_empty_dirs,
        exclude_patterns=state.effective_excludes(),
        package_version=state.package_version,
        package_release_name=state.package_release_name,
        resolved_version_file=state.resolved_version_file,
        archive_basename_requested=state.archive_basename_requested,
        append_version_to_name=False,
        disabled_default_excludes=state.disabled_default_excludes,
        pack_profile=state.pack_profile,
        include_prefixes=state.include_prefixes(),
    )


def rebuild_plan(state: WizardState) -> PackPlan:
    require_ready_state(state)
    section("4. Informacje co będzie spakowane — podstawa pakowania")
    if state.pack_profile == DUAL_PACKAGE_PROFILE:
        plans = discover_dual_package_plans(state)
        names = dual_archive_names(state.archive_name)
        print("\nProfil utworzy dwa oddzielne zestawy zwykłych ZIP-ów:")
        print(
            f"  SYSTEM: {names['system']} — {plans['system'].file_count} plików / "
            f"{human_size(plans['system'].source_total_size)}"
        )
        print(
            f"  MEMORY: {names['memory']} — {plans['memory'].file_count} plików / "
            f"{human_size(plans['memory'].source_total_size)}"
        )
    else:
        assert state.source_folder is not None
        state.component_plans.clear()
        state.plan = discover_pack_plan(
            state.source_folder,
            state.include_empty_dirs,
            state.effective_excludes(),
            state.include_prefixes(),
        )
    assert state.plan is not None
    print_pack_plan_summary(state)
    return state.plan


def save_preview_json(state: WizardState) -> Path:
    require_ready_state(state)
    if state.plan is None or (
        state.pack_profile == DUAL_PACKAGE_PROFILE
        and set(state.component_plans) != set(DUAL_PACKAGE_COMPONENTS)
    ):
        rebuild_plan(state)
    assert state.plan is not None
    assert state.source_folder is not None
    assert state.out_dir is not None

    out_dir = state.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    preview_path = out_dir / f"{state.archive_name}.pack_preview.json"
    data: dict[str, object] = {
        "created_at": now_iso(),
        "script": Path(__file__).name,
        "script_version": VERSION,
        "source_folder": str(state.source_folder.resolve()),
        "output_dir": str(out_dir),
        "base_archive_name": state.archive_name,
        "package_version": state.package_version,
        "package_release_name": state.package_release_name,
        "version_file": (
            str(state.resolved_version_file) if state.resolved_version_file else None
        ),
        "part_size_mb": state.part_size_mb,
        "compression_level": state.compression_level,
        "include_empty_dirs": state.include_empty_dirs,
        "force": state.force,
        "pack_profile": state.pack_profile,
        "pack_profile_label": state.profile_label(),
        "exclude_patterns": state.effective_excludes(),
        "disabled_default_exclude_patterns": state.disabled_default_excludes,
        "custom_exclude_patterns": state.custom_excludes,
        "source_file_count": state.plan.file_count,
        "source_dir_count": state.plan.dir_count,
        "source_total_size_bytes": state.plan.source_total_size,
        "excluded_count": len(state.plan.excluded),
        "files": [
            rel_posix(path, state.source_folder.resolve())
            for path in state.plan.files
        ],
        "dirs": [
            rel_posix(path, state.source_folder.resolve()) + "/"
            for path in state.plan.dirs
        ],
        "excluded_sample": [
            {"path": rel, "pattern": pattern}
            for rel, pattern in state.plan.excluded[:1000]
        ],
    }

    if state.pack_profile == DUAL_PACKAGE_PROFILE:
        names = dual_archive_names(state.archive_name)
        data["package_mode"] = "dual"
        data["extraction_order"] = ["system", "memory"]
        data["archive_names_after_join"] = names
        data["components"] = {
            component: {
                "archive_name_after_join": names[component],
                "source_file_count": state.component_plans[component].file_count,
                "source_dir_count": state.component_plans[component].dir_count,
                "source_total_size_bytes": (
                    state.component_plans[component].source_total_size
                ),
                "files": [
                    rel_posix(path, state.source_folder.resolve())
                    for path in state.component_plans[component].files
                ],
                "dirs": [
                    rel_posix(path, state.source_folder.resolve()) + "/"
                    for path in state.component_plans[component].dirs
                ],
            }
            for component in DUAL_PACKAGE_COMPONENTS
        }
    else:
        data["package_mode"] = "single"
        data["archive_name_after_join"] = state.archive_name
        data["include_prefixes"] = state.include_prefixes()

    preview_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return preview_path


def print_pack_plan_compact_summary(state: WizardState) -> None:
    require_ready_state(state)
    if state.plan is None:
        print("Brak aktualnego planu. Wybierz opcję podglądu, żeby przeskanować źródło.")
        return
    assert state.source_folder is not None
    assert state.out_dir is not None

    print("\nPodstawa pakowania została wyliczona z aktualnych ustawień.")
    print(f"Źródło: {state.source_folder}")
    print(f"Wyjście: {state.out_dir}")
    print(f"Profil: {state.profile_label()}")
    if state.pack_profile == DUAL_PACKAGE_PROFILE:
        names = dual_archive_names(state.archive_name)
        print(f"ZIP systemu: {names['system']}")
        print(f"ZIP pamięci: {names['memory']}")
        for component, label in (("system", "System"), ("memory", "Pamięć")):
            plan = state.component_plans.get(component)
            if plan is not None:
                print(
                    f"{label}: {plan.file_count} plików / "
                    f"{human_size(plan.source_total_size)}"
                )
    else:
        print(f"Nazwa ZIP: {state.archive_name}")
        print(f"Pliki do spakowania: {state.plan.file_count}")
        print(f"Rozmiar źródłowy: {human_size(state.plan.source_total_size)}")
    print(
        f"Część ZIP: {state.part_size_mb} MiB; "
        f"kompresja: {state.compression_level}; force: {state.force}"
    )


def _current_package_archive_names(state: WizardState) -> list[str]:
    if state.pack_profile == DUAL_PACKAGE_PROFILE:
        names = dual_archive_names(state.archive_name)
        return [names["system"], names["memory"]]
    return [sanitize_zip_name(state.archive_name)]


def join_current_package_from_menu(state: WizardState) -> None:
    if state.out_dir is None:
        raise ValueError("Najpierw ustaw folder zapisu paczki.")
    out_dir = state.out_dir.expanduser().resolve()
    results: list[str] = []
    for base_zip_name in _current_package_archive_names(state):
        subsection(f"Łączenie: {base_zip_name}")
        out_zip = out_dir / base_zip_name
        force = False
        keep_existing = False
        if out_zip.exists():
            print(f"Pełny ZIP już istnieje: {out_zip}")
            if ask_bool(
                "Użyć istniejącego ZIP-a bez ponownego sklejania",
                True,
                require_explicit=True,
            ):
                keep_existing = True
            else:
                force = ask_bool(
                    "Nadpisać pełny ZIP po ponownej walidacji części",
                    False,
                    require_explicit=True,
                )
                if not force:
                    print(f"Pominięto: {base_zip_name}")
                    continue
        results.append(
            str(
                join_split_package_to_zip(
                    out_dir,
                    base_zip_name,
                    force=force,
                    keep_existing=keep_existing,
                )
            )
        )
    section("Łączenie zakończone")
    for result in results:
        print(f"  - {result}")


def test_current_package_from_menu(state: WizardState) -> None:
    if state.out_dir is None:
        raise ValueError("Najpierw ustaw folder zapisu paczki.")
    out_dir = state.out_dir.expanduser().resolve()
    reports = []
    for base_zip_name in _current_package_archive_names(state):
        subsection(f"Test: {base_zip_name}")
        reports.append(
            test_split_package(
                out_dir,
                base_zip_name,
                join_if_missing=True,
                force_join=False,
                run_crc=True,
            )
        )
    section("Test paczki/paczek OK")
    print(json.dumps(reports, ensure_ascii=False, indent=2))


def create_dual_split_packages_from_args(
    *,
    source_folder: Path,
    out_dir: Path,
    archive_basename: str,
    part_size_mb: int,
    compression_level: int,
    force: bool,
    include_empty_dirs: bool,
    exclude_patterns: list[str],
    append_version_to_name: bool,
    version_file: str | Path | None,
    artifact_mode: str = DEFAULT_ARTIFACT_MODE,
    verify_after_pack: bool = VERIFY_AFTER_PACK,
    verify_crc: bool = VERIFY_CRC_AFTER_PACK,
) -> dict[str, object]:
    source_folder = source_folder.resolve()
    out_dir = out_dir.resolve()
    resolved_version_file = find_version_file(source_folder, version_file)
    package_version = read_version_from_py(resolved_version_file)
    package_release_name = normalize_release_name(
        read_optional_string_from_py(
            resolved_version_file,
            RELEASE_NAME_VARIABLES,
        )
        or PACKAGE_RELEASE_NAME
    )
    base_archive_name = apply_version_to_archive_name(
        archive_basename,
        package_version,
        package_release_name=package_release_name,
        enabled=append_version_to_name,
    )
    memory_root = source_folder / "memory"
    if not memory_root.exists() or not memory_root.is_dir():
        raise FileNotFoundError(f"Brak wymaganego katalogu pamięci: {memory_root}")

    plans = {
        "system": discover_pack_plan(
            source_folder,
            include_empty_dirs,
            _component_excludes(exclude_patterns, "system"),
            [],
        ),
        "memory": discover_pack_plan(
            source_folder,
            include_empty_dirs,
            _component_excludes(exclude_patterns, "memory"),
            ["memory/"],
        ),
    }
    return create_dual_split_packages(
        source_folder=source_folder,
        out_dir=out_dir,
        base_archive_name=base_archive_name,
        plans=plans,
        part_size_mb=part_size_mb,
        compression_level=compression_level,
        force=force,
        include_empty_dirs=include_empty_dirs,
        base_exclude_patterns=exclude_patterns,
        package_version=package_version,
        package_release_name=package_release_name,
        resolved_version_file=resolved_version_file,
        archive_basename_requested=archive_basename,
        append_version_to_name=append_version_to_name,
        disabled_default_excludes=[],
        artifact_mode=artifact_mode,
        verify_after_pack=verify_after_pack,
        verify_crc=verify_crc,
    )


def show_pack_list_and_offer_json(state: WizardState, ui_mode: str = "plain") -> None:
    """Pokazuje właściwy plan; dla profilu pełnego wymusza dwa plany komponentów."""
    needs_rebuild = state.plan is None or (
        state.pack_profile == DUAL_PACKAGE_PROFILE
        and set(state.component_plans) != set(DUAL_PACKAGE_COMPONENTS)
    )
    if needs_rebuild:
        rebuild_plan(state)
    else:
        section("Lista do spakowania z ustawieniami")
        print_pack_plan_summary(state)
    if state.out_dir is not None and ask_bool(
        "Zapisać pełny podgląd listy pakowania do JSON",
        False,
        require_explicit=True,
    ):
        preview = save_preview_json(state)
        print(f"Zapisano podgląd: {preview}")

def rebuild_plan_quiet_for_pack(state: WizardState) -> PackPlan:
    """Wylicza plan bez pełnej listy; profil domyślny buduje dwa komponenty."""
    require_ready_state(state)
    section("Przygotowanie podstawy pakowania")
    if state.pack_profile == DUAL_PACKAGE_PROFILE:
        discover_dual_package_plans(state)
    else:
        assert state.source_folder is not None
        state.component_plans.clear()
        state.plan = discover_pack_plan(
            state.source_folder,
            state.include_empty_dirs,
            state.effective_excludes(),
            state.include_prefixes(),
        )
    assert state.plan is not None
    return state.plan

def run_wizard(initial_source: str | None = None, *, ui_mode: str | None = None) -> int:
    settings_snapshot = snapshot_settings_file()
    state: WizardState | None = None
    try:
        activate_process_guard(prompt_user=True)
        state = initialize_state(initial_source)
        section(f"Jaźń / Łatka — generator paczki ZIP v{VERSION}")
        print_bar(100, 100, label="Ładowanie")
        if state.settings_needs_cleanup:
            save_settings(state, quiet=True)
            settings_snapshot = snapshot_settings_file()
        show_startup_warnings(state)
        ui_mode = resolve_ui_mode_with_optional_install(ui_mode, state)
        save_settings(state, quiet=True)
        settings_snapshot = snapshot_settings_file()
        prepare_plan_on_startup_if_possible(state)
    except UserRequestedAppExit:
        restore_settings_file(settings_snapshot); print("\nZamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
    except KeyboardInterrupt:
        restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. Start aplikacji został anulowany bez tracebacka."); return 130
    except EOFError:
        restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Start aplikacji został anulowany bez tracebacka."); return 130

    while True:
        try:
            assert state is not None
            if not should_use_cursor_menu(ui_mode):
                show_current_state(state)
                print(f"Tryb UI:                    {ui_mode_label(ui_mode)}; auto-start: {on_off_label(state.ui_auto_start)}")
            current_default_choice = default_menu_choice(state)
            choice = ask_menu_choice(state, current_default_choice, ui_mode)
            control_word = _plain_control_word(choice)
            if control_word == "exit": raise UserRequestedAppExit()
            if control_word == "cancel": continue

            known_menu_choices = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "t", "T", "z", "Z"}
            if choice not in known_menu_choices:
                if current_default_choice == "4" and state.source_folder is None:
                    if apply_source_path_text(state, choice): save_settings(state, quiet=True)
                    continue
                if current_default_choice == "5" and state.out_dir is None:
                    if apply_output_path_text(state, choice): save_settings(state, quiet=True)
                    continue

            if choice == APP_EXIT_MARKER:
                restore_settings_file(settings_snapshot); print("Zamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
            if choice == "1":
                configure_profile(state, ui_mode); save_settings(state, quiet=True)
            elif choice == "2":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode): save_settings(state, quiet=True); continue
                show_pack_list_and_offer_json(state, ui_mode)
                save_settings(state, quiet=True)
            elif choice == "3":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode): save_settings(state, quiet=True); continue
                save_pack_preview_from_main_menu(state, ui_mode)
            elif choice == "4":
                configure_source(state); save_settings(state, quiet=True)
            elif choice == "5":
                configure_output(state); save_settings(state, quiet=True)
            elif choice == "6":
                reset_archive_name_from_version(state); save_settings(state, quiet=True)
            elif choice == "7":
                configure_name(state); save_settings(state, quiet=True)
            elif choice == "8":
                _ = settings_submenu(state, ui_mode)
                ui_mode = normalize_ui_mode(state.ui_mode or ui_mode)
                if ui_mode == "auto": ui_mode = resolve_auto_ui_mode()
            elif choice.lower() == "t":
                if state.out_dir is None or not state.archive_name:
                    if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode):
                        save_settings(state, quiet=True); continue
                test_current_package_from_menu(state)
                pause()
            elif choice.lower() == "z":
                if state.out_dir is None or not state.archive_name:
                    if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode):
                        save_settings(state, quiet=True); continue
                join_current_package_from_menu(state)
                pause()
            elif choice == "9":
                if not ensure_ready_for_pack_plan(state, ui_mode=ui_mode): save_settings(state, quiet=True); continue
                # Nie używamy tutaj rebuild_plan(), bo ta funkcja drukuje pełną listę.
                # „Pakuj teraz” pokazuje tylko kompaktową podstawę, a pełną listę
                # dopiero po jawnej odpowiedzi T na poniższe pytanie.
                if state.plan is None: rebuild_plan_quiet_for_pack(state)
                print("\nTo zostanie użyte jako podstawa pakowania.")
                print_pack_plan_compact_summary(state)
                if ask_bool("Pokazać listę katalogów i plików przed pakowaniem", False, require_explicit=True):
                    print_pack_items_for_plan(state)
                if not ask_bool("Rozpocząć pakowanie", True, require_explicit=True):
                    # Po odmowie wróć do menu z kursorem na ostatniej pozycji: Pakuj teraz.
                    try:
                        _store_cursor_key("menu główne", "9", {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"})
                    except Exception:
                        pass
                    continue
                assert state.source_folder is not None and state.out_dir is not None and state.plan is not None
                create_packages_for_state(state)
                save_settings(state, quiet=True); return 0
            elif choice == "0":
                exit_action = exit_menu(ui_mode)
                if exit_action == "save": save_settings(state, quiet=False); print("Zakończono bez pakowania."); return 0
                if exit_action == "nosave": restore_settings_file(settings_snapshot); print("Zakończono bez zapisywania zmian."); return 0
                continue
            else:
                print("Nieznana opcja.")
        except UserRequestedAppExit:
            restore_settings_file(settings_snapshot); print("\nZamknięto skrótem Ctrl+X bez zapisywania zmian."); return 130
        except KeyboardInterrupt:
            restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. W trybie kursorowym skrótem zamknięcia aplikacji jest Ctrl+X. Zakończono bez zapisywania zmian."); return 130
        except EOFError:
            restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Zamknięto bez zapisywania zmian."); return 130
        except Exception as exc:
            save_settings(state, quiet=True); print(f"BŁĄD: {exc}")
            try: pause()
            except KeyboardInterrupt:
                restore_settings_file(settings_snapshot); print("\nPrzerwano przez Ctrl+C. Zakończono bez zapisywania zmian."); return 130
            except EOFError:
                restore_settings_file(settings_snapshot); print("\nWejście terminala zostało zamknięte. Zamknięto bez zapisywania zmian."); return 130

def parse_args(argv: list[str]) -> argparse.Namespace:
    help_text = """Jaźń / Łatka — generator paczki ZIP

Najprostsze uruchomienie aplikacji:
  py _jazn_pack_generator.py

Po pierwszym wyborze TXT/Kursorowy aplikacja zapisuje tryb i włącza auto-start:
przy następnym uruchomieniu użyje zapisanego trybu bez pytania. Auto-start można
wyłączyć w ustawieniach „Zmień interfejs TXT/Kursorowy”. Tryb tekstowy działa bez
dodatkowych bibliotek. Tryb kursorowy wymaga prompt_toolkit i daje menu ze strzałkami
oraz pola ścieżek z Tab/autouzupełnianiem.

Tryb prostego pakowania, zgodny z dawnym generate_Jazn_pack.py:
  py _jazn_pack_generator.py D:\\.AI\\jazn_latka_local --out D:\\Desktop\\pakiet --force

Test nowej paczki ZIP lub obsługa dawnych części .zip.001, .zip.002 itd.:
  py _jazn_pack_generator.py --parts-dir D:\\Desktop\\pakiet --zip-name jazn_latka_v14.8.6.6.2-runtime_events-in-jsonl-refreshed-ver.zip --test-package
  py _jazn_pack_generator.py --parts-dir D:\\Desktop\\pakiet --zip-name jazn_latka_v14.8.6.6.2-runtime_events-in-jsonl-refreshed-ver.zip --join-package

Domyślne propozycje ścieżek w aplikacji:
  folder do pakowania: folder generatora, np. D:\\.AI\\
  folder zapisu:       folder generatora\\pakiet\\, np. D:\\.AI\\pakiet\\

Pliki robocze tworzone obok aplikacji:
  __jazn_pack_generator_settings.json   zapamiętane ustawienia użytkownika
  __jazn_pack_generator.lock.json       tymczasowa blokada jednej instancji
"""
    parser = argparse.ArgumentParser(
        prog="py _jazn_pack_generator.py",
        description=help_text,
        formatter_class=argparse.RawTextHelpFormatter,
        usage="py _jazn_pack_generator.py [SOURCE] [options]",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Opcjonalny folder do spakowania. Jeśli podasz SOURCE, program działa w prostym trybie pakowania CLI.",
    )
    parser.add_argument(
        "--ui",
        choices=("tekstowy", "kursorowy"),
        default=None,
        help="Wymuś tryb interfejsu aplikacji interaktywnej: TXT albo kursorowy.",
    )
    parser.add_argument("--out", help="Folder wyjściowy dla prostego trybu pakowania CLI.")
    parser.add_argument("--name", help="Nazwa bazowa ZIP dla prostego trybu CLI, np. jazn_latka.")
    parser.add_argument("--profile", choices=tuple(PACK_PROFILES.keys()), default=DEFAULT_PACK_PROFILE, help="Profil CLI: pelna = osobne ZIP-y systemu i pamięci; system = sam system; memory = sama pamięć; full = system i pamięć razem w jednym ZIP-ie (jak 1.2_FINAL).")
    parser.add_argument("--version-file", default=VERSION_FILE or None, help="Ścieżka do version.py; domyślnie <SOURCE>\\latka_jazn\\version.py.")
    parser.add_argument("--no-version-suffix", action="store_true", help="Nie dopisuj automatycznie _v<wersja> do nazwy ZIP-a w trybie CLI.")
    parser.add_argument("--part-size-mb", type=int, default=PART_SIZE_MB, help="Rozmiar jednej części ZIP w MiB.")
    parser.add_argument("--compresslevel", type=int, default=COMPRESSION_LEVEL, choices=range(0, 10), metavar="0-9", help="Poziom kompresji ZIP_DEFLATED.")
    parser.add_argument("--force", action="store_true", default=FORCE_OVERWRITE, help="Nadpisz istniejące pliki wyjściowe w trybie CLI.")
    parser.add_argument("--diagnostic-files", action="store_true", help="Oprócz zwykłych ZIP-ów utwórz manifesty, pliki SHA256 oraz helper rozpakowania. Domyślnie powstają wyłącznie ZIP-y.")
    parser.add_argument("--skip-verify-after-pack", action="store_true", help="Pomiń automatyczną weryfikację SHA256 i otwarcie ZIP-a po pakowaniu. Niezalecane.")
    parser.add_argument("--skip-crc-after-pack", action="store_true", help="Po pakowaniu sprawdź strukturę ZIP, ale pomiń pełny test CRC wszystkich wpisów. Niezalecane.")
    parser.add_argument("--no-empty-dirs", action="store_true", help="Nie zapisuj pustych katalogów w trybie CLI.")
    parser.add_argument("--exclude", action="append", default=[], help="Dodatkowy wzorzec wykluczenia, np. docs/ albo *.log. Można podać wiele razy.")
    parser.add_argument("--no-default-excludes", action="store_true", help="Nie używaj domyślnych wykluczeń; zostaw tylko --exclude.")
    parser.add_argument("--parts-dir", help="Folder z częściami paczki do --test-package albo --join-package.")
    parser.add_argument("--zip-name", help="Nazwa pełnego ZIP-a dla --test-package/--join-package; gdy brak, skrypt spróbuje wykryć jedną paczkę w --parts-dir.")
    parser.add_argument("--zip-out", help="Opcjonalna ścieżka pełnego ZIP-a po sklejeniu. Domyślnie: <parts-dir>/<zip-name>.")
    parser.add_argument("--join-package", action="store_true", help="Połącz dawne binarne części .zip.001, .zip.002 itd.; nowe woluminy ZIP nie wymagają łączenia.")
    parser.add_argument("--test-package", action="store_true", help="Przetestuj paczkę: części, SHA256, central directory i CRC zipfile.testzip.")
    parser.add_argument("--skip-part-hash", action="store_true", help="Dla --test-package/--join-package pomiń SHA256 pojedynczych części.")
    parser.add_argument("--skip-crc", action="store_true", help="Dla --test-package pomiń pełny test CRC zipfile.testzip.")
    parser.add_argument("--force-join", action="store_true", help="Dla --join-package/--test-package nadpisz istniejący pełny ZIP, jeśli trzeba go skleić ponownie.")
    parser.add_argument("--pack", action="store_true", help="Wymuś prosty tryb pakowania CLI. Wymaga SOURCE.")
    parser.add_argument(
        "--reset-settings",
        action="store_true",
        help="Usuń plik ustawień aplikacji i zakończ.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Pokaż wersję generatora i zakończ.",
    )
    return parser.parse_args(argv)



def run_package_maintenance_from_args(args: argparse.Namespace) -> int:
    """CLI dla testowania i łączenia już wygenerowanej paczki."""
    raw_parts_dir = str(args.parts_dir or "").strip()
    if not raw_parts_dir:
        print("BŁĄD: --test-package/--join-package wymaga --parts-dir.", file=sys.stderr)
        return 2
    try:
        parts_dir = Path(normalize_path_text(raw_parts_dir)).expanduser().resolve()
        if not parts_dir.exists() or not parts_dir.is_dir():
            print(f"BŁĄD: folder części nie istnieje albo nie jest folderem: {parts_dir}", file=sys.stderr)
            return 2
        base_zip_name = infer_base_zip_name(parts_dir, args.zip_name)
        zip_out = Path(normalize_path_text(args.zip_out)).expanduser().resolve() if str(args.zip_out or "").strip() else None
        if args.join_package:
            out_path = join_split_package_to_zip(
                parts_dir,
                base_zip_name,
                zip_out=zip_out,
                skip_part_hash=bool(args.skip_part_hash),
                force=bool(args.force_join),
                keep_existing=False,
            )
            print(f"Gotowe: {out_path}")
        if args.test_package:
            report = test_split_package(
                parts_dir,
                base_zip_name,
                zip_out=zip_out,
                skip_part_hash=bool(args.skip_part_hash),
                join_if_missing=True,
                force_join=bool(args.force_join),
                run_crc=not bool(args.skip_crc),
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print("\nPrzerwano przez użytkownika.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        return 1

def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    args = parse_args(raw_argv)

    try:
        if args.version:
            print(f"_jazn_pack_generator.py v{VERSION}")
            return 0

        if args.reset_settings:
            removed = delete_settings_files()
            if removed:
                print("Usunięto ustawienia:")
                for path in removed:
                    print(f"  - {path}")
            else:
                print("Nie znaleziono pliku ustawień do usunięcia.")
            return 0

        if args.test_package or args.join_package:
            return run_package_maintenance_from_args(args)

        # Prosty tryb zgodny z dawnym generate_Jazn_pack.py.
        if args.source or args.pack:
            return run_direct_pack_from_args(args)

        return run_wizard(None, ui_mode=args.ui)
    finally:
        # Dodatkowe jawne sprzątanie locka, obok atexit.
        _release_process_lock()


if __name__ == "__main__":
    raise SystemExit(main())
