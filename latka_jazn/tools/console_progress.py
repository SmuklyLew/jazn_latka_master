from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import os
import shutil
import sys
import threading
import time
from typing import Callable, Iterator, TextIO


ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True, slots=True)
class ProgressSymbols:
    success: str
    error: str
    warning: str
    info: str
    arrow: str
    work: str
    wait: str
    folder: str
    lock: str
    log: str
    launch: str
    spinner: tuple[str, ...]
    fill: str
    empty: str


UNICODE_SYMBOLS = ProgressSymbols(
    success="✔",
    error="✖",
    warning="⚠",
    info="ℹ",
    arrow="➜",
    work="⚙",
    wait="⌛",
    folder="📁",
    lock="🔒",
    log="🪵",
    launch="🚀",
    spinner=("/", "-", "\\", "|"),
    fill="*",
    empty=" ",
)
ASCII_SYMBOLS = ProgressSymbols(
    success="OK",
    error="X",
    warning="!",
    info="i",
    arrow="->",
    work="*",
    wait="...",
    folder="DIR",
    lock="LOCK",
    log="LOG",
    launch=">>",
    spinner=("/", "-", "\\", "|"),
    fill="#",
    empty=" ",
)


def add_progress_arguments(parser: object) -> None:
    """Add the shared progress switches to an argparse parser."""

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--progress",
        action="store_const",
        const="always",
        dest="progress_mode",
        help="Pokazuj postęp na stderr także poza interaktywnym terminalem.",
    )
    group.add_argument(
        "--no-progress",
        action="store_const",
        const="never",
        dest="progress_mode",
        help="Nie pokazuj wskaźnika postępu.",
    )
    parser.set_defaults(progress_mode="auto")
    parser.add_argument(
        "--ascii-progress",
        action="store_true",
        help="Użyj wyłącznie znaków ASCII w komunikatach postępu.",
    )


def _mode_from_environment(default: str) -> str:
    value = os.environ.get("JAZN_CLI_PROGRESS", "").strip().lower()
    if value in {"1", "true", "yes", "on", "always"}:
        return "always"
    if value in {"0", "false", "no", "off", "never"}:
        return "never"
    return default


def _stream_supports_unicode(stream: TextIO) -> bool:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    sample = "✔✖⚠ℹ➜⚙⌛📁🔒🪵🚀"
    try:
        sample.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _format_elapsed(seconds: float) -> tuple[str, str]:
    safe = max(0.0, float(seconds))
    compact = f"{safe:.2f}s"
    whole = int(round(safe))
    return compact, str(timedelta(seconds=whole))


