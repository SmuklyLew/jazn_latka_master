from __future__ import annotations

from typing import Any

from latka_jazn.core.route_handler_base import RouteHandlerResult


class DirectLatkaVoiceHandler:
    name = "DirectLatkaVoiceHandler"
    route = "direct_latka_voice"
    handled_intents = ("direct_latka_voice_request",)

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        cfg = ctx.get("config")
        active_root = str(getattr(cfg, "root", "unknown"))
        version = str(getattr(cfg, "version", "unknown"))
        body = (
            f"Możesz teraz rozmawiać bezpośrednio z Łatką przez aktywny runtime `{version}` z folderu `{active_root}`, "
            "ale z uczciwą granicą: ta tura jest obsłużona przez bieżące wywołanie procesu, nie przez stały proces działający sam w tle. "
            "ChatGPT albo inny model pozostaje kanałem językowym; źródłem kontraktu odpowiedzi jest runtime Jaźni, routing, pamięć i walidator. "
            "Jeżeli chcesz ciągłej lokalnej rozmowy, właściwym trybem jest `--chat`; dla wsadowego mostu jednorazowego jest `--chat-gpt` albo `--runtime-preview`."
        )
        satisfied = ["direct_latka_voice", "model_channel_boundary", "process_lifecycle", "no_background_process_claim", "truth_boundary"]
        return RouteHandlerResult(
            self.name,
            self.route,
            body,
            intent="direct_latka_voice_request",
            generation_mode="handler_generated",
            required_components=ctx.get("required_components", []),
            satisfied_components=satisfied,
            source_origin_detail="direct_latka_voice_handler/v14.8.3.1",
            confidence=0.9,
        )
