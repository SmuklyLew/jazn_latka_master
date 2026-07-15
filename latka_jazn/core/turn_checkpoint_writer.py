from __future__ import annotations
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import json, hashlib

SCHEMA_VERSION = "turn_checkpoint/v14.6.10"

@dataclass(slots=True)
class TurnCheckpoint:
    schema_version: str
    turn_id: str
    trace_id: str
    thread_id: str
    created_at_utc: str
    created_at_warsaw: str
    user_text: str
    runtime_text: str
    visible_text: str
    detected_intent: str
    route: str
    response_generation_mode: str
    template_origin: dict[str, Any]
    validator: dict[str, Any]
    source_origin: dict[str, Any]
    memory_sources: list[dict[str, Any]] = field(default_factory=list)
    file_sources: list[dict[str, Any]] = field(default_factory=list)
    dictionary_sources: list[dict[str, Any]] = field(default_factory=list)
    truth_boundary: str = "Checkpoint zapisuje dokładne teksty tej tury. Nie oznacza stałego procesu w tle."
    user_text_sha256: str | None = None
    runtime_text_sha256: str | None = None
    visible_text_sha256: str | None = None
    def __post_init__(self) -> None:
        self.user_text_sha256 = self.user_text_sha256 or hashlib.sha256((self.user_text or '').encode('utf-8')).hexdigest()
        self.runtime_text_sha256 = self.runtime_text_sha256 or hashlib.sha256((self.runtime_text or '').encode('utf-8')).hexdigest()
        self.visible_text_sha256 = self.visible_text_sha256 or hashlib.sha256((self.visible_text or '').encode('utf-8')).hexdigest()
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class TurnCheckpointWriter:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.base = self.root / 'workspace_runtime' / 'turn_checkpoints'
    def append(self, checkpoint: TurnCheckpoint) -> Path:
        date = (checkpoint.created_at_utc or datetime.now(timezone.utc).isoformat())[:10]
        path = self.base / date / 'turns.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(checkpoint.to_dict(), ensure_ascii=False, sort_keys=True) + '\n')
        latest = self.base / 'LATEST_TURN_CHECKPOINT.json'
        latest.write_text(json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        return path
    def build_and_append(self, *, turn_id: str, trace_id: str, timestamp_header: str, user_text: str, runtime_text: str, visible_text: str, detected_intent: str, route: str, response_generation_mode: str, template_origin: dict[str, Any] | None, validator: dict[str, Any] | None, source_origin: dict[str, Any] | None, memory_sources: list[dict[str, Any]] | None = None, file_sources: list[dict[str, Any]] | None = None, dictionary_sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        cp = TurnCheckpoint(
            schema_version=SCHEMA_VERSION, turn_id=turn_id, trace_id=trace_id, thread_id='local-one-shot',
            created_at_utc=datetime.now(timezone.utc).isoformat(), created_at_warsaw=timestamp_header,
            user_text=user_text, runtime_text=runtime_text, visible_text=visible_text, detected_intent=detected_intent,
            route=route, response_generation_mode=response_generation_mode, template_origin=template_origin or {},
            validator=validator or {}, source_origin=source_origin or {}, memory_sources=memory_sources or [], file_sources=file_sources or [], dictionary_sources=dictionary_sources or [])
        path = self.append(cp)
        data = cp.to_dict(); data['written_to'] = str(path.relative_to(self.root)); return data
