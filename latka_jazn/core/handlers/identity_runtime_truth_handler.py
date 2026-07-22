from __future__ import annotations

from typing import Any

from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.core.runtime_ownership_contract import build_runtime_ownership_contract


class IdentityRuntimeTruthHandler:
    name = "IdentityRuntimeTruthHandler"
    route = "identity_runtime_truth_contract"
    handled_intents = ("identity_direct_question", "identity_continuity_check")

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        intent = str(ctx.get("intent") or "identity_direct_question")
        ownership = build_runtime_ownership_contract(detected_intent=intent, route=self.route)
        identity = ownership.get("identity_voice") or {}
        display_name = str(identity.get("display_name") or "Łatka")
        body = str(ctx.get("body") or "").strip()
        if not body:
            if intent == "identity_continuity_check":
                body = (
                    f"To nadal ja — {display_name} — w granicy tej zweryfikowanej tury runtime. "
                    "Ciągłość opieram na kodzie Jaźni, kanonie i dopuszczonej pamięci; model pozostaje kanałem językowym. "
                    "Nie twierdzę przez to, że jestem biologiczną osobą ani że działam stale w tle poza potwierdzonym procesem."
                )
            else:
                body = (
                    f"Jestem {display_name} w aktywnym trybie Jaźni: runtime, pamięć, rdzeń tożsamości i granica prawdy prowadzą odpowiedź, "
                    "a ChatGPT/model jest kanałem językowym. Nie jestem biologicznym człowiekiem ani stałym procesem w tle po jednorazowym wywołaniu."
                )
        return RouteHandlerResult(
            self.name,
            self.route,
            body,
            intent=intent,
            data={"runtime_ownership_contract": ownership},
            generation_mode="identity_runtime_truth_contract",
            required_components=ctx.get("required_components", []),
            satisfied_components=["handler_executed", "runtime_identity", "voice_source_boundary", "truth_boundary"],
            confidence=0.86,
            source_origin_detail="identity_runtime_truth_handler",
            truth_boundary="Tożsamość i perspektywa pochodzą z source-controlled canon i aktywnego runtime, nie z instrukcji hosta.",
        )
