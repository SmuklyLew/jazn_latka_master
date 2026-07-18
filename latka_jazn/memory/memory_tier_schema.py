from __future__ import annotations

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_tier_schema")
SCHEMA_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS memory_store_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_records(
  memory_id TEXT PRIMARY KEY,
  tier TEXT NOT NULL CHECK(tier IN ('working','short_term','long_term')),
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  domain TEXT NOT NULL,
  mode TEXT NOT NULL,
  truth_status TEXT NOT NULL,
  confidence REAL NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
  importance REAL NOT NULL CHECK(importance BETWEEN 0.0 AND 1.0),
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  record_json TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(tier,kind,content_sha256,domain,mode)
);
CREATE INDEX IF NOT EXISTS idx_memory_records_tier_updated
  ON memory_records(tier,active,updated_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_memory_records_domain_kind
  ON memory_records(domain,kind,active);
CREATE TABLE IF NOT EXISTS memory_evidence(
  memory_id TEXT NOT NULL,
  evidence_key TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  PRIMARY KEY(memory_id,evidence_key),
  FOREIGN KEY(memory_id) REFERENCES memory_records(memory_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_source
  ON memory_evidence(source_type,source_id);
CREATE TABLE IF NOT EXISTS working_memory_index(
  memory_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  turn_id TEXT,
  active_goal TEXT,
  expires_on_session_end INTEGER NOT NULL,
  checkpoint_allowed INTEGER NOT NULL,
  FOREIGN KEY(memory_id) REFERENCES memory_records(memory_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_working_session
  ON working_memory_index(session_id);
CREATE TABLE IF NOT EXISTS short_term_memory_index(
  memory_id TEXT PRIMARY KEY,
  expires_at_utc TEXT NOT NULL,
  reinforcement_count INTEGER NOT NULL,
  last_reinforced_at_utc TEXT,
  reinforcement_evidence_keys_json TEXT NOT NULL,
  promotion_status TEXT NOT NULL,
  FOREIGN KEY(memory_id) REFERENCES memory_records(memory_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_short_expiry
  ON short_term_memory_index(promotion_status,expires_at_utc);
CREATE TABLE IF NOT EXISTS long_term_memory_index(
  memory_id TEXT PRIMARY KEY,
  promoted_at_utc TEXT NOT NULL,
  promoted_from_memory_id TEXT,
  promotion_decision_id TEXT NOT NULL UNIQUE,
  approved_by TEXT NOT NULL,
  promotion_reason TEXT NOT NULL,
  revision INTEGER NOT NULL,
  invalidated_at_utc TEXT,
  invalidation_reason TEXT,
  FOREIGN KEY(memory_id) REFERENCES memory_records(memory_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS promotion_requests(
  request_id TEXT PRIMARY KEY,
  source_memory_id TEXT NOT NULL,
  target_tier TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  requested_at_utc TEXT NOT NULL,
  explicit_user_approval INTEGER NOT NULL,
  reason TEXT NOT NULL,
  request_json TEXT NOT NULL,
  FOREIGN KEY(source_memory_id) REFERENCES memory_records(memory_id)
);
CREATE TABLE IF NOT EXISTS promotion_decisions(
  decision_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  source_memory_id TEXT NOT NULL,
  outcome TEXT NOT NULL,
  target_tier TEXT NOT NULL,
  decided_at_utc TEXT NOT NULL,
  decided_by TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  automatic_commit_allowed INTEGER NOT NULL CHECK(automatic_commit_allowed=0),
  decision_json TEXT NOT NULL,
  FOREIGN KEY(request_id) REFERENCES promotion_requests(request_id),
  FOREIGN KEY(source_memory_id) REFERENCES memory_records(memory_id)
);
CREATE TABLE IF NOT EXISTS promotion_ledger(
  ledger_id TEXT PRIMARY KEY,
  source_memory_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  long_term_memory_id TEXT,
  event_type TEXT NOT NULL,
  event_at_utc TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(decision_id,event_type),
  FOREIGN KEY(source_memory_id) REFERENCES memory_records(memory_id),
  FOREIGN KEY(request_id) REFERENCES promotion_requests(request_id),
  FOREIGN KEY(decision_id) REFERENCES promotion_decisions(decision_id)
);
CREATE TABLE IF NOT EXISTS memory_outbox(
  event_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','processing','processed','failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at_utc TEXT NOT NULL,
  available_at_utc TEXT NOT NULL,
  claimed_at_utc TEXT,
  processed_at_utc TEXT,
  last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending
  ON memory_outbox(status,available_at_utc,created_at_utc);
CREATE TABLE IF NOT EXISTS session_checkpoints(
  checkpoint_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  payload_codec TEXT NOT NULL,
  payload_blob BLOB NOT NULL,
  uncompressed_size INTEGER NOT NULL,
  record_count INTEGER NOT NULL,
  state_sha256 TEXT NOT NULL,
  UNIQUE(session_id,state_sha256)
);
CREATE INDEX IF NOT EXISTS idx_checkpoint_session_time
  ON session_checkpoints(session_id,created_at_utc DESC);
"""
