from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator, TextIO
import hashlib
import io
import json
import re
import zipfile

from latka_jazn.tools.chat_export_models import (
    AssetReference,
    ConversationGraph,
    ExportInspectionReport,
    ExportSourceInfo,
    MessageNode,
)
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("chat_export_reader")
VOLATILE_METADATA_KEYS = frozenset({"lpe_keep_patch_ijhw"})
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_volatile(item) for key, item in value.items() if key not in VOLATILE_METADATA_KEYS}
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _safe_member_name(name: str) -> str:
    value = str(name or "").replace("\\", "/")
    parts = PurePosixPath(value).parts
    if not value or value.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"unsafe archive entry: {name!r}")
    if re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"absolute archive entry is forbidden: {name!r}")
    return value


def iter_json_array_objects(stream: TextIO, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[dict[str, Any]]:
    """Yield objects from a top-level JSON array without retaining the whole file."""
    decoder = json.JSONDecoder()
    buffer = ""
    position = 0
    started = False
    eof = False

    def read_more() -> bool:
        nonlocal buffer, eof
        chunk = stream.read(chunk_size)
        if not chunk:
            eof = True
            return False
        buffer += chunk
        return True

    while True:
        if position >= len(buffer) and not eof:
            buffer = ""
            position = 0
            read_more()
        while position < len(buffer) and buffer[position].isspace():
            position += 1
        if not started:
            if position >= len(buffer) and not eof:
                read_more()
                continue
            if position >= len(buffer) or buffer[position] != "[":
                raise ValueError("conversations.json must contain a top-level array")
            started = True
            position += 1
            continue
        while position < len(buffer) and (buffer[position].isspace() or buffer[position] == ","):
            position += 1
        if position < len(buffer) and buffer[position] == "]":
            return
        if position >= len(buffer):
            if eof:
                raise ValueError("unexpected end of conversations.json")
            buffer = buffer[position:]
            position = 0
            read_more()
            continue
        try:
            item, end = decoder.raw_decode(buffer, position)
        except json.JSONDecodeError:
            if eof:
                raise
            buffer = buffer[position:]
            position = 0
            read_more()
            continue
        position = end
        if isinstance(item, dict):
            yield item
        if position > chunk_size:
            buffer = buffer[position:]
            position = 0


def _read_js_object_assignment(handle: BinaryIO, marker: bytes, *, chunk_size: int = 1024 * 1024) -> dict[str, str]:
    """Read a JSON object assigned to a JS variable without loading all chat.html."""
    buffer = b""
    start = -1
    while start < 0:
        chunk = handle.read(chunk_size)
        if not chunk:
            return {}
        buffer += chunk
        marker_pos = buffer.find(marker)
        if marker_pos >= 0:
            brace = buffer.find(b"{", marker_pos + len(marker))
            if brace >= 0:
                start = brace
                buffer = buffer[start:]
                break
        if len(buffer) > len(marker) + 128:
            buffer = buffer[-(len(marker) + 128):]

    depth = 0
    in_string = False
    escaped = False
    index = 0
    while True:
        while index < len(buffer):
            byte = buffer[index]
            if in_string:
                if escaped:
                    escaped = False
                elif byte == 0x5C:
                    escaped = True
                elif byte == 0x22:
                    in_string = False
            else:
                if byte == 0x22:
                    in_string = True
                elif byte == 0x7B:
                    depth += 1
                elif byte == 0x7D:
                    depth -= 1
                    if depth == 0:
                        raw = buffer[: index + 1]
                        value = json.loads(raw.decode("utf-8"))
                        return {str(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}
            index += 1
        chunk = handle.read(chunk_size)
        if not chunk:
            raise ValueError("unterminated assetsJson object in chat.html")
        buffer += chunk


def _message_text_and_assets(message: dict[str, Any], assets_map: dict[str, str]) -> tuple[str, tuple[AssetReference, ...], str]:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    content_type = str(content.get("content_type") or "unknown")
    parts = content.get("parts") if isinstance(content.get("parts"), list) else []
    text_parts: list[str] = []
    assets: dict[str, AssetReference] = {}

    def collect(value: Any) -> None:
        if isinstance(value, str):
            text_parts.append(value)
            return
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, dict):
            return
        if isinstance(value.get("text"), str):
            text_parts.append(value["text"])
        pointers: list[tuple[str, str | None]] = []
        for key in ("asset_pointer", "file_id", "image_asset_pointer", "audio_asset_pointer", "video_asset_pointer"):
            if value.get(key):
                pointers.append((str(value[key]), key))
        for pointer, pointer_kind in pointers:
            assets[pointer] = AssetReference(
                asset_pointer=pointer,
                original_filename=assets_map.get(pointer),
                content_type=str(value.get("content_type") or pointer_kind or content_type),
                mime_type=str(value.get("mime_type")) if value.get("mime_type") else None,
            )
        for key, item in value.items():
            if key not in {"text", "asset_pointer", "file_id", "image_asset_pointer", "audio_asset_pointer", "video_asset_pointer"}:
                if isinstance(item, (dict, list)):
                    collect(item)

    for part in parts:
        collect(part)
    if not text_parts and isinstance(content.get("text"), str):
        text_parts.append(content["text"])
    text = "\n".join(part for part in text_parts if part).strip()
    return text, tuple(assets.values()), content_type


def _current_path(mapping: dict[str, Any], current_node_id: str | None) -> tuple[str, ...]:
    if not current_node_id:
        return ()
    path: list[str] = []
    seen: set[str] = set()
    node_id: str | None = current_node_id
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        path.append(node_id)
        node = mapping.get(node_id) if isinstance(mapping.get(node_id), dict) else {}
        parent = node.get("parent")
        node_id = str(parent) if parent else None
    path.reverse()
    return tuple(path)


def _structural_order(mapping: dict[str, Any]) -> tuple[str, ...]:
    roots = [
        node_id
        for node_id, node in mapping.items()
        if not isinstance(node, dict) or not node.get("parent") or str(node.get("parent")) not in mapping
    ]
    order: list[str] = []
    seen: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in seen or node_id not in mapping:
            return
        seen.add(node_id)
        order.append(node_id)
        node = mapping.get(node_id) if isinstance(mapping.get(node_id), dict) else {}
        for child in node.get("children") or []:
            visit(str(child))

    for root in roots:
        visit(str(root))
    for node_id in mapping:
        visit(str(node_id))
    return tuple(order)


def _branch_ids(mapping: dict[str, Any], order: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for node_id in order:
        node = mapping.get(node_id) if isinstance(mapping.get(node_id), dict) else {}
        parent = str(node.get("parent")) if node.get("parent") else None
        if parent and parent in mapping:
            siblings = (mapping.get(parent) or {}).get("children") or []
            result[node_id] = f"branch:{parent}:{node_id}" if len(siblings) > 1 else result.get(parent, "main")
        else:
            result[node_id] = "main"
    return result


def build_conversation_graph(conversation: dict[str, Any], *, assets_map: dict[str, str] | None = None) -> ConversationGraph:
    assets_map = assets_map or {}
    conversation_id = str(conversation.get("id") or conversation.get("conversation_id") or "").strip()
    if not conversation_id:
        raise ValueError("conversation is missing id")
    mapping = conversation.get("mapping") if isinstance(conversation.get("mapping"), dict) else {}
    mapping = {str(key): value for key, value in mapping.items()}
    current_node_id = str(conversation.get("current_node")) if conversation.get("current_node") else None
    current_path = _current_path(mapping, current_node_id)
    current_set = set(current_path)
    order = _structural_order(mapping)
    ordinals = {node_id: index for index, node_id in enumerate(order)}
    branch_ids = _branch_ids(mapping, order)
    branch_points = tuple(
        node_id for node_id, node in mapping.items() if isinstance(node, dict) and len(node.get("children") or []) > 1
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
        text, assets, content_type = _message_text_and_assets(message, assets_map)
        text_hash = _sha256_bytes(text.encode("utf-8")) if text else None
        raw_payload_hash = _sha256_bytes(_canonical_json_bytes(message)) if message else None
        semantic_payload_hash = _sha256_bytes(_canonical_json_bytes(_strip_volatile(message))) if message else None
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

    raw_hash = _sha256_bytes(_canonical_json_bytes(conversation))
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
    semantic_hash = _sha256_bytes(_canonical_json_bytes(semantic_tree))
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


class ChatExportReader:
    """Read ChatGPT exports without treating rendered HTML as canonical conversation order."""

    def __init__(self, source: str | Path, *, verify_crc: bool = True) -> None:
        self.path = Path(source).expanduser().resolve()
        self.verify_crc = bool(verify_crc)
        self._zip: zipfile.ZipFile | None = None
        self.info = self._inspect_source()
        self._assets_map: dict[str, str] | None = None

    def _inspect_source(self) -> ExportSourceInfo:
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        if self.path.is_dir():
            json_path = next(iter(sorted(self.path.glob("**/conversations.json"))), None)
            html_path = next(iter(sorted(self.path.glob("**/chat.html"))), None)
            if json_path is None and html_path is None:
                raise ValueError("directory does not contain conversations.json or chat.html")
            digest = hashlib.sha256()
            size = 0
            for item in (json_path, html_path):
                if item and item.is_file():
                    item_hash = sha256_file(item)
                    digest.update(item.relative_to(self.path).as_posix().encode("utf-8"))
                    digest.update(item_hash.encode("ascii"))
                    size += item.stat().st_size
            return ExportSourceInfo(
                str(self.path), self.path.name, "directory", digest.hexdigest(), size,
                str(json_path) if json_path else None, str(html_path) if html_path else None, False, True,
            )
        suffix = self.path.suffix.lower()
        source_hash = sha256_file(self.path)
        size = self.path.stat().st_size
        if suffix == ".zip":
            archive = zipfile.ZipFile(self.path, "r")
            try:
                names = [_safe_member_name(info.filename) for info in archive.infolist() if not info.is_dir()]
                lowered = {name.lower(): name for name in names}
                conversations = next((name for low, name in lowered.items() if low.endswith("conversations.json")), None)
                html = next((name for low, name in lowered.items() if low.endswith("chat.html")), None)
                if conversations is None and html is None:
                    raise ValueError("ZIP does not contain conversations.json or chat.html")
                crc_ok = True
                if self.verify_crc:
                    bad = archive.testzip()
                    crc_ok = bad is None
                    if bad:
                        raise zipfile.BadZipFile(f"CRC failure in {bad}")
                self._zip = archive
                return ExportSourceInfo(
                    str(self.path), self.path.name, "zip", source_hash, size,
                    conversations, html, self.verify_crc, crc_ok,
                )
            except Exception:
                archive.close()
                raise
        if suffix == ".json":
            return ExportSourceInfo(str(self.path), self.path.name, "json", source_hash, size, str(self.path), None, False, True)
        if suffix in {".html", ".htm"}:
            return ExportSourceInfo(str(self.path), self.path.name, "html", source_hash, size, None, str(self.path), False, True)
        raise ValueError(f"unsupported export source: {self.path}")

    @contextmanager
    def _conversation_stream(self) -> Iterator[TextIO]:
        if self.info.source_kind == "zip" and self.info.conversations_member:
            assert self._zip is not None
            raw = self._zip.open(self.info.conversations_member, "r")
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="strict", newline="")
            try:
                yield text
            finally:
                text.close()
            return
        path = Path(self.info.conversations_member or "")
        if self.info.source_kind == "directory" and path.is_file():
            with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as handle:
                yield handle
            return
        if self.info.source_kind == "json":
            with self.path.open("r", encoding="utf-8-sig", errors="strict", newline="") as handle:
                yield handle
            return
        raise ValueError("canonical conversations.json is not available")

    def assets_map(self) -> dict[str, str]:
        if self._assets_map is not None:
            return dict(self._assets_map)
        result: dict[str, str] = {}
        if self.info.source_kind == "zip" and self.info.html_member:
            assert self._zip is not None
            with self._zip.open(self.info.html_member, "r") as handle:
                result = _read_js_object_assignment(handle, b"assetsJson")
        elif self.info.html_member:
            html_path = Path(self.info.html_member)
            if html_path.is_file():
                with html_path.open("rb") as handle:
                    result = _read_js_object_assignment(handle, b"assetsJson")
        self._assets_map = result
        return dict(result)

    def iter_raw_conversations(self) -> Iterator[dict[str, Any]]:
        with self._conversation_stream() as stream:
            yield from iter_json_array_objects(stream)

    def iter_graphs(self) -> Iterator[ConversationGraph]:
        assets = self.assets_map()
        for conversation in self.iter_raw_conversations():
            yield build_conversation_graph(conversation, assets_map=assets)

    def inspect(self) -> ExportInspectionReport:
        report = ExportInspectionReport(source=self.info, relation="parsed_export")
        all_times: list[float] = []
        for graph in self.iter_graphs():
            report.conversation_count += 1
            report.node_count += graph.node_count
            report.message_count += graph.message_count
            report.branch_point_count += len(graph.branch_points)
            report.alternate_branch_node_count += sum(1 for node in graph.nodes if not node.on_current_path)
            report.asset_reference_count += sum(len(node.assets) for node in graph.nodes)
            report.structural_only_timestamp_count += sum(1 for node in graph.nodes if node.timestamp_status == "structural_only")
            report.missing_timestamp_count += sum(1 for node in graph.nodes if node.timestamp_status == "missing")
            if graph.first_message_time is not None:
                all_times.append(graph.first_message_time)
            if graph.last_message_time is not None:
                all_times.append(graph.last_message_time)
        report.first_message_time = min(all_times) if all_times else None
        report.last_message_time = max(all_times) if all_times else None
        if not self.info.conversations_member:
            report.errors.append("conversations.json is missing; rendered HTML alone is not a lossless source")
        return report

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> "ChatExportReader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
