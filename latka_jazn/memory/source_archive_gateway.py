from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import sqlite3
import zlib

from latka_jazn.memory.memory_tiers import SourceEvidence
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("source_archive_gateway")
REQUIRED_TABLES = frozenset({"import_sources", "conversations", "nodes", "fts_docs", "message_fts"})


@dataclass(slots=True, frozen=True)
class ArchiveSearchHit:
    conversation_id: str
    node_id: str
    message_id: str | None
    role: str | None
    title: str
    create_time: float | None
    text_sha256: str | None
    rank: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class ArchiveContextNode:
    node_id: str
    parent_node_id: str | None
    message_id: str | None
    role: str | None
    create_time: float | None
    timestamp_status: str
    on_current_path: bool
    branch_id: str
    text: str
    text_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class ArchiveContext:
    conversation_id: str
    title: str
    target_node_id: str
    nodes: tuple[ArchiveContextNode, ...]
    source_import_id: str
    source_sha256: str
    source_name: str
    semantic_tree_sha256: str
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = (
        "Kontekst pochodzi z L0 i jest dowodem źródłowym. Sam odczyt nie tworzy "
        "wspomnienia, refleksji, emocji ani kanonu."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_evidence(self, *, segment_id: str | None = None) -> SourceEvidence:
        node_ids = tuple(node.node_id for node in self.nodes)
        exact_hash = hashlib.sha256(
            json.dumps(
                [(node.node_id, node.text_sha256) for node in self.nodes],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        statuses = {node.timestamp_status for node in self.nodes}
        timestamp_status = statuses.pop() if len(statuses) == 1 else "mixed"
        return SourceEvidence(
            source_type="chat_export_archive",
            source_id=self.source_import_id,
            source_sha256=self.source_sha256,
            conversation_id=self.conversation_id,
            node_ids=node_ids,
            segment_id=segment_id,
            exact_excerpt_sha256=exact_hash,
            timestamp_status=timestamp_status,
            metadata={
                "source_name": self.source_name,
                "target_node_id": self.target_node_id,
                "semantic_tree_sha256": self.semantic_tree_sha256,
                "archive_schema_version": self.schema_version,
            },
        )


class SourceArchiveGateway:
    """Read-only access to importer L0; it has no write or promotion methods."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 10_000) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        uri = f"file:{self.path.as_posix()}?mode=ro"
        self.con = sqlite3.connect(uri, uri=True, timeout=max(1.0, busy_timeout_ms / 1000))
        self.con.row_factory = sqlite3.Row
        self.con.execute(f"PRAGMA busy_timeout={max(1000, int(busy_timeout_ms))}")
        self._verify_schema()

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "SourceArchiveGateway":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _verify_schema(self) -> None:
        present = {
            str(row[0])
            for row in self.con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }
        missing = sorted(REQUIRED_TABLES - present)
        if missing:
            raise sqlite3.DatabaseError(f"L0 archive schema is missing: {', '.join(missing)}")

    def validate(self, *, full: bool = False) -> dict[str, Any]:
        pragma = "integrity_check" if full else "quick_check"
        integrity = str(self.con.execute(f"PRAGMA {pragma}").fetchone()[0])
        foreign_keys = [tuple(row) for row in self.con.execute("PRAGMA foreign_key_check")]
        return {
            "ok": integrity == "ok" and not foreign_keys,
            "integrity_check": integrity,
            "validation_mode": pragma,
            "foreign_key_error_count": len(foreign_keys),
            "path": str(self.path),
            "read_only": True,
            "schema_version": SCHEMA_VERSION,
        }

    def search(self, query: str, *, limit: int = 20) -> list[ArchiveSearchHit]:
        if not query.strip():
            return []
        rows = self.con.execute(
            """SELECT d.conversation_id,d.node_id,d.message_id,d.role,d.title,d.create_time,
                      d.text_sha256,bm25(message_fts) AS rank
                 FROM message_fts JOIN fts_docs d ON d.rowid=message_fts.rowid
                WHERE message_fts MATCH ? ORDER BY rank LIMIT ?""",
            (query, max(1, int(limit))),
        ).fetchall()
        return [ArchiveSearchHit(
            conversation_id=str(row["conversation_id"]),
            node_id=str(row["node_id"]),
            message_id=str(row["message_id"]) if row["message_id"] is not None else None,
            role=str(row["role"]) if row["role"] is not None else None,
            title=str(row["title"] or ""),
            create_time=float(row["create_time"]) if row["create_time"] is not None else None,
            text_sha256=str(row["text_sha256"]) if row["text_sha256"] is not None else None,
            rank=float(row["rank"]),
        ) for row in rows]

    def conversation_payload(self, conversation_id: str) -> dict[str, Any]:
        row = self.con.execute(
            "SELECT payload_codec,payload_blob FROM conversations WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        if row["payload_codec"] != "zlib-json-v1":
            raise ValueError(f"unsupported L0 payload codec: {row['payload_codec']}")
        return json.loads(zlib.decompress(row["payload_blob"]).decode("utf-8"))

    def context_for_node(
        self,
        conversation_id: str,
        node_id: str,
        *,
        ancestor_limit: int = 12,
        include_target: bool = True,
    ) -> ArchiveContext:
        payload = self.conversation_payload(conversation_id)
        mapping = payload.get("mapping") if isinstance(payload.get("mapping"), dict) else {}
        if node_id not in mapping:
            raise KeyError(f"node {node_id!r} is absent from conversation {conversation_id!r}")
        selected: list[str] = []
        current: str | None = node_id
        seen: set[str] = set()
        while current and current not in seen and len(selected) < max(1, int(ancestor_limit)):
            seen.add(current)
            if include_target or current != node_id:
                selected.append(current)
            raw = mapping.get(current) if isinstance(mapping.get(current), dict) else {}
            current = str(raw.get("parent")) if raw.get("parent") else None
        selected.reverse()
        metadata_rows = {
            str(row["node_id"]): row
            for row in self.con.execute(
                f"""SELECT node_id,parent_node_id,message_id,role,create_time,timestamp_status,
                           on_current_path,branch_id,text_sha256
                      FROM nodes WHERE conversation_id=? AND node_id IN ({','.join('?' for _ in selected)})""",
                (conversation_id, *selected),
            )
        } if selected else {}
        nodes: list[ArchiveContextNode] = []
        for selected_id in selected:
            raw = mapping.get(selected_id) if isinstance(mapping.get(selected_id), dict) else {}
            message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
            content = message.get("content") if isinstance(message.get("content"), dict) else {}
            parts = content.get("parts") if isinstance(content.get("parts"), list) else []
            text = "\n".join(str(part) for part in parts if isinstance(part, str)).strip()
            row = metadata_rows.get(selected_id)
            nodes.append(ArchiveContextNode(
                node_id=selected_id,
                parent_node_id=(str(row["parent_node_id"]) if row and row["parent_node_id"] else None),
                message_id=(str(row["message_id"]) if row and row["message_id"] else None),
                role=(str(row["role"]) if row and row["role"] else None),
                create_time=(float(row["create_time"]) if row and row["create_time"] is not None else None),
                timestamp_status=(str(row["timestamp_status"]) if row else "structural_only"),
                on_current_path=bool(row["on_current_path"]) if row else False,
                branch_id=str(row["branch_id"]) if row else "unknown",
                text=text,
                text_sha256=(str(row["text_sha256"]) if row and row["text_sha256"] else None),
            ))
        source = self.con.execute(
            """SELECT c.title,c.semantic_tree_sha256,c.last_seen_import_id,s.sha256,s.source_name
                 FROM conversations c JOIN import_sources s ON s.import_id=c.last_seen_import_id
                WHERE c.conversation_id=?""",
            (conversation_id,),
        ).fetchone()
        if source is None:
            raise sqlite3.DatabaseError("conversation provenance is missing")
        return ArchiveContext(
            conversation_id=conversation_id,
            title=str(source["title"] or ""),
            target_node_id=node_id,
            nodes=tuple(nodes),
            source_import_id=str(source["last_seen_import_id"]),
            source_sha256=str(source["sha256"]),
            source_name=str(source["source_name"]),
            semantic_tree_sha256=str(source["semantic_tree_sha256"]),
        )
