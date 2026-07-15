from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from latka_jazn.config import JaznConfig
from latka_jazn.core.runtime_daemon import (
    DEFAULT_DAEMON_HOST,
    DEFAULT_DAEMON_PORT,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_START_TIMEOUT_SECONDS,
    inject_daemon_trusted_time,
    start_daemon,
    status_daemon,
)
from latka_jazn.packaging.split_zip_package import (
    extract_joined_zip_resumable,
    infer_base_zip_name,
    join_split_package_to_zip,
    load_package_expectations,
    resolve_renamed_package_parts,
    test_joined_zip,
    verify_extracted_zip_tree,
)
from latka_jazn.tools.active_extraction_cache import write_active_runtime_marker
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version, version_number
from latka_jazn.core.version_source import (
    read_runtime_version_from_version_py,
    read_version_checkpoint,
)

REQUIRED_FILES = ("VERSION.txt", "latka_jazn/version.py", "MANIFEST_CURRENT.json")
REQUIRED_DIRECTORIES = ("latka_jazn",)
OPTIONAL_RUNTIME_DIRECTORIES = ("memory", "workspace_runtime")
START_FILES = ("main.py", "run.py")
DEFAULT_CHATGPT_ROOT = Path("/mnt/data/jazn_runtime_current_full")
DEFAULT_CHATGPT_PARTS_DIR = Path("/mnt/data")
RECOVERY_SCHEMA_VERSION = schema_version("chatgpt_runtime_recovery", version=PACKAGE_VERSION_FULL)


@dataclass(slots=True)
class RuntimePreflightReport:
    ok: bool
    active_root: str
    structure_ok: bool
    manifest_ok: bool
    marker_ok: bool
    start_file: str | None
    version: str | None
    manifest_version: str | None
    marker_path: str | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = RECOVERY_SCHEMA_VERSION
    truth_boundary: str = (
        "Preflight potwierdza folder, start file, manifest i marker. Żywy daemon, "
        "timestamp i SQLite są osobnymi etapami aktywacji."
    )

    @property
    def needs_recovery(self) -> bool:
        return not (self.structure_ok and self.manifest_ok)

    @property
    def needs_marker_refresh(self) -> bool:
        return bool(self.structure_ok and self.manifest_ok and not self.marker_ok)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["needs_recovery"] = self.needs_recovery
        payload["needs_marker_refresh"] = self.needs_marker_refresh
        return payload


