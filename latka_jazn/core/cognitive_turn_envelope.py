from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
import hashlib
import json
import uuid
from typing import Any

from latka_jazn.core.full_canon_model_context import (
    build_full_canon_model_context,
    build_host_generation_contract,
)

SCHEMA_VERSION = "cognitive_turn_envelope/v14.6.2"
TRACE_SCHEMA_VERSION = "turn_trace/v14.6.2"


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


@dataclass(slots=True)
class TurnTrace:
    """One turn identity carried through runtime, cognitive frame and final text."""

    turn_id: str
    trace_id: str
    timestamp_header: str
    timezone: str
    runtime_mode: str
    client: str
    lifecycle: str
    schema_version: str = TRACE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CognitiveTurnEnvelope:
    """One integration envelope for state, canon, memory and visible response."""

    trace: TurnTrace
    runtime_version: str
    user_text: str
    cognitive_frame: dict[str, Any]
    client_context: dict[str, Any] = field(default_factory=dict)
    affect_mix: dict[str, Any] = field(default_factory=dict)
    dialogue_state: dict[str, Any] = field(default_factory=dict)
    conversation_decision: dict[str, Any] = field(default_factory=dict)
    runtime_turn_contract: dict[str, Any] = field(default_factory=dict)
    final_response_contract: dict[str, Any] = field(default_factory=dict)
    final_visible_text: str | None = None
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_cognitive_frame(
        cls,
        frame: dict[str, Any],
        *,
        user_text: str,
        client_context: dict[str, Any] | None = None,
        runtime_mode: str = "process_turn",
    ) -> "CognitiveTurnEnvelope":
        client_context = dict(client_context or {})
        trace_packet = dict(frame.get("turn_trace") or {})
        turn_id = str(trace_packet.get("turn_id") or frame.get("turn_id") or uuid.uuid4())
        trace_id = str(trace_packet.get("trace_id") or frame.get("trace_id") or uuid.uuid4())
        timestamp_header = str(trace_packet.get("timestamp_header") or frame.get("timestamp") or "")
        timezone = str(trace_packet.get("timezone") or frame.get("response_format", {}).get("timezone") or "Europe/Warsaw")
        client = str(trace_packet.get("client") or client_context.get("client") or frame.get("client_context", {}).get("client") or "runtime")
        lifecycle = str(trace_packet.get("lifecycle") or client_context.get("lifecycle") or frame.get("client_context", {}).get("lifecycle") or "one_shot")
        trace = TurnTrace(
            turn_id=turn_id,
            trace_id=trace_id,
            timestamp_header=timestamp_header,
            timezone=timezone,
            runtime_mode=runtime_mode,
            client=client,
            lifecycle=lifecycle,
        )
        copied = copy.deepcopy(frame)
        copied["turn_trace"] = trace.to_dict()
        copied["turn_id"] = turn_id
        copied["trace_id"] = trace_id
        full_canon = build_full_canon_model_context(copied)
        copied["full_canon_model_context"] = full_canon
        copied["host_generation_contract"] = build_host_generation_contract(full_canon)
        copied["full_canon_sha256"] = full_canon.get("immutable_canon_sha256")
        return cls(
            trace=trace,
            runtime_version=str(frame.get("runtime_version") or "unknown"),
            user_text=user_text,
            cognitive_frame=copied,
            client_context=client_context,
        )

    def attach_affect_mix(self, affect_mix: dict[str, Any]) -> None:
        self.affect_mix = dict(affect_mix or {})
        self.cognitive_frame["turn_affect_mix"] = self.affect_mix
        self._refresh_full_canon_dynamic_context()

    def attach_dialogue_state(self, dialogue_state: dict[str, Any]) -> None:
        self.dialogue_state = dict(dialogue_state or {})
        self.cognitive_frame["dialogue_state"] = self.dialogue_state
        self._refresh_full_canon_dynamic_context()

    def attach_conversation_decision(self, decision: dict[str, Any]) -> None:
        self.conversation_decision = dict(decision or {})
        self.cognitive_frame["conversation_decision"] = self.conversation_decision

    def attach_final_response_contract(self, contract: dict[str, Any], final_visible_text: str) -> None:
        self.final_response_contract = dict(contract or {})
        self.final_visible_text = final_visible_text
        self.cognitive_frame["final_response_contract"] = self.final_response_contract
        self.cognitive_frame["final_visible_reply_sha256"] = hashlib.sha256(final_visible_text.encode("utf-8")).hexdigest()

    def attach_runtime_turn_contract(self, contract: dict[str, Any]) -> None:
        self.runtime_turn_contract = dict(contract or {})
        self.cognitive_frame["runtime_turn_contract"] = self.runtime_turn_contract

    def _refresh_full_canon_dynamic_context(self) -> None:
        full_canon = build_full_canon_model_context(self.cognitive_frame)
        self.cognitive_frame["full_canon_model_context"] = full_canon
        self.cognitive_frame["host_generation_contract"] = build_host_generation_contract(full_canon)
        self.cognitive_frame["full_canon_sha256"] = full_canon.get("immutable_canon_sha256")

    def to_dict(self) -> dict[str, Any]:
        full_canon = self.cognitive_frame.get("full_canon_model_context")
        if not isinstance(full_canon, dict):
            self._refresh_full_canon_dynamic_context()
            full_canon = self.cognitive_frame.get("full_canon_model_context") or {}
        host_contract = self.cognitive_frame.get("host_generation_contract")
        if not isinstance(host_contract, dict):
            host_contract = build_host_generation_contract(full_canon)
            self.cognitive_frame["host_generation_contract"] = host_contract
        data = {
            "schema_version": self.schema_version,
            "runtime_version": self.runtime_version,
            "trace": self.trace.to_dict(),
            "user_text": self.user_text,
            "client_context": self.client_context,
            "affect_mix": self.affect_mix,
            "dialogue_state": self.dialogue_state,
            "conversation_decision": self.conversation_decision,
            "runtime_turn_contract": self.runtime_turn_contract,
            "final_response_contract": self.final_response_contract,
            "final_visible_text": self.final_visible_text,
            "full_canon_model_context": full_canon,
            "full_canon_sha256": full_canon.get("immutable_canon_sha256"),
            "host_generation_contract": host_contract,
            "cognitive_frame": self.cognitive_frame,
            "payload_sha256": _sha256_json({
                "trace": self.trace.to_dict(),
                "user_text": self.user_text,
                "cognitive_frame": self.cognitive_frame,
                "final_visible_text": self.final_visible_text,
            }),
            "truth_boundary": (
                "Koperta tury spina realne wywołanie runtime, pełny kanon, cognitive-frame i finalną odpowiedź. "
                "Nie oznacza stałego procesu w tle ani nie przenosi źródła tożsamości do hosta."
            ),
        }
        return data
