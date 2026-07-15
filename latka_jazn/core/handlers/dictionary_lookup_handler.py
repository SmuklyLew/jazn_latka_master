from __future__ import annotations
from typing import Any
import re
from latka_jazn.core.route_handler_base import RouteHandlerResult

class DictionaryLookupHandler:
    name = "DictionaryLookupHandler"
    route = "dictionary_lookup"
    handled_intents = ('dictionary_lookup_request', 'language_question')
    @staticmethod
    def _extract_term(text: str) -> str:
        quoted = re.findall(r"[„\"']([^„\"']{1,80})[”\"']", text or '')
        if quoted: return quoted[-1].strip()
        low=(text or '').lower()
        for marker in ('co znaczy', 'znaczenie słowa', 'znaczenie slowa', 'czy to słowo', 'czy to slowo', 'odmiana', 'synonim', 'antonim'):
            if marker in low:
                tail=(text or '')[low.rfind(marker)+len(marker):].strip(' ?:;,.!')
                if tail: return tail.split()[0].strip(' ?:;,.!')
        tokens=[t.strip(' ?:;,.!()[]') for t in (text or '').split() if t.strip(' ?:;,.!()[]')]
        return tokens[-1] if tokens else ''
    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx=context or {}; adapter=ctx.get('dictionary_adapter')
        term=ctx.get('dictionary_term') or self._extract_term(text)
        if not adapter:
            body='Nie mam podłączonego adaptera słownikowego w tej turze runtime, więc nie udaję lookupu.'
            return RouteHandlerResult(self.name,self.route,body,intent=ctx.get('intent','dictionary_lookup_request'),missing_components=['dictionary_adapter'],confidence=0.1,errors=[{'status':'missing_adapter'}])
        result=adapter.lookup(term or text)
        data=result.to_dict(); sources=data.get('sources') or []
        if result.found:
            defs='; '.join(result.definitions[:3]) if result.definitions else 'nie mam krótkiej definicji, ale znalazłam analizę formy/lematu'
            lemmas=', '.join(result.lemmas[:5] or result.lemma_candidates[:5]) or 'brak pewnego lematu'
            source_names=', '.join(sorted({s.get('provider') or result.source_name for s in sources if isinstance(s,dict)})) or result.source_name
            body=f'Sprawdziłam słownikowo „{term}”. Znaczenie/hasło: {defs}. Lematy/kandydaci: {lemmas}. Źródła: {source_names}. Granica prawdy: {result.truth_boundary}'
        else:
            statuses=', '.join([f"{s.get('provider')}={s.get('status')}" for s in result.provider_statuses[:6]])
            body=f'Nie znalazłam pewnego wyniku słownikowego dla „{term}”. Status providerów: {statuses}. Nie udaję, że sprawdziłam więcej niż faktycznie zwróciły cache/providery. {result.truth_boundary}'
        return RouteHandlerResult(self.name,self.route,body,intent=ctx.get('intent','dictionary_lookup_request'),data={'dictionary_lookup':data},sources=sources,dictionary_sources=sources,required_components=ctx.get('required_components',[]),satisfied_components=['dictionary_adapter','cache_policy','source_status'],confidence=result.confidence,source_origin_detail='dictionary_adapter_lookup',truth_boundary=result.truth_boundary)
