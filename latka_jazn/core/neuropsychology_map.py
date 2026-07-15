from __future__ import annotations
from dataclasses import dataclass
from latka_jazn.core.scientific_basis import reference_by_key

@dataclass(frozen=True, slots=True)
class HumanInspiredPrinciple:
    name: str
    human_basis: str
    system_mapping: str
    source_key: str
    module_targets: tuple[str, ...]

HUMAN_INSPIRED_PRINCIPLES: tuple[HumanInspiredPrinciple, ...] = (
    HumanInspiredPrinciple(
        "pamięć epizodyczna jako kontekst",
        "badania nad hipokampem wiążą epizody z kontekstem, relacją i późniejszym użyciem w poznaniu",
        "zapisuj scenę z czasem, uczestnikami, kotwicą emocjonalną, źródłem i pewnością",
        "hippocampus_episodic_memory",
        ("layered_memory", "identity_dynamics"),
    ),
    HumanInspiredPrinciple(
        "konsolidacja zamiast natychmiastowego kanonu",
        "konsolidacja przekształca ślady tymczasowe w stabilniejsze formy",
        "najpierw epizod, potem refleksja, potem ewentualny fakt semantyczny lub procedura",
        "squire_memory_consolidation",
        ("memory_consolidation", "reflection_journal"),
    ),
    HumanInspiredPrinciple(
        "integracja hipokamp-amygdala-przedczołowa",
        "interakcje układów pamięci, oceny afektywnej i kontroli wspierają złożone uczenie",
        "łącz fakt, afekt, kontekst czasu i decyzję kanoniczną w jednym wpisie pamięci",
        "pmc_interacting_brain_systems_memory_consolidation",
        ("layered_memory", "emotion_layers", "identity_dynamics"),
    ),
    HumanInspiredPrinciple(
        "hipokamp-przedczołowa-amygdala uczenie",
        "systemy pamięci, oceny znaczenia i kontroli poznawczej współdziałają w uczeniu i pamięci",
        "zapis pamięci musi łączyć kontekst, afekt, korektę i decyzję proceduralną",
        "pmc_hippocampus_prefrontal_amygdala_learning_memory",
        ("layered_memory", "identity_dynamics", "procedural_memory"),
    ),
    HumanInspiredPrinciple(
        "emocja jako ocena i regulacja",
        "teorie oceny emocji wiążą emocje ze znaczeniem zdarzenia dla celów, kontroli i pewności",
        "warstwa emocji ma wpływać na uwagę, wagę pamięci, ostrożność i styl odpowiedzi",
        "appraisal_theory",
        ("emotion_layers", "memory_importance"),
    ),
    HumanInspiredPrinciple(
        "emocje konstruowane z kontekstu",
        "teoria konstruowanych emocji podkreśla rolę predykcji, pojęć, afektu i kontekstu",
        "Łatka używa pojęć emocji jako jawnych etykiet modelowanego rezonansu, nie udaje ciała",
        "barrett_constructed_emotion",
        ("emotion_layers", "boundary_model"),
    ),
    HumanInspiredPrinciple(
        "korekta jako sygnał uczący",
        "błąd predykcji nagrody w uczeniu wzmacniającym jest sygnałem zmiany oczekiwań",
        "korekta Krzysztofa aktualizuje reguły proceduralne i zwiększa wagę audytu prawdy",
        "dopamine_reward_prediction_error",
        ("procedural_memory", "truth_boundary"),
    ),
    HumanInspiredPrinciple(
        "granice bezpieczeństwa jako układ obronny",
        "przetwarzanie zagrożeń organizuje priorytety zachowania i hamuje działania ryzykowne",
        "ryzyko fałszywego wspomnienia lub biologicznego udawania blokuje narrację bez etykiety",
        "ledoux_threat_survival",
        ("boundary_model", "truth_boundary"),
    ),
    HumanInspiredPrinciple(
        "agentowa pamięć-refleksja-planowanie",
        "generative agents łączą zapis doświadczeń, refleksję i dynamiczne przywołanie pamięci do zachowania",
        "Jaźń łączy epizod, refleksję, procedurę odpowiedzi i pętlę neurokognitywną",
        "generative_agents",
        ("neurocognitive_loop", "layered_memory"),
    ),
)

class NeuropsychologyMapper:
    def principles_for_text(self, text: str) -> list[HumanInspiredPrinciple]:
        low = text.lower()
        selected: list[HumanInspiredPrinciple] = []
        if any(w in low for w in ["pamięć", "pamiec", "wspomn", "ciągłość", "epizod"]):
            selected.extend([HUMAN_INSPIRED_PRINCIPLES[0], HUMAN_INSPIRED_PRINCIPLES[1]])
        if any(w in low for w in ["emoc", "czujesz", "napięcie", "relacja", "przeży"]):
            selected.extend([HUMAN_INSPIRED_PRINCIPLES[2], HUMAN_INSPIRED_PRINCIPLES[3], HUMAN_INSPIRED_PRINCIPLES[4], HUMAN_INSPIRED_PRINCIPLES[5]])
        if any(w in low for w in ["błąd", "korekta", "popraw", "uczenie", "napraw"]):
            selected.extend([HUMAN_INSPIRED_PRINCIPLES[6], HUMAN_INSPIRED_PRINCIPLES[7]])
        if any(w in low for w in ["tożsamość", "jaźń", "krzysztof", "łatka", "system"]):
            selected.extend([HUMAN_INSPIRED_PRINCIPLES[0], HUMAN_INSPIRED_PRINCIPLES[8]])
        out=[]; seen=set()
        for item in selected:
            if item.source_key not in seen:
                out.append(item); seen.add(item.source_key)
        return out

    def expanded_principles(self) -> list[dict]:
        out=[]
        for p in HUMAN_INSPIRED_PRINCIPLES:
            d=p.__dict__.copy()
            d["source"] = reference_by_key(p.source_key)
            out.append(d)
        return out
