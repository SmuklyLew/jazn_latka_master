from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("chat_export_models")

SourceKind = Literal["zip", "directory", "json", "html"]
TimestampStatus = Literal["exact", "structural_only", "missing"]
ConversationRelation = Literal[
    "new",
    "identical",
    "older_subset",
    "extends_active",
    "divergent",
]
ExportRelation = Literal[
    "new_export",
    "identical_export_duplicate",
    "parsed_export",
    "invalid_export",
]


@dataclass(slots=True, frozen=True)
class ExportSourceInfo:
    path: str
    source_name: str
    source_kind: SourceKind
    sha256: str
    size_bytes: int
    conversations_member: str | None
    html_member: str | None
    crc_checked: bool
    crc_ok: bool
    conversation_members: tuple[str, ...] = ()
    shared_conversations_members: tuple[str, ...] = ()
    format_version: str = "chatgpt-export/unknown"
    schema_version: str = SCHEMA_VERSION

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).expanduser().resolve()

    @property
    def has_canonical_conversations(self) -> bool:
        return bool(self.conversation_members or self.conversations_member)

    @property
    def has_shared_link_metadata(self) -> bool:
        return bool(self.shared_conversations_members)

    @property
    def shared_metadata_only(self) -> bool:
        return self.has_shared_link_metadata and not self.has_canonical_conversations

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["has_canonical_conversations"] = self.has_canonical_conversations
        data["has_shared_link_metadata"] = self.has_shared_link_metadata
        data["shared_metadata_only"] = self.shared_metadata_only
        return data


@dataclass(slots=True, frozen=True)
class AssetReference:
    asset_pointer: str
    original_filename: str | None
    content_type: str | None
    mime_type: str | None
    availability_status: str = "referenced_only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class MessageNode:
    conversation_id: str
    node_id: str
    parent_node_id: str | None
    children: tuple[str, ...]
    message_id: str | None
    role: str | None
    create_time: float | None
    timestamp_status: TimestampStatus
    content_type: str | None
    text: str
    text_sha256: str | None
    semantic_payload_sha256: str | None
    raw_payload_sha256: str | None
    structural_ordinal: int
    on_current_path: bool
    branch_id: str
    assets: tuple[AssetReference, ...] = ()

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not include_text:
            data.pop("text", None)
        return data


@dataclass(slots=True, frozen=True)
class ConversationGraph:
    conversation_id: str
    title: str
    create_time: float | None
    update_time: float | None
    current_node_id: str | None
    nodes: tuple[MessageNode, ...]
    current_path: tuple[str, ...]
    branch_points: tuple[str, ...]
    raw_tree_sha256: str
    semantic_tree_sha256: str
    message_count: int
    node_count: int
    first_message_time: float | None
    last_message_time: float | None
    source_payload: dict[str, Any] = field(repr=False)

    def node_index(self) -> dict[str, MessageNode]:
        return {node.node_id: node for node in self.nodes}

    def to_dict(self, *, include_payload: bool = False, include_text: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "conversation_id": self.conversation_id,
            "title": self.title,
            "create_time": self.create_time,
            "update_time": self.update_time,
            "current_node_id": self.current_node_id,
            "current_path": list(self.current_path),
            "branch_points": list(self.branch_points),
            "raw_tree_sha256": self.raw_tree_sha256,
            "semantic_tree_sha256": self.semantic_tree_sha256,
            "message_count": self.message_count,
            "node_count": self.node_count,
            "first_message_time": self.first_message_time,
            "last_message_time": self.last_message_time,
            "nodes": [node.to_dict(include_text=include_text) for node in self.nodes],
        }
        if include_payload:
            data["source_payload"] = self.source_payload
        return data


@dataclass(slots=True, frozen=True)
class ConversationPlan:
    conversation_id: str
    relation: ConversationRelation
    incoming_semantic_tree_sha256: str
    active_semantic_tree_sha256: str | None
    incoming_node_count: int
    active_node_count: int
    added_node_ids: tuple[str, ...] = ()
    changed_node_ids: tuple[str, ...] = ()
    missing_from_incoming_node_ids: tuple[str, ...] = ()
    reason: str = ""

    @property
    def write_payload_required(self) -> bool:
        return self.relation in {"new", "extends_active", "divergent"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExportInspectionReport:
    source: ExportSourceInfo
    relation: ExportRelation
    conversation_count: int = 0
    node_count: int = 0
    message_count: int = 0
    missing_timestamp_count: int = 0
    structural_only_timestamp_count: int = 0
    branch_point_count: int = 0
    alternate_branch_node_count: int = 0
    asset_reference_count: int = 0
    canonical_conversation_member_count: int = 0
    shared_conversation_member_count: int = 0
    skipped_metadata_record_count: int = 0
    duplicate_conversation_record_count: int = 0
    first_message_time: float | None = None
    last_message_time: float | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return not self.errors and self.source.crc_ok

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        return data


@dataclass(slots=True)
class ImportPlan:
    source: ExportSourceInfo
    export_relation: ExportRelation
    conversations: list[ConversationPlan]
    duplicate_import_id: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def counters(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in self.conversations:
            result[item.relation] = result.get(item.relation, 0) + 1
        return result

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source.to_dict(),
            "export_relation": self.export_relation,
            "duplicate_import_id": self.duplicate_import_id,
            "conversation_counters": self.counters(),
            "conversations": [item.to_dict() for item in self.conversations],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "ok": self.ok,
        }


@dataclass(slots=True)
class ImportResult:
    import_id: str | None
    source_sha256: str
    status: str
    export_relation: ExportRelation
    conversation_counters: dict[str, int]
    inserted_conversations: int = 0
    updated_conversations: int = 0
    inserted_nodes: int = 0
    inserted_fts_documents: int = 0
    inserted_topic_segments: int = 0
    inserted_asset_references: int = 0
    queued_memory_candidates: int = 0
    database_path: str | None = None
    elapsed_seconds: float | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return not self.errors and self.status in {
            "imported",
            "identical_export_duplicate",
            "dry_run_ok",
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        return data
