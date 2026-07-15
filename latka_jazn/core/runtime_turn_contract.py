from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from latka_jazn.core.full_canon_model_context import (
    build_full_canon_model_context,
    render_full_canon_developer_instructions,
    validate_full_canon_model_context,
)
from latka_jazn.model_adapters.base import ModelAdapterRequest
from latka_jazn.version import schema_version


@dataclass(slots=True)
class RuntimeTurnContract:
    """One truth-bearing contract shared by every visible turn entry point."""

    turn_id: str
    trace_id: str
    detected_intent: str
    route: str
    handler_name: str
    runtime_exact_text: str
    final_visible_text: str
    host_interpretation: str | None
    template_origin: dict[str, Any]
    source_origin_detail: str
    fallback_classification: str
    final_visible_integrity: dict[str, Any]
    can_generate_model_guided_speech: bool
    requires_host_model: bool
    response_generation_mode: str
    validation: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    retry_limit: int = 1
    schema_version: str = schema_version("runtime_turn_contract")
    truth_boundary: str = (
        "Kontrakt rozdziela tekst runtime, tekst widoczny i ewentualną interpretację hosta. "
        "Szablon, handler regułowy, repair i degraded fallback nie są dynamiczną wypowiedzią model-guided."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def for_model_request(
        cls,
        *,
        user_text: str,
        detected_intent: str,
        route: str,
        runtime_exact_text: str,
        system_context: dict[str, Any] | None = None,
    ) -> "RuntimeTurnContract":
        context = dict(system_context or {})
        turn_id = str(context.get("turn_id") or context.get("trace_id") or "model-candidate")
        return cls(
            turn_id=turn_id,
            trace_id=str(context.get("trace_id") or turn_id),
            detected_intent=detected_intent,
            route=route,
            handler_name=str(context.get("handler_name") or "candidate_request"),
            runtime_exact_text=runtime_exact_text,
            final_visible_text="",
            host_interpretation=None,
            template_origin={},
            source_origin_detail="runtime_turn_contract/model_adapter_request",
            fallback_classification="candidate_not_yet_validated",
            final_visible_integrity={"valid": False, "reason": "candidate_not_yet_validated"},
            can_generate_model_guided_speech=False,
            requires_host_model=False,
            response_generation_mode="model_candidate_request",
            validation={"accepted": False, "state": "not_yet_validated"},
        )

    def to_model_adapter_request(
        self,
        *,
        user_text: str | None = None,
        system_context: dict[str, Any] | None = None,
    ) -> ModelAdapterRequest:
        context = dict(system_context or {})
        full_canon = context.get("full_canon_model_context")
        if not isinstance(full_canon, dict) or not validate_full_canon_model_context(full_canon).get("ok"):
            full_canon = build_full_canon_model_context(context)
        canon_validation = validate_full_canon_model_context(full_canon)
        if not canon_validation.get("ok"):
            raise ValueError("full canon model context validation failed: " + "; ".join(canon_validation.get("violations") or []))

        context["full_canon_model_context"] = full_canon
        context.update(
            {
                "turn_id": self.turn_id,
                "trace_id": self.trace_id,
                "detected_intent": self.detected_intent,
                "route": self.route,
                "handler_name": self.handler_name,
                "runtime_exact_text": self.runtime_exact_text,
                "truth_boundary": self.truth_boundary,
                "source_origin_detail": self.source_origin_detail,
            }
        )
        return ModelAdapterRequest(
            prompt=str(user_text if user_text is not None else context.get("user_message") or ""),
            session_id=str(context.get("session_id") or self.turn_id),
            instructions=render_full_canon_developer_instructions(full_canon),
            system_context=context,
            metadata={
                "runtime_turn_contract_schema": self.schema_version,
                "candidate_requires_runtime_validation": True,
                "full_canon_required": True,
                "full_canon_sha256": str(full_canon.get("immutable_canon_sha256") or ""),
                "full_canon_schema_version": str(full_canon.get("schema_version") or ""),
            },
        )
