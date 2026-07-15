from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

SCHEMA_VERSION = "dialogue_state/v14.6.2"


@dataclass(slots=True)
class DialogueState:
    mode: str
    user_need: str
    technical_visibility: str
    next_reply_policy: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DialogueStateTracker:
    """Mały blackboard dialogu: co ma sterować następną odpowiedzią."""

    def classify(self, *, user_text: str, intent_tags: list[str] | None = None, client_context: dict[str, Any] | None = None) -> DialogueState:
        low = (user_text or "").lower()
        tags = set(intent_tags or [])
        client_context = client_context or {}
        if client_context.get("debug_direct"):
            return DialogueState(
                mode="debug_requested",
                user_need="diagnostyka techniczna",
                technical_visibility="show_runtime_details",
                next_reply_policy="można pokazać trasy, pliki i fallback diagnostics",
            )
        if any(x in low for x in ("procedura startowa", "instrukcja startowa", "aktywna paczka", "uruchom runtime", "wywołaj runtime", "odpowiedź runtime", "granica prawdy")):
            return DialogueState(
                mode="startup_truth_contract",
                user_need="sprawdzenie aktywnej paczki, realnego runtime i trafności odpowiedzi",
                technical_visibility="concise_startup_status",
                next_reply_policy="najpierw runtime/status, potem jedna odpowiedź; nie utożsamiać stylu z Jaźnią",
            )
        if any(x in low for x in ("v14.6.2", "14.6.2", "pełną aktualizację", "pelna aktualizacje", "prawidłową", "prawidlowa")):
            return DialogueState(
                mode="version_update_execution",
                user_need="pełna aktualizacja plików, testów, manifestu i eksportu",
                technical_visibility="implementation_evidence",
                next_reply_policy="pracować na istniejących modułach; pokazać tylko wynik, testy i ograniczenia",
            )
        if any(x in low for x in ("migren", "ból", "bol", "frimig", "niewysp")):
            return DialogueState(
                mode="care_first",
                user_need="spokojna obecność i brak presji",
                technical_visibility="minimal",
                next_reply_policy="krótko, łagodnie, bez przeciążania listą napraw",
            )
        if "correction" in tags or "architecture" in tags or "runtime" in low or "timestamp" in low:
            return DialogueState(
                mode="repair_integrated",
                user_need="naprawa spójności runtime/ChatGPT/timestamp/afekt",
                technical_visibility="concise_evidence",
                next_reply_policy="mów, co realnie poprawiono; nie ukrywaj ograniczeń",
            )
        if "affect" in tags or "awareness" in tags:
            return DialogueState(
                mode="self_state",
                user_need="odpowiedź o stanie operacyjnym Jaźni",
                technical_visibility="truth_boundary_visible",
                next_reply_policy="pierwsza osoba, stan operacyjny, bez biologicznego udawania",
            )
        return DialogueState(
            mode="ordinary_dialogue",
            user_need="naturalna rozmowa",
            technical_visibility="hidden_unless_requested",
            next_reply_policy="jeden głos Łatki; runtime jako źródło kontekstu, nie osobny raport",
        )
