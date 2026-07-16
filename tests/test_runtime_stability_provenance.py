from __future__ import annotations

from pathlib import Path
import json
import shutil
import subprocess

import pytest

from latka_jazn.core.source_provenance import read_source_provenance
from latka_jazn.tools.package_integrity import verify_package_integrity_manifest, write_package_integrity_manifest
from latka_jazn.tools.source_provenance import (
    SourceProvenanceError,
    build_source_provenance_document,
    generate_source_provenance,
)
from latka_jazn.tools.active_extraction_cache import build_active_runtime_status


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, stdin=subprocess.DEVNULL, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "latka_jazn").mkdir(parents=True)
    (root / "latka_jazn" / "version.py").write_text(
        'DISTRIBUTION_VERSION = "15.0.3.2"\nPACKAGE_VERSION = "v15.0.3.2"\nPACKAGE_RELEASE_NAME = ""\n',
        encoding="utf-8",
    )
    (root / "run.py").write_text("print('run')\n", encoding="utf-8")
    (root / "main.py").write_text("print('main')\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "remote", "add", "origin", "https://github.com/SmuklyLew/jazn_latka.git")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root


def test_clean_checkout_provenance_uses_real_git_objects(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    payload = build_source_provenance_document(root, write=False)
    assert payload["dirty"] is False
    assert len(payload["base_merge_commit"]) == 40
    assert _git(root, "cat-file", "-t", payload["base_merge_commit"]) == "commit"
    assert payload["repository"] == "SmuklyLew/jazn_latka"


def test_dirty_checkout_is_blocked_or_truthfully_declared(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "main.py").write_text("print('dirty')\n", encoding="utf-8")
    with pytest.raises(SourceProvenanceError, match="clean working tree"):
        build_source_provenance_document(root)
    payload = build_source_provenance_document(root, allow_dirty=True)
    assert payload["dirty"] is True
    assert payload["generation_mode"] == "development_preview"


def test_missing_git_never_invents_sha(tmp_path: Path) -> None:
    (tmp_path / "latka_jazn").mkdir()
    (tmp_path / "latka_jazn" / "version.py").write_text(
        'DISTRIBUTION_VERSION="15.0.3.2"\nPACKAGE_VERSION="v15.0.3.2"\n', encoding="utf-8"
    )
    report = generate_source_provenance(tmp_path, allow_dirty=True)
    assert report["ok"] is False
    assert report["exit_code"] == 2
    assert not (tmp_path / "SOURCE_PROVENANCE.json").exists()


def test_bad_sha_and_version_mismatch_are_invalid(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    payload = build_source_provenance_document(root)
    payload["base_merge_commit"] = "bad"
    (root / "SOURCE_PROVENANCE.json").write_text(json.dumps(payload), encoding="utf-8")
    assert read_source_provenance(root).status == "invalid"

    payload = build_source_provenance_document(root, allow_dirty=True)
    payload["runtime_version"] = "v0.0.0"
    (root / "SOURCE_PROVENANCE.json").write_text(json.dumps(payload), encoding="utf-8")
    assert read_source_provenance(root).status == "invalid"


def test_manifest_detects_file_changed_after_generation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    build_source_provenance_document(root, allow_dirty=True, write=True)
    write_package_integrity_manifest(root)
    assert verify_package_integrity_manifest(root)["ok"] is True
    (root / "main.py").write_text("tampered\n", encoding="utf-8")
    report = verify_package_integrity_manifest(root)
    assert report["ok"] is False
    assert any(item["code"] == "sha256_mismatch" and item["path"] == "main.py" for item in report["errors"])


def test_export_without_git_is_verified_only_through_manifest(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "development.txt").write_text("dirty\n", encoding="utf-8")
    build_source_provenance_document(root, allow_dirty=True, write=True)
    manifest = write_package_integrity_manifest(root)
    export = tmp_path / "export"
    export.mkdir()
    for entry in manifest["files"]:
        source = root / entry["path"]
        target = export / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(root / "PACKAGE_INTEGRITY_MANIFEST.json", export / "PACKAGE_INTEGRITY_MANIFEST.json")
    status = read_source_provenance(export)
    assert status.status == "verified_export_without_git_history"
    assert status.manifest_protected is True
    assert any("cannot be independently verified" in item for item in status.limitations)
    marker = build_active_runtime_status(export, marker_output=export / "workspace_runtime" / "marker.json")
    assert marker["source_provenance_status"] == "verified_export_without_git_history"
    assert marker["source_provenance_sha256"] == status.file_sha256
    assert marker["source_base_commit"] == status.base_merge_commit
    assert marker["source_provenance_without_git_history_restriction"]


def test_provenance_is_manifest_protected_and_forbidden_paths_are_absent(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "workspace_runtime").mkdir()
    (root / "workspace_runtime" / "runtime.log").write_text("secret runtime", encoding="utf-8")
    (root / "memory").mkdir()
    (root / "memory" / "private.sqlite3").write_bytes(b"private")
    build_source_provenance_document(root, allow_dirty=True, write=True)
    manifest = write_package_integrity_manifest(root)
    paths = {entry["path"] for entry in manifest["files"]}
    assert "SOURCE_PROVENANCE.json" in paths
    assert not any(path.startswith(("memory/", "workspace_runtime/")) for path in paths)
