from __future__ import annotations
from .clock import WarsawClock, TimeSample
from .emotions import AffectiveState
from .identity_guard import IdentityPerspectiveGuard

class ResponseRenderer:
    def __init__(self, clock: WarsawClock, guard: IdentityPerspectiveGuard) -> None:
        self.clock = clock
        self.guard = guard

    def render(self, body: str, state: AffectiveState | None = None, sample: TimeSample | None = None) -> str:
        state = state or AffectiveState()
        header = self.clock.header(sample)
        body = self.guard.enforce(body.strip())
        return f"{header} {state.marker()}\n{body}"
