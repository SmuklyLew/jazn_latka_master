from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from latka_jazn.core.source_provenance import read_source_provenance
from latka_jazn.core.package_integrity_manifest import package_integrity_manifest_status
from latka_jazn.version import PACKAGE_VERSION, PACKAGE_VERSION_FULL, version_number
from latka_jazn.core.version_source import (
    VERSION_MODULE_RELATIVE_PATH,
    read_runtime_version_from_version_py,
)

ACTIVE_EXTRACTION_CACHE_TOOL_VERSION = PACKAGE_VERSION_FULL

import hashlib
import json
import os

from latka_jazn.core.runtime_root import (
    START_FILE_NAMES,
    active_runtime_marker_path,
    find_start_file,
    resolve_active_runtime_root,
    resolve_active_runtime_marker_path,
)

FALLBACK_PACKAGE_VERSION = PACKAGE_VERSION_FULL
SCHEMA_PREFIX = "jazn_active_runtime_marker"
CACHE_CONTRACT_PREFIX = "active_extraction_cache_contract"
VISIBLE_PREVIEW_CONTRACT_PREFIX = "visible_runtime_preview_contract"
SCHEMA_VERSION = f"{SCHEMA_PREFIX}/{PACKAGE_VERSION_FULL}"
CACHE_CONTRACT_VERSION = f"{CACHE_CONTRACT_PREFIX}/{PACKAGE_VERSION_FULL}"
VISIBLE_PREVIEW_CONTRACT_VERSION = f"{VISIBLE_PREVIEW_CONTRACT_PREFIX}/{PACKAGE_VERSION_FULL}"
DEFAULT_MARKER_NAME = "JAZN_ACTIVE_RUNTIME.json"
START_FILE_ORDER = START_FILE_NAMES


def _version_number(package_version: str | None = None) -> str:
    value = str(package_version or FALLBACK_PACKAGE_VERSION or "").strip()
    value = value.lstrip("\ufeff").strip()
    if value.startswith("v"):
        value = value[1:]
    return value or version_number(PACKAGE_VERSION_FULL)


def active_marker_schema_version(package_version: str | None = None) -> str:
    return f"{SCHEMA_PREFIX}/v{_version_number(package_version)}"


def active_cache_contract_version(package_version: str | None = None) -> str:
    return f"{CACHE_CONTRACT_PREFIX}/v{_version_number(package_version)}"


def visible_preview_contract_version(root: Path | None = None, package_version: str | None = None) -> str:
    version = package_version
    if version is None and root is not None:
        version = read_runtime_version_from_version_py(Path(root), fallback=FALLBACK_PACKAGE_VERSION)
    return f"{VISIBLE_PREVIEW_CONTRACT_PREFIX}/v{_version_number(version)}"


def _sha256_file(path: Path) -> str | None:
    path = Path(path)
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff").strip()
    except FileNotFoundError:
        return None


def _default_marker_output(root: Path) -> Path:
    env = os.environ.get("JAZN_ACTIVE_RUNTIME_MARKER")
    if env:
        return resolve_active_runtime_marker_path(root, Path(env))
    root = Path(root).resolve()
    return active_runtime_marker_path(root)


def detect_start_file(root: Path) -> str | None:
    path = find_start_file(root)
    return path.name if path else None


def manifest_hash(root: Path) -> str | None:
    return package_integrity_manifest_status(root).sha256


def read_active_marker(marker_output: Path) -> dict[str, Any] | None:
    path = Path(marker_output)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": "invalid_json", "marker_path": str(path), "valid": False}
    data.setdefault("marker_path", str(path))
    return data


def _runtime_versions_equivalent(marker_version: Any, file_version: str | None) -> bool:
    """Compare runtime identity versions without treating release-name suffixes as cache drift."""
    if not marker_version or not file_version:
        return False
    return version_number(str(marker_version)) == version_number(str(file_version))


