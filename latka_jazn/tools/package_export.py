from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
import tempfile
import zipfile

from latka_jazn.memory.session_continuity import SessionContinuityManager
from latka_jazn.core.version_source import read_runtime_version_from_version_py
from latka_jazn.version import PACKAGE_VERSION

SYSTEM_EXCLUDE_PREFIXES = (
    "memory/",
    "workspace_runtime/",
    "exports/",
)
NLP_INCLUDE_PREFIXES = (
    "latka_jazn/nlp/",
    "latka_jazn/resources/",
)
NLP_INCLUDE_EXACT = {
    "MANIFEST_V14_6_1_NLP_ADAPTER_ZIP_PROFILES.json",
    "MANIFEST_V14_6_1_12_RUNTIME_PREVIEW_SOURCE_ORIGIN_SELF_STATE.json",
    "MANIFEST_V14_6_1_13_COGNITIVE_TURN_ENVELOPE.json",
    "UPDATE_REPORT_V14_6_1.md",
    "UPDATE_REPORT_V14_6_1_12.md",
    "UPDATE_REPORT_V14_6_1_14.md",
    "UPDATE_REPORT_V14_6_2.md",
    "UPDATE_REPORT_V14_6_2_1.md",
    "MANIFEST_V14_6_2_1_STALE_NLP_ROUTE_HOTFIX.json",
    "docs/UPDATE_V14_6_1_NLP_ADAPTER_ZIP_PROFILES.md",
    "docs/UPDATE_V14_6_1_12_RUNTIME_PREVIEW_SOURCE_ORIGIN_SELF_STATE.md",
    "docs/UPDATE_V14_6_1_13_COGNITIVE_TURN_ENVELOPE.md",
    "docs/UPDATE_V14_6_2_CONTEXTUAL_GREETING_FALLBACK_REPAIR.md",
    "docs/UPDATE_V14_6_2_1_STALE_NLP_ROUTE_HOTFIX.md",
}
GITHUB_SAFE_EXCLUDE_PREFIXES = (
    "memory/",
    "workspace_runtime/",
    "exports/",
)
# Source-safe means safe by provenance and content, not merely by directory.
GITHUB_SAFE_PRIVATE_EXACT = {
    "latka_jazn/core/canon/local_private_canon_extension.py",
    "latka_jazn/contracts/embedded_sources.py",
}
_PRIVATE_MARKER_PARTS = (
    ("local_private", "do_not_commit_without_review"),
    ("generated_from", "private_memory"),
    ("raw_conversation", "embedded_source"),
)
COMMON_EXCLUDE_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
}
COMMON_EXCLUDE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".tmp",
    ".bak",
    ".zip",
    ".sqlite3-wal",
    ".sqlite3-shm",
    "-wal",
    "-shm",
)
FORBIDDEN_PACKAGE_PREFIXES = (
    "workspace_runtime/",
    "requests/",
    "responses/",
    "processed/",
    "status/",
    "logs/",
    "log/",
    ".pytest_cache/",
)
FORBIDDEN_PACKAGE_EXACT = {
    "workspace_runtime",
    "workspace_runtime/JAZN_ACTIVE_RUNTIME.json",
    "ACTIVE_RUNTIME_CACHE_CONTRACT.json",
    "BOOTSTRAP_JAZN_CURRENT.json",
    "RUNTIME_STATE.json",
    "runtime_session_state.json",
    ".pytest_cache",
}
FORBIDDEN_PACKAGE_GLOBS = (
    "workspace_runtime/pytest_*",
    "runtime-preview-*.json",
    "*.sqlite3-wal",
    "*.sqlite3-shm",
    "*/codex_session_bridge/requests/*",
    "*/codex_session_bridge/responses/*",
    "*/codex_session_bridge/processed/*",
    "*/codex_session_bridge/status/*",
    "*/codex_session_bridge/logs/*",
    "*/codex_session_bridge/log/*",
)
SKIP_EXPANDED_RAW_CHAT_IF_ARCHIVE_PRESENT = True


class PackagePlanValidationError(ValueError):
    pass


@dataclass(slots=True)
class PackageExportReport:
    mode: str
    output_zip: str
    created_at_utc: str
    file_count: int
    total_uncompressed_bytes: int
    zip_size_bytes: int
    sha256: str
    includes_memory: bool
    includes_system: bool
    package_manifest_path: str
    packing_audit_path: str
    crc_ok: bool
    extract_smoke_ok: bool
    notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_package_rel(rel: str) -> str:
    return rel.replace("\\", "/").lstrip("/")


