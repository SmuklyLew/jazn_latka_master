from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class TurnRouteTrace:
    schema_version: str = "turn_route_trace/v14.8.3.1"
    user_text_preview: str = ""
    speech_act: str | None = None
    question_object: str | None = None
    primary_intent_initial: str | None = None
    primary_intent_final: str | None = None
    secondary_intents: list[str] = field(default_factory=list)
    topic_guard: dict[str, Any] = field(default_factory=dict)
    turn_logic_audit: dict[str, Any] = field(default_factory=dict)
    selected_route: str | None = None
    selected_handler: str | None = None
    memory_gate: str = "not_needed"
    startup_status_mode: str = "fast"
    sqlite_health_mode: str = "metadata"
    network_time_used: bool = False
    deep_audit_used: bool = False
    runtime_answer_validation: dict[str, Any] = field(default_factory=dict)
    final_text_source: str | None = None
    fallback_classification: str | None = None
    source_origin_detail: str | None = None
    can_generate_model_guided_speech: bool = False
    requires_host_model: bool = False
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
