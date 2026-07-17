from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
import hashlib
import json

from latka_jazn.tools.chat_export_models import ConversationGraph, ConversationPlan, MessageNode
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("chat_export_dedupe")


@dataclass(slots=True, frozen=True)
class ActiveConversationState:
    conversation_id: str
    semantic_tree_sha256: str
    node_hashes: Mapping[str, str | None]
    node_count: int

    @classmethod
    def from_graph(cls, graph: ConversationGraph) -> "ActiveConversationState":
        return cls(
            conversation_id=graph.conversation_id,
            semantic_tree_sha256=graph.semantic_tree_sha256,
            node_hashes={node.node_id: stable_node_hash(node) for node in graph.nodes},
            node_count=graph.node_count,
        )


@dataclass(slots=True, frozen=True)
class ExportHashMatch:
    import_id: str
    source_sha256: str
    source_name: str
    status: str


def stable_node_hash(node: MessageNode) -> str:
    """Hash only durable semantic fields, never volatile export metadata."""
    payload = {
        "message_id": node.message_id,
        "role": node.role,
        "content_type": node.content_type,
        "text_sha256": node.text_sha256,
        "assets": sorted(
            (asset.asset_pointer, asset.content_type, asset.mime_type)
            for asset in node.assets
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _incoming_hashes(nodes: Iterable[MessageNode]) -> dict[str, str | None]:
    return {node.node_id: stable_node_hash(node) for node in nodes}


def plan_conversation(
    graph: ConversationGraph,
    active: ActiveConversationState | None,
) -> ConversationPlan:
    """Classify one incoming tree in O(incoming_nodes + active_nodes).

    Node identity is the stable ChatGPT mapping key. A node is treated as the same
    semantic node only when its durable semantic fields are equal. Raw metadata
    differences remain available in the source archive but do not create false
    conversation revisions.
    """
    if active is None:
        return ConversationPlan(
            conversation_id=graph.conversation_id,
            relation="new",
            incoming_semantic_tree_sha256=graph.semantic_tree_sha256,
            active_semantic_tree_sha256=None,
            incoming_node_count=graph.node_count,
            active_node_count=0,
            added_node_ids=tuple(node.node_id for node in graph.nodes),
            reason="conversation_id is not present in the archive",
        )
    if graph.conversation_id != active.conversation_id:
        raise ValueError("incoming graph and active state have different conversation_id")
    if graph.semantic_tree_sha256 == active.semantic_tree_sha256:
        return ConversationPlan(
            conversation_id=graph.conversation_id,
            relation="identical",
            incoming_semantic_tree_sha256=graph.semantic_tree_sha256,
            active_semantic_tree_sha256=active.semantic_tree_sha256,
            incoming_node_count=graph.node_count,
            active_node_count=active.node_count,
            reason="semantic tree fingerprint is unchanged",
        )

    incoming = _incoming_hashes(graph.nodes)
    active_hashes = dict(active.node_hashes)
    incoming_ids = set(incoming)
    active_ids = set(active_hashes)
    shared = incoming_ids & active_ids
    changed = tuple(sorted(node_id for node_id in shared if incoming[node_id] != active_hashes[node_id]))
    added = tuple(sorted(incoming_ids - active_ids))
    missing = tuple(sorted(active_ids - incoming_ids))

    if not changed and incoming_ids == active_ids:
        return ConversationPlan(
            conversation_id=graph.conversation_id,
            relation="identical",
            incoming_semantic_tree_sha256=graph.semantic_tree_sha256,
            active_semantic_tree_sha256=active.semantic_tree_sha256,
            incoming_node_count=graph.node_count,
            active_node_count=active.node_count,
            reason="stable node identities and semantic contents are unchanged",
        )
    if not changed and incoming_ids < active_ids:
        return ConversationPlan(
            conversation_id=graph.conversation_id,
            relation="older_subset",
            incoming_semantic_tree_sha256=graph.semantic_tree_sha256,
            active_semantic_tree_sha256=active.semantic_tree_sha256,
            incoming_node_count=graph.node_count,
            active_node_count=active.node_count,
            missing_from_incoming_node_ids=missing,
            reason="all incoming nodes already exist unchanged in the active tree",
        )
    if not changed and active_ids < incoming_ids:
        return ConversationPlan(
            conversation_id=graph.conversation_id,
            relation="extends_active",
            incoming_semantic_tree_sha256=graph.semantic_tree_sha256,
            active_semantic_tree_sha256=active.semantic_tree_sha256,
            incoming_node_count=graph.node_count,
            active_node_count=active.node_count,
            added_node_ids=added,
            reason="incoming tree contains every active node unchanged and adds nodes",
        )
    return ConversationPlan(
        conversation_id=graph.conversation_id,
        relation="divergent",
        incoming_semantic_tree_sha256=graph.semantic_tree_sha256,
        active_semantic_tree_sha256=active.semantic_tree_sha256,
        incoming_node_count=graph.node_count,
        active_node_count=active.node_count,
        added_node_ids=added,
        changed_node_ids=changed,
        missing_from_incoming_node_ids=missing,
        reason=(
            "one or more existing node payloads changed"
            if changed
            else "both trees contain nodes absent from the other tree"
        ),
    )


def plan_export(
    graphs: Iterable[ConversationGraph],
    active_states: Mapping[str, ActiveConversationState],
) -> list[ConversationPlan]:
    return [plan_conversation(graph, active_states.get(graph.conversation_id)) for graph in graphs]


def summarize_relations(plans: Iterable[ConversationPlan]) -> dict[str, int]:
    counters: dict[str, int] = {}
    for plan in plans:
        counters[plan.relation] = counters.get(plan.relation, 0) + 1
    return counters


def active_states_from_rows(
    conversation_rows: Iterable[Mapping[str, Any]],
    node_rows: Iterable[Mapping[str, Any]],
) -> dict[str, ActiveConversationState]:
    """Build a bulk index from SQLite rows without per-conversation queries."""
    nodes: dict[str, dict[str, str | None]] = {}
    for row in node_rows:
        conversation_id = str(row["conversation_id"])
        nodes.setdefault(conversation_id, {})[str(row["node_id"])] = (
            str(row["semantic_payload_sha256"])
            if row.get("semantic_payload_sha256") is not None
            else None
        )
    result: dict[str, ActiveConversationState] = {}
    for row in conversation_rows:
        conversation_id = str(row["conversation_id"])
        node_hashes = nodes.get(conversation_id, {})
        result[conversation_id] = ActiveConversationState(
            conversation_id=conversation_id,
            semantic_tree_sha256=str(row["semantic_tree_sha256"]),
            node_hashes=node_hashes,
            node_count=int(row.get("node_count") or len(node_hashes)),
        )
    return result
