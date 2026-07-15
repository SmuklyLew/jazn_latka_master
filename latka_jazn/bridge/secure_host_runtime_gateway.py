from __future__ import annotations

from dataclasses import asdict, dataclass
import ipaddress
import json
from pathlib import Path
from typing import Any
from urllib import request, error

from latka_jazn.bridge.auth_policy import AuthPolicy, SlidingWindowRateLimiter
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("secure_host_runtime_gateway")


class GatewayError(RuntimeError):
    pass


@dataclass(slots=True)
class GatewayConfig:
    daemon_url: str = "http://127.0.0.1:8787"
    timeout_seconds: float = 15.0
    max_request_bytes: int = 2 * 1024 * 1024
    allowed_tools: tuple[str, ...] = (
        "jazn_generate_visible_reply",
        "jazn_status",
        "jazn_finalize_reply",
        "jazn_audit_lookup",
    )
    public_ingress_enabled: bool = False
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.public_ingress_enabled:
            raise GatewayError("public_ingress_forbidden_by_default")
        host = self.daemon_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            if host not in {"localhost"}:
                raise GatewayError("daemon_host_must_be_loopback") from exc
        else:
            if not address.is_loopback:
                raise GatewayError("daemon_host_must_be_loopback")


class SecureHostRuntimeGateway:
    def __init__(
        self,
        config: GatewayConfig | None = None,
        *,
        auth_policy: AuthPolicy | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self.config = config or GatewayConfig()
        self.config.validate()
        self.auth_policy = auth_policy or AuthPolicy()
        self.rate_limiter = rate_limiter or SlidingWindowRateLimiter()

    def authorize(self, *, tool_name: str, token: str | None, subject: str) -> None:
        if tool_name not in self.config.allowed_tools:
            raise GatewayError("tool_not_allowlisted")
        if not self.rate_limiter.allow(subject):
            raise GatewayError("rate_limit_exceeded")
        decision = self.auth_policy.authorize(token, subject=subject)
        if not decision.allowed:
            raise GatewayError(decision.reason)

    def _http_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if body is not None and len(body) > self.config.max_request_bytes:
            raise GatewayError("request_too_large")
        req = request.Request(
            self.config.daemon_url.rstrip("/") + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                data = response.read(self.config.max_request_bytes + 1)
        except (error.URLError, TimeoutError, OSError) as exc:
            raise GatewayError(f"daemon_unavailable:{type(exc).__name__}") from exc
        if len(data) > self.config.max_request_bytes:
            raise GatewayError("response_too_large")
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise GatewayError("daemon_response_not_object")
        return value

    def status(self) -> dict[str, Any]:
        try:
            daemon = self._http_json("GET", "/status")
            reachable = True
            error_text = None
        except GatewayError as exc:
            daemon = {}
            reachable = False
            error_text = str(exc)
        return {
            "schema_version": SCHEMA_VERSION,
            "gateway_ok": reachable,
            "daemon_reachable": reachable,
            "daemon": daemon,
            "error": error_text,
            "public_ingress_enabled": False,
            "transport": "loopback_only",
        }

    def chat(self, message: str, *, session_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": str(message), "client": "secure_mcp_gateway"}
        if session_id:
            payload["session_id"] = session_id
        return self._http_json("POST", "/chat", payload)
