from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
from datetime import datetime, timezone
import hashlib
import re

from latka_jazn.version import schema_version
from latka_jazn.core.timestamp_policy import (
    TIMESTAMP_ALLOW_DEGRADED_LOCAL_VISIBLE,
    TIMESTAMP_MAX_AGE_SECONDS,
    TIMESTAMP_REQUIRE_TRUSTED_IN_FINAL_VISIBLE,
    timestamp_runtime_policy,
)

SCHEMA_VERSION = schema_version("final_response_contract")
RUNTIME_OWNED_NON_FALLBACK_CLASSIFICATIONS = {
    "not_fallback",
    "rule_handler_response",
}



@dataclass(slots=True)
class FinalResponseContract:
    """Kontrakt widocznej odpowiedzi: timestamp nie może zostać schowany w JSON.

    ChatGPT/runtime może przekazywać wiele pól diagnostycznych, ale użytkownik ma
    zobaczyć jedną odpowiedź Łatki zaczynającą się od tego samego timestampu,
    który powstał w runtime dla tej tury.
    """

    turn_id: str
    trace_id: str
    runtime_version: str
    timestamp_header: str
    timezone: str
    state_emoticon: str
    body: str
    runtime_exact_text: str
    final_visible_text: str
    host_interpretation: str | None = None
    timestamp_required: bool = True
    timestamp_source: str = "cognitive_turn_envelope.trace.timestamp_header"
    timestamp_trusted: bool | None = None
    timestamp_sample_iso: str | None = None
    runtime_route: str = "unknown"
    detected_user_intent: str = "unknown"
    direct_answer_required: bool = False
    runtime_next_step: str | None = None
    greeting_prefix: str | None = None
    substantive_remainder: str | None = None
    continuity_badge_policy: dict[str, Any] | None = None
    runtime_followup_required: bool = False
    runtime_answer_quality: str = "topic_aligned"
    fallback_classification: str = "not_fallback"
    startup_procedure_required: bool = False
    response_generation_mode: str = "unknown"
    template_origin: dict[str, Any] | None = None
    source_origin_detail: str | None = None
    can_generate_model_guided_speech: bool = False
    requires_host_model: bool = False
    validation: dict[str, Any] | None = None
    retry_count: int = 0
    retry_limit: int = 1
    chatgpt_interpretation_distance: str = "unknown"
    runtime_text_hash: str | None = None
    visible_answer_hash: str | None = None
    provenance_contract: dict[str, Any] | None = None
    preservation_contract: dict[str, Any] | None = None
    voice_source_contract: dict[str, Any] | None = None
    runtime_rendering_mode: dict[str, Any] | None = None
    memory_recall_contract_status: dict[str, Any] | None = None
    final_visible_integrity: dict[str, Any] | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def accepted_model_candidate_text(response: Any, validation: Any) -> str:
        """Return adapter text only after explicit runtime acceptance."""
        response_payload = response if isinstance(response, dict) else (
            response.to_dict() if hasattr(response, "to_dict") else {}
        )
        validation_payload = validation if isinstance(validation, dict) else (
            validation.to_dict() if hasattr(validation, "to_dict") else {}
        )
        text = str(response_payload.get("text") or "").strip()
        if response_payload.get("status") != "completed" or not text:
            return ""
        if validation_payload.get("accepted") is not True:
            return ""
        return text

    @classmethod
    def build(
        cls,
        *,
        turn_id: str,
        trace_id: str,
        runtime_version: str,
        timestamp_header: str,
        timezone: str,
        state_emoticon: str,
        body: str,
        conversation_decision: dict[str, Any] | None = None,
        continuity_badge_policy: dict[str, Any] | None = None,
    ) -> "FinalResponseContract":
        body = re.sub(r"\s+", " ", (body or "").strip())
        decision = dict(conversation_decision or {})
        if not timestamp_header:
            raise ValueError("timestamp_header is required for final visible response")
        marker = state_emoticon or "🌿"
        final_visible_text = cls.ensure_timestamp_prefix(timestamp_header, marker, body)
        timestamp_contract = dict(decision.get("timestamp_contract") or {})
        declared_fallback = str(decision.get("fallback_classification") or "").strip()
        fallback_classification = declared_fallback or cls.classify_fallback(
            decision.get("route"), body, runtime_version=runtime_version
        )
        validation = dict(decision.get("final_answer_validation") or {})
        validation_passed = bool(validation.get("accepted", not validation.get("must_regenerate", False)))
        runtime_owned_origin = fallback_classification in RUNTIME_OWNED_NON_FALLBACK_CLASSIFICATIONS
        origin_truth_valid = bool(decision.get("origin_truth_valid", runtime_owned_origin))
        final_visible_integrity = cls.validate_visible_text(
            timestamp_header,
            final_visible_text,
            timestamp_contract=timestamp_contract,
            validation_passed=validation_passed,
            origin_truth_valid=origin_truth_valid,
        )
        if fallback_classification == "rule_handler_response":
            runtime_answer_quality = str(decision.get("runtime_answer_quality") or "rule_handler_response")
        elif fallback_classification != "not_fallback":
            runtime_answer_quality = "stale_route_mismatch" if fallback_classification == "stale_route_mismatch" else "fallback_or_debug"
        else:
            runtime_answer_quality = str(decision.get("runtime_answer_quality") or "topic_aligned")
        preservation_contract = {
            "must_preserve_runtime_body": True,
            "must_preserve_runtime_next_step": bool(decision.get("next_step")),
            "must_not_drop_runtime_followup": bool(decision.get("runtime_followup_required")),
            "must_answer_substantive_remainder": bool(decision.get("direct_answer_required")),
            "must_report_fallback_classification": fallback_classification != "not_fallback",
            "must_report_startup_status_when_required": bool(decision.get("startup_procedure_required")),
            "truth_boundary": "Warstwa ChatGPT może dopowiedzieć, ale nie może po cichu zgubić trasy, next_step, właściwej intencji runtime, klasyfikacji fallbacku ani pochodzenia template/runtime.",
        }
        return cls(
            turn_id=turn_id,
            trace_id=trace_id,
            runtime_version=runtime_version,
            timestamp_header=timestamp_header,
            timezone=timezone,
            state_emoticon=marker,
            body=body,
            runtime_exact_text=body,
            final_visible_text=final_visible_text,
            host_interpretation=decision.get("host_interpretation"),
            timestamp_source=str(timestamp_contract.get("source") or "cognitive_turn_envelope.trace.timestamp_header"),
            timestamp_trusted=(bool(timestamp_contract.get("trusted")) if "trusted" in timestamp_contract else None),
            timestamp_sample_iso=timestamp_contract.get("sample_iso"),
            runtime_route=str(decision.get("route") or "unknown"),
            detected_user_intent=str(decision.get("detected_user_intent") or "unknown"),
            direct_answer_required=bool(decision.get("direct_answer_required")),
            runtime_next_step=decision.get("next_step"),
            greeting_prefix=decision.get("greeting_prefix"),
            substantive_remainder=decision.get("substantive_remainder"),
            continuity_badge_policy=continuity_badge_policy or None,
            runtime_followup_required=bool(decision.get("runtime_followup_required")),
            runtime_answer_quality=runtime_answer_quality,
            fallback_classification=fallback_classification,
            startup_procedure_required=bool(decision.get("startup_procedure_required")),
            response_generation_mode=str(decision.get("response_generation_mode") or "unknown"),
            template_origin=decision.get("template_origin") or None,
            source_origin_detail=decision.get("source_origin_detail"),
            can_generate_model_guided_speech=bool(decision.get("can_generate_model_guided_speech")),
            requires_host_model=bool(decision.get("requires_host_model")),
            validation=validation,
            retry_count=int(decision.get("model_guided_retry_count") or 0),
            retry_limit=int(decision.get("model_guided_retry_limit") or 1),
            chatgpt_interpretation_distance=str(decision.get("interpretation_distance") or "unknown"),
            runtime_text_hash=decision.get("runtime_text_hash"),
            visible_answer_hash=decision.get("visible_answer_hash"),
            provenance_contract=decision.get("runtime_provenance") or None,
            preservation_contract=preservation_contract,
            voice_source_contract=decision.get("voice_source_contract") or None,
            runtime_rendering_mode=decision.get("runtime_rendering_mode") or None,
            memory_recall_contract_status=decision.get("memory_recall_contract_status") or None,
            final_visible_integrity=final_visible_integrity,
        )

    @staticmethod
    def classify_fallback(route: Any, body: str, *, runtime_version: str | None = None) -> str:
        text = (body or "").lower()
        route_text = str(route or "").lower()
        runtime_text = str(runtime_version or "").lower()
        technical_signatures = (
            "nie znalazłam osobnej trasy odpowiedzi",
            "runtime odebrał wiadomość",
            "debugowy fallback",
            "pusty fallback",
        )
        if any(sig in text for sig in technical_signatures):
            return "technical_fallback"
        if route_text == "v14_6_1_nlp_adapter_update" and not runtime_text.startswith("v14.6.1") and any(
            sig in text for sig in (
                "właściwy bezpieczny krok dla v14.6.1",
                "utrzymać v14.6.1",
                "pełny eksport v14.6.1",
            )
        ):
            return "stale_route_mismatch"
        if runtime_text.startswith("v14.6.10") and route_text == "v14_6_1_nlp_adapter_update" and "v14.6.10" in text:
            return "stale_route_mismatch"
        if route_text in {"general_dialogue", "open_question"} and "odpowiedź runtime ma teraz wyraźny obowiązek" in text:
            return "obligation_instead_of_answer"
        if "kontrakt" in text and "zamiast odpowiedzi" in text:
            return "contract_instead_of_answer"
        if route_text in {"free_open_question_no_specific_source", "free_open_question_synthesized"} and "nie mam dla niego specjalistycznej" in text:
            return "generic_no_source_instead_of_dialogue"
        if route_text == "correction_acknowledged" and any(x in text for x in ("co jeszcze jest źle", "co jest źle", "systemie jaźni")):
            return "diagnostic_question_misread_as_correction"
        if any(x in text for x in ("odebrałam sens wiadomości", "najuczciwszy model jest hybrydowy")) and route_text in {"general_dialogue", "open_question", "free_open_question_synthesized"}:
            return "generic_status_instead_of_answer"
        return "not_fallback"

    @staticmethod
    def ensure_timestamp_prefix(timestamp_header: str, state_emoticon: str, body_or_text: str) -> str:
        text = (body_or_text or "").strip()
        if text.startswith(timestamp_header):
            return text
        marker = state_emoticon or "🌿"
        return f"{timestamp_header} {marker}\n{text}"

    @staticmethod
    def validate_visible_text(
        timestamp_header: str,
        text: str,
        *,
        timestamp_contract: dict[str, Any] | None = None,
        validation_passed: bool = True,
        origin_truth_valid: bool = True,
    ) -> dict[str, Any]:
        """Waliduje widoczny timestamp: obecność, źródło, zaufanie i świeżość."""
        visible = (text or "").strip()
        contract = dict(timestamp_contract or {})
        has_timestamp = bool(timestamp_header) and visible.startswith(timestamp_header)
        trusted = contract.get("trusted")
        source = contract.get("source")
        sample_iso = contract.get("sample_iso")
        max_age_seconds = int(contract.get("max_age_seconds") or TIMESTAMP_MAX_AGE_SECONDS)
        freshness_seconds: int | None = None
        freshness_ok = True
        if sample_iso:
            try:
                sample_dt = datetime.fromisoformat(str(sample_iso).replace("Z", "+00:00"))
                if sample_dt.tzinfo is None:
                    sample_dt = sample_dt.replace(tzinfo=timezone.utc)
                freshness_seconds = abs(int((datetime.now(timezone.utc) - sample_dt.astimezone(timezone.utc)).total_seconds()))
                freshness_ok = freshness_seconds <= max_age_seconds
            except Exception:
                freshness_ok = False
        trust_required = bool(contract.get("require_trusted_in_final_visible", TIMESTAMP_REQUIRE_TRUSTED_IN_FINAL_VISIBLE))
        degraded_allowed = bool(contract.get("allow_degraded_local_visible", TIMESTAMP_ALLOW_DEGRADED_LOCAL_VISIBLE))
        trust_ok = True if trusted is None and not contract else bool(trusted) or not trust_required
        degraded_visible_ok = bool(degraded_allowed and has_timestamp and freshness_ok)
        timestamp_valid = bool(has_timestamp and freshness_ok and trust_ok)
        valid = bool(timestamp_valid and validation_passed and origin_truth_valid)
        return {
            "schema_version": schema_version("final_response_contract_validation"),
            "timestamp_policy": timestamp_runtime_policy(),
            "timestamp_header": timestamp_header,
            "timestamp_present": has_timestamp,
            "timestamp_source": source,
            "timestamp_trusted": trusted,
            "timestamp_sample_iso": sample_iso,
            "timestamp_freshness_seconds": freshness_seconds,
            "timestamp_max_age_seconds": max_age_seconds,
            "timestamp_freshness_ok": freshness_ok,
            "timestamp_trust_ok": trust_ok,
            "timestamp_degraded_allowed": degraded_allowed,
            "timestamp_degraded_visible_ok": degraded_visible_ok,
            "timestamp_valid": timestamp_valid,
            "validation_passed": bool(validation_passed),
            "origin_truth_valid": bool(origin_truth_valid),
            "valid": valid,
            "text_sha256": hashlib.sha256(visible.encode("utf-8")).hexdigest(),
        }
