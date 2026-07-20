from __future__ import annotations

from pathlib import Path
import hashlib
import json
import shutil
import subprocess

from latka_jazn.tools.package_integrity import verify_package_integrity_manifest
from latka_jazn.version import DISTRIBUTION_VERSION, PACKAGE_VERSION


_REQUIRED = (
    "SOURCE_PROVENANCE.json",
    "run.py",
    "main.py",
    "latka_jazn/version.py",
)


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


def _git_bytes(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return completed.stdout


def _release_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "latka_jazn").mkdir(parents=True)
    (root / "latka_jazn" / "version.py").write_text(
        f"DISTRIBUTION_VERSION = {DISTRIBUTION_VERSION!r}\n"
        f"PACKAGE_VERSION = {PACKAGE_VERSION!r}\n"
        'PACKAGE_RELEASE_NAME = ""\n',
        encoding="utf-8",
        newline="\n",
    )
    (root / ".gitattributes").write_text(
        "*.py text eol=lf\n*.json text eol=lf\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "run.py").write_text("print('run')\n", encoding="utf-8", newline="\n")
    (root / "main.py").write_text("print('main')\n", encoding="utf-8", newline="\n")
    (root / "SOURCE_PROVENANCE.json").write_text("{}\n", encoding="utf-8", newline="\n")

    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "source")

    entries = []
    for relative in _REQUIRED:
        raw = _git_bytes(root, "show", f"HEAD:{relative}")
        entries.append(
            {
                "path": relative,
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    manifest = {
        "version": PACKAGE_VERSION,
        "runtime_version": PACKAGE_VERSION,
        "files": entries,
    }
    (root / "PACKAGE_INTEGRITY_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(root, "add", "PACKAGE_INTEGRITY_MANIFEST.json")
    _git(root, "commit", "-m", "release manifest")
    assert subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", "--"],
        check=False,
    ).returncode == 0
    return root


def test_clean_checkout_verifies_canonical_blob_despite_crlf_worktree(tmp_path: Path) -> None:
    root = _release_repo(tmp_path)
    lf_bytes = b"print('main')\n"
    (root / "main.py").write_bytes(lf_bytes.replace(b"\n", b"\r\n"))

    assert subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", "--"],
        check=False,
    ).returncode == 0

    report = verify_package_integrity_manifest(root)

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["verification_basis"] == "canonical_git_head_blobs"
    assert report["worktree_state"] == "clean"
    assert report["git_head"] == _git(root, "rev-parse", "HEAD")


def test_dirty_checkout_falls_back_to_filesystem_and_detects_tampering(tmp_path: Path) -> None:
    root = _release_repo(tmp_path)
    (root / "main.py").write_text("print('tampered')\n", encoding="utf-8", newline="\n")

    report = verify_package_integrity_manifest(root)

    assert report["ok"] is False
    assert report["verification_basis"] == "filesystem_bytes"
    assert report["worktree_state"] == "dirty"
    assert any(
        item.get("code") == "sha256_mismatch" and item.get("path") == "main.py"
        for item in report["errors"]
    )


def test_unpacked_tree_without_git_verifies_raw_filesystem_bytes(tmp_path: Path) -> None:
    root = _release_repo(tmp_path)
    exported = tmp_path / "exported"
    shutil.copytree(root, exported, ignore=shutil.ignore_patterns(".git"))
    (exported / "main.py").write_bytes(b"print('main')\r\n")

    report = verify_package_integrity_manifest(exported)

    assert report["ok"] is False
    assert report["verification_basis"] == "filesystem_bytes"
    assert report["worktree_state"] == "not_a_git_checkout"
    assert any(
        item.get("code") == "sha256_mismatch" and item.get("path") == "main.py"
        for item in report["errors"]
    )
