from __future__ import annotations

from pathlib import Path
from typing import Any

from latka_jazn.core.package_integrity_manifest import sha256_file
from latka_jazn.tools.package_integrity import write_package_integrity_manifest
from latka_jazn.tools.source_provenance import generate_source_provenance
from latka_jazn.version import schema_version


def generate_release_metadata(root: Path | str, *, allow_dirty: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    provenance = generate_source_provenance(root, allow_dirty=allow_dirty)
    if not provenance.get("ok"):
        return {
            "schema_version": schema_version("release_metadata_generation"),
            "ok": False,
            "exit_code": 2,
            "provenance": provenance,
            "manifest": None,
            "order": ["SOURCE_PROVENANCE.json", "PACKAGE_INTEGRITY_MANIFEST.json"],
        }
    try:
        manifest = write_package_integrity_manifest(root)
    except Exception as exc:
        return {
            "schema_version": schema_version("release_metadata_generation"),
            "ok": False,
            "exit_code": 2,
            "provenance": provenance,
            "manifest": {"error": repr(exc)},
            "order": ["SOURCE_PROVENANCE.json", "PACKAGE_INTEGRITY_MANIFEST.json"],
        }
    return {
        "schema_version": schema_version("release_metadata_generation"),
        "ok": True,
        "exit_code": 0,
        "allow_dirty": allow_dirty,
        "provenance": provenance,
        "manifest": {
            "path": str(root / "PACKAGE_INTEGRITY_MANIFEST.json"),
            "sha256": sha256_file(root / "PACKAGE_INTEGRITY_MANIFEST.json"),
            "file_count": manifest.get("file_count"),
        },
        "order": ["SOURCE_PROVENANCE.json", "PACKAGE_INTEGRITY_MANIFEST.json"],
    }
