from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("reasoning_controller")


@dataclass(slots=True)
class ReasoningDecision:
    decision: str
    reason: str
    repair_hint: str | None = None
    blocks_final: bool = False
    schema_version: str = SCHEMA_VERSION
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReasoningController:
    """Final reasoning gate tied to the original user message.

    Route selection is evidence, not authority.  A candidate that only answers a
    mistakenly selected health-check cannot pass a broader architecture/capability
    request present in the original text.
    """

    _BROAD_AUDIT_TERMS = (
        "co umiesz", "co potrafisz", "co działa", "co dziala", "kod źródłowy", "kod zrodlowy",
        "co trzeba naprawić", "co trzeba naprawic", "co jeszcze trzeba naprawić", "co jeszcze trzeba naprawic",
        "gdzie są luki", "gdzie sa luki", "jakie są luki", "jakie sa luki", "co blokuje",
        "moduły", "moduly", "narzędzia", "narzedzia", "adapter", "test", "weryfik",
    )
    _HEALTH_INTENTS = {"runtime_health_check", "runtime_health_check_after_update", "runtime_activation_status_question"}

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    @classmethod
    def _original_intent_alignment(cls, user_text: str, intent: str, route: str, body: str) -> dict[str, Any]:
        source = cls._norm(user_text)
        candidate = cls._norm(body)
        audit_hits = [term for term in cls._BROAD_AUDIT_TERMS if term in source]
        broad = len(set(audit_hits)) >= 3 and any(token in source for token in ("jaź", "jazn", "runtime", "system", "kod", "adapter"))
        health_only = str(intent or "") in cls._HEALTH_INTENTS or str(route or "") in cls._HEALTH_INTENTS
        families = {
            "capability_or_scope": ("potraf", "umiesz", "zdolno", "moduł", "modul", "adapter", "narzędz", "narzedz", "zakres"),
            "defects_or_gaps": ("błąd", "blad", "luka", "napraw", "blok", "ryzy", "brak", "defekt"),
            "verification": ("test", "sprawdz", "weryfik", "integrity", "status", "wynik", "dowód", "dowod"),
        }
        required = list(families) if broad else []
        missing = [name for name, markers in families.items() if broad and not any(marker in candidate for marker in markers)]
        aligned = not (broad and health_only) and not missing
        return {
            "aligned": aligned,
            "broad_system_audit_requested": broad,
            "audit_evidence": audit_hits,
            "health_only_route_selected": health_only,
            "required_semantic_families": required,
            "missing_semantic_families": missing,
        }

    def assess_turn(
        self,
        *,
        user_text: str,
        intent: str,
        route: str,
        handler_name: str,
        body: str,
        policy: dict[str, Any] | None = None,
        logic_audit: dict[str, Any] | None = None,
        validation: dict[str, Any] | None = None,
    ) -> ReasoningDecision:
        policy = policy or {}
        logic_audit = logic_audit or {}
        validation = validation or {}
        alignment = self._original_intent_alignment(user_text, intent, route, body)
        base_details = {
            "intent": intent,
            "route": route,
            "handler_name": handler_name,
            "original_intent_alignment": alignment,
        }
        if alignment["broad_system_audit_requested"] and alignment["health_only_route_selected"]:
            return ReasoningDecision(
                "regenerate",
                "original_user_intent_conflicts_with_health_check_route",
                "Reclassify the full message as a system/capability audit and answer scope, defects and verification.",
                True,
                details=base_details,
            )
        if alignment["missing_semantic_families"]:
            return ReasoningDecision(
                "regenerate",
                "original_user_intent_not_fully_covered",
                "Add missing sections: " + ", ".join(alignment["missing_semantic_families"]),
                True,
                details=base_details,
            )
        if logic_audit.get("must_regenerate"):
            return ReasoningDecision(
                "regenerate", "turn_logic_audit_failed", logic_audit.get("repair_hint"), True,
                details={**base_details, "logic_errors": logic_audit.get("logic_errors")},
            )
        if validation.get("must_regenerate"):
            return ReasoningDecision(
                "regenerate", "runtime_answer_validator_failed", validation.get("mismatch_reason"), True,
                details={**base_details, "validation": validation},
            )
        required = set(policy.get("required_components") or [])
        if required and validation.get("missing_required_components"):
            return ReasoningDecision(
                "regenerate", "missing_required_components",
                ", ".join(validation.get("missing_required_components") or []), True,
                details=base_details,
            )
        if policy.get("source_boundary_required") and not any(x in (body or "").lower() for x in ("runtime", "chatgpt", "aktywn", "model", "granica")):
            return ReasoningDecision(
                "regenerate", "source_boundary_required_but_missing", "Add runtime/model/source boundary.", True,
                details=base_details,
            )
        return ReasoningDecision(
            "accept", "policy_validation_logic_and_original_intent_passed", details={"original_intent_alignment": alignment}
        )
