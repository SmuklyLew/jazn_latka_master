from __future__ import annotations
from datetime import datetime, timezone
from latka_jazn.nlp.providers.base import ProviderLookupResult
from latka_jazn.nlp.providers.http_client import SafeHttpClient

class LanguageToolHttpProvider:
    name='languagetool_optional'
    def __init__(self, *, endpoint: str = 'http://localhost:8081/v2/check', allow_network: bool = False, user_agent: str = 'LatkaJazn/14.6.10', timeout_seconds: float = 3.0):
        self.endpoint=endpoint; self.allow_network=allow_network; self.http=SafeHttpClient(user_agent=user_agent, timeout_seconds=timeout_seconds, max_retries=0)
    def lookup(self, term: str, language: str='pl') -> ProviderLookupResult:
        if not self.allow_network:
            return ProviderLookupResult(self.name,'network_disabled',term,language,error='LanguageTool HTTP disabled by config.',retrieved_at_utc=datetime.now(timezone.utc).isoformat())
        res=self.http.get_json(self.endpoint, {'language':language, 'text':term})
        if not res.ok or not isinstance(res.json_data, dict):
            return ProviderLookupResult(self.name,'provider_unavailable',term,language,error=res.error,source_url=self.endpoint,retrieved_at_utc=res.retrieved_at_utc,elapsed_ms=res.elapsed_ms)
        suggestions=[]
        for m in res.json_data.get('matches', [])[:5]:
            for repl in m.get('replacements', [])[:3]: suggestions.append(repl.get('value'))
        return ProviderLookupResult(self.name,'ok',term,language,spelling_suggestions=[x for x in suggestions if x],source_url=self.endpoint,retrieved_at_utc=res.retrieved_at_utc,elapsed_ms=res.elapsed_ms,confidence=0.55,raw={'match_count':len(res.json_data.get('matches', []))})
