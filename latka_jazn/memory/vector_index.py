from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from latka_jazn.memory.embedding_provider import EmbeddingProvider
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("vector_index")


@dataclass(slots=True)
class VectorHit:
    document_id: str
    score: float
    source_locator: str
    text_hash: str
    metadata: dict[str, Any]
    retrieval_mode: str = "vector"
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


class VectorIndex:
    def __init__(self, path: Path | str, provider: EmbeddingProvider) -> None:
        self.path = Path(path)
        self.provider = provider

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS vector_documents(
              document_id TEXT PRIMARY KEY,
              source_locator TEXT NOT NULL,
              text_hash TEXT NOT NULL,
              vector_json TEXT NOT NULL,
              metadata_json TEXT NOT NULL
            )
            """
        )
        return connection

    def rebuild(self, documents: Iterable[dict[str, Any]]) -> int:
        rows = list(documents)
        texts = [str(row.get("text") or "") for row in rows]
        vectors = self.provider.embed(texts) if rows else []
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM vector_documents")
                for row, vector in zip(rows, vectors):
                    connection.execute(
                        """
                        INSERT INTO vector_documents(document_id,source_locator,text_hash,vector_json,metadata_json)
                        VALUES(?,?,?,?,?)
                        """,
                        (
                            str(row["document_id"]),
                            str(row.get("source_locator") or row["document_id"]),
                            str(row["text_hash"]),
                            json.dumps(vector, separators=(",", ":")),
                            json.dumps(row.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                        ),
                    )
            return len(rows)
        finally:
            connection.close()

    def search(self, query: str, *, limit: int = 8, exclude_text_hash: str | None = None) -> list[VectorHit]:
        query_vector = self.provider.embed([query])[0]
        connection = self._connect()
        try:
            hits: list[VectorHit] = []
            for row in connection.execute("SELECT * FROM vector_documents").fetchall():
                if exclude_text_hash and row["text_hash"] == exclude_text_hash:
                    continue
                score = _cosine(query_vector, json.loads(row["vector_json"]))
                hits.append(
                    VectorHit(
                        document_id=row["document_id"],
                        score=score,
                        source_locator=row["source_locator"],
                        text_hash=row["text_hash"],
                        metadata=json.loads(row["metadata_json"]),
                    )
                )
            hits.sort(key=lambda hit: (-hit.score, hit.document_id))
            return hits[: max(0, int(limit))]
        finally:
            connection.close()
