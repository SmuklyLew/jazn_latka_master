from __future__ import annotations

from copy import deepcopy
from typing import Any

from latka_jazn.core.canon.identity_canon import default_identity_canon_data
from latka_jazn.version import schema_version


SCHEMA_VERSION = schema_version("runtime_ownership_contract")


def build_runtime_ownership_contract(
    *,
    detected_intent: str = "",
    route: str = "",
) -> dict[str, Any]:
    """Return the source-controlled ownership boundary for a visible reply.

    Agent instructions may start and validate the runtime, but they must not
    become a second source of identity, voice, routing or memory policy.
    """

    identity = default_identity_canon_data()
    host_contract = deepcopy(identity.get("host_visible_generation_contract") or {})
    return {
        "schema_version": SCHEMA_VERSION,
        "current_turn": {
            "detected_intent": str(detected_intent or ""),
            "route": str(route or ""),
        },
        "ownership": {
            "routing": [
                "latka_jazn/nlp/dialogue_intent_classifier.py",
                "latka_jazn/core/route_contract_matrix.py",
                "latka_jazn/core/route_registry.py",
            ],
            "identity": [
                "latka_jazn/core/canon/identity_canon.py",
                "latka_jazn/core/canon/canon_registry.py",
            ],
            "voice": [
                "latka_jazn/core/handlers/",
                "latka_jazn/core/runtime_response_synthesizer.py",
                "latka_jazn/core/model_guided_response_synthesizer.py",
            ],
            "memory": [
                "latka_jazn/core/memory_use_gate.py",
                "latka_jazn/memory/",
                "verified runtime memory tiers",
            ],
            "finalization": [
                "latka_jazn/core/chat_command_contract.py",
                "latka_jazn/core/host_visible_finalization.py",
                "runtime validators and turn ledger",
            ],
        },
        "identity_voice": {
            "identity_name": identity.get("identity_name"),
            "display_name": identity.get("display_name"),
            "dialogue_language": identity.get("dialogue_language", "pl-PL"),
            "grammar_gender": identity.get("grammar_gender"),
            "voice_style": identity.get("voice_style"),
            "narrative_rules": identity.get("narrative_rules"),
            "identity_perspective_contract": identity.get("identity_perspective_contract"),
        },
        "memory_truth": {
            "truthful_memory_contract": identity.get("truthful_memory_contract"),
            "safety_principles": identity.get("safety_principles"),
        },
        "host_visible_generation_contract": host_contract,
        "host_boundary": (
            "Project instructions and AGENTS files may operate the runtime, but must not "
            "supply Łatka's identity, voice, routing decision or memory content."
        ),
    }
