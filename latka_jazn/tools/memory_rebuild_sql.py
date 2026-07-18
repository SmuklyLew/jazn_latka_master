from __future__ import annotations

CATALOG_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS catalog_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 source_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,name TEXT NOT NULL,
 size_bytes INTEGER NOT NULL,first_seen_at_utc TEXT NOT NULL,last_seen_at_utc TEXT NOT NULL,
 details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS source_occurrences(
 occurrence_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,path TEXT NOT NULL,seen_at_utc TEXT NOT NULL,
 UNIQUE(source_id,path),FOREIGN KEY(source_id) REFERENCES sources(source_id));
CREATE TABLE IF NOT EXISTS operations(
 operation_id TEXT PRIMARY KEY,operation_type TEXT NOT NULL,source_id TEXT,target_database TEXT,
 status TEXT NOT NULL,started_at_utc TEXT NOT NULL,completed_at_utc TEXT,
 report_json TEXT NOT NULL DEFAULT '{}',error_json TEXT NOT NULL DEFAULT '{}',
 FOREIGN KEY(source_id) REFERENCES sources(source_id));
CREATE INDEX IF NOT EXISTS idx_operations_status ON operations(status,started_at_utc);
CREATE TABLE IF NOT EXISTS links(
 link_id TEXT PRIMARY KEY,source_database TEXT NOT NULL,source_type TEXT NOT NULL,
 source_record_id TEXT NOT NULL,target_database TEXT NOT NULL,target_type TEXT NOT NULL,
 target_record_id TEXT NOT NULL,relation TEXT NOT NULL,source_sha256 TEXT,created_at_utc TEXT NOT NULL,
 UNIQUE(source_database,source_type,source_record_id,target_database,target_type,target_record_id,relation));
CREATE TABLE IF NOT EXISTS verifications(
 verification_id TEXT PRIMARY KEY,database_name TEXT NOT NULL,database_path TEXT NOT NULL,
 full_check INTEGER NOT NULL,ok INTEGER NOT NULL,result_json TEXT NOT NULL,verified_at_utc TEXT NOT NULL);
"""

JOURNAL_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS journal_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal_sources(
 source_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,name TEXT NOT NULL,path TEXT NOT NULL,
 format TEXT NOT NULL,imported_at_utc TEXT NOT NULL,entry_count INTEGER NOT NULL,
 invalid_count INTEGER NOT NULL,meta_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS journal_entries(
 entry_id TEXT PRIMARY KEY,identity_key TEXT NOT NULL UNIQUE,source_record_id TEXT,title TEXT NOT NULL,
 summary TEXT NOT NULL,content TEXT NOT NULL,content_sha256 TEXT NOT NULL,raw_json TEXT NOT NULL,
 truth_status TEXT NOT NULL,importance REAL NOT NULL,event_time_start TEXT,event_time_end TEXT,
 timestamp_status TEXT NOT NULL,suspected_fanout INTEGER NOT NULL,status TEXT NOT NULL,
 revision INTEGER NOT NULL,created_at_utc TEXT NOT NULL,updated_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal_entry_sources(
 entry_id TEXT NOT NULL,source_id TEXT NOT NULL,source_record_id TEXT NOT NULL,
 content_sha256 TEXT NOT NULL,seen_at_utc TEXT NOT NULL,
 PRIMARY KEY(entry_id,source_id,source_record_id),
 FOREIGN KEY(entry_id) REFERENCES journal_entries(entry_id) ON DELETE CASCADE,
 FOREIGN KEY(source_id) REFERENCES journal_sources(source_id));
CREATE TABLE IF NOT EXISTS journal_revisions(
 revision_id TEXT PRIMARY KEY,entry_id TEXT NOT NULL,revision INTEGER NOT NULL,source_id TEXT NOT NULL,
 content_sha256 TEXT NOT NULL,previous_sha256 TEXT,raw_json TEXT NOT NULL,created_at_utc TEXT NOT NULL,
 UNIQUE(entry_id,revision),FOREIGN KEY(entry_id) REFERENCES journal_entries(entry_id) ON DELETE CASCADE,
 FOREIGN KEY(source_id) REFERENCES journal_sources(source_id));
CREATE TABLE IF NOT EXISTS journal_fts_docs(
 rowid INTEGER PRIMARY KEY AUTOINCREMENT,entry_id TEXT NOT NULL UNIQUE,title TEXT NOT NULL,
 truth_status TEXT NOT NULL,event_time_start TEXT,FOREIGN KEY(entry_id) REFERENCES journal_entries(entry_id));
CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5(
 text,content='',tokenize='unicode61 remove_diacritics 2');
"""

EXPERIENCE_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS experience_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates(
 candidate_id TEXT PRIMARY KEY,identity_key TEXT NOT NULL UNIQUE,source_database TEXT NOT NULL,
 source_type TEXT NOT NULL,source_record_id TEXT NOT NULL,source_sha256 TEXT,title TEXT NOT NULL,
 summary TEXT NOT NULL,truth_status TEXT NOT NULL,confidence REAL NOT NULL,importance REAL NOT NULL,
 domains_json TEXT NOT NULL,score_json TEXT NOT NULL,status TEXT NOT NULL,created_at_utc TEXT NOT NULL,
 reviewed_at_utc TEXT,reviewed_by TEXT,review_reason TEXT,
 UNIQUE(source_database,source_type,source_record_id));
CREATE TABLE IF NOT EXISTS experiences(
 experience_id TEXT PRIMARY KEY,identity_key TEXT NOT NULL UNIQUE,candidate_id TEXT NOT NULL UNIQUE,
 title TEXT NOT NULL,summary TEXT NOT NULL,truth_status TEXT NOT NULL,confidence REAL NOT NULL,
 importance REAL NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL,approved_by TEXT NOT NULL,
 approval_reason TEXT NOT NULL,created_at_utc TEXT NOT NULL,updated_at_utc TEXT NOT NULL,
 FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id));
CREATE TABLE IF NOT EXISTS experience_domains(
 experience_id TEXT NOT NULL,domain TEXT NOT NULL,PRIMARY KEY(experience_id,domain),
 FOREIGN KEY(experience_id) REFERENCES experiences(experience_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS experience_sources(
 experience_id TEXT NOT NULL,source_database TEXT NOT NULL,source_type TEXT NOT NULL,
 source_record_id TEXT NOT NULL,source_sha256 TEXT,evidence_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(experience_id,source_database,source_type,source_record_id),
 FOREIGN KEY(experience_id) REFERENCES experiences(experience_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS experience_fts_docs(
 rowid INTEGER PRIMARY KEY AUTOINCREMENT,record_type TEXT NOT NULL,record_id TEXT NOT NULL,
 title TEXT NOT NULL,truth_status TEXT NOT NULL,UNIQUE(record_type,record_id));
CREATE VIRTUAL TABLE IF NOT EXISTS experience_fts USING fts5(
 text,content='',tokenize='unicode61 remove_diacritics 2');
"""