class TerminalProgress:
    """Dependency-free progress renderer that never writes to stdout.

    ``auto`` enables animation only for an interactive stderr stream. ``always``
    keeps line-based progress visible in logs and tests. All JSON and other
    machine-readable payloads can therefore remain isolated on stdout.
    """

    def __init__(
        self,
        task: str,
        *,
        style: str = "bar",
        stream: TextIO | None = None,
        mode: str = "auto",
        ascii_only: bool = False,
        width: int | None = None,
        minimum_delay: float = 0.25,
        refresh_interval: float = 0.10,
    ) -> None:
        self.task = str(task).strip() or "Jaźń"
        self.style = style if style in {"bar", "dots", "spinner", "stages"} else "bar"
        self.stream = stream if stream is not None else sys.stderr
        requested_mode = _mode_from_environment(str(mode or "auto").lower())
        self.interactive = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = requested_mode == "always" or (requested_mode == "auto" and self.interactive)
        env_ascii = os.environ.get("JAZN_CLI_ASCII", "").strip().lower() in {"1", "true", "yes", "on"}
        self.symbols = ASCII_SYMBOLS if ascii_only or env_ascii or not _stream_supports_unicode(self.stream) else UNICODE_SYMBOLS
        terminal_columns = shutil.get_terminal_size((100, 24)).columns
        computed_width = max(20, min(52, terminal_columns - 42))
        self.width = max(12, int(width or computed_width))
        self.minimum_delay = max(0.0, float(minimum_delay))
        self.refresh_interval = max(0.04, float(refresh_interval))
        self.started_at = time.monotonic()
        self._last_line_length = 0
        self._last_percentage: int | None = None
        self._last_non_tty_bucket: int | None = None
        self._closed = False
        self._spinner_stop = threading.Event()
        self._spinner_thread: threading.Thread | None = None
        self._spinner_label = ""
        self._spinner_symbol = "wait"
        self._lock = threading.RLock()

    @classmethod
    def from_namespace(
        cls,
        namespace: object,
        task: str,
        *,
        style: str = "bar",
        stream: TextIO | None = None,
        width: int | None = None,
    ) -> "TerminalProgress":
        return cls(
            task,
            style=style,
            stream=stream,
            mode=getattr(namespace, "progress_mode", "auto"),
            ascii_only=bool(getattr(namespace, "ascii_progress", False)),
            width=width,
        )

    def _symbol(self, name: str) -> str:
        return str(getattr(self.symbols, name, self.symbols.work))

    def _write_dynamic(self, line: str, *, final: bool = False) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self.interactive:
                padding = " " * max(0, self._last_line_length - len(line))
                print(f"\r{line}{padding}", end="\n" if final else "", file=self.stream, flush=True)
                self._last_line_length = 0 if final else len(line)
            else:
                print(line, file=self.stream, flush=True)

    def _clear_dynamic(self) -> None:
        if not self.enabled or not self.interactive:
            return
        with self._lock:
            if self._last_line_length:
                print("\r" + (" " * self._last_line_length) + "\r", end="", file=self.stream, flush=True)
                self._last_line_length = 0

    def _render_meter(self, completed: int, total: int, label: str, *, symbol: str) -> str:
        safe_total = max(1, int(total))
        safe_completed = min(max(0, int(completed)), safe_total)
        fraction = safe_completed / safe_total
        percentage = min(100, max(0, round(fraction * 100)))
        if self.style == "dots":
            filled = min(self.width, round(self.width * fraction))
            meter = "." * filled + " " * (self.width - filled)
            return f"{meter} [{percentage:3d}%] {label}"
        filled = min(self.width, round(self.width * fraction))
        bar = self.symbols.fill * filled + self.symbols.empty * (self.width - filled)
        return f"{self._symbol(symbol)} [{bar}] {percentage:3d}% {label}"

    def update(
        self,
        completed: int,
        total: int,
        label: str,
        *,
        symbol: str = "work",
        force: bool = False,
    ) -> None:
        if not self.enabled or self._closed:
            return
        self.stop_spinner(clear=True)
        safe_total = max(1, int(total))
        safe_completed = min(max(0, int(completed)), safe_total)
        percentage = min(100, max(0, round(safe_completed * 100 / safe_total)))
        if not self.interactive and not force:
            bucket = percentage // 10
            if percentage not in {0, 100} and bucket == self._last_non_tty_bucket:
                return
            self._last_non_tty_bucket = bucket
        line = self._render_meter(safe_completed, safe_total, str(label), symbol=symbol)
        self._write_dynamic(line, final=False)
        self._last_percentage = percentage

    def callback(self, *, symbol: str = "work") -> ProgressCallback:
        def report(completed: int, total: int, label: str) -> None:
            self.update(completed, total, label, symbol=symbol)

        return report

    def start_spinner(self, label: str, *, symbol: str = "wait") -> None:
        if not self.enabled or self._closed:
            return
        self.stop_spinner(clear=True)
        self._spinner_label = str(label)
        self._spinner_symbol = symbol
        self._spinner_stop.clear()
        if not self.interactive:
            self._write_dynamic(f"{self._symbol(symbol)} {self._spinner_label}", final=False)
            return

        def animate() -> None:
            if self._spinner_stop.wait(self.minimum_delay):
                return
            index = 0
            while not self._spinner_stop.is_set():
                elapsed = time.monotonic() - self.started_at
                frame = self.symbols.spinner[index % len(self.symbols.spinner)]
                line = f"{self._symbol(self._spinner_symbol)} {frame} {self._spinner_label}  {_format_elapsed(elapsed)[1]}"
                self._write_dynamic(line, final=False)
                index += 1
                self._spinner_stop.wait(self.refresh_interval)

        self._spinner_thread = threading.Thread(target=animate, name=f"jazn-progress-{self.task}", daemon=True)
        self._spinner_thread.start()

    def stop_spinner(self, *, clear: bool) -> None:
        thread = self._spinner_thread
        if thread is not None:
            self._spinner_stop.set()
            thread.join(timeout=max(0.5, self.refresh_interval * 4))
            self._spinner_thread = None
        if clear:
            self._clear_dynamic()

    @contextmanager
    def spinning(self, label: str, *, symbol: str = "wait") -> Iterator["TerminalProgress"]:
        self.start_spinner(label, symbol=symbol)
        try:
            yield self
        except Exception:
            self.finish(False, f"{label} — przerwano")
            raise
        finally:
            self.stop_spinner(clear=True)

    def message(self, label: str, *, symbol: str = "info") -> None:
        if not self.enabled or self._closed:
            return
        self.stop_spinner(clear=True)
        self._write_dynamic(f"{self._symbol(symbol)} {label}", final=True)

    def finish(self, ok: bool, label: str, *, percentage: int = 100) -> None:
        if not self.enabled or self._closed:
            return
        self.stop_spinner(clear=True)
        compact, clock = _format_elapsed(time.monotonic() - self.started_at)
        status_symbol = self.symbols.success if ok else self.symbols.error
        safe_percentage = min(100, max(0, int(percentage)))
        if self.style in {"bar", "dots", "stages"}:
            if self.style == "dots":
                meter = "." * self.width
                line = f"{status_symbol} {meter} [{safe_percentage:3d}%] {label} in {compact} ({clock})"
            else:
                bar = self.symbols.fill * self.width
                line = f"{status_symbol} [{bar}] {safe_percentage:3d}% {label} in {compact} ({clock})"
        else:
            line = f"{status_symbol} {label} in {compact} ({clock})"
        self._write_dynamic(line, final=True)
        self._closed = True

    def fail(self, label: str) -> None:
        self.finish(False, label, percentage=self._last_percentage or 0)
