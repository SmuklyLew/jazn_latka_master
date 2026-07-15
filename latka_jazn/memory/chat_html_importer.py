from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from latka_jazn.core.clock import resolve_timezone
import hashlib
import io
import json
import re
import sqlite3
from typing import Any, Iterator

from latka_jazn.memory.store import MemoryStore

JSON_MARKER = b"var jsonData = "
TEXT_IMPORT_CHAR_LIMIT = 4_000


@dataclass(slots=True)
class ChatHtmlImportReport:
    status: str
    path: str
    conversations_seen: int = 0
    conversations_imported: int = 0
    messages_imported: int = 0
    skipped_messages: int = 0
    errors: list[str] | None = None
    sha256: str | None = None
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "path": self.path,
            "conversations_seen": self.conversations_seen,
            "conversations_imported": self.conversations_imported,
            "messages_imported": self.messages_imported,
            "skipped_messages": self.skipped_messages,
            "errors": self.errors or [],
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def warsaw_time(value: Any, tz_name: str = "Europe/Warsaw") -> str | None:
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(ts, timezone.utc).astimezone(resolve_timezone(tz_name)).isoformat()
    except Exception:
        return None


def _text_reader_after_marker(path: Path) -> io.TextIOWrapper:
    marker = JSON_MARKER
    with path.open("rb") as probe:
        offset = 0
        tail = b""
        while True:
            chunk = probe.read(1024 * 1024)
            if not chunk:
                raise ValueError("Nie znaleziono `var jsonData =` w chat.html")
            hay = tail + chunk
            idx = hay.find(marker)
            if idx >= 0:
                absolute = offset - len(tail) + idx + len(marker)
                raw = path.open("rb")
                raw.seek(absolute)
                return io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            offset += len(chunk)
            tail = hay[-len(marker) - 32 :]


def _class_names(attrs: list[tuple[str, str | None]]) -> set[str]:
    values: set[str] = set()
    for name, value in attrs:
        if name.lower() == "class" and value:
            values.update(part.strip().lower() for part in value.split() if part.strip())
    return values


