from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any
SCHEMA_VERSION="language_resource_registry/v14.8.0"
@dataclass(slots=True)
class LanguageResource:
    name: str; kind: str; languages: list[str]; online: bool; local_optional: bool; url: str|None; license_note: str; use_policy: str
    def to_dict(self)->dict[str,Any]: return asdict(self)
class LanguageResourceRegistry:
    def resources(self)->list[LanguageResource]:
        return [
            LanguageResource('local_jazn_mini_lexicon','mini_domain_lexicon',['pl'],False,False,None,'własny mały zasób runtime','always_available'),
            LanguageResource('Morfeusz2','morphological_analyzer',['pl'],False,True,'https://morfeusz.sgjp.pl/','sprawdź licencję instalacji lokalnej','optional_local_provider'),
            LanguageResource('Stanza','nlp_pipeline',['multi','pl'],False,True,'https://stanfordnlp.github.io/stanza/','modele i paczki według licencji Stanza/UD','optional_heavy_provider'),
            LanguageResource('plWordNet/Słowosieć','lexico_semantic_network',['pl'],True,True,'https://clarin-pl.eu/','zapisuj wersję/licencję zasobu','semantic_relations_if_available'),
            LanguageResource('WordNet/OMW','wordnet',['multi','en'],True,True,'https://omwn.org/','różne wordnety mają różne licencje','semantic_relations_if_available'),
            LanguageResource('Wiktionary','dictionary',['multi'],True,False,'https://www.wiktionary.org/','CC BY-SA/GFDL; API nie jest pełnym standardowym dictionary API','manual_or_dump_with_cache'),
            LanguageResource('SJP.PL','dictionary',['pl'],True,False,'https://sjp.pl/','serwis deklaruje otwarte licencje zależnie od wersji; runtime domyślnie zwraca link referencyjny i nie kopiuje definicji','reference_link_provider_no_mass_scrape'),
            LanguageResource('WSJP PAN','dictionary',['pl'],True,False,'https://wsjp.pl/','słownik IJP PAN; runtime domyślnie zwraca link referencyjny i nie kopiuje definicji','reference_link_provider_no_mass_scrape'),
            LanguageResource('LanguageTool','grammar_style',['multi','pl'],True,True,'https://languagetool.org/','sprawdź licencję/endpoint','optional_style_provider'),
        ]
    def to_dict(self)->dict[str,Any]:
        data=[r.to_dict() for r in self.resources()]
        return {'schema_version':SCHEMA_VERSION,'resources':data,'resource_count':len(data),'truth_boundary':'Rejestr wskazuje możliwe zasoby; runtime nie twierdzi, że pobrał wynik online bez realnego lookupu. SJP/WSJP są w v14.8.0 bezpiecznymi referencjami linkowymi, nie scraperami definicji.'}
