from __future__ import annotations
from typing import Any
from latka_jazn.core.route_handler_base import RouteHandlerResult

class RuntimeChatModeHandler:
    name = "RuntimeChatModeHandler"
    route = "runtime_chat_mode"
    handled_intents = ("runtime_chat_mode_request",)

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        body = (
            "Tak — dla takiej prośby właściwa trasa to `--chat`, a nie aktualizacja ani zwykła rozmowa. "
            "`python main.py --chat` utrzymuje jeden `JaznEngine` przez czas procesu i kolejne linie stdin aż do `/exit` albo EOF. "
            "W środowisku ChatGPT nie wolno udawać stałej pętli w tle; można użyć krótkiego testu wsadowego przez stdin albo `--chat-gpt`. "
            "Logika odpowiedzi ma pozostać ta sama co w one-shot i `--runtime-preview`; różni się tylko cykl życia procesu i format wejścia/wyjścia."
        )
        return RouteHandlerResult(self.name, self.route, body, intent=ctx.get("intent", "runtime_chat_mode_request"), generation_mode="runtime_chat_mode_handler", required_components=ctx.get("required_components", []), satisfied_components=["chat_mode", "process_lifecycle", "stdin_or_jsonl_boundary"], source_origin_detail="runtime_chat_mode_handler/v14.8.2.4", confidence=0.88)
