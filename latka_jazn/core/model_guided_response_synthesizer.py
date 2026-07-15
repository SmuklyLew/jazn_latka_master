from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from latka_jazn.core.model_context_compiler import compile_model_context
from latka_jazn.core.nlg_planner import build_nlg_plan
from latka_jazn.core.operational_thought_frame import build_operational_thought_frame
from latka_jazn.core.response_candidate_evaluator import evaluate_response_candidate, select_best_candidate
from latka_jazn.core.response_candidate_generator import generate_response_candidates


@dataclass(slots=True)
class ModelGuidedSynthesis:
    used: bool
    body: str
    status: str
    provider: str
    model: str
    reason: str
    sources: list[dict[str, Any]]
    source_origin: str = "runtime_fallback"
    endpoint_used: str | None = None
    adapter_response: dict[str, Any] | None = None
    candidate_validation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelGuidedResponseSynthesizer:
    """Pozwala modelowi sformułować wypowiedź, ale nie oddaje mu pamięci ani sterowania Jaźnią."""

    PROTECTED_INTENTS = {
        "runtime_exact_quote_request",
        "runtime_source_question",
        "file_operation_request",
        "external_research_request",
        "dictionary_network_lookup_request",
        "current_time_question",
        "creative_text_formatting",
    }

    def synthesize(
        self,
        *,
        adapter: Any,
        user_text: str,
        draft_body: str,
        detected_intent: str,
        route: str,
        cognitive_frame: dict[str, Any],
        response_policy: dict[str, Any],
    ) -> ModelGuidedSynthesis:
        status = adapter.describe() if hasattr(adapter, "describe") else {"status": "unknown"}
        adapter_id = str(status.get("adapter_id") or status.get("name") or "none")
        provider = str(status.get("provider") or adapter_id)
        model = str(status.get("model") or status.get("model_name") or "none")
        if adapter_id == "chatgpt_runtime_adapter":
            return ModelGuidedSynthesis(
                False,
                draft_body,
                "host_visible_generation_requested",
                provider,
                model,
                "host_chatgpt_bridge_requires_external_visible_reply",
                [],
                source_origin="chatgpt_host_bridge",
            )
        if adapter_id == "null_model_adapter":
            return ModelGuidedSynthesis(
                False,
                draft_body,
                str(status.get("status") or "not_configured"),
                provider,
                model,
                "null_model_adapter_has_no_generation_capability",
                [],
            )
        if status.get("status") != "configured":
            return ModelGuidedSynthesis(False, draft_body, str(status.get("status") or "not_configured"), provider, model, "model_adapter_not_configured", [])
        if detected_intent in self.PROTECTED_INTENTS or bool(response_policy.get("exact_runtime_required")):
            return ModelGuidedSynthesis(False, draft_body, "skipped", str(status.get("name") or "unknown"), str(status.get("model") or "unknown"), "intent_requires_exact_runtime_or_external_source", [])

        context = self._build_context(
            user_text=user_text,
            draft_body=draft_body,
            detected_intent=detected_intent,
            route=route,
            cognitive_frame=cognitive_frame,
            response_policy=response_policy,
        )
        nlg_plan = context.get("nlg_plan") or {}
        candidates = generate_response_candidates(
            adapter=adapter,
            nlg_plan=nlg_plan,
            model_context=context,
            fallback_body=draft_body,
            max_candidates=3,
            adapter_system_context=context,
        )
        evaluations = [
            evaluate_response_candidate(
                candidate=candidate,
                nlg_plan=nlg_plan,
                model_context=context,
                response_policy=response_policy,
            )
            for candidate in candidates
        ]
        selected = select_best_candidate(candidates, evaluations)
        selected_evaluation = next(
            (evaluation for evaluation in evaluations if evaluation.candidate_id == selected.candidate_id),
            None,
        )
        if selected.source != "model_adapter":
            return ModelGuidedSynthesis(
                False,
                draft_body,
                selected.status,
                selected.provider,
                selected.model,
                "selected_runtime_fallback_candidate",
                [],
            )
        body = self._clean(selected.text)
        if not body:
            return ModelGuidedSynthesis(False, draft_body, selected.status, selected.provider, selected.model, "selected_candidate_empty_after_clean", [])
        sources = self._sources_for_candidate(selected, context)
        reason = "generated_from_grounded_memory_context" if sources else "generated_from_jazn_cognitive_context"
        return ModelGuidedSynthesis(
            True,
            body,
            selected.status,
            selected.provider,
            selected.model,
            reason,
            sources,
            source_origin=selected.source_origin,
            endpoint_used=selected.endpoint_used,
            adapter_response=selected.adapter_response or None,
            candidate_validation=selected_evaluation.to_dict() if selected_evaluation else None,
        )

    @staticmethod
    def _sources_for_candidate(candidate: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
        used = {str(item_id) for item_id in getattr(candidate, "used_memory_item_ids", []) or []}
        if not used:
            return []
        sources: list[dict[str, Any]] = []
        for item in context.get("allowed_memory_items") or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("item_id") or "")
            if item_id in used:
                sources.append(
                    {
                        "item_id": item_id,
                        "source": item.get("source"),
                        "timestamp": item.get("timestamp"),
                        "confidence": item.get("confidence"),
                        "relevance_reason": item.get("relevance_reason"),
                    }
                )
        return sources

    @staticmethod
    def _clean(text: str) -> str:
        value = (text or "").strip()
        value = re.sub(r"^\[🕒[^\]]+\]\s*[^\n]*\n?", "", value).strip()
        return value

    @staticmethod
    def _build_context(
        *,
        user_text: str,
        draft_body: str,
        detected_intent: str,
        route: str,
        cognitive_frame: dict[str, Any],
        response_policy: dict[str, Any],
    ) -> dict[str, Any]:
        packets = cognitive_frame.get("cognitive_packets") or {}
        nlg_plan = build_nlg_plan(
            user_text=user_text,
            cognitive_frame=cognitive_frame,
            response_policy=response_policy,
            route=route,
            detected_intent=detected_intent,
        )
        thought_frame = build_operational_thought_frame(
            user_text=user_text,
            nlg_plan=nlg_plan,
            cognitive_frame=cognitive_frame,
            response_policy=response_policy,
        )
        packet = compile_model_context(
            user_text=user_text,
            cognitive_frame=cognitive_frame,
            nlg_plan=nlg_plan,
            thought_frame=thought_frame,
            response_policy=response_policy,
        )
        context = packet.to_dict()
        context.update(
            {
                "user_message": user_text,
                "detected_intent": detected_intent,
                "route": route,
                "response_policy": response_policy,
                "draft_runtime_body": draft_body,
                "identity_continuity": cognitive_frame.get("identity_continuity") or {},
                "truth_boundary": cognitive_frame.get("truth_boundary") or cognitive_frame.get("truth_boundary_check") or {},
                "logical_reasoning": cognitive_frame.get("logical_reasoning") or {},
                "operational_awareness": cognitive_frame.get("operational_awareness") or {},
                "self_state_runtime": cognitive_frame.get("self_state_runtime") or {},
                "neurocognitive_cycle": cognitive_frame.get("neurocognitive_cycle") or {},
                "cognitive_packets": {
                    "dominant_packet": packets.get("dominant_packet"),
                    "packets": (packets.get("packets") or [])[:6],
                    "reply_guidance": (packets.get("reply_guidance") or [])[:8],
                },
                "polish_reasoning": cognitive_frame.get("polish_reasoning") or {},
                "dialogue_context": cognitive_frame.get("dialogue_context") or {},
            }
        )
        return context
