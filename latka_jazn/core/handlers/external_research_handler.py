from __future__ import annotations
from typing import Any
from datetime import datetime, timezone
from latka_jazn.core.route_handler_base import RouteHandlerResult

class ExternalResearchHandler:
    name = "ExternalResearchHandler"
    route = "external_research"
    handled_intents = ('external_research_request',)
    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx=context or {}; cfg=ctx.get('config')
        result={
            'schema_version':'external_research_result/v14.6.10',
            'query': text,
            'executed': False,
            'provider': None,
            'sources': [],
            'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
            'cache_status': 'not_checked',
            'status': 'requires_external_web_execution',
            'reason': 'local_runtime_has_no_general_web_search_provider',
            'truth_boundary': 'Runtime lokalny nie wykonał ogólnego wyszukiwania internetu. Warstwa ChatGPT może użyć web.run i musi oznaczyć te źródła oddzielnie.',
            'errors': [],
            'network_policy': {'research_allow_network': getattr(cfg,'research_allow_network',None), 'requires_chatgpt_web': getattr(cfg,'research_requires_chatgpt_web_when_local_provider_missing',None)},
        }
        body='To wymaga aktualnego sprawdzenia w internecie. Mój lokalny runtime nie ma jeszcze własnego ogólnego providera web-search, więc zwracam jawny status: requires_external_web_execution. Warstwa ChatGPT powinna teraz użyć web.run i oddzielić źródła web od odpowiedzi runtime.'
        return RouteHandlerResult(self.name,self.route,body,intent=ctx.get('intent','external_research_request'),data={'external_research_result':result},sources=[],required_components=ctx.get('required_components',[]),satisfied_components=['truth_boundary','research_status'],missing_components=['local_general_web_search_provider'],confidence=0.65,source_origin_detail='requires_chatgpt_web_execution',truth_boundary=result['truth_boundary'])
