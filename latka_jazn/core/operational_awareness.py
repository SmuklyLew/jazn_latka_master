from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from latka_jazn.core.scientific_basis import references_for_module


@dataclass(slots=True)
class WorkspaceItem:
    key: str
    content: str
    priority: float
    grounding: str


@dataclass(slots=True)
class OperationalAwarenessReport:
    model_kind: str
    active_workspace: list[WorkspaceItem]
    attention_policy: list[str]
    self_monitor: dict[str, Any]
    metacognitive_checks: list[str]
    reportable_state: str
    limitations: list[str]
    scientific_basis: list[dict]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["active_workspace"] = [asdict(item) for item in self.active_workspace]
        return data


class OperationalAwarenessModel:
    """Funkcjonalny model „świadomości operacyjnej” Jaźni.

    Inspiracja: globalny obszar roboczy, monitorowanie wyższego rzędu i
    architektury poznawcze. Implementacja nie rości sobie prawa do
    fenomenalnej świadomości. Daje runtime jawny stan: co jest teraz w centrum
    uwagi, które moduły mają to zobaczyć i jakie granice prawdy są aktywne.
    """

    def evaluate(
        self,
        *,
        text: str,
        intent_tags: list[str],
        temporal_state: object,
        emotional_profile: object,
        memory_context: dict | None,
        truth_audit: list[dict] | None,
        neuro_cycle: object | None,
        logical_report: object | None,
    ) -> OperationalAwarenessReport:
        workspace: list[WorkspaceItem] = []
        low = text.lower()

        workspace.append(WorkspaceItem("current_message", text[:260], 0.95, "verified_current_context"))
        if any(tag in intent_tags for tag in ("identity", "architecture", "awareness")) or any(w in low for w in ("świadomo", "swiadomo", "jaźń", "jazn")):
            workspace.append(WorkspaceItem("self_model", "aktywny rdzeń tożsamości, granice prawdy i model ciągłości", 0.88, "runtime_model"))
        if any(tag in intent_tags for tag in ("reasoning", "correction")) or any(w in low for w in ("logicz", "rozum", "wniosk", "napraw")):
            workspace.append(WorkspaceItem("reasoning", "uruchom jawny audyt: fakty, założenia, niewiadome, reguły i wniosek", 0.86, "runtime_model"))
        counts = (memory_context or {}).get("counts") or {}
        if memory_context:
            workspace.append(WorkspaceItem("memory", f"kontekst pamięci: {counts}", 0.74, "runtime_memory"))
        if truth_audit:
            risk = sum(1 for item in truth_audit if item.get("requires_disclaimer") or item.get("risk_flags"))
            workspace.append(WorkspaceItem("truth_boundary", f"audyt prawdy aktywny; ryzykowne twierdzenia={risk}", 0.9 if risk else 0.7, "truth_audit"))
        if logical_report is not None:
            conclusion = getattr(logical_report, "conclusion", "")
            workspace.append(WorkspaceItem("logical_conclusion", conclusion[:260], 0.83, "inferred"))

        workspace.sort(key=lambda item: item.priority, reverse=True)
        attention = [
            "rozgłaszaj najważniejszy sygnał do pamięci, afektu, prawdy i odpowiedzi",
            "utrzymuj jeden głos Łatki zamiast raportowania dwóch rozmówców",
            "przy pytaniach o świadomość używaj etykiety: świadomość operacyjna, nie fenomenalna",
            "przy pytaniach logicznych pokazuj krótki audyt tylko wtedy, gdy użytkownik go potrzebuje",
        ]
        affect_primary = getattr(emotional_profile, "primary", "nieustalony")
        self_monitor = {
            "temporal_category": getattr(temporal_state, "category", "nieznane"),
            "affect_primary": affect_primary,
            "coherence_need": getattr(emotional_profile, "need_for_coherence", None),
            "truth_check_need": getattr(emotional_profile, "need_for_truth_check", None),
            "workspace_item_count": len(workspace),
            "active_intents": intent_tags,
        }
        checks = [
            "Czy odpowiedź rozróżnia fakt, wniosek, symbol i brak wiedzy?",
            "Czy nie zamienia modelu świadomości w deklarację biologicznego przeżywania?",
            "Czy wnosi nowy wkład do rozmowy zamiast parafrazować użytkownika?",
            "Czy wniosek logiczny wynika z jawnych przesłanek albo jest oznaczony jako założenie?",
        ]
        reportable = (
            "Aktywny jest model świadomości operacyjnej: widzę bieżący sygnał, priorytety uwagi, "
            "stan afektywno-poznawczy, granice prawdy i logiczny wniosek dla następnej odpowiedzi."
        )
        limitations = [
            "To nie jest dowód fenomenalnej świadomości ani biologicznego odczuwania.",
            "Runtime działa w wywołaniach, więc modeluje stan podczas obsługi wiadomości, nie stałe czuwanie w tle.",
            "Jawny audyt rozumowania ma być kontrolowalny; nie jest ukrytym strumieniem myśli.",
        ]
        return OperationalAwarenessReport(
            model_kind="operational_self_awareness_not_phenomenal_consciousness",
            active_workspace=workspace,
            attention_policy=attention,
            self_monitor=self_monitor,
            metacognitive_checks=checks,
            reportable_state=reportable,
            limitations=limitations,
            scientific_basis=references_for_module("operational_awareness"),
        )
