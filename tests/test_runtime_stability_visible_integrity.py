from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

from latka_jazn.core.final_response_contract import FinalResponseContract
from latka_jazn.core.runtime_truth_gate import apply_runtime_truth_gate, evaluate_final_response_contract
from latka_jazn.core.visible_integrity import (
    enforce_integrity_consensus,
    evaluate_origin_truth,
    validate_result_integrity,
)

HEADER = "[🕒 2026-07-16 12:00:00 GMT+2, czwartek, Europe/Warsaw]"
BODY = "Działam."
VISIBLE = f"{HEADER} 🌿\n{BODY}"


def _decision(*, classification: str = "rule_handler_response") -> dict:
    return {
        "fallback_classification": classification,
        "route": "presence",
        "handler_name": "presence_handler",
        "handler_result": {
            "handler_name": "presence_handler",
            "body": BODY,
            "required_components": ["presence"],
            "satisfied_components": ["presence"],
            "missing_components": [],
        },
        "final_answer_validation": {"accepted": True, "must_regenerate": False},
        "template_origin": {},
        "runtime_provenance": {
            "handler_name": "presence_handler",
            "source_origin_detail": "presence_handler",
            "response_generation_mode": "runtime_dynamic",
            "exact_runtime_text": BODY,
            "runtime_text_hash": hashlib.sha256(BODY.encode()).hexdigest(),
            "visible_answer_text": VISIBLE,
            "visible_answer_hash": hashlib.sha256(VISIBLE.encode()).hexdigest(),
        },
        "timestamp_contract": {
            "trusted": False,
            "source": "local_machine",
            "sample_iso": datetime.now(timezone.utc).isoformat(),
            "require_trusted_in_final_visible": False,
            "allow_degraded_local_visible": True,
        },
    }


def _result(decision: dict | None = None) -> dict:
    decision = decision or _decision()
    contract = FinalResponseContract.build(
        turn_id="t1", trace_id="x1", runtime_version="v15.0.3.2",
        timestamp_header=HEADER, timezone="Europe/Warsaw", state_emoticon="🌿",
        body=BODY, conversation_decision=decision,
    ).to_dict()
    return {
        "trace": {"timestamp_header": HEADER},
        "conversation_decision": decision,
        "final_response_contract": contract,
        "final_visible_text": VISIBLE,
        "runtime_provenance": decision.get("runtime_provenance"),
        "exact_runtime_text": BODY,
    }


def test_valid_rule_handler_has_runtime_owned_origin() -> None:
    result = _result()
    integrity = validate_result_integrity(result)
    assert integrity["valid"] is True
    assert integrity["origin_truth_valid"] is True
    assert result["final_response_contract"]["final_visible_integrity"]["valid"] is True


def test_host_generation_is_valid_only_after_finalization() -> None:
    decision = _decision(classification="not_fallback")
    decision["handler_result"] = {}
    decision["chatgpt_host_visible_bridge"] = {"accepted": True}
    decision["host_visible_finalization"] = {
        "accepted": True,
        "final_visible_text": VISIBLE,
        "final_text_sha256": hashlib.sha256(VISIBLE.encode()).hexdigest(),
    }
    valid, errors = evaluate_origin_truth(decision, body=BODY, final_visible_text=VISIBLE, timestamp_header=HEADER)
    assert valid is True, errors
    decision.pop("host_visible_finalization")
    valid, errors = evaluate_origin_truth(decision, body=BODY, final_visible_text=VISIBLE, timestamp_header=HEADER)
    assert valid is False
    assert "not_fallback_without_provenance" in errors


def test_text_changed_after_hash_is_rejected() -> None:
    result = _result()
    result["final_visible_text"] += " zmiana"
    integrity = validate_result_integrity(result)
    assert integrity["valid"] is False
    assert "visible_text_hash_mismatch" in integrity["errors"]


def test_stale_timestamp_is_rejected() -> None:
    decision = _decision()
    decision["timestamp_contract"]["sample_iso"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    result = _result(decision)
    integrity = validate_result_integrity(result)
    assert integrity["valid"] is False
    assert "timestamp_stale" in integrity["errors"]


def test_not_fallback_without_provenance_and_technical_fallback_are_rejected() -> None:
    decision = _decision(classification="not_fallback")
    decision["handler_result"] = {}
    decision["runtime_provenance"] = {}
    valid, errors = evaluate_origin_truth(decision, body=BODY, final_visible_text=VISIBLE, timestamp_header=HEADER)
    assert valid is False
    assert "runtime_provenance_missing" in errors

    decision = _decision(classification="technical_fallback")
    valid, errors = evaluate_origin_truth(decision, body=BODY, final_visible_text=VISIBLE, timestamp_header=HEADER)
    assert valid is False
    assert "classified_fallback" in errors


def test_missing_contract_and_missing_visible_text_fail_closed() -> None:
    gate = evaluate_final_response_contract(None)
    assert gate.ok is False
    assert gate.error_code == "runtime_not_started"

    result = _result()
    result["final_visible_text"] = ""
    updated, gate_payload = apply_runtime_truth_gate(result)
    assert gate_payload["ok"] is False
    assert updated["error_code"] == "final_visible_text_required"


def test_runtime_truth_gate_cannot_promote_invalid_contract() -> None:
    result = _result()
    contract = result["final_response_contract"]
    contract["final_visible_integrity"]["valid"] = False
    contract["final_visible_integrity"]["origin_truth_valid"] = False
    gate = evaluate_final_response_contract(contract)
    assert gate.ok is False
    assert gate.final_visible_integrity_valid is False
    assert gate.final_visible_origin_valid is False


def test_consensus_mismatch_blocks_normal_response() -> None:
    result = _result()
    result["final_visible_integrity"] = {"valid": True}
    result["final_response_contract"]["final_visible_integrity"]["valid"] = False
    result["runtime_truth_gate"] = {"final_visible_integrity_valid": True, "ok": True}
    result["session_provenance"] = {"final_visible_integrity_valid": True}
    updated, consensus = enforce_integrity_consensus(result)
    assert consensus["mismatch"] is True
    assert updated["error_code"] == "integrity_consensus_mismatch"
    assert updated["normal_response_blocked"] is True
    assert updated["final_visible_integrity"]["valid"] is False
    assert updated["final_response_contract"]["final_visible_integrity"]["valid"] is False
    assert updated["runtime_truth_gate"]["final_visible_integrity_valid"] is False
    assert updated["session_provenance"]["final_visible_integrity_valid"] is False
