from __future__ import annotations
from typing import Any
from latka_jazn.core.route_handler_base import RouteHandlerResult

class SystemRepairPlanHandler:
    name = "SystemRepairPlanHandler"
    route = "system_repair_plan"
    handled_intents = ("system_repair_plan_request", "logic_reasoning_audit_request")

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        body = (
            "Problem: bieżąca intencja może trafiać w złą trasę. Plan naprawy musi być kodowy i testowalny: najpierw failing tests dla `póki co`, `kod źródłowy`, `runtime-preview/--chat` i zwykłego `Co tam słychać?`; "
            "potem poprawa `dialogue_intent_classifier.py`, `ellipsis_resolver.py`, `route_registry.py`, `runtime_answer_validator.py`, `engine.py`, `raw_memory_status.py`, `package_export.py` i manifestów; "
            "następnie target_files: `TurnResponsePolicy`, `TurnLogicAuditor`, `ReasoningController`, `RuntimeSessionState`, handlery statusu Jaźni i `--chat`; "
            "na końcu tests: testy regresji, SQLite, CLI, ZIP/SHA i świeże uruchomienie z czystego folderu. acceptance_criteria: odpowiedź musi trafiać w bieżącą intencję, nie w stary szablon."
        )
        return RouteHandlerResult(self.name, self.route, body, intent=ctx.get("intent", "system_repair_plan_request"), generation_mode="system_repair_plan_handler", required_components=ctx.get("required_components", []), satisfied_components=["problem", "target_files", "code_steps", "tests", "acceptance_criteria"], source_origin_detail="system_repair_plan_handler/v14.8.2.4", confidence=0.87)
