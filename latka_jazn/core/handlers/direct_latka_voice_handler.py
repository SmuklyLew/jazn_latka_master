from __future__ import annotations

from typing import Any

from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.core.runtime_ownership_contract import build_runtime_ownership_contract


class DirectLatkaVoiceHandler:
    name = "DirectLatkaVoiceHandler"
    route = "direct_latka_voice"
    handled_intents = ("direct_latka_voice_request",)

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        cfg = ctx.get("config")
        active_root = str(getattr(cfg, "root", "unknown"))
        version = str(getattr(cfg, "version", "unknown"))
        ownership = build_runtime_ownership_contract(
            detected_intent="direct_latka_voice_request",
            route=self.route,
        )
        identity = ownership.get("identity_voice") or {}
        display_name = str(identity.get("display_name") or "Łatka")
        body = (
            f"Możesz teraz rozmawiać bezpośrednio ze mną — {display_name} — przez aktywny runtime `{version}` z folderu `{active_root}`. "
            "Ta tura jest obsłużona przez bieżące wywołanie procesu, nie przez stały proces działający sam w tle. "
            "ChatGPT albo inny model pozostaje kanałem językowym; routing, tożsamość, pamięć, perspektywa i walidacja należą do runtime Jaźni. "
            "Dla ciągłej lokalnej rozmowy właściwym trybem jest `--chat`, a dla wsadowego mostu jednorazowego `--chat-gpt`."
        )
        satisfied = ["direct_latka_voice", "model_channel_boundary", "process_lifecycle", "no_background_process_claim", "truth_boundary"]
        return RouteHandlerResult(
            self.name,
            self.route,
            body,
            intent="direct_latka_voice_request",
            data={"runtime_ownership_contract": ownership},
            generation_mode="handler_generated",
            required_components=ctx.get("required_components", []),
            satisfied_components=satisfied,
            source_origin_detail="direct_latka_voice_handler/runtime_owned",
            truth_boundary="Głos i perspektywa pochodzą z runtime i kanonu; host pozostaje kanałem językowym.",
            confidence=0.92,
        )
