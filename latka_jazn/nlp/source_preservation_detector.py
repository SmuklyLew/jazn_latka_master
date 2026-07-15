from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
SCHEMA_VERSION="source_preservation_detector/v14.6.10"
@dataclass(slots=True)
class SourcePreservationReport:
    preserve_required: bool; revision_allowed: bool; evidence: list[str]; schema_version: str=SCHEMA_VERSION
    def to_dict(self)->dict[str,Any]: return asdict(self)
class SourcePreservationDetector:
    def detect(self,text:str)->SourcePreservationReport:
        low=(text or '').lower()
        preserve=[x for x in ('nie zmieniaj','1:1','bez zmian','zachowaj','bez redakcji') if x in low]
        revise=[x for x in ('zredaguj','przerób','przerob','zmień','zmien','dodaj') if x in low]
        return SourcePreservationReport(bool(preserve), bool(revise), preserve+revise)
