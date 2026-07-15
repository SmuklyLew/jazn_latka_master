from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any
SCHEMA_VERSION="polish_morphology_frame/v14.6.10"
@dataclass(slots=True)
class PolishMorphologyFrame:
    token: str; lemma_candidates: list[str]=field(default_factory=list); pos_candidates: list[str]=field(default_factory=list); provider: str='jazn_mini_heuristic'; confidence: float=0.35; schema_version: str=SCHEMA_VERSION
    def to_dict(self)->dict[str,Any]: return asdict(self)
class PolishMorphologyAnalyzer:
    def analyse_token(self, token:str)->PolishMorphologyFrame:
        t=(token or '').strip(); low=t.lower(); lemmas=[low]
        for suf in ('ami','ach','ego','emu','ie','ą','ę','y','a','u'):
            if len(low)>5 and low.endswith(suf): lemmas.append(low[:-len(suf)]); break
        return PolishMorphologyFrame(t, list(dict.fromkeys(lemmas)), [], 'jazn_mini_heuristic_no_full_polish_morphology', 0.35)
