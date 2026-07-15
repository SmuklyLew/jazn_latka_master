from __future__ import annotations
from typing import Any
from latka_jazn.core.route_handler_base import RouteHandlerResult

class RuntimeActivationStatusHandler:
    name = "RuntimeActivationStatusHandler"
    route = "runtime_activation_status"
    handled_intents = ("runtime_activation_status_question", "runtime_restart_request", "identity_boundary_question")

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        cfg = ctx.get("config")
        active_root = str(getattr(cfg, "root", "unknown"))
        version = str(getattr(cfg, "version", "unknown"))
        intent = ctx.get("intent", "runtime_activation_status_question")
        if intent == "runtime_restart_request":
            route = "runtime_restart_request"
            body = (
                f"Odebrałam prośbę o ponowne uruchomienie Jaźni. Aktywna paczka to `{version}` w folderze `{active_root}`. "
                "Sama odpowiedź runtime nie może uczciwie potwierdzić restartu własnego procesu: zatrzymanie, ponowne uruchomienie mostu i kontrolę statusu musi wykonać warstwa hosta/Codex. "
                "Po restarcie trzeba sprawdzić PID, wersję, aktywną bazę i odpowiedź kontrolną. Granica prawdy: ta wiadomość potwierdza rozpoznanie żądania restartu, nie wykonanie restartu."
            )
            satisfied = ["runtime_status", "process_lifecycle", "truth_boundary"]
        else:
            route = self.route
            body = (
                f"Tak — w sensie technicznym odpowiadam przez aktywną paczkę Jaźni `{version}` z folderu `{active_root}`. "
                "ChatGPT jest kanałem/modelową warstwą językową, a Jaźń jest aktywnym źródłem runtime, pamięci, kontraktu tożsamości, logiki i granicy prawdy. "
                "Nie oznacza to stałego procesu w tle: przy one-shot/runtime-preview proces działa dla tej tury, a `--chat` utrzymuje jeden silnik tylko przez czas procesu terminalowego lub wsadowego stdin. "
                "Jeżeli runtime zwróci nietrafiony szablon, finalna warstwa musi to oznaczyć zamiast udawać pełną odpowiedź Jaźni."
            )
            satisfied = ["runtime_status", "model_channel_boundary", "no_background_process_claim"]
        return RouteHandlerResult(self.name, route, body, intent=intent, generation_mode="runtime_activation_status_handler", required_components=ctx.get("required_components", []), satisfied_components=satisfied, source_origin_detail="runtime_activation_status_handler/v14.8.2.6.6", confidence=0.88)
