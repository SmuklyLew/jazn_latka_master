from __future__ import annotations

from typing import Any
import hashlib
import json
import zlib

from latka_jazn.tools.chat_export_models import ConversationGraph, MessageNode
from latka_jazn.tools import chat_export_reader as reader_module
from latka_jazn.tools import chat_export_store as store_module

_INSTALLED = False
_ORIGINAL_STORE_INIT = store_module.ChatExportArchiveStore.__init__


def canonical_json_sha256(value: Any, *, strip_volatile: bool = False) -> str:
    """Hash canonical JSON without materializing a second full byte string."""
    digest = hashlib.sha256()

    def emit(item: Any) -> None:
        if isinstance(item, dict):
            digest.update(b"{")
            first = True
            for key in sorted(item, key=lambda part: str(part)):
                key_text = str(key)
                if strip_volatile and key_text in reader_module.VOLATILE_METADATA_KEYS:
                    continue
                if not first:
                    digest.update(b",")
                first = False
                digest.update(json.dumps(key_text, ensure_ascii=False).encode("utf-8"))
                digest.update(b":")
                emit(item[key])
            digest.update(b"}")
            return
        if isinstance(item, (list, tuple)):
            digest.update(b"[")
            for index, child in enumerate(item):
                if index:
                    digest.update(b",")
                emit(child)
            digest.update(b"]")
            return
        digest.update(json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))

    emit(value)
    return digest.hexdigest()


def build_conversation_graph(conversation: dict[str, Any], *, assets_map: dict[str, str] | None = None) -> ConversationGraph:
    assets_map = assets_map or {}
    conversation_id = str(conversation.get("id") or conversation.get("conversation_id") or "").strip()
    if not conversation_id:
        raise ValueError("conversation is missing id")
    mapping = conversation.get("mapping") if isinstance(conversation.get("mapping"), dict) else {}
    mapping = {str(key): value for key, value in mapping.items()}
    current_node_id = str(conversation.get("current_node")) if conversation.get("current_node") else None
    current_path = reader_module._current_path(mapping, current_node_id)
    current_set = set(current_path)
    order = reader_module._structural_order(mapping)
    ordinals = {node_id: index for index, node_id in enumerate(order)}
    branch_ids = reader_module._branch_ids(mapping, order)
    branch_points = tuple(
        node_id for node_id, node in mapping.items()
        if isinstance(node, dict) and len(node.get("children") or []) > 1
    )
    nodes: list[MessageNode] = []
    times: list[float] = []

    for node_id in order:
        raw_node = mapping.get(node_id) if isinstance(mapping.get(node_id), dict) else {}
        message = raw_node.get("message") if isinstance(raw_node.get("message"), dict) else {}
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        role = str(author.get("role")) if author.get("role") else None
        create_time = message.get("create_time")
        try:
            create_time = float(create_time) if create_time is not None else None
        except (TypeError, ValueError):
            create_time = None
        if create_time is not None:
            times.append(create_time)
        timestamp_status = "exact" if create_time is not None else ("structural_only" if raw_node else "missing")
        text, assets, content_type = reader_module._message_text_and_assets(message, assets_map)
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
        raw_payload_hash = canonical_json_sha256(message) if message else None
        semantic_payload_hash = canonical_json_sha256(message, strip_volatile=True) if message else None
        parent = str(raw_node.get("parent")) if raw_node.get("parent") else None
        children = tuple(str(child) for child in (raw_node.get("children") or []))
        nodes.append(MessageNode(
            conversation_id=conversation_id,
            node_id=node_id,
            parent_node_id=parent,
            children=children,
            message_id=str(message.get("id")) if message.get("id") else None,
            role=role,
            create_time=create_time,
            timestamp_status=timestamp_status,
            content_type=content_type,
            text=text,
            text_sha256=text_hash,
            semantic_payload_sha256=semantic_payload_hash,
            raw_payload_sha256=raw_payload_hash,
            structural_ordinal=ordinals[node_id],
            on_current_path=node_id in current_set,
            branch_id=branch_ids[node_id],
            assets=assets,
        ))

    raw_hash = canonical_json_sha256(conversation)
    semantic_tree = {
        "conversation_id": conversation_id,
        "title": conversation.get("title"),
        "current_node": current_node_id,
        "nodes": [
            {
                "node_id": node.node_id,
                "parent": node.parent_node_id,
                "children": node.children,
                "message_id": node.message_id,
                "role": node.role,
                "content_type": node.content_type,
                "create_time": node.create_time,
                "semantic_payload_sha256": node.semantic_payload_sha256,
                "assets": [asset.asset_pointer for asset in node.assets],
            }
            for node in nodes
        ],
    }
    semantic_hash = canonical_json_sha256(semantic_tree)
    return ConversationGraph(
        conversation_id=conversation_id,
        title=str(conversation.get("title") or ""),
        create_time=float(conversation["create_time"]) if conversation.get("create_time") is not None else None,
        update_time=float(conversation["update_time"]) if conversation.get("update_time") is not None else None,
        current_node_id=current_node_id,
        nodes=tuple(nodes),
        current_path=current_path,
        branch_points=branch_points,
        raw_tree_sha256=raw_hash,
        semantic_tree_sha256=semantic_hash,
        message_count=sum(1 for node in nodes if node.message_id),
        node_count=len(nodes),
        first_message_time=min(times) if times else None,
        last_message_time=max(times) if times else None,
        source_payload=conversation,
    )


def compressed_payload(graph: ConversationGraph) -> tuple[bytes, int]:
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    compressor = zlib.compressobj(level=6)
    chunks: list[bytes] = []
    raw_size = 0
    for text_chunk in encoder.iterencode(graph.source_payload):
        encoded = text_chunk.encode("utf-8")
        raw_size += len(encoded)
        compressed = compressor.compress(encoded)
        if compressed:
            chunks.append(compressed)
    tail = compressor.flush()
    if tail:
        chunks.append(tail)
    return b"".join(chunks), raw_size


def _store_init(self, path, *, busy_timeout_ms: int = 30_000) -> None:
    _ORIGINAL_STORE_INIT(self, path, busy_timeout_ms=busy_timeout_ms)
    self.con.execute("PRAGMA temp_store=FILE")
    self.con.execute("PRAGMA cache_size=-24576")


def install_performance_overrides() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    reader_module.build_conversation_graph = build_conversation_graph
    store_module._compressed_payload = compressed_payload
    store_module.ChatExportArchiveStore.__init__ = _store_init
    _INSTALLED = True
