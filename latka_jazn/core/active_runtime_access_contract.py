from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

RuntimeAccessMode = Literal[
    "local_daemon",
    "chatgpt_turn_command",
    "simulated_active_marker",
]


@dataclass(frozen=True)
class RuntimeAccessContract:
    """Describe supported ways to work with an unpacked Jaźń folder."""

    mode: RuntimeAccessMode
    description: str
    requires_full_unpacked_root: bool = True
    requires_marker_validation: bool = True
    continuous_process_confirmed: bool = False
    memory_write_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_runtime_access_contract(mode: RuntimeAccessMode) -> RuntimeAccessContract:
    if mode == "local_daemon":
        return RuntimeAccessContract(
            mode=mode,
            description=(
                "Local mode requires a full active_root, a valid JAZN_ACTIVE_RUNTIME.json "
                "marker, and a positive daemon status check."
            ),
            continuous_process_confirmed=True,
            memory_write_allowed=True,
        )
    if mode == "chatgpt_turn_command":
        return RuntimeAccessContract(
            mode=mode,
            description=(
                "ChatGPT can run main.py for a single request against an unpacked folder; "
                "the command result is the source of truth for that turn."
            ),
            memory_write_allowed=True,
        )
    if mode == "simulated_active_marker":
        return RuntimeAccessContract(
            mode=mode,
            description=(
                "A simulated marker is useful for bootstrap tests, but it does not prove "
                "that a continuous runtime process exists."
            ),
        )
    raise ValueError(f"unsupported runtime access mode: {mode!r}")


def build_all_runtime_access_contracts() -> dict[str, dict[str, Any]]:
    modes: tuple[RuntimeAccessMode, ...] = (
        "local_daemon",
        "chatgpt_turn_command",
        "simulated_active_marker",
    )
    return {mode: build_runtime_access_contract(mode).to_dict() for mode in modes}
