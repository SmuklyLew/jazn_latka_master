from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import subprocess
import zipfile

import pytest

from latka_jazn import cli
from latka_jazn.cli_commands import diagnostics
from latka_jazn.tools.release_bundle import build_release_bundle, verify_release_zip_manifest
from latka_jazn.tools.release_staging import create_release_staging, create_system_smoke_staging
from latka_jazn.tools.source_provenance import SourceProvenanceError
from latka_jazn.version import PACKAGE_VERSION_FULL


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _minimal_release_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "latka_jazn").mkdir(parents=True)
    (root / "latka_jazn" / "__init__.py").write_text("", encoding="utf-8")
    (root / "latka_jazn" / "version.py").write_text(
        "DISTRIBUTION_VERSION = '15.0.3.4'\n"
        "PACKAGE_VERSION = 'v15.0.3.4'\n"
        "PACKAGE_RELEASE_NAME = ''\n",
        encoding="utf-8",
    )
    (root / "run.py").write_text("print('run')\n", encoding="utf-8")
    (root / "main.py").write_text("print('main')\n", encoding="utf-8")
    (root / "README.md").write_text("release fixture\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "remote", "add", "origin", "https://github.com/SmuklyLew/jazn_latka.git")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    return root


def test_system_smoke_accepts_only_manifest_verified_export_without_git(tmp_path: Path) -> None:
    repo = _minimal_release_repo(tmp_path)
    export = tmp_path / "release-export"
    create_release_staging(repo, export)
    assert not (export / ".git").exists()

    smoke = tmp_path / "smoke"
    report = create_system_smoke_staging(export, smoke)
    assert report["ok"] is True
    assert report["source_kind"] == "verified_export_without_git"
    assert report["source_manifest_verification"]["ok"] is True

    (export / "run.py").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(SourceProvenanceError, match="valid PACKAGE_INTEGRITY_MANIFEST"):
        create_system_smoke_staging(export, tmp_path / "tampered-smoke")


def _manifest_entry(path: str, content: bytes) -> dict:
    return {
        "path": path,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "mutable_runtime": False,
        "classification": "static_project_file",
    }


def _write_release_zip(path: Path, *, unexpected: bool = False) -> None:
    run_bytes = b"print('ok')\n"
    manifest = {
        "schema_version": f"package_integrity_manifest/{PACKAGE_VERSION_FULL}",
        "version": PACKAGE_VERSION_FULL,
        "runtime_version": PACKAGE_VERSION_FULL,
        "package_version": PACKAGE_VERSION_FULL,
        "start_file": "run.py",
        "files": [_manifest_entry("run.py", run_bytes)],
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("run.py", run_bytes)
        archive.writestr(
            "PACKAGE_INTEGRITY_MANIFEST.json",
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        )
        if unexpected:
            archive.writestr("unexpected.txt", b"not listed")


def test_release_zip_verifier_rejects_unexpected_members(tmp_path: Path) -> None:
    clean_zip = tmp_path / "clean.zip"
    _write_release_zip(clean_zip)
    clean = verify_release_zip_manifest(clean_zip)
    assert clean["ok"] is True
    assert clean["checked_file_count"] == 1

    dirty_zip = tmp_path / "unexpected.zip"
    _write_release_zip(dirty_zip, unexpected=True)
    dirty = verify_release_zip_manifest(dirty_zip)
    assert dirty["ok"] is False
    assert any(item["code"] == "unexpected_zip_member" for item in dirty["errors"])




def test_release_build_failure_preserves_existing_zip(monkeypatch, tmp_path: Path) -> None:
    repo = _minimal_release_repo(tmp_path)
    output = tmp_path / "existing-release.zip"
    original = b"previous verified release"
    output.write_bytes(original)

    def fail_staging(*_args, **_kwargs):
        raise SourceProvenanceError("synthetic staging failure")

    monkeypatch.setattr("latka_jazn.tools.release_bundle.create_release_staging", fail_staging)
    report = build_release_bundle(repo, output)
    assert report["ok"] is False
    assert report["exit_code"] == 2
    assert output.read_bytes() == original


def test_default_release_output_directory_is_ignored_by_git() -> None:
    root = Path(__file__).resolve().parents[1]
    rules = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/exports/" in rules


def test_release_build_operator_command_delegates_and_returns_exit_code(monkeypatch, capsys, tmp_path: Path) -> None:
    output = tmp_path / "release.zip"
    received: dict[str, object] = {}

    def fake_build(root: Path, output_zip: Path | None):
        received["root"] = Path(root)
        received["output"] = output_zip
        return {"ok": True, "exit_code": 0, "output_zip": str(output)}

    monkeypatch.setattr("latka_jazn.tools.release_bundle.build_release_bundle", fake_build)
    code = cli.main([
        "release-build",
        "--root", str(tmp_path),
        "--output", str(output),
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert received["root"] == tmp_path.resolve()
    assert received["output"] == output


@dataclass
class _FakeManifestStatus:
    path: str
    present: bool = True
    primary_present: bool = True
    legacy_present: bool = False
    source_name: str = "PACKAGE_INTEGRITY_MANIFEST.json"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "present": self.present,
            "primary_present": self.primary_present,
            "legacy_present": self.legacy_present,
            "source_name": self.source_name,
        }


def _doctor_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    for relative in (
        "main.py",
        "run.py",
        "latka_jazn/version.py",
        "latka_jazn/core/private_data_export_gate.py",
        "latka_jazn/core/host_visible_finalization.py",
        "latka_jazn/mcp/server.py",
        "PACKAGE_INTEGRITY_MANIFEST.json",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if path.suffix == ".json" else "# fixture\n", encoding="utf-8")
    return root


def test_doctor_separates_installation_activation_release_and_live_readiness(monkeypatch, tmp_path: Path) -> None:
    root = _doctor_root(tmp_path)
    package_status = _FakeManifestStatus(str(root / "PACKAGE_INTEGRITY_MANIFEST.json"))
    status_payload = {
        "startup": {
            "active_cache_status": {},
            "model_adapter_status": {"selected_adapter": "null_model_adapter", "status": "available"},
            "conversation_archive_status": {"status": "unavailable"},
            "runtime_write_access_status": {"status": "readonly"},
        },
        "daemon": {
            "active_state": "inactive",
            "pid_alive": False,
            "endpoint_reachable": False,
            "heartbeat_fresh": False,
        },
    }
    monkeypatch.setattr(diagnostics, "status_payload", lambda *_a, **_k: status_payload)
    monkeypatch.setattr(
        diagnostics,
        "_read_manifest",
        lambda _root: ({"runtime_version": PACKAGE_VERSION_FULL, "start_file": "run.py"}, None),
    )
    monkeypatch.setattr(diagnostics, "package_integrity_manifest_status", lambda _root: package_status)
    monkeypatch.setattr(diagnostics, "verify_package_integrity_manifest", lambda _root: {"ok": True, "errors": []})
    monkeypatch.setattr(
        diagnostics,
        "read_source_provenance",
        lambda *_a, **_k: SimpleNamespace(to_dict=lambda: {
            "status": "clean_checkout_verified",
            "version_matches_runtime": True,
        }),
    )

    payload = diagnostics.doctor_payload(root)
    assert payload["ok"] is True
    assert payload["installation_ok"] is True
    assert payload["activation_ready"] is True
    assert payload["release_metadata_current"] is True
    assert payload["release_ready"] is True
    assert payload["live_runtime_ready"] is False

    monkeypatch.setattr(
        diagnostics,
        "verify_package_integrity_manifest",
        lambda _root: {"ok": False, "errors": [{"code": "version_mismatch"}]},
    )
    stale = diagnostics.doctor_payload(root)
    assert stale["installation_ok"] is True
    assert stale["activation_ready"] is False
    assert stale["release_ready"] is False

def test_release_build_persists_final_report_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "exports" / "jazn_latka_v15.0.3.4.zip"

    monkeypatch.setattr(
        "latka_jazn.tools.release_bundle.create_release_staging",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        "latka_jazn.tools.release_bundle.build_release_readiness_report",
        lambda *_args, **_kwargs: {"ok": True, "exit_code": 0},
    )

    def fake_export_package(_root, _mode, candidate):
        candidate = Path(candidate)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"verified-release-fixture")

        package_manifest = candidate.with_name(
            candidate.name + ".package_manifest.json"
        )
        packing_audit = candidate.with_name(
            candidate.name + ".PACKING_AUDIT.json"
        )
        report_path = candidate.with_suffix(".report.json")

        package_manifest.write_text("{}\n", encoding="utf-8")
        packing_audit.write_text("{}\n", encoding="utf-8")

        temporary_report = {
            "output_zip": str(candidate),
            "package_manifest_path": str(package_manifest),
            "packing_audit_path": str(packing_audit),
        }
        report_path.write_text(
            json.dumps(temporary_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()

        return SimpleNamespace(
            to_dict=lambda: {
                **temporary_report,
                "crc_ok": True,
                "extract_smoke_ok": True,
                "sha256": digest,
            }
        )

    monkeypatch.setattr(
        "latka_jazn.tools.release_bundle.export_package",
        fake_export_package,
    )
    monkeypatch.setattr(
        "latka_jazn.tools.release_bundle.verify_release_zip_manifest",
        lambda path: {
            "ok": True,
            "zip_path": str(path),
            "errors": [],
        },
    )

    result = build_release_bundle(tmp_path, output)

    assert result["ok"] is True

    persisted = json.loads(
        Path(result["report_path"]).read_text(encoding="utf-8")
    )

    assert persisted["output_zip"] == result["output_zip"]
    assert (
        persisted["package_manifest_path"]
        == result["package_manifest_path"]
    )
    assert (
        persisted["packing_audit_path"]
        == result["packing_audit_path"]
    )
    assert ".jazn-release-output-" not in json.dumps(persisted)