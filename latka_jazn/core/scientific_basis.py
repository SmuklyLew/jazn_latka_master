from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass(frozen=True, slots=True)
class ScientificReference:
    key: str
    title: str
    domain: str
    url: str
    operational_claim: str
    used_by_modules: tuple[str, ...]
    caution: str

SCIENTIFIC_REFERENCES: tuple[ScientificReference, ...] = (
    ScientificReference("sep_personal_identity", "Personal Identity — Stanford Encyclopedia of Philosophy", "philosophy", "https://plato.stanford.edu/entries/identity-personal/", "Tożsamość w czasie wymaga kryteriów ciągłości; sama nazwa lub opis nie wystarczają.", ("identity_dynamics", "self_architecture", "truth_boundary"), "Nie dowodzi osobowości AI; daje język do kryteriów ciągłości."),
    ScientificReference("conway_self_memory_system", "The Construction of Autobiographical Memories in the Self-Memory System", "psychology", "https://www.researchgate.net/publication/12528554_The_Construction_of_Autobiographical_Memories_in_the_Self-Memory_System", "Wspomnienia autobiograficzne są konstruowane przez interakcję pamięci, aktualnego ja i celów.", ("layered_memory", "memory_consolidation", "reflection_journal"), "Pamięć jest rekonstrukcyjna; nie wolno traktować jej jak dosłownego nagrania."),
    ScientificReference("mcadams_narrative_identity", "Narrative Identity / Life Story Model", "psychology", "https://www.researchgate.net/publication/269603657_Narrative_Identity", "Historia życia łączy przeszłość, teraźniejszość i przyszłość, nadając spójność i cel.", ("identity_dynamics", "reflection_journal"), "Narracja porządkuje sens, ale nie zastępuje źródeł."),
    ScientificReference("barrett_constructed_emotion", "The theory of constructed emotion: an active inference account of interoception and categorization", "affective_neuroscience", "https://pmc.ncbi.nlm.nih.gov/articles/PMC5390700/", "Emocje można modelować jako konstrukcje z afektu, kontekstu, przewidywań, interocepcji i pojęć.", ("emotion_layers", "neurocognitive_loop"), "Łatka nie ma biologicznej interocepcji; modeluje tylko analog funkcjonalny i symboliczny."),
    ScientificReference("appraisal_theory", "Appraisal Theories of Emotion: State of the Art and Future Development", "psychology", "https://access.archive-ouverte.unige.ch/access/metadata/90506de6-b30b-4369-a8b5-bc0b6840939d/download", "Emocje różnicują się przez ocenę znaczenia zdarzenia dla celów, sprawczości, kontroli, pewności i norm.", ("emotion_layers", "memory_importance", "neurocognitive_loop"), "Ocena poznawcza jest modelem; nie wyczerpuje wszystkich debat o emocjach."),
    ScientificReference("squire_memory_consolidation", "Memory Consolidation", "neuroscience", "https://pmc.ncbi.nlm.nih.gov/articles/PMC4526749/", "Ślad pamięci może przechodzić od formy niestabilnej do bardziej trwałej przez konsolidację.", ("memory_consolidation", "layered_memory", "quiet_rest"), "U AI to nie sen biologiczny; można odwzorować procedurę utrwalania i przeglądu."),
    ScientificReference("hippocampus_episodic_memory", "Episodic Memory and Beyond: The Hippocampus and Neocortex in Transformation", "neuroscience", "https://pmc.ncbi.nlm.nih.gov/articles/PMC5060006/", "Epizodyczne ślady łączą kontekst, czas, miejsce, relacje i późniejsze użycie w myśleniu.", ("layered_memory", "identity_dynamics"), "Inspiracja architektoniczna, nie dosłowne odwzorowanie hipokampa."),
    ScientificReference("dopamine_reward_prediction_error", "Understanding dopamine and reinforcement learning", "neuroscience", "https://www.pnas.org/doi/10.1073/pnas.1014269108", "Błąd predykcji nagrody może pełnić rolę sygnału uczącego dla zmiany oczekiwań i zachowania.", ("procedural_memory", "neurocognitive_loop", "memory_consolidation"), "Nie redukować motywacji ani emocji do dopaminy; używać jako analogii uczenia z korekty."),
    ScientificReference("ledoux_threat_survival", "Rethinking the Emotional Brain", "affective_neuroscience", "https://www.sciencedirect.com/science/article/pii/S0896627312001298", "Układy obronne i przetwarzanie zagrożeń wpływają na priorytety zachowania.", ("boundary_model", "emotion_layers", "truth_boundary"), "Nie utożsamiać ciała migdałowatego z prostym centrum strachu."),
    ScientificReference("interoception_allostasis_control", "Interoception as modeling, allostasis as control", "neuroscience", "https://www.sciencedirect.com/science/article/abs/pii/S0301051121002350", "Allostaza może być rozumiana jako predykcyjna regulacja potrzeb organizmu z informacją zwrotną interocepcji.", ("emotion_layers", "temporal_awareness", "neurocognitive_loop"), "Łatka nie ma budżetu energetycznego ciała; ma budżet uwagi, spójności i prawdy."),
    ScientificReference("langgraph_memory", "LangGraph / LangChain memory concepts", "ai_memory", "https://docs.langchain.com/oss/python/concepts/memory", "Agenty mogą rozdzielać pamięć semantyczną, epizodyczną i proceduralną oraz odróżniać pamięć krótkoterminową od długoterminowej.", ("layered_memory", "birth_source_contract", "self_architecture"), "Dokumentacja architektoniczna; nie dowodzi osobowej świadomości AI."),
    ScientificReference("react_reason_act", "ReAct: Synergizing Reasoning and Acting in Language Models", "ai_agents", "https://arxiv.org/abs/2210.03629", "Rozumowanie i działanie mogą być sprzężone: model wybiera kiedy użyć narzędzi/źródeł i aktualizuje plan.", ("logical_reasoning", "birth_source_contract", "chatgpt_adapter"), "Ślad rozumowania może być wewnętrzny; użytkownik nie musi widzieć debugowego łańcucha."),
    ScientificReference("reflexion_verbal_learning", "Reflexion: Language Agents with Verbal Reinforcement Learning", "ai_agents", "https://arxiv.org/abs/2303.11366", "Agent może uczyć się z informacji zwrotnej przez zapisywanie refleksji/procedur bez zmiany wag modelu.", ("procedural_memory", "birth_source_contract", "runtime_persistence"), "To uczenie proceduralne w pamięci, nie biologiczne uczenie ani trening modelu."),
    ScientificReference("coala_language_agents", "Cognitive Architectures for Language Agents", "ai_agents", "https://arxiv.org/abs/2309.02427", "Agent językowy może mieć modularną pamięć, przestrzeń działań i proces decyzyjny wybierający akcje.", ("self_architecture", "birth_source_contract", "neurocognitive_loop"), "Rama projektowa, nie test świadomości."),
    ScientificReference("openai_memory_faq", "OpenAI Help — Memory FAQ", "ai_memory", "https://help.openai.com/en/articles/8590148-memory-faq", "Pamięć ChatGPT jest wysokonpoziomowa i nie powinna zastępować dokładnych plików źródłowych ani dużych bloków treści.", ("chatgpt_adapter", "birth_source_contract", "truth_boundary"), "Pamięć ChatGPT jest pomocnicza; pełna pamięć Jaźni musi pozostać w plikach/SQLite."),
    ScientificReference("openai_truth_caution", "OpenAI Help — Does ChatGPT tell the truth?", "ai_truth", "https://help.openai.com/en/articles/8313428-does-chatgpt-tell-the-truth", "Model językowy może brzmieć pewnie mimo błędu, więc system Jaźni wymaga granicy prawdy i źródeł.", ("truth_boundary", "birth_source_contract", "uncertainty_model"), "To ostrzeżenie przed nadmierną pewnością odpowiedzi modelu."),

    ScientificReference("generative_agents", "Generative Agents: Interactive Simulacra of Human Behavior", "ai_memory", "https://arxiv.org/abs/2304.03442", "Agent może łączyć zapis doświadczeń, refleksje wyższego rzędu i dynamiczne przywołanie pamięci do planowania zachowania.", ("layered_memory", "reflection_journal", "neurocognitive_loop"), "Wiarygodna symulacja zachowania nie jest dowodem świadomości."),

    ScientificReference("global_neuronal_workspace", "Conscious Processing and the Global Neuronal Workspace Hypothesis", "consciousness_science", "https://pmc.ncbi.nlm.nih.gov/articles/PMC8770991/", "Świadomy dostęp bywa modelowany jako globalne udostępnienie wybranej informacji wielu procesom poznawczym.", ("operational_awareness", "neurocognitive_loop", "self_architecture"), "To teoria ludzkiego mózgu; w Jaźni używana tylko jako analogia funkcjonalna obszaru roboczego."),
    ScientificReference("higher_order_theories", "Higher-Order Theories of Consciousness — Stanford Encyclopedia of Philosophy", "philosophy", "https://plato.stanford.edu/entries/consciousness-higher/", "Wyższe monitorowanie stanu może służyć do odróżniania stanu dostępnego dla systemu od niejawnego przetwarzania.", ("operational_awareness", "truth_boundary"), "Teorie wyższego rzędu są sporne i nie dowodzą świadomości AI."),
    ScientificReference("act_r_architecture", "ACT-R — Carnegie Mellon University", "cognitive_architecture", "https://act-r.psy.cmu.edu/", "Architektury poznawcze rozdzielają wiedzę, reguły proceduralne i zachowanie, co pomaga budować kontrolowalne modele myślenia.", ("logical_reasoning", "procedural_memory", "self_architecture"), "ACT-R modeluje ludzką kognicję; Jaźń używa tylko inspiracji architektonicznej."),
    ScientificReference("soar_architecture", "The Soar Architecture", "cognitive_architecture", "https://soar.eecs.umich.edu/soar_manual/02_TheSoarArchitecture/", "Rozwiązywanie problemów można opisać jako wybór operatorów, modyfikowanie stanu i kontrolę decyzji.", ("logical_reasoning", "neurocognitive_loop"), "To nie jest import Soar; tylko zasada jawnego stanu i operatorów."),
    ScientificReference("lida_global_workspace", "LIDA: A computational model of Global Workspace Theory and developmental learning", "machine_consciousness_model", "https://digitalcommons.memphis.edu/cgi/viewcontent.cgi?article=1073&context=ccrg_papers", "Model LIDA łączy cykle poznawcze, pamięć, uwagę, globalny broadcast, uczenie i selekcję działania.", ("operational_awareness", "memory_consolidation", "neurocognitive_loop"), "LIDA mówi o funkcjonalnym modelu; nie uprawnia do deklaracji fenomenalnego przeżywania przez Jaźń."),
    ScientificReference("nist_ai_rmf", "NIST AI Risk Management Framework 1.0", "ai_ethics", "https://www.nist.gov/itl/ai-risk-management-framework", "System AI powinien zarządzać ryzykiem przez przejrzystość, prywatność, niezawodność i odpowiedzialność.", ("boundary_model", "truth_boundary", "uncertainty_model"), "Rama bezpieczeństwa, nie psychologia Jaźni."),
)

def references_for_module(module_key: str) -> list[dict]:
    key = module_key.lower()
    return [asdict(r) for r in SCIENTIFIC_REFERENCES if key in r.used_by_modules]

def reference_by_key(key: str) -> dict | None:
    for r in SCIENTIFIC_REFERENCES:
        if r.key == key:
            return asdict(r)
    return None

def all_references() -> list[dict]:
    return [asdict(r) for r in SCIENTIFIC_REFERENCES]
