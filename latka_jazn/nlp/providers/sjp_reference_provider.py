from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import quote
from latka_jazn.nlp.providers.base import ProviderLookupResult

class SJPReferenceProvider:
    """Bezpieczny provider referencyjny SJP.PL dla Jaźni.

    Nie wykonuje masowego scrapingu i nie kopiuje definicji z serwisu.
    Zwraca jawny link do hasła oraz status źródła, żeby runtime mógł pokazać,
    że SJP jest dostępne jako źródło referencyjne, ale nie udawać pobranego API.
    """
    name = 'sjp_reference'
    license_hint = 'SJP.PL: słownik online; przed pobieraniem danych lub masowym użyciem sprawdź aktualne warunki/licencję. Ten provider zwraca link referencyjny, nie kopiuje definicji.'
    def lookup(self, term: str, language: str = 'pl') -> ProviderLookupResult:
        normalized = (term or '').strip()
        if language != 'pl':
            return ProviderLookupResult(self.name,'language_not_supported',normalized,language,error='SJP.PL is used here as a Polish dictionary reference.',retrieved_at_utc=datetime.now(timezone.utc).isoformat())
        url = 'https://sjp.pl/' + quote(normalized.replace(' ', '+'))
        return ProviderLookupResult(
            self.name,
            'manual_reference_available',
            normalized,
            language,
            source_url=url,
            license_hint=self.license_hint,
            retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
            confidence=0.30,
            truth_boundary='To jest link referencyjny do SJP.PL. Provider nie pobiera definicji i nie potwierdza, że hasło istnieje bez osobnego lookupu online.'
        )
