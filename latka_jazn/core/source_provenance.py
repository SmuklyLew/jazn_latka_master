from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import re
import subprocess

from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version

SCHEMA_VERSION = schema_version("source_provenance_status")
PROVENANCE_FILENAME = "SOURCE_PROVENANCE.json"


@dataclass(slots=True)
class SourceProvenanceStatus:
    schema_version: str
    status: str
    file_path: str | None
    file_sha256: str | None
    repository: str | None
    base_branch: str | None
    base_version: str | None
    base_pull_request: int | None
    base_merge_commit: str | None
    runtime_version: str | None
    version_matches_runtime: bool
    merge_commit_shape_valid: bool
    git_directory_present: bool
    git_tree_sha: str | None
    dirty: bool | None
    manifest_protected: bool
    limitations: list[str]
    truth_boundary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> tuple[int, str, str]:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, stdin=subprocess.DEVNULL, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _manifest_protects_provenance(root: Path, path: Path) -> bool:
    manifest = root / "PACKAGE_INTEGRITY_MANIFEST.json"
    if not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    expected_hash = _sha256_file(path)
    expected_size = path.stat().st_size
    for entry in payload.get("files") or []:
        if not isinstance(entry, dict) or entry.get("path") != PROVENANCE_FILENAME:
            continue
        return entry.get("sha256") == expected_hash and int(entry.get("size_bytes", -1)) == expected_size
    return False


def read_source_provenance(root: Path | str) -> SourceProvenanceStatus:
    root = Path(root).resolve()
    path = root / PROVENANCE_FILENAME
    limitations: list[str] = []
    if not path.is_file():
        return SourceProvenanceStatus(
            schema_version=SCHEMA_VERSION,
            status="missing",
            file_path=None,
            file_sha256=None,
            repository=None,
            base_branch=None,
            base_version=None,
            base_pull_request=None,
            base_merge_commit=None,
            runtime_version=None,
            version_matches_runtime=False,
            merge_commit_shape_valid=False,
            git_directory_present=(root / ".git").exists(),
            git_tree_sha=None,
            dirty=None,
            manifest_protected=False,
            limitations=["SOURCE_PROVENANCE.json is missing"],
            truth_boundary="Without .git or a provenance document, runtime cannot identify its source commit.",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return SourceProvenanceStatus(
            schema_version=SCHEMA_VERSION,
            status="invalid",
            file_path=str(path),
            file_sha256=_sha256_file(path) if path.is_file() else None,
            repository=None,
            base_branch=None,
            base_version=None,
            base_pull_request=None,
            base_merge_commit=None,
            runtime_version=None,
            version_matches_runtime=False,
            merge_commit_shape_valid=False,
            git_directory_present=(root / ".git").exists(),
            git_tree_sha=None,
            dirty=None,
            manifest_protected=False,
            limitations=[f"invalid provenance JSON: {type(exc).__name__}"],
            truth_boundary="Invalid provenance is not accepted as source history.",
        )
    runtime_version = str(payload.get("runtime_version") or "") or None
    merge_commit = str(payload.get("base_merge_commit") or "") or None
    version_matches = runtime_version == PACKAGE_VERSION_FULL
    commit_valid = bool(merge_commit and re.fullmatch(r"[0-9a-fA-F]{40}", merge_commit))
    tree_sha = str(payload.get("git_tree_sha") or "") or None
    tree_shape_valid = bool(tree_sha and re.fullmatch(r"[0-9a-fA-F]{40}", tree_sha))
    declared_dirty = payload.get("dirty") if isinstance(payload.get("dirty"), bool) else None
    git_present = (root / ".git").exists()
    manifest_protected = _manifest_protects_provenance(root, path)
    if not version_matches:
        limitations.append(f"provenance runtime_version={runtime_version!r} differs from active {PACKAGE_VERSION_FULL!r}")
    if not commit_valid:
        limitations.append("base_merge_commit is not a 40-character Git SHA")
    if not tree_shape_valid:
        limitations.append("git_tree_sha is not a 40-character Git SHA")
    status = "invalid"
    if version_matches and commit_valid and tree_shape_valid and git_present:
        commit_rc, _, commit_error = _git(root, "cat-file", "-e", f"{merge_commit}^{{commit}}")
        _, current_tree, _ = _git(root, "rev-parse", f"{merge_commit}^{{tree}}")
        _, actual_status, _ = _git(root, "status", "--porcelain", "--untracked-files=all")
        actual_dirty = bool(actual_status)
        if commit_rc != 0:
            limitations.append(f"base commit does not exist in checkout: {commit_error}")
        if current_tree.lower() != str(tree_sha).lower():
            limitations.append("git_tree_sha does not match base commit")
        if declared_dirty is None or declared_dirty != actual_dirty:
            limitations.append("declared dirty state does not match working tree")
        if not limitations:
            status = "development_dirty_verified" if actual_dirty else "clean_checkout_verified"
    elif version_matches and commit_valid and tree_shape_valid and not git_present:
        limitations.append(".git is not included; local branch, tag and dirty state cannot be independently verified")
        if manifest_protected:
            status = "verified_export_without_git_history"
        else:
            limitations.append("PACKAGE_INTEGRITY_MANIFEST.json does not protect SOURCE_PROVENANCE.json")
    return SourceProvenanceStatus(
        schema_version=SCHEMA_VERSION,
        status=status,
        file_path=str(path),
        file_sha256=_sha256_file(path),
        repository=str(payload.get("repository") or "") or None,
        base_branch=str(payload.get("base_branch") or "") or None,
        base_version=str(payload.get("base_version") or "") or None,
        base_pull_request=int(payload["base_pull_request"]) if payload.get("base_pull_request") is not None else None,
        base_merge_commit=merge_commit,
        runtime_version=runtime_version,
        version_matches_runtime=version_matches,
        merge_commit_shape_valid=commit_valid,
        git_directory_present=git_present,
        git_tree_sha=tree_sha,
        dirty=declared_dirty,
        manifest_protected=manifest_protected,
        limitations=limitations,
        truth_boundary=str(payload.get("truth_boundary") or "Provenance is descriptive unless verified against Git and manifest hashes."),
    )
