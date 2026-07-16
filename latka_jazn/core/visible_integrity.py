from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
import hashlib
import re

from latka_jazn.core.timestamp_policy import (
    TIMESTAMP_ALLOW_DEGRADED_LOCAL_VISIBLE,
    TIMESTAMP_MAX_AGE_SECONDS,
    TIMESTAMP_REQUIRE_TRUSTED_IN_FINAL_VISIBLE,
    timestamp_runtime_policy,
)
from latka_jazn.version import schema_version

RUNTIME_OWNED_NON_FALLBACK_CLASSIFICATIONS = frozenset({
    "rule_handler_response",
})
TIMESTAMP_HEADER_RE = re.compile(
    r"^\[🕒 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} GMT[+-]\d{1,2}, [^,\]]+, Europe/Warsaw\]$"
)
RENDER_ARTIFACTS = (
    "aaaktywny", "aaktywny", "prrzez", "nieddziela", "niedzielaa",
    "pierwszoossobową", "pierwszoosobowąą", "GMMT", "2026-066", "221:",
    "13:43:228", "rozmawiać ć", "Uwa ażam", "operacyjnnego", "ddebug", "techniiczna",
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_body(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _visible_body(timestamp_header: str, text: str) -> str:
    value = str(text or "").strip()
    if timestamp_header and value.startswith(timestamp_header):
        value = value[len(timestamp_header):].lstrip()
        if value and "\n" in value:
            first, rest = value.split("\n", 1)
            if len(first.strip()) <= 8:
                value = rest
    return _canonical_body(value)


def evaluate_origin_truth(
    decision: Mapping[str, Any] | None,
    *,
    body: str,
    final_visible_text: str,
    timestamp_header: str = "",
) -> tuple[bool, list[str]]:
    decision = dict(decision or {})
    classification = str(decision.get("fallback_classification") or "not_fallback")
    validation = decision.get("final_answer_validation") if isinstance(decision.get("final_answer_validation"), Mapping) else {}
    accepted = validation.get("accepted") is True and validation.get("must_regenerate") is not True
    template = decision.get("template_origin") if isinstance(decision.get("template_origin"), Mapping) else {}
    provenance = decision.get("runtime_provenance") if isinstance(decision.get("runtime_provenance"), Mapping) else {}
    canonical_body = _canonical_body(body)
    visible_body = _visible_body(timestamp_header, final_visible_text)
    reasons: list[str] = []

    if not accepted:
        reasons.append("validator_not_accepted")
    if not canonical_body or visible_body != canonical_body:
        reasons.append("visible_body_mismatch")
    if provenance:
        if _canonical_body(str(provenance.get("exact_runtime_text") or "")) != canonical_body:
            reasons.append("provenance_runtime_text_mismatch")
    else:
        reasons.append("runtime_provenance_missing")

    if classification == "rule_handler_response":
        handler = decision.get("handler_result") if isinstance(decision.get("handler_result"), Mapping) else {}
        handler_body = _canonical_body(str(handler.get("body") or ""))
        required = set(handler.get("required_components") or [])
        satisfied = set(handler.get("satisfied_components") or decision.get("handler_satisfied_components") or [])
        missing = set(handler.get("missing_components") or decision.get("handler_missing_components") or [])
        handler_name = str(handler.get("handler_name") or decision.get("handler_name") or "")
        provenance_handler = str(provenance.get("handler_name") or "")
        source_origin = str(provenance.get("source_origin_detail") or decision.get("source_origin_detail") or "")
        if not handler_body or handler_body != canonical_body:
            reasons.append("handler_body_mismatch")
        if missing or not required.issubset(satisfied):
            reasons.append("handler_required_components_missing")
        if template.get("template_id") or handler.get("template_origin"):
            reasons.append("template_fallback_not_runtime_owned")
        if not handler_name or provenance_handler != handler_name or not source_origin:
            reasons.append("rule_handler_provenance_missing")
        return not reasons, reasons

    if classification != "not_fallback":
        reasons.append("classified_fallback")
        return False, reasons

    if decision.get("model_generated") is True:
        if str(provenance.get("response_generation_mode") or "") != "runtime_model_guided":
            reasons.append("model_candidate_not_runtime_accepted")
        return not reasons, reasons

    finalization = decision.get("host_visible_finalization") if isinstance(decision.get("host_visible_finalization"), Mapping) else {}
    if finalization.get("accepted") is True:
        if str(finalization.get("final_visible_text") or "") != str(final_visible_text or ""):
            reasons.append("host_finalization_text_mismatch")
        if str(finalization.get("final_text_sha256") or "") != _sha(str(final_visible_text or "").strip()):
            reasons.append("host_finalization_hash_mismatch")
        return not reasons, reasons

    reasons.append("not_fallback_without_provenance")
    return False, reasons


def validate_visible_text(
    timestamp_header: str,
    text: str,
    *,
    timestamp_contract: Mapping[str, Any] | None = None,
    validation_passed: bool,
    origin_truth_valid: bool,
    expected_visible_hash: str | None = None,
) -> dict[str, Any]:
    visible = str(text or "").strip()
    contract = dict(timestamp_contract or {})
    has_timestamp = bool(timestamp_header) and visible.startswith(timestamp_header)
    header_shape_valid = bool(timestamp_header and TIMESTAMP_HEADER_RE.match(timestamp_header))
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
    text_hash = _sha(visible)
    hash_valid = expected_visible_hash in (None, "", text_hash)
    errors: list[str] = []
    if not has_timestamp:
        errors.append("timestamp_missing")
    if not header_shape_valid:
        errors.append("timestamp_header_invalid")
    if not freshness_ok:
        errors.append("timestamp_stale")
    if not validation_passed:
        errors.append("answer_validation_failed")
    if not origin_truth_valid:
        errors.append("origin_truth_invalid")
    if not hash_valid:
        errors.append("visible_text_hash_mismatch")
    timestamp_valid = bool(has_timestamp and header_shape_valid and freshness_ok and trust_ok)
    return {
        "schema_version": schema_version("final_visible_integrity"),
        "timestamp_policy": timestamp_runtime_policy(),
        "timestamp_header": timestamp_header,
        "timestamp_present": has_timestamp,
        "timestamp_header_shape_valid": header_shape_valid,
        "timestamp_source": source,
        "timestamp_trusted": trusted,
        "timestamp_sample_iso": sample_iso,
        "timestamp_freshness_seconds": freshness_seconds,
        "timestamp_max_age_seconds": max_age_seconds,
        "timestamp_freshness_ok": freshness_ok,
        "timestamp_trust_ok": trust_ok,
        "timestamp_degraded_allowed": degraded_allowed,
        "timestamp_degraded_visible_ok": bool(degraded_allowed and has_timestamp and freshness_ok),
        "timestamp_valid": timestamp_valid,
        "validation_passed": bool(validation_passed),
        "origin_truth_valid": bool(origin_truth_valid),
        "hash_valid": hash_valid,
        "valid": bool(timestamp_valid and validation_passed and origin_truth_valid and hash_valid),
        "errors": errors,
        "text_sha256": text_hash,
    }


def validate_result_integrity(result: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(result or {})
    contract = result.get("final_response_contract") if isinstance(result.get("final_response_contract"), Mapping) else {}
    decision = result.get("conversation_decision") if isinstance(result.get("conversation_decision"), Mapping) else {}
    trace = result.get("trace") if isinstance(result.get("trace"), Mapping) else {}
    final_text = str(result.get("final_visible_text") or contract.get("final_visible_text") or "")
    timestamp_header = str(trace.get("timestamp_header") or contract.get("timestamp_header") or "")
    body = str(contract.get("body") or result.get("exact_runtime_text") or "")
    origin_valid, origin_errors = evaluate_origin_truth(
        decision, body=body, final_visible_text=final_text, timestamp_header=timestamp_header
    )
    validation = decision.get("final_answer_validation") if isinstance(decision.get("final_answer_validation"), Mapping) else contract.get("validation") or {}
    validation_passed = bool(validation.get("accepted") is True and validation.get("must_regenerate") is not True)
    provenance = result.get("runtime_provenance") if isinstance(result.get("runtime_provenance"), Mapping) else decision.get("runtime_provenance") or {}
    expected_hash = str(provenance.get("visible_answer_hash") or contract.get("visible_answer_hash") or "") or None
    integrity = validate_visible_text(
        timestamp_header, final_text,
        timestamp_contract=decision.get("timestamp_contract") or {
            "trusted": contract.get("timestamp_trusted"), "source": contract.get("timestamp_source"),
            "sample_iso": contract.get("timestamp_sample_iso"),
        },
        validation_passed=validation_passed, origin_truth_valid=origin_valid,
        expected_visible_hash=expected_hash,
    )
    errors = list(integrity.get("errors") or []) + origin_errors
    visible_provenance_text = str(provenance.get("visible_answer_text") or "")
    if visible_provenance_text and visible_provenance_text != final_text:
        errors.append("visible_answer_text_mismatch")
    exact_runtime_text = str(provenance.get("exact_runtime_text") or result.get("exact_runtime_text") or "")
    expected_runtime_hash = str(provenance.get("runtime_text_hash") or "")
    if expected_runtime_hash and expected_runtime_hash != _sha(exact_runtime_text):
        errors.append("runtime_text_hash_mismatch")
    for artifact in RENDER_ARTIFACTS:
        if artifact in final_text or artifact in exact_runtime_text:
            errors.append(f"render_artifact_detected:{artifact}")
    if "\ufffd" in final_text or "\ufffd" in exact_runtime_text:
        errors.append("unicode_replacement_character_detected")
    integrity["errors"] = sorted(set(errors))
    integrity["checked_artifact_count"] = len(RENDER_ARTIFACTS)
    integrity["valid"] = bool(integrity.get("valid") and not errors)
    return integrity


def enforce_integrity_consensus(result: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = dict(result or {})
    contract = dict(updated.get("final_response_contract") or {})
    contract_integrity = dict(contract.get("final_visible_integrity") or {})
    result_integrity = dict(updated.get("final_visible_integrity") or {})
    gate = dict(updated.get("runtime_truth_gate") or {})
    session = dict(updated.get("session_provenance") or {})
    values = {
        "result": result_integrity.get("valid"),
        "contract": contract_integrity.get("valid"),
        "runtime_truth_gate": gate.get("final_visible_integrity_valid"),
        "session_provenance": session.get("final_visible_integrity_valid"),
    }
    declared = [value for value in values.values() if isinstance(value, bool)]
    mismatch = bool(declared and any(value != declared[0] for value in declared[1:]))
    canonical = bool(contract_integrity.get("valid") and result_integrity.get("valid"))
    final_valid = bool(canonical and not mismatch)
    result_integrity["valid"] = final_valid
    result_integrity["consensus"] = not mismatch
    result_integrity["consensus_values"] = values
    contract_integrity["valid"] = final_valid
    contract_integrity["consensus"] = not mismatch
    gate["final_visible_integrity_valid"] = final_valid
    session["final_visible_integrity_valid"] = final_valid
    contract["final_visible_integrity"] = contract_integrity
    updated["final_visible_integrity"] = result_integrity
    updated["final_response_contract"] = contract
    updated["runtime_truth_gate"] = gate
    updated["session_provenance"] = session
    if mismatch:
        updated["ok"] = False
        updated["normal_response_blocked"] = True
        updated["error_code"] = "integrity_consensus_mismatch"
        updated["runtime_response_status"] = "blocked_by_integrity_consensus"
        gate["ok"] = False
        gate["normal_response_allowed"] = False
        gate["error_code"] = "integrity_consensus_mismatch"
        gate.setdefault("errors", []).append("integrity_consensus_mismatch")
    return updated, {
        "schema_version": schema_version("final_visible_integrity_consensus"),
        "valid": final_valid,
        "mismatch": mismatch,
        "values": values,
        "error_code": "integrity_consensus_mismatch" if mismatch else None,
    }
