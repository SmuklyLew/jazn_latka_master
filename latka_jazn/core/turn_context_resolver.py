from __future__ import annotations

from dataclasses import asdict, dataclass, field

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("turn_context_resolver")

SHORT_CONTINUATION_EXACT = {
    "tak", "ok", "okej", "dalej", "kontynuuj", "zrób to", "zrob to",
    "to zrób", "to zrob", "możesz to zrobić", "mozesz to zrobic",
    "a ty", "a u ciebie", "i tyle", "no i tyle",
}

CURRENT_TURN_ONLY_MARKERS = (
    "jak się czujesz", "jak sie czujesz", "co tam", "co słychać", "co slychac",
    "czy działa", "czy dziala", "uruchom jaźń", "uruchom jazn", "czy uruchomiona",
    "co pamiętasz", "co pamietasz", "dzień dobry", "dzien dobry", "dobry wieczór",
    "dobry wieczor", "hej", "cześć", "czesc", "siemka", "dobranoc",
)

SYSTEM_UPDATE_ROUTE_MARKERS = (
    "system_update", "update", "manifest", "package", "zip", "legacy_diagnostic_only",
)


def _fold(text: str) -> str:
    return (text or "").lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz")).strip()


@dataclass(slots=True)
class TurnContextResolution:
    schema_version: str
    carryover_allowed: bool
    carryover_reason: str
    previous_context_used: bool
    previous_context_summary: str | None = None
    forced_current_turn_only: bool = False
    risk_flags: list[str] = field(default_factory=list)
    previous_context_age_seconds: int | None = None
    truth_boundary: str = "Poprzednią turę wolno użyć tylko dla jawnej lub krótkiej kontynuacji. Zwykłe pytania, powitania i health-checki są current-turn-only."

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class TurnContextResolver:
    def resolve(
        self,
        *,
        current_user_text: str,
        previous_user_text: str | None = None,
        previous_intent: str | None = None,
        previous_route: str | None = None,
        session_id: str | None = None,
        no_carryover: bool = False,
        time_gap_seconds: int | None = None,
        explicit_previous_user_text: bool = False,
    ) -> TurnContextResolution:
        text = _fold(current_user_text)
        previous_route_folded = _fold(previous_route or "")
        previous_intent_folded = _fold(previous_intent or "")
        words = [w for w in text.replace("?", " ").replace("!", " ").split() if w]
        risks: list[str] = []

        if no_carryover:
            return TurnContextResolution(SCHEMA_VERSION, False, "no_carryover_flag", False, None, True, ["no_carryover"], time_gap_seconds)
        if not previous_user_text:
            return TurnContextResolution(SCHEMA_VERSION, False, "no_previous_context", False, None, False, [], time_gap_seconds)

        if any(marker in text for marker in CURRENT_TURN_ONLY_MARKERS):
            risks.append("current_turn_only_marker")
        if any(marker in previous_route_folded for marker in SYSTEM_UPDATE_ROUTE_MARKERS) or any(marker in previous_intent_folded for marker in SYSTEM_UPDATE_ROUTE_MARKERS):
            risks.append("previous_system_or_update_route")
        if time_gap_seconds is not None and time_gap_seconds > 21600 and not explicit_previous_user_text:
            risks.append("previous_context_expired")

        short_or_ellipsis = (
            text in SHORT_CONTINUATION_EXACT
            or len(words) <= 4
            or (text.startswith(("a ", "wiec ", "więc ", "czyli ")) and len(words) <= 8)
        )

        if risks and not explicit_previous_user_text:
            return TurnContextResolution(
                SCHEMA_VERSION, False, "forced_current_turn_only_due_to_risk", False, None, True, risks, time_gap_seconds
            )

        if short_or_ellipsis or explicit_previous_user_text:
            summary = (previous_user_text or "").strip().replace("\n", " ")[:220]
            reason = "explicit_previous_user_text" if explicit_previous_user_text else "short_continuation"
            return TurnContextResolution(SCHEMA_VERSION, True, reason, True, summary, False, risks, time_gap_seconds)

        return TurnContextResolution(SCHEMA_VERSION, False, "not_a_short_or_explicit_continuation", False, None, True, risks, time_gap_seconds)
