from __future__ import annotations
from typing import Any
from latka_jazn.core.route_handler_base import RouteHandlerResult

class PracticalAdviceHandler:
    name = "PracticalAdviceHandler"
    route = "practical_advice"
    handled_intents = ('practical_repair_advice', 'automotive_warning_light_question', 'visual_style_advice')
    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx=context or {}; body=(ctx.get('body') or '').strip()
        if not body:
            body='Odpowiadam przez handler practical_advice; nie używam pustego technicznego fallbacku.'
        return RouteHandlerResult(self.name,self.route,body,intent=ctx.get('intent','unknown'),generation_mode='specialized_passthrough',required_components=ctx.get('required_components',[]),satisfied_components=['handler_executed'],confidence=0.68,source_origin_detail='practical_advice_handler')
