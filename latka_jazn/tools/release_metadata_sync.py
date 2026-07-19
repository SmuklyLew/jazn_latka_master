from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Mapping

from latka_jazn.core.version_source import read_runtime_version_from_version_py
from latka_jazn.tools.package_integrity import (
    FORBIDDEN_FILE_NAMES,
    FORBIDDEN_ROOT_NAMES,
    FORBIDDEN_SUFFIXES,
    MANIFEST_NAME,
    REQUIRED_STATIC_PATHS,
    path_is_forbidden,
    serialize_package_integrity_manifest,
)
from latka_jazn.tools.safe_paths import validate_safe_relative_path
from latka_jazn.version import schema_version

PROVENANCE_NAME = "SOURCE_PROVENANCE.json"
METADATA_ONLY_PATHS = frozenset({PROVENANCE_NAME, MANIFEST_NAME})
_RELEASE_MODE = "release_metadata"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseMetadataSyncError(RuntimeError):
    pass


def _git(
    root: Path,
    *args: str,
    binary: bool = False,
    check: bool = True,
) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace") if binary else completed.stderr
        raise ReleaseMetadataSyncError(
            f"git {' '.join(args)} failed ({completed.returncode}): {str(stderr).strip()}"
        )
    return completed.stdout


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _clean_worktree(root: Path) -> bool:
    status = str(_git(root, "status", "--porcelain", "--untracked-files=all"))
    return not status.strip()