def forbidden_package_reason(rel: str) -> str | None:
    raw = str(rel).replace("\\", "/")
    raw_parts = Path(raw).parts
    if "\x00" in raw:
        return "NUL byte is forbidden in package paths"
    if raw.startswith(("/", "\\")) or (raw_parts and ":" in raw_parts[0]):
        return "absolute or drive path is forbidden"
    if ".." in raw_parts:
        return "parent traversal is forbidden"
    rel = _normalize_package_rel(raw)
    parts = set(Path(rel).parts)
    if "__pycache__" in parts:
        return "__pycache__ is never packaged"
    if ".pytest_cache" in parts:
        return ".pytest_cache is never packaged"
    if rel in FORBIDDEN_PACKAGE_EXACT:
        return "runtime/root marker is never packaged"
    if rel.startswith(FORBIDDEN_PACKAGE_PREFIXES):
        return "runtime or bridge queue directory is never packaged"
    if any(Path(rel).match(pattern) for pattern in FORBIDDEN_PACKAGE_GLOBS):
        return "forbidden runtime/cache pattern is never packaged"
    return None


def find_forbidden_package_paths(rel_paths) -> list[tuple[str, str]]:
    blocked: list[tuple[str, str]] = []
    for rel in rel_paths:
        normalized = _normalize_package_rel(str(rel))
        reason = forbidden_package_reason(normalized)
        if reason:
            blocked.append((normalized, reason))
    return blocked


def validate_package_plan(rel_paths) -> None:
    blocked = find_forbidden_package_paths(rel_paths)
    if not blocked:
        return
    examples = ", ".join(f"{rel} ({reason})" for rel, reason in blocked[:10])
    more = "" if len(blocked) <= 10 else f"; +{len(blocked) - 10} more"
    raise PackagePlanValidationError(f"Forbidden paths in package plan: {examples}{more}")


def _is_common_excluded(path: Path, rel: str, output_zip: Path) -> bool:
    if path == output_zip:
        return True
    if forbidden_package_reason(rel):
        return True
    if any(part in COMMON_EXCLUDE_PARTS for part in path.parts):
        return True
    if rel.startswith("exports/"):
        return True
    return any(rel.endswith(suffix) for suffix in COMMON_EXCLUDE_SUFFIXES)


def private_generated_source_reason(path: Path, rel: str) -> str | None:
    """Return a blocking provenance reason for source-safe export candidates.

    The scanner intentionally checks exact known generated sources first and then
    a small, bounded text prefix. Marker literals are assembled from parts so the
    scanner implementation cannot match itself merely because it documents them.
    """
    rel = _normalize_package_rel(rel)
    if rel in GITHUB_SAFE_PRIVATE_EXACT:
        return "known_private_generated_source"
    if path.suffix.lower() not in {".py", ".json", ".jsonl", ".md", ".txt"}:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:262144].lower()
    except OSError:
        return "source_unreadable_for_privacy_scan"
    for left, right in _PRIVATE_MARKER_PARTS:
        marker = left + "_" + right
        if marker in text:
            return f"private_marker:{marker}"
    return None


def _iter_files(root: Path, mode: str, output_zip: Path):
    root = Path(root).resolve()
    output_zip = Path(output_zip).resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_common_excluded(path.resolve(), rel, output_zip):
            continue
        if (
            SKIP_EXPANDED_RAW_CHAT_IF_ARCHIVE_PRESENT
            and rel == "memory/raw/chat.html"
            and (root / "memory" / "raw" / "chat.html.7z").exists()
        ):
            continue
        if mode == "system" and rel.startswith(SYSTEM_EXCLUDE_PREFIXES):
            continue
        if mode == "memory" and not (rel.startswith("memory/") or rel.startswith("workspace_runtime/")):
            continue
        if mode == "nlp" and not (rel.startswith(NLP_INCLUDE_PREFIXES) or rel in NLP_INCLUDE_EXACT):
            continue
        if mode == "github_source_safe":
            if rel.startswith(GITHUB_SAFE_EXCLUDE_PREFIXES):
                continue
            if private_generated_source_reason(path, rel):
                continue
        yield path, rel


def build_package_plan(root: Path, mode: str, output_zip: Path | None = None) -> list[tuple[Path, str]]:
    """Build the exact immutable-by-value plan used by preview and export."""
    root = Path(root).resolve()
    preview_output = Path(output_zip).resolve() if output_zip is not None else (root / "exports" / ".preview.zip").resolve()
    plan = list(_iter_files(root, mode, preview_output))
    validate_package_plan(rel for _, rel in plan)
    if mode == "github_source_safe":
        blocked = [
            {"path": rel, "reason": reason}
            for path, rel in plan
            if (reason := private_generated_source_reason(path, rel))
        ]
        if blocked:
            raise PackagePlanValidationError(
                "Private generated sources remain in source-safe plan: "
                + json.dumps(blocked[:10], ensure_ascii=False)
            )
    return plan


