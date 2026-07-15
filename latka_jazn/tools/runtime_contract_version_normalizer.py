from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import argparse
import hashlib
import json

from latka_jazn.core.version_source import read_runtime_version_from_version_py
from latka_jazn.tools.active_extraction_cache import (
    active_cache_contract_version,
    active_marker_schema_version,
    visible_preview_contract_version,
)

TARGET_VERSION = "v14.8.2.6.3-free-dialogue-short-turn-fallback-hotfix"
TARGET_FILES = (
    "PACKAGE_INTEGRITY_MANIFEST.json",
    "MANIFEST_CURRENT.json",
    "workspace_runtime/JAZN_ACTIVE_RUNTIME.json",
    "ACTIVE_RUNTIME_CACHE_CONTRACT.json",
    "BOOTSTRAP_JAZN_CURRENT.json",
)
SCHEMA_PREFIXES = {
    "package_integrity_manifest": lambda version: f"package_integrity_manifest/v{_version_number(version)}",
    "manifest_current": lambda version: f"package_integrity_manifest/v{_version_number(version)}",
    "jazn_active_runtime_marker": active_marker_schema_version,
    "active_extraction_cache_contract": active_cache_contract_version,
    "active_runtime_cache_contract": active_cache_contract_version,
    "visible_runtime_preview_contract": visible_preview_contract_version,
    "bootstrap_jazn_current": lambda version: f"bootstrap_jazn_current/v{_version_number(version)}",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _version_number(package_version: str | None) -> str:
    value = str(package_version or TARGET_VERSION).strip()
    if value.startswith("v"):
        value = value[1:]
    return value.split("-", 1)[0] or "14.8.2.6.2"


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_package_version(root: Path) -> str:
    return read_runtime_version_from_version_py(root, fallback=TARGET_VERSION) or TARGET_VERSION


def _schema_for_value(value: str, package_version: str) -> str | None:
    if "/" not in value:
        return None
    prefix, _old = value.rsplit("/", 1)
    builder = SCHEMA_PREFIXES.get(prefix)
    if not builder:
        return None
    return builder(package_version)


@dataclass(slots=True)
class NormalizationResult:
    rel_path: str
    exists: bool
    changed: bool
    updates: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.rel_path,
            "exists": self.exists,
            "changed": self.changed,
            "updates": self.updates,
        }


def _normalize_node(node: Any, package_version: str, updates: list[dict[str, Any]], path: str = "$") -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            current_path = f"{path}.{key}"
            if key in {"version", "runtime_version", "package_version"} and isinstance(value, str) and value != package_version:
                out[key] = package_version
                updates.append({"path": current_path, "old": value, "new": package_version})
                continue
            if key in {"cache_contract_version", "active_extraction_cache_contract"} and isinstance(value, str):
                expected = active_cache_contract_version(package_version)
                if value != expected:
                    out[key] = expected
                    updates.append({"path": current_path, "old": value, "new": expected})
                    continue
            if key in {"runtime_preview_contract", "visible_runtime_preview_contract"} and isinstance(value, str):
                expected = visible_preview_contract_version(package_version=package_version)
                if value != expected:
                    out[key] = expected
                    updates.append({"path": current_path, "old": value, "new": expected})
                    continue
            if key == "schema_version" and isinstance(value, str):
                expected = _schema_for_value(value, package_version)
                if expected and value != expected:
                    out[key] = expected
                    updates.append({"path": current_path, "old": value, "new": expected})
                    continue
            out[key] = _normalize_node(value, package_version, updates, current_path)
        return out
    if isinstance(node, list):
        return [_normalize_node(item, package_version, updates, f"{path}[{idx}]") for idx, item in enumerate(node)]
    return node


def normalize_json_file(root: Path, rel_path: str, package_version: str, *, apply: bool) -> NormalizationResult:
    path = root / rel_path
    if not path.exists():
        return NormalizationResult(rel_path, False, False, [])
    data = json.loads(path.read_text(encoding="utf-8"))
    updates: list[dict[str, Any]] = []
    normalized = _normalize_node(data, package_version, updates)
    changed = bool(updates)
    if changed and apply:
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return NormalizationResult(rel_path, True, changed, updates)


def normalize_runtime_contract_versions(root: Path, *, apply: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    package_version = read_package_version(root)
    results = [normalize_json_file(root, rel, package_version, apply=apply) for rel in TARGET_FILES]

    primary_manifest = root / "PACKAGE_INTEGRITY_MANIFEST.json"
    legacy_manifest = root / "MANIFEST_CURRENT.json"
    manifest_path = primary_manifest if primary_manifest.is_file() else legacy_manifest
    manifest_sha = _sha256_file(manifest_path)
    marker_rel = "workspace_runtime/JAZN_ACTIVE_RUNTIME.json"
    marker_path = root / marker_rel
    marker_sha_update: dict[str, Any] | None = None
    if apply and manifest_sha and marker_path.exists():
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        old = marker.get("package_integrity_manifest_sha256") or marker.get("manifest_current_sha256")
        if old != manifest_sha:
            marker["package_integrity_manifest_sha256"] = manifest_sha
            marker["manifest_current_sha256"] = manifest_sha
            marker["updated_at_utc"] = _utc_now()
            marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            marker_sha_update = {
                "path": f"{marker_rel}.package_integrity_manifest_sha256",
                "old": old,
                "new": manifest_sha,
            }

    return {
        "schema_version": f"runtime_contract_version_normalizer/v{_version_number(package_version)}",
        "runtime_version": package_version,
        "active_marker_schema_version": active_marker_schema_version(package_version),
        "active_cache_contract_version": active_cache_contract_version(package_version),
        "visible_runtime_preview_contract_version": visible_preview_contract_version(package_version=package_version),
        "applied": apply,
        "package_integrity_manifest_sha256": manifest_sha,
        "manifest_current_sha256": manifest_sha,
        "marker_manifest_sha256_update": marker_sha_update,
        "results": [item.to_dict() for item in results],
        "truth_boundary": "Normalizator poprawia tylko aktywne markery/kontrakty bieżącego folderu. Nie zmienia historycznych backupów ani archiwalnych embedded_sources.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit/fix runtime contract/schema/cache version fields for the active Jaźń folder.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = normalize_runtime_contract_versions(Path(args.root), apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
