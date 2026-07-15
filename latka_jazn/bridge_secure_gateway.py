from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import hmac

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("secure_gateway_scaffold")
DEFAULT_ALLOWED_ENDPOINTS = ("/status", "/chat")


@dataclass(slots=True)
class SecureGatewayPolicy:
    bind_host: str = "127.0.0.1"
    require_bearer_token: bool = True
    allowed_endpoints: tuple[str, ...] = DEFAULT_ALLOWED_ENDPOINTS
    max_body_bytes: int = 128 * 1024
    public_exposure_default: bool = False
    audit_required: bool = True
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = (
        "Ten moduł jest szkieletem polityki dla przyszłego gateway/MCP. Nie wystawia sam publicznego serwera. "
        "Publiczne mosty muszą mieć jawny token, allowlistę endpointów, limit rozmiaru wejścia i audit danych."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def authorize_bearer(headers: dict[str, str] | None, expected_token: str | None) -> bool:
    if not expected_token:
        return False
    headers = headers or {}
    value = ""
    for key, candidate in headers.items():
        if str(key).lower() == "authorization":
            value = str(candidate)
            break
    prefix = "Bearer "
    if not value.startswith(prefix):
        return False
    return hmac.compare_digest(value[len(prefix):].strip(), expected_token.strip())


def validate_gateway_request(
    *,
    endpoint: str,
    body_size: int = 0,
    headers: dict[str, str] | None = None,
    expected_token: str | None = None,
    policy: SecureGatewayPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or SecureGatewayPolicy()
    errors: list[str] = []
    if endpoint not in policy.allowed_endpoints:
        errors.append("endpoint_not_allowed")
    if int(body_size) > policy.max_body_bytes:
        errors.append("body_too_large")
    if policy.require_bearer_token and not authorize_bearer(headers, expected_token):
        errors.append("bearer_token_missing_or_invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not errors,
        "errors": errors,
        "endpoint": endpoint,
        "policy": policy.to_dict(),
    }