@dataclass(slots=True)
class RecoveryResult:
    ok: bool
    state: str
    active_root: str
    report: dict[str, Any]
    pending: bool = False
    exit_code: int = 0
    schema_version: str = RECOVERY_SCHEMA_VERSION
    truth_boundary: str = (
        "Folder staje się aktywny dopiero po pełnym SHA256/CRC, rozpakowaniu bez uciętych plików, "
        "porównaniu ZIP–filesystem, atomowej aktywacji i zapisaniu markera."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _part_resolution_cache_valid(cache: dict[str, Any] | None, expected: list[Any], canonical_dir: Path) -> bool:
    if not isinstance(cache, dict) or cache.get("ok") is not True:
        return False
    rows = cache.get("resolved_parts")
    if not isinstance(rows, list) or len(rows) != len(expected):
        return False
    by_no = {int(row.get("part_no", -1)): row for row in rows if isinstance(row, dict)}
    for part in expected:
        row = by_no.get(int(part.part_no))
        if not row or row.get("expected_name") != part.filename:
            return False
        source = Path(str(row.get("source_path") or ""))
        target = canonical_dir / part.filename
        if not source.is_file() or not target.is_file():
            return False
        stat = source.stat()
        if int(row.get("size_bytes", -1)) != stat.st_size or int(row.get("source_mtime_ns", -1)) != stat.st_mtime_ns:
            return False
        if part.size_bytes is not None and target.stat().st_size != part.size_bytes:
            return False
        if part.sha256 and str(row.get("sha256") or "").lower() != part.sha256.lower():
            return False
    return True


def _zip_verification_cache_valid(cache: dict[str, Any] | None, zip_path: Path, expected_sha: str | None, run_crc: bool) -> bool:
    if not isinstance(cache, dict) or cache.get("ok") is not True or not zip_path.is_file():
        return False
    stat = zip_path.stat()
    if int(cache.get("size_bytes", -1)) != stat.st_size or int(cache.get("mtime_ns", -1)) != stat.st_mtime_ns:
        return False
    if expected_sha and str(cache.get("sha256") or "").lower() != expected_sha.lower():
        return False
    if run_crc and cache.get("crc_tested") is not True:
        return False
    return True


def _runtime_version(root: Path) -> str | None:
    return read_runtime_version_from_version_py(root)


def _find_start_file(root: Path) -> str | None:
    for name in START_FILES:
        if (root / name).is_file():
            return name
    return None


def _candidate_marker_paths(root: Path, explicit: Path | None = None) -> list[Path]:
    paths: list[Path] = []
    if explicit is not None:
        paths.append(Path(explicit).expanduser().resolve())
    paths.extend((root / "JAZN_ACTIVE_RUNTIME.json", root / "workspace_runtime" / "JAZN_ACTIVE_RUNTIME.json"))
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def runtime_preflight(root: Path, *, marker_path: Path | None = None) -> RuntimePreflightReport:
    root = Path(root).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            errors.append(f"missing_file:{name}")
    for name in REQUIRED_DIRECTORIES:
        if not (root / name).is_dir():
            errors.append(f"missing_directory:{name}")
    for name in OPTIONAL_RUNTIME_DIRECTORIES:
        if not (root / name).is_dir():
            warnings.append(f"runtime_directory_missing_will_be_initialized:{name}")
    start_file = _find_start_file(root)
    if not start_file:
        errors.append("missing_start_file:main.py_or_run.py")
    structure_ok = not errors

    version = _runtime_version(root)
    checkpoint = read_version_checkpoint(root)
    if version and checkpoint != version:
        errors.append(f"version_checkpoint_mismatch:{checkpoint!r}!={version!r}")
        structure_ok = False
    manifest = _read_json(root / "MANIFEST_CURRENT.json")
    manifest_version = None
    manifest_ok = False
    if manifest is None:
        if (root / "MANIFEST_CURRENT.json").exists():
            errors.append("manifest_invalid_json")
    else:
        manifest_version = str(manifest.get("version") or manifest.get("runtime_version") or "").strip() or None
        manifest_start = str(manifest.get("start_file") or "").strip() or None
        versions_match = bool(
            version and manifest_version and version_number(version) == version_number(manifest_version)
        )
        start_matches = bool(start_file and (not manifest_start or manifest_start == start_file or (root / manifest_start).is_file()))
        manifest_ok = bool(versions_match and start_matches)
        if not versions_match:
            errors.append(f"manifest_version_mismatch:{version!r}!={manifest_version!r}")
        if not start_matches:
            errors.append(f"manifest_start_file_invalid:{manifest_start!r}")

    selected_marker: Path | None = None
    marker_ok = False
    for candidate in _candidate_marker_paths(root, marker_path):
        marker = _read_json(candidate)
        if marker is None:
            continue
        if selected_marker is None:
            selected_marker = candidate
        active = str(marker.get("active_root") or marker.get("active_folder") or "").strip()
        candidate_ok = bool(active and Path(active).expanduser().resolve() == root)
        marker_version = str(marker.get("version") or "").strip()
        if candidate_ok and marker_version and version:
            candidate_ok = version_number(marker_version) == version_number(version)
            if not candidate_ok:
                warnings.append(f"marker_version_mismatch:{marker_version!r}!={version!r}")
        if candidate_ok:
            selected_marker = candidate
            marker_ok = True
            break
    if not marker_ok:
        warnings.append("active_marker_missing_or_not_trusted")

    return RuntimePreflightReport(
        ok=bool(structure_ok and manifest_ok and marker_ok),
        active_root=str(root),
        structure_ok=structure_ok,
        manifest_ok=manifest_ok,
        marker_ok=marker_ok,
        start_file=start_file,
        version=version,
        manifest_version=manifest_version,
        marker_path=str(selected_marker) if selected_marker else None,
        errors=errors,
        warnings=warnings,
    )


def _safe_remove_tree(path: Path) -> None:
    path = Path(path)
    if path.exists():
        shutil.rmtree(path)


def _atomic_activate(staging: Path, destination: Path, *, work_dir: Path) -> dict[str, Any]:
    staging = Path(staging).resolve()
    destination = Path(destination).resolve()
    work_dir = Path(work_dir).resolve()
    if staging.parent != destination.parent:
        raise ValueError("Staging i destination muszą być na tym samym filesystemie i w tym samym katalogu nadrzędnym.")
    backup = destination.parent / f".{destination.name}.previous-{int(time.time())}"
    moved_old = False
    try:
        if destination.exists():
            os.replace(destination, backup)
            moved_old = True
        os.replace(staging, destination)
    except Exception:
        if moved_old and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    if backup.exists():
        _safe_remove_tree(backup)
    return {
        "ok": True,
        "destination": str(destination),
        "staging": str(staging),
        "backup_removed": not backup.exists(),
        "work_dir": str(work_dir),
    }


def _sqlite_health(root: Path) -> dict[str, Any]:
    bootstrap = _read_json(root / "BOOTSTRAP_JAZN_CURRENT.json") or {}
    rel = str(bootstrap.get("active_database") or "").strip()
    if not rel:
        return {"ok": False, "reason": "active_database_not_declared"}
    db_path = (root / rel).resolve()
    if not db_path.is_file():
        return {"ok": False, "reason": "active_database_missing", "database": str(db_path)}
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0) as con:
            integrity_rows = con.execute("PRAGMA integrity_check").fetchall()
            foreign_rows = con.execute("PRAGMA foreign_key_check").fetchall()
            tables = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()
        integrity = [str(row[0]) for row in integrity_rows]
        return {
            "ok": integrity == ["ok"] and not foreign_rows,
            "database": str(db_path),
            "integrity_check": integrity,
            "foreign_key_check_count": len(foreign_rows),
            "table_count": int(tables[0]) if tables else 0,
        }
    except Exception as exc:
        return {"ok": False, "database": str(db_path), "reason": f"{type(exc).__name__}: {exc}"}


