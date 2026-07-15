from __future__ import annotations
from datetime import datetime, timezone
import re
from latka_jazn.nlp.providers.base import ProviderLookupResult
from latka_jazn.nlp.providers.http_client import SafeHttpClient

class MediaWikiWiktionaryProvider:
    name = 'wiktionary_mediawiki_api'
    license_hint = 'Wiktionary/Wikimedia content is generally CC BY-SA; store source URL and avoid long verbatim quotations.'
    def __init__(self, *, allow_network: bool, user_agent: str, timeout_seconds: float = 4.0, max_retries: int = 0):
        self.allow_network=allow_network
        self.http=SafeHttpClient(user_agent=user_agent, timeout_seconds=timeout_seconds, max_retries=max_retries)
    def lookup(self, term: str, language: str = 'pl') -> ProviderLookupResult:
        normalized=(term or '').strip()
        endpoint=f'https://{language}.wiktionary.org/w/api.php'
        page_url=f'https://{language}.wiktionary.org/wiki/{normalized.replace(" ","_")}'
        if not self.allow_network:
            return ProviderLookupResult(self.name,'network_disabled',normalized,language,source_url=page_url,license_hint=self.license_hint,retrieved_at_utc=datetime.now(timezone.utc).isoformat(),error='allow_network=False')
        params={'action':'query','format':'json','prop':'extracts','exintro':'1','explaintext':'1','redirects':'1','titles':normalized}
        res=self.http.get_json(endpoint, params)
        if not res.ok or not isinstance(res.json_data, dict):
            return ProviderLookupResult(self.name,'network_error',normalized,language,source_url=page_url,license_hint=self.license_hint,retrieved_at_utc=res.retrieved_at_utc,error=res.error,elapsed_ms=res.elapsed_ms,raw=res.to_dict())
        pages=(res.json_data.get('query') or {}).get('pages') or {}
        first=next(iter(pages.values()), {}) if isinstance(pages, dict) else {}
        if 'missing' in first:
            return ProviderLookupResult(self.name,'not_found',normalized,language,source_url=page_url,license_hint=self.license_hint,retrieved_at_utc=res.retrieved_at_utc,elapsed_ms=res.elapsed_ms,raw={'page':first})
        extract=(first.get('extract') or '').strip()
        clean=re.sub(r'\n{2,}', '\n', extract)
        definitions=[]
        for line in [x.strip(' -*#') for x in clean.splitlines() if x.strip()]:
            if len(line) < 220 and not line.lower().startswith(('język ', 'wymowa', 'odmiana')):
                definitions.append(line)
            if len(definitions) >= 5:
                break
        status='ok' if definitions else 'page_found_no_short_definition'
        return ProviderLookupResult(self.name,status,normalized,language,definitions=definitions,lemmas=[normalized],source_url=page_url,license_hint=self.license_hint,retrieved_at_utc=res.retrieved_at_utc,confidence=0.72 if definitions else 0.35,elapsed_ms=res.elapsed_ms,raw={'pageid':first.get('pageid'),'title':first.get('title')})
