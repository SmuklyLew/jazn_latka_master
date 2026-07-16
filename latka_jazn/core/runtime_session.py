from __future__ import annotations
from dataclasses import asdict
from typing import Any
from latka_jazn.config import JaznConfig
from latka_jazn.core.engine import JaznEngine
from latka_jazn.core.runtime_session_state import RuntimeSessionStateStore
from latka_jazn.core.session_provenance import build_session_provenance, repair_final_visible_integrity, validate_final_visible_integrity
from latka_jazn.core.runtime_truth_gate import apply_runtime_truth_gate
from latka_jazn.core.visible_integrity import enforce_integrity_consensus

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
    ) -> dict[str, Any]:
        ctx = {"client": client, "lifecycle": lifecycle, "session_id": self.state.session_id, "no_carryover": self.no_carryover}
        if not self.no_carryover and self.state.last_user_text:
            ctx["previous_user_text"] = self.state.last_user_text
            ctx["previous_detected_intent"] = self.state.last_intent
            ctx["previous_runtime_route"] = self.state.last_route
        envelope = self.engine.process_turn(user_text, client_context=ctx)
        env = envelope.to_dict()
        decision = (env.get("cognitive_frame") or {}).get("conversation_decision") or {}
        self.state.update(user_text=user_text, intent=str(decision.get("detected_user_intent") or "unknown"), route=str(decision.get("route") or "unknown"))
        save_status = self.state_store.save(self.state)
        self._turn_count += 1
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
            "session_provenance": build_session_provenance(
                session_id=self.state.session_id,
                client=client,
                lifecycle=lifecycle,
                process_reused=process_reused,
                engine_reused_between_turns=True,
                load_metadata=self.state_store.last_load_metadata,
                save_status=save_status,
            ),
        }
        result, integrity_repairs = repair_final_visible_integrity(result)
        result["final_visible_integrity"] = validate_final_visible_integrity(result)
        contract = dict(result.get("final_response_contract") or {})
        contract["final_visible_integrity"] = dict(result["final_visible_integrity"])
        result["final_response_contract"] = contract
        decision = dict(result.get("conversation_decision") or {})
        decision["origin_truth_valid"] = bool(result["final_visible_integrity"].get("origin_truth_valid"))
        decision["origin_truth_errors"] = list(result["final_visible_integrity"].get("errors") or [])
        result["conversation_decision"] = decision
        session_provenance = dict(result.get("session_provenance") or {})
        session_provenance["final_visible_integrity_valid"] = bool(result["final_visible_integrity"].get("valid"))
        result["session_provenance"] = session_provenance
        if integrity_repairs:
            result["final_visible_integrity"]["repairs"] = integrity_repairs
        result, gate_payload = apply_runtime_truth_gate(result)
        result, consensus = enforce_integrity_consensus(result)
        result["final_visible_integrity_consensus"] = consensus
        gate_payload = dict(result.get("runtime_truth_gate") or gate_payload)
        if gate_payload.get("normal_response_allowed") is False:
            result["final_visible_integrity"]["runtime_truth_gate_blocked"] = not bool(gate_payload.get("ok"))
            result["final_visible_integrity"]["truthful_degraded_disclosure"] = bool(gate_payload.get("truthful_degraded_disclosure"))
            result["final_visible_integrity"]["runtime_truth_gate_errors"] = list(gate_payload.get("errors") or [])
        return result

    def close(self) -> None:
        self.state_store.save(self.state)
        self.engine.shutdown()