def _checkpoint_sqlite_databases(root: Path) -> list[str]:
    """Record active WAL files without blocking export.

    Transient WAL/SHM files are excluded from the archive. The note preserves
    the truth that a checkpoint was not forced while another process could be
    using a database.
    """
    notes: list[str] = []
    for db in sorted(Path(root).rglob("*.sqlite3")):
        try:
            rel = db.relative_to(root).as_posix()
        except Exception:
            rel = str(db)
        if forbidden_package_reason(rel):
            continue
        if any(part in COMMON_EXCLUDE_PARTS for part in db.parts):
            continue
        if Path(str(db) + "-wal").exists() or Path(str(db) + "-shm").exists():
            notes.append(f"Pominięto blokujący checkpoint WAL dla {rel}; transient WAL/SHM nie są pakowane.")
    return notes


def _unsafe_zip_entries(zf: zipfile.ZipFile) -> list[str]:
    unsafe: list[str] = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        path = Path(name)
        if name.startswith("/") or ".." in path.parts or (path.parts and ":" in path.parts[0]):
            unsafe.append(name)
    return unsafe


def build_package_manifest(zip_path: Path, *, mode: str) -> dict:
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        entries = [
            {
                "path": info.filename,
                "size_bytes": info.file_size,
                "compressed_size_bytes": info.compress_size,
                "crc32": f"{info.CRC:08x}",
                "is_dir": info.is_dir(),
            }
            for info in zf.infolist()
        ]
    return {
        "schema_version": f"package_manifest/{PACKAGE_VERSION}",
        "archive_name": zip_path.name,
        "archive_sha256": _sha256_file(zip_path),
        "mode": mode,
        "entry_count": len(entries),
        "entries": entries,
        "truth_boundary": "Manifest opisuje wpisy faktycznie zapisane w ZIP-ie; nie jest markerem aktywnego runtime.",
    }


def build_packing_audit(zip_path: Path, package_manifest: dict) -> dict:
    zip_path = Path(zip_path)
    errors: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = [info.filename for info in zf.infolist()]
        unsafe = _unsafe_zip_entries(zf)
        forbidden = find_forbidden_package_paths(names)
        bad_crc = zf.testzip()
        if unsafe:
            errors.append("unsafe_paths")
        if forbidden:
            errors.append("forbidden_paths")
        if bad_crc:
            errors.append(f"crc_failure:{bad_crc}")
        extracted: list[str] = []
        extract_error = None
        if not errors:
            try:
                with tempfile.TemporaryDirectory(prefix="jazn_package_smoke_") as tmp:
                    target = Path(tmp)
                    zf.extractall(target)
                    extracted = sorted(
                        item.relative_to(target).as_posix()
                        for item in target.rglob("*")
                        if item.is_file()
                    )
            except Exception as exc:
                extract_error = f"{type(exc).__name__}: {exc}"
                errors.append("extract_smoke_failed")
        expected = sorted(
            entry["path"]
            for entry in package_manifest.get("entries", [])
            if not entry.get("is_dir")
        )
        extract_ok = extract_error is None and extracted == expected and not errors
    return {
        "schema_version": f"packing_audit/{PACKAGE_VERSION}",
        "archive_name": zip_path.name,
        "archive_sha256": _sha256_file(zip_path),
        "entry_count": len(package_manifest.get("entries", [])),
        "manifest_entry_count_matches": len(package_manifest.get("entries", [])) == len(names),
        "unsafe_paths": unsafe,
        "forbidden_paths": [{"path": path, "reason": reason} for path, reason in forbidden],
        "crc_ok": bad_crc is None,
        "crc_failure_entry": bad_crc,
        "extract_smoke_ok": extract_ok,
        "extract_error": extract_error,
        "errors": errors,
        "ok": not errors and bad_crc is None and extract_ok,
        "truth_boundary": "PACKING_AUDIT potwierdza transport ZIP, CRC, ścieżki i świeże rozpakowanie; nie potwierdza działania runtime.",
    }


