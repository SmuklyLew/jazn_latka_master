from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import subprocess
import zipfile

from latka_jazn.core.version_source import read_runtime_version_from_version_py
from latka_jazn.tools.safe_paths import (
    UnsafeRelativePathError,
    resolve_safe_path,
    resolve_safe_source,
    validate_safe_relative_path,
)
from latka_jazn.version import schema_version

MANIFEST_NAME = "PACKAGE_INTEGRITY_MANIFEST.json"
REQUIRED_STATIC_PATHS = {"SOURCE_PROVENANCE.json", "run.py", "main.py", "latka_jazn/version.py"}
FORBIDDEN_ROOT_NAMES = {
    ".git", "memory", "workspace_runtime", "backups", ".pytest_cache", "__pycache__",
}
FORBIDDEN_FILE_NAMES = {
    MANIFEST_NAME, "MANIFEST_CURRENT.json", "VERSION.txt", "RUNTIME_STATE.json",
    "JAZN_ACTIVE_RUNTIME.json", "BOOTSTRAP_JAZN_CURRENT.json",
    "__jazn_pack_generator.lock.json", "__jazn_pack_generator_settings.json",
    "_jazn_pack_generator.before.py",
}
FORBIDDEN_SUFFIXES = {
    ".sqlite", ".sqlite3", ".db", ".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm",
    ".zip", ".log", ".tmp", ".temp", ".bak", ".pyc", ".before.py",
}
_VERSION_VARIABLES = ("PACKAGE_VERSION", "__version__", "VERSION", "DISTRIBUTION_VERSION")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_checkout_head_for_verification(root: Path) -> tuple[str | None, str]:
    """Return a trusted HEAD for canonical verification or a fallback reason.

    A clean Git checkout may contain platform-specific worktree bytes, such as
    CRLF produced by Git on Windows, while the release manifest intentionally
    protects canonical Git blobs. Canonical verification is allowed only for
    the exact repository root, a clean index/worktree and ordinary tracked
    files without ``assume-unchanged`` or ``skip-worktree`` flags.
    """

    top_level = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if top_level.returncode != 0:
        return None, "not_a_git_checkout"
    try:
        repository_root = Path(top_level.stdout.strip()).resolve()
    except (OSError, RuntimeError):
        return None, "git_root_unresolvable"
    if repository_root != root:
        return None, "not_exact_repository_root"

    unstaged = subprocess.run(
        ["git", "-C", str(root), "diff", "--quiet", "--ignore-submodules", "--"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if unstaged.returncode not in {0, 1}:
        return None, "git_diff_failed"
    if unstaged.returncode == 1:
        return None, "dirty"

    staged = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--quiet", "--ignore-submodules", "--"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if staged.returncode not in {0, 1}:
        return None, "git_diff_failed"
    if staged.returncode == 1:
        return None, "dirty"

    untracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if untracked.returncode != 0:
        return None, "git_untracked_probe_failed"
    if untracked.stdout:
        return None, "dirty"

    assume_flags = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-v", "-z"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if assume_flags.returncode != 0:
        return None, "git_index_flags_unavailable"
    for record in assume_flags.stdout.split(b"\0"):
        tag = record[:1]
        if tag and tag.isalpha() and tag.islower():
            return None, "assume_unchanged_present"

    stage_flags = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-t", "-z"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if stage_flags.returncode != 0:
        return None, "git_index_flags_unavailable"
    if any(record[:1] == b"S" for record in stage_flags.stdout.split(b"\0") if record):
        return None, "skip_worktree_present"

    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="ascii",
        errors="replace",
        check=False,
    )
    value = head.stdout.strip().lower()
    if (
        head.returncode != 0
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        return None, "git_head_invalid"
    return value, "clean"


def _git_blob_bytes(root: Path, head: str, relative: str) -> bytes | None:
    completed = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", f"{head}:{relative}"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def path_is_forbidden(relative: str) -> bool:
    try:
        rel = validate_safe_relative_path(relative)
    except UnsafeRelativePathError:
        return True
    parts = [part for part in rel.split("/") if part]
    lower_parts = [part.lower() for part in parts]
    forbidden_roots = {name.lower() for name in FORBIDDEN_ROOT_NAMES}
    if not parts or lower_parts[0] in forbidden_roots:
        return True
    name = parts[-1]
    lower_name = name.lower()
    if lower_name in {item.lower() for item in FORBIDDEN_FILE_NAMES}:
        return True
    if lower_name == ".env" or lower_name.startswith(".env.") and lower_name != ".env.example":
        return True
    if any(token in lower_name for token in ("secret", "private_key", "credentials")):
        return True
    if any(lower_name.endswith(suffix.lower()) for suffix in FORBIDDEN_SUFFIXES):
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


def _selected_paths(root: Path, relative_paths: Iterable[str] | None) -> list[str]:
    if relative_paths is None:
        if (root / ".git").exists():
            candidates, missing = _git_paths(root)
            if missing:
                raise RuntimeError(f"tracked/unignored files missing from working tree: {missing[:10]}")
            return candidates
        return list(_walk_paths(root))

    selected: set[str] = set()
    missing: list[str] = []
    for raw in relative_paths:
        try:
            relative = validate_safe_relative_path(str(raw))
            path = resolve_safe_source(root, relative)
        except UnsafeRelativePathError:
            continue
        if not path.is_file():
            missing.append(relative)
            continue
        selected.add(relative)
    if missing:
        raise RuntimeError(f"selected package files missing from source tree: {missing[:10]}")
    return sorted(selected)


def build_package_integrity_manifest(
    root: Path | str,
    *,
    relative_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build the canonical static manifest.

    ``relative_paths`` narrows the manifest to the exact immutable package plan.
    This is used by ZIP exporters so preview, manifest and archive cannot drift.
    The manifest file itself and runtime/memory artifacts remain excluded.
    """

    root = Path(root).resolve()
    runtime_version = read_runtime_version_from_version_py(root)
    if not runtime_version:
        raise RuntimeError("latka_jazn/version.py is missing or invalid")
    candidates = _selected_paths(root, relative_paths)
    files: list[dict[str, Any]] = []
    excluded: list[str] = []
    for relative in candidates:
        try:
            relative = validate_safe_relative_path(relative)
            path = resolve_safe_source(root, relative)
        except UnsafeRelativePathError:
            excluded.append(str(relative))
            continue
        if path_is_forbidden(relative):
            excluded.append(relative)
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
            "The manifest hashes the exact static package plan including SOURCE_PROVENANCE.json. "
            "It excludes itself, Git history, memory, runtime state, SQLite, archives, secrets, logs, "
            "backups, generator state and temporary files."
        ),
        "files": files,
        "excluded_files": excluded,
        "deferred_hash_files": [],
    }


def serialize_package_integrity_manifest(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_package_integrity_manifest(
    root: Path | str,
    *,
    relative_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    payload = build_package_integrity_manifest(root, relative_paths=relative_paths)
    path = root / MANIFEST_NAME
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(serialize_package_integrity_manifest(payload))
    try:
        temp.replace(path)
    except PermissionError:
        # Windows can deny replacement of an existing tracked file even when
        # overwriting its contents is allowed. Preserve the same complete
        # serialized payload and remove only the generator-owned temp file.
        path.write_bytes(temp.read_bytes())
        temp.unlink(missing_ok=True)
    return payload


def _manifest_entries(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    entries = payload.get("files")
    if not isinstance(entries, list):
        return None
    return [entry for entry in entries if isinstance(entry, dict)]


def verify_package_integrity_manifest(root: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    git_head, worktree_state = _git_checkout_head_for_verification(root)
    verification_basis = "canonical_git_head_blobs" if git_head else "filesystem_bytes"
    path = root / MANIFEST_NAME
    errors: list[dict[str, Any]] = []
    if not path.is_file():
        return {"ok": False, "configuration_error": True, "errors": [{"code": "manifest_missing"}]}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"ok": False, "configuration_error": True, "errors": [{"code": "manifest_invalid_json", "detail": repr(exc)}]}
    entries = _manifest_entries(payload) if isinstance(payload, dict) else None
    if entries is None:
        return {"ok": False, "configuration_error": True, "errors": [{"code": "manifest_files_missing"}]}
    seen: set[str] = set()
    for entry in entries:
        raw_relative = entry.get("path")
        relative = str(raw_relative) if raw_relative is not None else ""
        try:
            canonical = validate_safe_relative_path(relative)
            file_path = resolve_safe_path(root, canonical)
        except UnsafeRelativePathError as exc:
            errors.append({"code": "unsafe_manifest_path", "path": relative, "detail": str(exc)})
            continue
        if canonical in seen or path_is_forbidden(canonical):
            errors.append({"code": "invalid_or_duplicate_manifest_path", "path": canonical})
            continue
        seen.add(canonical)
        if not file_path.is_file():
            errors.append({"code": "file_missing", "path": canonical})
            continue
        if git_head:
            raw = _git_blob_bytes(root, git_head, canonical)
            if raw is None:
                errors.append({"code": "git_blob_missing", "path": canonical})
                continue
            size = len(raw)
            digest = hashlib.sha256(raw).hexdigest()
        else:
            size = file_path.stat().st_size
            digest = sha256_file(file_path)
        if size != int(entry.get("size_bytes", -1)):
            errors.append({"code": "size_mismatch", "path": canonical})
        if digest != str(entry.get("sha256") or ""):
            errors.append({"code": "sha256_mismatch", "path": canonical})
    for required in sorted(REQUIRED_STATIC_PATHS):
        if required not in seen:
            errors.append({"code": "required_path_unprotected", "path": required})
    if git_head:
        version_bytes = _git_blob_bytes(root, git_head, "latka_jazn/version.py")
        runtime_version = _version_from_python_bytes(version_bytes) if version_bytes is not None else None
    else:
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
        "verification_basis": verification_basis,
        "worktree_state": worktree_state,
        "git_head": git_head,
    }


def _version_from_python_bytes(raw: bytes) -> str | None:
    """Read the canonical full package version from archived ``version.py``.

    ``PACKAGE_VERSION`` and ``PACKAGE_RELEASE_NAME`` are separate literal fields
    in the authoritative module. ``PACKAGE_VERSION_FULL`` is commonly an f-string,
    so it cannot be recovered by reading constants alone and must be reconstructed.
    """

    try:
        tree = ast.parse(raw.decode("utf-8-sig"))
    except Exception:
        return None
    values: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value.value.strip()
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                values[node.target.id] = value.value.strip()

    package_version = values.get("PACKAGE_VERSION", "").strip()
    release_name = values.get("PACKAGE_RELEASE_NAME", "").strip()
    if package_version:
        if release_name and not package_version.lower().endswith(f"-{release_name}".lower()):
            return f"{package_version}-{release_name}"
        return package_version

    for name in _VERSION_VARIABLES:
        value = values.get(name)
        if value:
            return value
    return None


def verify_package_integrity_manifest_in_zips(
    zip_paths: Path | str | Iterable[Path | str],
    *,
    allowed_unprotected_prefixes: Iterable[str] = (),
) -> dict[str, Any]:
    """Verify one ZIP or a set of independent ZIP volumes against the embedded manifest."""

    if isinstance(zip_paths, (str, Path)):
        paths = [Path(zip_paths).resolve()]
    else:
        paths = [Path(path).resolve() for path in zip_paths]
    allowed_prefixes = tuple(
        validate_safe_relative_path(str(prefix).rstrip("/")) + "/"
        for prefix in allowed_unprotected_prefixes
        if str(prefix).strip()
    )
    errors: list[dict[str, Any]] = []
    members: dict[str, tuple[Path, zipfile.ZipInfo]] = {}
    manifest_bytes: bytes | None = None

    for zip_path in paths:
        if not zip_path.is_file():
            errors.append({"code": "zip_missing", "path": str(zip_path)})
            continue
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    try:
                        canonical = validate_safe_relative_path(info.filename)
                    except UnsafeRelativePathError as exc:
                        errors.append({"code": "unsafe_zip_member", "path": info.filename, "detail": str(exc)})
                        continue
                    if canonical in members:
                        errors.append({"code": "duplicate_zip_member", "path": canonical})
                        continue
                    members[canonical] = (zip_path, info)
                    if canonical == MANIFEST_NAME:
                        manifest_bytes = archive.read(info)
        except Exception as exc:
            errors.append({"code": "zip_open_failed", "path": str(zip_path), "detail": repr(exc)})

    payload: dict[str, Any] = {}
    if manifest_bytes is None:
        errors.append({"code": "manifest_missing"})
        entries: list[dict[str, Any]] = []
    else:
        try:
            decoded = json.loads(manifest_bytes.decode("utf-8-sig"))
            if not isinstance(decoded, dict):
                raise ValueError("manifest is not a JSON object")
            payload = decoded
        except Exception as exc:
            errors.append({"code": "manifest_invalid_json", "detail": repr(exc)})
        entries = _manifest_entries(payload) or []
        if not isinstance(payload.get("files"), list):
            errors.append({"code": "manifest_files_missing"})

    listed: set[str] = set()
    checked = 0
    for entry in entries:
        relative = str(entry.get("path") or "")
        try:
            canonical = validate_safe_relative_path(relative)
        except UnsafeRelativePathError as exc:
            errors.append({"code": "unsafe_manifest_path", "path": relative, "detail": str(exc)})
            continue
        if canonical in listed or path_is_forbidden(canonical):
            errors.append({"code": "invalid_or_duplicate_manifest_path", "path": canonical})
            continue
        listed.add(canonical)
        member = members.get(canonical)
        if member is None:
            errors.append({"code": "file_missing", "path": canonical})
            continue
        zip_path, info = member
        expected_size = int(entry.get("size_bytes", -1))
        if info.file_size != expected_size:
            errors.append({"code": "size_mismatch", "path": canonical, "expected": expected_size, "actual": info.file_size})
        digest = hashlib.sha256()
        with zipfile.ZipFile(zip_path, "r") as archive:
            with archive.open(info, "r") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        actual_hash = digest.hexdigest()
        expected_hash = str(entry.get("sha256") or "")
        if actual_hash != expected_hash:
            errors.append({"code": "sha256_mismatch", "path": canonical, "expected": expected_hash, "actual": actual_hash})
        checked += 1

    for required in sorted(REQUIRED_STATIC_PATHS):
        if required not in listed:
            errors.append({"code": "required_path_unprotected", "path": required})

    version_member = members.get("latka_jazn/version.py")
    archive_version = None
    if version_member is not None:
        version_zip, version_info = version_member
        with zipfile.ZipFile(version_zip, "r") as archive:
            archive_version = _version_from_python_bytes(archive.read(version_info))
    manifest_version = str(payload.get("runtime_version") or payload.get("version") or "")
    if not archive_version or manifest_version != archive_version:
        errors.append({"code": "version_mismatch", "manifest": manifest_version, "archive": archive_version})

    allowed = set(listed)
    allowed.add(MANIFEST_NAME)
    for relative in sorted(set(members) - allowed):
        if any(relative.startswith(prefix) for prefix in allowed_prefixes):
            continue
        errors.append({"code": "unexpected_zip_member", "path": relative})

    return {
        "schema_version": schema_version("package_integrity_zip_verification"),
        "ok": not errors,
        "zip_paths": [str(path) for path in paths],
        "manifest_runtime_version": manifest_version,
        "archive_runtime_version": archive_version,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest() if manifest_bytes is not None else None,
        "checked_file_count": checked,
        "member_count": len(members),
        "errors": errors,
    }
