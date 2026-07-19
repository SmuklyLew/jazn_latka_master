from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence
import hashlib
import json
import os
import subprocess
import sys

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_restore")
ProgressCallback = Callable[[dict[str, Any]], None]
RestoreMode = Literal["developer", "system"]
SUPPORTED_SUFFIXES = {".zip", ".json", ".jsonl", ".ndjson", ".html", ".htm"}
SYSTEM_CONFIRMATION = "SYSTEM_RESTORE"
DEVELOPER_CONFIRMATION = "RESTORE"
KNOWN_NON_MEMORY_JSON_NAMES = {
    "package_integrity_manifest.json",
    "source_provenance.json",
    "chatgpt-app-submission.json",
}
KNOWN_NON_MEMORY_JSON_SUFFIXES = (".package.json", ".manifest.json", ".report.json", ".settings.json")

@dataclass(slots=True)
class MemoryRestoreSettings:
    source_directory: str = ""
    target_root: str = ""
    mode: RestoreMode = "developer"
    recursive_scan: bool = False
    verify_after_each: bool = True
    full_validation: bool = True
    continue_on_error: bool = False
    create_backup: bool = True
    audit_classifiers: bool = True
    reclassify_journal_dry_run: bool = True
    apply_reclassification: bool = False
    analyse_topics: bool = False
    force_topics: bool = False
    candidate_limit: int = 0
    progress_every_conversations: int = 5
    baseline_roots: list[str] = field(default_factory=list)

    def normalized(self) -> "MemoryRestoreSettings":
        source = str(Path(self.source_directory).expanduser().resolve()) if self.source_directory else ""
        target = str(Path(self.target_root).expanduser().resolve()) if self.target_root else ""
        baselines = [str(Path(item).expanduser().resolve()) for item in self.baseline_roots if str(item).strip()]
        return MemoryRestoreSettings(
            source_directory=source,
            target_root=target,
            mode=self.mode,
            recursive_scan=bool(self.recursive_scan),
            verify_after_each=bool(self.verify_after_each),
            full_validation=bool(self.full_validation),
            continue_on_error=bool(self.continue_on_error),
            create_backup=bool(self.create_backup),
            audit_classifiers=bool(self.audit_classifiers),
            reclassify_journal_dry_run=bool(self.reclassify_journal_dry_run),
            apply_reclassification=bool(self.apply_reclassification),
            analyse_topics=bool(self.analyse_topics),
            force_topics=bool(self.force_topics),
            candidate_limit=max(0, int(self.candidate_limit)),
            progress_every_conversations=max(1, int(self.progress_every_conversations)),
            baseline_roots=baselines,
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "MemoryRestoreSettings":
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("restore settings must be a JSON object")
        return cls(**payload).normalized()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalized())

@dataclass(slots=True, frozen=True)
class RestoreSource:
    path: Path
    size_bytes: int
    suffix: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "name": self.path.name, "size_bytes": self.size_bytes, "suffix": self.suffix}

@dataclass(slots=True)
class MemoryRestorePlan:
    settings: MemoryRestoreSettings
    selected_sources: list[Path]
    chats: list[dict[str, Any]]
    journals: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    target_preflight: dict[str, Any]
    current_status: dict[str, Any]

    @property
    def ok(self) -> bool:
        return bool(self.chats or self.journals) and not self.target_preflight.get("blocking_errors")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "schema_version": SCHEMA_VERSION,
            "settings": self.settings.to_dict(),
            "selected_source_count": len(self.selected_sources),
            "chat_source_count": len(self.chats),
            "journal_source_count": len(self.journals),
            "rejected_source_count": len(self.rejected),
            "chats": self.chats,
            "journals": self.journals,
            "rejected": self.rejected,
            "target_preflight": self.target_preflight,
            "current_status": self.current_status,
            "automatic_experience": False,
            "automatic_l2": False,
            "automatic_l3": False,
        }

def confirmation_token(settings: MemoryRestoreSettings) -> str:
    normalized = settings.normalized()
    if normalized.mode == "system":
        return f"{SYSTEM_CONFIRMATION}:{normalized.target_root}"
    return DEVELOPER_CONFIRMATION

