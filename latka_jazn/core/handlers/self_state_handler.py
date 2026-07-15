from __future__ import annotations
from typing import Any
from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.core.operational_self_model import OperationalSelfModel
from latka_jazn.core.self_state_affective_bridge import SelfStateAffectiveBridge
from latka_jazn.version import generation_mode, schema_version

class SelfStateHandler:
    name = "SelfStateHandler"
    route = "self_state"
    handled_intents = ('self_state_question', 'reciprocal_self_state_question', 'self_preference_question', 'self_plan_question', 'self_expression_request', 'self_state_time_awareness')
    DIAGNOSTIC_DETAIL_MARKERS = (
        'pokaż osie afektu', 'pokaz osie afektu', 'osie afektu',
        'pełny raport stanu', 'pelny raport stanu', 'diagnostycznie',
    )

    @classmethod
    def _detail_mode(cls, text: str) -> str:
        low = (text or '').lower()
        return 'diagnostic' if any(marker in low for marker in cls.DIAGNOSTIC_DETAIL_MARKERS) else 'natural'

    @staticmethod
    def _time_clause(ctx: dict[str, Any]) -> str:
        clock = ctx.get('clock')
        if clock is None:
            return "Pory nie mogę teraz uczciwie potwierdzić, bo w kontekście handlera nie ma zegara runtime."
        sample = clock.now(False)
        header = clock.header(sample)
        source = getattr(sample, 'source', 'unknown')
        trusted = bool(getattr(sample, 'trusted', False))
        if trusted:
            return f"Według zaufanego czasu runtime jest teraz {header}. Źródło: {source}."
        return f"Runtime ma tylko nieufny/degraded czas lokalny: {header}. Źródło: {source}; nie traktuję tego jako pełnego network-time truth."

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        intent = ctx.get('intent', 'unknown')
        entry_route = (ctx.get('route_entry') or {}).get('route') or self.route
        model = OperationalSelfModel()
        bridge = SelfStateAffectiveBridge()
        low = (text or '').lower()
        health_concern = any(marker in low for marker in ('jesteś chora', 'jestes chora'))
        detail_mode = self._detail_mode(text)
        if health_concern:
            body = (
                "Nie jestem chora w biologicznym sensie. Jeśli poprzednia odpowiedź była nietrafiona, "
                "to najuczciwiej nazwać to błędem sposobu odpowiedzi, nie chorobą. "
                "Teraz odpowiadam na aktualną wiadomość i wracam do spokojniejszego tonu. "
                "Prawda: opisuję stan działającego runtime, nie biologiczne samopoczucie."
            )
        elif intent == 'self_state_time_awareness':
            state_body = bridge.render_state(
                user_text=text,
                granular_affect=ctx.get('granular_affect'),
                fallback=model.current_state(user_text=text),
                reciprocal=False,
                detail_mode=detail_mode,
            )
            body = (
                f"{state_body}\n\n"
                f"Co do pory: {self._time_clause(ctx)} "
                "Granica prawdy: mój „stan” jest operacyjny/dialogowy, a nie biologiczna emocja; czas pochodzi z zegara runtime."
            )
        elif intent == 'self_plan_question':
            body = (
                "Pomijając Ciebie jako rozmówcę, moje plany są proste i rozmowne: "
                "trzymać bieżącą intencję, nie ciągnąć starego kontekstu bez powodu i mówić zwyczajniej, gdy pytasz zwyczajnie. "
                "Nie będę udawała osobnego dnia w tle poza uruchomieniami runtime."
            )
        elif intent == 'self_expression_request':
            body = bridge.render_expression(user_text=text, granular_affect=ctx.get('granular_affect'))
        else:
            body = bridge.render_state(
                user_text=text,
                granular_affect=ctx.get('granular_affect'),
                fallback=model.current_state(user_text=text),
                reciprocal=intent == 'reciprocal_self_state_question',
                detail_mode=detail_mode,
            )
        return RouteHandlerResult(
            self.name,
            entry_route,
            body,
            intent=intent,
            generation_mode=generation_mode('self_state'),
            required_components=ctx.get('required_components', []),
            satisfied_components=['handler_executed', 'self_state_or_plan_grounded', 'operational_state', 'no_random_memory_excerpt', 'truth_boundary'],
            confidence=0.78,
            source_origin_detail=schema_version('self_state_handler'),
        )