def _clean_dom_text(parts: list[str]) -> str:
    text = "".join(parts)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+\n", "\n", text)
    text = re.sub(r"\n[ \t\f\v]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _role_from_rendered_author(author: str) -> str:
    lowered = author.strip().lower()
    if lowered == "user":
        return "user"
    if lowered in {"assistant", "chatgpt", "latka", "latka/jazn", "latka jazn"}:
        return "assistant"
    if lowered in {"system", "tool"}:
        return lowered
    return "unknown"


def _rendered_dom_conversation_id(index: int, title: str, messages: list[dict[str, str]]) -> str:
    seed = json.dumps(
        {
            "index": index,
            "title": title,
            "message_count": len(messages),
            "first_author": messages[0].get("author") if messages else "",
            "first_text": (messages[0].get("text") or "")[:200] if messages else "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"rendered-dom-{index:06d}-{hashlib.sha256(seed.encode('utf-8', errors='replace')).hexdigest()[:12]}"


def _conversation_from_rendered_dom(index: int, title: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    conversation_id = _rendered_dom_conversation_id(index, title, messages)
    mapping: dict[str, Any] = {}
    previous_node_id: str | None = None
    current_node: str | None = None
    for msg_index, item in enumerate(messages, start=1):
        node_id = f"{conversation_id}-node-{msg_index:06d}"
        message_id = f"{conversation_id}-message-{msg_index:06d}"
        author_label = item.get("author") or "unknown"
        role = _role_from_rendered_author(author_label)
        text = item.get("text") or ""
        mapping[node_id] = {
            "id": node_id,
            "parent": previous_node_id,
            "children": [],
            "message": {
                "id": message_id,
                "author": {"role": role, "name": author_label},
                "create_time": None,
                "content": {"content_type": "text", "parts": [text]},
                "metadata": {
                    "message_type": "rendered_dom_message",
                    "author_label": author_label,
                    "source_format": "rendered_chat_dom_html",
                },
            },
        }
        if previous_node_id is not None:
            mapping[previous_node_id]["children"].append(node_id)
        previous_node_id = node_id
        current_node = node_id
    return {
        "conversation_id": conversation_id,
        "id": conversation_id,
        "title": title or "(bez tytulu)",
        "create_time": None,
        "update_time": None,
        "current_node": current_node,
        "mapping": mapping,
        "source_format": "rendered_chat_dom_html",
    }


class _RenderedChatDomParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.conversations: list[dict[str, Any]] = []
        self._conversation_depth: int | None = None
        self._conversation_index = 0
        self._title_parts: list[str] | None = None
        self._capturing_title = False
        self._messages: list[dict[str, str]] = []
        self._message_depth: int | None = None
        self._author_depth: int | None = None
        self._author_parts: list[str] = []
        self._content_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        classes = _class_names(attrs)
        if tag == "div" and self._conversation_depth is None and "conversation" in classes:
            self._conversation_depth = 1
            self._conversation_index += 1
            self._title_parts = None
            self._capturing_title = False
            self._messages = []
            return
        if tag == "div" and self._conversation_depth is not None:
            self._conversation_depth += 1
        if self._conversation_depth is None:
            return
        if tag == "h4" and self._message_depth is None:
            self._title_parts = []
            self._capturing_title = True
            return
        if tag == "pre" and "message" in classes and self._message_depth is None:
            self._message_depth = 1
            self._author_depth = None
            self._author_parts = []
            self._content_parts = []
            return
        if self._message_depth is not None:
            self._message_depth += 1
            if tag == "div" and "author" in classes and self._author_depth is None:
                self._author_depth = 1
                return
            if self._author_depth is not None:
                self._author_depth += 1
            elif tag in {"br", "div", "p", "li", "section", "h1", "h2", "h3", "h4", "h5", "h6"}:
                self._content_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "h4" and self._title_parts is not None and self._message_depth is None:
            self._capturing_title = False
            return
        if self._message_depth is not None:
            if self._author_depth is not None:
                self._author_depth -= 1
                if self._author_depth <= 0:
                    self._author_depth = None
            elif tag in {"div", "p", "li", "section", "h1", "h2", "h3", "h4", "h5", "h6"}:
                self._content_parts.append("\n")
            self._message_depth -= 1
            if self._message_depth <= 0:
                self._finish_message()
        if tag == "div" and self._conversation_depth is not None:
            self._conversation_depth -= 1
            if self._conversation_depth <= 0:
                self._finish_conversation()

    def handle_data(self, data: str) -> None:
        if self._capturing_title and self._title_parts is not None and self._message_depth is None:
            self._title_parts.append(data)
        elif self._message_depth is not None:
            if self._author_depth is not None:
                self._author_parts.append(data)
            else:
                self._content_parts.append(data)

    def _finish_message(self) -> None:
        author = _clean_dom_text(self._author_parts) or "unknown"
        text = _clean_dom_text(self._content_parts)
        self._messages.append({"author": author, "text": text})
        self._message_depth = None
        self._author_depth = None
        self._author_parts = []
        self._content_parts = []

    def _finish_conversation(self) -> None:
        title = _clean_dom_text(self._title_parts or [])
        self.conversations.append(_conversation_from_rendered_dom(self._conversation_index, title, self._messages))
        self._conversation_depth = None
        self._title_parts = None
        self._capturing_title = False
        self._messages = []


def iter_rendered_chat_dom_conversations(path: Path) -> Iterator[dict[str, Any]]:
    parser = _RenderedChatDomParser()
    parser.feed(path.read_text(encoding="utf-8-sig", errors="replace"))
    parser.close()
    yield from parser.conversations


def iter_chatgpt_export_conversations(path: Path) -> Iterator[dict[str, Any]]:
    """Streamuje obiekty rozmów z chat.html bez wczytywania całych 800+ MB do RAM.

    Eksport ChatGPT trzyma JSON w skrypcie jako `var jsonData = [...]`. Parser idzie po
    top-level array, pilnuje stringów i nawiasów klamrowych, a następnie dekoduje po
    jednej rozmowie. Dzięki temu największym buforem jest pojedyncza rozmowa, nie cały plik.
    """
    try:
        reader = _text_reader_after_marker(path)
    except ValueError:
        yield from iter_rendered_chat_dom_conversations(path)
        return
    try:
        # Dojdź do początku tablicy.
        started = False
        collecting = False
        depth = 0
        in_string = False
        escaped = False
        buf: list[str] = []

        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                if not started:
                    raise ValueError("Nie znaleziono początku tablicy JSON po `var jsonData =`.")
                break
            for ch in chunk:
                if not started:
                    if ch == "[":
                        started = True
                    continue
                if not collecting:
                    if ch == "{":
                        collecting = True
                        depth = 1
                        in_string = False
                        escaped = False
                        buf = [ch]
                    elif ch == "]":
                        return
                    else:
                        continue
                    continue

                buf.append(ch)
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        raw_obj = "".join(buf)
                        collecting = False
                        yield json.loads(raw_obj)
                        buf = []
    finally:
        try:
            raw = reader.detach()
            raw.close()
        except Exception:
            pass


def visible_path(mapping: dict[str, Any], current_node: str | None) -> list[str]:
    if not current_node:
        return []
    child_to_parent: dict[str, str] = {}
    for node_id, node in mapping.items():
        for child in (node or {}).get("children") or []:
            child_to_parent[str(child)] = str(node_id)
    path: list[str] = []
    seen: set[str] = set()
    node_id: str | None = str(current_node)
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        path.append(node_id)
        node = mapping.get(node_id) or {}
        parent = node.get("parent") or child_to_parent.get(node_id)
        if not parent:
            msg = node.get("message") or {}
            meta = msg.get("metadata") or {}
            parent = meta.get("parent_id")
        node_id = str(parent) if parent else None
    path.reverse()
    return path


def extract_text_and_parts(message: dict[str, Any]) -> tuple[str, list[Any], list[dict[str, Any]], int]:
    content = message.get("content") or {}
    parts = content.get("parts") or []
    text_parts: list[str] = []
    total_chars = 0
    stored_chars = 0
    truncated = False
    assets: list[dict[str, Any]] = []

    def add_text(value: str) -> None:
        nonlocal total_chars, stored_chars, truncated
        if not value:
            return
        total_chars += len(value)
        if stored_chars >= TEXT_IMPORT_CHAR_LIMIT:
            truncated = True
            return
        remaining = TEXT_IMPORT_CHAR_LIMIT - stored_chars
        piece = value[:remaining]
        text_parts.append(piece)
        stored_chars += len(piece)
        if len(value) > remaining:
            truncated = True

    for part in parts:
        if isinstance(part, str):
            add_text(part)
        elif isinstance(part, dict):
            if isinstance(part.get("text"), str):
                add_text(part["text"])
            if isinstance(part.get("name"), str) or isinstance(part.get("asset_pointer"), str) or part.get("content_type"):
                assets.append({k: part.get(k) for k in ["content_type", "name", "asset_pointer", "mime_type", "size_bytes"] if k in part})
            # Nie wrzucam całych binarnych/metadanych do SQLite — tylko tekst i lekkie metadane.
    if not text_parts and isinstance(content.get("text"), str):
        add_text(content["text"])
    text = "\n".join(x for x in text_parts if x is not None).strip()
    if truncated:
        text += "\n[...ucięte w indeksie SQLite; pełna treść pozostaje w memory/raw/chat.html...]"
    return text, parts, assets, total_chars


def summarize_parts_for_sqlite(parts: list[Any], max_items: int = 20) -> list[Any]:
    summary: list[Any] = []
    for part in parts[:max_items]:
        if isinstance(part, str):
            summary.append({"type": "text", "char_count": len(part), "preview": part[:200]})
        elif isinstance(part, dict):
            item = {k: part.get(k) for k in ["content_type", "name", "mime_type", "size_bytes", "asset_pointer"] if k in part}
            if isinstance(part.get("text"), str):
                item["text_char_count"] = len(part["text"])
                item["text_preview"] = part["text"][:200]
            summary.append(item or {"type": "dict", "keys": sorted(part.keys())[:20]})
        else:
            summary.append({"type": type(part).__name__})
    if len(parts) > max_items:
        summary.append({"truncated_parts": len(parts) - max_items})
    return summary


def search_raw_chat_html_snippets(chat_html_path: Path, terms: list[str], *, limit: int = 5, window: int = 450) -> list[dict[str, Any]]:
    """Awaryjne pełnotekstowe szukanie w surowym chat.html bez importu do SQLite.

    To nie zastępuje indeksu, ale pozwala runtime znaleźć ślady typu „Lumiel”
    nawet wtedy, gdy pełny import 800+ MB nie został jeszcze wykonany.
    """
    path = Path(chat_html_path)
    if not path.exists():
        return []
    needles: list[tuple[str, str]] = []
    for term in terms:
        if not term or len(term) < 3:
            continue
        raw = term.lower()
        escaped = json.dumps(term, ensure_ascii=True)[1:-1].lower()
        for n in {raw, escaped}:
            if n:
                needles.append((term, n))
    if not needles:
        return []
    snippets: list[dict[str, Any]] = []
    overlap = window * 2
    tail = ""
    with path.open("rb") as f:
        while len(snippets) < limit:
            chunk_b = f.read(1024 * 1024)
            if not chunk_b:
                break
            chunk = tail + chunk_b.decode("utf-8", errors="replace")
            low = chunk.lower()
            for original, needle in needles:
                pos = low.find(needle)
                while pos >= 0 and len(snippets) < limit:
                    start = max(0, pos - window)
                    end = min(len(chunk), pos + len(needle) + window)
                    raw_snip = chunk[start:end]
                    try:
                        decoded = raw_snip.encode("utf-8", errors="replace").decode("unicode_escape", errors="replace")
                    except Exception:
                        decoded = raw_snip
                    decoded = re.sub(r"\\n|\s+", " ", decoded).strip()
                    decoded = decoded.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
                    snippets.append({"term": original, "snippet": decoded[: window * 2]})
                    pos = low.find(needle, pos + len(needle))
            tail = chunk[-overlap:]
    return snippets


def import_chat_html_to_store(
    store: MemoryStore,
    chat_html_path: Path,
    *,
    force: bool = False,
    limit_conversations: int | None = None,
    tz_name: str = "Europe/Warsaw",
) -> ChatHtmlImportReport:
    chat_html_path = Path(chat_html_path)
    report = ChatHtmlImportReport("ok", str(chat_html_path), errors=[])
    if not chat_html_path.exists():
        return ChatHtmlImportReport("missing", str(chat_html_path), errors=["Plik chat.html nie istnieje."])

    size = chat_html_path.stat().st_size
    report.size_bytes = size
    sha = sha256_file(chat_html_path)
    report.sha256 = sha

    prev = store.get_meta("chat_html_import_sha256")
    if prev == sha and not force and store.stats().get("legacy_messages", 0) > 0:
        report.status = "already_imported"
        return report

    if force:
        store.con.execute("DELETE FROM legacy_messages")
        store.con.execute("DELETE FROM legacy_conversations")
        store.con.commit()

    # Bulk import: tekst jest już skracany do indeksu, a pełny chat.html zostaje
    # źródłem surowym. Wyłączamy kosztowną synchronizację dysku tylko na czas importu.
    try:
        store.con.execute("PRAGMA synchronous=OFF")
        store.con.execute("PRAGMA temp_store=MEMORY")
    except Exception:
        pass
    store.con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_legacy_message_unique ON legacy_messages(conversation_id, message_id)")
    store.con.execute("CREATE INDEX IF NOT EXISTS idx_legacy_messages_title ON legacy_messages(conversation_title)")
    store.con.commit()

    conv_sql = """
        INSERT OR REPLACE INTO legacy_conversations
        (conversation_id, title, create_time, create_time_warsaw, update_time, update_time_warsaw, payload_json)
        VALUES(?,?,?,?,?,?,?)
    """
    msg_sql = """
        INSERT OR IGNORE INTO legacy_messages
        (conversation_id, conversation_title, message_id, author_role, create_time, create_time_warsaw,
         text, parts_json, assets_json, is_visible_path, visible_index, text_sha256, char_count)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    errors: list[str] = []
    try:
        for conv in iter_chatgpt_export_conversations(chat_html_path):
            report.conversations_seen += 1
            if limit_conversations is not None and report.conversations_seen > limit_conversations:
                break
            try:
                conversation_id = str(conv.get("conversation_id") or conv.get("id") or f"conv-{report.conversations_seen}")
                title = conv.get("title") or "(bez tytułu)"
                mapping = conv.get("mapping") or {}
                vpath = visible_path(mapping, conv.get("current_node"))
                vindex = {node_id: idx for idx, node_id in enumerate(vpath)}
                payload = {
                    "id": conversation_id,
                    "title": title,
                    "create_time": conv.get("create_time"),
                    "update_time": conv.get("update_time"),
                    "current_node": conv.get("current_node"),
                    "source": "chat.html",
                    "mapping_node_count": len(mapping),
                }
                store.con.execute(
                    conv_sql,
                    (
                        conversation_id,
                        title,
                        conv.get("create_time"),
                        warsaw_time(conv.get("create_time"), tz_name),
                        conv.get("update_time"),
                        warsaw_time(conv.get("update_time"), tz_name),
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    ),
                )
                report.conversations_imported += 1
                message_rows: list[tuple[Any, ...]] = []
                for node_id, node in mapping.items():
                    msg = (node or {}).get("message")
                    if not isinstance(msg, dict):
                        continue
                    message_id = str(msg.get("id") or node_id)
                    text, parts, assets, total_chars = extract_text_and_parts(msg)
                    if not text and not assets:
                        report.skipped_messages += 1
                        continue
                    author = (msg.get("author") or {}).get("role") or "unknown"
                    created = msg.get("create_time")
                    stored_text = text
                    digest = hashlib.sha256((stored_text + "|char_count=" + str(total_chars)).encode("utf-8", errors="replace")).hexdigest() if text else None
                    message_rows.append(
                        (
                            conversation_id,
                            title,
                            message_id,
                            author,
                            created,
                            warsaw_time(created, tz_name),
                            stored_text,
                            json.dumps(summarize_parts_for_sqlite(parts), ensure_ascii=False, sort_keys=True),
                            json.dumps(assets, ensure_ascii=False, sort_keys=True),
                            1 if node_id in vindex else 0,
                            vindex.get(node_id),
                            digest,
                            total_chars,
                        )
                    )
                if message_rows:
                    store.con.executemany(msg_sql, message_rows)
                    report.messages_imported += len(message_rows)
                if report.conversations_seen % 10 == 0:
                    store.con.commit()
            except Exception as exc:  # keep importing next conversations
                errors.append(f"conversation#{report.conversations_seen}: {exc!r}")
                if len(errors) >= 20:
                    break
        store.con.commit()
        store.set_meta("chat_html_import_sha256", sha)
        store.set_meta("chat_html_imported_at_utc", datetime.now(timezone.utc).isoformat())
        store.set_meta("chat_html_import_report", json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        report.status = "error"
        errors.append(repr(exc))
    report.errors = errors
    return report
