from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import shutil
import subprocess

from latka_jazn.core.source_provenance import read_source_provenance
from latka_jazn.tools.package_integrity import (
    build_package_integrity_manifest,
    verify_package_integrity_manifest,
    write_package_integrity_manifest,
)
from latka_jazn.tools.safe_paths import resolve_safe_destination, resolve_safe_source, validate_safe_relative_path
from latka_jazn.tools.source_provenance import (
    SourceProvenanceError,
    build_source_provenance_document,
)
from latka_jazn.version import schema_version


def _git(root: Path, *args: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace") if binary else completed.stderr
        raise SourceProvenanceError(f"git {' '.join(args)} failed ({completed.returncode}): {stderr.strip()}")
    return completed.stdout


def _fresh_destination(destination: Path) -> Path:
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise SourceProvenanceError("release staging destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy_manifest_files(root: Path, destination: Path, manifest: dict[str, Any]) -> None:
    """Copy only manifest-listed files after source and destination containment proofs."""

    root = Path(root).resolve()
    destination = Path(destination).resolve()
    for entry in manifest.get("files") or []:
        relative = validate_safe_relative_path(str((entry or {}).get("path") or ""))
        source = resolve_safe_source(root, relative)
        target = resolve_safe_destination(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _verified_export_manifest(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    verification = verify_package_integrity_manifest(root)
    if not verification.get("ok"):
        raise SourceProvenanceError(
            "package without .git requires a valid PACKAGE_INTEGRITY_MANIFEST.json"
        )
    try:
        manifest = json.loads((root / "PACKAGE_INTEGRITY_MANIFEST.json").read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise SourceProvenanceError(f"cannot read verified package manifest: {exc!r}") from exc
    if not isinstance(manifest, dict):
        raise SourceProvenanceError("verified package manifest is not a JSON object")
    return manifest, verification


def create_system_smoke_staging(root: Path | str, destination: Path | str) -> dict[str, Any]:
    """Create an isolated, truthful snapshot of a checkout or verified export.

    A source checkout derives provenance from real Git objects.  A package
    without ``.git`` is accepted only when its existing manifest verifies and
    protects a valid release provenance document.  The latter path never
    invents a commit or dirty state.
    """

    root = Path(root).resolve()
    destination = _fresh_destination(Path(destination))
    if (root / ".git").exists():
        source_kind = "git_checkout"
        source_manifest = build_package_integrity_manifest(root)
        copy_manifest_files(root, destination, source_manifest)
        provenance = build_source_provenance_document(root, allow_dirty=True, write=False)
        _write_json(destination / "SOURCE_PROVENANCE.json", provenance)
        source_verification: dict[str, Any] | None = None
    else:
        source_kind = "verified_export_without_git"
        source_manifest, source_verification = _verified_export_manifest(root)
        source_status = read_source_provenance(root, profile="system_smoke").to_dict()
        if source_status.get("status") != "verified_export_without_git_history":
            raise SourceProvenanceError(
                "package without .git requires manifest-protected release provenance"
            )
        copy_manifest_files(root, destination, source_manifest)
        provenance = json.loads((root / "SOURCE_PROVENANCE.json").read_text(encoding="utf-8"))

    manifest = write_package_integrity_manifest(destination)
    verification = verify_package_integrity_manifest(destination)
    provenance_status = read_source_provenance(destination, profile="system_smoke").to_dict()
    if not verification.get("ok") or provenance_status.get("status") != "verified_export_without_git_history":
        raise SourceProvenanceError("system smoke staging verification failed")
    return {
        "schema_version": schema_version("system_smoke_staging"),
        "ok": True,
        "staging_root": str(destination),
        "source_kind": source_kind,
        "source_dirty": provenance.get("dirty"),
        "source_commit": provenance.get("base_merge_commit"),
        "source_manifest_verification": source_verification,
        "manifest_file_count": manifest.get("file_count"),
        "manifest_verification": verification,
        "provenance": provenance_status,
    }


def create_release_staging(
    root: Path | str,
    destination: Path | str,
    *,
    commit: str = "HEAD",
) -> dict[str, Any]:
    """Materialize one clean Git commit, then generate provenance and manifest."""

    root = Path(root).resolve()
    destination = _fresh_destination(Path(destination))
    provenance = build_source_provenance_document(root, allow_dirty=False, write=False)
    head = str(_git(root, "rev-parse", "HEAD")).strip().lower()
    selected = str(_git(root, "rev-parse", commit)).strip().lower()
    if selected != head or provenance.get("base_merge_commit") != head:
        raise SourceProvenanceError("release staging commit must match the clean current HEAD")

    raw_listing = _git(root, "ls-tree", "-rz", selected, binary=True)
    assert isinstance(raw_listing, bytes)
    file_count = 0
    for raw_record in raw_listing.split(b"\x00"):
        if not raw_record:
            continue
        metadata, raw_path = raw_record.split(b"\t", 1)
        mode, object_type, object_sha = metadata.decode("ascii").split(" ", 2)
        relative = validate_safe_relative_path(raw_path.decode("utf-8", "strict"))
        if object_type != "blob" or mode == "120000":
            raise SourceProvenanceError(f"release staging rejects non-regular Git entry: {relative}")
        target = resolve_safe_destination(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = _git(root, "cat-file", "blob", object_sha, binary=True)
        assert isinstance(blob, bytes)
        target.write_bytes(blob)
        file_count += 1

    _write_json(destination / "SOURCE_PROVENANCE.json", provenance)
    manifest = write_package_integrity_manifest(destination)
    verification = verify_package_integrity_manifest(destination)
    provenance_status = read_source_provenance(destination, profile="export_without_git").to_dict()
    if not verification.get("ok") or provenance_status.get("status") != "verified_export_without_git_history":
        raise SourceProvenanceError("release staging verification failed")
    return {
        "schema_version": schema_version("release_staging"),
        "ok": True,
        "staging_root": str(destination),
        "source_commit": selected,
        "source_tree": provenance.get("git_tree_sha"),
        "tracked_file_count": file_count,
        "manifest_file_count": manifest.get("file_count"),
        "manifest_verification": verification,
        "provenance": provenance_status,
        "status": "verified_export_without_git_history",
    }
