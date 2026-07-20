from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
import hashlib
import json
import os
import shutil
import sqlite3
import uuid

from latka_jazn.config import JaznConfig
from latka_jazn.memory.store import SCHEMA as LEGACY_MEMORY_SCHEMA
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("legacy_memory_recovery")
ProgressCallback = Callable[[int, int, str], None]
TRUTH_BOUNDARY = (
    "Recovery builds a new read/write database from independently verifiable L0 and JSON/JSONL sources. "
    "It never repairs or overwrites the corrupt legacy SQLite in place, and recovered rows remain source-labelled."
)

RECOVERY_SCHEMA = LEGACY_MEMORY_SCHEMA + """
CREATE TABLE IF NOT EXISTS messages(
  message_id TEXT PRIMARY KEY,
  conversation_id TEXT,
  conversation_title TEXT,
  role TEXT NOT NULL,
  timestamp TEXT,
  content_text TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  first_source_file TEXT,
  first_source_sha256 TEXT,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id,timestamp,message_id);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role,timestamp);
CREATE VIEW IF NOT EXISTS messages_user_assistant AS
  SELECT * FROM messages WHERE role IN ('user','assistant');
CREATE VIEW IF NOT EXISTS active_conversation_messages AS SELECT * FROM messages;
CREATE TABLE IF NOT EXISTS message_sources(
  message_id TEXT NOT NULL,
  source_key TEXT NOT NULL,
  source_file TEXT,
  source_sha256 TEXT,
  source_ref_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(message_id,source_key),
  FOREIGN KEY(message_id) REFERENCES messages(message_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS recovery_runs(
  run_id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  completed_at_utc TEXT,
  source_root TEXT NOT NULL,
  source_fingerprint TEXT NOT NULL,
  source_inventory_json TEXT NOT NULL,
  output_counts_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  errors_json TEXT NOT NULL DEFAULT '[]',
  truth_boundary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recovery_provenance(
  target_table TEXT NOT NULL,
  target_id TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  source_line INTEGER,
  source_record_sha256 TEXT NOT NULL,
  source_ref_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(target_table,target_id,source_kind,source_sha256,source_record_sha256)
);
CREATE INDEX IF NOT EXISTS idx_recovery_provenance_source
  ON recovery_provenance(source_kind,source_sha256,source_line);
"""

LAYERED_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("episodic_memories", "layered/episodic.jsonl", "exported_from_sqlite/episodic_from_sqlite.jsonl"),
    ("semantic_facts", "layered/semantic.jsonl", "exported_from_sqlite/semantic_from_sqlite.jsonl"),
    ("procedural_rules", "layered/procedural.jsonl", "exported_from_sqlite/procedural_from_sqlite.jsonl"),
    ("reflection_entries", "layered/reflections.jsonl", "exported_from_sqlite/reflections_from_sqlite.jsonl"),
    ("truth_audits", "layered/truth_audits.jsonl", "exported_from_sqlite/truth_audits_from_sqlite.jsonl"),
)


