from __future__ import annotations

from typing import Any

from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.version import generation_mode, schema_version


class TimeAwarenessHandler:
    """Answer current-time questions from the runtime clock with trust boundary."""

    name = "TimeAwarenessHandler"
    route = "time_awareness"
    handled_intents = ("time_awareness_question",)

    @staticmethod
    def _time_text(ctx: dict[str, Any]) -> tuple[str, bool]:
        clock = ctx.get("clock")
        if clock is None:
            return "Nie mam teraz w kontekście zegara runtime, więc nie potwierdzę uczciwie pory.", False
        sample = clock.now(False)
        header = clock.header(sample)
        source = getattr(sample, "source", "unknown")
        trusted = bool(getattr(sample, "trusted", False))
        if trusted:
            return f"Według zaufanego czasu runtime jest teraz {header}. Źródło czasu: {source}.", True
        return (
            f"Runtime ma teraz tylko nieufny/degraded czas lokalny: {header}. "
            f"Źródło czasu: {source}. Traktuję to jako fallback, nie pełne potwierdzenie network-time truth.",
            False,
        )

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        body, trusted = self._time_text(ctx)
        return RouteHandlerResult(
            self.name,
            self.route,
            body,
            intent=ctx.get("intent", "time_awareness_question"),
            generation_mode=generation_mode("time_awareness"),
            required_components=ctx.get("required_components", []),
            satisfied_components=["current_time", "timezone", "source_or_fallback", "truth_boundary"],
            confidence=0.84,
            source_origin_detail=schema_version("time_awareness_handler"),
            truth_boundary="Odpowiedź o czasie opiera się na zegarze runtime; gdy timestamp nie jest trusted, odpowiedź musi pozostać degraded.",
            data={"timestamp_trusted": trusted},
        )
