from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class CacheDecision:
    source_id: str
    allow_cache: bool
    allow_bulk_mirror: bool
    cache_kind: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PolishReasoningCachePolicy:
    def decide(self, source: dict[str, Any]) -> CacheDecision:
        source_id = str(source.get("source_id") or source.get("name") or "unknown")
        mode = str(source.get("mode") or "online_lookup")
        redistrib = str(source.get("redistribution") or "manual_review_required")
        if mode == "offline_mandatory":
            return CacheDecision(source_id, True, bool(source.get("allow_bulk_mirror", False)), "local_runtime_index", "Źródło jest przeznaczone do lokalnego użycia przez instalację lub bootstrap.")
        if "no_mass" in redistrib or mode == "online_lookup_preferred":
            return CacheDecision(source_id, True, False, "lookup_metadata_only", "Dozwolone tylko kontrolowane lookupi i metadane; nie mirrorować całego zasobu.")
        return CacheDecision(source_id, True, False, "manual_review_required", "Brak pełnej decyzji licencyjnej; cache ograniczony do metadanych i hashy.")
