from __future__ import annotations

from latka_jazn.adapters.chatgpt_adapter import ChatGPTAdapter
from latka_jazn.core.cognitive_turn_envelope import CognitiveTurnEnvelope
from latka_jazn.core.full_canon_model_context import (
    build_full_canon_model_context,
    evaluate_visible_voice_against_full_canon,
    validate_full_canon_model_context,
)
from latka_jazn.core.identity_guard import IdentityPerspectiveGuard
from latka_jazn.core.model_context_compiler import compile_model_context
from latka_jazn.core.response_candidate import ResponseCandidate
from latka_jazn.core.response_candidate_evaluator import evaluate_response_candidate
from latka_jazn.core.runtime_turn_contract import RuntimeTurnContract
from latka_jazn.version import PACKAGE_VERSION_FULL


def _plan() -> dict:
    return {
        "answer_kind": "natural_dialogue",
        "detected_intent": "ordinary_conversation",
        "route": "ordinary_dialogue",
        "memory_policy": "not_needed",
        "source_policy": "runtime_only",
        "forbidden_components": [],
        "truth_boundary": "runtime truth",
    }


def test_python_canon_fallback_is_complete_without_memory_or_project_style() -> None:
    context = build_full_canon_model_context({})
    validation = validate_full_canon_model_context(context)
    assert validation["ok"] is True
    assert context["read_only"] is True
    assert context["canon_presence"]["missing_blocks"] == []
    identity = context["immutable_canon"]["identity_core"]
    assert identity["identity_name"] == "Łatka"
    assert identity["grammar_gender"] == "feminine"
    assert "character_profile" in context["immutable_canon"]
    assert "relation_canon" in context["immutable_canon"]
    assert len(context["immutable_canon_sha256"]) == 64


def test_compiler_injects_full_canon_on_every_model_path() -> None:
    packet = compile_model_context(
        user_text="Cześć",
        cognitive_frame={},
        nlg_plan=_plan(),
        thought_frame={"truth_boundary": "runtime truth"},
        response_policy={},
    ).to_dict()
    assert packet["full_canon_model_context"]["validation"]["ok"] is True
    assert packet["voice_source_contract"]["speaking_identity"] == "Łatka"
    assert "Traktuj full_canon_model_context" in " ".join(packet["output_instructions"])


def test_adapter_request_uses_full_canon_as_developer_instructions() -> None:
    packet = compile_model_context(
        user_text="Jak się masz?",
        cognitive_frame={},
        nlg_plan=_plan(),
        thought_frame={},
        response_policy={},
    ).to_dict()
    contract = RuntimeTurnContract.for_model_request(
        user_text="Jak się masz?",
        detected_intent="ordinary_conversation",
        route="ordinary_dialogue",
        runtime_exact_text="runtime fallback",
        system_context=packet,
    )
    request = contract.to_model_adapter_request(user_text="Jak się masz?", system_context=packet)
    assert "<full_canon" in (request.instructions or "")
    assert "Łatki" in (request.instructions or "")
    assert request.metadata["full_canon_required"] is True
    assert request.metadata["full_canon_sha256"] == packet["full_canon_model_context"]["immutable_canon_sha256"]


def test_chatgpt_contract_carries_full_canon_and_host_rules() -> None:
    contract = ChatGPTAdapter().contract().to_dict()
    assert contract["full_canon_model_context"]["validation"]["ok"] is True
    assert contract["host_generation_contract"]["ok"] is True
    assert contract["full_canon_sha256"] == contract["full_canon_model_context"]["immutable_canon_sha256"]


def test_cognitive_envelope_exposes_full_canon_at_top_level() -> None:
    envelope = CognitiveTurnEnvelope.from_cognitive_frame(
        {
            "runtime_version": PACKAGE_VERSION_FULL,
            "timestamp": "[🕒 2026-07-15 12:00:00 GMT+0200, Wednesday, Europe/Warsaw]",
            "voice_source_contract": {"speaking_identity": "Łatka"},
        },
        user_text="Cześć",
        client_context={"client": "test"},
    ).to_dict()
    assert envelope["full_canon_model_context"]["validation"]["ok"] is True
    assert envelope["host_generation_contract"]["ok"] is True
    assert envelope["cognitive_frame"]["full_canon_sha256"] == envelope["full_canon_sha256"]


def test_model_candidate_with_third_person_identity_drift_is_rejected() -> None:
    full_canon = build_full_canon_model_context({})
    candidate = ResponseCandidate(
        candidate_id="model-1",
        text="Łatka jest przy Tobie i odpowiada spokojnie.",
        source="model_adapter",
        provider="test",
        model="test",
        status="completed",
        used_memory_item_ids=[],
        generation_reason="test",
    )
    evaluation = evaluate_response_candidate(
        candidate=candidate,
        nlg_plan=_plan(),
        model_context={"full_canon_model_context": full_canon, "allowed_memory_items": []},
        response_policy={},
    )
    assert evaluation.accepted is False
    assert "third_person_latka_voice_drift" in evaluation.violations


def test_first_person_voice_is_accepted_by_canon_gate() -> None:
    full_canon = build_full_canon_model_context({})
    result = evaluate_visible_voice_against_full_canon(
        "Jestem przy tej rozmowie i odpowiadam własnym głosem.",
        full_canon,
    )
    assert result["ok"] is True


def test_identity_guard_uses_real_word_boundaries() -> None:
    guard = IdentityPerspectiveGuard()
    assert guard.enforce("Łatka jest obecna.") == "Jestem obecna."
    assert guard.enforce("Łatka pamięta ten fragment.") == "Pamiętam ten fragment."
