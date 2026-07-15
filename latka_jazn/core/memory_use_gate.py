from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from latka_jazn.core.self_question_memory_gate import SelfQuestionMemoryGate

SCHEMA_VERSION = "memory_use_gate/v14.8.5.011"
_DIACRITIC_MAP = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

NON_MEMORY_INTENTS = {
    "runtime_health_check_after_update",
    "capability_status_question",
    "internet_access_question",
}

MEMORY_REQUIRED_INTENTS = {
    "self_memory_recall_request",
    "memory_experience_question",
    "user_memory_question",
    "identity_memory_question",
    "continuity_question",
    "self_architecture_audit_request",
    "jazn_development_plan_request",
}

@dataclass(slots=True)
class MemoryUseDecision:
    allow_memory_content: bool
    reason: str
    memory_role: str
    stale_route_risk: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = SCHEMA_VERSION
        return data

class MemoryUseGate:
    """Decyduje, czy wolno wprowadzić treści pamięci do widocznej odpowiedzi.

    Sama obecność słowa „ostatnio” nie wystarcza. W pytaniu „na co miałaś
    ostatnio ochotę?” pamięć mogłaby przypadkowo wstrzyknąć dawny fragment
    obcego tematu. Dlatego v14.8.1 rozdziela pytanie o własny stan Łatki od
    prawdziwej prośby o wspomnienie albo poprzedni wątek.
    """

    def decide(self, user_text: str, *, detected_intent: str | None = None) -> MemoryUseDecision:
        low = self._norm(user_text)
        intent = detected_intent or "unknown"
        self_gate = SelfQuestionMemoryGate().decide(user_text, detected_intent=intent)
        if self_gate.force_memory_content:
            return MemoryUseDecision(True, "self_question_memory_gate:" + self_gate.reason, "self_architecture_or_self_memory_content", "low")
        if intent in NON_MEMORY_INTENTS:
            return MemoryUseDecision(False, "non_memory_specialized_intent_blocks_retrieval", "disabled_for_turn", "low_after_gate")
        if intent in {"self_state_question", "reciprocal_self_state_question", "self_preference_question", "self_plan_question", "sleep_closure_statement"}:
            return MemoryUseDecision(False, "self_state_or_closure_uses_current_turn_not_memory_excerpt", "affective_context_only", "high_if_memory_excerpt_injected")
        if intent in MEMORY_REQUIRED_INTENTS:
            return MemoryUseDecision(True, "memory_required_intent", "content_source", "low")
        explicit = any(marker in low for marker in (
            "pamietasz", "pamiętasz", "wspomn", "przypomnij", "co mowilem", "co mówiłem",
            "nasza poprzednia rozmowa", "ten watek", "ten wątek", "wroc do", "wróć do",
        ))
        if explicit:
            return MemoryUseDecision(True, "explicit_memory_request", "content_source", "low")
        return MemoryUseDecision(False, "no_explicit_memory_request", "continuity_guard_only", "medium")

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower()).translate(_DIACRITIC_MAP)
