from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("system_temporal_semantics")


class TemporalRelation(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    OVERLAP = "overlap"
    EQUAL = "equal"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class TemporalEvent:
    event_id: str
    event_time: str | None = None
    observed_time: str | None = None
    persisted_time: str | None = None
    narrative_time: str | None = None
    source_trust: str = "unknown"
    uncertainty: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def parsed_event_time(self) -> datetime | None:
        if not self.event_time:
            return None
        try:
            return datetime.fromisoformat(self.event_time.replace("Z", "+00:00"))
        except ValueError:
            return None


@dataclass(slots=True)
class TemporalEdge:
    left_event_id: str
    right_event_id: str
    relation: str
    confidence: float
    reason: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SystemTemporalSemantics:
    def compare(self, left: TemporalEvent, right: TemporalEvent) -> TemporalEdge:
        left_dt = left.parsed_event_time()
        right_dt = right.parsed_event_time()
        if left_dt is None or right_dt is None:
            return TemporalEdge(left.event_id, right.event_id, TemporalRelation.UNKNOWN.value, 0.0, "missing_or_invalid_event_time")
        if left.source_trust == "contradictory" or right.source_trust == "contradictory":
            return TemporalEdge(left.event_id, right.event_id, TemporalRelation.UNKNOWN.value, 0.0, "contradictory_source_time")
        if left_dt == right_dt:
            return TemporalEdge(left.event_id, right.event_id, TemporalRelation.EQUAL.value, 1.0, "equal_event_time")
        if left_dt < right_dt:
            return TemporalEdge(left.event_id, right.event_id, TemporalRelation.BEFORE.value, 0.95, "ordered_event_time")
        return TemporalEdge(left.event_id, right.event_id, TemporalRelation.AFTER.value, 0.95, "ordered_event_time")

    def build_graph(self, events: Iterable[TemporalEvent]) -> dict[str, Any]:
        ordered = sorted(events, key=lambda event: event.event_id)
        edges = [self.compare(left, right) for index, left in enumerate(ordered) for right in ordered[index + 1 :]]
        return {
            "schema_version": SCHEMA_VERSION,
            "events": [event.to_dict() for event in ordered],
            "edges": [edge.to_dict() for edge in edges],
            "unknown_relation_count": sum(edge.relation == TemporalRelation.UNKNOWN.value for edge in edges),
        }
