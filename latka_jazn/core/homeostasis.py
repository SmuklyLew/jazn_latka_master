from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("homeostasis")


@dataclass(slots=True)
class HomeostasisInput:
    load: float = 0.0
    source_conflict: float = 0.0
    memory_tension: float = 0.0
    uncertainty: float = 0.0
    truth_need: float = 0.0
    action_cost: float = 0.0
    write_action: bool = False
    sensitive_action: bool = False


@dataclass(slots=True)
class HomeostasisDecision:
    risk_score: float
    action: str
    max_tool_calls: int
    generation_limit: int | None
    requires_verification: bool
    requires_user_confirmation: bool
    reasons: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Operational load regulation; not biological fatigue or emotion."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HomeostasisRegulator:
    def decide(self, state: HomeostasisInput) -> HomeostasisDecision:
        values = [
            max(0.0, min(1.0, state.load)),
            max(0.0, min(1.0, state.source_conflict)),
            max(0.0, min(1.0, state.memory_tension)),
            max(0.0, min(1.0, state.uncertainty)),
            max(0.0, min(1.0, state.truth_need)),
            max(0.0, min(1.0, state.action_cost)),
        ]
        risk = round(
            0.20 * values[0]
            + 0.22 * values[1]
            + 0.12 * values[2]
            + 0.18 * values[3]
            + 0.14 * values[4]
            + 0.14 * values[5],
            4,
        )
        reasons: list[str] = []
        if state.source_conflict >= 0.5:
            reasons.append("source_conflict")
        if state.uncertainty >= 0.5:
            reasons.append("high_uncertainty")
        if state.sensitive_action:
            reasons.append("sensitive_action")
        if state.write_action:
            reasons.append("write_action")

        confirmation = bool(state.write_action or state.sensitive_action)
        verification = bool(risk >= 0.45 or state.source_conflict >= 0.5 or state.truth_need >= 0.7)
        if risk >= 0.8 and (state.write_action or state.sensitive_action):
            action = "block_until_verified_and_confirmed"
            max_tools = 0
            generation_limit = 400
        elif risk >= 0.65:
            action = "reduce_and_verify"
            max_tools = 1
            generation_limit = 800
        elif risk >= 0.4:
            action = "verify_then_continue"
            max_tools = 3
            generation_limit = 1600
        else:
            action = "continue"
            max_tools = 8
            generation_limit = None
        return HomeostasisDecision(
            risk_score=risk,
            action=action,
            max_tool_calls=max_tools,
            generation_limit=generation_limit,
            requires_verification=verification,
            requires_user_confirmation=confirmation,
            reasons=reasons,
        )
