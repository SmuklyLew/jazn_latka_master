from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
import hashlib
import json
import os
import shutil
import sqlite3
import uuid

from latka_jazn.config import JaznConfig
from latka_jazn.memory.chat_html_importer import (
    extract_text_and_parts,
    iter_chatgpt_export_conversations,
    sha256_file,
    summarize_parts_for_sqlite,
    visible_path,
    warsaw_time,
)
from latka_jazn.memory.legacy_memory_recovery import RECOVERY_SCHEMA
from latka_jazn.memory.memory_recovery_pipeline import MemoryRecoveryPipeline
from latka_jazn.memory.memory_tier_status import inspect_memory_tier_store
from latka_jazn.memory.memory_tier_store import MemoryTierStore
from latka_jazn.memory.normalization_sidecar import MemoryNormalizationSidecar
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("html_memory_ingest")
ProgressCallback = Callable[[int, int, str], None]
TRUTH_BOUNDARY = (
    "Import HTML creates source-labelled SQLite conversation memory and a verified wake-state input. "
    "It does not automatically turn every conversation into a semantic fact, emotion, identity claim, "
    "book canon, or L3 memory. L3 still requires an exact manifest and explicit approval."
)

HTML_IMPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS html_import_sources(
  source_sha256 TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  source_name TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  source_format TEXT NOT NULL,
  first_imported_at_utc TEXT NOT NULL,
  last_imported_at_utc TEXT NOT NULL,
  last_run_id TEXT NOT NULL,
  conversations_seen INTEGER NOT NULL DEFAULT 0,
  messages_seen INTEGER NOT NULL DEFAULT 0,
  messages_written INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  truth_boundary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS html_import_runs(
  run_id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  ended_at_utc TEXT,
  source_path TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  source_size_bytes INTEGER NOT NULL,
  source_format TEXT NOT NULL,
  conversations_seen INTEGER NOT NULL DEFAULT 0,
  conversations_written INTEGER NOT NULL DEFAULT 0,
  messages_seen INTEGER NOT NULL DEFAULT 0,
  messages_written INTEGER NOT NULL DEFAULT 0,
  messages_deduplicated INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  errors_json TEXT NOT NULL DEFAULT '[]',
  truth_boundary TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_html_import_runs_source
  ON html_import_runs(source_sha256,started_at_utc);
"""


@dataclass(slots=True)
class HtmlSourceImportReport:
    path: str
    sha256: str | None
    size_bytes: int | None
    source_format: str | None
    status: str
    run_id: str | None = None
    conversations_seen: int = 0
    conversations_written: int = 0
    messages_seen: int = 0
    messages_written: int = 0
    messages_deduplicated: int = 0
    errors: list[str] | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"imported", "already_imported", "dry_run_ok"}

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok, "errors": list(self.errors or [])}


@dataclass(slots=True)
class HtmlMemoryIngestReport:
    schema_version: str
    status: str
    dry_run: bool
    root: str
    target_database: str
    backup_path: str | None
    failed_working_copy: str | None
    sources: list[dict[str, Any]]
    validation: dict[str, Any] | None
    normalization: dict[str, Any] | None
    wake_state: dict[str, Any] | None
    memory_tiers: dict[str, Any] | None
    memory_tier_backup_path: str | None
    l2: dict[str, Any] | None
    l3_manifest: dict[str, Any] | None
    errors: list[str]
    truth_boundary: str = TRUTH_BOUNDARY

    @property
    def ok(self) -> bool:
        return self.status in {
            "ready",
            "ready_with_l2",
            "ready_with_l3_manifest",
            "already_current",
            "dry_run_ok",
        }

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _utc_time(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        text = str(value).strip()
        return text or None


def _validate_sqlite(path: Path, *, full: bool = True) -> dict[str, Any]:
    if not path.is_file():
        return {
            "ok": False,
            "path": str(path),
            "integrity_check": None,
            "foreign_key_error_count": None,
            "errors": ["database_missing"],
        }
    errors: list[str] = []
    integrity: str | None = None
    foreign_key_errors: list[tuple[Any, ...]] = []
    try:
        con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            con.execute("PRAGMA query_only=ON")
            pragma = "integrity_check" if full else "quick_check"
            integrity = str(con.execute(f"PRAGMA {pragma}").fetchone()[0])
            foreign_key_errors = [tuple(row) for row in con.execute("PRAGMA foreign_key_check").fetchall()]
        finally:
            con.close()
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {
        "ok": not errors and integrity == "ok" and not foreign_key_errors,
        "path": str(path),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": sha256_file(path) if path.is_file() else None,
        "integrity_check": integrity,
        "foreign_key_error_count": len(foreign_key_errors),
        "foreign_key_errors": foreign_key_errors[:20],
        "errors": errors,
    }


def _checkpoint_database(path: Path) -> None:
    if not path.is_file():
        return
    con = sqlite3.connect(path, timeout=30.0)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


def _settle_database(path: Path) -> None:
    """Checkpoint a closed database and leave no stale WAL/SHM pair."""
    if not path.is_file():
        return
    con = sqlite3.connect(path, timeout=30.0)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("PRAGMA journal_mode=DELETE")
        con.commit()
    finally:
        con.close()
    _remove_stale_sidecars(path)


def _sqlite_backup(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".tmp-{uuid.uuid4().hex}")
    if temporary.exists():
        temporary.unlink()
    source_con = sqlite3.connect(source, timeout=30.0)
    destination_con = sqlite3.connect(temporary)
    try:
        source_con.execute("PRAGMA busy_timeout=30000")
        source_con.backup(destination_con, pages=2048, sleep=0.05)
        destination_con.commit()
    finally:
        destination_con.close()
        source_con.close()
    validation = _validate_sqlite(temporary, full=True)
    if not validation.get("ok"):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"sqlite backup validation failed: {validation}")
    os.replace(temporary, destination)
    return validation


def _remove_stale_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


def _resolve_sources(sources: Iterable[str | Path]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        path = Path(source).expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        resolved.append(path)
    return resolved


def _message_id(
    con: sqlite3.Connection,
    source_message_id: str,
    content_hash: str,
) -> tuple[str, bool]:
    existing = con.execute(
        "SELECT content_hash FROM messages WHERE message_id=?",
        (source_message_id,),
    ).fetchone()
    if existing is None or str(existing[0]) == content_hash:
        return source_message_id, existing is not None
    derived = f"{source_message_id}@{content_hash[:16]}"
    duplicate = con.execute("SELECT 1 FROM messages WHERE message_id=?", (derived,)).fetchone() is not None
    return derived, duplicate


def _merge_source_refs(raw: str | None, source_ref: dict[str, Any]) -> str:
    try:
        refs = json.loads(raw or "[]")
        if not isinstance(refs, list):
            refs = []
    except Exception:
        refs = []
    key = _canonical_json(source_ref)
    if all(_canonical_json(item) != key for item in refs if isinstance(item, dict)):
        refs.append(source_ref)
    return _canonical_json(refs)


class HtmlMemoryIngestor:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.config = JaznConfig(root=self.root)
        self.target_database = self.config.recovered_memory_db_path

    def run(
        self,
        sources: Iterable[str | Path],
        *,
        dry_run: bool = False,
        force_reimport: bool = False,
        limit_conversations: int | None = None,
        normalize_limit: int | None = None,
        prepare_l2: bool = False,
        l2_limit: int = 120,
        build_l3_manifest: bool = False,
        l3_limit: int = 25,
        backup_dir: str | Path | None = None,
        progress: ProgressCallback | None = None,
    ) -> HtmlMemoryIngestReport:
        paths = _resolve_sources(sources)
        errors: list[str] = []
        source_reports: list[HtmlSourceImportReport] = []
        backup_path: Path | None = None
        failed_working_copy: Path | None = None
        validation: dict[str, Any] | None = None
        normalization: dict[str, Any] | None = None
        wake_state: dict[str, Any] | None = None
        memory_tiers: dict[str, Any] | None = None
        memory_tier_backup_path: Path | None = None
        l2: dict[str, Any] | None = None
        l3_manifest: dict[str, Any] | None = None

        if not paths:
            return self._report(
                "invalid_input", dry_run=dry_run, sources=[], errors=["no HTML sources supplied"]
            )
        for path in paths:
            if not path.is_file():
                source_reports.append(HtmlSourceImportReport(
                    path=str(path), sha256=None, size_bytes=None, source_format=None,
                    status="missing", errors=["source file does not exist"],
                ))
                continue
            if path.suffix.lower() not in {".html", ".htm"}:
                source_reports.append(HtmlSourceImportReport(
                    path=str(path), sha256=None, size_bytes=path.stat().st_size, source_format=None,
                    status="invalid_extension", errors=["expected .html or .htm"],
                ))
        if source_reports:
            return self._report(
                "invalid_input", dry_run=dry_run,
                sources=[item.to_dict() for item in source_reports],
                errors=[error for item in source_reports for error in (item.errors or [])],
            )

        total_steps = max(3, len(paths) + 3)
        self._progress(progress, 0, total_steps, "Weryfikuję źródła HTML")
        if dry_run:
            for index, path in enumerate(paths, start=1):
                report = self._scan_source(path, limit_conversations=limit_conversations)
                source_reports.append(report)
                self._progress(progress, index, total_steps, f"Sprawdzono {path.name}")
            if all(item.ok for item in source_reports):
                return self._report(
                    "dry_run_ok", dry_run=True,
                    sources=[item.to_dict() for item in source_reports], errors=[],
                )
            return self._report(
                "dry_run_failed", dry_run=True,
                sources=[item.to_dict() for item in source_reports],
                errors=[error for item in source_reports for error in (item.errors or [])],
            )

        target = self.target_database
        target.parent.mkdir(parents=True, exist_ok=True)
        backup_root = (
            Path(backup_dir).expanduser().resolve()
            if backup_dir is not None
            else self.root / "memory" / "backups" / "html_import"
        )
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        working = target.with_name(target.name + f".html-import-{uuid.uuid4().hex}.tmp")

        try:
            if target.is_file():
                self._progress(progress, 1, total_steps, "Tworzę zweryfikowany backup SQLite")
                _checkpoint_database(target)
                backup_path = backup_root / f"{target.stem}-before-html-import-{stamp}.sqlite3"
                _sqlite_backup(target, backup_path)
                shutil.copy2(backup_path, working)
            else:
                sqlite3.connect(working).close()

            con = sqlite3.connect(working, timeout=30.0)
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA busy_timeout=30000")
                con.execute("PRAGMA foreign_keys=ON")
                con.execute("PRAGMA journal_mode=WAL")
                con.executescript(RECOVERY_SCHEMA)
                con.executescript(HTML_IMPORT_SCHEMA)
                con.execute(
                    "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                    ("html_memory_ingest_schema", SCHEMA_VERSION),
                )
                con.execute(
                    "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                    ("html_memory_ingest_truth_boundary", TRUTH_BOUNDARY),
                )
                con.commit()
                for index, path in enumerate(paths, start=1):
                    report = self._import_source(
                        con,
                        path,
                        force_reimport=force_reimport,
                        limit_conversations=limit_conversations,
                    )
                    source_reports.append(report)
                    self._progress(
                        progress,
                        min(total_steps - 2, index + 1),
                        total_steps,
                        f"Zaimportowano {path.name}: {report.messages_written} wiadomości",
                    )
                    if not report.ok:
                        raise RuntimeError(f"HTML import failed for {path}: {report.errors}")
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.execute("PRAGMA journal_mode=DELETE")
                con.commit()
            finally:
                con.close()

            _settle_database(working)
            validation = _validate_sqlite(working, full=True)
            if not validation.get("ok"):
                raise RuntimeError(f"working SQLite validation failed: {validation}")
            _remove_stale_sidecars(working)

            self._progress(progress, total_steps - 2, total_steps, "Aktywuję zweryfikowaną bazę pamięci")
            if target.exists():
                _remove_stale_sidecars(target)
            os.replace(working, target)
            validation = _validate_sqlite(target, full=True)
            if not validation.get("ok"):
                raise RuntimeError(f"activated SQLite validation failed: {validation}")
            _remove_stale_sidecars(target)

            sidecar = MemoryNormalizationSidecar(
                self.root,
                source_db_path=target,
                sidecar_db_path=self.config.normalization_sidecar_db_path,
                runtime_version=self.config.version,
            )
            normalized = sidecar.normalize(limit=normalize_limit)
            normalization = normalized.to_dict()
            if normalized.status != "ok":
                errors.extend(normalized.errors)
                return self._report(
                    "normalization_failed", dry_run=False, backup_path=backup_path,
                    sources=[item.to_dict() for item in source_reports], validation=validation,
                    normalization=normalization, errors=errors,
                )
            wake = sidecar.build_wake_state()
            wake_state = wake.to_dict()
            if wake.status != "ready":
                errors.extend(wake.errors)
                return self._report(
                    "wake_state_failed", dry_run=False, backup_path=backup_path,
                    sources=[item.to_dict() for item in source_reports], validation=validation,
                    normalization=normalization, wake_state=wake_state, errors=errors,
                )

            tier_database = self.config.memory_tier_db_path
            if tier_database.is_file():
                _checkpoint_database(tier_database)
                memory_tier_backup_path = (
                    backup_root / f"{tier_database.stem}-before-html-import-{stamp}.sqlite3"
                )
                _sqlite_backup(tier_database, memory_tier_backup_path)
            with MemoryTierStore(tier_database) as tier_store:
                tier_store.validate(full=True)
            _settle_database(tier_database)
            memory_tiers = inspect_memory_tier_store(tier_database, full=True).to_dict()
            if not memory_tiers.get("ready"):
                return self._report(
                    "memory_tier_failed", dry_run=False, backup_path=backup_path,
                    memory_tier_backup_path=memory_tier_backup_path,
                    sources=[item.to_dict() for item in source_reports], validation=validation,
                    normalization=normalization, wake_state=wake_state,
                    memory_tiers=memory_tiers, errors=[str(memory_tiers.get("error") or "memory tier validation failed")],
                )

            pipeline = MemoryRecoveryPipeline(self.root)
            if prepare_l2:
                l2 = pipeline.prepare_l2(limit=max(1, int(l2_limit)))
                _settle_database(tier_database)
                memory_tiers = inspect_memory_tier_store(tier_database, full=True).to_dict()
            if build_l3_manifest:
                l3_manifest = pipeline.build_l3_manifest(limit=max(0, int(l3_limit)))

            status = "ready"
            if l2 and int(l2.get("written") or 0) > 0:
                status = "ready_with_l2"
            if l3_manifest:
                status = "ready_with_l3_manifest"
            self._progress(progress, total_steps, total_steps, "Import i synchronizacja pamięci zakończone")
            return self._report(
                status,
                dry_run=False,
                backup_path=backup_path,
                sources=[item.to_dict() for item in source_reports],
                validation=validation,
                normalization=normalization,
                wake_state=wake_state,
                memory_tiers=memory_tiers,
                memory_tier_backup_path=memory_tier_backup_path,
                l2=l2,
                l3_manifest=l3_manifest,
                errors=errors,
            )
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if working.exists():
                failed_working_copy = backup_root / f"failed-html-import-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
                try:
                    _remove_stale_sidecars(working)
                    os.replace(working, failed_working_copy)
                except Exception as move_exc:
                    errors.append(f"failed_working_copy:{type(move_exc).__name__}: {move_exc}")
                    failed_working_copy = None
            return self._report(
                "import_failed",
                dry_run=False,
                backup_path=backup_path,
                failed_working_copy=failed_working_copy,
                sources=[item.to_dict() for item in source_reports],
                validation=validation,
                normalization=normalization,
                wake_state=wake_state,
                memory_tiers=memory_tiers,
                memory_tier_backup_path=memory_tier_backup_path,
                l2=l2,
                l3_manifest=l3_manifest,
                errors=errors,
            )

    def _scan_source(self, path: Path, *, limit_conversations: int | None) -> HtmlSourceImportReport:
        report = HtmlSourceImportReport(
            path=str(path),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            source_format=None,
            status="dry_run_ok",
            errors=[],
        )
        try:
            for conversation in iter_chatgpt_export_conversations(path):
                report.conversations_seen += 1
                if limit_conversations is not None and report.conversations_seen > limit_conversations:
                    report.conversations_seen -= 1
                    break
                source_format = str(conversation.get("source_format") or "chatgpt_export_json_html")
                report.source_format = report.source_format or source_format
                mapping = conversation.get("mapping") or {}
                for node in mapping.values():
                    if isinstance((node or {}).get("message"), dict):
                        report.messages_seen += 1
            if report.conversations_seen == 0:
                report.status = "invalid_html"
                report.errors = ["no conversations found in HTML"]
            report.source_format = report.source_format or "unknown_html"
        except Exception as exc:
            report.status = "parse_error"
            report.errors = [f"{type(exc).__name__}: {exc}"]
        return report

    def _import_source(
        self,
        con: sqlite3.Connection,
        path: Path,
        *,
        force_reimport: bool,
        limit_conversations: int | None,
    ) -> HtmlSourceImportReport:
        source_sha = sha256_file(path)
        source_size = path.stat().st_size
        existing = con.execute(
            "SELECT status,source_format FROM html_import_sources WHERE source_sha256=?",
            (source_sha,),
        ).fetchone()
        if existing is not None and str(existing["status"]) == "imported" and not force_reimport:
            return HtmlSourceImportReport(
                path=str(path), sha256=source_sha, size_bytes=source_size,
                source_format=str(existing["source_format"]), status="already_imported", errors=[],
            )

        run_id = str(uuid.uuid4())
        started = _now()
        report = HtmlSourceImportReport(
            path=str(path), sha256=source_sha, size_bytes=source_size,
            source_format=None, status="importing", run_id=run_id, errors=[],
        )
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                """INSERT INTO html_import_runs(
                   run_id,schema_version,started_at_utc,source_path,source_sha256,source_size_bytes,
                   source_format,status,truth_boundary)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (run_id, SCHEMA_VERSION, started, str(path), source_sha, source_size,
                 "detecting", "running", TRUTH_BOUNDARY),
            )
            for conversation in iter_chatgpt_export_conversations(path):
                report.conversations_seen += 1
                if limit_conversations is not None and report.conversations_seen > limit_conversations:
                    report.conversations_seen -= 1
                    break
                source_format = str(conversation.get("source_format") or "chatgpt_export_json_html")
                report.source_format = report.source_format or source_format
                conversation_id = str(
                    conversation.get("conversation_id")
                    or conversation.get("id")
                    or f"html-{source_sha[:16]}-{report.conversations_seen:08d}"
                )
                title = str(conversation.get("title") or "(bez tytułu)")
                create_time = conversation.get("create_time")
                update_time = conversation.get("update_time")
                mapping = conversation.get("mapping") or {}
                path_nodes = visible_path(mapping, conversation.get("current_node"))
                visible_indexes = {node_id: index for index, node_id in enumerate(path_nodes)}
                conversation_payload = {
                    "conversation_id": conversation_id,
                    "title": title,
                    "create_time": create_time,
                    "update_time": update_time,
                    "current_node": conversation.get("current_node"),
                    "mapping_node_count": len(mapping),
                    "source_path": str(path),
                    "source_sha256": source_sha,
                    "source_format": source_format,
                    "import_run_id": run_id,
                }
                con.execute(
                    """INSERT INTO legacy_conversations(
                       conversation_id,title,create_time,create_time_warsaw,
                       update_time,update_time_warsaw,payload_json)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(conversation_id) DO UPDATE SET
                         title=excluded.title,
                         create_time=COALESCE(legacy_conversations.create_time,excluded.create_time),
                         create_time_warsaw=COALESCE(legacy_conversations.create_time_warsaw,excluded.create_time_warsaw),
                         update_time=COALESCE(excluded.update_time,legacy_conversations.update_time),
                         update_time_warsaw=COALESCE(excluded.update_time_warsaw,legacy_conversations.update_time_warsaw),
                         payload_json=excluded.payload_json""",
                    (
                        conversation_id, title, create_time, warsaw_time(create_time),
                        update_time, warsaw_time(update_time), _canonical_json(conversation_payload),
                    ),
                )
                report.conversations_written += 1

                for node_id, node in mapping.items():
                    message = (node or {}).get("message")
                    if not isinstance(message, dict):
                        continue
                    report.messages_seen += 1
                    source_message_id = str(message.get("id") or node_id)
                    text, parts, assets, total_chars = extract_text_and_parts(
                        message,
                        text_char_limit=None,
                    )
                    if not text and not assets:
                        continue
                    role = str((message.get("author") or {}).get("role") or "unknown")
                    created = message.get("create_time")
                    content_hash = _sha256_text(text)
                    canonical_message_id, duplicate = _message_id(
                        con,
                        source_message_id,
                        content_hash,
                    )
                    source_ref = {
                        "source_kind": source_format,
                        "source_path": str(path),
                        "source_sha256": source_sha,
                        "conversation_id": conversation_id,
                        "source_message_id": source_message_id,
                        "node_id": str(node_id),
                        "visible_index": visible_indexes.get(str(node_id)),
                        "import_run_id": run_id,
                    }
                    current = con.execute(
                        "SELECT source_refs_json FROM messages WHERE message_id=?",
                        (canonical_message_id,),
                    ).fetchone()
                    source_refs_json = _merge_source_refs(
                        str(current["source_refs_json"]) if current else None,
                        source_ref,
                    )
                    con.execute(
                        """INSERT INTO messages(
                           message_id,conversation_id,conversation_title,role,timestamp,content_text,
                           content_hash,first_source_file,first_source_sha256,source_refs_json,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(message_id) DO UPDATE SET
                             conversation_id=excluded.conversation_id,
                             conversation_title=excluded.conversation_title,
                             role=excluded.role,
                             timestamp=COALESCE(messages.timestamp,excluded.timestamp),
                             content_text=excluded.content_text,
                             content_hash=excluded.content_hash,
                             source_refs_json=excluded.source_refs_json,
                             updated_at=excluded.updated_at""",
                        (
                            canonical_message_id, conversation_id, title, role, _utc_time(created), text,
                            content_hash, str(path), source_sha, source_refs_json,
                            _utc_time(created) or started, _now(),
                        ),
                    )
                    source_key = _sha256_text(_canonical_json(source_ref))
                    con.execute(
                        """INSERT OR REPLACE INTO message_sources(
                           message_id,source_key,source_file,source_sha256,source_ref_json)
                           VALUES(?,?,?,?,?)""",
                        (canonical_message_id, source_key, str(path), source_sha, _canonical_json(source_ref)),
                    )
                    con.execute(
                        """INSERT INTO legacy_messages(
                           conversation_id,conversation_title,message_id,author_role,create_time,create_time_warsaw,
                           text,parts_json,assets_json,is_visible_path,visible_index,text_sha256,char_count)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(conversation_id,message_id) DO UPDATE SET
                             conversation_title=excluded.conversation_title,
                             author_role=excluded.author_role,
                             create_time=COALESCE(legacy_messages.create_time,excluded.create_time),
                             create_time_warsaw=COALESCE(legacy_messages.create_time_warsaw,excluded.create_time_warsaw),
                             text=excluded.text,
                             parts_json=excluded.parts_json,
                             assets_json=excluded.assets_json,
                             is_visible_path=excluded.is_visible_path,
                             visible_index=excluded.visible_index,
                             text_sha256=excluded.text_sha256,
                             char_count=excluded.char_count""",
                        (
                            conversation_id, title, canonical_message_id, role, created, warsaw_time(created),
                            text, _canonical_json(summarize_parts_for_sqlite(parts)), _canonical_json(assets),
                            1 if str(node_id) in visible_indexes else 0,
                            visible_indexes.get(str(node_id)), content_hash, total_chars,
                        ),
                    )
                    record_hash = _sha256_text(_canonical_json({
                        "message_id": canonical_message_id,
                        "content_hash": content_hash,
                        "source_ref": source_ref,
                    }))
                    con.execute(
                        """INSERT OR IGNORE INTO recovery_provenance(
                           target_table,target_id,source_kind,source_path,source_sha256,
                           source_line,source_record_sha256,source_ref_json)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            "messages", canonical_message_id, source_format, str(path), source_sha,
                            None, record_hash, _canonical_json(source_ref),
                        ),
                    )
                    report.messages_written += 1
                    if duplicate:
                        report.messages_deduplicated += 1

            if report.conversations_seen == 0:
                raise ValueError("no conversations found in HTML")
            source_format = report.source_format or "unknown_html"
            con.execute(
                """INSERT OR REPLACE INTO source_files(
                   sha256,path,size_bytes,kind,original_path,imported_at_utc)
                   VALUES(?,?,?,?,?,?)""",
                (source_sha, str(path), source_size, source_format, str(path), _now()),
            )
            con.execute(
                """INSERT INTO html_import_sources(
                   source_sha256,source_path,source_name,size_bytes,source_format,
                   first_imported_at_utc,last_imported_at_utc,last_run_id,
                   conversations_seen,messages_seen,messages_written,status,truth_boundary)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_sha256) DO UPDATE SET
                     source_path=excluded.source_path,
                     source_name=excluded.source_name,
                     size_bytes=excluded.size_bytes,
                     source_format=excluded.source_format,
                     last_imported_at_utc=excluded.last_imported_at_utc,
                     last_run_id=excluded.last_run_id,
                     conversations_seen=excluded.conversations_seen,
                     messages_seen=excluded.messages_seen,
                     messages_written=excluded.messages_written,
                     status=excluded.status,
                     truth_boundary=excluded.truth_boundary""",
                (
                    source_sha, str(path), path.name, source_size, source_format,
                    started, _now(), run_id, report.conversations_seen, report.messages_seen,
                    report.messages_written, "imported", TRUTH_BOUNDARY,
                ),
            )
            con.execute(
                """UPDATE html_import_runs SET
                   ended_at_utc=?,source_format=?,conversations_seen=?,conversations_written=?,
                   messages_seen=?,messages_written=?,messages_deduplicated=?,status=?,errors_json=?
                   WHERE run_id=?""",
                (
                    _now(), source_format, report.conversations_seen, report.conversations_written,
                    report.messages_seen, report.messages_written, report.messages_deduplicated,
                    "imported", "[]", run_id,
                ),
            )
            con.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                ("last_html_memory_import", _canonical_json({
                    "run_id": run_id,
                    "source_path": str(path),
                    "source_sha256": source_sha,
                    "source_format": source_format,
                    "imported_at_utc": _now(),
                })),
            )
            con.commit()
            report.source_format = source_format
            report.status = "imported"
            return report
        except Exception as exc:
            con.rollback()
            report.status = "import_failed"
            report.errors = [f"{type(exc).__name__}: {exc}"]
            return report

    def _report(
        self,
        status: str,
        *,
        dry_run: bool,
        backup_path: Path | None = None,
        failed_working_copy: Path | None = None,
        sources: list[dict[str, Any]],
        validation: dict[str, Any] | None = None,
        normalization: dict[str, Any] | None = None,
        wake_state: dict[str, Any] | None = None,
        memory_tiers: dict[str, Any] | None = None,
        memory_tier_backup_path: Path | None = None,
        l2: dict[str, Any] | None = None,
        l3_manifest: dict[str, Any] | None = None,
        errors: list[str],
    ) -> HtmlMemoryIngestReport:
        return HtmlMemoryIngestReport(
            schema_version=SCHEMA_VERSION,
            status=status,
            dry_run=dry_run,
            root=str(self.root),
            target_database=str(self.target_database),
            backup_path=str(backup_path) if backup_path else None,
            failed_working_copy=str(failed_working_copy) if failed_working_copy else None,
            sources=sources,
            validation=validation,
            normalization=normalization,
            wake_state=wake_state,
            memory_tiers=memory_tiers,
            memory_tier_backup_path=str(memory_tier_backup_path) if memory_tier_backup_path else None,
            l2=l2,
            l3_manifest=l3_manifest,
            errors=list(errors),
        )

    @staticmethod
    def _progress(
        progress: ProgressCallback | None,
        completed: int,
        total: int,
        label: str,
    ) -> None:
        if progress is not None:
            progress(completed, total, label)
