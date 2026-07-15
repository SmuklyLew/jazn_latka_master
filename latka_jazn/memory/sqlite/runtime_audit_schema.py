from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 1

DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        component TEXT PRIMARY KEY,
        version INTEGER NOT NULL,
        updated_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_records (
        idempotency_key TEXT PRIMARY KEY,
        operation TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        trace_id TEXT NOT NULL,
        contract_hash TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        state TEXT NOT NULL,
        result_json TEXT,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS host_bridge_audit (
        audit_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        idempotency_key TEXT,
        turn_id TEXT NOT NULL,
        trace_id TEXT NOT NULL,
        contract_hash TEXT,
        payload_hash TEXT,
        final_hash TEXT,
        metadata_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_tool_audit (
        audit_id TEXT PRIMARY KEY,
        tool_name TEXT NOT NULL,
        subject TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        approval_state TEXT NOT NULL,
        outcome TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS candidate_decisions (
        decision_id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL,
        trace_id TEXT NOT NULL,
        selected_candidate_id TEXT,
        final_hash TEXT,
        metadata_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS response_candidates (
        decision_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        text_hash TEXT NOT NULL,
        score REAL,
        accepted INTEGER NOT NULL,
        validation_json TEXT NOT NULL,
        rejection_reasons_json TEXT NOT NULL,
        raw_text TEXT,
        PRIMARY KEY (decision_id, candidate_id),
        FOREIGN KEY (decision_id) REFERENCES candidate_decisions(decision_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_host_bridge_audit_turn ON host_bridge_audit(turn_id, trace_id, created_at_utc)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_tool_audit_key ON mcp_tool_audit(idempotency_key, created_at_utc)",
    "CREATE INDEX IF NOT EXISTS idx_candidate_decisions_turn ON candidate_decisions(turn_id, trace_id, created_at_utc)",
)


def connect_runtime_audit(path: Path | str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def ensure_runtime_audit_schema(connection: sqlite3.Connection) -> None:
    with connection:
        for statement in DDL:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_meta(component, version, updated_at_utc)
            VALUES('runtime_audit', ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(component) DO UPDATE SET
              version=excluded.version,
              updated_at_utc=excluded.updated_at_utc
            """,
            (SCHEMA_VERSION,),
        )


def initialize_runtime_audit(path: Path | str) -> None:
    connection = connect_runtime_audit(path)
    try:
        ensure_runtime_audit_schema(connection)
    finally:
        connection.close()
