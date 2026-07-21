from __future__ import annotations

import importlib.util
import os
import tempfile
import threading
import sys
import time
from pathlib import Path
from typing import Any

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.data_structures import Size

SCRIPT = Path(__file__).with_name("jazn_pack_generator_7.0.py")
spec = importlib.util.spec_from_file_location("jazn_pack_generator_v7", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class SizedDummyOutput(DummyOutput):
    def __init__(self, columns: int, rows: int):
        super().__init__()
        self._size = Size(rows=rows, columns=columns)

    def get_size(self) -> Size:
        return self._size

    def set_size(self, columns: int, rows: int) -> None:
        self._size = Size(rows=rows, columns=columns)


def wait_for(predicate, timeout: float = 4.0, message: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"Timeout waiting for {message}")


def send_down(pipe: Any, count: int) -> None:
    pipe.send_text("\x1b[B" * count)


def send_up(pipe: Any, count: int) -> None:
    pipe.send_text("\x1b[A" * count)


def run_case(columns: int, *, compact_expected: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="jazn-pack-v7-ui-") as raw:
        temp = Path(raw)
        root = temp / "jazn_root"
        (root / "latka_jazn").mkdir(parents=True)
        (root / "latka_jazn" / "version.py").write_text(
            'DISTRIBUTION_VERSION = "15.1.0.3"\n'
            'PACKAGE_VERSION = "v15.1.0.3.700"\n'
            'PACKAGE_RELEASE_NAME = "UI test"\n',
            encoding="utf-8",
        )
        out_dir = temp / "packages"
        out_dir.mkdir()
        (out_dir / "alpha").mkdir()
        state = mod.InteractiveState(
            source=root,
            out_dir=out_dir,
            profile="system",
            ui_mode="kursorowy",
        )

        # Pakowanie jest zastąpione szybką operacją, aby testować sam kontrakt UI.
        original_build = mod.build_plans_for_options
        original_run = mod.run_pack_with_plans
        setattr(mod, "build_plans_for_options", lambda options: [])
        setattr(mod, "run_pack_with_plans", lambda options, plans: [])

        debug: dict[str, Any] = {}
        output = SizedDummyOutput(columns, 32)
        result_box: dict[str, Any] = {}

        with create_pipe_input() as pipe:
            def target() -> None:
                result_box["result"] = mod.cursor_dashboard(
                    state,
                    0,
                    _input=pipe,
                    _output=output,
                    _debug_state=debug,
                )

            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            wait_for(lambda: debug.get("ready") is True, message="dashboard ready")
            wait_for(lambda: debug.get("compact") is compact_expected, message="layout mode")
            assert debug["menu_index"] == 0
            assert debug["panel_title"] == "Pakuj teraz"
            assert debug["focus_zone"] == "menu"

            # Główna akcja: fokus musi przejść na prawą stronę.
            pipe.send_text("\r")
            wait_for(lambda: debug.get("panel_mode") == "action", message="pack action panel")
            assert debug["focus_zone"] == "detail"
            if compact_expected:
                assert debug["compact_page"] == "detail"

            # Drugie Enter uruchamia worker; UI nie może się zawiesić.
            pipe.send_text("\r")
            wait_for(lambda: debug.get("busy") is False and debug.get("panel_mode") == "result", message="worker finish")

            # Powrót do menu i aktualizacja informacji przy zmianie kursora.
            pipe.send_text("\x1b")
            wait_for(lambda: debug.get("focus_zone") == "menu", message="return to menu")
            send_down(pipe, 6)
            wait_for(lambda: debug.get("menu_index") == 6, message="tools row")
            assert debug["panel_title"] == "Zweryfikuj istniejącą paczkę"

            # Narzędzie ma otworzyć prawy edytor i przejąć fokus.
            pipe.send_text("\r")
            wait_for(lambda: debug.get("panel_mode") == "verify_input", message="verify editor")
            assert debug["focus_zone"] == "detail"
            wait_for(lambda: debug.get("buffer_name") == "right-editor", message="right editor focus")
            assert debug["buffer_has_completer"] is True

            # Automatyczne podpowiedzi ścieżek po wpisaniu fragmentu katalogu.
            pipe.send_text(os.sep + "alp")
            wait_for(lambda: debug.get("completion_active") is True, message="path completions")
            pipe.send_text("\x1b")
            wait_for(lambda: debug.get("focus_zone") == "menu", message="leave verify")

            # Opcje: Format jest pierwszą pozycją i otwiera popup.
            send_down(pipe, 3)
            wait_for(lambda: debug.get("menu_index") == 9, message="options row")
            pipe.send_text("\r")
            wait_for(lambda: debug.get("panel_mode") == "options", message="options panel")
            assert debug["focus_zone"] == "detail"
            pipe.send_text("\r")
            wait_for(lambda: debug.get("popup_visible") is True and debug.get("popup_kind") == "format", message="format popup")
            pipe.send_text("\x1b")
            wait_for(lambda: debug.get("popup_visible") is False, message="popup close")
            pipe.send_text("\x1b")
            wait_for(lambda: debug.get("focus_zone") == "menu", message="leave options")

            # Wykluczenia: przejście na prawą stronę i dalsza nawigacja listą.
            send_down(pipe, 1)
            wait_for(lambda: debug.get("menu_index") == 10, message="exclusions row")
            pipe.send_text("\r")
            wait_for(lambda: debug.get("panel_mode") == "exclusions", message="exclusions panel")
            assert debug["focus_zone"] == "detail"
            pipe.send_text("\r")
            wait_for(lambda: debug.get("panel_mode") == "exclusion_list", message="base exclusions list")
            pipe.send_text("\x1b")
            wait_for(lambda: debug.get("panel_mode") == "exclusions", message="back to exclusions actions")
            pipe.send_text("\x1b")
            wait_for(lambda: debug.get("focus_zone") == "menu", message="back to menu")

            # Zakończ przez Ctrl+X -> Wyjdź bez zapisu.
            pipe.send_text("\x18")
            wait_for(lambda: debug.get("panel_mode") == "exit_choice", message="exit choice")
            send_up(pipe, 1)  # domyślnie Wróć -> Wyjdź bez zapisu
            pipe.send_text("\r")
            thread.join(timeout=4)
            assert not thread.is_alive(), "Dashboard did not exit"

        setattr(mod, "build_plans_for_options", original_build)
        setattr(mod, "run_pack_with_plans", original_run)
        return {
            "compact": compact_expected,
            "final": dict(debug),
            "result": result_box.get("result"),
        }


def main() -> None:
    wide = run_case(120, compact_expected=False)
    compact = run_case(80, compact_expected=True)
    print("UI CONTRACT PASS")
    print({"wide": wide["result"], "compact": compact["result"]})


if __name__ == "__main__":
    main()
