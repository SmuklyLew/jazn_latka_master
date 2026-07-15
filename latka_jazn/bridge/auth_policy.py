from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import defaultdict, deque
import hmac
import os
import time
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("mcp_auth_policy")


@dataclass(slots=True)
class AuthDecision:
    allowed: bool
    reason: str
    subject: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuthPolicy:
    def __init__(
        self,
        expected_token: str | None = None,
        *,
        allow_unauthenticated_local_test: bool = False,
        trust_stdio_parent: bool = False,
    ) -> None:
        self.expected_token = expected_token if expected_token is not None else os.getenv("JAZN_MCP_TOKEN", "")
        self.allow_unauthenticated_local_test = allow_unauthenticated_local_test
        self.trust_stdio_parent = trust_stdio_parent

    def authorize(self, supplied_token: str | None, *, subject: str = "unknown") -> AuthDecision:
        if not self.expected_token:
            if self.trust_stdio_parent:
                return AuthDecision(True, "trusted_stdio_parent_transport", subject)
            if self.allow_unauthenticated_local_test:
                return AuthDecision(True, "local_test_auth_disabled", subject)
            return AuthDecision(False, "mcp_token_not_configured", subject)
        if supplied_token and hmac.compare_digest(str(supplied_token), str(self.expected_token)):
            return AuthDecision(True, "token_match", subject)
        return AuthDecision(False, "unauthorized", subject)


class SlidingWindowRateLimiter:
    def __init__(self, *, limit: int = 60, window_seconds: float = 60.0) -> None:
        self.limit = int(limit)
        self.window_seconds = float(window_seconds)
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, subject: str) -> bool:
        now = time.monotonic()
        queue = self._events[subject]
        while queue and now - queue[0] > self.window_seconds:
            queue.popleft()
        if len(queue) >= self.limit:
            return False
        queue.append(now)
        return True
