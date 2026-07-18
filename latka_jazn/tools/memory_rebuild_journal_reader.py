from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

from latka_jazn.tools.chat_export_reader import sha256_file
from latka_jazn.tools.memory_rebuild_common import (
    CONTENT_FIELDS, FANOUT_FIELDS, bounded, canonical_json, norm, sha_text,
)


@dataclass(slots=True, frozen=True)
class JournalItem:
    record_id: str
    identity: str
    title: str
    summary: str
    content: str
    content_hash: str
    raw: dict[str, Any]
    truth: str
    importance: float
    start: str | None
    end: str | None
    timestamp_status: str
    fanout: bool


class JournalReader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.sha256 = sha256_file(self.path)
        self.format = self.path.suffix.lower().lstrip(".") or "json"
        self.meta: dict[str, Any] = {}
        self.invalid = 0
        self.rows = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if self.path.suffix.lower() in {".jsonl", ".ndjson"}:
            result = []
            for line in self.path.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    self.invalid += 1
                    continue
                if isinstance(value, dict):
                    result.append(value)
                else:
                    self.invalid += 1
            return result
        value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if isinstance(value, dict) and isinstance(value.get("entries"), list):
            self.meta = dict(value.get("meta") or {}) if isinstance(value.get("meta"), dict) else {}
            source = value["entries"]
        elif isinstance(value, list):
            source = value
        else:
            raise ValueError("journal must be {meta,entries}, a JSON list, or JSONL")
        result = []
        for item in source:
            if isinstance(item, dict):
                result.append(item)
            else:
                self.invalid += 1
        return result

    def items(self) -> list[JournalItem]:
        result = []
        for raw in self.rows:
            lines = [f"{key}: {norm(raw.get(key))}" for key in CONTENT_FIELDS if norm(raw.get(key))]
            content = "\n".join(lines) or canonical_json(raw)
            content_hash = sha_text(content)
            explicit = norm(raw.get("id") or raw.get("entry_id") or raw.get("uuid"))
            record_id = explicit or sha_text(canonical_json(raw))
            identity = f"id:{explicit}" if explicit else f"content:{content_hash}"
            title = norm(raw.get("tytuł") or raw.get("tytul") or raw.get("title")) or norm(content)[:120]
            summary = norm(raw.get("wpis") or raw.get("treść") or raw.get("tresc") or raw.get("content") or raw.get("opis")) or norm(content)[:2000]
            truth_text = " ".join(norm(raw.get(key)).lower() for key in ("truth_status", "grounding", "granica_prawdy", "source"))
            if "user_confirmed" in truth_text or "verified" in truth_text:
                truth = "user_confirmed"
            elif "source_recorded" in truth_text or "runtime" in truth_text:
                truth = "source_recorded"
            elif "book_scene" in truth_text or "scena książ" in truth_text:
                truth = "book_scene"
            elif "symbol" in truth_text or "wyobraź" in truth_text:
                truth = "symbolic"
            else:
                truth = "inferred"
            start = norm(raw.get("event_time_start") or raw.get("timestamp") or raw.get("data")) or None
            result.append(JournalItem(
                record_id, identity, title, summary, content, content_hash, dict(raw), truth,
                bounded(raw.get("importance", raw.get("ważność", raw.get("waznosc"))), 0.6),
                start, norm(raw.get("event_time_end")) or None,
                "source_recorded" if start else "missing",
                sum(1 for key in FANOUT_FIELDS if norm(raw.get(key))) >= 2,
            ))
        return result

    def inspect(self) -> dict[str, Any]:
        items = self.items()
        return {
            "ok": True, "path": str(self.path), "sha256": self.sha256, "format": self.format,
            "valid_entries": len(items), "invalid_entries": self.invalid,
            "suspected_fanout": sum(1 for item in items if item.fanout),
            "automatic_l2": False, "automatic_l3": False,
        }
