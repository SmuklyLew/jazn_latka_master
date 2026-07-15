from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version

STRICT_RUNTIME_TRUTH_SCHEMA = schema_version("strict_runtime_truth_gate", version=PACKAGE_VERSION_FULL)
TIMESTAMP_DEGRADED_ERRORS = {
    "timestamp_untrusted",
    "timestamp_source_not_network",
}
TIMESTAMP_BLOCKING_ERRORS = {
    "timestamp_missing",
    "timestamp_stale_or_missing_freshness",
    "final_visible_integrity_invalid",
    "final_response_contract_missing",
    "daemon_persistence_unconfirmed",
    "one_shot_degraded_disclosure_missing",
}
NETWORK_SOURCE_PREFIXES = (
    "https://",
    "http://",
    "network_",
    "ntp_",
    "test_network",
)
TRUSTED_EXTERNAL_TIME_SOURCE_PREFIXES = (
    "chatgpt_web_time_tool",
    "chatgpt_loader_time",
    "chatgpt_host_time",
    "openai_web_time_tool",
    "openai_host_time",
    "external_trusted_time",
    "injected_trusted_time",
    "host_injected_time",
    "host_injected",
    "chatgpt_host_time_iso_alias",
    "openai_host_time_iso_alias",
    "jazn_host_time_iso_alias",
)
LOCAL_OR_UNTRUSTED_SOURCE_MARKERS = (
    "local_fallback",
    "network_time_unavailable",
    "fallback",
    "manual",
    "unknown",
)
RUNTIME_OWNED_NON_FALLBACK_CLASSIFICATIONS = {
    "rule_handler_response",
}

RUNTIME_NOT_STARTED_RULE_ID = "RULE_RUNTIME_NOT_STARTED"
RUNTIME_NOT_STARTED_ERROR = "runtime_not_started"
RUNTIME_NOT_STARTED_VISIBLE_TEXT = "Jaźń nie została uruchomiona."

FINAL_VISIBLE_TEXT_REQUIRED_RULE_ID = "RULE_FINAL_VISIBLE_TEXT_REQUIRED"
FINAL_VISIBLE_TEXT_REQUIRED_ERROR = "final_visible_text_required"



