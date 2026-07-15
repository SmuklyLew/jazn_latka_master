from __future__ import annotations

from dataclasses import asdict, is_dataclass
import re
from typing import Any

from latka_jazn.core.response_candidate import ResponseCandidate
from latka_jazn.core.runtime_turn_contract import RuntimeTurnContract


def generate_response_candidates(
    *,
    adapter: Any,
    nlg_plan: Any,
    model_context: Any,
    fallback_body: str,
    max_candidates: int = 3,
    adapter_system_context: dict[str, Any] | None = None,
) -> list[ResponseCandidate]:
    """Wygeneruj kandydatów odpowiedzi bez udawania modelu.

    Fallback runtime jest zawsze kandydatem. Modelowy kandydat powstaje wyłącznie
    wtedy, gdy adapter jest jawnie skonfigurowany i zwróci status completed.
    """

    limit = _safe_limit(max_candidates)
    context = _as_dict(model_context)
    plan = _as_dict(nlg_plan)
    candidates = [
        ResponseCandidate(
            candidate_id="runtime_fallback",
            text=fallback_body,
            source="runtime_fallback",
            provider="jazn_runtime",
            model="runtime",
            status="available",
            used_memory_item_ids=_memory_item_ids(context),
            generation_reason="fallback_runtime_always_available",
        )
    ]
    if limit <= 1:
        return candidates[:limit]

    status = adapter.describe() if hasattr(adapter, "describe") else {"status": "unknown"}
    if status.get("status") != "configured":
        return candidates[:limit]

    prompt = _candidate_prompt(plan)
    system_context = dict(adapter_system_context or context)
    system_context.setdefault("nlg_plan", plan)
    system_context.setdefault("detected_intent", plan.get("detected_intent"))
    system_context.setdefault("route", plan.get("route"))
    turn_contract = RuntimeTurnContract.for_model_request(
        user_text=str(context.get("user_message") or prompt),
        detected_intent=str(plan.get("detected_intent") or context.get("detected_intent") or "unknown"),
        route=str(plan.get("route") or context.get("route") or "unknown"),
        runtime_exact_text=fallback_body,
        system_context=system_context,
    )
    request = turn_contract.to_model_adapter_request(
        user_text=str(context.get("user_message") or prompt),
        system_context=system_context,
    )
    request.metadata["candidate_prompt"] = prompt
    response = adapter.generate(request)
    text = _clean_model_text(getattr(response, "text", ""))
    if getattr(response, "status", "") == "completed" and text:
        candidates.append(
            ResponseCandidate(
                candidate_id="model_candidate_1",
                text=text,
                source="model_adapter",
                provider=str(getattr(response, "provider", status.get("name") or "unknown")),
                model=str(getattr(response, "model", status.get("model") or "unknown")),
                status=str(getattr(response, "status", "completed")),
                used_memory_item_ids=_memory_item_ids(context),
                generation_reason="adapter_completed",
                source_origin=str(getattr(response, "source_origin", "model_adapter")),
                endpoint_used=getattr(response, "endpoint_used", None),
                adapter_response=(response.to_dict() if hasattr(response, "to_dict") else {}),
            )
        )
    return candidates[:limit]


def _candidate_prompt(plan: dict[str, Any]) -> str:
    answer_kind = str(plan.get("answer_kind") or "natural_dialogue")
    return (
        "Utwórz jednego kandydata odpowiedzi zgodnego z NLG Plan i ModelContextPacket. "
        "Nie dodawaj timestampu; runtime zrobi to osobno. "
        "Nie opisuj procesu tworzenia odpowiedzi. "
        "Nie twierdź, że model jest Jaźnią, pamięcią ani źródłem prawdy. "
        f"Rodzaj odpowiedzi: {answer_kind}."
    )


def _safe_limit(value: Any) -> int:
    try:
        limit = int(value or 3)
    except (TypeError, ValueError):
        limit = 3
    return max(1, min(limit, 5))


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        maybe = value.to_dict()
        return maybe if isinstance(maybe, dict) else {}
    if is_dataclass(value):
        return asdict(value)
    return {}


def _memory_item_ids(context: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in context.get("allowed_memory_items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or item.get("id") or "").strip()
        if item_id and item_id not in ids:
            ids.append(item_id)
    return ids


def _clean_model_text(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(r"^\[🕒[^\]]+\]\s*", "", value).strip()
    return value
