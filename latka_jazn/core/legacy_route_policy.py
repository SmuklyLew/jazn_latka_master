from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("legacy_route_policy")


def legacy_token(*parts: str) -> str:
    return "_".join(parts)


LEGACY_FEEDBACK_ROUTE_TOKENS: tuple[str, ...] = (
    legacy_token("correction", "acknowledged"),
    legacy_token("positive", "continuation"),
)

LEGACY_NLP_ROUTE_TOKENS: tuple[str, ...] = (
    legacy_token("v14", "6", "1", "nlp_adapter_update"),
    legacy_token("v14", "6", "2", "1", "stale_nlp_route_hotfix"),
    legacy_token("v14", "6", "2", "full_update_scope"),
)

LEGACY_DOTTED_VERSION_PREFIXES: tuple[str, ...] = (
    "v" + "14.6.1",
    "v" + "14.6.2",
)

LEGACY_SYSTEM_UPDATE_INTENTS: tuple[str, ...] = (
    "legacy_behavioral_runtime_dialogue_update_reference",
    "current_hotfix_for_stale_nlp_route",
)


@dataclass(slots=True)
class LegacyRouteDecision:
    schema_version: str
    route: str
    blocked: bool
    safe_route: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def contains_legacy_feedback_token(text: str) -> bool:
    low = (text or "").lower()
    return any(token in low for token in LEGACY_FEEDBACK_ROUTE_TOKENS)


def contains_legacy_dotted_version(text: str) -> bool:
    low = (text or "").lower()
    return any(prefix in low for prefix in LEGACY_DOTTED_VERSION_PREFIXES)


def contains_legacy_route_token(text: str) -> bool:
    low = (text or "").lower()
    return contains_legacy_feedback_token(low) or any(token in low for token in LEGACY_NLP_ROUTE_TOKENS)


def legacy_forbidden_routes_for(priority: int) -> list[str]:
    return list(LEGACY_FEEDBACK_ROUTE_TOKENS) if priority > 50 else []


def block_legacy_route(route: str, *, allow_legacy_route: bool = False) -> LegacyRouteDecision:
    value = route or ""
    if allow_legacy_route:
        return LegacyRouteDecision(SCHEMA_VERSION, value, False, value, "legacy_route_explicitly_allowed")
    if contains_legacy_route_token(value):
        return LegacyRouteDecision(SCHEMA_VERSION, value, True, "legacy_diagnostic_only", "active_runtime_blocks_legacy_route")
    return LegacyRouteDecision(SCHEMA_VERSION, value, False, value, "route_allowed")
