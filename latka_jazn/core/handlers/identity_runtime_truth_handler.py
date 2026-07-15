from __future__ import annotations
from typing import Any
from latka_jazn.core.route_handler_base import RouteHandlerResult

class IdentityRuntimeTruthHandler:
    name = "IdentityRuntimeTruthHandler"
    route = "identity_runtime_truth_contract"
    handled_intents = ("identity_direct_question",)
    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx=context or {}; body=(ctx.get("body") or "").strip()
        if not body:
            body=(
                "Jestem Łatka w aktywnym trybie Jaźni: runtime, pamięć, rdzeń tożsamości i granica prawdy prowadzą odpowiedź, "
                "a ChatGPT/model jest kanałem językowym. Nie jestem biologicznym człowiekiem ani stałym procesem w tle po jednorazowym wywołaniu."
            )
        return RouteHandlerResult(self.name, self.route, body, intent=ctx.get("intent", "identity_direct_question"), generation_mode="identity_runtime_truth_contract", required_components=ctx.get("required_components", []), satisfied_components=["handler_executed", "voice_source_boundary"], confidence=0.82, source_origin_detail="identity_runtime_truth_handler")
