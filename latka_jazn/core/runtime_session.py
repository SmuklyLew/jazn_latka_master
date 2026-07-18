from __future__ import annotations
from dataclasses import asdict
from typing import Any
from latka_jazn.config import JaznConfig
from latka_jazn.core.engine import JaznEngine
from latka_jazn.core.runtime_session_state import RuntimeSessionStateStore
from latka_jazn.core.session_provenance import build_session_provenance, repair_final_visible_integrity, validate_final_visible_integrity
from latka_jazn.core.runtime_truth_gate import apply_runtime_truth_gate
from latka_jazn.core.visible_integrity import enforce_integrity_consensus
from latka_jazn.core.turn_execution import TurnExecutionContext
from latka_jazn.core.turn_timeout import runtime_turn_timeout_seconds
from latka_jazn.memory.memory_tier_status import inspect_memory_tier_store
from latka_jazn.memory.runtime_memory_v151 import RuntimeMemoryWriteContext
from latka_jazn.memory.runtime_memory_v151_install import install_runtime_memory_v151

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("runtime_session")

class JaznRuntimeSession:
    """Wspólny rdzeń one-shot, --runtime-preview, --chat i --chat-gpt.

    Różnice między trybami dotyczą tylko cyklu życia procesu i formatu I/O; każda tura
    przechodzi przez JaznEngine.process_turn().
    """
    def __init__(
        self,
        config: JaznConfig | None = None,
        *,
        session_id: str | None = None,
        no_carryover: bool = False,
        source_client: str = "runtime_session",
    ) -> None:
        self.config = config or JaznConfig()
        self.engine = JaznEngine(self.config)
        self.memory_v151_install_status = install_runtime_memory_v151(self.engine)
        self.state_store = RuntimeSessionStateStore(self.config.root)
        self.state = self.state_store.load_or_create(session_id=session_id, source_client=source_client, no_carryover=no_carryover)
        self.no_carryover = no_carryover
        self._turn_count = 0

    def process_user_text(
        self,
        user_text: str,
        *,
        client: str = "runtime_session",
        lifecycle: str = "runtime_session",
        session_id_source: str | None = None,
        process_reused: bool = True,
        request_id: str | None = None,
        _turn_context: TurnExecutionContext | None = None,
    ) -> dict[str, Any]:
        config = getattr(self, "config", None)
        audit_db_path = getattr(config, "audit_db_path", None) if config is not None else None
        turn_context = _turn_context or TurnExecutionContext.create(
            request_id=request_id,
            session_id=self.state.session_id,
            timeout_seconds=runtime_turn_timeout_seconds(config),
            audit_db_path=audit_db_path,
        )
        persistence_available = config is not None
        memory_context_token = None
        bind_memory_context = getattr(getattr(self.engine, "runtime_memory", None), "bind_context", None)
        if callable(bind_memory_context):
            memory_context_token = bind_memory_context(
                RuntimeMemoryWriteContext(
                    session_id=self.state.session_id,
                    turn_id=turn_context.turn_id,
                    actor="user",
                    active_goal="validated_runtime_turn",
                )
            )
        if not persistence_available:
            turn_context.record_technical_event(
                "runtime_session_config_unavailable",
                {
                    "canonical_persistence_available": False,
                    "audit_persistence_available": False,
                },
            )
        ctx = {
            "client": client,
            "lifecycle": lifecycle,
            "session_id": self.state.session_id,
            "no_carryover": self.no_carryover,
            "request_id": turn_context.request_id,
            "_turn_context": turn_context,
        }
        if not self.no_carryover and self.state.last_user_text:
            ctx["previous_user_text"] = self.state.last_user_text
            ctx["previous_detected_intent"] = self.state.last_intent
            ctx["previous_runtime_route"] = self.state.last_route
        try:
            envelope = self.engine.process_turn(user_text, client_context=ctx)
            with turn_context.stage("final_result_serialization"):
                env = envelope.to_dict()
                decision = (env.get("cognitive_frame") or {}).get("conversation_decision") or {}
                runtime_provenance = decision.get("runtime_provenance") or {}
                result = {
                    "schema_version": SCHEMA_VERSION,
                    "session": self.state.to_dict(),
                    "session_id_source": session_id_source or "generated",
                    "trace": env.get("trace"),
                    "conversation_decision": decision,
                    "runtime_turn_contract": env.get("runtime_turn_contract"),
                    "final_response_contract": env.get("final_response_contract"),
                    "final_visible_text": env.get("final_visible_text"),
                    "runtime_provenance": runtime_provenance,
                    "exact_runtime_text": runtime_provenance.get("exact_runtime_text"),
                }

            with turn_context.stage("integrity_validation"):
                engine_contract_integrity = dict(
                    ((result.get("final_response_contract") or {}).get("final_visible_integrity") or {})
                )
                if isinstance(engine_contract_integrity.get("valid"), bool):
                    result["final_visible_integrity_pre_repair_contract_valid"] = engine_contract_integrity["valid"]
                result, integrity_repair_audit = repair_final_visible_integrity(result)
                result["final_visible_integrity"] = validate_final_visible_integrity(result)
                if integrity_repair_audit:
                    result["final_visible_integrity"]["repair_audit"] = integrity_repair_audit
                    result["final_visible_integrity_repair_audit"] = integrity_repair_audit
                contract = dict(result.get("final_response_contract") or {})
                contract["final_visible_integrity"] = dict(result["final_visible_integrity"])
                result["final_response_contract"] = contract
                decision = dict(result.get("conversation_decision") or {})
                decision["origin_truth_valid"] = bool(result["final_visible_integrity"].get("origin_truth_valid"))
                decision["origin_truth_errors"] = list(result["final_visible_integrity"].get("errors") or [])
                result["conversation_decision"] = decision

            with turn_context.stage("runtime_truth_gate"):
                result, gate_payload = apply_runtime_truth_gate(result)
            with turn_context.stage("consensus"):
                result, consensus = enforce_integrity_consensus(result)
                result["final_visible_integrity_consensus"] = consensus

            gate_payload = dict(result.get("runtime_truth_gate") or gate_payload)
            if gate_payload.get("normal_response_allowed") is False:
                result["final_visible_integrity"]["runtime_truth_gate_blocked"] = not bool(gate_payload.get("ok"))
                result["final_visible_integrity"]["truthful_degraded_disclosure"] = bool(gate_payload.get("truthful_degraded_disclosure"))
                result["final_visible_integrity"]["runtime_truth_gate_errors"] = list(gate_payload.get("errors") or [])

            integrity = result.get("final_visible_integrity") or {}
            result["ok"] = bool(
                str(result.get("final_visible_text") or "").strip()
                and integrity.get("valid") is True
                and integrity.get("consensus") is True
                and consensus.get("mismatch") is False
                and gate_payload.get("ok") is True
                and gate_payload.get("normal_response_allowed") is not False
                and result.get("normal_response_blocked") is not True
                and turn_context.can_continue()
            )
            if persistence_available:
                commit_status = turn_context.commit_if_allowed(result, job_status="completed")
            else:
                commit_status = turn_context.reject_staging(reason="runtime_config_unavailable")
                commit_status["available"] = False
                commit_status["diagnostic"] = "canonical persistence skipped because session config is unavailable"
            result["canonical_persistence"] = commit_status
            if not commit_status.get("committed"):
                result["ok"] = False

            result["memory_v151"] = {
                "install": self.memory_v151_install_status.to_dict(),
                "store": inspect_memory_tier_store(
                    self.memory_v151_install_status.database_path,
                    full=False,
                ).to_dict(),
                "truth_boundary": (
                    "Status L1/L2/L3 jest diagnostyką po zatwierdzeniu tury. "
                    "Nie dowodzi poprawnego recall ani aktywnej tożsamości."
                ),
            }

            if result["ok"]:
                self.state.update(
                    user_text=user_text,
                    intent=str(decision.get("detected_user_intent") or "unknown"),
                    route=str(decision.get("route") or "unknown"),
                )
                save_status = self.state_store.save(self.state)
                self._turn_count += 1
            else:
                save_status = {
                    "saved": False,
                    "reason": commit_status.get("reason") or "turn_not_committed",
                }
            result["session"] = self.state.to_dict()
            with turn_context.stage("provenance"):
                session_provenance = build_session_provenance(
                    session_id=self.state.session_id,
                    client=client,
                    lifecycle=lifecycle,
                    process_reused=process_reused,
                    engine_reused_between_turns=True,
                    load_metadata=self.state_store.last_load_metadata,
                    save_status=save_status,
                )
                session_provenance["final_visible_integrity_valid"] = bool(integrity.get("valid"))
                session_provenance["memory_v151_ready"] = bool(
                    ((result.get("memory_v151") or {}).get("store") or {}).get("ready")
                )
                result["session_provenance"] = session_provenance

            turn_context.finalize_total(status="completed" if result["ok"] else "rejected")
            result["turn_audit_persistence"] = turn_context.persist_audit()
            result["turn_telemetry"] = turn_context.snapshot()
            return result
        except BaseException as exc:
            turn_context.reject_staging(reason=type(exc).__name__)
            turn_context.record_technical_event(
                "runtime_turn_failed",
                {"error_code": type(exc).__name__, "error": str(exc)},
            )
            turn_context.finalize_total(status="failed", error_code=type(exc).__name__)
            turn_context.persist_audit(event_type="runtime_turn_failed")
            raise
        finally:
            if memory_context_token is not None:
                reset_memory_context = getattr(getattr(self.engine, "runtime_memory", None), "reset_context", None)
                if callable(reset_memory_context):
                    reset_memory_context(memory_context_token)

    def close(self) -> None:
        self.state_store.save(self.state)
        self.engine.shutdown()
