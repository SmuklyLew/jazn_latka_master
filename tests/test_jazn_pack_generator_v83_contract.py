from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "tools" / "jazn_pack_generator.py"


def _load_generator():
    module_name = "jazn_pack_generator_v83_contract_test"
    spec = importlib.util.spec_from_file_location(module_name, GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _runtime_fixture(root: Path) -> Path:
    (root / "latka_jazn").mkdir(parents=True)
    (root / "latka_jazn" / "version.py").write_text(
        'DISTRIBUTION_VERSION = "91.82.73.64"\n'
        'PACKAGE_VERSION = "v91.82.73.64"\n'
        'PACKAGE_RELEASE_NAME = "fixture-release"\n',
        encoding="utf-8",
    )
    (root / "SOURCE_PROVENANCE.json").write_text("{}\n", encoding="utf-8")
    (root / "run.py").write_text("print('run')\n", encoding="utf-8")
    (root / "main.py").write_text("print('main')\n", encoding="utf-8")
    (root / "payload.txt").write_text("system payload\n", encoding="utf-8")
    return root


def test_generator_identity_examples_and_default_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    generator = _load_generator()

    assert generator.GENERATOR_VERSION == "8.3"
    assert generator.SETTINGS_SCHEMA == "jazn_pack_generator_settings/v8.3"
    assert generator.__doc__ is not None
    assert r"py -X utf8 .\tools\jazn_pack_generator.py" in generator.__doc__
    assert "py _jazn_pack_generator.py" not in generator.__doc__
    assert r"D:\.AI\.packages" in generator.__doc__

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    state = generator.default_interactive_state()
    assert state.out_dir == tmp_path / ".packages"


def test_cli_rejects_abbreviated_long_options() -> None:
    generator = _load_generator()

    exact = generator.parser().parse_args(["pack", ".", "--profile", "system"])
    assert exact.profile == "system"

    with pytest.raises(SystemExit):
        generator.parser().parse_args(["pack", ".", "--prof", "system"])
    with pytest.raises(SystemExit):
        generator.parser().parse_args(["plan", ".", "--prof", "system"])
    with pytest.raises(SystemExit):
        generator.parser().parse_args(["extract", "sidecar.json", "out", "--cl"])


def test_v83_menu_and_responsive_contract(tmp_path: Path) -> None:
    generator = _load_generator()
    state = generator.InteractiveState(
        source=tmp_path / "jazn",
        out_dir=tmp_path / ".packages",
        profile="dual",
    )

    rows = generator.main_menu_rows(state)
    assert rows[:3] == [
        "Profil: [SYSTEM + PAMIĘĆ (dual)]",
        "Pakuj teraz",
        "Pokaż kanoniczny plan",
    ]
    assert rows[3].startswith("System Jaźni: [")
    assert rows[4].startswith("Zapis archiwum: [")
    assert rows[5].startswith("Nazwa: [")
    assert rows[6] == "Odśwież nazwę paczki"
    assert "Zapisz ustawienia" not in rows

    details = generator.main_menu_details(state)
    assert "modalnym oknie wyskakującym" in details[0]
    assert "następnym wierszu" in details[3]
    assert "następnym wierszu" in details[4]
    assert "następnym wierszu" in details[5]

    assert generator.dashboard_left_width_mode(right_visible=False, compact=False) == "full"
    assert generator.dashboard_left_width_mode(right_visible=True, compact=False) == "narrow"
    assert generator.dashboard_left_width_mode(right_visible=False, compact=True) == "compact"


def test_canonical_system_package_requires_both_entrypoints(tmp_path: Path) -> None:
    generator = _load_generator()
    root = _runtime_fixture(tmp_path / "runtime")

    plan = generator.build_plan(root, "system", [])
    assert {"run.py", "main.py"} <= set(plan.paths)

    (root / "main.py").unlink()
    with pytest.raises(generator.PackError, match="main.py"):
        generator.build_plan(root, "system", [])


def test_dual_package_roundtrip_and_exclusion_contract(tmp_path: Path) -> None:
    generator = _load_generator()
    root = _runtime_fixture(tmp_path / "runtime")
    memory = root / "memory"
    memory.mkdir()
    (memory / "runtime_memory.sqlite3").write_bytes(b"sqlite fixture")
    (memory / "runtime_memory.sqlite3-wal").write_bytes(b"transient")
    (memory / "nested.zip").write_bytes(b"not a real zip")
    (root / "workspace_runtime").mkdir()
    (root / "workspace_runtime" / "state.json").write_text("{}\n", encoding="utf-8")

    out_dir = tmp_path / ".packages"
    options = generator.PackOptions(
        source=root,
        out_dir=out_dir,
        profile="dual",
        archive_format="independent",
        archive_basename="",
        part_size_mb=4,
        compatibility_checks=False,
        update_source_manifest=True,
    )

    results = generator.run_pack(options)
    by_profile = {result.profile: result for result in results}
    assert set(by_profile) == {"system", "memory"}

    system_sidecar = json.loads(by_profile["system"].sidecar_path.read_text(encoding="utf-8"))
    system_entries = {item["path"] for item in system_sidecar["entries"]}
    assert "PACKAGE_INTEGRITY_MANIFEST.json" in system_entries
    assert not any(path.startswith("memory/") for path in system_entries)
    assert not any(path.startswith("workspace_runtime/") for path in system_entries)

    memory_sidecar = json.loads(by_profile["memory"].sidecar_path.read_text(encoding="utf-8"))
    memory_entries = {item["path"] for item in memory_sidecar["entries"]}
    assert "memory/runtime_memory.sqlite3" in memory_entries
    assert "memory/MEMORY_PACKAGE_MANIFEST.json" in memory_entries
    assert "memory/runtime_memory.sqlite3-wal" not in memory_entries
    assert "memory/nested.zip" not in memory_entries

    for result in results:
        report = generator.verify_package_sidecar(result.sidecar_path)
        assert report["ok"] is True
        destination = tmp_path / f"extract-{result.profile}"
        extracted = generator.extract_package_sidecar(
            result.sidecar_path,
            destination,
            clean=False,
            force=False,
        )
        assert extracted["destination"] == str(destination.resolve())

    assert (tmp_path / "extract-system" / "run.py").is_file()
    assert (tmp_path / "extract-system" / "main.py").is_file()
    assert not (tmp_path / "extract-system" / "memory").exists()
    assert (tmp_path / "extract-memory" / "memory" / "runtime_memory.sqlite3").is_file()


def test_path_safety_rejects_traversal_and_absolute_paths(tmp_path: Path) -> None:
    generator = _load_generator()
    for unsafe in ("../escape.txt", "/absolute.txt", r"C:\absolute.txt"):
        with pytest.raises(generator.PackError):
            generator.safe_destination_path(tmp_path, unsafe)
