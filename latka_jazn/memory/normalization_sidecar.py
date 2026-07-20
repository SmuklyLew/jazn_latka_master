from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import sqlite3
import uuid

from latka_jazn.config import JaznConfig
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_normalization_sidecar")
WAKE_STATE_SCHEMA_VERSION = schema_version("wake_state_snapshot")
LAYERED_DEDUPE_SCHEMA_VERSION = schema_version("layered_dedupe")
FINGERPRINT_CONTRACT_VERSION = "memory_normalization_input/v1"
RELEVANT_FINGERPRINT_COLUMNS: dict[str, tuple[str, ...]] = {
    "messages": (
        "message_id", "conversation_id", "conversation_title", "role", "timestamp",
        "content_text", "content_hash", "first_source_file", "first_source_sha256", "source_refs_json",
    ),
    "legacy_chunks": (
        "legacy_chunk_id", "source_sha256", "source_file", "source_rel_path", "chunk_index",
        "page_start", "page_end", "content_text", "inferred_date",
    ),
    "episodic_memories": (
        "episode_id", "created_at_utc", "scene", "participants_json", "source", "grounding",
        "confidence", "raw_excerpt", "tags_json",
    ),
    "semantic_facts": (
        "fact_id", "created_at_utc", "subject", "predicate", "value", "source", "confidence", "tags_json",
    ),
    "procedural_rules": (
        "rule_id", "created_at_utc", "trigger", "action", "reason", "priority", "source",
    ),
    "reflection_entries": (
        "reflection_id", "created_at_utc", "episode_id", "meaning_for_latka", "identity_impact",
        "boundary_note", "next_question", "confidence",
    ),
    "truth_audits": ("audit_id", "created_at_utc", "text", "audit_json"),
    "journal": ("journal_id", "created_at_utc", "created_at_local", "kind", "text", "payload_json"),
}
TRUTH_BOUNDARY = (
    "Sidecar normalizacji czyta aktywną SQLite jako źródło i zapisuje wyłącznie "
    "do bazy audytowej. Nie nadpisuje kanonicznej pamięci rozmów i nie udaje "
    "ciągłości bez zbudowanego wake_state."
)
DEDUPE_TRUTH_BOUNDARY = (
    "Deduplikacja warstwowa nie kasuje rekordów źródłowych. Zapisuje grupy i "
    "reprezentantów w sidecarze, żeby wyszukiwanie i wake_state mogły zwijać "
    "szum bez utraty historii wystąpień, źródeł, kontekstu i wag."
)


