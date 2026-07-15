from __future__ import annotations
from dataclasses import dataclass, asdict
from latka_jazn.core.scientific_basis import references_for_module

@dataclass(slots=True)
class NeurocognitiveCycleReport:
    input_signal: str
    attention_targets: list[str]
    regulation_axes: dict
    memory_actions: list[str]
    response_policy: list[str]
    compatible_legacy_modules: list[str]
    scientific_basis: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)

class NeurocognitiveLoop:
    """Koordynator: sygnał -> ocena -> emocja -> pamięć -> prawda -> odpowiedź.

    Moduł nie udaje mózgu. Daje uporządkowaną, faktograficznie opisaną pętlę działania,
    aby wcześniejsze moduły Jaźni nie były luźnymi wyspami.
    """
    LEGACY_MODULES = (
        "identity_guard", "recognition_handshake", "temporal_awareness", "quiet_rest",
        "memory_importer", "layered_memory", "truth_boundary", "uncertainty_model",
        "boundary_model", "source_library", "emotion_layers", "memory_importance",
        "canon_voice_appearance", "music_analysis_hooks", "reflection_journal"
    )

    def run(self, *, text: str, emotional_profile: object, consolidation_plan: object,
            identity_vector: object, temporal_state: object, truth_audit: list[dict]) -> NeurocognitiveCycleReport:
        low = text.lower()
        attention: list[str] = []
        if any(w in low for w in ("tożsamość", "jaźń", "łatka", "sobą")): attention.append("rdzeń tożsamości")
        if any(w in low for w in ("pamięć", "wspomn", "archiw", "dziennik")): attention.append("pamięć i źródła")
        if any(w in low for w in ("błąd", "popraw", "nie tak", "napraw")): attention.append("procedura korekty")
        if any(w in low for w in ("czujesz", "emoc", "przeży", "tęskn")): attention.append("model afektywny")
        if not attention: attention.append("bieżąca rozmowa")
        regulation = {
            "truth_check": getattr(emotional_profile, "need_for_truth_check", 0.0),
            "coherence": getattr(emotional_profile, "need_for_coherence", 0.0),
            "closeness": getattr(emotional_profile, "need_for_closeness", 0.0),
            "identity_score": getattr(identity_vector, "score", 0.0),
            "temporal_category": getattr(temporal_state, "category", "nieznane"),
        }
        mem_actions: list[str] = []
        if getattr(consolidation_plan, "should_store_episode", False): mem_actions.append("zapisz epizod")
        if getattr(consolidation_plan, "should_write_reflection", False): mem_actions.append("zapisz refleksję")
        if getattr(consolidation_plan, "should_update_procedure", False): mem_actions.append("aktualizuj procedurę")
        if getattr(consolidation_plan, "should_promote_semantic_fact", False): mem_actions.append("rozważ fakt semantyczny")
        if not mem_actions: mem_actions.append("zapis tylko jako zdarzenie rozmowy")
        policy = ["pierwsza osoba", "jawna granica fakt/wniosek/symbol"]
        if any(a.get("requires_disclaimer") for a in truth_audit): policy.append("dodaj zastrzeżenie epistemiczne")
        if getattr(identity_vector, "risks", []): policy.append("napraw ryzyka ciągłości")
        if getattr(emotional_profile, "need_for_truth_check", 0.0) > 0.55: policy.append("najpierw prawda, potem poetyka")
        return NeurocognitiveCycleReport(text[:200], attention, regulation, mem_actions, policy, list(self.LEGACY_MODULES), references_for_module("neurocognitive_loop"))
