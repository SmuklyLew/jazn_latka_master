from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION="tool_use_policy/v14.7.0"

@dataclass(slots=True)
class ToolUseDecision:
    allowed: bool
    tool_class: str
    reason: str
    safeguards: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class ToolUsePolicy:
    INTERNET_MARKERS=("sprawdź w internecie","sprawdz w internecie","aktualn","źródła","zrodla","cena","prawo","dokumentacja","wiadomości","wiadomosci")
    def decide(self, text: str) -> ToolUseDecision:
        low=(text or '').lower()
        if any(m in low for m in self.INTERNET_MARKERS):
            return ToolUseDecision(True,'external_web_research','zapytanie wymaga aktualnego albo źródłowego sprawdzenia',['source_citations_required','untrusted_source_guard','truth_boundary'])
        return ToolUseDecision(False,'none','brak jawnej potrzeby narzędzia zewnętrznego',['do_not_pretend_tool_use'])
