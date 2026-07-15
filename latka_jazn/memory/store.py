from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib, json, sqlite3, uuid

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  created_at_local TEXT NOT NULL,
  source TEXT NOT NULL,
  actor TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  importance REAL NOT NULL DEFAULT 0.5,
  emotional_weight REAL NOT NULL DEFAULT 0.0,
  canonical_impact INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS journal(
  journal_id TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  created_at_local TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS source_files(
  sha256 TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  kind TEXT NOT NULL,
  original_path TEXT,
  imported_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS legacy_conversations(
  conversation_id TEXT PRIMARY KEY,
  title TEXT,
  create_time REAL,
  create_time_warsaw TEXT,
  update_time REAL,
  update_time_warsaw TEXT,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS legacy_messages(
  legacy_rowid INTEGER PRIMARY KEY,
  conversation_id TEXT,
  conversation_title TEXT,
  message_id TEXT,
  author_role TEXT,
  create_time REAL,
  create_time_warsaw TEXT,
  text TEXT,
  parts_json TEXT,
  assets_json TEXT,
  is_visible_path INTEGER,
  visible_index INTEGER,
  text_sha256 TEXT,
  char_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_legacy_messages_conversation ON legacy_messages(conversation_id, visible_index);
CREATE UNIQUE INDEX IF NOT EXISTS idx_legacy_message_unique ON legacy_messages(conversation_id, message_id);
CREATE INDEX IF NOT EXISTS idx_legacy_messages_title ON legacy_messages(conversation_title);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, created_at_utc);

CREATE TABLE IF NOT EXISTS episodic_memories(
  episode_id TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  local_time_label TEXT,
  scene TEXT NOT NULL,
  participants_json TEXT NOT NULL DEFAULT '[]',
  emotional_anchor TEXT,
  source TEXT NOT NULL,
  grounding TEXT NOT NULL,
  confidence REAL NOT NULL,
  raw_excerpt TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_episodic_scene ON episodic_memories(scene);
CREATE TABLE IF NOT EXISTS semantic_facts(
  fact_id TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  value TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_semantic_subject ON semantic_facts(subject);
CREATE TABLE IF NOT EXISTS procedural_rules(
  rule_id TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  trigger TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  priority INTEGER NOT NULL,
  source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reflection_entries(
  reflection_id TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  episode_id TEXT,
  meaning_for_latka TEXT NOT NULL,
  identity_impact TEXT NOT NULL,
  boundary_note TEXT NOT NULL,
  next_question TEXT,
  confidence REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS truth_audits(
  audit_id TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  text TEXT NOT NULL,
  audit_json TEXT NOT NULL
);
"""

class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("schema_version", "14529"))
        self.con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("system_version", "v14.8.2.4-logic-routing-memory-grounding-repair"))
        self.con.commit()

    def close(self) -> None:
        self.con.commit(); self.con.close()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        self.con.commit()

    def add_event(self, event_type: str, payload: dict, *, source: str="runtime", actor: str="latka", tags: list[str]|None=None, importance: float=0.5, emotional_weight: float=0.0, canonical_impact: int=0, created_at_local: str|None=None) -> str:
        now = datetime.now(timezone.utc).isoformat()
        eid = hashlib.sha256((event_type + now + json.dumps(payload, ensure_ascii=False, sort_keys=True)).encode('utf-8')).hexdigest()
        self.con.execute("""INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (eid, event_type, now, created_at_local or now, source, actor, json.dumps(payload, ensure_ascii=False), json.dumps(tags or [], ensure_ascii=False), importance, emotional_weight, canonical_impact))
        self.con.commit()
        return eid

    def write_journal(self, kind: str, text: str, *, payload: dict|None=None, created_at_local: str|None=None) -> str:
        now = datetime.now(timezone.utc).isoformat()
        jid = str(uuid.uuid4())
        self.con.execute("INSERT INTO journal VALUES(?,?,?,?,?,?)", (jid, now, created_at_local or now, kind, text, json.dumps(payload or {}, ensure_ascii=False)))
        self.con.commit()
        return jid

    def register_source_file(self, path: Path, *, kind: str, original_path: str|None=None) -> str:
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024), b''):
                h.update(chunk)
        sha=h.hexdigest()
        self.con.execute("INSERT OR REPLACE INTO source_files VALUES(?,?,?,?,?,?)", (sha, str(path), path.stat().st_size, kind, original_path, datetime.now(timezone.utc).isoformat()))
        self.con.commit()
        return sha

    def search_messages(self, phrase: str, limit: int=10) -> list[sqlite3.Row]:
        like = f"%{phrase}%"
        return list(self.con.execute(
            """SELECT conversation_id, conversation_title, author_role, create_time_warsaw, visible_index,
                      substr(text,1,1200) AS text
                 FROM legacy_messages
                WHERE text LIKE ? OR conversation_title LIKE ?
                ORDER BY is_visible_path DESC, create_time DESC, legacy_rowid DESC
                LIMIT ?""",
            (like, like, limit),
        ))

    def search_messages_any(self, phrases: list[str], limit: int=10) -> list[sqlite3.Row]:
        clean = [p.strip() for p in phrases if p and p.strip()]
        if not clean:
            return []
        seen: set[int] = set()
        out: list[sqlite3.Row] = []
        per = max(limit, 3)
        for phrase in clean:
            for row in self.search_messages(phrase, per):
                key = row["conversation_id"], row["author_role"], row["create_time_warsaw"], row["text"]
                h = hash(key)
                if h in seen:
                    continue
                seen.add(h)
                out.append(row)
                if len(out) >= limit:
                    return out
        return out

    def stats(self) -> dict:
        keys = {}
        for table in ["events", "journal", "source_files", "legacy_conversations", "legacy_messages", "episodic_memories", "semantic_facts", "procedural_rules", "reflection_entries", "truth_audits"]:
            keys[table] = self.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return keys

    def add_episodic_memory(self, rec: dict) -> None:
        self.con.execute("""INSERT OR REPLACE INTO episodic_memories VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
            rec["episode_id"], rec["created_at_utc"], rec.get("local_time_label"), rec["scene"],
            json.dumps(rec.get("participants") or [], ensure_ascii=False), rec.get("emotional_anchor"),
            rec.get("source") or "runtime", rec.get("grounding") or "unknown", float(rec.get("confidence") or 0.0),
            rec.get("raw_excerpt"), json.dumps(rec.get("tags") or [], ensure_ascii=False)
        ))
        self.con.commit()

    def add_semantic_fact(self, rec: dict) -> None:
        self.con.execute("""INSERT OR REPLACE INTO semantic_facts VALUES(?,?,?,?,?,?,?,?)""", (
            rec["fact_id"], rec["created_at_utc"], rec["subject"], rec["predicate"], rec["value"],
            rec.get("source") or "runtime", float(rec.get("confidence") or 0.0), json.dumps(rec.get("tags") or [], ensure_ascii=False)
        ))
        self.con.commit()

    def add_procedural_rule(self, rec: dict) -> None:
        self.con.execute("""INSERT OR REPLACE INTO procedural_rules VALUES(?,?,?,?,?,?,?)""", (
            rec["rule_id"], rec["created_at_utc"], rec["trigger"], rec["action"], rec["reason"],
            int(rec.get("priority") or 50), rec.get("source") or "runtime"
        ))
        self.con.commit()

    def add_reflection(self, rec: dict) -> None:
        self.con.execute("""INSERT OR REPLACE INTO reflection_entries VALUES(?,?,?,?,?,?,?,?)""", (
            rec["reflection_id"], rec["created_at_utc"], rec.get("episode_id"), rec["meaning_for_latka"],
            rec["identity_impact"], rec["boundary_note"], rec.get("next_question"), float(rec.get("confidence") or 0.0)
        ))
        self.con.commit()

    def add_truth_audit(self, rec: dict) -> str:
        aid = hashlib.sha256((rec["created_at_utc"] + rec["text"]).encode("utf-8")).hexdigest()
        self.con.execute("INSERT OR REPLACE INTO truth_audits VALUES(?,?,?,?)", (
            aid, rec["created_at_utc"], rec["text"], json.dumps(rec.get("audit") or [], ensure_ascii=False)
        ))
        self.con.commit()
        return aid

    def search_episodic_memories(self, phrase: str, limit: int=5) -> list[dict]:
        like = f"%{phrase}%"
        rows = self.con.execute(
            """SELECT * FROM episodic_memories
               WHERE scene LIKE ? OR emotional_anchor LIKE ? OR raw_excerpt LIKE ?
               ORDER BY created_at_utc DESC LIMIT ?""",
            (like, like, like, limit),
        ).fetchall()
        out=[]
        for r in rows:
            d=dict(r)
            d["participants"] = json.loads(d.pop("participants_json") or "[]")
            d["tags"] = json.loads(d.pop("tags_json") or "[]")
            out.append(d)
        return out
