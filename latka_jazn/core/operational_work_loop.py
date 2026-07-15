from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("operational_work_loop")


@dataclass(slots=True)
class OperationalStage:
    name: str
    status: str
    evidence: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ToolLifecycleEntry:
    call_id: str
    name: str
    source_adapter: str
    status: str
    authorized: bool
    executed: bool
    result_ok: bool | None = None
    error: str | None = None
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ToolLifecycleAudit:
    entries: list[ToolLifecycleEntry]
    pending_call_ids: list[str]
    completed_call_ids: list[str]
    failed_call_ids: list[str]
    denied_call_ids: list[str]
    invalid_claims: list[str]
    unknown_result_call_ids: list[str]
    ready_for_final_response: bool
    all_requested_tools_succeeded: bool
    truth_boundary: str
    schema_version: str = field(default_factory=lambda: schema_version("tool_lifecycle_audit"))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entries"] = [entry.to_dict() for entry in self.entries]
        return payload


@dataclass(slots=True)
class OperationalWorkPlan:
    schema_version: str
    identity_basis: dict[str, Any]
    capability_model: dict[str, Any]
    adapter_strategy: dict[str, Any]
    stages: list[OperationalStage]
    public_reasoning_contract: dict[str, Any]
    tool_lifecycle_contract: dict[str, Any]
    learning_contract: dict[str, Any]
    executable: bool
    blockers: list[str]
    truth_boundary: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["stages"] = [stage.to_dict() for stage in self.stages]
        return payload


