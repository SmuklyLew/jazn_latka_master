from __future__ import annotations

from pathlib import Path
from typing import Any

from latka_jazn.core.package_integrity_manifest import sha256_file
from latka_jazn.tools.release_metadata_sync import write_release_metadata
from latka_jazn.version import schema_version


def generate_release_metadata(root: Path | str, *, allow_dirty: bool = False) -> dict[str, Any]:
    """Generate deterministic, Git-object-backed release metadata.

    ``allow_dirty`` is retained for CLI compatibility, but release metadata is
    intentionally never generated from a dirty checkout. A dirty worktree has
    no immutable Git object graph from which a trustworthy package manifest can
    be derived.
    """

    root = Path(root).resolve()
    if allow_dirty:
        return {
            "schema_version": schema_version("release_metadata_generation"),
            "ok": False,
            "exit_code": 2,
            "provenance": {
                "ok": False,
                "error": "deterministic release metadata rejects allow_dirty=True",
            },
            "manifest": None,
            "order": ["SOURCE_PROVENANCE.json", "PACKAGE_INTEGRITY_MANIFEST.json"],
        }
    try:
        report = write_release_metadata(root)
    except Exception as exc:
        return {
            "schema_version": schema_version("release_metadata_generation"),
            "ok": False,
            "exit_code": 2,
            "provenance": {"ok": False, "error": repr(exc)},
            "manifest": None,
            "order": ["SOURCE_PROVENANCE.json", "PACKAGE_INTEGRITY_MANIFEST.json"],
        }
    return {
        "schema_version": schema_version("release_metadata_generation"),
        "ok": True,
        "exit_code": 0,
        "allow_dirty": False,
        "source_commit": report.get("source_commit"),
        "source_tree": report.get("source_tree"),
        "provenance": {
            "ok": True,
            "path": str(root / "SOURCE_PROVENANCE.json"),
            "document": report.get("provenance"),
        },
        "manifest": {
            "path": str(root / "PACKAGE_INTEGRITY_MANIFEST.json"),
            "sha256": sha256_file(root / "PACKAGE_INTEGRITY_MANIFEST.json"),
            "file_count": (report.get("manifest") or {}).get("file_count"),
        },
        "order": ["SOURCE_PROVENANCE.json", "PACKAGE_INTEGRITY_MANIFEST.json"],
    }