def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def is_known_non_memory_source(path: Path) -> bool:
    name = path.name.casefold()
    return name in KNOWN_NON_MEMORY_JSON_NAMES or any(name.endswith(suffix) for suffix in KNOWN_NON_MEMORY_JSON_SUFFIXES)

def journal_inspection_is_plausible(path: Path, inspection: dict[str, Any]) -> bool:
    if int(inspection.get("valid_entries", 0)) <= 0:
        return False
    filename_signal = any(token in path.name.casefold() for token in ("dziennik", "journal", "memory", "wspomn"))
    labels = bool(inspection.get("source_label_counts"))
    timestamps = int(inspection.get("timestamp_status_counts", {}).get("source_recorded", 0)) > 0
    classified = any(
        key != "unclassified" and int(value) > 0
        for key, value in inspection.get("profile_counts", {}).items()
    )
    return filename_signal or labels or timestamps or classified

def discover_restore_sources(directory: str | Path, *, recursive: bool = False) -> list[RestoreSource]:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    iterator: Iterable[Path] = root.rglob("*") if recursive else root.iterdir()
    found: list[RestoreSource] = []
    for path in iterator:
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if path.suffix.lower() == ".json" and is_known_non_memory_source(path):
            continue
        found.append(RestoreSource(path.resolve(), path.stat().st_size, path.suffix.lower()))
    return sorted(found, key=lambda item: (-item.size_bytes, item.path.name.casefold()))

def _repo_root_for(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists() and (candidate / "latka_jazn" / "version.py").exists():
            return candidate
    return None

def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False

def _read_json_command(command: Sequence[str], cwd: Path, timeout: float = 60.0) -> dict[str, Any]:
    completed = subprocess.run(
        list(command), cwd=str(cwd), text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]}
    payload.setdefault("returncode", completed.returncode)
    return payload

def target_preflight(settings: MemoryRestoreSettings, *, tool_root: Path | None = None) -> dict[str, Any]:
    normalized = settings.normalized()
    target = Path(normalized.target_root)
    source = Path(normalized.source_directory) if normalized.source_directory else None
    errors: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, Any] = {}
    if not normalized.target_root:
        errors.append("target_root_missing")
    if source is not None and target == source:
        errors.append("target_root_equals_source_directory")
    repo_root = tool_root or _repo_root_for(Path(__file__).resolve())
    if normalized.mode == "developer":
        if repo_root and _is_relative_to(target, repo_root):
            errors.append("developer_target_must_be_outside_repository")
    elif normalized.mode == "system":
        required = [target / "run.py", target / "PACKAGE_INTEGRITY_MANIFEST.json", target / "latka_jazn" / "version.py"]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            errors.append("system_target_missing_runtime_files")
            evidence["missing_runtime_files"] = missing
        else:
            status = _read_json_command([sys.executable, "-X", "utf8", "run.py", "status", "--snapshot", "--json"], target)
            doctor = _read_json_command([sys.executable, "-X", "utf8", "run.py", "doctor", "--json"], target)
            evidence["status"] = status
            evidence["doctor"] = doctor
            daemon = status.get("daemon", status.get("status", {}).get("daemon", {}))
            if daemon.get("pid_alive") or daemon.get("active_state") in {"active_trusted", "active_degraded"}:
                errors.append("system_runtime_must_be_stopped")
            readiness = doctor.get("readiness", {})
            if not doctor.get("installation_ok", doctor.get("ok", False)):
                errors.append("system_target_doctor_failed")
            if readiness and not readiness.get("release_metadata_current", True):
                warnings.append("system_release_metadata_not_current")
    else:
        errors.append("invalid_restore_mode")
    return {
        "ok": not errors,
        "mode": normalized.mode,
        "target_root": str(target),
        "blocking_errors": errors,
        "warnings": warnings,
        "evidence": evidence,
    }

__all__ = [
    "DEVELOPER_CONFIRMATION", "SYSTEM_CONFIRMATION", "SCHEMA_VERSION", "ProgressCallback",
    "MemoryRestorePlan", "MemoryRestoreSettings", "RestoreSource", "confirmation_token",
    "discover_restore_sources", "is_known_non_memory_source", "journal_inspection_is_plausible",
    "sha256_file", "target_preflight", "utc_stamp", "atomic_json",
]
