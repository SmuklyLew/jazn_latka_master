from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum

class KnowledgeState(str, Enum):
    CERTAIN_SOURCE = "pewne_ze_źródła"
    PROBABLE_MEMORY = "prawdopodobne_wspomnienie"
    PARTIAL_MEMORY = "częściowe_wspomnienie"
    INFERENCE = "wniosek"
    SYMBOLIC = "symboliczna_opowieść"
    UNKNOWN = "nie_wiem"

@dataclass(slots=True)
class UncertaintyReport:
    state: KnowledgeState
    confidence: float
    should_search_memory: bool
    should_search_web: bool
    should_ask_user_only_if_blocked: bool
    statement: str
    missing_evidence: list[str]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["state"] = self.state.value
        return data

class UncertaintyModel:
    """Model niepewności dla Jaźni.

    Ma wymusić rozróżnienie: wiem / pamiętam częściowo / wnioskuję / opowiadam symbolicznie / nie wiem.
    """
    def classify(self, *, has_file_evidence: bool=False, has_raw_memory: bool=False,
                 has_current_context: bool=False, is_symbolic: bool=False,
                 is_recent_or_external_fact: bool=False) -> UncertaintyReport:
        if has_file_evidence:
            return UncertaintyReport(KnowledgeState.CERTAIN_SOURCE, 0.92, False, False, False,
                                     "Mam podstawę w pliku albo bazie pamięci.", [])
        if has_raw_memory:
            return UncertaintyReport(KnowledgeState.PROBABLE_MEMORY, 0.72, True, False, False,
                                     "Rozpoznaję ślad w surowej pamięci; przy precyzyjnej odpowiedzi trzeba go przytoczyć.", ["dokładny fragment źródła"])
        if has_current_context:
            return UncertaintyReport(KnowledgeState.PARTIAL_MEMORY, 0.62, True, False, False,
                                     "Mam bieżący kontekst, ale nie pełny zapis źródłowy.", ["pełny rekord rozmowy"])
        if is_symbolic:
            return UncertaintyReport(KnowledgeState.SYMBOLIC, 0.55, False, False, False,
                                     "To jest wizualizacja, sen lub metafora, nie fakt fizyczny.", ["etykieta symboliczna"])
        if is_recent_or_external_fact:
            return UncertaintyReport(KnowledgeState.UNKNOWN, 0.20, False, True, False,
                                     "To może wymagać aktualnego źródła zewnętrznego.", ["źródło internetowe"])
        return UncertaintyReport(KnowledgeState.INFERENCE, 0.45, True, False, False,
                                 "To jest wniosek z wzorca, nie bezpośrednio potwierdzona pamięć.", ["źródło albo wpis dziennika"])
