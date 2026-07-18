#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "tools" / "memory_import_ui.py"
MENU_MARKER = "Importer pamięci rozmów ChatGPT"


def _run_case(*, keys: str, expected_exit: int, expected_text: str, name: str) -> dict[str, object]:
    if sys.platform != "win32":
        raise RuntimeError("Windows ConPTY smoke must run on Windows")

    from winpty import PtyProcess

    with tempfile.TemporaryDirectory(prefix="jazn-cursor-conpty-") as temporary:
        database = Path(temporary) / "archive.sqlite3"
        command = subprocess.list2cmdline([
            sys.executable,
            "-X",
            "utf8",
            str(UI),
            "--database",
            str(database),
        ])
        process = PtyProcess.spawn(
            command,
            cwd=str(ROOT),
            dimensions=(40, 120),
        )
        chunks: list[str] = []
        reader_error: list[str] = []

        def read_output() -> None:
            try:
                while True:
                    try:
                        chunk = process.read(4096)
                    except EOFError:
                        break
                    if not chunk:
                        break
                    chunks.append(str(chunk))
            except Exception as exc:  # pragma: no cover - Windows diagnostic path
                reader_error.append(f"{type(exc).__name__}: {exc}")

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + 30.0
        while MENU_MARKER not in "".join(chunks):
            if not process.isalive():
                break
            if time.monotonic() >= deadline:
                process.close(force=True)
                raise TimeoutError(f"{name}: menu marker was not rendered")
            time.sleep(0.05)

        output_before = "".join(chunks)
        if MENU_MARKER not in output_before:
            process.close(force=True)
            raise AssertionError(f"{name}: cursor menu did not render; output={output_before!r}")

        process.write(keys)
        while process.isalive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if process.isalive():
            process.close(force=True)
            raise TimeoutError(f"{name}: UI did not exit after key sequence")

        exit_code = process.wait()
        reader.join(timeout=5.0)
        output = "".join(chunks)
        if reader_error:
            raise AssertionError(f"{name}: reader failed: {reader_error}")
        if exit_code != expected_exit:
            raise AssertionError(
                f"{name}: exit={exit_code}, expected={expected_exit}; output={output!r}"
            )
        if expected_text not in output:
            raise AssertionError(
                f"{name}: expected text {expected_text!r} missing; output={output!r}"
            )
        if "Traceback (most recent call last)" in output:
            raise AssertionError(f"{name}: traceback leaked to terminal; output={output!r}")
        return {
            "name": name,
            "exit_code": exit_code,
            "rendered_menu": True,
            "output_characters": len(output),
        }


def main() -> int:
    results = [
        _run_case(
            name="arrow-navigation-and-enter",
            keys=("\x1b[B" * 10) + "\r",
            expected_exit=0,
            expected_text=MENU_MARKER,
        ),
        _run_case(
            name="ctrl-x-clean-exit",
            keys="\x18",
            expected_exit=130,
            expected_text="Przerwano przez Ctrl+X",
        ),
    ]
    for result in results:
        print(result)
    print("WINDOWS_CONPTY_CURSOR_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
