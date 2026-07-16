from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re
import subprocess

from latka_jazn.core.version_source import read_runtime_version_from_version_py
from latka_jazn.version import schema_version

PROVENANCE_FILENAME = "SOURCE_PROVENANCE.json"


class SourceProvenanceError(RuntimeError):
    pass


def _git(root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and completed.returncode != 0:
        raise SourceProvenanceError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _repository_name(remote_url: str) -> str:
    value = remote_url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if ":" in value and not value.startswith(("http://", "https://", "ssh://")):
        value = value.split(":", 1)[1]
    elif "/" in value:
        parts = value.split("/")
        value = "/".join(parts[-2:])
    return value


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def build_source_provenance_document(
    root: Path | str,
    *,
    allow_dirty: bool = False,
    write: bool = False,
) -> dict[str, Any]:
    root = Path(root).resolve()
    runtime_version = read_runtime_version_from_version_py(root)
    if not runtime_version:
        raise SourceProvenanceError("latka_jazn/version.py is missing or invalid")
    if not (root / ".git").exists():
        raise SourceProvenanceError("Git metadata is unavailable; source commit must not be invented")
    head = _git(root, "rev-parse", "HEAD").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise SourceProvenanceError("HEAD is not a full 40-character Git SHA")
    _git(root, "cat-file", "-e", f"{head}^{{commit}}")
    tree = _git(root, "rev-parse", f"{head}^{{tree}}").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise SourceProvenanceError("Git tree is not a full 40-character SHA")
    branch = _git(root, "branch", "--show-current") or "detached"
    remote_url = _git(root, "remote", "get-url", "origin")
    if not remote_url:
        raise SourceProvenanceError("origin remote URL is missing")
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=all"))
    if dirty and not allow_dirty:
        raise SourceProvenanceError("release provenance requires a clean working tree")
    tags = [line for line in _git(root, "tag", "--points-at", head).splitlines() if line.strip()]
    commit_date = _git(root, "show", "-s", "--format=%cI", head)
    payload = {
        "schema_version": schema_version("source_provenance"),
        "repository": _repository_name(remote_url),
        "remote_url": remote_url,
        "base_branch": branch,
        "base_merge_commit": head,
        "git_tree_sha": tree,
        "base_version": runtime_version,
        "runtime_version": runtime_version,
        "update_version": runtime_version,
        "version_source": "latka_jazn/version.py",
        "dirty": dirty,
        "tag": tags[0] if tags else None,
        "commit_date": commit_date,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generation_mode": "development_preview" if dirty else "release",
        "truth_boundary": (
            "Git fields were read from this checkout. dirty=true is never promoted to release-clean. "
            "An export without .git can verify this document only through PACKAGE_INTEGRITY_MANIFEST.json; "
            "branch, tag and dirty state cannot then be independently rechecked."
        ),
    }
    if write:
        _write_json_atomic(root / PROVENANCE_FILENAME, payload)
    return payload


def generate_source_provenance(root: Path | str, *, allow_dirty: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    try:
        payload = build_source_provenance_document(root, allow_dirty=allow_dirty, write=True)
        return {
            "schema_version": schema_version("source_provenance_generation_report"),
            "ok": True,
            "exit_code": 0,
            "path": str(root / PROVENANCE_FILENAME),
            "dirty": payload["dirty"],
            "base_merge_commit": payload["base_merge_commit"],
            "document": payload,
        }
    except SourceProvenanceError as exc:
        return {
            "schema_version": schema_version("source_provenance_generation_report"),
            "ok": False,
            "exit_code": 2,
            "path": str(root / PROVENANCE_FILENAME),
            "error": str(exc),
        }
