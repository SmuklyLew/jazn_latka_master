from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import re

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
            limitations=[f"invalid provenance JSON: {type(exc).__name__}"],
            truth_boundary="Invalid provenance is not accepted as source history.",
        )
    runtime_version = str(payload.get("runtime_version") or "") or None
    merge_commit = str(payload.get("base_merge_commit") or "") or None
    version_matches = runtime_version == PACKAGE_VERSION_FULL
    commit_valid = bool(merge_commit and re.fullmatch(r"[0-9a-fA-F]{40}", merge_commit))
    git_present = (root / ".git").exists()
    if not version_matches:
        limitations.append(f"provenance runtime_version={runtime_version!r} differs from active {PACKAGE_VERSION_FULL!r}")
    if not commit_valid:
        limitations.append("base_merge_commit is not a 40-character Git SHA")
    if not git_present:
        limitations.append(".git is not included; local branch, tag and dirty state cannot be independently verified")
    status = "declared_base_verified_structure"
    if not version_matches or not commit_valid:
        status = "invalid"
    elif not git_present:
        status = "verified_export_without_git_history"
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
        limitations=limitations,
        truth_boundary=str(payload.get("truth_boundary") or "Provenance is descriptive unless verified against Git and manifest hashes."),
    )
