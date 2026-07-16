from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
import fnmatch
import json
import re


RESOURCE_NAME = "zip_package_profiles.json"
RESOURCE_SCHEMA_VERSION = "zip_package_profiles/v1"
CANONICAL_VERSION_SOURCE = "latka_jazn/version.py"
CANONICAL_MANIFEST_SOURCE = "PACKAGE_INTEGRITY_MANIFEST.json"
FORBIDDEN_LEGACY_ENTRIES = {"VERSION.txt", "MANIFEST_CURRENT.json"}
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class PackageProfileError(ValueError):
    """Raised when the active package-profile resource is missing or unsafe."""


@dataclass(slots=True)
class PackageProfile:
    name: str
    includes: list[str]
    excludes: list[str]
    purpose: str

    def to_dict(self) -> dict:
        return asdict(self)


def _validate_pattern(pattern: object, *, field: str, allow_legacy_exclusion: bool = False) -> str:
    value = str(pattern or "").strip().replace("\\", "/")
    if not value:
        raise PackageProfileError(f"{field} contains an empty pattern")
    if (
        "\x00" in value
        or value.startswith("/")
        or _DRIVE_RE.match(value)
        or ":" in value
        or "//" in value
    ):
        raise PackageProfileError(f"{field} contains an absolute or invalid pattern: {value!r}")
    parts = PurePosixPath(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise PackageProfileError(f"{field} contains an ambiguous or traversing pattern: {value!r}")
    if not allow_legacy_exclusion and any(part in FORBIDDEN_LEGACY_ENTRIES for part in parts):
        raise PackageProfileError(f"{field} contains forbidden legacy contract data: {value!r}")
    return value


def _pattern_matches(pattern: str, candidate: str) -> bool:
    return fnmatch.fnmatchcase(candidate, pattern)


def _profile_excludes_legacy(includes: list[str], excludes: list[str], legacy_name: str) -> bool:
    can_include = any(_pattern_matches(pattern, legacy_name) for pattern in includes)
    if not can_include:
        return True
    return any(_pattern_matches(pattern, legacy_name) for pattern in excludes)


def load_package_profiles(root: Path) -> dict[str, PackageProfile]:
    path = Path(root) / "latka_jazn" / "resources" / RESOURCE_NAME
    if not path.is_file():
        raise PackageProfileError(f"active package profile resource is missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageProfileError(f"cannot read active package profiles: {type(exc).__name__}: {exc}") from exc

    if data.get("schema_version") != RESOURCE_SCHEMA_VERSION:
        raise PackageProfileError(
            f"active package profile schema must be {RESOURCE_SCHEMA_VERSION!r}"
        )
    if data.get("version_source") != CANONICAL_VERSION_SOURCE:
        raise PackageProfileError("active package profiles must use latka_jazn/version.py as version source")
    if data.get("manifest_source") != CANONICAL_MANIFEST_SOURCE:
        raise PackageProfileError(
            "active package profiles must use PACKAGE_INTEGRITY_MANIFEST.json as manifest source"
        )

    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise PackageProfileError("active package profiles must contain a non-empty profiles list")

    profiles: dict[str, PackageProfile] = {}
    for index, item in enumerate(raw_profiles):
        if not isinstance(item, dict):
            raise PackageProfileError(f"profiles[{index}] must be an object")
        name = str(item.get("name") or "").strip()
        if not _PROFILE_NAME_RE.fullmatch(name) or name in profiles:
            raise PackageProfileError(f"duplicate or invalid package profile name: {name!r}")
        includes = [
            _validate_pattern(value, field=f"profiles[{index}].includes")
            for value in list(item.get("includes") or [])
        ]
        excludes = [
            _validate_pattern(
                value,
                field=f"profiles[{index}].excludes",
                allow_legacy_exclusion=True,
            )
            for value in list(item.get("excludes") or [])
        ]
        if not includes:
            raise PackageProfileError(f"package profile {name!r} has no include patterns")
        for legacy_name in sorted(FORBIDDEN_LEGACY_ENTRIES):
            if not _profile_excludes_legacy(includes, excludes, legacy_name):
                raise PackageProfileError(
                    f"package profile {name!r} can include forbidden legacy file {legacy_name!r} "
                    "without an explicit exclusion"
                )
        purpose = str(item.get("purpose") or "").strip()
        if not purpose:
            raise PackageProfileError(f"package profile {name!r} has no purpose")
        profiles[name] = PackageProfile(
            name=name,
            includes=includes,
            excludes=excludes,
            purpose=purpose,
        )
    return profiles