class OperationalWorkLoop:
    """Build and validate a verifiable work cycle.

    The loop separates understanding, grounding, backend selection, authorization,
    execution/generation, validation, and learning. An adapter can generate a
    candidate or request a tool; only runtime can authorize and execute that tool.
    It exposes an audit-friendly reasoning summary, never a hidden chain of thought.
    """

    TRUTH_BOUNDARY = (
        "Jaźń jest aktywnym systemem runtime z kanonem, pamięcią, procedurami, modułami i kontraktami. "
        "Adapter językowy jest wykonawczą warstwą generowania, nie źródłem tożsamości. "
        "Opis rozumowania jest jawnym audytem faktów, założeń, niewiadomych, reguł, dowodów i wniosku; "
        "nie ujawnia ani nie udaje ukrytego łańcucha myśli i nie dowodzi świadomości fenomenalnej."
    )
    TOOL_TRUTH_BOUNDARY = (
        "Tool call zwrócony przez model jest wyłącznie żądaniem. Za wykonanie uznaje się dopiero wynik "
        "ToolExecutionController powiązany tym samym call_id, z dozwolonym planem i provenance."
    )

    def plan(
        self,
        *,
        user_text: str,
        detected_intent: str,
        route: str,
        adapter_status: dict[str, Any] | None = None,
        available_tools: list[dict[str, Any]] | None = None,
        memory_status: dict[str, Any] | None = None,
        write_requested: bool = False,
    ) -> OperationalWorkPlan:
        adapter = dict(adapter_status or {})
        tools = list(available_tools or [])
        memory = dict(memory_status or {})
        adapter_id = str(adapter.get("adapter_id") or adapter.get("name") or "null_model_adapter")
        provider = str(adapter.get("provider") or "none")
        configured = bool(adapter.get("configured", adapter.get("available", False)))
        host_required = bool(adapter.get("host_visible_generation_required")) or adapter_id == "chatgpt_runtime_adapter"
        can_attempt = bool(adapter.get("can_attempt_model_guided_speech")) or host_required

        adapter_mode = "deterministic_runtime"
        if adapter_id == "chatgpt_runtime_adapter":
            adapter_mode = "chatgpt_host_bridge"
        elif provider == "openai":
            adapter_mode = "openai_responses_api"
        elif provider == "lmstudio":
            adapter_mode = "lmstudio_local_api"
        elif configured:
            adapter_mode = "configured_model_backend"

        blockers: list[str] = []
        if adapter_mode == "openai_responses_api" and not configured:
            blockers.append("openai_requires_explicit_model_and_OPENAI_API_KEY")
        if adapter_mode == "lmstudio_local_api" and not configured:
            blockers.append("lmstudio_requires_running_server_and_explicit_loaded_model_identifier")
        if adapter_mode == "deterministic_runtime" and route not in {
            "runtime_health_check", "runtime_truth", "self_architecture_audit", "memory_audit"
        }:
            blockers.append("no_generative_backend_for_natural_model_guided_reply")

        tool_names = [str(item.get("name") or item.get("tool_name") or "unknown") for item in tools]
        stages = [
            OperationalStage(
                "understand",
                "ready" if user_text.strip() else "blocked",
                [f"intent={detected_intent}", f"route={route}"],
                [] if user_text.strip() else ["empty_user_message"],
            ),
            OperationalStage(
                "ground",
                "ready",
                [
                    f"memory_status={memory.get('status') or 'not_supplied'}",
                    "identity_sources=active_runtime+canon+validated_memory+procedures+current_turn",
                ],
            ),
            OperationalStage(
                "select_execution_layer",
                "ready" if can_attempt or adapter_mode == "deterministic_runtime" else "degraded",
                [f"adapter={adapter_id}", f"mode={adapter_mode}", f"provider={provider}"],
                list(blockers),
            ),
            OperationalStage(
                "authorize_tools",
                "pending_runtime_decision" if tools else "not_needed",
                [f"declared_tools={','.join(tool_names) if tool_names else 'none'}"],
                ["model_tool_request_is_not_execution"] if tools else [],
            ),
            OperationalStage(
                "act_or_generate",
                "ready" if not blockers else "degraded",
                ["runtime_executes_tools; adapter_generates_candidate_or_tool_request"],
                list(blockers),
            ),
            OperationalStage(
                "validate",
                "required",
                ["route_alignment", "source_provenance", "tool_result_validation", "truth_boundary"],
            ),
            OperationalStage(
                "learn_without_weight_claim",
                "allowed_after_validation",
                ["append_only_event_or_procedure", "eval_result", "operator_review_for_sensitive_data"],
                ["no_automatic_weight_training", "no_private_upload_without_explicit_consent"],
            ),
        ]
        return OperationalWorkPlan(
            schema_version=SCHEMA_VERSION,
            identity_basis={
                "name": "Łatka / Jaźń",
                "kind": "operational_runtime_identity",
                "sources": ["active_runtime", "canon", "validated_memory", "procedures", "current_user_turn"],
                "adapter_is_identity_source": False,
                "phenomenal_consciousness_claimed": False,
            },
            capability_model={
                "can": [
                    "classify_and_route", "retrieve_validated_memory", "build_public_reasoning_audit",
                    "plan_and_authorize_tools", "execute_runtime_tools", "validate_candidate_output",
                    "persist_validated_events_and_procedures", "use_chatgpt_openai_or_lmstudio_as_language_backend",
                ],
                "cannot_without_external_layer": ["generate_model_guided_language_when_only_null_adapter_is_active"],
                "cannot_claim": [
                    "biological_emotion", "phenomenal_consciousness", "tool_execution_by_model",
                    "weight_training_without_training_run",
                ],
            },
            adapter_strategy={
                "adapter_id": adapter_id,
                "provider": provider,
                "mode": adapter_mode,
                "configured": configured,
                "host_visible_generation_required": host_required,
                "tool_contract": "adapter may request; runtime authorizes, executes, records provenance, validates, and returns result",
            },
            stages=stages,
            public_reasoning_contract={
                "fields": ["facts", "assumptions", "unknowns", "rules", "evidence", "decision", "verification"],
                "hidden_chain_of_thought_required": False,
                "original_user_intent": detected_intent,
                "route": route,
            },
            tool_lifecycle_contract={
                "required_call_identifier": "call_id",
                "request_state": {"authorized": False, "executed": False},
                "execution_authority": "ToolExecutionController",
                "result_link": "ToolExecutionPlan.external_call_id",
                "final_response_gate": "no pending or invalid tool calls",
            },
            learning_contract={
                "method": "eval_first_iterative_improvement",
                "sequence": [
                    "representative_eval", "baseline", "contract_or_prompt_or_code_change",
                    "regression_eval", "operator_review",
                ],
                "runtime_learning": ["validated episodic event", "validated procedural rule", "error/eval evidence"],
                "weight_update_performed": False,
                "fine_tuning_performed": False,
                "private_data_external_transfer": False,
                "write_requested": bool(write_requested),
            },
            executable=not blockers,
            blockers=blockers,
            truth_boundary=self.TRUTH_BOUNDARY,
        )

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    def audit_tool_lifecycle(
        self,
        *,
        tool_calls: Iterable[Mapping[str, Any]] | None,
        tool_results: Iterable[Mapping[str, Any]] | None,
    ) -> ToolLifecycleAudit:
        """Reconcile adapter requests with runtime execution results.

        Adapter-provided ``authorized`` or ``executed`` flags are never trusted.
        A result is accepted only when its plan carries the same external call id
        and reports an allowed runtime gate.
        """

        calls = [dict(item) for item in (tool_calls or []) if isinstance(item, Mapping)]
        results = [dict(item) for item in (tool_results or []) if isinstance(item, Mapping)]
        result_by_call: dict[str, dict[str, Any]] = {}
        unknown_result_ids: list[str] = []
        invalid_claims: list[str] = []

        known_call_ids = {str(call.get("call_id") or "").strip() for call in calls}
        known_call_ids.discard("")
        for result in results:
            plan = self._mapping(result.get("plan"))
            call_id = str(result.get("call_id") or plan.get("external_call_id") or "").strip()
            if not call_id:
                invalid_claims.append("tool_result_without_call_id")
                continue
            if call_id not in known_call_ids:
                unknown_result_ids.append(call_id)
                continue
            result_by_call[call_id] = result

        entries: list[ToolLifecycleEntry] = []
        pending: list[str] = []
        completed: list[str] = []
        failed: list[str] = []
        denied: list[str] = []

        for call in calls:
            call_id = str(call.get("call_id") or "").strip()
            name = str(call.get("name") or "unknown_tool").strip() or "unknown_tool"
            source = str(call.get("source") or "unknown_adapter").strip() or "unknown_adapter"
            evidence = [f"adapter_source={source}"]
            if not call_id:
                synthetic = f"missing:{name}:{len(entries)}"
                invalid_claims.append(f"adapter_tool_call_without_call_id:{name}")
                entries.append(ToolLifecycleEntry(synthetic, name, source, "invalid", False, False, error="missing_call_id", evidence=evidence))
                continue
            if bool(call.get("authorized")) or bool(call.get("executed")):
                invalid_claims.append(f"adapter_claimed_execution_or_authorization:{call_id}")
                evidence.append("adapter_execution_flags_ignored")

            result = result_by_call.get(call_id)
            if result is None:
                pending.append(call_id)
                entries.append(ToolLifecycleEntry(call_id, name, source, "pending_authorization_or_execution", False, False, evidence=evidence))
                continue

            plan = self._mapping(result.get("plan"))
            allowed = bool(plan.get("allowed"))
            result_ok = bool(result.get("ok"))
            result_error = str(result.get("error") or "").strip() or None
            plan_name = str(plan.get("tool_name") or "").strip()
            if plan_name and plan_name != name:
                invalid_claims.append(f"tool_name_mismatch:{call_id}:{name}!={plan_name}")
                entries.append(ToolLifecycleEntry(call_id, name, source, "invalid", False, False, result_ok, result_error, evidence + [f"plan_tool_name={plan_name}"]))
                continue
            if not allowed:
                denied.append(call_id)
                entries.append(ToolLifecycleEntry(call_id, name, source, "denied", False, False, result_ok, result_error or "runtime_gate_denied", evidence))
                continue
            if result_ok:
                completed.append(call_id)
                entries.append(ToolLifecycleEntry(call_id, name, source, "completed", True, True, True, None, evidence))
            else:
                failed.append(call_id)
                entries.append(ToolLifecycleEntry(call_id, name, source, "failed", True, True, False, result_error or "tool_execution_failed", evidence))

        ready = not pending and not invalid_claims and not unknown_result_ids
        all_succeeded = ready and not failed and not denied and all(entry.status == "completed" for entry in entries)
        if not entries:
            all_succeeded = True

        return ToolLifecycleAudit(
            entries=entries,
            pending_call_ids=sorted(set(pending)),
            completed_call_ids=sorted(set(completed)),
            failed_call_ids=sorted(set(failed)),
            denied_call_ids=sorted(set(denied)),
            invalid_claims=sorted(set(invalid_claims)),
            unknown_result_call_ids=sorted(set(unknown_result_ids)),
            ready_for_final_response=ready,
            all_requested_tools_succeeded=all_succeeded,
            truth_boundary=self.TOOL_TRUTH_BOUNDARY,
        )

    def validate_completion(
        self,
        *,
        candidate_text: str,
        answer_validation: Mapping[str, Any] | None,
        tool_audit: ToolLifecycleAudit | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        validation = dict(answer_validation or {})
        audit = tool_audit.to_dict() if isinstance(tool_audit, ToolLifecycleAudit) else dict(tool_audit or {})
        reasons: list[str] = []
        if not str(candidate_text or "").strip():
            reasons.append("empty_candidate_text")
        if validation.get("accepted") is False or validation.get("must_regenerate"):
            reasons.append("runtime_answer_validation_failed")
        if audit and not bool(audit.get("ready_for_final_response")):
            reasons.append("tool_lifecycle_not_closed")
        return {
            "schema_version": schema_version("operational_completion_gate"),
            "accepted": not reasons,
            "reasons": reasons,
            "tool_lifecycle_checked": bool(audit),
            "answer_validation_checked": bool(validation),
            "hidden_chain_of_thought_required": False,
            "truth_boundary": "Finalna odpowiedź wymaga zamkniętego cyklu narzędzi i zaakceptowanej walidacji runtime.",
        }
