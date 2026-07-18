from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, TextIO
import os
import select
import sys

from latka_jazn.tools.chat_export_importer import ChatExportImporter
from latka_jazn.tools.chat_export_store import ChatExportArchiveStore
from latka_jazn.tools.chat_export_topics import ChatExportTopicStore
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("chat_export_cursor_ui")
DOMAIN_OPTIONS = (
    "development", "daily_life", "relationship", "health", "book",
    "creative_imagination", "music", "image", "video", "reading",
    "advice", "system_identity", "system", "unknown",
)


class KeySource(Protocol):
    def read_key(self) -> str: ...


class TerminalKeySource:
    """Cross-platform single-key reader with non-blocking ESC detection on POSIX."""

    def __init__(self, input_stream: TextIO | None = None) -> None:
        self.input = input_stream or sys.stdin
        self._fd: int | None = None
        self._old_termios = None

    def __enter__(self) -> "TerminalKeySource":
        if os.name != "nt":
            import termios
            import tty
            self._fd = self.input.fileno()
            self._old_termios = termios.tcgetattr(self._fd)
            tty.setraw(self._fd)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if os.name != "nt" and self._fd is not None and self._old_termios is not None:
            import termios
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_termios)

    def read_key(self) -> str:
        if os.name == "nt":
            import msvcrt
            key = msvcrt.getwch()
            if key in {"\x00", "\xe0"}:
                return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch(), "unknown")
            return {
                "\r": "enter", " ": "space", "\x1b": "escape", "\x18": "ctrl_x",
            }.get(key, f"text:{key}")

        first = self.input.read(1)
        if first == "\x18":
            return "ctrl_x"
        if first in {"\r", "\n"}:
            return "enter"
        if first == " ":
            return "space"
        if first != "\x1b":
            return f"text:{first}"
        fd = self._fd if self._fd is not None else self.input.fileno()
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            return "escape"
        second = self.input.read(1)
        if second != "[":
            return "escape"
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            return "escape"
        third = self.input.read(1)
        return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(third, "unknown")


@dataclass(slots=True)
class ScriptedKeySource:
    keys: list[str]
    position: int = 0

    def read_key(self) -> str:
        if self.position >= len(self.keys):
            return "escape"
        key = self.keys[self.position]
        self.position += 1
        return key


@dataclass(slots=True)
class CursorMenu:
    title: str
    options: list[str]
    multi: bool = False
    help_text: str = "↑/↓ wybór • Spacja zaznacz • Enter zatwierdź • Esc wróć • Ctrl+X zakończ"

    def choose(
        self,
        *,
        key_source: KeySource | None = None,
        output: TextIO | None = None,
        input_func: Callable[[str], str] = input,
    ) -> int | set[int] | None:
        output = output or sys.stdout
        if not self.options:
            return set() if self.multi else None
        if key_source is None and (not sys.stdin.isatty() or not output.isatty()):
            return self._plain(output=output, input_func=input_func)
        if key_source is not None:
            return self._interactive(key_source, output)
        with TerminalKeySource() as source:
            return self._interactive(source, output)

    def _interactive(self, source: KeySource, output: TextIO) -> int | set[int] | None:
        cursor = 0
        selected: set[int] = set()
        while True:
            self._render(cursor, selected, output)
            key = source.read_key()
            if key == "up":
                cursor = (cursor - 1) % len(self.options)
            elif key == "down":
                cursor = (cursor + 1) % len(self.options)
            elif key == "space" and self.multi:
                selected.symmetric_difference_update({cursor})
            elif key == "enter":
                return set(selected) if self.multi else cursor
            elif key == "escape":
                return None
            elif key == "ctrl_x":
                raise KeyboardInterrupt

    def _render(self, cursor: int, selected: set[int], output: TextIO) -> None:
        output.write("\x1b[2J\x1b[H")
        output.write(f"{self.title}\n{self.help_text}\n\n")
        for index, label in enumerate(self.options):
            pointer = "▶" if index == cursor else " "
            mark = ("[x]" if index in selected else "[ ]") if self.multi else ""
            output.write(f"{pointer} {mark} {label}\n")
        output.flush()

    def _plain(self, *, output: TextIO, input_func: Callable[[str], str]) -> int | set[int] | None:
        output.write(f"{self.title}\n")
        for index, option in enumerate(self.options, 1):
            output.write(f"{index}. {option}\n")
        output.flush()
        raw = input_func("Wybór (puste = anuluj): ").strip()
        if not raw:
            return None
        if self.multi:
            result: set[int] = set()
            for part in raw.replace(";", ",").split(","):
                value = part.strip()
                if value.isdigit() and 1 <= int(value) <= len(self.options):
                    result.add(int(value) - 1)
            return result
        return int(raw) - 1 if raw.isdigit() and 1 <= int(raw) <= len(self.options) else None


