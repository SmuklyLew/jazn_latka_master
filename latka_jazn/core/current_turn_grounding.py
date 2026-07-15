from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from latka_jazn.version import PACKAGE_VERSION, schema_version

SCHEMA_VERSION = schema_version("current_turn_grounding")


def _dotted(*parts: str) -> str:
    return "v" + ".".join(parts)


def _route(*parts: str) -> str:
    return "_".join(parts)


LEGACY_OUTPUT_MARKERS = (
    _dotted("14", "6", "10"),
    _dotted("14", "8", "2", "4"),
    _dotted("14", "8", "3", "4", "093"),
    _dotted("14", "8", "5", "000"),
    _route("v14", "6", "2", "1", "stale_nlp_route_hotfix"),
    _route("v14", "6", "10", "behavioral_runtime_dialogue_intent_source_integrity_update"),
)

ORDINARY_DIALOGUE_INTENTS = {
    "ordinary_conversation", "standalone_greeting", "casual_greeting", "casual_feedback",
    "expressive_reaction", "short_free_dialogue", "positive_feedback_current_turn",
    "negative_feedback_current_turn", "sleep_closure_statement",
}

ORDINARY_META_LEAK_MARKERS = (
    "cognitive-frame", "techniczny fallback", "diagnostyka", "runtime jako",
    "nie znalazłam osobnej trasy", "nie znalazlam osobnej trasy",
    "odpowiadam zwyczajnie na bieżącą wiadomość", "odpowiadam zwyczajnie na biezaca wiadomosc",
)

STALE_UPDATE_MARKERS = (
    "ta aktualizacja ma trzy rdzenie", "dużej aktualizacji", "duzej aktualizacji",
    "zadanie wykonania aktualizacji v14.8.5.000",
    "pełny zip", "pelny zip", "manifest i eksport",
)

MEMORY_INJECTION_MARKERS = (
    "w pamięci widzę", "w pamieci widze", "najbliższy trop", "najblizszy trop",
    "mam aktywne tropy pamięci", "mam aktywne tropy pamieci",
)


def _fold(text: str) -> str:
    return (text or "").lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))


@dataclass(slots=True)
class CurrentTurnGroundingReport:
    schema_version: str
    valid: bool
    quality: str
    issues: list[str] = field(default_factory=list)
    repair_route: str | None = None
    repair_body: str | None = None
    runtime_version: str = PACKAGE_VERSION
    truth_boundary: str = "CurrentTurnGrounding wykrywa znane klasy stale-route i szablonów. Nie zastępuje pełnego rozumowania modelu."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_current_turn_grounding(
    *,
    user_text: str,
    response_body: str,
    detected_intent: str,
    route: str,
    handler: str | None = None,
    allowed_memory_items: list[dict[str, Any]] | None = None,
    carryover_report: dict[str, Any] | None = None,
    runtime_version: str = PACKAGE_VERSION,
) -> CurrentTurnGroundingReport:
    user_low = _fold(user_text)
    body_low = _fold(response_body)
    route_low = _fold(route)
    issues: list[str] = []

    for marker in LEGACY_OUTPUT_MARKERS:
        marker_low = _fold(marker)
        if marker_low in body_low and marker_low not in user_low:
            issues.append("stale_version_output")
            break

    if any(marker in body_low for marker in STALE_UPDATE_MARKERS) and not any(x in user_low for x in ("aktualiz", "patch", "zip", "manifest")):
        issues.append("stale_update_template_output")

    if detected_intent in ORDINARY_DIALOGUE_INTENTS and any(marker in body_low for marker in ORDINARY_META_LEAK_MARKERS):
        issues.append("ordinary_dialogue_meta_leak")

    memory_allowed = bool(allowed_memory_items) or any(x in user_low for x in ("pamiętasz", "pamietasz", "wspomn", "pamięć", "pamiec"))
    if not memory_allowed and any(marker in body_low for marker in MEMORY_INJECTION_MARKERS):
        issues.append("unrequested_memory_injection")

    carryover = carryover_report or {}
    if carryover.get("forced_current_turn_only") and any(marker in body_low for marker in ("poprzednio", "wcześniej", "wczesniej", "ostatnio")) and not any(marker in user_low for marker in ("poprzednio", "wcześniej", "wczesniej", "ostatnio")):
        issues.append("previous_context_leak")

    if issues:
        return CurrentTurnGroundingReport(
            schema_version=SCHEMA_VERSION,
            valid=False,
            quality="current_turn_mismatch",
            issues=issues,
            repair_route="current_turn_grounding_repair",
            repair_body=(
                "Nie pokażę tej odpowiedzi, bo wygląda na powrót do starej trasy albo szablonu. "
                "Odpowiadam od nowa do bieżącej wiadomości: bez historycznej wersji, bez przypadkowej pamięci i bez diagnostyki, jeśli nie była potrzebna."
            ),
            runtime_version=runtime_version,
        )

    return CurrentTurnGroundingReport(SCHEMA_VERSION, True, "topic_aligned", [], None, None, runtime_version)