@dataclass(slots=True)
class MemoryNormalizationStatus:
    schema_version: str
    source_db_path: str
    sidecar_db_path: str
    source_db_exists: bool
    sidecar_db_exists: bool
    sidecar_schema_present: bool
    status: str
    source_counts: dict[str, int]
    sidecar_counts: dict[str, int]
    last_run: dict[str, Any] | None
    freshness: dict[str, Any] | None = None
    truth_boundary: str = TRUTH_BOUNDARY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NormalizationRunReport:
    schema_version: str
    run_id: str | None
    dry_run: bool
    status: str
    source_db_path: str
    sidecar_db_path: str
    input_counts: dict[str, int]
    output_counts: dict[str, int]
    errors: list[str]
    source_integrity_check: str | None
    source_foreign_key_error_count: int | None
    source_fingerprint: dict[str, Any] | None = None
    truth_boundary: str = TRUTH_BOUNDARY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WakeStateStatus:
    schema_version: str
    sidecar_db_path: str
    sidecar_db_exists: bool
    sidecar_schema_present: bool
    active_snapshot_present: bool
    active_snapshot: dict[str, Any] | None
    status: str
    deep_verify: bool = False
    active_snapshot_count: int = 0
    source_integrity_check: str | None = None
    source_foreign_key_error_count: int | None = None
    sidecar_integrity_check: str | None = None
    sidecar_foreign_key_error_count: int | None = None
    errors: list[str] | None = None
    freshness: dict[str, Any] | None = None
    truth_boundary: str = TRUTH_BOUNDARY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WakeStateBuildReport:
    schema_version: str
    dry_run: bool
    status: str
    snapshot_id: str | None
    snapshot_sha256: str | None
    item_count: int
    actor_count: int
    snapshot: dict[str, Any] | None
    errors: list[str]
    truth_boundary: str = TRUTH_BOUNDARY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryPrepareReport:
    schema_version: str
    dry_run: bool
    force: bool
    deep_verify: bool
    status: str
    normalization_performed: bool
    snapshot_built: bool
    normalization: dict[str, Any] | None
    wake_state: dict[str, Any]
    errors: list[str]
    truth_boundary: str = TRUTH_BOUNDARY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LayeredDedupeReport:
    schema_version: str
    run_id: str | None
    dry_run: bool
    status: str
    source_db_path: str
    sidecar_db_path: str
    input_counts: dict[str, int]
    layer_counts: dict[str, dict[str, int]]
    errors: list[str]
    truth_boundary: str = DEDUPE_TRUTH_BOUNDARY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SIDE_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sidecar_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS normalization_runs(
  run_id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  runtime_version TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  ended_at_utc TEXT,
  mode TEXT NOT NULL,
  source_db_path TEXT NOT NULL,
  source_db_sha256 TEXT,
  source_db_size INTEGER,
  source_db_mtime_ns INTEGER,
  source_db_wal_size INTEGER,
  source_db_wal_mtime_ns INTEGER,
  source_db_shm_present INTEGER,
  source_schema_sha256 TEXT,
  relevant_content_sha256 TEXT,
  relevant_schema_sha256 TEXT,
  relevant_row_count INTEGER,
  relevant_revision TEXT,
  fingerprint_contract_version TEXT,
  source_integrity_check TEXT,
  source_foreign_key_error_count INTEGER,
  input_counts_json TEXT NOT NULL DEFAULT '{}',
  output_counts_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  errors_json TEXT NOT NULL DEFAULT '[]',
  dry_run INTEGER NOT NULL DEFAULT 0,
  truth_boundary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actors(
  actor_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  identity_confidence REAL NOT NULL,
  privacy_namespace TEXT NOT NULL,
  source_evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS normalized_memory_items(
  item_id TEXT PRIMARY KEY,
  memory_type TEXT NOT NULL,
  source_table TEXT NOT NULL,
  source_row_id TEXT,
  conversation_id TEXT,
  message_id TEXT,
  speaker_actor_id TEXT NOT NULL,
  interlocutor_actor_id TEXT NOT NULL,
  participants_json TEXT NOT NULL,
  source_timestamp TEXT,
  source_timestamp_confidence REAL NOT NULL DEFAULT 0.0,
  source_file TEXT,
  source_sha256 TEXT,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  source_conversation_title TEXT,
  content_excerpt TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  grounding TEXT NOT NULL,
  truth_status TEXT NOT NULL,
  confidence REAL NOT NULL,
  importance REAL NOT NULL,
  privacy_scope TEXT NOT NULL,
  memory_namespace TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  source_evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL,
  run_id TEXT,
  FOREIGN KEY(speaker_actor_id) REFERENCES actors(actor_id),
  FOREIGN KEY(interlocutor_actor_id) REFERENCES actors(actor_id),
  FOREIGN KEY(run_id) REFERENCES normalization_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_normalized_memory_namespace ON normalized_memory_items(memory_namespace);
CREATE INDEX IF NOT EXISTS idx_normalized_memory_type ON normalized_memory_items(memory_type);
CREATE INDEX IF NOT EXISTS idx_normalized_memory_conversation ON normalized_memory_items(conversation_id);
CREATE INDEX IF NOT EXISTS idx_normalized_memory_message ON normalized_memory_items(message_id);
CREATE INDEX IF NOT EXISTS idx_normalized_memory_source ON normalized_memory_items(source_table, source_sha256);
CREATE TABLE IF NOT EXISTS wake_state_snapshots(
  snapshot_id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0,
  source_run_id TEXT,
  snapshot_sha256 TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  validation_status TEXT NOT NULL,
  truth_boundary TEXT NOT NULL,
  FOREIGN KEY(source_run_id) REFERENCES normalization_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_wake_state_active ON wake_state_snapshots(active, created_at_utc);
CREATE TABLE IF NOT EXISTS layered_dedupe_runs(
  run_id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  runtime_version TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  ended_at_utc TEXT,
  mode TEXT NOT NULL,
  source_db_path TEXT NOT NULL,
  sidecar_db_path TEXT NOT NULL,
  candidate_count INTEGER NOT NULL DEFAULT 0,
  full_text_fallback_count INTEGER NOT NULL DEFAULT 0,
  layer_counts_json TEXT NOT NULL DEFAULT '{}',
  criteria_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  errors_json TEXT NOT NULL DEFAULT '[]',
  dry_run INTEGER NOT NULL DEFAULT 0,
  truth_boundary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS layered_dedupe_groups(
  group_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  layer TEXT NOT NULL,
  group_key_hash TEXT NOT NULL,
  representative_item_id TEXT NOT NULL,
  member_count INTEGER NOT NULL,
  redundant_count INTEGER NOT NULL,
  memory_type TEXT,
  source_table TEXT,
  speaker_actor_id TEXT,
  memory_namespace TEXT,
  truth_status TEXT,
  context_policy TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  literal_text_hash TEXT,
  context_hash TEXT,
  metadata_hash TEXT,
  max_importance REAL NOT NULL DEFAULT 0.0,
  max_confidence REAL NOT NULL DEFAULT 0.0,
  first_timestamp TEXT,
  last_timestamp TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at_utc TEXT NOT NULL,
  truth_boundary TEXT NOT NULL,
  UNIQUE(run_id, layer, group_key_hash),
  FOREIGN KEY(run_id) REFERENCES layered_dedupe_runs(run_id),
  FOREIGN KEY(representative_item_id) REFERENCES normalized_memory_items(item_id)
);
CREATE TABLE IF NOT EXISTS layered_dedupe_members(
  group_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  role_in_group TEXT NOT NULL,
  source_table TEXT NOT NULL,
  source_row_id TEXT,
  conversation_id TEXT,
  message_id TEXT,
  memory_type TEXT NOT NULL,
  source_timestamp TEXT,
  importance REAL NOT NULL DEFAULT 0.0,
  confidence REAL NOT NULL DEFAULT 0.0,
  text_hash TEXT NOT NULL,
  literal_text_hash TEXT,
  context_before_hash TEXT,
  context_after_hash TEXT,
  context_hash TEXT,
  metadata_hash TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at_utc TEXT NOT NULL,
  PRIMARY KEY(group_id, item_id),
  FOREIGN KEY(group_id) REFERENCES layered_dedupe_groups(group_id),
  FOREIGN KEY(item_id) REFERENCES normalized_memory_items(item_id)
);
CREATE INDEX IF NOT EXISTS idx_layered_dedupe_groups_layer ON layered_dedupe_groups(layer, member_count);
CREATE INDEX IF NOT EXISTS idx_layered_dedupe_members_item ON layered_dedupe_members(item_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _dedupe_text(text: str | None) -> str:
    return " ".join((text or "").replace("\x00", " ").split())


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sqlite_checks(path: Path) -> tuple[str | None, int | None, list[str]]:
    errors: list[str] = []
    try:
        with _connect_readonly(path) as con:
            integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
            fk_count = sum(1 for _ in con.execute("PRAGMA foreign_key_check"))
        return integrity, fk_count, errors
    except Exception as exc:
        errors.append(repr(exc))
        return None, None, errors


def _sqlite_schema_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with _connect_readonly(path) as con:
            for row in con.execute(
                "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name"
            ):
                digest.update(_json(list(row)).encode("utf-8"))
                digest.update(b"\n")
        return digest.hexdigest()
    except Exception:
        return None


def _file_fingerprint(path: Path) -> tuple[int | None, int | None]:
    try:
        stat = path.stat()
        return int(stat.st_size), int(stat.st_mtime_ns)
    except OSError:
        return None, None


def _physical_sqlite_state(path: Path, *, include_hash: bool = False) -> dict[str, Any]:
    size, mtime_ns = _file_fingerprint(path)
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    wal_size, wal_mtime_ns = _file_fingerprint(wal)
    return {
        "source_db_size": size,
        "source_db_mtime_ns": mtime_ns,
        "source_db_sha256": _sha256_file(path) if include_hash else None,
        "source_db_wal_size": wal_size or 0,
        "source_db_wal_mtime_ns": wal_mtime_ns,
        "source_db_shm_present": shm.exists(),
    }


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _fingerprint_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if isinstance(value, float):
        return format(value, ".17g")
    return value


def _relevant_logical_fingerprint(path: Path, *, include_content: bool) -> dict[str, Any]:
    """Fingerprint only semantic inputs consumed by normalization.

    Fast callers collect schema/count/revision metadata. Deep verification also
    streams deterministic semantic rows, without rowid, WAL or SQLite page layout.
    """
    result: dict[str, Any] = {
        "fingerprint_contract_version": FINGERPRINT_CONTRACT_VERSION,
        "relevant_content_sha256": None,
        "relevant_schema_sha256": None,
        "relevant_row_count": 0,
        "relevant_revision": None,
        "tables": {},
    }
    con = _connect_readonly(path)
    try:
        present_tables = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        schema_digest = hashlib.sha256()
        revision_rows: list[dict[str, Any]] = []
        content_digest = hashlib.sha256() if include_content else None
        total = 0
        for table, configured_columns in RELEVANT_FINGERPRINT_COLUMNS.items():
            if table not in present_tables:
                result["tables"][table] = {"present": False, "row_count": 0}
                continue
            schema_rows = con.execute(
                "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master "
                "WHERE (type='table' AND name=?) OR (tbl_name=? AND type IN ('index','trigger')) "
                "ORDER BY type,name,tbl_name",
                (table, table),
            ).fetchall()
            for schema_row in schema_rows:
                schema_digest.update(_json(list(schema_row)).encode("utf-8"))
                schema_digest.update(b"\n")
            available = {
                str(row[1])
                for row in con.execute(f"PRAGMA table_info({_quoted(table)})")
            }
            columns = tuple(column for column in configured_columns if column in available)
            if not columns:
                count = 0
                identity_min = identity_max = max_timestamp = None
            else:
                count = int(con.execute(f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()[0] or 0)
                identity_column = columns[0]
                timestamp_column = next(
                    (column for column in ("timestamp", "inferred_date", "created_at_utc") if column in columns),
                    identity_column,
                )
                aggregate = con.execute(
                    f"SELECT MIN(CAST({_quoted(identity_column)} AS TEXT)), "
                    f"MAX(CAST({_quoted(identity_column)} AS TEXT)), "
                    f"MAX(CAST({_quoted(timestamp_column)} AS TEXT)) FROM {_quoted(table)}"
                ).fetchone()
                identity_min, identity_max, max_timestamp = aggregate
            table_revision = {
                "table": table,
                "columns": list(columns),
                "row_count": count,
                "identity_min": identity_min,
                "identity_max": identity_max,
                "max_semantic_timestamp": max_timestamp,
            }
            result["tables"][table] = {"present": True, **table_revision}
            revision_rows.append(table_revision)
            total += count
            if content_digest is not None and columns:
                select_columns = ",".join(_quoted(column) for column in columns)
                order_columns = ",".join(_quoted(column) for column in columns)
                for row in con.execute(
                    f"SELECT {select_columns} FROM {_quoted(table)} ORDER BY {order_columns}"
                ):
                    content_digest.update(table.encode("utf-8"))
                    content_digest.update(b"\x00")
                    content_digest.update(
                        _json([_fingerprint_value(value) for value in row]).encode("utf-8")
                    )
                    content_digest.update(b"\n")
        result["relevant_schema_sha256"] = schema_digest.hexdigest()
        result["relevant_row_count"] = total
        result["relevant_revision"] = _hash_text(_json(revision_rows))
        if content_digest is not None:
            result["relevant_content_sha256"] = content_digest.hexdigest()
        return result
    finally:
        con.close()


def _connect_readonly(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    options = "mode=ro&immutable=1" if immutable else "mode=ro"
    uri = f"file:{path.resolve().as_posix()}?{options}"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def _connect_write(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def _count_table(con: sqlite3.Connection, table: str) -> int:
    if not _table_exists(con, table):
        return 0
    return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)


def _excerpt(text: str, limit: int = 1800) -> str:
    clean = " ".join((text or "").replace("\x00", " ").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _load_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _bucket_float(value: Any, *, scale: int = 10) -> str:
    return str(int(round(_safe_float(value) * scale)))


def _snapshot_summary(
    snapshot: dict[str, Any],
    *,
    snapshot_id: str | None,
    snapshot_sha256: str | None,
    source_run_id: str | None = None,
    created_at_utc: str | None = None,
    validation_status: str | None = None,
) -> dict[str, Any]:
    namespace_policy = snapshot.get("namespace_policy") if isinstance(snapshot.get("namespace_policy"), dict) else {}
    truth_digest = snapshot.get("truth_boundary_digest") if isinstance(snapshot.get("truth_boundary_digest"), dict) else {}
    relationship = snapshot.get("relationship_digest") if isinstance(snapshot.get("relationship_digest"), dict) else {}
    return {
        "snapshot_id": snapshot_id,
        "schema_version": snapshot.get("schema_version"),
        "created_at_utc": created_at_utc or snapshot.get("created_at_utc"),
        "source_run_id": source_run_id,
        "snapshot_sha256": snapshot_sha256,
        "validation_status": validation_status or snapshot.get("validation_status"),
        "source_counts": snapshot.get("source_counts") if isinstance(snapshot.get("source_counts"), dict) else {},
        "namespace_counts": namespace_policy.get("namespace_counts") if isinstance(namespace_policy.get("namespace_counts"), dict) else {},
        "relationship_digest": {
            "krzysztof_candidate_present": bool(relationship.get("krzysztof_candidate_present")),
            "krzysztof_private_namespace_allowed": bool(relationship.get("krzysztof_private_namespace_allowed")),
            "private_namespace_requires_confirmed_actor": True,
        },
        "truth_boundary_digest": {
            "must_not_claim_background_process": bool(truth_digest.get("must_not_claim_background_process")),
            "must_not_claim_memory_without_source": bool(truth_digest.get("must_not_claim_memory_without_source")),
            "emotions_are_modelled_operational_relational": bool(
                truth_digest.get("emotions_are_modelled_operational_relational")
            ),
            "source_truth_counts": truth_digest.get("source_truth_counts") if isinstance(truth_digest.get("source_truth_counts"), dict) else {},
        },
    }


class MemoryNormalizationSidecar:
    def __init__(
        self,
        root: Path | str,
        *,
        source_db_path: Path | str | None = None,
        sidecar_db_path: Path | str | None = None,
        runtime_version: str | None = None,
    ) -> None:
        cfg = JaznConfig(root=Path(root))
        self.root = Path(root)
        self.runtime_version = runtime_version or cfg.version
        self.source_db_path = Path(source_db_path) if source_db_path else cfg.normalization_source_db_path
        self.sidecar_db_path = Path(sidecar_db_path) if sidecar_db_path else cfg.normalization_sidecar_db_path

    def ensure_schema(self) -> None:
        with _connect_write(self.sidecar_db_path) as con:
            con.executescript(SIDE_SCHEMA)
            columns = {str(row[1]) for row in con.execute("PRAGMA table_info(normalization_runs)")}
            for name, sql_type in (
                ("source_db_size", "INTEGER"),
                ("source_db_mtime_ns", "INTEGER"),
                ("source_db_wal_size", "INTEGER"),
                ("source_db_wal_mtime_ns", "INTEGER"),
                ("source_db_shm_present", "INTEGER"),
                ("source_schema_sha256", "TEXT"),
                ("relevant_content_sha256", "TEXT"),
                ("relevant_schema_sha256", "TEXT"),
                ("relevant_row_count", "INTEGER"),
                ("relevant_revision", "TEXT"),
                ("fingerprint_contract_version", "TEXT"),
            ):
                if name not in columns:
                    con.execute(f"ALTER TABLE normalization_runs ADD COLUMN {name} {sql_type}")
            con.execute("INSERT OR REPLACE INTO sidecar_meta(key,value) VALUES(?,?)", ("schema_version", SCHEMA_VERSION))
            con.execute("INSERT OR REPLACE INTO sidecar_meta(key,value) VALUES(?,?)", ("truth_boundary", TRUTH_BOUNDARY))
            con.commit()

            # Legacy sidecars could contain several active rows. Keep the newest one
            # before installing the partial unique index. Both changes are atomic.
            con.execute("BEGIN IMMEDIATE")
            active_rows = con.execute(
                "SELECT snapshot_id FROM wake_state_snapshots WHERE active=1 "
                "ORDER BY created_at_utc DESC, rowid DESC"
            ).fetchall()
            for row in active_rows[1:]:
                con.execute("UPDATE wake_state_snapshots SET active=0 WHERE snapshot_id=?", (row[0],))
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_wake_state_single_active "
                "ON wake_state_snapshots(active) WHERE active=1"
            )
            con.commit()

    def _evaluate_freshness(self, run: dict[str, Any] | sqlite3.Row, *, deep_verify: bool) -> dict[str, Any]:
        stored = dict(run)
        flags = {
            "relevant_content_changed": False,
            "relevant_schema_changed": False,
            "relevant_row_revision_changed": False,
            "metadata_only_change": False,
            "wal_checkpoint_only": False,
            "file_layout_changed_without_logical_change": False,
            "physical_hash_changed": None,
            "logical_fingerprint_changed": False,
            "unchanged": False,
            "unknown_change_reason": False,
            "invalidates_wake_state": False,
            "reason": "unchanged",
        }
        required = (
            "relevant_content_sha256", "relevant_schema_sha256", "relevant_row_count",
            "relevant_revision", "fingerprint_contract_version",
        )
        if any(stored.get(name) is None for name in required):
            flags.update(
                reason="fingerprint_contract_missing",
                unknown_change_reason=True,
                invalidates_wake_state=True,
            )
            return flags
        if str(stored.get("fingerprint_contract_version")) != FINGERPRINT_CONTRACT_VERSION:
            flags.update(
                reason="fingerprint_contract_changed",
                unknown_change_reason=True,
                invalidates_wake_state=True,
            )
            return flags
        try:
            logical = _relevant_logical_fingerprint(self.source_db_path, include_content=deep_verify)
            physical = _physical_sqlite_state(self.source_db_path, include_hash=deep_verify)
        except Exception as exc:
            flags.update(
                reason="fingerprint_read_error",
                unknown_change_reason=True,
                invalidates_wake_state=True,
                error=repr(exc),
            )
            return flags

        flags.update(
            fingerprint_contract_version=FINGERPRINT_CONTRACT_VERSION,
            stored_relevant_content_sha256=stored.get("relevant_content_sha256"),
            current_relevant_content_sha256=logical.get("relevant_content_sha256"),
            stored_relevant_schema_sha256=stored.get("relevant_schema_sha256"),
            current_relevant_schema_sha256=logical.get("relevant_schema_sha256"),
            stored_relevant_row_count=stored.get("relevant_row_count"),
            current_relevant_row_count=logical.get("relevant_row_count"),
            stored_relevant_revision=stored.get("relevant_revision"),
            current_relevant_revision=logical.get("relevant_revision"),
            stored_source_db_sha256=stored.get("source_db_sha256"),
            current_source_db_sha256=physical.get("source_db_sha256"),
            stored_source_db_size=stored.get("source_db_size"),
            current_source_db_size=physical.get("source_db_size"),
            stored_source_db_mtime_ns=stored.get("source_db_mtime_ns"),
            current_source_db_mtime_ns=physical.get("source_db_mtime_ns"),
            stored_source_db_wal_size=stored.get("source_db_wal_size") or 0,
            current_source_db_wal_size=physical.get("source_db_wal_size") or 0,
            stored_source_db_wal_mtime_ns=stored.get("source_db_wal_mtime_ns"),
            current_source_db_wal_mtime_ns=physical.get("source_db_wal_mtime_ns"),
            stored_source_db_shm_present=bool(stored.get("source_db_shm_present")),
            current_source_db_shm_present=bool(physical.get("source_db_shm_present")),
        )
        if logical["relevant_schema_sha256"] != stored.get("relevant_schema_sha256"):
            flags.update(
                reason="relevant_schema_changed",
                relevant_schema_changed=True,
                logical_fingerprint_changed=True,
                invalidates_wake_state=True,
            )
            return flags
        if (
            int(logical["relevant_row_count"]) != int(stored.get("relevant_row_count") or 0)
            or logical["relevant_revision"] != stored.get("relevant_revision")
        ):
            flags.update(
                reason="relevant_row_revision_changed",
                relevant_row_revision_changed=True,
                logical_fingerprint_changed=True,
                invalidates_wake_state=True,
            )
            if deep_verify and logical.get("relevant_content_sha256") != stored.get("relevant_content_sha256"):
                flags.update(reason="relevant_content_changed", relevant_content_changed=True)
            return flags
        if deep_verify and logical.get("relevant_content_sha256") != stored.get("relevant_content_sha256"):
            flags.update(
                reason="relevant_content_changed",
                relevant_content_changed=True,
                logical_fingerprint_changed=True,
                invalidates_wake_state=True,
            )
            return flags

        main_metadata_changed = (
            physical.get("source_db_size") != stored.get("source_db_size")
            or physical.get("source_db_mtime_ns") != stored.get("source_db_mtime_ns")
        )
        wal_changed = (
            (physical.get("source_db_wal_size") or 0) != (stored.get("source_db_wal_size") or 0)
            or physical.get("source_db_wal_mtime_ns") != stored.get("source_db_wal_mtime_ns")
            or bool(physical.get("source_db_shm_present")) != bool(stored.get("source_db_shm_present"))
        )
        if deep_verify:
            flags["physical_hash_changed"] = physical.get("source_db_sha256") != stored.get("source_db_sha256")
        if not main_metadata_changed and not wal_changed and not flags["physical_hash_changed"]:
            flags.update(reason="unchanged", unchanged=True)
        elif main_metadata_changed and wal_changed and not (physical.get("source_db_wal_size") or 0):
            flags.update(reason="wal_checkpoint_only", wal_checkpoint_only=True)
        elif main_metadata_changed:
            flags.update(
                reason="file_layout_changed_without_logical_change",
                file_layout_changed_without_logical_change=True,
            )
        else:
            flags.update(reason="metadata_only_change", metadata_only_change=True)
        return flags

    def status(self, *, deep_verify: bool = False) -> MemoryNormalizationStatus:
        # Ordinary startup/status remains bounded: only file metadata and small
        # sidecar rows are read. Deep verification is explicit.
        source_counts = self._source_counts(immutable=True) if deep_verify else {}
        sidecar_counts: dict[str, int] = {}
        schema_present = False
        last_run = None
        freshness: dict[str, Any] | None = None
        read_error = False
        if self.sidecar_db_path.exists():
            try:
                with _connect_readonly(self.sidecar_db_path) as con:
                    required = {"normalization_runs", "actors", "normalized_memory_items", "wake_state_snapshots"}
                    optional = {"layered_dedupe_runs", "layered_dedupe_groups", "layered_dedupe_members"}
                    present = {
                        str(row[0])
                        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    }
                    schema_present = required.issubset(present)
                    for table in sorted(required | (optional & present)):
                        sidecar_counts[table] = _count_table(con, table)
                    if _table_exists(con, "normalization_runs"):
                        row = con.execute(
                            "SELECT * FROM normalization_runs ORDER BY started_at_utc DESC LIMIT 1"
                        ).fetchone()
                        last_run = dict(row) if row else None
            except Exception as exc:
                read_error = True
                sidecar_counts["read_error"] = 1
                last_run = {"status": "read_error", "error": repr(exc)}
        if not self.source_db_path.exists():
            status = "source_missing"
        elif not self.sidecar_db_path.exists():
            status = "sidecar_missing"
        elif not schema_present:
            status = "sidecar_schema_missing"
        elif read_error:
            status = "read_error"
        elif not last_run:
            status = "normalization_required"
        elif str(last_run.get("status")) != "ok" or not last_run.get("ended_at_utc"):
            status = "source_run_invalid"
        elif str(last_run.get("schema_version")) != SCHEMA_VERSION or str(last_run.get("runtime_version")) != self.runtime_version:
            status = "normalization_stale"
        else:
            freshness = self._evaluate_freshness(last_run, deep_verify=deep_verify)
            if freshness.get("reason") in {"fingerprint_contract_missing", "fingerprint_contract_changed"}:
                status = "normalization_stale"
            elif freshness.get("invalidates_wake_state"):
                status = "source_changed"
            else:
                status = "ready"
            if deep_verify and status == "ready":
                integrity, fk_count, errors = _sqlite_checks(self.source_db_path)
                if errors or integrity != "ok" or fk_count != 0:
                    status = "validation_failed"
            if status == "ready" and sidecar_counts.get("normalized_memory_items", 0) <= 0:
                status = "normalization_required"
        return MemoryNormalizationStatus(
            schema_version=SCHEMA_VERSION,
            source_db_path=str(self.source_db_path),
            sidecar_db_path=str(self.sidecar_db_path),
            source_db_exists=self.source_db_path.exists(),
            sidecar_db_exists=self.sidecar_db_path.exists(),
            sidecar_schema_present=schema_present,
            status=status,
            source_counts=source_counts,
            sidecar_counts=sidecar_counts,
            last_run=last_run,
            freshness=freshness,
        )

    def normalize(self, *, dry_run: bool = False, limit: int | None = None) -> NormalizationRunReport:
        if not self.source_db_path.exists():
            return NormalizationRunReport(
                schema_version=SCHEMA_VERSION,
                run_id=None,
                dry_run=dry_run,
                status="source_missing",
                source_db_path=str(self.source_db_path),
                sidecar_db_path=str(self.sidecar_db_path),
                input_counts={},
                output_counts={},
                errors=[f"missing source db: {self.source_db_path}"],
                source_integrity_check=None,
                source_foreign_key_error_count=None,
            )
        integrity, fk_count, check_errors = _sqlite_checks(self.source_db_path)
        input_counts = self._source_counts()
        if check_errors or integrity != "ok" or fk_count != 0:
            return NormalizationRunReport(
                schema_version=SCHEMA_VERSION,
                run_id=None,
                dry_run=dry_run,
                status="validation_failed",
                source_db_path=str(self.source_db_path),
                sidecar_db_path=str(self.sidecar_db_path),
                input_counts=input_counts,
                output_counts={},
                errors=check_errors or [f"integrity={integrity!r}, foreign_key_errors={fk_count!r}"],
                source_integrity_check=integrity,
                source_foreign_key_error_count=fk_count,
            )
        logical_fingerprint = _relevant_logical_fingerprint(self.source_db_path, include_content=True)
        physical_fingerprint = _physical_sqlite_state(self.source_db_path, include_hash=True)
        source_hash = physical_fingerprint["source_db_sha256"]
        source_size = physical_fingerprint["source_db_size"]
        source_mtime_ns = physical_fingerprint["source_db_mtime_ns"]
        source_schema_hash = _sqlite_schema_sha256(self.source_db_path)
        if dry_run:
            return NormalizationRunReport(
                schema_version=SCHEMA_VERSION,
                run_id=None,
                dry_run=True,
                status="dry_run_ok",
                source_db_path=str(self.source_db_path),
                sidecar_db_path=str(self.sidecar_db_path),
                input_counts=input_counts,
                output_counts=self._estimated_output_counts(input_counts, limit=limit),
                errors=[],
                source_integrity_check=integrity,
                source_foreign_key_error_count=fk_count,
                source_fingerprint=logical_fingerprint,
            )

        self.ensure_schema()
        run_id = str(uuid.uuid4())
        started = _now()
        errors: list[str] = []
        output_counts = {"actors": 0, "normalized_memory_items": 0}
        with _connect_readonly(self.source_db_path) as source:
            with _connect_write(self.sidecar_db_path) as side:
                side.execute(
                    """INSERT INTO normalization_runs
                       (run_id,schema_version,runtime_version,started_at_utc,mode,source_db_path,
                         source_db_sha256,source_db_size,source_db_mtime_ns,
                         source_db_wal_size,source_db_wal_mtime_ns,source_db_shm_present,
                         source_schema_sha256,relevant_content_sha256,relevant_schema_sha256,
                         relevant_row_count,relevant_revision,fingerprint_contract_version,
                         source_integrity_check,source_foreign_key_error_count,
                         input_counts_json,output_counts_json,status,errors_json,dry_run,truth_boundary)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        SCHEMA_VERSION,
                        self.runtime_version,
                        started,
                        "sidecar_normalization",
                        str(self.source_db_path),
                        source_hash,
                        source_size,
                        source_mtime_ns,
                        physical_fingerprint["source_db_wal_size"],
                        physical_fingerprint["source_db_wal_mtime_ns"],
                        int(bool(physical_fingerprint["source_db_shm_present"])),
                        source_schema_hash,
                        logical_fingerprint["relevant_content_sha256"],
                        logical_fingerprint["relevant_schema_sha256"],
                        logical_fingerprint["relevant_row_count"],
                        logical_fingerprint["relevant_revision"],
                        logical_fingerprint["fingerprint_contract_version"],
                        integrity,
                        fk_count,
                        _json(input_counts),
                        "{}",
                        "running",
                        "[]",
                        0,
                        TRUTH_BOUNDARY,
                    ),
                )
                output_counts["actors"] = self._upsert_default_actors(side)
                inserted = 0
                for item in self._iter_normalized_items(source, run_id=run_id, limit=limit):
                    try:
                        before = side.total_changes
                        self._insert_item(side, item)
                        if side.total_changes > before:
                            inserted += 1
                    except Exception as exc:
                        errors.append(repr(exc))
                output_counts["normalized_memory_items"] = inserted
                status = "ok" if not errors and integrity == "ok" and fk_count == 0 else "completed_with_warnings"
                side.execute(
                    """UPDATE normalization_runs
                          SET ended_at_utc=?, output_counts_json=?, status=?, errors_json=?
                        WHERE run_id=?""",
                    (_now(), _json(output_counts), status, _json(errors), run_id),
                )
                side.commit()
        return NormalizationRunReport(
            schema_version=SCHEMA_VERSION,
            run_id=run_id,
            dry_run=False,
            status=status,
            source_db_path=str(self.source_db_path),
            sidecar_db_path=str(self.sidecar_db_path),
            input_counts=input_counts,
            output_counts=output_counts,
            errors=errors,
            source_integrity_check=integrity,
            source_foreign_key_error_count=fk_count,
            source_fingerprint={**logical_fingerprint, **physical_fingerprint},
        )

    def wake_state_status(self, *, deep_verify: bool = False) -> WakeStateStatus:
        if not self.sidecar_db_path.exists():
            return WakeStateStatus(
                schema_version=WAKE_STATE_SCHEMA_VERSION,
                sidecar_db_path=str(self.sidecar_db_path),
                sidecar_db_exists=False,
                sidecar_schema_present=False,
                active_snapshot_present=False,
                active_snapshot=None,
                status="sidecar_missing",
                deep_verify=deep_verify,
                errors=[],
            )
        try:
            with _connect_readonly(self.sidecar_db_path) as con:
                required = {"normalization_runs", "normalized_memory_items", "wake_state_snapshots"}
                schema_present = all(_table_exists(con, name) for name in required)
                active = None
                active_count = 0
                status = "sidecar_schema_missing"
                errors: list[str] = []
                source_integrity = None
                source_fk_count = None
                sidecar_integrity = None
                sidecar_fk_count = None
                freshness: dict[str, Any] | None = None
                if schema_present:
                    rows = con.execute(
                        "SELECT * FROM wake_state_snapshots WHERE active=1 ORDER BY created_at_utc DESC, rowid DESC"
                    ).fetchall()
                    active_count = len(rows)
                    row = rows[0] if rows else None
                    if row:
                        raw = str(row["snapshot_json"])
                        try:
                            snapshot = json.loads(raw)
                        except Exception as exc:
                            snapshot = {}
                            errors.append(f"snapshot_json:{exc!r}")
                        active = _snapshot_summary(
                            snapshot, snapshot_id=row["snapshot_id"], snapshot_sha256=row["snapshot_sha256"],
                            source_run_id=row["source_run_id"], created_at_utc=row["created_at_utc"],
                            validation_status=row["validation_status"],
                        )
                        run = con.execute(
                            "SELECT * FROM normalization_runs WHERE run_id=?", (row["source_run_id"],)
                        ).fetchone()
                        if active_count != 1:
                            status = "validation_failed"
                            errors.append(f"active_snapshot_count={active_count}")
                        elif _hash_text(raw) != str(row["snapshot_sha256"]):
                            status = "snapshot_hash_mismatch"
                        elif run is None or str(run["status"]) != "ok" or not run["ended_at_utc"] or int(run["dry_run"]):
                            status = "source_run_invalid"
                        elif str(row["validation_status"]) != "valid" or snapshot.get("validation_status") != "valid":
                            status = "validation_failed"
                        elif int(snapshot.get("source_counts", {}).get("normalized_memory_items", 0)) <= 0:
                            status = "validation_failed"
                            errors.append("empty_snapshot")
                        elif str(run["schema_version"]) != SCHEMA_VERSION or str(run["runtime_version"]) != self.runtime_version:
                            status = "normalization_stale"
                        elif str(Path(run["source_db_path"]).resolve()) != str(self.source_db_path.resolve()):
                            status = "source_changed"
                            freshness = {
                                "reason": "source_path_changed",
                                "unknown_change_reason": True,
                                "invalidates_wake_state": True,
                                "logical_fingerprint_changed": False,
                            }
                        elif not self.source_db_path.exists():
                            status = "normalization_required"
                        else:
                            freshness = self._evaluate_freshness(run, deep_verify=deep_verify)
                            if freshness.get("reason") in {"fingerprint_contract_missing", "fingerprint_contract_changed"}:
                                status = "normalization_stale"
                            elif freshness.get("invalidates_wake_state"):
                                status = "source_changed"
                            else:
                                status = "ready"
                        if deep_verify and status == "ready":
                            source_integrity, source_fk_count, source_errors = _sqlite_checks(self.source_db_path)
                            sidecar_integrity, sidecar_fk_count, sidecar_errors = _sqlite_checks(self.sidecar_db_path)
                            errors.extend(source_errors + sidecar_errors)
                            if errors or source_integrity != "ok" or source_fk_count != 0 or sidecar_integrity != "ok" or sidecar_fk_count != 0:
                                status = "validation_failed"
                    else:
                        status = "snapshot_missing"
                return WakeStateStatus(
                    schema_version=WAKE_STATE_SCHEMA_VERSION,
                    sidecar_db_path=str(self.sidecar_db_path),
                    sidecar_db_exists=True,
                    sidecar_schema_present=schema_present,
                    active_snapshot_present=bool(active),
                    active_snapshot=active,
                    status=status,
                    deep_verify=deep_verify,
                    active_snapshot_count=active_count,
                    source_integrity_check=source_integrity,
                    source_foreign_key_error_count=source_fk_count,
                    sidecar_integrity_check=sidecar_integrity,
                    sidecar_foreign_key_error_count=sidecar_fk_count,
                    errors=errors,
                    freshness=freshness,
                )
        except Exception as exc:
            return WakeStateStatus(
                schema_version=WAKE_STATE_SCHEMA_VERSION,
                sidecar_db_path=str(self.sidecar_db_path),
                sidecar_db_exists=True,
                sidecar_schema_present=False,
                active_snapshot_present=False,
                active_snapshot={"error": repr(exc)},
                status="read_error",
                deep_verify=deep_verify,
                errors=[repr(exc)],
            )

    def build_wake_state(self, *, dry_run: bool = False) -> WakeStateBuildReport:
        if not dry_run:
            self.ensure_schema()
        status = self.status(deep_verify=True)
        if status.status not in {"ready"}:
            return WakeStateBuildReport(
                schema_version=WAKE_STATE_SCHEMA_VERSION,
                dry_run=dry_run,
                status=f"blocked:{status.status}",
                snapshot_id=None,
                snapshot_sha256=None,
                item_count=status.sidecar_counts.get("normalized_memory_items", 0),
                actor_count=status.sidecar_counts.get("actors", 0),
                snapshot=None,
                errors=[status.status],
            )
        with _connect_readonly(self.sidecar_db_path) as con:
            row = con.execute(
                "SELECT * FROM normalization_runs WHERE status='ok' AND ended_at_utc IS NOT NULL AND dry_run=0 "
                "ORDER BY started_at_utc DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return WakeStateBuildReport(
                    schema_version=WAKE_STATE_SCHEMA_VERSION, dry_run=dry_run, status="source_run_invalid",
                    snapshot_id=None, snapshot_sha256=None, item_count=0, actor_count=0, snapshot=None,
                    errors=["no valid normalization run"],
                )
            last_run_id = str(row["run_id"])
            snapshot = self._build_wake_snapshot(con, run_id=last_run_id)
            snapshot_raw = _json(snapshot)
            snapshot_sha = _hash_text(snapshot_raw)
            snapshot_summary = _snapshot_summary(
                snapshot, snapshot_id=None, snapshot_sha256=snapshot_sha, source_run_id=last_run_id
            )
            item_count = int(con.execute(
                "SELECT COUNT(*) FROM normalized_memory_items WHERE run_id=?", (last_run_id,)
            ).fetchone()[0])
            actor_count = _count_table(con, "actors")
        if snapshot.get("validation_status") != "valid" or item_count <= 0:
            return WakeStateBuildReport(
                schema_version=WAKE_STATE_SCHEMA_VERSION, dry_run=dry_run, status="validation_failed",
                snapshot_id=None, snapshot_sha256=snapshot_sha, item_count=item_count, actor_count=actor_count,
                snapshot=snapshot_summary, errors=["snapshot invalid or empty"],
            )
        if dry_run:
            return WakeStateBuildReport(
                schema_version=WAKE_STATE_SCHEMA_VERSION,
                dry_run=True,
                status="dry_run_ok",
                snapshot_id=None,
                snapshot_sha256=snapshot_sha,
                item_count=item_count,
                actor_count=actor_count,
                snapshot=snapshot_summary,
                errors=[],
            )
        self.ensure_schema()
        snapshot_id = str(uuid.uuid4())
        with _connect_write(self.sidecar_db_path) as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE wake_state_snapshots SET active=0 WHERE active=1")
            con.execute(
                """INSERT INTO wake_state_snapshots
                   (snapshot_id,schema_version,created_at_utc,active,source_run_id,snapshot_sha256,
                    snapshot_json,validation_status,truth_boundary)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot_id,
                    WAKE_STATE_SCHEMA_VERSION,
                    _now(),
                    1,
                    last_run_id,
                    snapshot_sha,
                    snapshot_raw,
                    snapshot["validation_status"],
                    TRUTH_BOUNDARY,
                ),
            )
            stored = con.execute(
                "SELECT snapshot_json,snapshot_sha256,source_run_id,validation_status "
                "FROM wake_state_snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
            active_count = int(con.execute(
                "SELECT COUNT(*) FROM wake_state_snapshots WHERE active=1"
            ).fetchone()[0])
            integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
            fk_count = sum(1 for _ in con.execute("PRAGMA foreign_key_check"))
            stored_valid = bool(
                stored
                and _hash_text(str(stored["snapshot_json"])) == str(stored["snapshot_sha256"])
                and str(stored["source_run_id"]) == last_run_id
                and str(stored["validation_status"]) == "valid"
                and active_count == 1
                and integrity == "ok"
                and fk_count == 0
            )
            if not stored_valid:
                con.rollback()
                return WakeStateBuildReport(
                    schema_version=WAKE_STATE_SCHEMA_VERSION, dry_run=False, status="validation_failed",
                    snapshot_id=None, snapshot_sha256=snapshot_sha, item_count=item_count, actor_count=actor_count,
                    snapshot=snapshot_summary,
                    errors=[f"readback/hash/source/integrity validation failed; active={active_count}, integrity={integrity}, fk={fk_count}"],
                )
            con.commit()
        report_summary = _snapshot_summary(
            snapshot,
            snapshot_id=snapshot_id,
            snapshot_sha256=snapshot_sha,
            source_run_id=last_run_id,
            validation_status=snapshot["validation_status"],
        )
        return WakeStateBuildReport(
            schema_version=WAKE_STATE_SCHEMA_VERSION,
            dry_run=False,
            status="ready",
            snapshot_id=snapshot_id,
            snapshot_sha256=snapshot_sha,
            item_count=item_count,
            actor_count=actor_count,
            snapshot=report_summary,
            errors=[],
        )

    def prepare(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
        deep_verify: bool = True,
    ) -> MemoryPrepareReport:
        if not self.source_db_path.exists():
            wake = self.wake_state_status(deep_verify=False).to_dict()
            return MemoryPrepareReport(
                schema_version=schema_version("memory_prepare"), dry_run=dry_run, force=force,
                deep_verify=deep_verify, status="source_missing", normalization_performed=False,
                snapshot_built=False, normalization=None, wake_state=wake,
                errors=[f"missing source db: {self.source_db_path}"],
            )

        source_integrity, source_fk_count, source_errors = _sqlite_checks(self.source_db_path)
        if source_errors or source_integrity != "ok" or source_fk_count != 0:
            wake = self.wake_state_status(deep_verify=False).to_dict()
            errors = source_errors or [
                f"integrity={source_integrity!r}, foreign_key_errors={source_fk_count!r}"
            ]
            return MemoryPrepareReport(
                schema_version=schema_version("memory_prepare"), dry_run=dry_run, force=force,
                deep_verify=deep_verify, status="validation_failed", normalization_performed=False,
                snapshot_built=False, normalization=None, wake_state=wake, errors=errors,
            )

        if dry_run:
            before = self.status(deep_verify=True)
            normalization = None
            if force or before.status != "ready":
                normalization = self.normalize(dry_run=True).to_dict()
            wake = self.wake_state_status(deep_verify=deep_verify).to_dict()
            hard_failures = {
                "source_missing", "validation_failed", "source_run_invalid",
                "snapshot_hash_mismatch", "read_error",
            }
            if before.status in hard_failures:
                planned_status = before.status
            elif wake.get("status") in hard_failures:
                planned_status = str(wake.get("status"))
            elif before.status == "ready" and wake.get("status") == "ready" and not force:
                planned_status = "ready"
            elif normalization and normalization.get("status") == "dry_run_ok":
                planned_status = "dry_run_ok"
            else:
                planned_status = before.status
            dry_errors = list((normalization or {}).get("errors") or []) + list(wake.get("errors") or [])
            return MemoryPrepareReport(
                schema_version=schema_version("memory_prepare"), dry_run=True, force=force,
                deep_verify=deep_verify, status=planned_status, normalization_performed=False,
                snapshot_built=False, normalization=normalization, wake_state=wake, errors=dry_errors,
            )

        self.ensure_schema()
        before = self.status(deep_verify=True)
        normalization_report: dict[str, Any] | None = None
        normalization_performed = bool(force or before.status != "ready")
        if normalization_performed:
            normalized = self.normalize(dry_run=False)
            normalization_report = normalized.to_dict()
            if normalized.status != "ok":
                wake = self.wake_state_status(deep_verify=False).to_dict()
                return MemoryPrepareReport(
                    schema_version=schema_version("memory_prepare"), dry_run=False, force=force,
                    deep_verify=deep_verify, status=normalized.status, normalization_performed=True,
                    snapshot_built=False, normalization=normalization_report, wake_state=wake,
                    errors=list(normalized.errors),
                )

        wake_before = self.wake_state_status(deep_verify=True)
        snapshot_built = wake_before.status != "ready" or normalization_performed
        build_report = None
        if snapshot_built:
            build_report = self.build_wake_state(dry_run=False)
            if build_report.status != "ready":
                return MemoryPrepareReport(
                    schema_version=schema_version("memory_prepare"), dry_run=False, force=force,
                    deep_verify=deep_verify, status=build_report.status,
                    normalization_performed=normalization_performed, snapshot_built=True,
                    normalization=normalization_report, wake_state=wake_before.to_dict(),
                    errors=list(build_report.errors),
                )
        final_wake = self.wake_state_status(deep_verify=deep_verify)
        errors = list(final_wake.errors or [])
        return MemoryPrepareReport(
            schema_version=schema_version("memory_prepare"), dry_run=False, force=force,
            deep_verify=deep_verify, status=final_wake.status,
            normalization_performed=normalization_performed, snapshot_built=snapshot_built,
            normalization=normalization_report, wake_state=final_wake.to_dict(), errors=errors,
        )

    def build_layered_dedupe(self, *, dry_run: bool = False, min_group_size: int = 2) -> LayeredDedupeReport:
        status = self.status()
        if status.status != "ready":
            return LayeredDedupeReport(
                schema_version=LAYERED_DEDUPE_SCHEMA_VERSION,
                run_id=None,
                dry_run=dry_run,
                status=f"blocked:{status.status}",
                source_db_path=str(self.source_db_path),
                sidecar_db_path=str(self.sidecar_db_path),
                input_counts={},
                layer_counts={},
                errors=[status.status],
            )

        min_group_size = max(2, int(min_group_size or 2))
        errors: list[str] = []
        with _connect_readonly(self.source_db_path) as source, _connect_readonly(self.sidecar_db_path) as side:
            source_index = self._source_text_context_index(source)
            candidates = self._dedupe_candidates(side, source_index)
        groups_by_layer = self._layered_dedupe_groups(candidates, min_group_size=min_group_size)
        layer_counts = self._layer_counts(groups_by_layer)
        input_counts = {
            "candidate_items": len(candidates),
            "full_text_fallback_items": sum(1 for item in candidates if item.get("full_text_source") == "sidecar_excerpt_fallback"),
            "min_group_size": min_group_size,
        }
        if dry_run:
            return LayeredDedupeReport(
                schema_version=LAYERED_DEDUPE_SCHEMA_VERSION,
                run_id=None,
                dry_run=True,
                status="dry_run_ok",
                source_db_path=str(self.source_db_path),
                sidecar_db_path=str(self.sidecar_db_path),
                input_counts=input_counts,
                layer_counts=layer_counts,
                errors=[],
            )

        self.ensure_schema()
        run_id = str(uuid.uuid4())
        started = _now()
        criteria = {
            "layers": {
                "exact_text": "same whitespace-collapsed full text",
                "typed_text": "exact_text + memory_type + source_table + speaker_actor_id",
                "contextual_safe": (
                    "typed_text + conversation_id/context before/context after when available + "
                    "memory_namespace + truth_status + importance/confidence buckets"
                ),
            },
            "deletion_policy": "no source rows are deleted",
            "representative_policy": "highest importance, then confidence, then earliest timestamp, then item_id",
        }
        with _connect_write(self.sidecar_db_path) as con:
            con.execute(
                """INSERT INTO layered_dedupe_runs
                   (run_id,schema_version,runtime_version,started_at_utc,mode,source_db_path,sidecar_db_path,
                    candidate_count,full_text_fallback_count,layer_counts_json,criteria_json,status,errors_json,dry_run,truth_boundary)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    LAYERED_DEDUPE_SCHEMA_VERSION,
                    self.runtime_version,
                    started,
                    "layered_dedupe_mark_only",
                    str(self.source_db_path),
                    str(self.sidecar_db_path),
                    input_counts["candidate_items"],
                    input_counts["full_text_fallback_items"],
                    _json(layer_counts),
                    _json(criteria),
                    "running",
                    "[]",
                    0,
                    DEDUPE_TRUTH_BOUNDARY,
                ),
            )
            now = _now()
            for layer, groups in groups_by_layer.items():
                for group in groups:
                    group_id = _hash_text("|".join([run_id, layer, group["group_key_hash"]]))
                    representative = group["representative"]
                    timestamps = sorted([str(item.get("source_timestamp") or "") for item in group["members"] if item.get("source_timestamp")])
                    con.execute(
                        """INSERT INTO layered_dedupe_groups
                           (group_id,run_id,layer,group_key_hash,representative_item_id,member_count,redundant_count,
                            memory_type,source_table,speaker_actor_id,memory_namespace,truth_status,context_policy,
                            text_hash,literal_text_hash,context_hash,metadata_hash,max_importance,max_confidence,
                            first_timestamp,last_timestamp,evidence_json,created_at_utc,truth_boundary)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            group_id,
                            run_id,
                            layer,
                            group["group_key_hash"],
                            representative["item_id"],
                            len(group["members"]),
                            len(group["members"]) - 1,
                            representative.get("memory_type"),
                            representative.get("source_table"),
                            representative.get("speaker_actor_id"),
                            representative.get("memory_namespace"),
                            representative.get("truth_status"),
                            group["context_policy"],
                            representative["text_hash"],
                            representative["literal_text_hash"],
                            representative.get("context_hash"),
                            representative.get("metadata_hash"),
                            max(_safe_float(item.get("importance")) for item in group["members"]),
                            max(_safe_float(item.get("confidence")) for item in group["members"]),
                            timestamps[0] if timestamps else None,
                            timestamps[-1] if timestamps else None,
                            _json(group["evidence"]),
                            now,
                            DEDUPE_TRUTH_BOUNDARY,
                        ),
                    )
                    for item in group["members"]:
                        con.execute(
                            """INSERT INTO layered_dedupe_members
                               (group_id,item_id,role_in_group,source_table,source_row_id,conversation_id,message_id,
                                memory_type,source_timestamp,importance,confidence,text_hash,literal_text_hash,
                                context_before_hash,context_after_hash,context_hash,metadata_hash,evidence_json,created_at_utc)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                group_id,
                                item["item_id"],
                                "representative" if item["item_id"] == representative["item_id"] else "duplicate_member",
                                item["source_table"],
                                item.get("source_row_id"),
                                item.get("conversation_id"),
                                item.get("message_id"),
                                item["memory_type"],
                                item.get("source_timestamp"),
                                _safe_float(item.get("importance")),
                                _safe_float(item.get("confidence")),
                                item["text_hash"],
                                item["literal_text_hash"],
                                item.get("context_before_hash"),
                                item.get("context_after_hash"),
                                item.get("context_hash"),
                                item.get("metadata_hash"),
                                _json({"full_text_source": item.get("full_text_source"), "privacy_scope": item.get("privacy_scope")}),
                                now,
                            ),
                        )
            final_status = "ok" if not errors else "completed_with_warnings"
            con.execute(
                "UPDATE layered_dedupe_runs SET ended_at_utc=?, status=?, errors_json=?, layer_counts_json=? WHERE run_id=?",
                (_now(), final_status, _json(errors), _json(layer_counts), run_id),
            )
            con.commit()
        return LayeredDedupeReport(
            schema_version=LAYERED_DEDUPE_SCHEMA_VERSION,
            run_id=run_id,
            dry_run=False,
            status=final_status,
            source_db_path=str(self.source_db_path),
            sidecar_db_path=str(self.sidecar_db_path),
            input_counts=input_counts,
            layer_counts=layer_counts,
            errors=errors,
        )

    def _source_text_context_index(self, source: sqlite3.Connection) -> dict[str, dict[str, dict[str, Any]]]:
        index: dict[str, dict[str, dict[str, Any]]] = {}

        if _table_exists(source, "messages"):
            message_rows = [
                dict(row)
                for row in source.execute(
                    """SELECT rowid, conversation_id, message_id, role, timestamp, content_text
                         FROM messages
                        WHERE role IN ('user','assistant')
                          AND COALESCE(content_text,'') <> ''
                        ORDER BY conversation_id, COALESCE(timestamp,''), rowid"""
                )
            ]
            by_conversation: dict[str, list[dict[str, Any]]] = {}
            for row in message_rows:
                by_conversation.setdefault(str(row.get("conversation_id") or ""), []).append(row)
            message_index: dict[str, dict[str, Any]] = {}
            for rows in by_conversation.values():
                word_hashes = [_hash_text(_dedupe_text(row["content_text"])) for row in rows]
                literal_hashes = [_hash_text(row["content_text"] or "") for row in rows]
                for pos, row in enumerate(rows):
                    before_hash = word_hashes[pos - 1] if pos > 0 else ""
                    after_hash = word_hashes[pos + 1] if pos + 1 < len(rows) else ""
                    message_index[str(row["rowid"])] = {
                        "literal_text_hash": literal_hashes[pos],
                        "text_hash": word_hashes[pos],
                        "context_before_hash": before_hash,
                        "context_after_hash": after_hash,
                        "context_hash": _hash_text("|".join([before_hash, after_hash])),
                        "full_text_source": "source.messages.content_text",
                    }
            index["messages"] = message_index

        table_specs: list[tuple[str, str, Any]] = [
            ("legacy_chunks", "SELECT legacy_chunk_id AS source_row_id, content_text FROM legacy_chunks WHERE COALESCE(content_text,'') <> ''", lambda r: r["content_text"]),
            ("episodic_memories", "SELECT rowid AS source_row_id, scene FROM episodic_memories WHERE COALESCE(scene,'') <> ''", lambda r: r["scene"]),
            ("semantic_facts", "SELECT rowid AS source_row_id, subject, predicate, value FROM semantic_facts", lambda r: f"{r['subject']} {r['predicate']} {r['value']}"),
            ("procedural_rules", "SELECT rowid AS source_row_id, trigger, action, reason FROM procedural_rules", lambda r: f"Trigger: {r['trigger']}. Action: {r['action']}. Reason: {r['reason']}"),
            ("reflection_entries", "SELECT rowid AS source_row_id, meaning_for_latka, identity_impact, boundary_note FROM reflection_entries", lambda r: f"{r['meaning_for_latka']} {r['identity_impact']} {r['boundary_note']}"),
            ("truth_audits", "SELECT rowid AS source_row_id, text FROM truth_audits WHERE COALESCE(text,'') <> ''", lambda r: r["text"]),
        ]
        for table, sql, builder in table_specs:
            if not _table_exists(source, table):
                continue
            table_index: dict[str, dict[str, Any]] = {}
            for row in source.execute(sql):
                text = str(builder(row) or "")
                text_hash = _hash_text(_dedupe_text(text))
                table_index[str(row["source_row_id"])] = {
                    "literal_text_hash": _hash_text(text),
                    "text_hash": text_hash,
                    "context_before_hash": "",
                    "context_after_hash": "",
                    "context_hash": "non_dialogue_context",
                    "full_text_source": f"source.{table}",
                }
            index[table] = table_index
        return index

    def _dedupe_candidates(
        self,
        sidecar: sqlite3.Connection,
        source_index: dict[str, dict[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        if not _table_exists(sidecar, "normalized_memory_items"):
            return candidates
        rows = sidecar.execute(
            """SELECT item_id,memory_type,source_table,source_row_id,conversation_id,message_id,
                      speaker_actor_id,interlocutor_actor_id,source_timestamp,content_excerpt,
                      confidence,importance,privacy_scope,memory_namespace,truth_status
                 FROM normalized_memory_items
                WHERE COALESCE(content_excerpt,'') <> ''"""
        )
        for row in rows:
            source_table = str(row["source_table"] or "")
            source_row_id = str(row["source_row_id"] or "")
            text_info = source_index.get(source_table, {}).get(source_row_id)
            if not text_info:
                text = str(row["content_excerpt"] or "")
                text_info = {
                    "literal_text_hash": _hash_text(text),
                    "text_hash": _hash_text(_dedupe_text(text)),
                    "context_before_hash": "",
                    "context_after_hash": "",
                    "context_hash": "sidecar_excerpt_context_unknown",
                    "full_text_source": "sidecar_excerpt_fallback",
                }
            importance_bucket = _bucket_float(row["importance"])
            confidence_bucket = _bucket_float(row["confidence"])
            metadata_hash = _hash_text("|".join([
                str(row["memory_namespace"] or ""),
                str(row["truth_status"] or ""),
                str(row["privacy_scope"] or ""),
                importance_bucket,
                confidence_bucket,
            ]))
            candidates.append({
                "item_id": row["item_id"],
                "memory_type": row["memory_type"],
                "source_table": source_table,
                "source_row_id": source_row_id,
                "conversation_id": row["conversation_id"],
                "message_id": row["message_id"],
                "speaker_actor_id": row["speaker_actor_id"],
                "interlocutor_actor_id": row["interlocutor_actor_id"],
                "source_timestamp": row["source_timestamp"],
                "confidence": _safe_float(row["confidence"]),
                "importance": _safe_float(row["importance"]),
                "privacy_scope": row["privacy_scope"],
                "memory_namespace": row["memory_namespace"],
                "truth_status": row["truth_status"],
                "literal_text_hash": text_info["literal_text_hash"],
                "text_hash": text_info["text_hash"],
                "context_before_hash": text_info["context_before_hash"],
                "context_after_hash": text_info["context_after_hash"],
                "context_hash": text_info["context_hash"],
                "metadata_hash": metadata_hash,
                "full_text_source": text_info["full_text_source"],
                "importance_bucket": importance_bucket,
                "confidence_bucket": confidence_bucket,
            })
        return candidates

    def _layered_dedupe_groups(
        self,
        candidates: list[dict[str, Any]],
        *,
        min_group_size: int,
    ) -> dict[str, list[dict[str, Any]]]:
        layer_keys = {
            "exact_text": lambda item: "|".join(["exact_text", item["text_hash"]]),
            "typed_text": lambda item: "|".join([
                "typed_text",
                str(item.get("memory_type") or ""),
                str(item.get("source_table") or ""),
                str(item.get("speaker_actor_id") or ""),
                item["text_hash"],
            ]),
            "contextual_safe": lambda item: "|".join([
                "contextual_safe",
                str(item.get("memory_type") or ""),
                str(item.get("source_table") or ""),
                str(item.get("speaker_actor_id") or ""),
                str(item.get("conversation_id") or ""),
                str(item.get("memory_namespace") or ""),
                str(item.get("truth_status") or ""),
                str(item.get("importance_bucket") or ""),
                str(item.get("confidence_bucket") or ""),
                str(item.get("context_hash") or ""),
                item["text_hash"],
            ]),
        }
        context_policy = {
            "exact_text": "same full text after whitespace collapse; context preserved only as member metadata",
            "typed_text": "same full text plus memory type/source/speaker; context preserved only as member metadata",
            "contextual_safe": "same text/type/source/speaker/namespace/truth/weight bucket plus same conversation context",
        }
        grouped: dict[str, list[dict[str, Any]]] = {}
        for layer, key_fn in layer_keys.items():
            buckets: dict[str, list[dict[str, Any]]] = {}
            for item in candidates:
                key_raw = key_fn(item)
                buckets.setdefault(_hash_text(key_raw), []).append(item)
            groups: list[dict[str, Any]] = []
            for group_key_hash, members in buckets.items():
                if len(members) < min_group_size:
                    continue
                representative = sorted(
                    members,
                    key=lambda item: (
                        -_safe_float(item.get("importance")),
                        -_safe_float(item.get("confidence")),
                        str(item.get("source_timestamp") or "9999"),
                        str(item.get("item_id") or ""),
                    ),
                )[0]
                groups.append({
                    "group_key_hash": group_key_hash,
                    "members": members,
                    "representative": representative,
                    "context_policy": context_policy[layer],
                    "evidence": {
                        "layer": layer,
                        "distinct_conversation_ids": len({str(item.get("conversation_id") or "") for item in members}),
                        "distinct_source_tables": len({str(item.get("source_table") or "") for item in members}),
                        "distinct_memory_types": len({str(item.get("memory_type") or "") for item in members}),
                        "full_text_sources": sorted({str(item.get("full_text_source") or "") for item in members}),
                    },
                })
            groups.sort(key=lambda group: (-len(group["members"]), group["group_key_hash"]))
            grouped[layer] = groups
        return grouped

    def _layer_counts(self, groups_by_layer: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for layer, groups in groups_by_layer.items():
            member_count = sum(len(group["members"]) for group in groups)
            counts[layer] = {
                "duplicate_groups": len(groups),
                "members_in_duplicate_groups": member_count,
                "redundant_members": sum(max(0, len(group["members"]) - 1) for group in groups),
                "largest_group": max((len(group["members"]) for group in groups), default=0),
            }
        return counts

    def _source_counts(self, *, immutable: bool = False) -> dict[str, int]:
        if not self.source_db_path.exists():
            return {}
        counts: dict[str, int] = {}
        try:
            with _connect_readonly(self.source_db_path, immutable=immutable) as con:
                for table in (
                    "messages",
                    "messages_user_assistant",
                    "active_conversation_messages",
                    "message_sources",
                    "legacy_chunks",
                    "episodic_memories",
                    "semantic_facts",
                    "procedural_rules",
                    "reflection_entries",
                    "truth_audits",
                    "journal",
                ):
                    counts[table] = _count_table(con, table)
        except Exception as exc:
            counts["read_error"] = 1
            counts["read_error_repr_hash"] = int(_hash_text(repr(exc))[:8], 16)
        return counts

    def _estimated_output_counts(self, input_counts: dict[str, int], *, limit: int | None) -> dict[str, int]:
        total = (
            (input_counts.get("messages_user_assistant", 0) or input_counts.get("messages", 0))
            + input_counts.get("legacy_chunks", 0)
            + input_counts.get("episodic_memories", 0)
            + input_counts.get("semantic_facts", 0)
            + input_counts.get("procedural_rules", 0)
            + input_counts.get("reflection_entries", 0)
            + input_counts.get("truth_audits", 0)
            + input_counts.get("journal", 0)
        )
        if limit is not None:
            total = min(total, max(0, int(limit)))
        return {"actors": 3, "normalized_memory_items": total}

    def _upsert_default_actors(self, con: sqlite3.Connection) -> int:
        now = _now()
        rows = [
            ("latka", "Łatka", "jazn_runtime_identity", 1.0, "jazn_core", {"source": "runtime_identity_contract"}),
            ("interlocutor_unknown", "Niepotwierdzony rozmówca", "unknown_interlocutor", 0.2, "public_or_unknown", {"source": "role_without_identity_proof"}),
            ("krzysztof_candidate", "Krzysztof", "candidate_interlocutor", 0.75, "krzysztof_candidate_locked", {"source": "explicit_name_in_curated_memory_only"}),
        ]
        for actor_id, name, actor_type, confidence, namespace, evidence in rows:
            con.execute(
                """INSERT INTO actors(actor_id,display_name,actor_type,identity_confidence,privacy_namespace,
                                      source_evidence_json,created_at_utc,updated_at_utc)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(actor_id) DO UPDATE SET
                     display_name=excluded.display_name,
                     actor_type=excluded.actor_type,
                     identity_confidence=excluded.identity_confidence,
                     privacy_namespace=excluded.privacy_namespace,
                     source_evidence_json=excluded.source_evidence_json,
                     updated_at_utc=excluded.updated_at_utc""",
                (actor_id, name, actor_type, confidence, namespace, _json(evidence), now, now),
            )
        return len(rows)

    def _iter_normalized_items(
        self,
        source: sqlite3.Connection,
        *,
        run_id: str,
        limit: int | None,
    ) -> Iterable[dict[str, Any]]:
        emitted = 0
        # Curated/source-labelled records are normalized before raw dialogue. The
        # complete dialogue remains searchable in L0; a bounded sidecar limit must
        # not starve procedural, semantic, truth-boundary, or journal material.
        for iterator in (
            self._iter_procedural_items,
            self._iter_semantic_items,
            self._iter_episodic_items,
            self._iter_reflection_items,
            self._iter_truth_audit_items,
            self._iter_journal_items,
            self._iter_legacy_chunk_items,
        ):
            for item in iterator(source, run_id):
                yield item
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
        for item in self._iter_message_items(source, run_id):
            yield item
            emitted += 1
            if limit is not None and emitted >= limit:
                return

    def _base_item(
        self,
        *,
        memory_type: str,
        source_table: str,
        source_row_id: str | None,
        content: str,
        run_id: str,
        speaker_actor_id: str = "latka",
        interlocutor_actor_id: str = "interlocutor_unknown",
        participants: list[str] | None = None,
        source_timestamp: str | None = None,
        source_timestamp_confidence: float = 0.0,
        source_file: str | None = None,
        source_sha256: str | None = None,
        source_refs: list[Any] | None = None,
        source_conversation_title: str | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        grounding: str = "sqlite_index",
        truth_status: str = "source_recorded",
        confidence: float = 0.68,
        importance: float = 0.45,
        privacy_scope: str = "conversation_private_unverified",
        memory_namespace: str = "dialogue_general_unverified",
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        excerpt = _excerpt(content)
        content_hash = _hash_text(excerpt)
        dedupe_key = _hash_text("|".join([
            memory_type,
            source_table,
            source_row_id or "",
            conversation_id or "",
            message_id or "",
            content_hash,
            source_sha256 or "",
        ]))
        now = _now()
        return {
            "item_id": dedupe_key,
            "memory_type": memory_type,
            "source_table": source_table,
            "source_row_id": source_row_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "speaker_actor_id": speaker_actor_id,
            "interlocutor_actor_id": interlocutor_actor_id,
            "participants_json": _json(participants or [speaker_actor_id, interlocutor_actor_id]),
            "source_timestamp": source_timestamp,
            "source_timestamp_confidence": source_timestamp_confidence,
            "source_file": source_file,
            "source_sha256": source_sha256,
            "source_refs_json": _json(source_refs or []),
            "source_conversation_title": source_conversation_title,
            "content_excerpt": excerpt,
            "content_hash": content_hash,
            "grounding": grounding,
            "truth_status": truth_status,
            "confidence": confidence,
            "importance": importance,
            "privacy_scope": privacy_scope,
            "memory_namespace": memory_namespace,
            "dedupe_key": dedupe_key,
            "source_evidence_json": _json(evidence or {}),
            "created_at_utc": now,
            "updated_at_utc": now,
            "run_id": run_id,
        }

    def _iter_message_items(self, con: sqlite3.Connection, run_id: str) -> Iterable[dict[str, Any]]:
        if not _table_exists(con, "messages"):
            return
        sql = """
            SELECT rowid, message_id, conversation_id, conversation_title, role, timestamp,
                   content_text, content_hash, first_source_file, first_source_sha256, source_refs_json
              FROM messages
             WHERE role IN ('user','assistant')
               AND COALESCE(content_text,'') <> ''
             ORDER BY COALESCE(timestamp, created_at, updated_at) DESC, rowid DESC
        """
        for row in con.execute(sql):
            role = row["role"] or "unknown"
            speaker = "latka" if role == "assistant" else "interlocutor_unknown"
            interlocutor = "interlocutor_unknown" if role == "assistant" else "latka"
            yield self._base_item(
                memory_type=f"conversation_{role}_message",
                source_table="messages",
                source_row_id=str(row["rowid"]),
                content=row["content_text"],
                run_id=run_id,
                speaker_actor_id=speaker,
                interlocutor_actor_id=interlocutor,
                participants=["latka", "interlocutor_unknown"],
                source_timestamp=row["timestamp"],
                source_timestamp_confidence=0.82 if row["timestamp"] else 0.0,
                source_file=row["first_source_file"],
                source_sha256=row["first_source_sha256"],
                source_refs=_load_json_list(row["source_refs_json"]),
                source_conversation_title=row["conversation_title"],
                conversation_id=row["conversation_id"],
                message_id=row["message_id"],
                grounding="sqlite_message_index",
                truth_status="source_recorded",
                confidence=0.74,
                importance=0.55 if role == "user" else 0.46,
                privacy_scope="conversation_private_unverified",
                memory_namespace="dialogue_general_unverified",
                evidence={"role": role, "content_hash": row["content_hash"]},
            )

    def _iter_journal_items(self, con: sqlite3.Connection, run_id: str) -> Iterable[dict[str, Any]]:
        if not _table_exists(con, "journal"):
            return
        for row in con.execute("SELECT rowid, * FROM journal ORDER BY created_at_utc, journal_id"):
            text = str(row["text"] or "")
            if not text.strip():
                continue
            yield self._base_item(
                memory_type="legacy_journal_entry",
                source_table="journal",
                source_row_id=str(row["journal_id"] or row["rowid"]),
                content=text,
                run_id=run_id,
                source_timestamp=row["created_at_utc"],
                source_timestamp_confidence=0.78,
                grounding="recovered_journal_source",
                truth_status="source_recorded_pending_review",
                confidence=0.62,
                importance=0.55,
                privacy_scope="legacy_private_pending_review",
                memory_namespace="legacy_journal_pending_review",
                evidence={"kind": row["kind"], "payload_json": row["payload_json"]},
            )

    def _iter_legacy_chunk_items(self, con: sqlite3.Connection, run_id: str) -> Iterable[dict[str, Any]]:
        if not _table_exists(con, "legacy_chunks"):
            return
        sql = """
            SELECT legacy_chunk_id, source_sha256, source_file, source_rel_path, chunk_index,
                   page_start, page_end, content_text, inferred_date
              FROM legacy_chunks
             WHERE COALESCE(content_text,'') <> ''
             ORDER BY source_file, chunk_index
        """
        for row in con.execute(sql):
            yield self._base_item(
                memory_type="legacy_chunk",
                source_table="legacy_chunks",
                source_row_id=str(row["legacy_chunk_id"]),
                content=row["content_text"],
                run_id=run_id,
                speaker_actor_id="interlocutor_unknown",
                interlocutor_actor_id="latka",
                participants=["latka", "interlocutor_unknown"],
                source_timestamp=row["inferred_date"],
                source_timestamp_confidence=0.35 if row["inferred_date"] else 0.0,
                source_file=row["source_file"],
                source_sha256=row["source_sha256"],
                grounding="legacy_chunk_index",
                truth_status="recovered_from_legacy_source",
                confidence=0.56,
                importance=0.35,
                privacy_scope="legacy_private_unverified",
                memory_namespace="legacy_recovered_unverified",
                evidence={"source_rel_path": row["source_rel_path"], "chunk_index": row["chunk_index"], "page_start": row["page_start"], "page_end": row["page_end"]},
            )

    def _iter_episodic_items(self, con: sqlite3.Connection, run_id: str) -> Iterable[dict[str, Any]]:
        if not _table_exists(con, "episodic_memories"):
            return
        for row in con.execute("SELECT rowid, * FROM episodic_memories ORDER BY created_at_utc"):
            participants_raw = _load_json_list(row["participants_json"])
            has_krzysztof = any("krzysztof" in str(x).lower() for x in participants_raw)
            participants = ["latka", "krzysztof_candidate"] if has_krzysztof else ["latka", "interlocutor_unknown"]
            yield self._base_item(
                memory_type="episodic_memory",
                source_table="episodic_memories",
                source_row_id=str(row["rowid"]),
                content=row["scene"],
                run_id=run_id,
                speaker_actor_id="latka",
                interlocutor_actor_id="krzysztof_candidate" if has_krzysztof else "interlocutor_unknown",
                participants=participants,
                source_timestamp=row["created_at_utc"],
                source_timestamp_confidence=0.9,
                grounding=row["grounding"] or "curated_episodic_memory",
                truth_status="curated_memory_record",
                confidence=float(row["confidence"] or 0.0),
                importance=0.72,
                privacy_scope="relationship_private_candidate" if has_krzysztof else "conversation_private_unverified",
                memory_namespace="relationship_candidate_unconfirmed" if has_krzysztof else "episodic_general",
                evidence={"source": row["source"], "tags_json": row["tags_json"], "raw_excerpt": row["raw_excerpt"]},
            )

    def _iter_semantic_items(self, con: sqlite3.Connection, run_id: str) -> Iterable[dict[str, Any]]:
        if not _table_exists(con, "semantic_facts"):
            return
        for row in con.execute("SELECT rowid, * FROM semantic_facts ORDER BY created_at_utc"):
            content = f"{row['subject']} {row['predicate']} {row['value']}"
            yield self._base_item(
                memory_type="semantic_fact",
                source_table="semantic_facts",
                source_row_id=str(row["rowid"]),
                content=content,
                run_id=run_id,
                source_timestamp=row["created_at_utc"],
                source_timestamp_confidence=0.9,
                grounding="curated_semantic_fact",
                truth_status="semantic_claim_with_confidence",
                confidence=float(row["confidence"] or 0.0),
                importance=0.62,
                privacy_scope="semantic_private_unverified",
                memory_namespace="semantic_general",
                evidence={"source": row["source"], "tags_json": row["tags_json"]},
            )

    def _iter_procedural_items(self, con: sqlite3.Connection, run_id: str) -> Iterable[dict[str, Any]]:
        if not _table_exists(con, "procedural_rules"):
            return
        for row in con.execute("SELECT rowid, * FROM procedural_rules ORDER BY priority DESC, created_at_utc"):
            content = f"Trigger: {row['trigger']}. Action: {row['action']}. Reason: {row['reason']}"
            yield self._base_item(
                memory_type="procedural_rule",
                source_table="procedural_rules",
                source_row_id=str(row["rowid"]),
                content=content,
                run_id=run_id,
                source_timestamp=row["created_at_utc"],
                source_timestamp_confidence=0.9,
                grounding="curated_procedural_rule",
                truth_status="runtime_rule_record",
                confidence=0.84,
                importance=min(0.95, 0.4 + (float(row["priority"] or 0) / 120.0)),
                privacy_scope="procedural_runtime",
                memory_namespace="procedural_rules",
                evidence={"source": row["source"], "priority": row["priority"]},
            )

    def _iter_reflection_items(self, con: sqlite3.Connection, run_id: str) -> Iterable[dict[str, Any]]:
        if not _table_exists(con, "reflection_entries"):
            return
        for row in con.execute("SELECT rowid, * FROM reflection_entries ORDER BY created_at_utc"):
            content = f"{row['meaning_for_latka']} {row['identity_impact']} {row['boundary_note']}"
            yield self._base_item(
                memory_type="reflection_entry",
                source_table="reflection_entries",
                source_row_id=str(row["rowid"]),
                content=content,
                run_id=run_id,
                source_timestamp=row["created_at_utc"],
                source_timestamp_confidence=0.9,
                grounding="curated_reflection",
                truth_status="reflection_record",
                confidence=float(row["confidence"] or 0.0),
                importance=0.66,
                privacy_scope="reflection_private_unverified",
                memory_namespace="reflections",
                evidence={"episode_id": row["episode_id"], "next_question": row["next_question"]},
            )

    def _iter_truth_audit_items(self, con: sqlite3.Connection, run_id: str) -> Iterable[dict[str, Any]]:
        if not _table_exists(con, "truth_audits"):
            return
        for row in con.execute("SELECT rowid, * FROM truth_audits ORDER BY created_at_utc"):
            yield self._base_item(
                memory_type="truth_audit",
                source_table="truth_audits",
                source_row_id=str(row["rowid"]),
                content=row["text"],
                run_id=run_id,
                source_timestamp=row["created_at_utc"],
                source_timestamp_confidence=0.9,
                grounding="truth_audit",
                truth_status="truth_boundary_record",
                confidence=0.86,
                importance=0.7,
                privacy_scope="truth_boundary_runtime",
                memory_namespace="truth_audits",
                evidence={"audit_json": row["audit_json"]},
            )

    def _insert_item(self, con: sqlite3.Connection, item: dict[str, Any]) -> None:
        keys = [
            "item_id", "memory_type", "source_table", "source_row_id", "conversation_id", "message_id",
            "speaker_actor_id", "interlocutor_actor_id", "participants_json", "source_timestamp",
            "source_timestamp_confidence", "source_file", "source_sha256", "source_refs_json",
            "source_conversation_title", "content_excerpt", "content_hash", "grounding", "truth_status",
            "confidence", "importance", "privacy_scope", "memory_namespace", "dedupe_key",
            "source_evidence_json", "created_at_utc", "updated_at_utc", "run_id",
        ]
        placeholders = ",".join("?" for _ in keys)
        update_columns = [key for key in keys if key not in {"item_id", "dedupe_key", "created_at_utc"}]
        updates = ",".join(f"{key}=excluded.{key}" for key in update_columns)
        con.execute(
            f"INSERT INTO normalized_memory_items({','.join(keys)}) VALUES({placeholders}) "
            f"ON CONFLICT(dedupe_key) DO UPDATE SET {updates}",
            tuple(item.get(k) for k in keys),
        )

    def _build_wake_snapshot(self, con: sqlite3.Connection, *, run_id: str) -> dict[str, Any]:
        namespace_counts = {
            row["memory_namespace"]: int(row["c"])
            for row in con.execute(
                "SELECT memory_namespace, COUNT(*) c FROM normalized_memory_items WHERE run_id=? "
                "GROUP BY memory_namespace ORDER BY c DESC",
                (run_id,),
            )
        }
        truth_counts = {
            row["truth_status"]: int(row["c"])
            for row in con.execute(
                "SELECT truth_status, COUNT(*) c FROM normalized_memory_items WHERE run_id=? "
                "GROUP BY truth_status ORDER BY c DESC",
                (run_id,),
            )
        }
        actors = [dict(row) for row in con.execute("SELECT actor_id, display_name, actor_type, identity_confidence, privacy_namespace FROM actors ORDER BY actor_id")]
        recent_rows = con.execute(
            """SELECT memory_type, source_timestamp, source_conversation_title, memory_namespace,
                      truth_status, content_excerpt
                 FROM normalized_memory_items
                 WHERE source_timestamp IS NOT NULL AND run_id=?
                 ORDER BY source_timestamp DESC
                 LIMIT 12""",
            (run_id,),
        ).fetchall()
        recent_events = [
            {
                "memory_type": row["memory_type"],
                "source_timestamp": row["source_timestamp"],
                "title": row["source_conversation_title"],
                "namespace": row["memory_namespace"],
                "truth_status": row["truth_status"],
                "excerpt": _excerpt(row["content_excerpt"], limit=260),
            }
            for row in recent_rows
        ]
        procedural_rows = con.execute(
            """SELECT content_excerpt, importance
                 FROM normalized_memory_items
                 WHERE memory_namespace='procedural_rules' AND run_id=?
                 ORDER BY importance DESC, source_timestamp DESC
                 LIMIT 8""",
            (run_id,),
        ).fetchall()
        procedural_rules = [_excerpt(row["content_excerpt"], limit=240) for row in procedural_rows]
        krzysztof_actor = next((a for a in actors if a["actor_id"] == "krzysztof_candidate"), None)
        krzysztof_private_allowed = bool(krzysztof_actor and float(krzysztof_actor["identity_confidence"]) >= 0.85)
        item_count = int(con.execute(
            "SELECT COUNT(*) FROM normalized_memory_items WHERE run_id=?", (run_id,)
        ).fetchone()[0])
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        fk_count = len(con.execute("PRAGMA foreign_key_check").fetchall())
        validation_status = "valid" if integrity == "ok" and fk_count == 0 and item_count > 0 else "invalid_or_empty"
        return {
            "schema_version": WAKE_STATE_SCHEMA_VERSION,
            "created_at_utc": _now(),
            "identity_snapshot": {
                "active_identity": "Łatka",
                "source": "actors + procedural_rules + normalized_memory_items",
                "runtime_role": "active_source_runtime_memory_identity_truth_boundary",
                "truth_boundary": "To operacyjny snapshot startowy, nie dowód biologicznej ani fenomenalnej świadomości.",
            },
            "truth_boundary_digest": {
                "must_not_claim_background_process": True,
                "must_not_claim_memory_without_source": True,
                "emotions_are_modelled_operational_relational": True,
                "source_truth_counts": truth_counts,
            },
            "relationship_digest": {
                "krzysztof_candidate_present": krzysztof_actor is not None,
                "krzysztof_private_namespace_allowed": krzysztof_private_allowed,
                "rule": "Prywatny namespace Krzysztofa wymaga identity_confidence >= 0.85; kandydat 0.75 nie wystarcza do ujawniania prywatnych symboli.",
            },
            "recent_events": recent_events,
            "open_threads": procedural_rules,
            "namespace_policy": {
                "namespace_counts": namespace_counts,
                "default_for_unknown_interlocutor": "dialogue_general_unverified",
                "private_namespace_requires_confirmed_actor": True,
            },
            "source_counts": {
                "normalized_memory_items": item_count,
                "actors": len(actors),
            },
            "source_run_id": run_id,
            "validation_status": validation_status,
            "validation": {
                "sidecar_integrity_check": integrity,
                "sidecar_foreign_key_error_count": fk_count,
            },
        }


def build_memory_normalization_status(
    config: JaznConfig | None = None, *, deep_verify: bool = False
) -> MemoryNormalizationStatus:
    cfg = config or JaznConfig()
    return MemoryNormalizationSidecar(
        cfg.root,
        source_db_path=cfg.normalization_source_db_path,
        sidecar_db_path=cfg.normalization_sidecar_db_path,
        runtime_version=cfg.version,
    ).status(deep_verify=deep_verify)


def build_wake_state_status(
    config: JaznConfig | None = None, *, deep_verify: bool = False
) -> WakeStateStatus:
    cfg = config or JaznConfig()
    return MemoryNormalizationSidecar(
        cfg.root,
        source_db_path=cfg.normalization_source_db_path,
        sidecar_db_path=cfg.normalization_sidecar_db_path,
        runtime_version=cfg.version,
    ).wake_state_status(deep_verify=deep_verify)
