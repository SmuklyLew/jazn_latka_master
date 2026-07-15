from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
SCHEMA_VERSION="context_carryover/v14.6.10"
@dataclass(slots=True)
class ContextCarryoverReport:
    needs_previous_turn: bool; carryover_type: str; confidence: float; evidence: list[str]; schema_version: str=SCHEMA_VERSION
    def to_dict(self)->dict[str,Any]: return asdict(self)
class ContextCarryover:
    ELLIPSES=("a ty","co dalej","i co","co jeszcze","dlaczego","czemu","na pewno")
    def analyse(self,text:str, previous_text:str|None=None)->ContextCarryoverReport:
        low=(text or '').strip().lower()
        if low in self.ELLIPSES or any(low.startswith(x) for x in self.ELLIPSES):
            return ContextCarryoverReport(True,'ellipsis_or_continuation',0.82,[low])
        return ContextCarryoverReport(False,'none',0.0,[])
