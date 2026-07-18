from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
import hashlib
import os
import sqlite3
import time

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("sqlite_archive_snapshot")
ProgressCallback = Callable[[int, int], None]


def _sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(slots=True, frozen=True)
class SnapshotReport:
    source: str
    destination: str
    source_size_bytes: int
    snapshot_size_bytes: int
    snapshot_sha256: str
    integrity_check: str
    foreign_key_error_count: int
    elapsed_seconds: float
    schema_version: str = SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return self.integrity_check == "ok" and self.foreign_key_error_count == 0

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["ok"] = self.ok
        return data


def create_sqlite_snapshot(
    source: str | Path,
    destination: str | Path,
    *,
    pages_per_step: int = 2048,
    sleep_seconds: float = 0.0,
    progress: ProgressCallback | None = None,
    full_integrity_check: bool = False,
) -> SnapshotReport:
    """Create a transactionally consistent SQLite snapshot using the Backup API.

    The destination is built in a sibling temporary file, validated, fsynced and
    atomically replaced. Existing destination files are never modified in place.
    Committed WAL content is included by SQLite's backup mechanism.
    """
    started = time.monotonic()
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path == destination_path:
        raise ValueError("snapshot destination must differ from source")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination_path.with_name(destination_path.name + f".tmp-{os.getpid()}")
    temp_path.unlink(missing_ok=True)

    source_uri = f"file:{source_path.as_posix()}?mode=ro"
    source_con = sqlite3.connect(source_uri, uri=True, timeout=30.0)
    target_con = sqlite3.connect(temp_path, timeout=30.0)
    try:
        source_con.execute("PRAGMA busy_timeout=30000")
        target_con.execute("PRAGMA foreign_keys=ON")

        def _progress(status: int, remaining: int, total: int) -> None:
            del status
            if progress is not None:
                progress(max(0, total - remaining), max(0, total))

        source_con.backup(
            target_con,
            pages=max(1, int(pages_per_step)),
            progress=_progress,
            sleep=max(0.0, float(sleep_seconds)),
        )
        target_con.commit()
        pragma = "integrity_check" if full_integrity_check else "quick_check"
        integrity = str(target_con.execute(f"PRAGMA {pragma}").fetchone()[0])
        foreign_key_errors = list(target_con.execute("PRAGMA foreign_key_check"))
        if integrity != "ok" or foreign_key_errors:
            raise sqlite3.DatabaseError(
                f"snapshot validation failed: {pragma}={integrity!r}, "
                f"foreign_key_errors={len(foreign_key_errors)}"
            )
    finally:
        target_con.close()
        source_con.close()

    with temp_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temp_path, destination_path)
    try:
        directory_fd = os.open(destination_path.parent, os.O_RDONLY)
    except (AttributeError, OSError):
        directory_fd = None
    if directory_fd is not None:
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    return SnapshotReport(
        source=str(source_path),
        destination=str(destination_path),
        source_size_bytes=source_path.stat().st_size,
        snapshot_size_bytes=destination_path.stat().st_size,
        snapshot_sha256=_sha256_file(destination_path),
        integrity_check=integrity,
        foreign_key_error_count=len(foreign_key_errors),
        elapsed_seconds=round(time.monotonic() - started, 6),
    )
