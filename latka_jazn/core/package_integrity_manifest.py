from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from latka_jazn.version import PACKAGE_VERSION

PACKAGE_INTEGRITY_MANIFEST_NAME = "PACKAGE_INTEGRITY_MANIFEST.json"
LEGACY_PACKAGE_MANIFEST_NAME = "MANIFEST_CURRENT.json"
TRANSITION_POLICY = f"primary_plus_legacy_alias_for_{PACKAGE_VERSION}"


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
    aliases_match: bool | None
    runtime_start_blocking: bool = False
    purpose: str = "package_and_release_integrity_only"
    transition_policy: str = TRANSITION_POLICY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_package_integrity_manifest(root: Path) -> Path | None:
    root = Path(root).resolve()
    primary = root / PACKAGE_INTEGRITY_MANIFEST_NAME
    legacy = root / LEGACY_PACKAGE_MANIFEST_NAME
    if primary.is_file():
        return primary
    if legacy.is_file():
        return legacy
    return None


def package_integrity_manifest_status(root: Path) -> PackageIntegrityManifestStatus:
    root = Path(root).resolve()
    primary = root / PACKAGE_INTEGRITY_MANIFEST_NAME
    legacy = root / LEGACY_PACKAGE_MANIFEST_NAME
    selected = resolve_package_integrity_manifest(root)
    aliases_match: bool | None = None
    if primary.is_file() and legacy.is_file():
        aliases_match = primary.read_bytes() == legacy.read_bytes()
    if selected is None:
        return PackageIntegrityManifestStatus(False, False, None, None, None, None, False, False, aliases_match)
    try:
        payload = json.loads(selected.read_text(encoding="utf-8-sig"))
        valid = isinstance(payload, dict)
    except Exception:
        payload = {}
        valid = False
    return PackageIntegrityManifestStatus(
        True,
        valid,
        selected.name,
        str(selected),
        _sha256(selected),
        str(payload.get("version") or payload.get("runtime_version") or "").strip() or None,
        primary.is_file(),
        legacy.is_file(),
        aliases_match,
    )
