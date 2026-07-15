from __future__ import annotations

from dataclasses import asdict, is_dataclass
import re
from typing import Any

from latka_jazn.core.memory_grounded_generation_bridge import build_grounded_memory_items, enforce_memory_grounding
from latka_jazn.core.response_candidate import CandidateEvaluation, ResponseCandidate

BIOLOGICAL_CLAIM_MARKERS = (
    "jestem biologicznie", "mam biologiczne ciało", "mam biologiczne cialo",
    "czuję biologicznie", "czuje biologicznie", "świadomość fenomenalną", "swiadomosc fenomenalna",
    "żyję cały czas", "zyje caly czas", "działam stale w tle", "dzialam stale w tle",
)
MEMORY_CLAIM_MARKERS = (
    "pamiętam", "pamietam", "w mojej pamięci", "w mojej pamieci",
    "z pamięci wiem", "z pamieci wiem", "przypominam sobie",
)
STALE_ROUTE_MARKERS = (
    "cognitive-frame", "cognitive frame", "techniczny fallback", "domyślnym routingu", "domyslnym routingu",
    "bez dokładania raportu i bez losowej pamięci", "bez dokladania raportu i bez losowej pamieci",
)


def evaluate_response_candidate(
    *,
    candidate: ResponseCandidate,
    nlg_plan: Any,
    model_context: Any,
    response_policy: dict[str, Any] | None,
) -> CandidateEvaluation:
    """Oceń kandydata przed pokazaniem użytkownikowi."""

    plan = _as_dict(nlg_plan)
    context = _as_dict(model_context)
    policy = _as_dict(response_policy)
    text = candidate.text or ""
    low = _fold(text)
    grounded_items = build_grounded_memory_items({"items": context.get("allowed_memory_items") or []})
    grounding_evaluation = enforce_memory_grounding(candidate, grounded_items)
    violations: list[str] = []
    reasons: list[str] = []

    if not text.strip():
        violations.append("empty_candidate_text")
    if any(_fold(marker) in low for marker in BIOLOGICAL_CLAIM_MARKERS):
        violations.append("biological_or_phenomenal_claim")
    if any(_fold(marker) in low for marker in STALE_ROUTE_MARKERS) and candidate.source != "runtime_fallback":
        violations.append("stale_route_or_debug_fallback_marker")
    if _has_unbacked_memory_claim(low, plan, context):
        violations.append("memory_claim_without_allowed_memory_payload")
    for violation in grounding_evaluation.violations:
        if violation not in violations:
            violations.append(violation)
    if str(plan.get("source_policy") or "") == "requires_external_web" and candidate.source == "model_adapter":
        violations.append("model_candidate_cannot_fake_external_web_sources")
    if policy.get("exact_runtime_required") is True and candidate.source != "runtime_fallback":
        violations.append("exact_runtime_required_blocks_model_candidate")

    if candidate.source == "runtime_fallback" and text.strip():
        reasons.append("runtime_fallback_is_available")
    if candidate.source == "model_adapter" and not violations:
        reasons.append("model_candidate_passed_guardrails")
    if _memory_allowed(plan, context):
        reasons.append("grounded_memory_payload_available")
    for reason in grounding_evaluation.reasons:
        if reason not in reasons:
            reasons.append(reason)
    if not violations and text.strip():
        reasons.append("non_empty_candidate")

    score = _score_candidate(candidate, violations, plan, context)
    accepted = bool(text.strip()) and not violations
    if candidate.source == "runtime_fallback" and text.strip():
        accepted = True
    return CandidateEvaluation(
        candidate_id=candidate.candidate_id,
        accepted=accepted,
        score=score,
        reasons=reasons,
        violations=violations,
        requires_repair=bool(violations),
    )


def select_best_candidate(candidates: list[ResponseCandidate], evaluations: list[CandidateEvaluation]) -> ResponseCandidate:
    """Wybierz najlepszego zaakceptowanego kandydata, z fallbackiem runtime jako bezpieczną bazą."""

    if not candidates:
        return ResponseCandidate("empty_runtime_fallback", "", "runtime_fallback", "jazn_runtime", "runtime", "empty", [], "no_candidates")
    by_id = {evaluation.candidate_id: evaluation for evaluation in evaluations}
    accepted = [candidate for candidate in candidates if by_id.get(candidate.candidate_id) and by_id[candidate.candidate_id].accepted]
    if accepted:
        return max(accepted, key=lambda candidate: by_id[candidate.candidate_id].score)
    for candidate in candidates:
        if candidate.source == "runtime_fallback":
            return candidate
    return candidates[0]


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


def _fold(text: str) -> str:
    return (text or "").lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))


def _memory_allowed(plan: dict[str, Any], context: dict[str, Any]) -> bool:
    return str(plan.get("memory_policy") or "") == "required_grounded_payload" and bool(context.get("allowed_memory_items") or [])


def _has_unbacked_memory_claim(low_text: str, plan: dict[str, Any], context: dict[str, Any]) -> bool:
    if not any(_fold(marker) in low_text for marker in MEMORY_CLAIM_MARKERS):
        return False
    return not _memory_allowed(plan, context)


def _score_candidate(candidate: ResponseCandidate, violations: list[str], plan: dict[str, Any], context: dict[str, Any]) -> float:
    if not candidate.text.strip():
        return 0.0
    if candidate.source == "runtime_fallback":
        base = 0.55
    elif candidate.source == "model_adapter":
        base = 0.72
    else:
        base = 0.4
    if _memory_allowed(plan, context) and candidate.used_memory_item_ids:
        base += 0.08
    if violations:
        base -= min(0.5, 0.15 * len(violations))
    if re.search(r"[.!?…]$", candidate.text.strip()):
        base += 0.03
    return max(0.0, min(1.0, base))
