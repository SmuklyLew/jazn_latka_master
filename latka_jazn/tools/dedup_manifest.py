from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json

EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".git", ".mypy_cache", ".ruff_cache", "workspace_runtime", "exports"}
DEFAULT_SKIP = {"memory/raw/chat.html"}
TRANSIENT_SUFFIXES = ("-wal", "-shm", ".sqlite3-wal", ".sqlite3-shm", ".db-wal", ".db-shm", ".pyc", ".pyo")

@dataclass(slots=True)
class DuplicateFileRecord:
    path: str
    size: int
    sha256: str

@dataclass(slots=True)
class DuplicateGroup:
    sha256: str
    size: int
    count: int
    canonical_path: str
    duplicate_paths: list[str]

@dataclass(slots=True)
class DedupReport:
    created_at_utc: str
    root: str
    file_count: int
    duplicate_group_count: int
    duplicate_file_count: int
    duplicate_bytes: int
    groups: list[DuplicateGroup]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["groups"] = [asdict(g) for g in self.groups]
        return data

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def should_skip(path: Path, rel: str) -> bool:
    if any(part in EXCLUDE_PARTS for part in path.parts):
        return True
    if rel.startswith("docs/update_history/"):
        return True
    if rel in DEFAULT_SKIP:
        return True
    if rel.endswith(TRANSIENT_SUFFIXES):
        return True
    return False

def canonical_score(rel: str) -> tuple[int, str]:
    # niższy wynik = lepszy kanoniczny plik
    score = 50
    if rel.startswith("memory/raw/"):
        score -= 30
    if rel.startswith("latka_jazn/") or rel in {"main.py", "VERSION.txt", "README.md"}:
        score -= 25
    if rel.startswith("docs/"):
        score -= 15
    if rel.startswith("reports/"):
        score += 10
    if rel.startswith("memory/versioned_sources/"):
        score += 20
    if "MANIFEST" in rel.upper():
        score -= 5
    if rel.endswith(".diff") and not rel.startswith("reports/"):
        score -= 5
    return (score, rel)

def build_dedup_report(root: Path) -> DedupReport:
    root = Path(root).resolve()
    by_hash: dict[str, list[DuplicateFileRecord]] = defaultdict(list)
    file_count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if should_skip(path, rel):
            continue
        digest = sha256_file(path)
        by_hash[digest].append(DuplicateFileRecord(rel, path.stat().st_size, digest))
        file_count += 1
    groups: list[DuplicateGroup] = []
    duplicate_bytes = 0
    for digest, records in by_hash.items():
        if len(records) <= 1:
            continue
        records = sorted(records, key=lambda r: canonical_score(r.path))
        canonical = records[0]
        dups = records[1:]
        duplicate_bytes += sum(r.size for r in dups)
        groups.append(DuplicateGroup(
            sha256=digest,
            size=canonical.size,
            count=len(records),
            canonical_path=canonical.path,
            duplicate_paths=[r.path for r in dups],
        ))
    groups.sort(key=lambda g: (-g.size * (g.count - 1), g.canonical_path))
    return DedupReport(
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        root=str(root),
        file_count=file_count,
        duplicate_group_count=len(groups),
        duplicate_file_count=sum(len(g.duplicate_paths) for g in groups),
        duplicate_bytes=duplicate_bytes,
        groups=groups,
    )

def write_dedup_report(root: Path, output: Path | None = None) -> Path:
    root = Path(root).resolve()
    report = build_dedup_report(root)
    if output is None:
        output = root / "reports" / "DEDUP_REPORT.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return output
