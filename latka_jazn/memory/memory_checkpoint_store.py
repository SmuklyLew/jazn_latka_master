from __future__ import annotations

from typing import Any
import hashlib
import json
import sqlite3
import zlib

from latka_jazn.memory.memory_tier_support import iso, json_text
from latka_jazn.memory.memory_tiers import utc_now
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_checkpoint_store")


class MemoryCheckpointStoreMixin:
    con: Any

    def checkpoint_session(
        self,
        session_id: str,
        *,
        state: dict[str, Any] | None = None,
        keep_latest: int = 3,
    ) -> str:
        rows = self.con.execute(
            """SELECT r.record_json FROM memory_records r
               JOIN working_memory_index w ON w.memory_id=r.memory_id
               WHERE w.session_id=? AND w.checkpoint_allowed=1
               ORDER BY r.updated_at_utc,r.memory_id""", (session_id,),
        ).fetchall()
        payload = {"schema_version": SCHEMA_VERSION, "session_id": session_id,
                   "records": [json.loads(row["record_json"]) for row in rows], "state": state or {}}
        raw = json_text(payload).encode()
        state_sha = hashlib.sha256(raw).hexdigest()
        checkpoint_id = hashlib.sha256(f"{session_id}|{state_sha}".encode()).hexdigest()
        with self.transaction():
            self.con.execute(
                """INSERT OR IGNORE INTO session_checkpoints(
                   checkpoint_id,session_id,created_at_utc,payload_codec,payload_blob,
                   uncompressed_size,record_count,state_sha256) VALUES(?,?,?,'zlib-json-v1',?,?,?,?)""",
                (checkpoint_id, session_id, iso(utc_now()), zlib.compress(raw, 6),
                 len(raw), len(rows), state_sha),
            )
            stale = self.con.execute(
                """SELECT checkpoint_id FROM session_checkpoints WHERE session_id=?
                   ORDER BY created_at_utc DESC,checkpoint_id DESC LIMIT -1 OFFSET ?""",
                (session_id, max(1, int(keep_latest))),
            ).fetchall()
            if stale:
                self.con.executemany("DELETE FROM session_checkpoints WHERE checkpoint_id=?",
                                     [(str(row["checkpoint_id"]),) for row in stale])
        return checkpoint_id

    def load_latest_checkpoint(self, session_id: str) -> dict[str, Any] | None:
        row = self.con.execute(
            """SELECT payload_codec,payload_blob,state_sha256 FROM session_checkpoints
               WHERE session_id=? ORDER BY created_at_utc DESC,checkpoint_id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        if row["payload_codec"] != "zlib-json-v1":
            raise ValueError("unsupported checkpoint codec")
        raw = zlib.decompress(row["payload_blob"])
        if hashlib.sha256(raw).hexdigest() != row["state_sha256"]:
            raise sqlite3.DatabaseError("checkpoint hash mismatch")
        return json.loads(raw.decode())
