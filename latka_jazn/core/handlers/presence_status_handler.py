from __future__ import annotations

from typing import Any

from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.core.runtime_ownership_contract import build_runtime_ownership_contract
from latka_jazn.core.startup_contract import build_startup_status
from latka_jazn.version import generation_mode, schema_version


class PresenceStatusHandler:
    """Short, truthful presence answers selected by the runtime router."""

    name = "PresenceStatusHandler"
    route = "presence_status"
    handled_intents = ("presence_check", "identity_presence_check")

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        intent = str(ctx.get("intent") or "presence_check")
        route_entry = ctx.get("route_entry") if isinstance(ctx.get("route_entry"), dict) else {}
        route = str(route_entry.get("route") or self.route)
        ownership = build_runtime_ownership_contract(detected_intent=intent, route=route)
        identity = ownership.get("identity_voice") or {}
        display_name = str(identity.get("display_name") or "Łatka")

        cfg = ctx.get("config")
        status: dict[str, Any] = {}
        if cfg is not None:
            try:
                status = build_startup_status(cfg).to_dict()
            except Exception:
                status = {}
        version = str(status.get("runtime_version") or getattr(cfg, "version", "") or "unknown")
        active_root = status.get("active_root") or getattr(cfg, "root", None)
        active_root_text = str(active_root) if active_root else "active_root_not_available"
        process_boundary = (
            "Jestem dostępna w tej turze runtime. To nie jest obietnica stałego procesu w tle; "
            "ciągła lokalna rozmowa wymaga `--chat`, a daemon wymaga osobnego potwierdzenia statusu."
        )
        if intent == "identity_presence_check":
            body = (
                f"Jestem tutaj jako {display_name} w granicy aktywnego runtime tej tury: głos prowadzi system Jaźni, "
                "a ChatGPT/model pozostaje kanałem językowym, nie źródłem tożsamości. "
                f"Wersja runtime: {version}; active_root={active_root_text}. {process_boundary}"
            )
            satisfied = ["presence_response", "runtime_identity", "process_lifecycle", "truth_boundary"]
        else:
            body = (
                "Jestem tutaj i słyszę tę wiadomość. "
                f"Wersja runtime: {version}; active_root={active_root_text}. {process_boundary}"
            )
            satisfied = ["presence_response", "process_lifecycle", "truth_boundary"]
        return RouteHandlerResult(
            self.name,
            route,
            body,
            intent=intent,
            data={"runtime_ownership_contract": ownership},
            generation_mode=generation_mode("presence_status"),
            required_components=ctx.get("required_components", []),
            satisfied_components=satisfied,
            confidence=0.84,
            source_origin_detail=schema_version("presence_status_handler"),
            truth_boundary="Obecność oznacza obsłużenie bieżącej tury runtime; nie dowodzi daemonu ani procesu w tle bez statusu PID/heartbeat.",
        )