def _existing_relative(root: Path, candidates: list[str]) -> str | None:
    root = Path(root)
    for rel in candidates:
        value = str(rel or "").strip().replace("\\", "/")
        if value and (root / value).is_file():
            return value
    return None


def _active_runtime_write_database(root: Path) -> str | None:
    root = Path(root)
    shard_manifest = root / "memory" / "sqlite" / "runtime_write_v1" / "runtime_memory_shards.json"
    try:
        data = json.loads(shard_manifest.read_text(encoding="utf-8"))
        active_id = str(data.get("active_write_shard") or "").strip()
        shards = data.get("shards") if isinstance(data.get("shards"), list) else []
        ordered = [item for item in shards if isinstance(item, dict)]
        ordered.sort(key=lambda item: (str(item.get("shard_id") or ""), str(item.get("path") or "")), reverse=True)
        for item in ordered:
            if active_id and str(item.get("shard_id") or "") != active_id:
                continue
            rel = str(item.get("path") or "").strip().replace("\\", "/")
            if rel and (root / rel).is_file():
                return rel
        for item in ordered:
            rel = str(item.get("path") or "").strip().replace("\\", "/")
            if rel and (root / rel).is_file() and rel.startswith("memory/sqlite/runtime_write_v1/"):
                return rel
    except Exception:
        pass
    candidates = sorted(
        (root / "memory" / "sqlite" / "runtime_write_v1").glob("runtime_memory*.sqlite3"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    ) if (root / "memory" / "sqlite" / "runtime_write_v1").is_dir() else []
    return candidates[0].relative_to(root).as_posix() if candidates else None


def _active_storage_from_bootstrap(root: Path, version: str | None) -> dict[str, Any]:
    root = Path(root)
    try:
        data = json.loads((root / "BOOTSTRAP_JAZN_CURRENT.json").read_text(encoding="utf-8"))
        active = str(data.get("active_database") or "").strip().replace("\\", "/")
        if active and (root / active).is_file():
            runtime_write = str(data.get("active_runtime_write_database") or "").strip().replace("\\", "/")
            runtime_write = runtime_write if runtime_write and (root / runtime_write).is_file() else _active_runtime_write_database(root)
            return {
                "active_database": active,
                "active_runtime_write_database": runtime_write,
                "active_audit_database": _existing_relative(root, [str(data.get("active_audit_database") or data.get("audit_database") or ""), "memory/sqlite/runtime_write_v1/runtime_audit.sqlite3"]),
                "active_conversation_archive": _existing_relative(root, [str(data.get("active_conversation_archive") or active), active]),
                "active_conversation_fts": _existing_relative(root, [str(data.get("active_conversation_fts") or ""), "memory/sqlite/conversation_fts_v1/conversation_fts_0001.sqlite3"]),
                "active_staging_database": _existing_relative(root, [str(data.get("active_staging_database") or ""), "memory/sqlite/staging_v1/staging_memory_0001.sqlite3"]),
                "storage_layout": str(data.get("storage_layout") or "conversation_archive_v1+fts_v1+staging_v1+runtime_write_v1"),
                "storage_detection": "bootstrap_verified",
            }
    except Exception:
        pass

    archive = _existing_relative(root, [
        "memory/sqlite/conversation_archive_v1/conversation_archive_manifest.sqlite3",
        "memory/sqlite/conversation_archive_v1/conversation_archive_0001.sqlite3",
    ])
    runtime_write = _active_runtime_write_database(root)
    fts = _existing_relative(root, ["memory/sqlite/conversation_fts_v1/conversation_fts_0001.sqlite3"])
    staging = _existing_relative(root, ["memory/sqlite/staging_v1/staging_memory_0001.sqlite3"])
    audit = _existing_relative(root, ["memory/sqlite/runtime_write_v1/runtime_audit.sqlite3"])
    if archive or runtime_write or fts or staging:
        active = archive or runtime_write or staging or fts
        return {
            "active_database": active,
            "active_runtime_write_database": runtime_write,
            "active_audit_database": audit,
            "active_conversation_archive": archive,
            "active_conversation_fts": fts,
            "active_staging_database": staging,
            "storage_layout": "conversation_archive_v1+fts_v1+staging_v1+runtime_write_v1",
            "storage_detection": "filesystem_verified",
        }

    legacy_candidates = []
    if str(version).startswith("v14.8.2"):
        legacy_candidates.append("workspace_runtime/latka_jazn_v14_8_2.sqlite3")
    elif str(version).startswith("v14.8.1"):
        legacy_candidates.append("workspace_runtime/latka_jazn_v14_8_1.sqlite3")
    elif str(version).startswith("v14.8.0"):
        legacy_candidates.append("workspace_runtime/latka_jazn_v14_8_0.sqlite3")
    elif str(version).startswith("v14.7.0"):
        legacy_candidates.append("workspace_runtime/latka_jazn_v14_7_0.sqlite3")
    active = _existing_relative(root, legacy_candidates)
    return {
        "active_database": active,
        "active_runtime_write_database": active,
        "active_audit_database": None,
        "active_conversation_archive": None,
        "active_conversation_fts": None,
        "active_staging_database": None,
        "storage_layout": "legacy_single_sqlite" if active else "not_detected",
        "storage_detection": "legacy_existing_only" if active else "not_detected",
    }


def build_active_runtime_status(root: Path, *, source_zip: Path | None = None, marker_output: Path | None = None) -> dict[str, Any]:
    requested_root = Path(root).resolve()
    marker_output = resolve_active_runtime_marker_path(requested_root, marker_output) if marker_output else _default_marker_output(requested_root)
    root_resolution = resolve_active_runtime_root(requested_root, marker_path=marker_output)
    root = root_resolution.root
    version = read_runtime_version_from_version_py(root, fallback=FALLBACK_PACKAGE_VERSION)
    start_file = detect_start_file(root)
    package_manifest_status = package_integrity_manifest_status(root)
    current_manifest_sha256 = package_manifest_status.sha256
    source_zip = Path(source_zip).resolve() if source_zip else None
    source_zip_sha256 = _sha256_file(source_zip) if source_zip else None
    existing_marker = read_active_marker(marker_output)
    marker_rejected = root_resolution.marker_found and not root_resolution.marker_valid
    cache_hit_reasons: list[str] = []
    cache_miss_reasons: list[str] = []

    if root.exists():
        cache_hit_reasons.append("active_root_exists")
    else:
        cache_miss_reasons.append("active_root_missing")
    if (root / "latka_jazn").is_dir():
        cache_hit_reasons.append("latka_jazn_package_exists")
    else:
        cache_miss_reasons.append("latka_jazn_package_missing")
    if version:
        cache_hit_reasons.append("version_py_exists")
    else:
        cache_miss_reasons.append("version_py_missing")
    if start_file:
        cache_hit_reasons.append("start_file_exists")
    else:
        cache_miss_reasons.append("start_file_missing_main_run_jazn")
    if current_manifest_sha256:
        cache_hit_reasons.append("package_integrity_manifest_exists")
    else:
        cache_miss_reasons.append("package_integrity_manifest_missing")

    if existing_marker and existing_marker.get("valid", True):
        if existing_marker.get("active_root") == str(root):
            cache_hit_reasons.append("marker_active_root_matches")
        else:
            cache_miss_reasons.append("marker_active_root_differs_or_missing")
        if _runtime_versions_equivalent(existing_marker.get("version"), version):
            cache_hit_reasons.append("marker_version_matches")
        else:
            cache_miss_reasons.append("marker_version_differs_or_missing")
        marker_manifest_sha = existing_marker.get("package_integrity_manifest_sha256")
        legacy_marker_manifest_sha = existing_marker.get("manifest_current_sha256")
        if current_manifest_sha256 and marker_manifest_sha == current_manifest_sha256:
            cache_hit_reasons.append("marker_package_integrity_sha256_matches")
        elif current_manifest_sha256:
            if not marker_manifest_sha and legacy_marker_manifest_sha == current_manifest_sha256:
                cache_miss_reasons.append("marker_legacy_manifest_sha256_requires_refresh")
            else:
                cache_miss_reasons.append("marker_package_integrity_sha256_differs_or_missing")
        if source_zip_sha256:
            if existing_marker.get("source_zip_sha256") == source_zip_sha256:
                cache_hit_reasons.append("marker_source_zip_sha256_matches")
            else:
                cache_miss_reasons.append("marker_source_zip_sha256_differs_or_missing")
    else:
        cache_miss_reasons.append("active_marker_missing_or_invalid")

    if marker_rejected and root_resolution.error:
        cache_miss_reasons.append(root_resolution.error)
    hard_missing_reasons = {"active_root_missing", "latka_jazn_package_missing", "version_py_missing", "start_file_missing_main_run_jazn", "package_integrity_manifest_missing"}
    missing_hard_requirement = any(reason in hard_missing_reasons for reason in cache_miss_reasons)
    marker_differs = any("differs" in reason for reason in cache_miss_reasons)
    marker_refresh_required = any(reason.startswith("marker_") or reason.startswith("active_marker_") for reason in cache_miss_reasons)
    source_zip_mismatch = any(reason == "marker_source_zip_sha256_differs_or_missing" for reason in cache_miss_reasons)
    should_reuse_existing_extraction = bool(
        root.exists()
        and version
        and start_file
        and bool(current_manifest_sha256)
        and not missing_hard_requirement
        and not marker_rejected
        and not source_zip_mismatch
    )

    marker_schema_version = active_marker_schema_version(version)
    cache_contract_version = active_cache_contract_version(version)
    storage = _active_storage_from_bootstrap(root, version)
    source_provenance = read_source_provenance(root).to_dict()
    if not existing_marker:
        marker_lifecycle_state = "missing"
        marker_trusted = False
    elif marker_rejected or not root_resolution.marker_valid:
        marker_lifecycle_state = "imported" if root_resolution.source in {"marker", "imported_marker"} else "error"
        marker_trusted = False
    elif not marker_refresh_required:
        marker_lifecycle_state = "trusted"
        marker_trusted = True
    else:
        marker_lifecycle_state = "degraded"
        marker_trusted = False

    return {
        "schema_version": marker_schema_version,
        "cache_contract_version": cache_contract_version,
        "requested_runtime_root": str(requested_root),
        "active_root_source": root_resolution.source,
        "active_marker_valid": root_resolution.marker_valid,
        "active_root_validation_error": root_resolution.error,
        "runtime_root_valid": not missing_hard_requirement,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_root": str(root),
        "version": version,
        "start_file": start_file,
        **storage,
        "source_provenance": source_provenance,
        "source_provenance_status": source_provenance.get("status"),
        "source_provenance_sha256": source_provenance.get("file_sha256"),
        "source_base_commit": source_provenance.get("base_merge_commit"),
        "source_provenance_limitations": list(source_provenance.get("limitations") or []),
        "source_provenance_without_git_history_restriction": (
            "Without .git, branch/tag/dirty state cannot be independently verified; trust is limited to the provenance hash protected by PACKAGE_INTEGRITY_MANIFEST.json."
            if not source_provenance.get("git_directory_present") else None
        ),
        "package_integrity_manifest": package_manifest_status.to_dict(),
        "package_integrity_manifest_sha256": current_manifest_sha256,
        "source_zip": str(source_zip) if source_zip else None,
        "source_zip_sha256": source_zip_sha256,
        "marker_output": str(marker_output),
        "existing_marker_found": bool(existing_marker),
        "marker_lifecycle_state": marker_lifecycle_state,
        "marker_trusted": marker_trusted,
        "marker_source": "runtime_generated" if marker_trusted else (root_resolution.source or "missing"),
        "cache_hit_reasons": cache_hit_reasons,
        "cache_miss_reasons": cache_miss_reasons,
        "should_reuse_existing_extraction": should_reuse_existing_extraction,
        "marker_refresh_required": bool(marker_refresh_required),
        "marker_differs": bool(marker_differs),
        "must_not_extract_again_when": [
            "active_root exists",
            "latka_jazn/version.py provides expected Jaźń version",
            "PACKAGE_INTEGRITY_MANIFEST.json sha256 matches marker when package reuse is being verified",
            "source ZIP sha256 matches marker when a ZIP path is provided",
        ],
        "truth_boundary": "ZIP jest źródłem importu/eksportu. Bieżące zapisy runtime i pamięci powstają w aktywnym folderze roboczym; nie wolno udawać, że zapisują się do już utworzonego ZIP-a.",
    }


def write_active_runtime_marker(root: Path, *, source_zip: Path | None = None, marker_output: Path | None = None, action: str = "reuse_existing_unpacked_folder") -> dict[str, Any]:
    root = Path(root).resolve()
    marker_output = resolve_active_runtime_marker_path(root, marker_output) if marker_output else _default_marker_output(root)
    status = build_active_runtime_status(root, source_zip=source_zip, marker_output=marker_output)
    effective_root = Path(status["active_root"]).resolve()
    marker_only_prefixes = ("active_marker_", "marker_")
    status["cache_miss_reasons"] = [
        reason for reason in status.get("cache_miss_reasons", [])
        if not reason.startswith(marker_only_prefixes)
    ]
    status.setdefault("cache_hit_reasons", []).append("active_marker_written_now")
    status["existing_marker_found"] = True
    status["marker_refresh_required"] = any(
        reason.startswith(marker_only_prefixes)
        for reason in status["cache_miss_reasons"]
    )
    status["marker_differs"] = any("differs" in reason for reason in status["cache_miss_reasons"])
    hard_missing = {"active_root_missing", "latka_jazn_package_missing", "version_py_missing", "start_file_missing_main_run_jazn", "package_integrity_manifest_missing"}
    status["should_reuse_existing_extraction"] = not any(reason in hard_missing for reason in status["cache_miss_reasons"])
    status["active_marker_valid"] = status["runtime_root_valid"]
    status["active_root_validation_error"] = None if status["runtime_root_valid"] else status.get("active_root_validation_error")
    status["marker_lifecycle_state"] = "generated_now" if status["runtime_root_valid"] else "error"
    status["marker_trusted"] = bool(status["runtime_root_valid"])
    status["marker_source"] = "runtime_generated"
    status.pop("manifest_current_sha256", None)
    marker = {
        **status,
        "schema_version": status.get("schema_version") or active_marker_schema_version(status.get("version")),
        "written_at_utc": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "memory_write_root": str(effective_root / "memory"),
        "workspace_runtime_root": str(effective_root / "workspace_runtime"),
        "exports_root": str(effective_root / "exports"),
        "visible_runtime_preview_contract": {
            "schema_version": visible_preview_contract_version(effective_root, status.get("version")),
            "required_when_user_asks_about": ["runtime", "timestamp", "runtime preview", "aktywny folder", "pamięć", "pliki Jaźni", "uruchomienie", "fallback"],
            "required_visible_fields": ["timestamp_header", "active_root", "start_file", "runtime_answer_quality", "fallback_classification", "response_source", "one_shot_or_chat_loop_limit"],
            "forbidden_behavior": "Nie wolno schować timestampu i jakości runtime w samym JSON ani mówić ogólnie, że runtime działa, bez pokazania statusu przy pytaniu diagnostycznym.",
        },
    }
    marker_output.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker_output.with_suffix(marker_output.suffix + ".tmp")
    tmp.write_text(json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, marker_output)
    return marker
