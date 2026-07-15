from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION="memory_recall_content_contract/v14.7.0"

@dataclass(slots=True)
class MemoryRecallItem:
    content: str
    source: str
    memory_type: str
    timestamp: str | None = None
    confidence: float = 0.0
    relevance: float = 0.0
    truth_boundary: str = "recalled_or_indexed_memory_not_biological_experience"
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    def to_dict(self): return asdict(self)

@dataclass(slots=True)
class MemoryRecallContract:
    query: str
    items: list[dict[str, Any]]
    counts: dict[str, int]
    raw_memory_status: str = "unknown"
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Pamięć musi przekazywać treść, źródło, typ, czas, confidence i relevance. Same liczniki nie wystarczają do odpowiedzi."
    def to_dict(self): return asdict(self)

class MemoryRecallContractBuilder:
    def build(self, memory_context: dict[str, Any], *, user_text: str) -> MemoryRecallContract:
        ctx=memory_context or {}; items=[]
        for ep in ctx.get('episodes') or []:
            content=str(ep.get('scene') or ep.get('text') or '')
            if content:
                items.append(MemoryRecallItem(content=content[:1800], source=str(ep.get('source') or 'episodic_memories'), memory_type='episode', timestamp=ep.get('created_at_local') or ep.get('created_at_utc'), confidence=float(ep.get('confidence') or 0.70), relevance=float(ep.get('relevance') or 0.60), metadata={k:v for k,v in ep.items() if k not in {'scene','text'}}).to_dict())
        for msg in ctx.get('legacy_messages') or []:
            content=str(msg.get('text') or msg.get('content') or '')
            if content:
                items.append(MemoryRecallItem(content=content[:1800], source=str(msg.get('conversation_title') or msg.get('source') or 'legacy_messages'), memory_type='legacy_message', timestamp=msg.get('created_at_local') or msg.get('created_at_utc'), confidence=float(msg.get('confidence') or 0.62), relevance=float(msg.get('relevance') or 0.55), metadata={k:v for k,v in msg.items() if k not in {'text','content'}}).to_dict())
        for hit in ctx.get('source_file_hits') or []:
            content=str(hit.get('snippet') or hit.get('text') or hit.get('path') or '')
            if content:
                items.append(MemoryRecallItem(content=content[:1800], source=str(hit.get('path') or 'source_file'), memory_type='source_file_hit', timestamp=hit.get('modified_at'), confidence=float(hit.get('confidence') or 0.55), relevance=float(hit.get('score') or hit.get('relevance') or 0.50), metadata=hit).to_dict())
        for hit in ctx.get('conversation_archive_hits') or []:
            content=str(hit.get('excerpt') or hit.get('text') or '')
            if content:
                source = str(hit.get('source_name') or hit.get('source_locator') or 'conversation_archive_v1')
                items.append(MemoryRecallItem(content=content[:1800], source=source, memory_type='conversation_archive_hit', timestamp=hit.get('create_time_warsaw') or hit.get('create_time'), confidence=float(hit.get('identity_confidence') or 0.58), relevance=float(hit.get('relevance') or 0.58), metadata=hit).to_dict())
        for raw in ctx.get('raw_chat_fallback') or []:
            content=str(raw.get('snippet') or raw.get('text') or '')
            if content:
                items.append(MemoryRecallItem(content=content[:1800], source='memory/raw/chat.html', memory_type='raw_chat_fallback', timestamp=raw.get('timestamp'), confidence=0.45, relevance=float(raw.get('score') or 0.45), metadata=raw).to_dict())
        return MemoryRecallContract(query=user_text, items=items, counts=dict(ctx.get('counts') or {}))
