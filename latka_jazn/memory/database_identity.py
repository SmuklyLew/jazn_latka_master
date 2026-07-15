from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version

SCHEMA_VERSION = schema_version("database_identity")
TABLE_NAME = "jazn_database_identity"


@dataclass(slots=True)
class DatabaseIdentity:
    database_uuid: str
    schema_identity: str
    schema_version_number: int
    created_by_runtime: str
    created_at_utc: str
    trust_state: str = "trusted"
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_identity_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME}(
          singleton INTEGER PRIMARY KEY CHECK(singleton=1),
          database_uuid TEXT NOT NULL UNIQUE,
          schema_identity TEXT NOT NULL,
          schema_version_number INTEGER NOT NULL,
          created_by_runtime TEXT NOT NULL,
          created_at_utc TEXT NOT NULL,
          trust_state TEXT NOT NULL
        )
        """
    )


def read_database_identity(connection: sqlite3.Connection) -> DatabaseIdentity | None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE_NAME,)
    ).fetchone()
    if not exists:
        return None
    row = connection.execute(
        f"SELECT database_uuid,schema_identity,schema_version_number,created_by_runtime,created_at_utc,trust_state FROM {TABLE_NAME} WHERE singleton=1"
    ).fetchone()
    if row is None:
        return None
    return DatabaseIdentity(*row)


def initialize_database_identity(
    connection: sqlite3.Connection,
    *,
    schema_identity: str,
    schema_version_number: int,
    runtime_version: str = PACKAGE_VERSION_FULL,
    trust_state: str = "trusted",
    database_uuid: str | None = None,
) -> DatabaseIdentity:
    ensure_identity_table(connection)
    existing = read_database_identity(connection)
    if existing is not None:
        return existing
    identity = DatabaseIdentity(
        database_uuid=database_uuid or str(uuid.uuid4()),
        schema_identity=schema_identity,
        schema_version_number=int(schema_version_number),
        created_by_runtime=runtime_version,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        trust_state=trust_state,
    )
    connection.execute(
        f"""
        INSERT INTO {TABLE_NAME}(
          singleton,database_uuid,schema_identity,schema_version_number,
          created_by_runtime,created_at_utc,trust_state
        ) VALUES(1,?,?,?,?,?,?)
        """,
        (
            identity.database_uuid,
            identity.schema_identity,
            identity.schema_version_number,
            identity.created_by_runtime,
            identity.created_at_utc,
            identity.trust_state,
        ),
    )
    return identity


def mark_imported_untrusted(connection: sqlite3.Connection, *, schema_identity: str = "imported") -> DatabaseIdentity:
    identity = initialize_database_identity(
        connection,
        schema_identity=schema_identity,
        schema_version_number=0,
        trust_state="imported_untrusted",
    )
    if identity.trust_state != "imported_untrusted":
        connection.execute(f"UPDATE {TABLE_NAME} SET trust_state='imported_untrusted' WHERE singleton=1")
        identity.trust_state = "imported_untrusted"
    return identity
