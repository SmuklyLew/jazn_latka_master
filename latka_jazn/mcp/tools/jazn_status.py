from __future__ import annotations

from typing import Any

from latka_jazn.bridge.secure_host_runtime_gateway import SecureHostRuntimeGateway


def run(gateway: SecureHostRuntimeGateway) -> dict[str, Any]:
    status = gateway.status()
    text = "Jaźń runtime gateway is ready." if status.get("gateway_ok") else "Jaźń runtime gateway is unavailable."
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": status,
        "_meta": {"tool": "jazn_status", "private_operator_detail": True},
        "isError": not bool(status.get("gateway_ok")),
    }
