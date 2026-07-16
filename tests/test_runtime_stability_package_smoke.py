from __future__ import annotations

from pathlib import Path
import json

from latka_jazn import cli
from latka_jazn.tools import release_readiness
from latka_jazn.tools.package_export import forbidden_package_reason


def test_package_smoke_does_not_reference_missing_external_script() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "tools/release_readiness_v15.py" not in source
    assert "build_release_readiness_report" in source


def test_package_smoke_json_is_one_document_and_preserves_exit_code(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setattr(
        release_readiness,
        "build_release_readiness_report",
        lambda root, profile="system": {
            "schema_version": "test/v1", "ok": False, "exit_code": 1,
            "profile": profile, "root": str(root), "checks": [],
        },
    )
    code = cli.main(["package-smoke", "--root", str(tmp_path), "--profile", "system", "--json"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == ""
    assert json.loads(captured.out)["exit_code"] == 1


def test_incomplete_package_is_configuration_error(tmp_path: Path) -> None:
    report = release_readiness.build_release_readiness_report(tmp_path, profile="system")
    assert report["ok"] is False
    assert report["exit_code"] == 2


def test_system_profile_does_not_require_memory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(release_readiness, "verify_package_integrity_manifest", lambda _root: {"ok": False, "configuration_error": True, "errors": []})
    report = release_readiness.build_release_readiness_report(tmp_path, profile="system")
    assert not any(item["name"] == "memory_wake_state" for item in report["checks"])


def test_backups_are_forbidden_export_paths() -> None:
    assert forbidden_package_reason("backups/pre-change/working-tree.patch") is not None
