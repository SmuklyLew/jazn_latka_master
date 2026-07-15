from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

PACKAGE_INTEGRITY_MANIFEST_NAME = "PACKAGE_INTEGRITY_MANIFEST.json"
LEGACY_PACKAGE_MANIFEST_NAME = "MANIFEST_CURRENT.json"
TRANSITION_POLICY = "primary_only; legacy_alias_is_read_only_migration_signal"


@dataclass(slots=True)
class PackageIntegrityManifestStatus:
    present: bool
    valid_json: bool
    source_name: str | None
    path: str | None
    sha256: str | None
    version: str | None
    primary_present: bool
    legacy_present: bool
    aliases_match: bool | None = None
    runtime_start_blocking: bool = True
    purpose: str = "sole_package_integrity_manifest"
    transition_policy: str = TRANSITION_POLICY

    @property
    def legacy_requires_cleanup(self) -> bool:
        return self.legacy_present

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["legacy_requires_cleanup"] = self.legacy_requires_cleanup
        return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_package_integrity_manifest(root: Path) -> Path | None:
    """Resolve only the canonical package manifest.

    ``MANIFEST_CURRENT.json`` is never selected as a fallback. Its presence is
    reported for cleanup/migration but cannot make preflight trusted.
    """
    primary = Path(root).expanduser().resolve() / PACKAGE_INTEGRITY_MANIFEST_NAME
    return primary if primary.is_file() else None


def package_integrity_manifest_status(root: Path) -> PackageIntegrityManifestStatus:
    root = Path(root).expanduser().resolve()
    primary = root / PACKAGE_INTEGRITY_MANIFEST_NAME
    legacy = root / LEGACY_PACKAGE_MANIFEST_NAME
    if not primary.is_file():
        return PackageIntegrityManifestStatus(
            present=False,
            valid_json=False,
            source_name=None,
            path=None,
            sha256=None,
            version=None,
            primary_present=False,
            legacy_present=legacy.is_file(),
        )
    try:
        payload = json.loads(primary.read_text(encoding="utf-8-sig"))
        valid = isinstance(payload, dict)
    except Exception:
        payload = {}
        valid = False
    return PackageIntegrityManifestStatus(
        present=True,
        valid_json=valid,
        source_name=PACKAGE_INTEGRITY_MANIFEST_NAME,
        path=str(primary),
        sha256=sha256_file(primary),
        version=str(payload.get("version") or payload.get("runtime_version") or "").strip() or None,
        primary_present=True,
        legacy_present=legacy.is_file(),
    )
