from __future__ import annotations
from typing import Any
from latka_jazn.core.route_handler_base import RouteHandlerResult

class MemoryAuditHandler:
    name = "MemoryAuditHandler"
    route = "memory_audit"
    handled_intents = ('memory_audit_request', 'memory_recall_request')
    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx=context or {}; memory_context=ctx.get('memory_context') if isinstance(ctx.get('memory_context'), dict) else {}
        hits=memory_context.get('source_file_hits') or memory_context.get('hits') or []
        counts=memory_context.get('counts') or {}
        if hits:
            sample='; '.join([str((h.get('path') or h.get('source') or h))[:120] for h in hits[:5] if isinstance(h,dict)])
            body='Znalazłam konkretne ślady pamięci/plików, nie tylko liczby: '+sample
        else:
            body='Nie mam w tej turze pewnych trafień treści pamięci. Status liczników: '+str(counts)[:1200]
        return RouteHandlerResult(self.name,self.route,body,intent=ctx.get('intent','memory_audit_request'),data={'memory_context':memory_context},memory_sources=hits if isinstance(hits,list) else [],required_components=ctx.get('required_components',[]),satisfied_components=['memory_context_checked'],confidence=0.65,source_origin_detail='memory_search_planner_or_context',truth_boundary='Pamięć jest cytowana tylko z trafień przekazanych do handlera; brak trafień nie oznacza braku wspomnienia.')
