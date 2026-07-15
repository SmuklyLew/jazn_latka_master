from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


LEXICAL_RESOURCE_CACHE_SCHEMA = "lexical_resource_cache/v14.8.4.005"


class LexicalResourceCache:
    """Mały, jawny cache lookupów zasobów leksykalnych.

    Cache przechowuje tylko wyniki kontrolowanych lookupów wraz z metadanymi
    źródła. Nie pobiera niczego sam, nie mirroruje dużych baz i nie zastępuje
    lokalnych providerów morfologicznych. Domyślna ścieżka wskazuje istniejący
    runtime cache w workspace_runtime, więc baza nie jest przeznaczona do commita.
    """

    def __init__(self, root: str | Path | None = None, path: str | Path | None = None) -> None:
        base = Path(root) if root else Path.cwd()
        self.root = base
        self.path = Path(path) if path else base / "workspace_runtime" / "dictionary_cache.sqlite3"

    def lookup(self, source_id: str, key: str) -> dict[str, Any] | None:
        source_id = (source_id or "").strip()
        key = (key or "").strip()
        if not source_id or not key or not self.path.exists():
            return None
        try:
            with sqlite3.connect(self.path) as db:
                self._ensure_schema(db)
                row = db.execute(
                    """
                    SELECT payload_json, source_url, license, retrieved_at_utc, provider
                    FROM lexical_resource_cache
                    WHERE source_id = ? AND key = ?
                    """,
                    (source_id, key),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        payload_json, source_url, license_name, retrieved_at_utc, provider = row
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {"raw_payload": payload_json}
        return {
            "schema_version": LEXICAL_RESOURCE_CACHE_SCHEMA,
            "source_id": source_id,
            "key": key,
            "payload": payload,
            "source_url": source_url,
            "license": license_name,
            "retrieved_at_utc": retrieved_at_utc,
            "provider": provider,
            "truth_boundary": "Cache entry is a stored lookup result with provenance; it is not proof that a full external dictionary was bundled in the repository.",
        }

    def store(
        self,
        source_id: str,
        key: str,
        payload: dict[str, Any],
        source_url: str,
        license: str | None,
        *,
        provider: str | None = None,
        retrieved_at_utc: str | None = None,
    ) -> None:
        source_id = (source_id or "").strip()
        key = (key or "").strip()
        source_url = (source_url or "").strip()
        if not source_id:
            raise ValueError("source_id is required")
        if not key:
            raise ValueError("key is required")
        if not source_url:
            raise ValueError("source_url is required for provenance")
        timestamp = retrieved_at_utc or datetime.now(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            self._ensure_schema(db)
            db.execute(
                """
                INSERT INTO lexical_resource_cache(
                    source_id, key, payload_json, source_url, license, retrieved_at_utc, provider
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    source_url = excluded.source_url,
                    license = excluded.license,
                    retrieved_at_utc = excluded.retrieved_at_utc,
                    provider = excluded.provider
                """,
                (
                    source_id,
                    key,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    source_url,
                    license,
                    timestamp,
                    provider or source_id,
                ),
            )
            db.commit()

    def stats(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": LEXICAL_RESOURCE_CACHE_SCHEMA,
                "path": str(self.path),
                "exists": False,
                "entries_total": 0,
                "entries_by_source": {},
            }
        try:
            with sqlite3.connect(self.path) as db:
                self._ensure_schema(db)
                rows = db.execute("SELECT source_id, COUNT(*) FROM lexical_resource_cache GROUP BY source_id").fetchall()
        except sqlite3.Error as exc:
            return {
                "schema_version": LEXICAL_RESOURCE_CACHE_SCHEMA,
                "path": str(self.path),
                "exists": True,
                "entries_total": 0,
                "entries_by_source": {},
                "error": f"sqlite_error:{type(exc).__name__}",
            }
        by_source = {str(source): int(count) for source, count in rows}
        return {
            "schema_version": LEXICAL_RESOURCE_CACHE_SCHEMA,
            "path": str(self.path),
            "exists": True,
            "entries_total": sum(by_source.values()),
            "entries_by_source": by_source,
        }

    def _ensure_schema(self, db: sqlite3.Connection) -> None:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS lexical_resource_cache (
                source_id TEXT NOT NULL,
                key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                source_url TEXT NOT NULL,
                license TEXT,
                retrieved_at_utc TEXT NOT NULL,
                provider TEXT,
                PRIMARY KEY(source_id, key)
            )
            """
        )
