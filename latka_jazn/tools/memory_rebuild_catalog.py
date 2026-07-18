from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

from latka_jazn.tools.memory_rebuild_common import canonical_json, now_utc, uid, schema_version
from latka_jazn.tools.memory_rebuild_sql import CATALOG_SQL
from latka_jazn.tools.memory_rebuild_store import Store


class CatalogStore(Store):
    def __init__(self, path: Path) -> None:
        super().__init__(path, CATALOG_SQL, "catalog_meta", schema_version("memory_rebuild_catalog"))

    def source(self, path: Path, source_hash: str, kind: str, size: int, details: dict[str, Any]) -> str:
        source_id = uid("source", source_hash)
        current = now_utc()
        with self.transaction():
            self.con.execute(
                """INSERT INTO sources(source_id,sha256,kind,name,size_bytes,first_seen_at_utc,last_seen_at_utc,details_json)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(sha256) DO UPDATE SET
                   last_seen_at_utc=excluded.last_seen_at_utc,details_json=excluded.details_json""",
                (source_id, source_hash, kind, path.name, size, current, current, canonical_json(details)),
            )
            self.con.execute(
                "INSERT OR IGNORE INTO source_occurrences(occurrence_id,source_id,path,seen_at_utc) VALUES(?,?,?,?)",
                (uid("occurrence", source_id, str(path)), source_id, str(path), current),
            )
        return source_id

    def begin(self, kind: str, source_id: str | None, target: str | None) -> str:
        operation_id = str(uuid.uuid4())
        with self.transaction():
            self.con.execute(
                "INSERT INTO operations(operation_id,operation_type,source_id,target_database,status,started_at_utc) VALUES(?,?,?,?,?,?)",
                (operation_id, kind, source_id, target, "running", now_utc()),
            )
        return operation_id

    def finish(self, operation_id: str, report: dict[str, Any], status: str = "verified") -> None:
        with self.transaction():
            self.con.execute(
                "UPDATE operations SET status=?,completed_at_utc=?,report_json=? WHERE operation_id=?",
                (status, now_utc(), canonical_json(report), operation_id),
            )

    def fail(self, operation_id: str, error: BaseException) -> None:
        with self.transaction():
            self.con.execute(
                "UPDATE operations SET status='failed',completed_at_utc=?,error_json=? WHERE operation_id=?",
                (now_utc(), canonical_json({"type": type(error).__name__, "error": str(error)}), operation_id),
            )

    def link(self, source_db: str, source_type: str, source_id: str, target_db: str,
             target_type: str, target_id: str, relation: str, source_hash: str | None) -> None:
        with self.transaction():
            self.con.execute(
                """INSERT OR IGNORE INTO links(link_id,source_database,source_type,source_record_id,
                   target_database,target_type,target_record_id,relation,source_sha256,created_at_utc)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (uid("link", source_db, source_type, source_id, target_db, target_type, target_id, relation),
                 source_db, source_type, source_id, target_db, target_type, target_id, relation, source_hash, now_utc()),
            )

    def status(self) -> dict[str, int]:
        queries = {
            "sources": "sources", "occurrences": "source_occurrences", "operations": "operations",
            "failed": "operations WHERE status='failed'", "links": "links", "verifications": "verifications",
        }
        return {key: int(self.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for key, table in queries.items()}
