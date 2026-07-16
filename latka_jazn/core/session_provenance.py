from __future__ import annotations

from typing import Any
import hashlib

from latka_jazn.version import schema_version
from latka_jazn.core.visible_integrity import validate_result_integrity

SCHEMA_VERSION = schema_version("session_provenance")

def build_session_provenance(
    *,
    session_id: str,
    client: str,
    lifecycle: str,
    process_reused: bool,
    engine_reused_between_turns: bool,
    load_metadata: dict[str, Any] | None = None,
    save_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    load_metadata = dict(load_metadata or {})
    save_status = dict(save_status or {})
    save_truth_boundary = save_status.pop("truth_boundary", None)
    truth_boundary = (
        "Sesja oznacza stan rozmowy w tym procesie i ewentualny zapis runtime_session_state. "
        "Nie oznacza, że po EOF, /exit albo zakończeniu batcha działa proces w tle."
    )
    if save_status and not save_status.get("session_state_saved", False):
        truth_boundary += " Zapis stanu sesji nie został potwierdzony; trwałość jest ograniczona do pamięci procesu."
    if save_truth_boundary:
        truth_boundary += f" {save_truth_boundary}"
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "client": client,
        "lifecycle": lifecycle,
        "process_reused": bool(process_reused),
        "engine_reused_between_turns": bool(engine_reused_between_turns),
        "session_reused": bool(load_metadata.get("session_reused", False)),
        "session_resurrected_from_disk": bool(load_metadata.get("session_resurrected_from_disk", False)),
        "session_loaded_from": str(load_metadata.get("session_loaded_from") or "new"),
        "background_process_claim_allowed": False,
        "truth_boundary": truth_boundary,
        **save_status,
    }


def repair_final_visible_integrity(result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Naprawia widoczny tekst tury przed walidacją bridge/session.

    Runtime ma już FinalResponseContract, ale warstwa mostu ChatGPT i testowe
    klienty mogą dostać rozjechany payload: timestamp w trace istnieje, a
    final_visible_text albo runtime_provenance.visible_answer_text go zgubiły.
    Ta funkcja nie zmienia treści merytorycznej odpowiedzi — tylko dopina
    wspólną kopertę tury i synchronizuje hash widocznej odpowiedzi.
    """
    repaired = dict(result or {})
    repairs: list[str] = []
    trace = repaired.get("trace") if isinstance(repaired.get("trace"), dict) else {}
    timestamp_header = str((trace or {}).get("timestamp_header") or "")
    if not timestamp_header:
        return repaired, repairs

    final_text = str(repaired.get("final_visible_text") or "").strip()
    contract = repaired.get("final_response_contract") if isinstance(repaired.get("final_response_contract"), dict) else {}
    contract_text = str((contract or {}).get("final_visible_text") or "").strip()
    if (not final_text or not final_text.startswith(f"{timestamp_header} ")) and contract_text.startswith(f"{timestamp_header} "):
        final_text = contract_text
        repairs.append("final_visible_text_restored_from_final_response_contract")
    elif final_text and not final_text.startswith(f"{timestamp_header} "):
        final_text = f"{timestamp_header} 🌿\n{final_text}"
        repairs.append("final_visible_text_timestamp_prefixed")

    if final_text:
        repaired["final_visible_text"] = final_text
        if isinstance(contract, dict):
            contract = dict(contract)
            contract["final_visible_text"] = final_text
            repaired["final_response_contract"] = contract
        runtime_provenance = repaired.get("runtime_provenance") if isinstance(repaired.get("runtime_provenance"), dict) else {}
        if isinstance(runtime_provenance, dict):
            runtime_provenance = dict(runtime_provenance)
            if runtime_provenance.get("visible_answer_text") != final_text:
                runtime_provenance["visible_answer_text"] = final_text
                runtime_provenance["visible_answer_hash"] = hashlib.sha256(final_text.encode("utf-8")).hexdigest()
                repairs.append("runtime_provenance_visible_answer_synced")
            repaired["runtime_provenance"] = runtime_provenance
            decision = repaired.get("conversation_decision") if isinstance(repaired.get("conversation_decision"), dict) else {}
            if isinstance(decision, dict):
                decision = dict(decision)
                decision["runtime_provenance"] = runtime_provenance
                decision["visible_answer_hash"] = runtime_provenance.get("visible_answer_hash")
                repaired["conversation_decision"] = decision
    return repaired, repairs


def validate_final_visible_integrity(result: dict[str, Any]) -> dict[str, Any]:
    return validate_result_integrity(result)