@dataclass(slots=True)
class CursorAppState:
    database: Path
    sources: list[Path] = field(default_factory=list)
    last_report: dict | None = None


class MemoryImportCursorApp:
    """Cursor UI; all data operations remain in tested importer/store APIs."""

    MENU = (
        "Dodaj pliki lub folder eksportów",
        "Zbadaj eksporty bez zapisu",
        "Porównaj eksporty z bazą (dry plan)",
        "Importuj do archiwum L0",
        "Sprawdź integralność bazy",
        "Analizuj i pokaż tematy",
        "Dodaj domeny do kolejki ręcznego przeglądu",
        "Pokaż kolejkę przeglądu",
        "Wyszukaj rozmowy",
        "Zmień ścieżkę bazy",
        "Zakończ",
    )

    def __init__(
        self,
        database: str | Path,
        *,
        input_func: Callable[[str], str] = input,
        output: TextIO | None = None,
    ) -> None:
        self.state = CursorAppState(Path(database).expanduser().resolve())
        self.input = input_func
        self.output = output or sys.stdout
        self.importer = ChatExportImporter()

    def run(self) -> int:
        while True:
            title = (
                "Importer pamięci rozmów ChatGPT\n"
                f"Baza: {self.state.database}\n"
                f"Wybrane źródła: {len(self.state.sources)}"
            )
            choice = CursorMenu(title, list(self.MENU)).choose(output=self.output, input_func=self.input)
            if choice is None:
                continue
            if choice == 10:
                return 0
            try:
                self._dispatch(int(choice))
            except KeyboardInterrupt:
                self._write("Przerwano. Zatwierdzone eksporty pozostają zapisane; aktywna transakcja została cofnięta.")
                return 130
            except Exception as exc:
                self._write(f"Błąd: {type(exc).__name__}: {exc}")
            self.input("\nEnter — wróć do menu: ")

    def _dispatch(self, choice: int) -> None:
        actions = {
            0: self._add_sources,
            1: self._inspect,
            2: self._plan,
            3: self._import,
            4: self._verify,
            5: self._topics,
            6: self._queue_domains,
            7: self._show_review,
            8: self._search,
            9: self._change_database,
        }
        actions[choice]()

    def _write(self, text: str) -> None:
        self.output.write(text + "\n")
        self.output.flush()

    def _add_sources(self) -> None:
        raw = self.input("Podaj pliki/foldery rozdzielone średnikiem: ").strip()
        if not raw:
            return
        found: list[Path] = []
        for part in raw.split(";"):
            path = Path(part.strip().strip('"')).expanduser().resolve()
            if path.is_dir():
                found.extend(sorted(item for item in path.iterdir() if item.suffix.lower() in {".zip", ".json", ".html", ".htm"}))
            elif path.is_file():
                found.append(path)
            else:
                self._write(f"Pominięto nieistniejącą ścieżkę: {path}")
        seen = {path for path in self.state.sources}
        for path in found:
            if path not in seen:
                self.state.sources.append(path)
                seen.add(path)
        self._write(f"Wybrano {len(self.state.sources)} unikalnych źródeł.")

    def _require_sources(self) -> list[Path]:
        if not self.state.sources:
            raise RuntimeError("najpierw dodaj co najmniej jeden eksport")
        return list(self.state.sources)

    def _inspect(self) -> None:
        reports = []
        for index, source in enumerate(self._require_sources(), 1):
            self._write(f"[{index}/{len(self.state.sources)}] Sprawdzam {source.name}…")
            reports.append(self.importer.inspect(source))
        self.state.last_report = {"reports": reports}
        self._write(json_pretty(self.state.last_report))

    def _plan(self) -> None:
        plans = []
        for index, source in enumerate(self._require_sources(), 1):
            self._write(f"[{index}/{len(self.state.sources)}] Porównuję {source.name}…")
            plan = self.importer.plan(source, self.state.database)
            data = plan.to_dict()
            data.pop("conversations", None)
            plans.append(data)
        self.state.last_report = {"plans": plans}
        self._write(json_pretty(self.state.last_report))

    def _import(self) -> None:
        sources = self._require_sources()
        plans = [self.importer.plan(source, self.state.database) for source in sources]
        counters: dict[str, int] = {}
        for plan in plans:
            for key, value in plan.counters().items():
                counters[key] = counters.get(key, 0) + value
        self._write(f"Plan: {counters}; źródła={len(sources)}; baza={self.state.database}")
        if not explicit_confirmation(self.input, "Wpisz IMPORTUJ, aby rozpocząć zapis: ", token="IMPORTUJ"):
            self._write("Import anulowany — baza nie została zmieniona.")
            return
        self.state.database.parent.mkdir(parents=True, exist_ok=True)
        results = self.importer.import_many(sources, self.state.database, full_validation=False)
        with ChatExportArchiveStore(self.state.database) as store:
            validation = store.validate(full=True)
        self.state.last_report = {
            "results": [result.to_dict() for result in results],
            "validation": validation,
        }
        self._write(json_pretty(self.state.last_report))

    def _verify(self) -> None:
        if not self.state.database.exists():
            raise FileNotFoundError(self.state.database)
        with ChatExportArchiveStore(self.state.database) as store:
            report = store.validate(full=True)
        self.state.last_report = report
        self._write(json_pretty(report))

    def _topics(self) -> None:
        if not self.state.database.exists():
            raise FileNotFoundError(self.state.database)
        with ChatExportTopicStore(self.state.database) as topics:
            report = topics.analyse_all(force=False)
            summary = topics.summary()
        self.state.last_report = {"analysis": report, "summary": summary}
        self._write(json_pretty(self.state.last_report))

    def _queue_domains(self) -> None:
        if not self.state.database.exists():
            raise FileNotFoundError(self.state.database)
        selected = CursorMenu(
            "Wybierz domeny do ręcznej kolejki przeglądu",
            list(DOMAIN_OPTIONS),
            multi=True,
        ).choose(output=self.output, input_func=self.input)
        if not selected:
            self._write("Nie wybrano domen.")
            return
        domains = [DOMAIN_OPTIONS[index] for index in sorted(selected)]
        reason = self.input("Powód dodania do kolejki: ").strip() or "manual cursor review"
        self._write(f"Wybrane domeny: {', '.join(domains)}")
        if not explicit_confirmation(self.input, "Wpisz DODAJ, aby utworzyć kandydatów: ", token="DODAJ"):
            self._write("Operacja anulowana.")
            return
        with ChatExportTopicStore(self.state.database) as topics:
            topics.analyse_all(force=False)
            inserted = topics.queue_domains(domains, reason=reason)
        self._write(f"Dodano {inserted} nowych kandydatów. Nie dokonano promocji do L2/L3.")

    def _show_review(self) -> None:
        with ChatExportTopicStore(self.state.database) as topics:
            queue = topics.review_queue(status="pending_review", limit=200)
        self.state.last_report = {"pending_review": queue}
        self._write(json_pretty(self.state.last_report))

    def _search(self) -> None:
        query = self.input("Zapytanie FTS5: ").strip()
        if not query:
            return
        with ChatExportArchiveStore(self.state.database) as store:
            results = store.search(query, limit=30)
        self.state.last_report = {"query": query, "results": results}
        self._write(json_pretty(self.state.last_report))

    def _change_database(self) -> None:
        raw = self.input(f"Nowa ścieżka bazy [{self.state.database}]: ").strip().strip('"')
        if raw:
            self.state.database = Path(raw).expanduser().resolve()


def explicit_confirmation(input_func: Callable[[str], str], prompt: str, *, token: str) -> bool:
    """Never authorize a write by empty input or a generic Enter press."""
    return input_func(prompt).strip() == token


def json_pretty(value: object) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
