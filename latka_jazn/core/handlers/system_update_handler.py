from __future__ import annotations
from typing import Any
from latka_jazn.core.route_handler_base import RouteHandlerResult

class SystemUpdateHandler:
    name = "SystemUpdateHandler"
    route = "system_update"
    handled_intents = (
        'system_update_execution_request',
        'system_update_manifest_request',
        'update_manifest_request',
        'v14_6_10_behavioral_runtime_dialogue_intent_source_integrity_update',
    )
    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        route = (ctx.get('route_entry') or {}).get('route') or self.route
        body = ctx.get('body') or 'Zadanie aktualizacji systemu wymaga pracy na aktywnej paczce, pełnej zawartości plików, testów i eksportu.'
        data = {'target_version': ctx.get('runtime_version'), 'required_components': ctx.get('required_components', [])}
        return RouteHandlerResult(
            self.name,
            route,
            body,
            intent=ctx.get('intent','system_update_execution_request'),
            data=data,
            required_components=ctx.get('required_components',[]),
            satisfied_components=['update_intent_detected','no_summary_contract'],
            confidence=0.76,
            source_origin_detail='system_update_handler',
            truth_boundary='Handler nie tworzy paczki sam bez narzędzi plikowych; pilnuje kontraktu pełnych plików i testów.',
        )
