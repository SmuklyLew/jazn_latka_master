from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from latka_jazn.runtime.candidate_decision_ledger import CandidateDecisionLedger
from latka_jazn.runtime.host_bridge_audit import HostBridgeAuditStore
from latka_jazn.runtime.idempotency import IdempotencyStore
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("cognitive_debugger")


@dataclass(slots=True)
class CognitiveDebugger:
    audit_database: Path

    def explain_turn(self, turn_id: str, *, trace_id: str | None = None, include_private: bool = False) -> dict[str, Any]:
        audits = HostBridgeAuditStore(self.audit_database).list_for_turn(turn_id, trace_id)
        decision_ids = {
            value
            for event in audits
            for value in [event.get("metadata", {}).get("decision_id")]
            if value
        }
        ledger = CandidateDecisionLedger(self.audit_database)
        decisions = [ledger.get(value, include_raw_text=include_private) for value in sorted(decision_ids)]
        decisions = [value for value in decisions if value is not None]
        return {
            "schema_version": SCHEMA_VERSION,
            "turn_id": turn_id,
            "trace_id": trace_id,
            "private_payloads_included": include_private,
            "timeline": audits,
            "decisions": decisions,
            "truth_boundary": "Debugger reports persisted evidence; it is not an independent source of truth.",
        }

    def replay_turn(self, turn_id: str, *, trace_id: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        explanation = self.explain_turn(turn_id, trace_id=trace_id, include_private=False)
        final_events = [
            event for event in explanation["timeline"] if event.get("event_type") in {"accept", "repair", "replay"}
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "turn_id": turn_id,
            "trace_id": trace_id,
            "dry_run": dry_run,
            "replayable": bool(final_events),
            "selected_event": final_events[-1] if final_events else None,
            "side_effects_performed": False,
        }