def recover_chatgpt_runtime(
    *,
    parts_dir: Path = DEFAULT_CHATGPT_PARTS_DIR,
    destination: Path = DEFAULT_CHATGPT_ROOT,
    base_zip_name: str | None = None,
    work_dir: Path | None = None,
    time_budget_seconds: float | None = 25.0,
    run_crc: bool = True,
    force_reextract: bool = False,
    start_runtime_daemon: bool = True,
    daemon_host: str = DEFAULT_DAEMON_HOST,
    daemon_port: int = DEFAULT_DAEMON_PORT,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    startup_timeout: float = DEFAULT_START_TIMEOUT_SECONDS,
    trusted_time_iso: str | None = None,
    trusted_time_source: str | None = None,
    trusted_time_max_age_seconds: int | None = None,
) -> RecoveryResult:
    parts_dir = Path(parts_dir).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    work_dir = Path(work_dir).expanduser().resolve() if work_dir else destination.parent / f".{destination.name}.recovery"
    work_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "parts_dir": str(parts_dir),
        "destination": str(destination),
        "work_dir": str(work_dir),
        "started_at_epoch": time.time(),
    }

    preflight = runtime_preflight(destination)
    report["preflight_before"] = preflight.to_dict()
    if preflight.structure_ok and preflight.manifest_ok and not force_reextract:
        marker = write_active_runtime_marker(destination, action="chatgpt_recovery_reuse_verified_folder")
        report["marker"] = marker
        config = JaznConfig(root=destination)
        if start_runtime_daemon:
            report["daemon_start"] = start_daemon(
                config,
                host=daemon_host,
                port=daemon_port,
                heartbeat_interval=heartbeat_interval,
                startup_timeout=startup_timeout,
            )
        if trusted_time_iso:
            report["trusted_time_injection"] = inject_daemon_trusted_time(
                config,
                trusted_time_iso=trusted_time_iso,
                source=trusted_time_source or "chatgpt_loader_time",
                max_age_seconds=trusted_time_max_age_seconds,
                host=daemon_host,
                port=daemon_port,
                timeout=min(max(startup_timeout, 1.0), 10.0),
            )
        report["daemon_status"] = status_daemon(config, host=daemon_host, port=daemon_port)
        report["sqlite"] = _sqlite_health(destination)
        report["preflight_after"] = runtime_preflight(destination).to_dict()
        ok = bool(
            report["preflight_after"]["ok"]
            and report["sqlite"].get("ok")
            and (not start_runtime_daemon or report["daemon_status"].get("active_state") in {"active_trusted", "active_degraded"})
        )
        return RecoveryResult(ok=ok, state="reused", active_root=str(destination), report=report, exit_code=0 if ok else 4)

    zip_name = infer_base_zip_name(parts_dir, base_zip_name)
    report["base_zip_name"] = zip_name
    expected, expected_full_sha, source = load_package_expectations(parts_dir, zip_name)
    report["expectations_source"] = source
    report["expected_parts_count"] = len(expected)
    report["expected_full_sha256"] = expected_full_sha

    canonical_dir = work_dir / "canonical_parts"
    resolution_cache_path = work_dir / "part-resolution.json"
    cached_aliases = _read_json(resolution_cache_path)
    if _part_resolution_cache_valid(cached_aliases, expected, canonical_dir):
        aliases = dict(cached_aliases or {})
        aliases["cache_reused"] = True
    else:
        aliases = resolve_renamed_package_parts(
            parts_dir,
            expected,
            canonical_dir=canonical_dir,
            skip_part_hash=False,
        )
        aliases["cache_reused"] = False
        _write_json_atomic(resolution_cache_path, aliases)
    report["part_resolution"] = aliases
    for suffix in (".manifest.json", ".parts.sha256", ".sha256"):
        source_sidecar = parts_dir / f"{zip_name}{suffix}"
        if source_sidecar.is_file():
            shutil.copy2(source_sidecar, canonical_dir / source_sidecar.name)
    zip_out = work_dir / zip_name
    zip_cache_path = work_dir / "zip-verification.json"
    zip_cache = _read_json(zip_cache_path)
    if _zip_verification_cache_valid(zip_cache, zip_out, expected_full_sha, run_crc):
        zip_report = dict(zip_cache or {})
        zip_report["cache_reused"] = True
    else:
        zip_out = join_split_package_to_zip(
            canonical_dir,
            zip_name,
            zip_out=zip_out,
            force=False,
            keep_existing=True,
        )
        zip_report = test_joined_zip(zip_out, run_crc=run_crc)
        zip_report.update({
            "sha256": expected_full_sha,
            "mtime_ns": zip_out.stat().st_mtime_ns,
            "cache_reused": False,
        })
        _write_json_atomic(zip_cache_path, zip_report)
    report["zip_test"] = zip_report

    staging = destination.parent / f".{destination.name}.staging"
    if force_reextract and staging.exists():
        _safe_remove_tree(staging)
    extraction = extract_joined_zip_resumable(
        zip_out,
        staging,
        progress_path=work_dir / "extract-progress.json",
        time_budget_seconds=time_budget_seconds,
    )
    report["extraction"] = extraction
    if extraction.get("pending"):
        report["resume_command"] = (
            f"python -X utf8 main.py --recover-chatgpt-runtime --recovery-parts-dir {parts_dir} "
            f"--recovery-destination {destination} --recovery-work-dir {work_dir}"
        )
        return RecoveryResult(
            ok=False,
            pending=True,
            state="extracting_pending",
            active_root=str(destination),
            report=report,
            exit_code=75,
        )

    verification = verify_extracted_zip_tree(zip_out, staging, reject_extra_files=False)
    report["filesystem_verification"] = verification
    if not verification["ok"]:
        return RecoveryResult(ok=False, state="verification_failed", active_root=str(destination), report=report, exit_code=5)

    staging_preflight = runtime_preflight(staging)
    report["staging_preflight"] = staging_preflight.to_dict()
    if not (staging_preflight.structure_ok and staging_preflight.manifest_ok):
        return RecoveryResult(ok=False, state="staging_runtime_invalid", active_root=str(destination), report=report, exit_code=6)

    report["activation"] = _atomic_activate(staging, destination, work_dir=work_dir)
    marker = write_active_runtime_marker(destination, source_zip=zip_out, action="chatgpt_recovery_atomic_activation")
    report["marker"] = marker
    config = JaznConfig(root=destination)
    if start_runtime_daemon:
        report["daemon_start"] = start_daemon(
            config,
            host=daemon_host,
            port=daemon_port,
            heartbeat_interval=heartbeat_interval,
            startup_timeout=startup_timeout,
        )
    if trusted_time_iso:
        report["trusted_time_injection"] = inject_daemon_trusted_time(
            config,
            trusted_time_iso=trusted_time_iso,
            source=trusted_time_source or "chatgpt_loader_time",
            max_age_seconds=trusted_time_max_age_seconds,
            host=daemon_host,
            port=daemon_port,
            timeout=min(max(startup_timeout, 1.0), 10.0),
        )
    report["daemon_status"] = status_daemon(config, host=daemon_host, port=daemon_port)
    report["sqlite"] = _sqlite_health(destination)
    after = runtime_preflight(destination)
    report["preflight_after"] = after.to_dict()
    daemon_ok = not start_runtime_daemon or report["daemon_status"].get("active_state") in {"active_trusted", "active_degraded"}
    ok = bool(after.ok and daemon_ok and report["sqlite"].get("ok"))
    return RecoveryResult(ok=ok, state="active" if ok else "activated_degraded", active_root=str(destination), report=report, exit_code=0 if ok else 7)


__all__ = [
    "DEFAULT_CHATGPT_PARTS_DIR",
    "DEFAULT_CHATGPT_ROOT",
    "RecoveryResult",
    "RuntimePreflightReport",
    "recover_chatgpt_runtime",
    "runtime_preflight",
]