@dataclass(slots=True)
class RecoveryInspection:
    schema_version: str
    root: str
    output_path: str
    sources: list[dict[str, Any]]
    legacy_database: dict[str, Any]
    required_sources_present: bool
    recoverable: bool
    errors: list[str]
    truth_boundary: str = TRUTH_BOUNDARY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LegacyRecoveryReport:
    schema_version: str
    status: str
    dry_run: bool
    run_id: str | None
    root: str
    output_path: str
    manifest_path: str
    source_fingerprint: str | None
    source_inventory: list[dict[str, Any]]
    output_counts: dict[str, int]
    integrity_check: str | None
    foreign_key_error_count: int | None
    backup_path: str | None
    errors: list[str]
    truth_boundary: str = TRUTH_BOUNDARY

    @property
    def ok(self) -> bool:
        return self.status in {"ready", "already_current", "dry_run_ok"}

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def _sqlite_health(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "path": str(path), "integrity_check": None, "foreign_key_error_count": None}
    try:
        with _sqlite_readonly(path) as con:
            integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = len(con.execute("PRAGMA foreign_key_check").fetchall())
        return {
            "exists": True,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
            "integrity_check": integrity,
            "foreign_key_error_count": foreign_keys,
            "ok": integrity == "ok" and foreign_keys == 0,
        }
    except Exception as exc:
        return {
            "exists": True,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
            "integrity_check": None,
            "foreign_key_error_count": None,
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _timestamp_value(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).isoformat()
    except Exception:
        return str(value)


def _timestamp_float(value: Any) -> float | None:
    text = _timestamp_value(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _journal_text(entry: dict[str, Any]) -> str:
    fields = (
        "tytuł", "tytul", "treść", "tresc", "opis", "wpis", "content",
        "doświadczenie_latki", "doswiadczenie_latki", "wspomnienie",
        "wspomnienia_do_zachowania", "refleksja", "emocje_latki", "granica_prawdy",
    )
    lines: list[str] = []
    for key in fields:
        value = entry.get(key)
        if value in (None, "", [], {}):
            continue
        rendered = value if isinstance(value, str) else _canonical_json(value)
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines) or _canonical_json(entry)


def _source_record_hash(record: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(record))


class LegacyMemoryRecovery:
    """Atomically rebuild a healthy legacy-compatible source database.

    The corrupt runtime_write_v1 database is inspected only. Recovery input is
    the conversation L0 archive plus journal/layered JSON sources. The result is
    a new database that can be read by the existing engine and normalization
    sidecar without claiming that all recovered rows are trusted memories.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.memory_root = self.root / "memory"
        cfg = JaznConfig(root=self.root)
        self.output_path = Path(output_path).expanduser().resolve() if output_path else cfg.recovered_memory_db_path
        self.manifest_path = self.output_path.with_suffix(self.output_path.suffix + ".recovery.json")
        self.archive_manifest = self.memory_root / "sqlite" / "conversation_archive_v1" / "conversation_archive_manifest.sqlite3"
        self.legacy_database = self.memory_root / "sqlite" / "runtime_write_v1" / "runtime_memory.sqlite3"
        self.journal_path = self.memory_root / "raw" / "dziennik.json"

    def inspect(self) -> RecoveryInspection:
        errors: list[str] = []
        inventory = self._source_inventory(include_hash=True)
        archive_present = self.archive_manifest.is_file()
        structured_present = any(item["kind"] in {"journal", "layered_jsonl"} for item in inventory)
        if not archive_present:
            errors.append("conversation_archive_manifest_missing")
        if not structured_present:
            errors.append("journal_and_layered_sources_missing")
        return RecoveryInspection(
            schema_version=SCHEMA_VERSION,
            root=str(self.root),
            output_path=str(self.output_path),
            sources=inventory,
            legacy_database=_sqlite_health(self.legacy_database),
            required_sources_present=archive_present and structured_present,
            recoverable=archive_present and structured_present,
            errors=errors,
        )

    def rebuild(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
        progress: ProgressCallback | None = None,
    ) -> LegacyRecoveryReport:
        inspection = self.inspect()
        inventory = inspection.sources
        fingerprint = _sha256_text(_canonical_json([
            {key: item.get(key) for key in ("kind", "path", "size_bytes", "sha256")}
            for item in inventory
        ]))
        empty = LegacyRecoveryReport(
            schema_version=SCHEMA_VERSION,
            status="validation_failed",
            dry_run=dry_run,
            run_id=None,
            root=str(self.root),
            output_path=str(self.output_path),
            manifest_path=str(self.manifest_path),
            source_fingerprint=fingerprint,
            source_inventory=inventory,
            output_counts={},
            integrity_check=None,
            foreign_key_error_count=None,
            backup_path=None,
            errors=list(inspection.errors),
        )
        if not inspection.recoverable:
            return empty
        if dry_run:
            empty.status = "dry_run_ok"
            empty.errors = []
            return empty

        current = self._existing_current(fingerprint)
        if current and not force:
            return LegacyRecoveryReport(
                **{
                    **asdict(empty),
                    "status": "already_current",
                    "run_id": current.get("run_id"),
                    "output_counts": dict(current.get("output_counts") or {}),
                    "integrity_check": "ok",
                    "foreign_key_error_count": 0,
                    "errors": [],
                }
            )

        run_id = _sha256_text(f"{fingerprint}|{SCHEMA_VERSION}")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.output_path.with_name(f".{self.output_path.name}.{uuid.uuid4().hex}.tmp")
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(temp_path) + suffix)
            if candidate.exists():
                candidate.unlink()
        backup_path: Path | None = None
        counts: dict[str, int] = {}
        errors: list[str] = []
        total_steps = 8
        self._progress(progress, 0, total_steps, "Weryfikuję źródła recovery")
        started = _now()
        try:
            with closing(sqlite3.connect(temp_path)) as con:
                con.row_factory = sqlite3.Row
                con.execute("PRAGMA foreign_keys=ON")
                con.execute("PRAGMA synchronous=FULL")
                con.execute("PRAGMA temp_store=FILE")
                con.executescript(RECOVERY_SCHEMA)
                con.execute(
                    "INSERT INTO recovery_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id, SCHEMA_VERSION, started, None, str(self.root), fingerprint,
                        _canonical_json(inventory), "{}", "running", "[]", TRUTH_BOUNDARY,
                    ),
                )
                con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (SCHEMA_VERSION,))
                con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('recovery_source_fingerprint',?)", (fingerprint,))
                con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('recovery_run_id',?)", (run_id,))
                con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('truth_boundary',?)", (TRUTH_BOUNDARY,))
                con.commit()

                self._progress(progress, 1, total_steps, "Odtwarzam rozmowy L0")
                counts.update(self._import_archive(con))
                con.commit()

                self._progress(progress, 3, total_steps, "Odtwarzam dziennik")
                counts.update(self._import_journal(con))
                con.commit()

                self._progress(progress, 4, total_steps, "Odtwarzam warstwy JSONL")
                layered_counts = self._import_layered(con)
                for key, value in layered_counts.items():
                    counts[key] = counts.get(key, 0) + value
                con.commit()

                self._progress(progress, 6, total_steps, "Waliduję nową bazę")
                integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
                fk_count = len(con.execute("PRAGMA foreign_key_check").fetchall())
                if integrity != "ok" or fk_count:
                    raise sqlite3.DatabaseError(
                        f"recovered database validation failed: integrity={integrity!r}, foreign_keys={fk_count}"
                    )
                counts.update(self._table_counts(con))
                con.execute(
                    "UPDATE recovery_runs SET completed_at_utc=?,output_counts_json=?,status='ready',errors_json='[]' WHERE run_id=?",
                    (_now(), _canonical_json(counts), run_id),
                )
                con.commit()
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            self._progress(progress, 7, total_steps, "Publikuję recovery atomowo")
            if self.output_path.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_path = self.output_path.with_name(f"{self.output_path.name}.bak-{stamp}")
                shutil.copy2(self.output_path, backup_path)
            os.replace(temp_path, self.output_path)
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "created_at_utc": _now(),
                "source_root": str(self.root),
                "source_fingerprint": fingerprint,
                "source_inventory": inventory,
                "output_path": str(self.output_path),
                "output_sha256": _sha256_file(self.output_path),
                "output_size_bytes": self.output_path.stat().st_size,
                "output_counts": counts,
                "integrity_check": "ok",
                "foreign_key_error_count": 0,
                "legacy_database_inspection": inspection.legacy_database,
                "truth_boundary": TRUTH_BOUNDARY,
            }
            temp_manifest = self.manifest_path.with_name(f".{self.manifest_path.name}.{uuid.uuid4().hex}.tmp")
            temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temp_manifest, self.manifest_path)
            self._progress(progress, total_steps, total_steps, "Recovery gotowe")
            return LegacyRecoveryReport(
                schema_version=SCHEMA_VERSION,
                status="ready",
                dry_run=False,
                run_id=run_id,
                root=str(self.root),
                output_path=str(self.output_path),
                manifest_path=str(self.manifest_path),
                source_fingerprint=fingerprint,
                source_inventory=inventory,
                output_counts=counts,
                integrity_check="ok",
                foreign_key_error_count=0,
                backup_path=str(backup_path) if backup_path else None,
                errors=[],
            )
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(temp_path) + suffix)
                if candidate.exists():
                    candidate.unlink()
            empty.run_id = run_id
            empty.status = "recovery_failed"
            empty.errors = errors
            empty.backup_path = str(backup_path) if backup_path else None
            return empty

    def _existing_current(self, fingerprint: str) -> dict[str, Any] | None:
        if not self.output_path.is_file() or not self.manifest_path.is_file():
            return None
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if manifest.get("source_fingerprint") != fingerprint:
                return None
            if manifest.get("output_sha256") != _sha256_file(self.output_path):
                return None
            health = _sqlite_health(self.output_path)
            if not health.get("ok"):
                return None
            return manifest
        except Exception:
            return None

    def _source_inventory(self, *, include_hash: bool) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        paths: list[tuple[str, Path]] = [
            ("conversation_archive_manifest", self.archive_manifest),
            ("journal", self.journal_path),
        ]
        for _, primary, fallback in LAYERED_SOURCES:
            primary_path = self.memory_root / primary
            fallback_path = self.memory_root / fallback
            selected = primary_path if primary_path.is_file() else fallback_path
            paths.append(("layered_jsonl", selected))
        seen: set[Path] = set()
        for kind, path in paths:
            path = path.resolve()
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            item: dict[str, Any] = {
                "kind": kind,
                "path": str(path),
                "relative_path": str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path),
                "size_bytes": path.stat().st_size,
            }
            if include_hash:
                item["sha256"] = _sha256_file(path)
            entries.append(item)
        if self.archive_manifest.is_file():
            try:
                with _sqlite_readonly(self.archive_manifest) as con:
                    rows = con.execute(
                        "SELECT family,relative_path,size_bytes,sha256,integrity_check,foreign_key_error_count "
                        "FROM shard_files ORDER BY family,ordinal"
                    ).fetchall()
                for row in rows:
                    shard = self._resolve_shard_path(str(row["relative_path"]))
                    if not shard.is_file() or shard in seen:
                        continue
                    seen.add(shard)
                    item = {
                        "kind": f"conversation_{row['family']}_shard",
                        "path": str(shard),
                        "relative_path": str(shard.relative_to(self.root)) if shard.is_relative_to(self.root) else str(shard),
                        "size_bytes": shard.stat().st_size,
                        "manifest_sha256": row["sha256"],
                        "manifest_integrity_check": row["integrity_check"],
                        "manifest_foreign_key_error_count": row["foreign_key_error_count"],
                    }
                    if include_hash:
                        item["sha256"] = _sha256_file(shard)
                    entries.append(item)
            except Exception as exc:
                entries.append({"kind": "archive_inventory_error", "path": str(self.archive_manifest), "error": repr(exc)})
        entries.sort(key=lambda item: (str(item.get("kind")), str(item.get("relative_path"))))
        return entries

    def _resolve_shard_path(self, relative_path: str) -> Path:
        rel = Path(relative_path)
        candidates = (
            self.memory_root / "sqlite" / rel,
            self.archive_manifest.parent / rel,
            self.root / rel,
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return candidates[0].resolve()

    def _import_archive(self, target: sqlite3.Connection) -> dict[str, int]:
        counts = {"archive_conversations": 0, "archive_messages": 0, "archive_message_sources": 0}
        with _sqlite_readonly(self.archive_manifest) as manifest:
            source_rows = manifest.execute("SELECT * FROM archive_sources").fetchall()
            sources = {str(row["source_uid"]): dict(row) for row in source_rows}
            shards = manifest.execute(
                "SELECT relative_path FROM shard_files WHERE family='archive' ORDER BY ordinal"
            ).fetchall()
        for shard_row in shards:
            shard_path = self._resolve_shard_path(str(shard_row["relative_path"]))
            shard_sha = _sha256_file(shard_path)
            with _sqlite_readonly(shard_path) as source:
                for row in source.execute("SELECT * FROM archive_conversations ORDER BY conversation_uid"):
                    payload = dict(row)
                    target.execute(
                        """INSERT OR IGNORE INTO legacy_conversations(
                           conversation_id,title,create_time,create_time_warsaw,update_time,update_time_warsaw,payload_json)
                           VALUES(?,?,?,?,?,?,?)""",
                        (
                            row["conversation_uid"], row["title"], _timestamp_float(row["create_time"]),
                            _timestamp_value(row["create_time"]), _timestamp_float(row["update_time"]),
                            _timestamp_value(row["update_time"]), _canonical_json(payload),
                        ),
                    )
                    counts["archive_conversations"] += max(0, target.execute("SELECT changes()").fetchone()[0])
                query = """
                    SELECT m.*,c.title,b.text,o.source_locator,o.source_uid AS occurrence_source_uid
                      FROM archive_messages m
                      JOIN content_blobs b ON b.content_hash=m.content_hash
                      LEFT JOIN archive_conversations c ON c.conversation_uid=m.conversation_uid
                      LEFT JOIN archive_message_occurrences o ON o.occurrence_uid=m.first_occurrence_uid
                     ORDER BY m.conversation_uid,COALESCE(m.visible_index,2147483647),m.message_uid
                """
                for row in source.execute(query):
                    role = str(row["role"] or "unknown")
                    text = str(row["text"] or "")
                    source_uid = str(row["first_source_uid"] or row["occurrence_source_uid"] or "")
                    source_info = sources.get(source_uid, {})
                    source_file = str(source_info.get("source_name") or source_info.get("path") or "") or None
                    source_sha = str(source_info.get("sha256") or "") or None
                    refs = [{
                        "occurrence_uid": row["first_occurrence_uid"],
                        "source_uid": source_uid or None,
                        "source_locator": row["source_locator"],
                    }]
                    target.execute(
                        """INSERT OR IGNORE INTO messages(
                           message_id,conversation_id,conversation_title,role,timestamp,content_text,content_hash,
                           first_source_file,first_source_sha256,source_refs_json,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            row["message_uid"], row["conversation_uid"], row["title"], role,
                            _timestamp_value(row["create_time"]), text, row["content_hash"], source_file,
                            source_sha, _canonical_json(refs), _timestamp_value(row["create_time"]),
                            _timestamp_value(row["create_time"]),
                        ),
                    )
                    changed = max(0, target.execute("SELECT changes()").fetchone()[0])
                    counts["archive_messages"] += changed
                    if changed:
                        target.execute(
                            """INSERT INTO legacy_messages(
                               conversation_id,conversation_title,message_id,author_role,create_time,create_time_warsaw,
                               text,parts_json,assets_json,is_visible_path,visible_index,text_sha256,char_count)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                row["conversation_uid"], row["title"], row["message_uid"], role,
                                _timestamp_float(row["create_time"]), _timestamp_value(row["create_time"]), text,
                                _canonical_json([text]), "[]", int(row["is_visible_path"] or 0),
                                row["visible_index"], row["content_hash"], len(text),
                            ),
                        )
                        record = {
                            "message_uid": row["message_uid"], "conversation_uid": row["conversation_uid"],
                            "content_hash": row["content_hash"], "source_uid": source_uid,
                            "source_locator": row["source_locator"],
                        }
                        self._provenance(
                            target, "messages", str(row["message_uid"]), "conversation_archive",
                            shard_path, shard_sha, None, record,
                        )
                    if source_uid:
                        source_key = str(row["first_occurrence_uid"] or source_uid)
                        target.execute(
                            "INSERT OR IGNORE INTO message_sources VALUES(?,?,?,?,?)",
                            (row["message_uid"], source_key, source_file, source_sha, _canonical_json(refs[0])),
                        )
                        counts["archive_message_sources"] += max(0, target.execute("SELECT changes()").fetchone()[0])
        return counts

    def _import_journal(self, target: sqlite3.Connection) -> dict[str, int]:
        counts = {"journal_imported": 0, "journal_invalid": 0}
        if not self.journal_path.is_file():
            return counts
        data = json.loads(self.journal_path.read_text(encoding="utf-8-sig"))
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ValueError("dziennik.json must contain an entries list")
        source_sha = _sha256_file(self.journal_path)
        for line_no, raw in enumerate(entries, start=1):
            if not isinstance(raw, dict):
                counts["journal_invalid"] += 1
                continue
            record_sha = _source_record_hash(raw)
            journal_id = str(raw.get("id") or raw.get("entry_id") or record_sha)
            created = _timestamp_value(raw.get("timestamp") or raw.get("data")) or "1970-01-01T00:00:00+00:00"
            kind = str(raw.get("typ") or raw.get("type") or raw.get("kategoria") or "legacy_journal")
            text = _journal_text(raw)
            target.execute(
                "INSERT OR IGNORE INTO journal(journal_id,created_at_utc,created_at_local,kind,text,payload_json) VALUES(?,?,?,?,?,?)",
                (journal_id, created, str(raw.get("data") or created), kind, text, _canonical_json(raw)),
            )
            changed = max(0, target.execute("SELECT changes()").fetchone()[0])
            counts["journal_imported"] += changed
            if changed:
                self._provenance(target, "journal", journal_id, "dziennik_json", self.journal_path, source_sha, line_no, raw)
        return counts

    def _import_layered(self, target: sqlite3.Connection) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table, primary, fallback in LAYERED_SOURCES:
            source_path = self.memory_root / primary
            if not source_path.is_file():
                source_path = self.memory_root / fallback
            if not source_path.is_file():
                counts[f"{table}_missing"] = 1
                continue
            source_sha = _sha256_file(source_path)
            imported = invalid = 0
            with source_path.open("r", encoding="utf-8-sig") as handle:
                for line_no, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        if not isinstance(raw, dict):
                            raise ValueError("record_not_object")
                        record_id = self._insert_layered_record(target, table, raw)
                        changed = max(0, target.execute("SELECT changes()").fetchone()[0])
                        imported += changed
                        if changed:
                            self._provenance(
                                target, table, record_id, "layered_jsonl", source_path,
                                source_sha, line_no, raw,
                            )
                    except Exception:
                        invalid += 1
            counts[f"{table}_imported"] = imported
            counts[f"{table}_invalid"] = invalid
        return counts

    def _insert_layered_record(self, target: sqlite3.Connection, table: str, raw: dict[str, Any]) -> str:
        if table == "episodic_memories":
            record_id = str(raw.get("episode_id") or _source_record_hash(raw))
            participants = raw.get("participants") if isinstance(raw.get("participants"), list) else _json_list(raw.get("participants_json"))
            target.execute(
                "INSERT OR IGNORE INTO episodic_memories VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record_id, _timestamp_value(raw.get("created_at_utc")) or "1970-01-01T00:00:00+00:00",
                    raw.get("local_time_label"), str(raw.get("scene") or raw.get("content") or ""),
                    _canonical_json(participants), raw.get("emotional_anchor"), str(raw.get("source") or "layered_jsonl"),
                    str(raw.get("grounding") or "recovered"), float(raw.get("confidence") or 0.0),
                    raw.get("raw_excerpt"), _canonical_json(raw.get("tags") if isinstance(raw.get("tags"), list) else _json_list(raw.get("tags_json"))),
                ),
            )
            return record_id
        if table == "semantic_facts":
            record_id = str(raw.get("fact_id") or _source_record_hash(raw))
            target.execute(
                "INSERT OR IGNORE INTO semantic_facts VALUES(?,?,?,?,?,?,?,?)",
                (
                    record_id, _timestamp_value(raw.get("created_at_utc")) or "1970-01-01T00:00:00+00:00",
                    str(raw.get("subject") or "unknown"), str(raw.get("predicate") or "states"),
                    str(raw.get("value") or raw.get("content") or ""), str(raw.get("source") or "layered_jsonl"),
                    float(raw.get("confidence") or 0.0),
                    _canonical_json(raw.get("tags") if isinstance(raw.get("tags"), list) else _json_list(raw.get("tags_json"))),
                ),
            )
            return record_id
        if table == "procedural_rules":
            record_id = str(raw.get("rule_id") or _source_record_hash(raw))
            target.execute(
                "INSERT OR IGNORE INTO procedural_rules VALUES(?,?,?,?,?,?,?)",
                (
                    record_id, _timestamp_value(raw.get("created_at_utc")) or "1970-01-01T00:00:00+00:00",
                    str(raw.get("trigger") or "unknown"), str(raw.get("action") or raw.get("content") or ""),
                    str(raw.get("reason") or "legacy recovered source"), int(raw.get("priority") or 50),
                    str(raw.get("source") or "layered_jsonl"),
                ),
            )
            return record_id
        if table == "reflection_entries":
            record_id = str(raw.get("reflection_id") or _source_record_hash(raw))
            target.execute(
                "INSERT OR IGNORE INTO reflection_entries VALUES(?,?,?,?,?,?,?,?)",
                (
                    record_id, _timestamp_value(raw.get("created_at_utc")) or "1970-01-01T00:00:00+00:00",
                    raw.get("episode_id"), str(raw.get("meaning_for_latka") or raw.get("content") or ""),
                    str(raw.get("identity_impact") or ""), str(raw.get("boundary_note") or "recovered_unverified"),
                    raw.get("next_question"), float(raw.get("confidence") or 0.0),
                ),
            )
            return record_id
        if table == "truth_audits":
            record_id = str(raw.get("audit_id") or _source_record_hash(raw))
            audit = raw.get("audit_json")
            if audit is None:
                audit = _canonical_json(raw.get("audit") or [])
            elif not isinstance(audit, str):
                audit = _canonical_json(audit)
            target.execute(
                "INSERT OR IGNORE INTO truth_audits VALUES(?,?,?,?)",
                (
                    record_id, _timestamp_value(raw.get("created_at_utc")) or "1970-01-01T00:00:00+00:00",
                    str(raw.get("text") or raw.get("content") or ""), str(audit),
                ),
            )
            return record_id
        raise ValueError(f"unsupported layered table: {table}")

    def _provenance(
        self,
        target: sqlite3.Connection,
        target_table: str,
        target_id: str,
        source_kind: str,
        source_path: Path,
        source_sha256: str,
        source_line: int | None,
        raw: dict[str, Any],
    ) -> None:
        record_sha = _source_record_hash(raw)
        target.execute(
            "INSERT OR IGNORE INTO recovery_provenance VALUES(?,?,?,?,?,?,?,?)",
            (
                target_table, target_id, source_kind, str(source_path), source_sha256,
                source_line, record_sha, _canonical_json(raw),
            ),
        )

    @staticmethod
    def _table_counts(con: sqlite3.Connection) -> dict[str, int]:
        tables = (
            "messages", "legacy_conversations", "legacy_messages", "journal",
            "episodic_memories", "semantic_facts", "procedural_rules",
            "reflection_entries", "truth_audits", "recovery_provenance",
        )
        return {table: int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}

    @staticmethod
    def _progress(callback: ProgressCallback | None, completed: int, total: int, label: str) -> None:
        if callback is not None:
            callback(completed, total, label)
