from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION="external_research_contract/v14.7.0"

@dataclass(slots=True)
class ExternalResearchResult:
    network_allowed: bool
    provider: str
    query: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    retrieved_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cache_status: str = "requires_external_web_execution"
    truth_boundary: str = "Lokalny runtime nie udaje sprawdzenia internetu. Jeśli nie ma providera web, wynik wymaga wykonania przez warstwę zewnętrzną i przekazania źródeł."
    schema_version: str = SCHEMA_VERSION
    def to_dict(self): return asdict(self)

class ExternalResearchContract:
    def build_required(self, query: str, *, network_allowed: bool=True, provider: str='chatgpt_web_bridge') -> ExternalResearchResult:
        return ExternalResearchResult(network_allowed=network_allowed, provider=provider, query=query)
