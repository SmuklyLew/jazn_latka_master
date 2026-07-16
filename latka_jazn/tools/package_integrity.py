from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import subprocess

from latka_jazn.core.version_source import read_runtime_version_from_version_py
from latka_jazn.version import schema_version

MANIFEST_NAME = "PACKAGE_INTEGRITY_MANIFEST.json"
REQUIRED_STATIC_PATHS = {"SOURCE_PROVENANCE.json", "run.py", "main.py", "latka_jazn/version.py"}
FORBIDDEN_ROOT_NAMES = {
    ".git", "memory", "workspace_runtime", "backups", ".pytest_cache", "__pycache__",
}
FORBIDDEN_FILE_NAMES = {
    MANIFEST_NAME, "MANIFEST_CURRENT.json", "VERSION.txt", "RUNTIME_STATE.json",
    "JAZN_ACTIVE_RUNTIME.json", "BOOTSTRAP_JAZN_CURRENT.json",
}
FORBIDDEN_SUFFIXES = {
    ".sqlite", ".sqlite3", ".db", ".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm",
    ".zip", ".log", ".tmp", ".temp", ".bak", ".pyc",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_is_forbidden(relative: str) -> bool:
    rel = relative.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    parts = [part for part in rel.split("/") if part]
    lower_parts = [part.lower() for part in parts]
    if not parts or lower_parts[0] in {name.lower() for name in FORBIDDEN_ROOT_NAMES}:
        return True
    name = parts[-1]
    lower_name = name.lower()
    if name in FORBIDDEN_FILE_NAMES:
        return True
    if lower_name == ".env" or lower_name.startswith(".env.") and lower_name != ".env.example":
        return True
    if any(token in lower_name for token in ("secret", "private_key", "credentials")):
        return True
    if any(lower_name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return True
    if ".zip." in lower_name or lower_name.endswith(("-wal", "-shm")):
        return True
    return False


def _git_paths(root: Path) -> tuple[list[str], list[str]]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True, stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {completed.stderr.strip()}")
    candidates = sorted({line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()})
    missing = [path for path in candidates if not (root / path).is_file()]
    return candidates, missing


def _walk_paths(root: Path) -> Iterable[str]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path.relative_to(root).as_posix()


def build_package_integrity_manifest(root: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    runtime_version = read_runtime_version_from_version_py(root)
    if not runtime_version:
        raise RuntimeError("latka_jazn/version.py is missing or invalid")
    if (root / ".git").exists():
        candidates, missing = _git_paths(root)
        if missing:
            raise RuntimeError(f"tracked/unignored files missing from working tree: {missing[:10]}")
    else:
        candidates = list(_walk_paths(root))
    files: list[dict[str, Any]] = []
    excluded: list[str] = []
    for relative in candidates:
        if path_is_forbidden(relative):
            excluded.append(relative)
            continue
        path = root / relative
        if not path.is_file():
            continue
        files.append({
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "mutable_runtime": False,
            "classification": "static_project_file",
            "archive": False,
            "hash_policy": "sha256_file_bytes",
        })
    present = {entry["path"] for entry in files}
    missing_required = sorted(REQUIRED_STATIC_PATHS - present)
    if missing_required:
        raise RuntimeError(f"required static files missing: {missing_required}")
    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": schema_version("package_integrity_manifest"),
        "version": runtime_version,
        "runtime_version": runtime_version,
        "package_version": runtime_version,
        "generated_at_utc": generated_at,
        "updated_at_utc": generated_at,
        "start_file": "run.py",
        "file_count": len(files),
        "static_file_count": len(files),
        "mutable_runtime_file_count": 0,
        "runtime_mutable_file_count": 0,
        "excluded_file_count": len(excluded),
        "runtime_state_file": "RUNTIME_STATE.json",
        "runtime_memory_split_policy": {
            "static_manifest": "PACKAGE_INTEGRITY_MANIFEST.json protects static project files only.",
            "runtime_state": "Runtime state, memory, SQLite and workspace_runtime are excluded.",
        },
        "excluded_policy": {
            "roots": sorted(FORBIDDEN_ROOT_NAMES),
            "file_names": sorted(FORBIDDEN_FILE_NAMES),
            "suffixes": sorted(FORBIDDEN_SUFFIXES),
        },
        "truth_boundary": (
            "The manifest hashes static files including SOURCE_PROVENANCE.json. It excludes itself, Git history, "
            "memory, runtime state, SQLite, archives, secrets, logs, backups and temporary files."
        ),
        "files": files,
        "excluded_files": excluded,
        "deferred_hash_files": [],
    }


def write_package_integrity_manifest(root: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    payload = build_package_integrity_manifest(root)
    path = root / MANIFEST_NAME
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        temp.replace(path)
    except PermissionError:
        # Windows can deny replacement of an existing tracked file even when
        # overwriting its contents is allowed. Preserve the same complete
        # serialized payload and remove only the generator-owned temp file.
        path.write_bytes(temp.read_bytes())
        temp.unlink(missing_ok=True)
    return payload


def verify_package_integrity_manifest(root: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    path = root / MANIFEST_NAME
    errors: list[dict[str, Any]] = []
    if not path.is_file():
        return {"ok": False, "configuration_error": True, "errors": [{"code": "manifest_missing"}]}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"ok": False, "configuration_error": True, "errors": [{"code": "manifest_invalid_json", "detail": repr(exc)}]}
    entries = payload.get("files") if isinstance(payload.get("files"), list) else None
    if entries is None:
        return {"ok": False, "configuration_error": True, "errors": [{"code": "manifest_files_missing"}]}
    seen: set[str] = set()
    for entry in entries:
        relative = str((entry or {}).get("path") or "").replace("\\", "/")
        if not relative or relative in seen or path_is_forbidden(relative):
            errors.append({"code": "invalid_or_duplicate_manifest_path", "path": relative})
            continue
        seen.add(relative)
        file_path = root / relative
        if not file_path.is_file():
            errors.append({"code": "file_missing", "path": relative})
            continue
        size = file_path.stat().st_size
        digest = sha256_file(file_path)
        if size != int(entry.get("size_bytes", -1)):
            errors.append({"code": "size_mismatch", "path": relative})
        if digest != str(entry.get("sha256") or ""):
            errors.append({"code": "sha256_mismatch", "path": relative})
    for required in sorted(REQUIRED_STATIC_PATHS):
        if required not in seen:
            errors.append({"code": "required_path_unprotected", "path": required})
    runtime_version = read_runtime_version_from_version_py(root)
    if not runtime_version or str(payload.get("runtime_version") or payload.get("version") or "") != runtime_version:
        errors.append({"code": "version_mismatch"})
    return {
        "schema_version": schema_version("package_integrity_verification"),
        "ok": not errors,
        "configuration_error": False,
        "manifest_path": str(path),
        "manifest_sha256": sha256_file(path),
        "checked_file_count": len(entries),
        "errors": errors,
    }
