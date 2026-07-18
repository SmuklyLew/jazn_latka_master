from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
import json
import sqlite3
import time
import uuid
import zlib

from latka_jazn.tools.chat_export_dedupe import ActiveConversationState, stable_node_hash
from latka_jazn.tools.chat_export_models import ConversationGraph, ConversationPlan, ExportSourceInfo
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("chat_export_archive_store")
PAYLOAD_CODEC = "zlib-json-v1"

SCHEMA_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS archive_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_sources(
  import_id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  source_name TEXT NOT NULL,
  source_path TEXT,
  size_bytes INTEGER NOT NULL,
  status TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  completed_at_utc TEXT,
  conversation_count INTEGER NOT NULL DEFAULT 0,
  node_count INTEGER NOT NULL DEFAULT 0,
  message_count INTEGER NOT NULL DEFAULT 0,
  report_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS import_source_aliases(
  alias_id TEXT PRIMARY KEY,
  import_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_path TEXT,
  observed_at_utc TEXT NOT NULL,
  UNIQUE(import_id,source_name,source_path),
  FOREIGN KEY(import_id) REFERENCES import_sources(import_id)
);
CREATE TABLE IF NOT EXISTS conversations(
  conversation_id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  create_time REAL,
  update_time REAL,
  current_node_id TEXT,
  raw_tree_sha256 TEXT NOT NULL,
  semantic_tree_sha256 TEXT NOT NULL,
  payload_codec TEXT NOT NULL,
  payload_blob BLOB NOT NULL,
  payload_size_uncompressed INTEGER NOT NULL,
  payload_size_compressed INTEGER NOT NULL,
  node_count INTEGER NOT NULL,
  message_count INTEGER NOT NULL,
  current_path_count INTEGER NOT NULL,
  branch_point_count INTEGER NOT NULL,
  first_seen_import_id TEXT NOT NULL,
  last_seen_import_id TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at_utc TEXT NOT NULL,
  FOREIGN KEY(first_seen_import_id) REFERENCES import_sources(import_id),
  FOREIGN KEY(last_seen_import_id) REFERENCES import_sources(import_id)
);
CREATE INDEX IF NOT EXISTS idx_conversations_update_time ON conversations(update_time);
CREATE TABLE IF NOT EXISTS conversation_occurrences(
  conversation_id TEXT NOT NULL,
  import_id TEXT NOT NULL,
  relation_to_active TEXT NOT NULL,
  raw_tree_sha256 TEXT NOT NULL,
  semantic_tree_sha256 TEXT NOT NULL,
  node_count INTEGER NOT NULL,
  message_count INTEGER NOT NULL,
  observed_at_utc TEXT NOT NULL,
  PRIMARY KEY(conversation_id,import_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(import_id) REFERENCES import_sources(import_id)
);
CREATE TABLE IF NOT EXISTS conversation_revisions(
  revision_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  import_id TEXT NOT NULL,
  relation_to_active TEXT NOT NULL,
  raw_tree_sha256 TEXT NOT NULL,
  semantic_tree_sha256 TEXT NOT NULL,
  node_count INTEGER NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at_utc TEXT NOT NULL,
  UNIQUE(conversation_id,import_id,semantic_tree_sha256),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(import_id) REFERENCES import_sources(import_id)
);
CREATE TABLE IF NOT EXISTS nodes(
  conversation_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  parent_node_id TEXT,
  message_id TEXT,
  role TEXT,
  create_time REAL,
  timestamp_status TEXT NOT NULL,
  content_type TEXT,
  text_sha256 TEXT,
  stable_node_sha256 TEXT NOT NULL,
  raw_payload_sha256 TEXT,
  structural_ordinal INTEGER NOT NULL,
  on_current_path INTEGER NOT NULL,
  branch_id TEXT NOT NULL,
  has_assets INTEGER NOT NULL,
  first_seen_import_id TEXT NOT NULL,
  last_seen_import_id TEXT NOT NULL,
  PRIMARY KEY(conversation_id,node_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(first_seen_import_id) REFERENCES import_sources(import_id),
  FOREIGN KEY(last_seen_import_id) REFERENCES import_sources(import_id)
);
CREATE INDEX IF NOT EXISTS idx_nodes_message ON nodes(message_id);
CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(conversation_id,on_current_path,structural_ordinal);
CREATE TABLE IF NOT EXISTS fts_docs(
  rowid INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  message_id TEXT,
  role TEXT,
  title TEXT,
  create_time REAL,
  text_sha256 TEXT,
  UNIQUE(conversation_id,node_id),
  FOREIGN KEY(conversation_id,node_id) REFERENCES nodes(conversation_id,node_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
  text,
  content='',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS assets(
  asset_pointer TEXT PRIMARY KEY,
  original_filename TEXT,
  content_type TEXT,
  mime_type TEXT,
  availability_status TEXT NOT NULL DEFAULT 'referenced_only',
  first_seen_import_id TEXT NOT NULL,
  last_seen_import_id TEXT NOT NULL,
  FOREIGN KEY(first_seen_import_id) REFERENCES import_sources(import_id),
  FOREIGN KEY(last_seen_import_id) REFERENCES import_sources(import_id)
);
CREATE TABLE IF NOT EXISTS message_assets(
  conversation_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  asset_pointer TEXT NOT NULL,
  PRIMARY KEY(conversation_id,node_id,asset_pointer),
  FOREIGN KEY(conversation_id,node_id) REFERENCES nodes(conversation_id,node_id),
  FOREIGN KEY(asset_pointer) REFERENCES assets(asset_pointer)
);
CREATE TABLE IF NOT EXISTS import_conflicts(
  conflict_id TEXT PRIMARY KEY,
  import_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  changed_node_ids_json TEXT NOT NULL DEFAULT '[]',
  added_node_ids_json TEXT NOT NULL DEFAULT '[]',
  missing_node_ids_json TEXT NOT NULL DEFAULT '[]',
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at_utc TEXT NOT NULL,
  FOREIGN KEY(import_id) REFERENCES import_sources(import_id)
);
"""


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _payload_bytes(graph: ConversationGraph) -> tuple[bytes, bytes]:
    raw = json.dumps(
        graph.source_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return raw, zlib.compress(raw, level=6)


class ChatExportArchiveStore:
    """Canonical archive store; it never promotes text to autobiographical memory."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 30_000) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path, timeout=max(1.0, busy_timeout_ms / 1000), isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.execute(f"PRAGMA busy_timeout={max(1000, int(busy_timeout_ms))}")
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("PRAGMA temp_store=MEMORY")
        self.con.execute("PRAGMA cache_size=-65536")
        self.con.executescript(SCHEMA_SQL)
        self.con.execute(
            "INSERT OR REPLACE INTO archive_meta(key,value) VALUES('schema_version',?)",
            (SCHEMA_VERSION,),
        )
        self.con.execute(
            "INSERT OR REPLACE INTO archive_meta(key,value) VALUES('truth_boundary',?)",
            ("Archiwum rozmów jest źródłem dowodowym, nie automatyczną pamięcią długotrwałą.",),
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self.con.in_transaction:
            raise RuntimeError("nested archive transactions are not supported")
        self.con.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.con.rollback()
            raise
        else:
            self.con.commit()

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "ChatExportArchiveStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def find_import_by_sha(self, source_sha256: str) -> dict[str, Any] | None:
        row = self.con.execute(
            "SELECT * FROM import_sources WHERE sha256=? AND status='completed'",
            (source_sha256,),
        ).fetchone()
        return dict(row) if row else None

    def register_duplicate_alias(self, existing_import_id: str, source: ExportSourceInfo) -> str:
        alias_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{existing_import_id}|{source.path}"))
        with self.transaction():
            self.con.execute(
                """INSERT OR IGNORE INTO import_source_aliases
                   (alias_id,import_id,source_name,source_path,observed_at_utc)
                   VALUES(?,?,?,?,?)""",
                (alias_id, existing_import_id, source.source_name, source.path, _utc_now()),
            )
        return alias_id

    def load_active_states(self) -> dict[str, ActiveConversationState]:
        conversations = self.con.execute(
            "SELECT conversation_id,semantic_tree_sha256,node_count FROM conversations"
        ).fetchall()
        node_rows = self.con.execute(
            "SELECT conversation_id,node_id,stable_node_sha256 FROM nodes ORDER BY conversation_id,node_id"
        ).fetchall()
        by_conversation: dict[str, dict[str, str]] = {}
        for row in node_rows:
            by_conversation.setdefault(str(row["conversation_id"]), {})[str(row["node_id"])] = str(row["stable_node_sha256"])
        return {
            str(row["conversation_id"]): ActiveConversationState(
                conversation_id=str(row["conversation_id"]),
                semantic_tree_sha256=str(row["semantic_tree_sha256"]),
                node_hashes=by_conversation.get(str(row["conversation_id"]), {}),
                node_count=int(row["node_count"]),
            )
            for row in conversations
        }

    def begin_import(self, source: ExportSourceInfo) -> str:
        import_id = str(uuid.uuid4())
        self.con.execute(
            """INSERT INTO import_sources
               (import_id,sha256,source_name,source_path,size_bytes,status,started_at_utc)
               VALUES(?,?,?,?,?,'running',?)""",
            (import_id, source.sha256, source.source_name, source.path, source.size_bytes, _utc_now()),
        )
        self.con.execute(
            """INSERT INTO import_source_aliases
               (alias_id,import_id,source_name,source_path,observed_at_utc)
               VALUES(?,?,?,?,?)""",
            (str(uuid.uuid4()), import_id, source.source_name, source.path, _utc_now()),
        )
        return import_id

    def finish_import(
        self,
        import_id: str,
        *,
        conversation_count: int,
        node_count: int,
        message_count: int,
        report: dict[str, Any],
    ) -> None:
        self.con.execute(
            """UPDATE import_sources SET status='completed',completed_at_utc=?,conversation_count=?,
               node_count=?,message_count=?,report_json=? WHERE import_id=?""",
            (
                _utc_now(), conversation_count, node_count, message_count,
                json.dumps(report, ensure_ascii=False, sort_keys=True, default=str), import_id,
            ),
        )

    def store_graph(self, import_id: str, graph: ConversationGraph, plan: ConversationPlan) -> dict[str, int]:
        if plan.conversation_id != graph.conversation_id:
            raise ValueError("plan and graph refer to different conversations")
        counters = {
            "conversations_inserted": 0,
            "conversations_updated": 0,
            "nodes_inserted": 0,
            "fts_inserted": 0,
            "assets_upserted": 0,
            "conflicts": 0,
        }
        now = _utc_now()
        if plan.relation == "new":
            raw, compressed = _payload_bytes(graph)
            self.con.execute(
                """INSERT INTO conversations(
                   conversation_id,title,create_time,update_time,current_node_id,raw_tree_sha256,
                   semantic_tree_sha256,payload_codec,payload_blob,payload_size_uncompressed,
                   payload_size_compressed,node_count,message_count,current_path_count,branch_point_count,
                   first_seen_import_id,last_seen_import_id,revision,updated_at_utc)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    graph.conversation_id, graph.title, graph.create_time, graph.update_time,
                    graph.current_node_id, graph.raw_tree_sha256, graph.semantic_tree_sha256,
                    PAYLOAD_CODEC, compressed, len(raw), len(compressed), graph.node_count,
                    graph.message_count, len(graph.current_path), len(graph.branch_points),
                    import_id, import_id, now,
                ),
            )
            counters["conversations_inserted"] = 1
            new_nodes = list(graph.nodes)
        elif plan.relation == "extends_active":
            raw, compressed = _payload_bytes(graph)
            self.con.execute(
                """UPDATE conversations SET title=?,create_time=?,update_time=?,current_node_id=?,
                   raw_tree_sha256=?,semantic_tree_sha256=?,payload_codec=?,payload_blob=?,
                   payload_size_uncompressed=?,payload_size_compressed=?,node_count=?,message_count=?,
                   current_path_count=?,branch_point_count=?,last_seen_import_id=?,revision=revision+1,
                   updated_at_utc=? WHERE conversation_id=?""",
                (
                    graph.title, graph.create_time, graph.update_time, graph.current_node_id,
                    graph.raw_tree_sha256, graph.semantic_tree_sha256, PAYLOAD_CODEC, compressed,
                    len(raw), len(compressed), graph.node_count, graph.message_count,
                    len(graph.current_path), len(graph.branch_points), import_id, now,
                    graph.conversation_id,
                ),
            )
            counters["conversations_updated"] = 1
            added = set(plan.added_node_ids)
            new_nodes = [node for node in graph.nodes if node.node_id in added]
            self.con.execute(
                "UPDATE nodes SET last_seen_import_id=? WHERE conversation_id=?",
                (import_id, graph.conversation_id),
            )
        elif plan.relation in {"identical", "older_subset"}:
            self.con.execute(
                "UPDATE conversations SET last_seen_import_id=?,updated_at_utc=? WHERE conversation_id=?",
                (import_id, now, graph.conversation_id),
            )
            new_nodes = []
        else:
            self._record_conflict(import_id, graph, plan)
            counters["conflicts"] = 1
            new_nodes = []

        if new_nodes:
            counters.update(self._insert_nodes(import_id, graph, new_nodes))
        self.con.execute(
            """INSERT OR IGNORE INTO conversation_occurrences
               (conversation_id,import_id,relation_to_active,raw_tree_sha256,semantic_tree_sha256,
                node_count,message_count,observed_at_utc) VALUES(?,?,?,?,?,?,?,?)""",
            (
                graph.conversation_id, import_id, plan.relation, graph.raw_tree_sha256,
                graph.semantic_tree_sha256, graph.node_count, graph.message_count, now,
            ),
        )
        details = {
            "added_node_ids": list(plan.added_node_ids),
            "changed_node_ids": list(plan.changed_node_ids),
            "missing_from_incoming_node_ids": list(plan.missing_from_incoming_node_ids),
            "reason": plan.reason,
        }
        self.con.execute(
            """INSERT OR IGNORE INTO conversation_revisions
               (revision_id,conversation_id,import_id,relation_to_active,raw_tree_sha256,
                semantic_tree_sha256,node_count,details_json,created_at_utc)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), graph.conversation_id, import_id, plan.relation,
                graph.raw_tree_sha256, graph.semantic_tree_sha256, graph.node_count,
                json.dumps(details, ensure_ascii=False, sort_keys=True), now,
            ),
        )
        return counters

    def _insert_nodes(self, import_id: str, graph: ConversationGraph, nodes: list[Any]) -> dict[str, int]:
        node_rows = []
        for node in nodes:
            node_rows.append((
                graph.conversation_id, node.node_id, node.parent_node_id, node.message_id, node.role,
                node.create_time, node.timestamp_status, node.content_type, node.text_sha256,
                stable_node_hash(node), node.raw_payload_sha256, node.structural_ordinal,
                int(node.on_current_path), node.branch_id, int(bool(node.assets)), import_id, import_id,
            ))
        self.con.executemany(
            """INSERT INTO nodes(
               conversation_id,node_id,parent_node_id,message_id,role,create_time,timestamp_status,
               content_type,text_sha256,stable_node_sha256,raw_payload_sha256,structural_ordinal,
               on_current_path,branch_id,has_assets,first_seen_import_id,last_seen_import_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            node_rows,
        )
        text_nodes = [node for node in nodes if node.text]
        self.con.executemany(
            """INSERT INTO fts_docs(conversation_id,node_id,message_id,role,title,create_time,text_sha256)
               VALUES(?,?,?,?,?,?,?)""",
            [
                (
                    graph.conversation_id, node.node_id, node.message_id, node.role, graph.title,
                    node.create_time, node.text_sha256,
                )
                for node in text_nodes
            ],
        )
        rowids = {
            str(row["node_id"]): int(row["rowid"])
            for row in self.con.execute(
                "SELECT rowid,node_id FROM fts_docs WHERE conversation_id=?",
                (graph.conversation_id,),
            )
        }
        self.con.executemany(
            "INSERT INTO message_fts(rowid,text) VALUES(?,?)",
            [(rowids[node.node_id], node.text) for node in text_nodes],
        )
        asset_count = 0
        for node in nodes:
            for asset in node.assets:
                self.con.execute(
                    """INSERT INTO assets(asset_pointer,original_filename,content_type,mime_type,
                       availability_status,first_seen_import_id,last_seen_import_id)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(asset_pointer) DO UPDATE SET
                       original_filename=COALESCE(excluded.original_filename,assets.original_filename),
                       content_type=COALESCE(excluded.content_type,assets.content_type),
                       mime_type=COALESCE(excluded.mime_type,assets.mime_type),
                       last_seen_import_id=excluded.last_seen_import_id""",
                    (
                        asset.asset_pointer, asset.original_filename, asset.content_type, asset.mime_type,
                        asset.availability_status, import_id, import_id,
                    ),
                )
                self.con.execute(
                    "INSERT OR IGNORE INTO message_assets(conversation_id,node_id,asset_pointer) VALUES(?,?,?)",
                    (graph.conversation_id, node.node_id, asset.asset_pointer),
                )
                asset_count += 1
        return {
            "nodes_inserted": len(nodes),
            "fts_inserted": len(text_nodes),
            "assets_upserted": asset_count,
        }

    def _record_conflict(self, import_id: str, graph: ConversationGraph, plan: ConversationPlan) -> None:
        self.con.execute(
            """INSERT INTO import_conflicts(
               conflict_id,import_id,conversation_id,changed_node_ids_json,added_node_ids_json,
               missing_node_ids_json,details_json,created_at_utc) VALUES(?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), import_id, graph.conversation_id,
                json.dumps(list(plan.changed_node_ids), ensure_ascii=False),
                json.dumps(list(plan.added_node_ids), ensure_ascii=False),
                json.dumps(list(plan.missing_from_incoming_node_ids), ensure_ascii=False),
                json.dumps({"reason": plan.reason}, ensure_ascii=False, sort_keys=True),
                _utc_now(),
            ),
        )

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.con.execute(
            """SELECT d.rowid,d.conversation_id,d.node_id,d.message_id,d.role,d.title,
                      d.create_time,d.text_sha256,bm25(message_fts) AS rank
                 FROM message_fts JOIN fts_docs d ON d.rowid=message_fts.rowid
                WHERE message_fts MATCH ? ORDER BY rank LIMIT ?""",
            (query, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def conversation_payload(self, conversation_id: str) -> dict[str, Any] | None:
        row = self.con.execute(
            "SELECT payload_codec,payload_blob FROM conversations WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        if row["payload_codec"] != PAYLOAD_CODEC:
            raise ValueError(f"unsupported payload codec: {row['payload_codec']}")
        return json.loads(zlib.decompress(row["payload_blob"]).decode("utf-8"))

    def counts(self) -> dict[str, int]:
        tables = (
            "import_sources", "import_source_aliases", "conversations", "conversation_occurrences",
            "conversation_revisions", "nodes", "fts_docs", "assets", "message_assets", "import_conflicts",
        )
        return {
            table: int(self.con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        }

    def validate(self, *, full: bool = True) -> dict[str, Any]:
        started = time.monotonic()
        pragma = "integrity_check" if full else "quick_check"
        integrity = str(self.con.execute(f"PRAGMA {pragma}").fetchone()[0])
        foreign_keys = [tuple(row) for row in self.con.execute("PRAGMA foreign_key_check")]
        return {
            "ok": integrity == "ok" and not foreign_keys,
            "integrity_check": integrity,
            "validation_mode": pragma,
            "foreign_key_error_count": len(foreign_keys),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "path": str(self.path),
            "counts": self.counts(),
        }