def export_package(root: Path, mode: str, output_zip: Path | None = None) -> PackageExportReport:
    """Create a validated system, memory, NLP, GitHub-safe or full ZIP."""
    root = Path(root).resolve()
    if mode not in {"system", "memory", "nlp", "github_source_safe", "full"}:
        raise ValueError("mode must be one of: system, memory, nlp, github_source_safe, full")
    exports_dir = root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if output_zip is None:
        output_zip = exports_dir / f"latka_jazn_{mode}_{stamp}.zip"
    else:
        output_zip = Path(output_zip)
        if not output_zip.is_absolute():
            output_zip = root / output_zip
        output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()

    notes: list[str] = []
    if mode in {"memory", "full"}:
        try:
            SessionContinuityManager(
                root,
                version=read_runtime_version_from_version_py(root, fallback="unknown") or "unknown",
            ).update_index(reason=f"export_{mode}", source="package_export.export_package")
            notes.append("Zaktualizowano session_continuity_index.json przed eksportem pamięci/pełnej paczki.")
        except Exception as exc:
            notes.append(f"Nie udało się odświeżyć session_continuity_index.json przed eksportem: {exc!r}")
        notes.extend(_checkpoint_sqlite_databases(root))

    file_count = 0
    total = 0
    package_plan = build_package_plan(root, mode, output_zip)
    with zipfile.ZipFile(
        output_zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
        compresslevel=1,
    ) as zf:
        for path, rel in package_plan:
            stat = path.stat()
            total += stat.st_size
            file_count += 1
            compress_type = zipfile.ZIP_STORED if rel.endswith((".sqlite3", ".7z")) else zipfile.ZIP_DEFLATED
            zf.write(path, rel, compress_type=compress_type)

    if file_count == 0:
        notes.append("Paczka nie zawierała plików; sprawdź tryb eksportu i ścieżkę root.")
    if mode in {"memory", "full"}:
        continuity_index = root / "memory" / "raw" / "session_continuity_index.json"
        if continuity_index.exists():
            notes.append("Dołączono memory/raw/session_continuity_index.json oraz memory/layered/continuity.jsonl, jeśli istnieje.")
        raw_chat = root / "memory" / "raw" / "chat.html"
        raw_archive = root / "memory" / "raw" / "chat.html.7z"
        if raw_archive.exists():
            notes.append(f"Dołączono skompresowaną surową pamięć chat.html.7z ({raw_archive.stat().st_size} B).")
            if raw_chat.exists() and SKIP_EXPANDED_RAW_CHAT_IF_ARCHIVE_PRESENT:
                notes.append("Pominięto rozpakowany memory/raw/chat.html, aby nie dublować danych.")
        elif raw_chat.exists():
            notes.append(f"Dołączono rozpakowaną surową pamięć chat.html ({raw_chat.stat().st_size} B).")
        else:
            notes.append("Nie znaleziono memory/raw/chat.html ani chat.html.7z.")
    if mode == "system":
        notes.append("Eksport system-only celowo pomija memory/ oraz workspace_runtime/.")
    if mode == "nlp":
        notes.append("Eksport NLP-resources-only zawiera adaptery i lekkie zasoby NLP; nie zawiera pamięci ani ciężkich modeli.")
    if mode == "github_source_safe":
        notes.append("Eksport github-source-safe pomija cały katalog memory/, workspace_runtime/, surowe czaty i aktywne bazy SQLite.")

    package_manifest = build_package_manifest(output_zip, mode=mode)
    packing_audit = build_packing_audit(output_zip, package_manifest)
    package_manifest_path = output_zip.with_name(output_zip.name + ".package_manifest.json")
    packing_audit_path = output_zip.with_name(output_zip.name + ".PACKING_AUDIT.json")
    package_manifest_text = json.dumps(package_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    packing_audit_text = json.dumps(packing_audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    package_manifest_path.write_text(package_manifest_text, encoding="utf-8")
    packing_audit_path.write_text(packing_audit_text, encoding="utf-8")
    (output_zip.parent / "package_manifest.json").write_text(package_manifest_text, encoding="utf-8")
    (output_zip.parent / "PACKING_AUDIT.json").write_text(packing_audit_text, encoding="utf-8")
    if not packing_audit["ok"]:
        raise PackagePlanValidationError(
            "Packing audit failed: " + ", ".join(packing_audit.get("errors") or ["unknown_error"])
        )

    report = PackageExportReport(
        mode=mode,
        output_zip=str(output_zip),
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        file_count=file_count,
        total_uncompressed_bytes=total,
        zip_size_bytes=output_zip.stat().st_size,
        sha256=_sha256_file(output_zip),
        includes_memory=mode in {"memory", "full"},
        includes_system=mode in {"system", "nlp", "github_source_safe", "full"},
        package_manifest_path=str(package_manifest_path),
        packing_audit_path=str(packing_audit_path),
        crc_ok=bool(packing_audit["crc_ok"]),
        extract_smoke_ok=bool(packing_audit["extract_smoke_ok"]),
        notes=notes,
    )
    report_path = output_zip.with_suffix(".report.json")
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def export_package_json(root: Path, mode: str, output_zip: Path | None = None) -> str:
    return json.dumps(
        export_package(root, mode, output_zip).to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
