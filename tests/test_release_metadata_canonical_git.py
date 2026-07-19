from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from latka_jazn.core.source_provenance import read_source_provenance
from latka_jazn.tools.release_metadata_sync import (
    METADATA_ONLY_PATHS,
    build_canonical_package_manifest,
    build_release_metadata_documents,
    resolve_release_source_commit,
    write_release_metadata,
)
from latka_jazn.version import DISTRIBUTION_VERSION, PACKAGE_VERSION


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "latka_jazn").mkdir(parents=True)
    (root / "latka_jazn" / "version.py").write_text(
        f"DISTRIBUTION_VERSION = {DISTRIBUTION_VERSION!r}\n"
        f"PACKAGE_VERSION = {PACKAGE_VERSION!r}\n"
        'PACKAGE_RELEASE_NAME = ""\n',
        encoding="utf-8",
        newline="\n",
    )
    (root / ".gitattributes").write_text("*.py text eol=lf\n*.json text eol=lf\n", encoding="utf-8")
    (root / "run.py").write_text("print('run')\n", encoding="utf-8", newline="\n")
    (root / "main.py").write_text("print('main')\n", encoding="utf-8", newline="\n")
    (root / "SOURCE_PROVENANCE.json").write_text("{}\n", encoding="utf-8", newline="\n")
    (root / "PACKAGE_INTEGRITY_MANIFEST.json").write_text("{}\n", encoding="utf-8", newline="\n")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "remote", "add", "origin", "https://github.com/SmuklyLew/jazn_latka.git")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "source")
    return root


def test_canonical_manifest_hashes_git_blob_not_crlf_worktree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = _git(root, "rev-parse", "HEAD")
    lf_bytes = b"print('main')\n"
    crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
    (root / "main.py").write_bytes(crlf_bytes)

    manifest = build_canonical_package_manifest(
        root,
        source_commit=source,
        overrides={"SOURCE_PROVENANCE.json": b"{}\n"},
    )
    entry = next(item for item in manifest["files"] if item["path"] == "main.py")

    assert entry["size_bytes"] == len(lf_bytes)
    assert entry["size_bytes"] != len(crlf_bytes)
    assert entry["sha256"] == hashlib.sha256(lf_bytes).hexdigest()


def test_release_metadata_is_deterministic_after_metadata_only_commit(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source_commit = _git(root, "rev-parse", "HEAD")

    first = build_release_metadata_documents(root, base_branch="master")
    assert first["source_commit"] == source_commit
    write_release_metadata(root, base_branch="master")
    _git(root, "add", "SOURCE_PROVENANCE.json", "PACKAGE_INTEGRITY_MANIFEST.json")
    _git(root, "commit", "-m", "release metadata")

    metadata_head = _git(root, "rev-parse", "HEAD")
    assert metadata_head != source_commit
    assert resolve_release_source_commit(root) == source_commit
    assert set(_git(root, "diff", "--name-only", source_commit, metadata_head).splitlines()) == set(
        METADATA_ONLY_PATHS
    )

    second = build_release_metadata_documents(root, base_branch="master")
    assert second["source_commit"] == source_commit
    assert second["provenance_bytes"] == first["provenance_bytes"]
    assert second["manifest_bytes"] == first["manifest_bytes"]


def test_release_profile_accepts_metadata_only_descendant(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source_commit = _git(root, "rev-parse", "HEAD")
    write_release_metadata(root, base_branch="master")
    _git(root, "add", "SOURCE_PROVENANCE.json", "PACKAGE_INTEGRITY_MANIFEST.json")
    _git(root, "commit", "-m", "release metadata")

    status = read_source_provenance(root, profile="release")

    assert status.status == "clean_checkout_verified"
    assert status.limitations == []
    assert status.base_merge_commit == source_commit
    assert status.commit_matches_head is False
    assert status.tree_matches_commit is True
    assert status.generation_mode == "release_metadata"
    assert status.manifest_protected is True


def test_code_change_after_metadata_commit_invalidates_provenance(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    write_release_metadata(root, base_branch="master")
    _git(root, "add", "SOURCE_PROVENANCE.json", "PACKAGE_INTEGRITY_MANIFEST.json")
    _git(root, "commit", "-m", "release metadata")

    (root / "main.py").write_text("print('changed')\n", encoding="utf-8", newline="\n")
    _git(root, "add", "main.py")
    _git(root, "commit", "-m", "code change")

    status = read_source_provenance(root, profile="release")

    assert status.status == "invalid"
    assert any("current HEAD" in item for item in status.limitations)


def test_written_provenance_is_current_v151_and_manifest_protected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    write_release_metadata(root, base_branch="master")
    payload = json.loads((root / "SOURCE_PROVENANCE.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "PACKAGE_INTEGRITY_MANIFEST.json").read_text(encoding="utf-8"))

    assert payload["runtime_version"] == PACKAGE_VERSION
    assert payload["base_version"] == PACKAGE_VERSION
    assert payload["generation_mode"] == "release_metadata"
    assert payload["dirty"] is False
    assert payload["base_branch"] == "master"
    assert set(payload["metadata_only_paths"]) == set(METADATA_ONLY_PATHS)
    assert "SOURCE_PROVENANCE.json" in {item["path"] for item in manifest["files"]}