def _commit_exists(root: Path, commit: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"],
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _diff_paths(root: Path, base: str, head: str) -> set[str]:
    raw = _git(root, "diff", "--name-only", "-z", f"{base}..{head}", binary=True)
    assert isinstance(raw, bytes)
    paths: set[str] = set()
    for item in raw.split(b"\0"):
        if not item:
            continue
        paths.add(validate_safe_relative_path(item.decode("utf-8", "strict")))
    return paths


def _source_tree(root: Path, commit: str) -> str:
    tree = str(_git(root, "rev-parse", f"{commit}^{{tree}}" )).strip().lower()
    if not _SHA_RE.fullmatch(tree):
        raise ReleaseMetadataSyncError("resolved source tree is not a 40-character Git SHA")
    return tree


def resolve_release_source_commit(root: Path | str) -> str:
    """Resolve the immutable code/content commit described by release metadata.

    A metadata synchronization commit changes only SOURCE_PROVENANCE.json and
    PACKAGE_INTEGRITY_MANIFEST.json. Such a commit cannot truthfully point at
    itself because changing the provenance changes the commit SHA. The stable
    contract therefore records the nearest code/content commit and allows only
    metadata-only descendants of that commit.
    """

    root = Path(root).resolve()
    head = str(_git(root, "rev-parse", "HEAD")).strip().lower()
    if not _SHA_RE.fullmatch(head):
        raise ReleaseMetadataSyncError("HEAD is not a 40-character Git SHA")

    payload = _read_json(root / PROVENANCE_NAME)
    if not payload or payload.get("generation_mode") != _RELEASE_MODE:
        return head

    candidate = str(payload.get("base_merge_commit") or "").strip().lower()
    tree = str(payload.get("git_tree_sha") or "").strip().lower()
    if not _SHA_RE.fullmatch(candidate) or not _SHA_RE.fullmatch(tree):
        return head
    if not _commit_exists(root, candidate) or not _is_ancestor(root, candidate, head):
        return head
    if _source_tree(root, candidate) != tree:
        return head
    if not _diff_paths(root, candidate, head).issubset(METADATA_ONLY_PATHS):
        return head
    return candidate


def _repository_name(remote_url: str) -> str:
    value = remote_url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if ":" in value and not value.startswith(("http://", "https://", "ssh://")):
        value = value.split(":", 1)[1]
    elif "/" in value:
        value = "/".join(value.split("/")[-2:])
    return value


def _canonical_base_branch(root: Path, explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    env_value = os.environ.get("JAZN_RELEASE_BASE_BRANCH", "").strip()
    if env_value:
        return env_value
    symbolic = str(
        _git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
    ).strip()
    if symbolic.startswith("origin/"):
        return symbolic.split("/", 1)[1]
    return "master"


def _commit_timestamp_utc(root: Path, commit: str) -> str:
    value = str(_git(root, "show", "-s", "--format=%cI", commit)).strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseMetadataSyncError(f"invalid Git commit timestamp: {value!r}") from exc
    return parsed.astimezone(timezone.utc).isoformat()


def build_release_provenance_document(
    root: Path | str,
    *,
    source_commit: str | None = None,
    base_branch: str | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    if not (root / ".git").exists():
        raise ReleaseMetadataSyncError("release metadata synchronization requires Git metadata")
    if not _clean_worktree(root):
        raise ReleaseMetadataSyncError("release metadata synchronization requires a clean working tree")

    runtime_version = read_runtime_version_from_version_py(root)
    if not runtime_version:
        raise ReleaseMetadataSyncError("latka_jazn/version.py is missing or invalid")

    source = (source_commit or resolve_release_source_commit(root)).strip().lower()
    if not _SHA_RE.fullmatch(source) or not _commit_exists(root, source):
        raise ReleaseMetadataSyncError("release source commit is missing or invalid")

    remote_url = str(_git(root, "remote", "get-url", "origin")).strip()
    if not remote_url:
        raise ReleaseMetadataSyncError("origin remote URL is missing")

    tags = [
        line.strip()
        for line in str(_git(root, "tag", "--points-at", source)).splitlines()
        if line.strip()
    ]
    generated_at = _commit_timestamp_utc(root, source)
    return {
        "schema_version": schema_version("source_provenance"),
        "repository": _repository_name(remote_url),
        "remote_url": remote_url,
        "base_branch": _canonical_base_branch(root, base_branch),
        "base_merge_commit": source,
        "git_tree_sha": _source_tree(root, source),
        "base_version": runtime_version,
        "runtime_version": runtime_version,
        "update_version": runtime_version,
        "version_source": "latka_jazn/version.py",
        "dirty": False,
        "tag": tags[0] if tags else None,
        "commit_date": str(_git(root, "show", "-s", "--format=%cI", source)).strip(),
        "generated_at_utc": generated_at,
        "generation_mode": _RELEASE_MODE,
        "metadata_only_paths": sorted(METADATA_ONLY_PATHS),
        "source_commit_policy": (
            "base_merge_commit identifies the immutable code/content commit; current HEAD may be "
            "that commit or a descendant changing only release metadata files"
        ),
        "truth_boundary": (
            "Git fields were derived from canonical Git objects. The provenance records the immutable "
            "code/content commit rather than pretending a self-referential metadata commit can name itself. "
            "Only descendants changing SOURCE_PROVENANCE.json and PACKAGE_INTEGRITY_MANIFEST.json remain valid."
        ),
    }


def _git_tree_blobs(root: Path, commit: str) -> dict[str, tuple[str, str]]:
    raw = _git(root, "ls-tree", "-rz", "--full-tree", "-r", commit, binary=True)
    assert isinstance(raw, bytes)
    result: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, object_sha = metadata.decode("ascii").split(" ", 2)
        relative = validate_safe_relative_path(raw_path.decode("utf-8", "strict"))
        if object_type != "blob" or mode == "120000":
            raise ReleaseMetadataSyncError(f"release manifest rejects non-regular Git entry: {relative}")
        result[relative] = (mode, object_sha)
    return result


def _git_blob(root: Path, object_sha: str) -> bytes:
    raw = _git(root, "cat-file", "blob", object_sha, binary=True)
    assert isinstance(raw, bytes)
    return raw


def build_canonical_package_manifest(
    root: Path | str,
    *,
    source_commit: str,
    overrides: Mapping[str, bytes] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a package manifest from canonical Git blobs, never worktree EOLs."""

    root = Path(root).resolve()
    runtime_version = read_runtime_version_from_version_py(root)
    if not runtime_version:
        raise ReleaseMetadataSyncError("latka_jazn/version.py is missing or invalid")

    override_map = {
        validate_safe_relative_path(path): bytes(raw)
        for path, raw in dict(overrides or {}).items()
    }
    blobs = _git_tree_blobs(root, source_commit)
    candidates = sorted(set(blobs) | set(override_map))
    files: list[dict[str, Any]] = []
    excluded: list[str] = []

    for relative in candidates:
        if path_is_forbidden(relative):
            excluded.append(relative)
            continue
        if relative in override_map:
            raw = override_map[relative]
        else:
            raw = _git_blob(root, blobs[relative][1])
        files.append(
            {
                "path": relative,
                "size_bytes": len(raw),
                "sha256": _sha256_bytes(raw),
                "mutable_runtime": False,
                "classification": "static_project_file",
                "archive": False,
                "hash_policy": "sha256_file_bytes",
            }
        )

    present = {entry["path"] for entry in files}
    missing_required = sorted(REQUIRED_STATIC_PATHS - present)
    if missing_required:
        raise ReleaseMetadataSyncError(f"required static files missing: {missing_required}")

    generated_at = generated_at_utc or datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": schema_version("package_integrity_manifest"),
        "version": runtime_version,
        "runtime_version": runtime_version,
        "package_version": runtime_version,
        "generated_at_utc": generated_at,
        "updated_at_utc": generated_at,
        "start_file": "run.py",
        "file_count": len(files),
        "static_file_count": len(files),
        "mutable_runtime_file_count": 0,
        "runtime_mutable_file_count": 0,
        "excluded_file_count": len(excluded),
        "runtime_state_file": "RUNTIME_STATE.json",
        "runtime_memory_split_policy": {
            "static_manifest": "PACKAGE_INTEGRITY_MANIFEST.json protects static project files only.",
            "runtime_state": "Runtime state, memory, SQLite and workspace_runtime are excluded.",
        },
        "excluded_policy": {
            "roots": sorted(FORBIDDEN_ROOT_NAMES),
            "file_names": sorted(FORBIDDEN_FILE_NAMES),
            "suffixes": sorted(FORBIDDEN_SUFFIXES),
        },
        "truth_boundary": (
            "The manifest hashes the exact static package plan including SOURCE_PROVENANCE.json. "
            "It excludes itself, Git history, memory, runtime state, SQLite, archives, secrets, logs, "
            "backups, generator state and temporary files."
        ),
        "files": files,
        "excluded_files": excluded,
        "deferred_hash_files": [],
    }


def build_release_metadata_documents(
    root: Path | str,
    *,
    base_branch: str | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    source_commit = resolve_release_source_commit(root)
    provenance = build_release_provenance_document(
        root,
        source_commit=source_commit,
        base_branch=base_branch,
    )
    provenance_bytes = _json_bytes(provenance)
    manifest = build_canonical_package_manifest(
        root,
        source_commit=source_commit,
        overrides={PROVENANCE_NAME: provenance_bytes},
        generated_at_utc=str(provenance["generated_at_utc"]),
    )
    manifest_bytes = serialize_package_integrity_manifest(manifest)
    return {
        "schema_version": schema_version("release_metadata_sync"),
        "ok": True,
        "source_commit": source_commit,
        "source_tree": provenance["git_tree_sha"],
        "base_branch": provenance["base_branch"],
        "provenance": provenance,
        "manifest": manifest,
        "provenance_bytes": provenance_bytes,
        "manifest_bytes": manifest_bytes,
    }


def _write_atomic(path: Path, raw: bytes) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(raw)
    temp.replace(path)


def write_release_metadata(
    root: Path | str,
    *,
    base_branch: str | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    documents = build_release_metadata_documents(root, base_branch=base_branch)
    _write_atomic(root / PROVENANCE_NAME, documents["provenance_bytes"])
    _write_atomic(root / MANIFEST_NAME, documents["manifest_bytes"])
    return {
        key: value
        for key, value in documents.items()
        if key not in {"provenance_bytes", "manifest_bytes"}
    }


def check_release_metadata(
    root: Path | str,
    *,
    base_branch: str | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    documents = build_release_metadata_documents(root, base_branch=base_branch)
    current_provenance = (root / PROVENANCE_NAME).read_bytes() if (root / PROVENANCE_NAME).is_file() else None
    current_manifest = (root / MANIFEST_NAME).read_bytes() if (root / MANIFEST_NAME).is_file() else None
    synchronized = (
        current_provenance == documents["provenance_bytes"]
        and current_manifest == documents["manifest_bytes"]
    )
    return {
        "schema_version": schema_version("release_metadata_sync_check"),
        "ok": synchronized,
        "synchronized": synchronized,
        "source_commit": documents["source_commit"],
        "source_tree": documents["source_tree"],
        "base_branch": documents["base_branch"],
        "provenance_matches": current_provenance == documents["provenance_bytes"],
        "manifest_matches": current_manifest == documents["manifest_bytes"],
        "file_count": documents["manifest"]["file_count"],
    }


def _json_safe(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"provenance_bytes", "manifest_bytes"}
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize deterministic release provenance and manifest")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base-branch", default=None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    ns = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if ns.write:
            payload = write_release_metadata(ns.root, base_branch=ns.base_branch)
            exit_code = 0
        else:
            payload = check_release_metadata(ns.root, base_branch=ns.base_branch)
            exit_code = 0 if payload.get("ok") else 2
    except Exception as exc:
        payload = {
            "schema_version": schema_version("release_metadata_sync_error"),
            "ok": False,
            "error": repr(exc),
        }
        exit_code = 2

    safe_payload = _json_safe(payload)
    if ns.as_json:
        print(json.dumps(safe_payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print(safe_payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
