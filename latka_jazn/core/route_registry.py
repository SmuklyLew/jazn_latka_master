from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any
from latka_jazn.version import schema_version
from latka_jazn.core.legacy_route_policy import legacy_forbidden_routes_for

SCHEMA_VERSION = schema_version("route_registry")

@dataclass(slots=True)
class RouteRegistryEntry:
    intent: str
    route: str
    handler_name: str
    priority: int
    required_components: list[str] = field(default_factory=list)
    forbidden_legacy_routes: list[str] = field(default_factory=list)
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class RouteRegistry:
    """Priorytetowy rejestr tras: DialogueIntentClassifier > RouteRegistry > LegacyMarkers."""
    PRIORITIES = {
        "self_architecture_audit_request": 101, "jazn_development_plan_request": 100, "runtime_behavior_diagnostic_request": 100, "voice_perspective_diagnostic_request": 100, "runtime_exact_quote_request": 99,
        "runtime_source_question": 98, "canon_source_question": 98, "package_runtime_status_question": 97, "runtime_activation_status_question": 97, "runtime_chat_mode_request": 97, "system_repair_plan_request": 96, "logic_reasoning_audit_request": 96, "memory_grounding_status_question": 96, "system_diagnostic_question": 96,
        "system_update_execution_request": 95, "system_update_manifest_request": 94,
        "creative_text_formatting": 92, "creative_text_analysis": 90,
        "creative_source_preservation_request": 89, "identity_boundary_question": 88, "identity_direct_question": 88,
        "self_state_question": 87, "reciprocal_self_state_question": 86, "self_preference_question": 86, "self_plan_question": 85, "sleep_closure_statement": 85, "current_time_question": 85, "substantive_question_about_last_year": 84, "current_hotfix_for_stale_nlp_route": 83, "memory_experience_question": 82, "ordinary_workday_report": 81, "legacy_behavioral_runtime_dialogue_update_reference": 80,
        "memory_audit_request": 84, "memory_recall_request": 83,
        "dictionary_lookup_request": 82, "language_question": 81,
        "external_research_request": 80, "practical_repair_advice": 78,
        "automotive_warning_light_question": 77, "visual_style_advice": 76,
        "module_inventory_request": 96, "system_capability_gap_question": 96,
        "runtime_restart_request": 98, "runtime_health_check": 97, "runtime_health_check_after_update": 97, "presence_check": 87, "identity_presence_check": 89, "identity_continuity_check": 88, "time_awareness_question": 86, "self_state_time_awareness": 88, "internet_access_question": 96, "model_adapter_status_question": 97, "capability_status_question": 95,
        "user_memory_recall_request": 92,
        "self_memory_recall_request": 91,
        "direct_latka_voice_request": 97,
        "identity_memory_existence_compound_question": 94,
        "self_expression_request": 86, "negative_feedback_current_turn": 86, "positive_feedback_current_turn": 65,
        "casual_feedback": 87, "casual_greeting": 62, "expressive_reaction": 61, "short_free_dialogue": 59,
        "standalone_greeting": 60,
        "ordinary_conversation": 10,
    }
    HANDLERS = {
        "self_architecture_audit_request": ("self_architecture_audit", "SelfArchitectureAuditHandler"),
        "jazn_development_plan_request": ("self_architecture_audit", "SelfArchitectureAuditHandler"),
        "runtime_source_question": ("runtime_source", "RuntimeSourceHandler"),
        "canon_source_question": ("canon_source", "CanonSourceHandler"),
        "runtime_exact_quote_request": ("runtime_source", "RuntimeSourceHandler"),
        "package_runtime_status_question": ("package_runtime_status", "PackageRuntimeStatusHandler"),
        "runtime_activation_status_question": ("runtime_activation_status", "RuntimeActivationStatusHandler"),
        "runtime_restart_request": ("runtime_restart_request", "RuntimeActivationStatusHandler"),
        "runtime_chat_mode_request": ("runtime_chat_mode", "RuntimeChatModeHandler"),
        "system_repair_plan_request": ("system_repair_plan", "SystemRepairPlanHandler"),
        "logic_reasoning_audit_request": ("system_repair_plan", "SystemRepairPlanHandler"),
        "memory_grounding_status_question": ("memory_audit", "MemoryAuditHandler"),
        "runtime_behavior_diagnostic_request": ("runtime_diagnostic", "RuntimeDiagnosticHandler"),
        "voice_perspective_diagnostic_request": ("runtime_diagnostic", "RuntimeDiagnosticHandler"),
        "system_diagnostic_question": ("runtime_diagnostic", "RuntimeDiagnosticHandler"),
        "module_inventory_request": ("runtime_diagnostic", "RuntimeDiagnosticHandler"),
        "system_capability_gap_question": ("runtime_diagnostic", "RuntimeDiagnosticHandler"),
        "system_update_execution_request": ("system_update", "SystemUpdateHandler"),
        "system_update_manifest_request": ("system_update", "SystemUpdateHandler"),
        "update_manifest_request": ("system_update", "SystemUpdateHandler"),
        "creative_text_formatting": ("creative_text", "CreativeTextHandler"),
        "creative_text_analysis": ("creative_text", "CreativeTextHandler"),
        "creative_source_preservation_request": ("creative_text", "CreativeTextHandler"),
        "identity_boundary_question": ("identity_boundary", "IdentityBoundaryHandler"),
        "identity_direct_question": ("identity_runtime_truth_contract", "IdentityRuntimeTruthHandler"),
        "self_state_question": ("self_state", "SelfStateHandler"),
        "self_state_time_awareness": ("self_state", "SelfStateHandler"),
        "reciprocal_self_state_question": ("self_state", "SelfStateHandler"),
        "self_preference_question": ("self_state", "SelfStateHandler"),
        "self_plan_question": ("self_plan", "SelfStateHandler"),
        "presence_check": ("presence_status", "PresenceStatusHandler"),
        "identity_presence_check": ("identity_presence_status", "PresenceStatusHandler"),
        "identity_continuity_check": ("identity_runtime_truth_contract", "IdentityRuntimeTruthHandler"),
        "time_awareness_question": ("time_awareness", "TimeAwarenessHandler"),
        "self_expression_request": ("self_expression", "SelfStateHandler"),
        "negative_feedback_current_turn": ("ordinary_dialogue", "OrdinaryDialogueHandler"),
        "positive_feedback_current_turn": ("ordinary_dialogue", "OrdinaryDialogueHandler"),
        "casual_feedback": ("ordinary_dialogue", "OrdinaryDialogueHandler"),
        "casual_greeting": ("greeting", "OrdinaryDialogueHandler"),
        "expressive_reaction": ("ordinary_dialogue", "OrdinaryDialogueHandler"),
        "short_free_dialogue": ("ordinary_dialogue", "OrdinaryDialogueHandler"),
        "sleep_closure_statement": ("sleep_closure", "OrdinaryDialogueHandler"),
        "current_time_question": ("current_time", "OrdinaryDialogueHandler"),
        "substantive_question_about_last_year": ("last_year_reflection", "OrdinaryDialogueHandler"),
        "current_hotfix_for_stale_nlp_route": ("legacy_diagnostic_only", "RuntimeDiagnosticHandler"),
        "memory_experience_question": ("free_memory_dialogue_no_source", "OrdinaryDialogueHandler"),
        "ordinary_workday_report": ("ordinary_workday_dialogue", "OrdinaryDialogueHandler"),
        "legacy_behavioral_runtime_dialogue_update_reference": ("legacy_diagnostic_only", "RuntimeDiagnosticHandler"),
        "memory_audit_request": ("memory_audit", "MemoryAuditHandler"),
        "memory_recall_request": ("memory_audit", "MemoryAuditHandler"),
        "dictionary_lookup_request": ("dictionary_lookup", "DictionaryLookupHandler"),
        "language_question": ("dictionary_lookup", "DictionaryLookupHandler"),
        "external_research_request": ("external_research", "ExternalResearchHandler"),
        "runtime_health_check": ("runtime_health_check", "CapabilityStatusHandler"),
        "runtime_health_check_after_update": ("runtime_health_check_after_update", "CapabilityStatusHandler"),
        "internet_access_question": ("internet_access_status", "CapabilityStatusHandler"),
        "model_adapter_status_question": ("model_adapter_status", "CapabilityStatusHandler"),
        "capability_status_question": ("capability_status", "CapabilityStatusHandler"),
        "user_memory_recall_request": ("user_memory_recall", "UserMemoryRecallHandler"),
        "self_memory_recall_request": ("self_memory_recall", "SelfMemoryRecallHandler"),
        "direct_latka_voice_request": ("direct_latka_voice", "DirectLatkaVoiceHandler"),
        "identity_memory_existence_compound_question": ("identity_memory_existence", "IdentityMemoryExistenceHandler"),
        "practical_repair_advice": ("practical_advice", "PracticalAdviceHandler"),
        "automotive_warning_light_question": ("practical_advice", "PracticalAdviceHandler"),
        "visual_style_advice": ("practical_advice", "PracticalAdviceHandler"),
        "standalone_greeting": ("greeting", "OrdinaryDialogueHandler"),
        "ordinary_conversation": ("ordinary_dialogue", "OrdinaryDialogueHandler"),
    }

    def resolve(self, primary_intent: str, *, confidence: float = 0.0) -> RouteRegistryEntry:
        intent = primary_intent or "ordinary_conversation"
        route, handler = self.HANDLERS.get(intent, ("fallback", "FallbackHandler"))
        required = self.required_components_for(intent)
        priority = self.PRIORITIES.get(intent, 20)
        return RouteRegistryEntry(intent, route, handler, priority, required, legacy_forbidden_routes_for(priority))

    def required_components_for(self, intent: str) -> list[str]:
        if intent in {"self_architecture_audit_request", "jazn_development_plan_request"}:
            return ["self_architecture_audit", "reflection_grounding", "grounded_reflection_store", "memory_gate", "recall_quality", "capability_reality_check", "development_backlog", "scientific_basis", "tests", "truth_boundary"]
        if intent == "package_runtime_status_question":
            return ["package_status", "runtime_status", "archive_integrity_boundary", "known_issues", "truth_boundary", "source_origin"]
        if intent in {"runtime_activation_status_question"}:
            return ["runtime_status", "model_channel_boundary", "no_background_process_claim"]
        if intent == "runtime_restart_request":
            return ["runtime_status", "process_lifecycle", "truth_boundary"]
        if intent in {"runtime_health_check", "runtime_health_check_after_update"}:
            return ["runtime_status", "version", "active_database", "cache_reuse", "memory_status", "truth_boundary"]
        if intent in {"presence_check", "identity_presence_check"}:
            return ["presence_response", "process_lifecycle", "truth_boundary"] + (["identity_continuity"] if intent == "identity_presence_check" else [])
        if intent == "identity_continuity_check":
            return ["runtime_identity", "model_channel_boundary", "process_lifecycle", "truth_boundary"]
        if intent == "internet_access_question":
            return ["internet_access", "provider_status", "truth_boundary", "source_origin"]
        if intent == "model_adapter_status_question":
            return ["provider", "model", "adapter_status", "endpoint", "truth_boundary"]
        if intent == "capability_status_question":
            return ["capability_list", "runtime_status", "memory_status", "network_boundary", "truth_boundary"]
        if intent == "user_memory_recall_request":
            return ["memory_content", "source_or_index_status", "truth_boundary", "user_memory_not_self_memory"]
        if intent == "self_memory_recall_request":
            return ["memory_content", "source_or_index_status", "truth_boundary", "no_update_route_substitution", "self_memory_not_user_memory"]
        if intent == "direct_latka_voice_request":
            return ["direct_latka_voice", "model_channel_boundary", "process_lifecycle", "no_background_process_claim", "truth_boundary"]
        if intent == "identity_memory_existence_compound_question":
            return ["identity_position", "memory_status", "known_unknown_boundary", "origin", "being_boundary", "truth_boundary"]
        if intent in {"runtime_chat_mode_request"}:
            return ["chat_mode", "process_lifecycle", "stdin_or_jsonl_boundary"]
        if intent in {"system_repair_plan_request", "logic_reasoning_audit_request"}:
            return ["problem", "target_files", "code_steps", "tests", "acceptance_criteria"]
        if intent in {"memory_grounding_status_question"}:
            return ["memory_status", "source_or_index_status", "truth_boundary"]
        if intent == "canon_source_question":
            return ["python_canon_modules", "public_resource_boundary", "private_memory_candidate_boundary", "local_private_extension_boundary", "review_required_boundary", "source_origin_detail"]
        if intent in {"runtime_source_question", "runtime_exact_quote_request"}:
            return ["exact_runtime_text", "template_origin", "runtime_vs_visible_boundary", "source_origin_detail"]
        if intent == "voice_perspective_diagnostic_request":
            return ["module_or_file", "problem", "change_plan", "regression_test", "source_origin", "first_person_voice_contract"]
        if intent in {"runtime_behavior_diagnostic_request", "system_diagnostic_question"}:
            return ["module_or_file", "problem", "change_plan", "regression_test", "source_origin"]
        if intent == "module_inventory_request":
            return ["module_or_file", "runtime_status", "truth_boundary", "source_origin"]
        if intent == "system_capability_gap_question":
            return ["module_or_file", "problem", "change_plan", "truth_boundary", "source_origin"]
        if intent in {"system_update_execution_request", "system_update_manifest_request", "update_manifest_request"}:
            return ["version", "priority_list", "target_files", "new_files", "tests", "acceptance_criteria"]
        if intent.startswith("creative_text"):
            return ["source_preservation", "change_list_if_changed", "original_text_boundary"]
        if intent in {"practical_repair_advice", "automotive_warning_light_question"}:
            return ["problem", "tools_or_materials", "steps", "risks", "when_to_stop"]
        if intent in {"dictionary_lookup_request", "language_question"}:
            return ["term", "language", "source_or_cache", "truth_boundary"]
        if intent in {"self_state_question", "reciprocal_self_state_question", "self_preference_question", "self_expression_request", "self_state_time_awareness"}:
            return ["operational_state", "truth_boundary", "no_random_memory_excerpt"]
        if intent == "sleep_closure_statement":
            return ["current_turn_closure", "warmth", "no_diagnostics", "no_random_memory_excerpt"]
        if intent == "time_awareness_question":
            return ["current_time", "timezone", "source_or_fallback", "truth_boundary"]
        if intent == "current_time_question":
            return ["current_time", "timezone", "source_or_fallback", "truth_boundary"]
        if intent == "memory_experience_question":
            return ["memory_content", "source_or_index_status", "truth_boundary", "no_current_turn_echo"]
        return []

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "entry_count": len(self.HANDLERS), "priority_rule": "DialogueIntentClassifier > RouteRegistry > LegacyMarkers", "priorities": self.PRIORITIES}
