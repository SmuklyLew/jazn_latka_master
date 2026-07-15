from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote
import datetime, hashlib, json, sqlite3, uuid

AUDIT_SCHEMA_VERSION = "audit_context/v14.8.2.5"

def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def sqlite_readonly_uri(path: Path) -> str:
    return "file:" + quote(str(path.resolve()).replace("\\", "/"), safe="/:") + "?mode=ro"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS audit_runtime_events(
  audit_event_id TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  event_type TEXT NOT NULL,
  source TEXT NOT NULL,
  actor TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  trace_id TEXT,
  turn_id TEXT,
  payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_runtime_events_type_time ON audit_runtime_events(event_type, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_audit_runtime_events_trace ON audit_runtime_events(trace_id, turn_id);
CREATE TABLE IF NOT EXISTS audit_db_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

class AuditContextStore:
    """Realtime writer for the separate audit SQLite database.

    The audit database is not Łatka's relational memory. It stores system/tool/runtime
    traces, provider/router decisions, startup markers and technical events.
    """
    def __init__(self, path: Path) -> None:
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path, timeout=30.0); self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.execute("INSERT OR REPLACE INTO audit_db_meta(key,value) VALUES(?,?)", ("schema_version", AUDIT_SCHEMA_VERSION))
        self.con.commit()
    def close(self) -> None:
        self.con.commit(); self.con.close()
    def append_event(self, event_type: str, payload: dict[str, Any] | None = None, *, source: str = "runtime", actor: str = "system", tags: list[str] | None = None, trace_id: str | None = None, turn_id: str | None = None) -> str:
        payload = payload or {}; payload_json = _json(payload)
        payload_sha = hashlib.sha256(payload_json.encode("utf-8", errors="surrogatepass")).hexdigest()
        event_id = hashlib.sha256((event_type + _now_utc() + payload_sha + str(uuid.uuid4())).encode("utf-8")).hexdigest()
        self.con.execute("""INSERT INTO audit_runtime_events(audit_event_id, created_at_utc, event_type, source, actor, payload_json, tags_json, trace_id, turn_id, payload_sha256) VALUES(?,?,?,?,?,?,?,?,?,?)""", (event_id, _now_utc(), event_type, source, actor, payload_json, json.dumps(tags or [], ensure_ascii=False), trace_id, turn_id, payload_sha))
        self.con.commit(); return event_id
    def status(self) -> dict[str, Any]:
        return {"path": str(self.path), "exists": self.path.exists(), "schema_version": self.con.execute("SELECT value FROM audit_db_meta WHERE key='schema_version'").fetchone()[0], "audit_runtime_events": self.con.execute("SELECT COUNT(*) FROM audit_runtime_events").fetchone()[0]}
    @staticmethod
    def readonly_status(path: Path) -> dict[str, Any]:
        if not path.exists(): return {"path": str(path), "exists": False}
        con = sqlite3.connect(sqlite_readonly_uri(path), uri=True, timeout=10.0)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            out: dict[str, Any] = {"path": str(path), "exists": True, "tables": len(tables)}
            if "audit_runtime_events" in tables:
                out["audit_runtime_events"] = con.execute("SELECT COUNT(*) FROM audit_runtime_events").fetchone()[0]
            return out
        finally:
            con.close()
