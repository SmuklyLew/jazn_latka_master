from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from latka_jazn.memory.embedding_provider import DisabledEmbeddingProvider, EmbeddingProvider
from latka_jazn.memory.vector_index import VectorIndex
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("hybrid_retriever")


def _text_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class RetrievalHit:
    document_id: str
    text: str
    source_locator: str
    confidence: float
    retrieval_mode: str
    provenance: dict[str, Any]
    text_hash: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HybridRetriever:
    """FTS5-first local retriever with an optional rebuildable vector layer."""

    def __init__(
        self,
        path: Path | str,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.path = Path(path)
        self.embedding_provider = embedding_provider or DisabledEmbeddingProvider()
        self.vector_index = VectorIndex(self.path.with_suffix(".vectors.sqlite3"), self.embedding_provider)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS hybrid_documents(
                      document_id TEXT PRIMARY KEY,
                      text TEXT NOT NULL,
                      source_locator TEXT NOT NULL,
                      text_hash TEXT NOT NULL,
                      metadata_json TEXT NOT NULL
                    )
                    """
                )
                try:
                    connection.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS hybrid_documents_fts USING fts5(document_id UNINDEXED, text, content='hybrid_documents', content_rowid='rowid')"
                    )
                except sqlite3.OperationalError:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS hybrid_documents_fts(document_id TEXT PRIMARY KEY,text TEXT NOT NULL)"
                    )
        finally:
            connection.close()

    def rebuild(self, documents: Iterable[dict[str, Any]], *, rebuild_vectors: bool = True) -> dict[str, Any]:
        rows = []
        for item in documents:
            text = str(item.get("text") or "")
            rows.append(
                {
                    "document_id": str(item["document_id"]),
                    "text": text,
                    "source_locator": str(item.get("source_locator") or item["document_id"]),
                    "text_hash": str(item.get("text_hash") or _text_hash(text)),
                    "metadata": dict(item.get("metadata") or {}),
                }
            )
        rows.sort(key=lambda row: row["document_id"])
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM hybrid_documents")
                connection.execute("DELETE FROM hybrid_documents_fts")
                for row in rows:
                    connection.execute(
                        "INSERT INTO hybrid_documents(document_id,text,source_locator,text_hash,metadata_json) VALUES(?,?,?,?,?)",
                        (
                            row["document_id"],
                            row["text"],
                            row["source_locator"],
                            row["text_hash"],
                            json.dumps(row["metadata"], ensure_ascii=False, sort_keys=True),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO hybrid_documents_fts(document_id,text) VALUES(?,?)",
                        (row["document_id"], row["text"]),
                    )
        finally:
            connection.close()
        vector_count = 0
        if rebuild_vectors and getattr(self.embedding_provider, "name", "disabled") != "disabled":
            vector_count = self.vector_index.rebuild(rows)
        return {"document_count": len(rows), "vector_count": vector_count, "deterministic_order": True}

    def _fts_hits(self, query: str, *, limit: int, exclude_text_hash: str | None) -> list[RetrievalHit]:
        connection = self._connect()
        try:
            try:
                rows = connection.execute(
                    """
                    SELECT d.*, bm25(hybrid_documents_fts) AS rank
                    FROM hybrid_documents_fts
                    JOIN hybrid_documents d ON d.document_id=hybrid_documents_fts.document_id
                    WHERE hybrid_documents_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = connection.execute(
                    """
                    SELECT d.*, 1.0 AS rank FROM hybrid_documents d
                    WHERE lower(d.text) LIKE '%' || lower(?) || '%'
                    ORDER BY d.document_id LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            hits = []
            for index, row in enumerate(rows):
                if exclude_text_hash and row["text_hash"] == exclude_text_hash:
                    continue
                confidence = max(0.0, 1.0 - index / max(1, limit))
                hits.append(
                    RetrievalHit(
                        document_id=row["document_id"],
                        text=row["text"],
                        source_locator=row["source_locator"],
                        confidence=confidence,
                        retrieval_mode="fts",
                        provenance={"database": str(self.path), "rank": row["rank"]},
                        text_hash=row["text_hash"],
                    )
                )
            return hits
        finally:
            connection.close()

    def search(self, query: str, *, limit: int = 8, current_turn_text: str | None = None) -> list[RetrievalHit]:
        exclude_hash = _text_hash(current_turn_text) if current_turn_text else None
        merged: dict[str, RetrievalHit] = {
            hit.document_id: hit for hit in self._fts_hits(query, limit=max(limit * 2, 8), exclude_text_hash=exclude_hash)
        }
        if getattr(self.embedding_provider, "name", "disabled") != "disabled":
            vector_hits = self.vector_index.search(query, limit=max(limit * 2, 8), exclude_text_hash=exclude_hash)
            connection = self._connect()
            try:
                for vector_hit in vector_hits:
                    row = connection.execute(
                        "SELECT * FROM hybrid_documents WHERE document_id=?", (vector_hit.document_id,)
                    ).fetchone()
                    if row is None:
                        continue
                    existing = merged.get(vector_hit.document_id)
                    if existing:
                        existing.confidence = min(1.0, 0.55 * existing.confidence + 0.45 * max(0.0, vector_hit.score))
                        existing.retrieval_mode = "hybrid"
                        existing.provenance["vector_score"] = vector_hit.score
                    else:
                        merged[vector_hit.document_id] = RetrievalHit(
                            document_id=row["document_id"],
                            text=row["text"],
                            source_locator=row["source_locator"],
                            confidence=max(0.0, vector_hit.score) * 0.85,
                            retrieval_mode="vector",
                            provenance={"database": str(self.path), "vector_score": vector_hit.score},
                            text_hash=row["text_hash"],
                        )
            finally:
                connection.close()
        ordered = sorted(merged.values(), key=lambda hit: (-hit.confidence, hit.document_id))
        return ordered[: max(0, int(limit))]
