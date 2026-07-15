from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import quote
from latka_jazn.nlp.providers.base import ProviderLookupResult

class WSJPReferenceProvider:
    name='wsjp_reference'
    def lookup(self, term: str, language: str='pl') -> ProviderLookupResult:
        if language != 'pl':
            return ProviderLookupResult(self.name,'language_not_supported',term,language,error='WSJP is a Polish reference dictionary.')
        url='https://wsjp.pl/szukaj/podstawowe/wyniki?szukaj=' + quote((term or '').strip())
        return ProviderLookupResult(self.name,'manual_reference_available',term,language,source_url=url,license_hint='WSJP PAN: provider returns reference link only; no mass scraping or copied definitions.',retrieved_at_utc=datetime.now(timezone.utc).isoformat(),confidence=0.30,truth_boundary='To jest link referencyjny, nie pobrana definicja.')
