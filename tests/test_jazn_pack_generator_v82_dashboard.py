from __future__ import annotations

import importlib.util
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest


prompt_toolkit = pytest.importorskip("prompt_toolkit")
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "tools" / "jazn_pack_generator.py"


def _load_generator():
    module_name = "jazn_pack_generator_v82_dashboard_test"
    spec = importlib.util.spec_from_file_location(module_name, GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class SizedDummyOutput(DummyOutput):
    def __init__(self, columns: int, rows: int):
        super().__init__()
        self._size = Size(rows=rows, columns=columns)

    def get_size(self) -> Size:
        return self._size


def _wait_for(predicate, timeout: float = 5.0, message: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"Timeout waiting for {message}")


def test_dashboard_profile_popup_full_width_and_exit_contract() -> None:
    generator = _load_generator()
    if not generator._dashboard_available():
        pytest.skip("prompt_toolkit dashboard dependencies are unavailable")

    with tempfile.TemporaryDirectory(prefix="jazn-pack-v82-dashboard-") as raw:
        temp = Path(raw)
        root = temp / "runtime"
        (root / "latka_jazn").mkdir(parents=True)
        (root / "latka_jazn" / "version.py").write_text(
            'DISTRIBUTION_VERSION = "91.82.73.64"\n'
            'PACKAGE_VERSION = "v91.82.73.64"\n'
            'PACKAGE_RELEASE_NAME = "dashboard-fixture"\n',
            encoding="utf-8",
        )
        (root / "run.py").write_text("print('run')\n", encoding="utf-8")
        (root / "main.py").write_text("print('main')\n", encoding="utf-8")
        (root / "SOURCE_PROVENANCE.json").write_text("{}\n", encoding="utf-8")

        state = generator.InteractiveState(
            source=root,
            out_dir=temp / ".packages",
            profile="dual",
            ui_mode="kursorowy",
        )
        debug: dict[str, Any] = {}
        result_box: dict[str, Any] = {}

        with create_pipe_input() as pipe:
            def target() -> None:
                result_box["result"] = generator.cursor_dashboard(
                    state,
                    _input=pipe,
                    _output=SizedDummyOutput(120, 32),
                    _debug_state=debug,
                )

            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            _wait_for(lambda: debug.get("ready") is True, message="dashboard ready")
            _wait_for(lambda: debug.get("left_width_mode") == "full", message="full-width menu")
            assert debug["menu_index"] == 0
            assert debug["right_panel_visible"] is False

            pipe.send_text("\r")
            _wait_for(
                lambda: debug.get("popup_visible") is True and debug.get("popup_kind") == "profile",
                message="profile popup",
            )
            pipe.send_text("\x1b")
            _wait_for(lambda: debug.get("popup_visible") is False, message="profile popup close")

            pipe.send_text("\x18")
            _wait_for(
                lambda: debug.get("popup_visible") is True and debug.get("popup_kind") == "exit",
                message="exit popup",
            )
            pipe.send_text("\r")
            thread.join(timeout=5)
            assert not thread.is_alive(), "Dashboard did not exit"
            assert result_box["result"] == "exit"