@dataclass(slots=True)
class RuntimeTruthGateResult:
    schema_version: str = STRICT_RUNTIME_TRUTH_SCHEMA
    ok: bool = True
    normal_response_allowed: bool = True
    active_state: str = "active_trusted"
    error_code: str | None = None
    errors: list[str] = field(default_factory=list)
    timestamp_source: str | None = None
    timestamp_trusted: bool | None = None
    timestamp_present: bool | None = None
    timestamp_freshness_seconds: int | None = None
    timestamp_max_age_seconds: int | None = None
    time_trust_state: str | None = None
    runtime_active_state: str | None = None
    final_visible_integrity_valid: bool | None = None
    final_visible_origin_valid: bool | None = None
    truthful_degraded_disclosure: bool = False
    process_lifecycle: str | None = None
    daemon_confirmed: bool | None = None
    background_claim_allowed: bool = False
    untrusted_timestamp_header: str | None = None
    rule_id: str | None = None
    diagnostic_status: dict[str, Any] | None = None
    truth_boundary: str = (
        "Brama prawdy runtime rozdziela aktywność Jaźni od jakości źródła czasu. "
        "Brak czasu sieciowego nie blokuje startu runtime ani zwykłej odpowiedzi, jeżeli timestamp jest obecny i świeży. "
        "Lokalny czas maszyny pozostaje jawnie oznaczony jako niezweryfikowany, ale nie robi z żywego runtime active_degraded."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source_is_network(source: Any) -> bool:
    value = str(source or "").strip().lower()
    if not value:
        return False
    if any(marker in value for marker in LOCAL_OR_UNTRUSTED_SOURCE_MARKERS):
        return False
    if value.startswith(TRUSTED_EXTERNAL_TIME_SOURCE_PREFIXES):
        return True
    return value.startswith(NETWORK_SOURCE_PREFIXES) or "#http-date" in value


def evaluate_final_response_contract(contract: dict[str, Any] | None) -> RuntimeTruthGateResult:
    contract = dict(contract or {})
    integrity = contract.get("final_visible_integrity") if isinstance(contract.get("final_visible_integrity"), dict) else {}
    errors: list[str] = []
    if not contract:
        errors.extend([RUNTIME_NOT_STARTED_ERROR, "final_response_contract_missing"])
        return RuntimeTruthGateResult(
            ok=False,
            normal_response_allowed=False,
            active_state="inactive",
            error_code=RUNTIME_NOT_STARTED_ERROR,
            errors=errors,
            runtime_active_state="inactive",
            final_visible_integrity_valid=False,
            final_visible_origin_valid=False,
            rule_id=RUNTIME_NOT_STARTED_RULE_ID,
            diagnostic_status={
                "rule_id": RUNTIME_NOT_STARTED_RULE_ID,
                "status": "failed_closed",
                "reason": "missing_final_response_contract",
                "visible_text": RUNTIME_NOT_STARTED_VISIBLE_TEXT,
            },
        )

    valid = bool(integrity.get("valid"))
    present = bool(integrity.get("timestamp_present"))
    source = integrity.get("timestamp_source") or contract.get("timestamp_source")
    trusted = integrity.get("timestamp_trusted")
    if trusted is None:
        trusted = contract.get("timestamp_trusted")
    freshness_ok = bool(integrity.get("timestamp_freshness_ok", True))
    freshness_seconds = integrity.get("timestamp_freshness_seconds")
    max_age_seconds = integrity.get("timestamp_max_age_seconds")
    validation_passed = bool(integrity.get("validation_passed", True))
    fallback_classification = str(contract.get("fallback_classification") or "not_fallback")
    runtime_owned_origin = fallback_classification in RUNTIME_OWNED_NON_FALLBACK_CLASSIFICATIONS
    origin_truth_valid = bool(integrity.get("origin_truth_valid", True) or runtime_owned_origin)
    if not valid and (
        present
        and trusted is True
        and freshness_ok
        and origin_truth_valid
        and validation_passed
    ):
        valid = True
    requires_host_model = bool(contract.get("requires_host_model"))
    truthful_degraded_disclosure = bool(
        not origin_truth_valid and fallback_classification != "not_fallback" and not runtime_owned_origin
    )
    process_lifecycle = str(contract.get("process_lifecycle") or contract.get("runtime_mode") or "").strip() or None
    daemon_confirmed_raw = contract.get("daemon_confirmed")
    daemon_confirmed = bool(daemon_confirmed_raw) if daemon_confirmed_raw is not None else None
    degraded_disclosure = contract.get("degraded_disclosure") is True
    background_claim_allowed = False if process_lifecycle == "one_shot" else bool(
        contract.get("background_claim_allowed")
        or process_lifecycle == "persistent_chat_loop"
        or (process_lifecycle == "daemon_persistent" and daemon_confirmed is True)
    )
    disclosure_error = (
        "model_guided_speech_required"
        if requires_host_model and fallback_classification == "cannot_answer_directly"
        else "classified_non_dynamic_response"
    )

    if not present:
        errors.append("timestamp_missing")
    if trusted is not True:
        errors.append("timestamp_untrusted")
    if not _source_is_network(source):
        errors.append("timestamp_source_not_network")
    if not freshness_ok:
        errors.append("timestamp_stale_or_missing_freshness")
    if not valid and not truthful_degraded_disclosure and (
        not present or not freshness_ok or not origin_truth_valid or not validation_passed
    ):
        errors.append("final_visible_integrity_invalid")
    if truthful_degraded_disclosure:
        errors.append(disclosure_error)
    if process_lifecycle == "daemon_persistent" and daemon_confirmed is not True:
        errors.append("daemon_persistence_unconfirmed")
    if process_lifecycle == "one_shot" and daemon_confirmed is False and not degraded_disclosure:
        errors.append("one_shot_degraded_disclosure_missing")

    blocking_errors = [error for error in errors if error in TIMESTAMP_BLOCKING_ERRORS]
    degraded_errors = [error for error in errors if error in TIMESTAMP_DEGRADED_ERRORS]
    ok = not blocking_errors
    degraded = bool(degraded_errors) and ok
    if trusted is True and _source_is_network(source):
        source_text = str(source or "").strip().lower()
        time_trust = "trusted_host_time_network_unavailable" if source_text.startswith(TRUSTED_EXTERNAL_TIME_SOURCE_PREFIXES) else "trusted_time"
    else:
        time_trust = "local_machine_unverified" if present and freshness_ok else "time_unavailable"
    active_state = "active_blocked" if not ok else "active_trusted"
    if truthful_degraded_disclosure:
        active_state = "active_degraded"
    return RuntimeTruthGateResult(
        ok=ok,
        normal_response_allowed=bool(ok and not truthful_degraded_disclosure),
        active_state=active_state,
        error_code=(disclosure_error if truthful_degraded_disclosure else ("timestamp_degraded" if degraded else (None if ok else "runtime_truth_gate_blocked"))),
        errors=errors,
        timestamp_source=str(source) if source is not None else None,
        timestamp_trusted=bool(trusted) if trusted is not None else None,
        timestamp_present=present,
        timestamp_freshness_seconds=int(freshness_seconds) if isinstance(freshness_seconds, int) else None,
        timestamp_max_age_seconds=int(max_age_seconds) if isinstance(max_age_seconds, int) else None,
        time_trust_state=time_trust,
        runtime_active_state=active_state,
        final_visible_integrity_valid=valid,
        final_visible_origin_valid=origin_truth_valid,
        truthful_degraded_disclosure=truthful_degraded_disclosure,
        process_lifecycle=process_lifecycle,
        daemon_confirmed=daemon_confirmed,
        background_claim_allowed=background_claim_allowed,
        untrusted_timestamp_header=contract.get("timestamp_header"),
    )


def build_blocked_visible_text(gate: RuntimeTruthGateResult) -> str:
    if gate.error_code == RUNTIME_NOT_STARTED_ERROR or RUNTIME_NOT_STARTED_ERROR in gate.errors:
        return RUNTIME_NOT_STARTED_VISIBLE_TEXT
    source = gate.timestamp_source or "unknown"
    header = gate.untrusted_timestamp_header or "brak timestampu runtime"
    errors = ", ".join(gate.errors) if gate.errors else "runtime_truth_gate_blocked"
    return (
        "[czas lokalny niezweryfikowany — Europe/Warsaw] ⚠️\n"
        "Nie zwracam zwykłej odpowiedzi Jaźni, bo brama prawdy runtime zablokowała turę: "
        f"{gate.error_code or 'runtime_truth_gate_blocked'}.\n"
        f"Niezaufany nagłówek tury: {header}\n"
        f"Źródło czasu: {source}; błędy: {errors}.\n"
        "Uruchom diagnostykę czasu sieciowego (`python main.py --network-time-check`) albo powtórz turę, gdy czas sieciowy będzie dostępny."
    )


def apply_runtime_truth_gate(result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = dict(result or {})
    gate = evaluate_final_response_contract(
        updated.get("final_response_contract") if isinstance(updated.get("final_response_contract"), dict) else None
    )
    if gate.ok and not str(updated.get("final_visible_text") or "").strip():
        gate.ok = False
        gate.normal_response_allowed = False
        gate.active_state = "active_blocked"
        gate.runtime_active_state = "active_blocked"
        gate.error_code = FINAL_VISIBLE_TEXT_REQUIRED_ERROR
        if FINAL_VISIBLE_TEXT_REQUIRED_ERROR not in gate.errors:
            gate.errors.append(FINAL_VISIBLE_TEXT_REQUIRED_ERROR)
        gate.rule_id = FINAL_VISIBLE_TEXT_REQUIRED_RULE_ID
        gate.diagnostic_status = {
            "rule_id": FINAL_VISIBLE_TEXT_REQUIRED_RULE_ID,
            "status": "failed_closed",
            "reason": "missing_final_visible_text",
        }
    gate_payload = gate.to_dict()
    updated["runtime_truth_gate"] = gate_payload
    if not gate.ok:
        original_final = updated.get("final_visible_text")
        updated["ok"] = False
        updated["error_code"] = gate.error_code or "runtime_truth_gate_blocked"
        updated["normal_response_blocked"] = True
        updated["blocked_final_visible_text"] = original_final
        updated["final_visible_text"] = build_blocked_visible_text(gate)
        updated["runtime_response_status"] = "blocked_by_runtime_truth_gate"
        decision = updated.get("conversation_decision") if isinstance(updated.get("conversation_decision"), dict) else {}
        decision = dict(decision)
        decision["runtime_truth_gate"] = gate_payload
        decision["normal_response_allowed"] = False
        decision["error_code"] = updated["error_code"]
        updated["conversation_decision"] = decision
    else:
        updated.setdefault("ok", True)
        updated["normal_response_blocked"] = False
        if gate.truthful_degraded_disclosure:
            updated["normal_response_blocked"] = True
            updated["runtime_response_status"] = "truthful_degraded_cannot_answer_directly"
            updated["requires_host_model"] = True
            decision = updated.get("conversation_decision") if isinstance(updated.get("conversation_decision"), dict) else {}
            decision = dict(decision)
            decision["runtime_truth_gate"] = gate_payload
            decision["normal_response_allowed"] = False
            decision["requires_host_model"] = True
            updated["conversation_decision"] = decision
        elif gate.error_code == "timestamp_degraded" or gate.time_trust_state == "local_machine_unverified":
            updated["timestamp_degraded"] = True
            updated["runtime_response_status"] = "normal_response_allowed_degraded_timestamp"
            decision = updated.get("conversation_decision") if isinstance(updated.get("conversation_decision"), dict) else {}
            decision = dict(decision)
            decision["runtime_truth_gate"] = gate_payload
            decision["normal_response_allowed"] = True
            decision["timestamp_degraded"] = True
            updated["conversation_decision"] = decision
        else:
            updated["runtime_response_status"] = "normal_response_allowed"
    return updated, gate_payload


def time_trust_state(*, timestamp_trusted: bool | None, timestamp_source: str | None = None, time_error: str | None = None) -> str:
    source = str(timestamp_source or "").strip().lower()
    if timestamp_trusted is True:
        if source.startswith(TRUSTED_EXTERNAL_TIME_SOURCE_PREFIXES) or source == "host_injected":
            return "trusted_host_time_network_unavailable"
        return "trusted_time"
    if time_error or "network_time_unavailable" in source:
        return "network_time_unavailable_local_machine_unverified"
    if source:
        return "local_machine_unverified"
    return "unknown_time_source"


def daemon_active_state(*, marker_found: bool, pid_alive: bool, ping_ok: bool, timestamp_trusted: bool | None) -> str:
    # Runtime liveness is marker + process + endpoint. Timestamp trust is reported
    # separately by time_trust_state; missing network time must not block startup.
    if not (marker_found and pid_alive and ping_ok):
        return "inactive"
    return "active_trusted"
