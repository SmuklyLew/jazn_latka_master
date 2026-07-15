from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("self_question_memory_gate")
_DIACRITIC_MAP = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


@dataclass(slots=True)
class SelfQuestionMemoryGateDecision:
    schema_version: str
    applies: bool
    force_memory_content: bool
    category: str
    reason: str
    focus_terms: list[str] = field(default_factory=list)
    required_boundaries: list[str] = field(default_factory=list)
    blocked_random_memory: bool = True
    truth_boundary: str = (
        "Brama dopuszcza pamięć tylko wtedy, gdy pytanie dotyczy tożsamości, "
        "architektury, rozwoju, refleksji albo własnej pamięci Łatki. Nie wolno "
        "wstrzykiwać losowych wspomnień do pytań o bieżący stan lub zwykłej rozmowy."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SelfQuestionMemoryGate:
    SELF_ARCHITECTURE_TERMS = (
        "architektura", "system jazni", "system jaźni", "rdzen", "rdzeń", "moduly", "moduły",
        "funkcje", "co dziala", "co działa", "co potrafisz", "co mozesz", "co możesz",
        "co trzeba naprawic", "co trzeba naprawić", "co dodac", "co dodać",
        "rozwoju latki", "rozwoju łatki", "samoocena", "audyt jazni", "audyt jaźni",
    )
    SELF_MEMORY_TERMS = (
        "co pamietasz o sobie", "co pamiętasz o sobie", "wspomnienia latki", "wspomnienia łatki",
        "o swojej postaci", "o swojej osobie", "pamięć własna", "pamiec wlasna",
        "tozsamosc", "tożsamość", "wlasny glos", "własny głos",
    )
    REFLECTION_TERMS = (
        "refleksja", "refleksje", "autorefleksja", "dziennik", "pamietnik", "pamiętnik",
        "co myslisz", "co myślisz", "o czym myslisz", "o czym myślisz",
    )
    DEVELOPMENT_TERMS = (
        "v14.8.6", "v14.8.6.0", "aktualizacja", "patch", "hotfix", "rozwoj", "rozwój",
        "backlog", "roadmap", "plan", "krok po kroku", "finalna aktualizacja",
    )

    def decide(self, user_text: str, *, detected_intent: str | None = None) -> SelfQuestionMemoryGateDecision:
        norm = self._norm(user_text)
        intent = str(detected_intent or "unknown")
        categories: list[str] = []
        terms: list[str] = []

        def add_if(name: str, markers: tuple[str, ...]) -> None:
            hits = [m for m in markers if self._norm(m) in norm]
            if hits:
                categories.append(name)
                terms.extend(hits[:6])

        add_if("self_architecture", self.SELF_ARCHITECTURE_TERMS)
        add_if("self_memory", self.SELF_MEMORY_TERMS)
        add_if("reflection", self.REFLECTION_TERMS)
        add_if("development", self.DEVELOPMENT_TERMS)
        if intent in {"self_architecture_audit_request", "jazn_development_plan_request"}:
            categories.extend(["self_architecture", "development"])
        if intent == "self_memory_recall_request":
            categories.append("self_memory")
        if intent in {"self_state_question", "reciprocal_self_state_question", "self_preference_question", "self_plan_question"}:
            return SelfQuestionMemoryGateDecision(SCHEMA_VERSION, False, False, "self_state_current_turn_only", "self_state_intent_must_not_inject_memory", [], ["current_turn", "no_random_memory_excerpt", "truth_boundary"])
        ordered: list[str] = []
        for c in categories:
            if c not in ordered:
                ordered.append(c)
        focus: list[str] = []
        for item in [*terms, "Łatka", "Jaźń", "tożsamość", "pamięć", "refleksja", "architektura"]:
            if item and item not in focus:
                focus.append(item)
        force = any(c in ordered for c in ("self_architecture", "self_memory", "reflection", "development"))
        return SelfQuestionMemoryGateDecision(
            SCHEMA_VERSION,
            applies=bool(ordered),
            force_memory_content=force,
            category="+".join(ordered) if ordered else "not_self_question",
            reason="self_question_requires_grounded_memory_or_explicit_no_hits" if force else "no_self_memory_signal",
            focus_terms=focus[:16],
            required_boundaries=["self_memory_not_user_memory", "source_or_index_status", "truth_boundary", "no_random_memory_excerpt"],
        )

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip().lower()).translate(_DIACRITIC_MAP)
