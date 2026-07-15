from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("turn_response_policy")

@dataclass(slots=True)
class TurnResponsePolicy:
    intent: str
    route: str
    answer_kind: str
    must_answer_current_question: bool = True
    allow_memory_content: bool = False
    allow_architecture_explanation: bool = False
    allow_previous_turn_carryover: bool = False
    required_components: list[str] = field(default_factory=list)
    forbidden_topics: list[str] = field(default_factory=list)
    forbidden_legacy_routes: list[str] = field(default_factory=list)
    source_boundary_required: bool = False
    exact_runtime_required: bool = False
    max_meta_technicality: Literal["none", "low", "medium", "high"] = "low"
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Polityka odpowiedzi jest kontraktem przed syntezą: ogranicza pamięć, technikalia, carryover i wymagane komponenty odpowiedzi."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def build(cls, *, intent: str, route: str, context: dict[str, Any] | None = None) -> "TurnResponsePolicy":
        intent = intent or "ordinary_conversation"
        route = route or "ordinary_dialogue"
        if intent in {"ordinary_conversation", "standalone_greeting", "ordinary_workday_report", "sleep_closure_statement", "positive_feedback_current_turn"}:
            return cls(
                intent=intent, route=route, answer_kind="natural_dialogue",
                allow_memory_content=False, allow_architecture_explanation=False,
                allow_previous_turn_carryover=False,
                forbidden_topics=["random_memory_excerpt", "debug_report", "runtime_architecture_metareport"],
                forbidden_legacy_routes=["correction_acknowledged", "positive_continuation"],
                max_meta_technicality="none",
            )
        if intent == "runtime_restart_request":
            return cls(
                intent=intent, route=route, answer_kind="runtime_process_control",
                allow_architecture_explanation=True, source_boundary_required=True,
                required_components=["runtime_status", "process_lifecycle", "truth_boundary"],
                forbidden_topics=["random_memory_excerpt", "unconfirmed_restart_claim"],
                max_meta_technicality="medium",
            )
        if intent in {"runtime_activation_status_question", "identity_boundary_question", "identity_direct_question"}:
            return cls(
                intent=intent, route=route, answer_kind="runtime_identity_boundary",
                allow_architecture_explanation=True, source_boundary_required=True,
                required_components=["runtime_status", "model_channel_boundary", "no_background_process_claim"],
                forbidden_topics=["random_memory_excerpt"], max_meta_technicality="medium",
            )
        if intent == "runtime_chat_mode_request":
            return cls(
                intent=intent, route=route, answer_kind="runtime_chat_mode",
                allow_architecture_explanation=True,
                required_components=["chat_mode", "process_lifecycle", "stdin_or_jsonl_boundary"],
                forbidden_topics=["system_update_execution"], max_meta_technicality="medium",
            )
        if intent == "direct_latka_voice_request":
            return cls(
                intent=intent, route=route, answer_kind="direct_latka_voice",
                allow_architecture_explanation=True, source_boundary_required=True,
                required_components=["direct_latka_voice", "model_channel_boundary", "process_lifecycle", "no_background_process_claim", "truth_boundary"],
                forbidden_topics=["background_daemon_claim", "generic_fallback"], max_meta_technicality="medium",
            )
        if intent == "identity_memory_existence_compound_question":
            return cls(
                intent=intent, route=route, answer_kind="identity_memory_existence",
                allow_memory_content=True, allow_architecture_explanation=True, source_boundary_required=True,
                required_components=["identity_position", "memory_status", "known_unknown_boundary", "origin", "being_boundary", "truth_boundary"],
                forbidden_topics=["update_route_substitution", "memory_only_answer"], max_meta_technicality="medium",
            )
        if intent in {"system_repair_plan_request", "logic_reasoning_audit_request", "system_diagnostic_question", "runtime_behavior_diagnostic_request"}:
            return cls(
                intent=intent, route=route, answer_kind="system_repair_or_diagnostic",
                allow_architecture_explanation=True,
                required_components=["problem", "target_files", "code_steps", "tests", "acceptance_criteria"],
                max_meta_technicality="high",
            )
        if intent in {"runtime_source_question", "runtime_exact_quote_request"}:
            return cls(
                intent=intent, route=route, answer_kind="runtime_exact_or_source",
                allow_architecture_explanation=True, source_boundary_required=True, exact_runtime_required=True,
                required_components=["exact_runtime_text", "runtime_vs_model_boundary"],
                max_meta_technicality="high",
            )
        if intent in {"memory_audit_request", "memory_recall_request", "memory_grounding_status_question"}:
            return cls(
                intent=intent, route=route, answer_kind="memory_status_or_recall",
                allow_memory_content=True, allow_architecture_explanation=True,
                required_components=["memory_status", "source_or_index_status", "truth_boundary"],
                max_meta_technicality="high",
            )
        if intent in {"dictionary_lookup_request", "language_question", "dictionary_network_lookup_request", "sjp_wsjp_reference_request"}:
            return cls(
                intent=intent, route=route, answer_kind="language_lookup",
                allow_architecture_explanation=True,
                required_components=["provider_status", "source_or_cache", "truth_boundary"],
                max_meta_technicality="medium",
            )
        return cls(intent=intent, route=route, answer_kind="specialized", max_meta_technicality="medium")
