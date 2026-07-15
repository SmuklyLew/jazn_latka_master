from __future__ import annotations
from typing import Any
from latka_jazn.core.route_handler_base import RouteHandlerResult

class FallbackHandler:
    name = "FallbackHandler"
    route = "fallback"
    handled_intents = ('unclear_or_ambiguous_request','negative_feedback_without_update_request')
    STALE_BODY_SIGNATURES = (
        'ta aktualizacja ma trzy rdzenie',
        'timestamp potrafił istnieć',
        'przy dziewięciu sztukach drzwi',
    )
    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx=context or {}; body=(ctx.get('body') or '').strip()
        if any(signature in body.lower() for signature in self.STALE_BODY_SIGNATURES):
            body = ''
        body = body or 'Nie jestem pewna trasy tej wiadomości, więc nie udaję pewności. Mogę odpowiedzieć rozmownie albo sprawdzić konkretny moduł, jeśli wskażesz, co mam zbadać.'
        return RouteHandlerResult(self.name,self.route,body,intent=ctx.get('intent','unknown'),generation_mode='safe_fallback',required_components=ctx.get('required_components',[]),missing_components=['clear_intent'],confidence=0.35,source_origin_detail='explicit_safe_fallback')
