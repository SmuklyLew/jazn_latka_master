from __future__ import annotations

from typing import Any

from latka_jazn.bridge.secure_host_runtime_gateway import SecureHostRuntimeGateway


def run(gateway: SecureHostRuntimeGateway, *, message: str, session_id: str | None = None) -> dict[str, Any]:
    response = gateway.chat(message, session_id=session_id)
    final_text = str(response.get("final_visible_text") or "")
    integrity = response.get("final_visible_integrity") or response.get("final_response_contract", {}).get("final_visible_integrity") or {}
    valid = bool(final_text and integrity.get("valid") is True)
    if not valid:
        return {
            "content": [{"type": "text", "text": "Runtime did not return a validated final_visible_text."}],
            "structuredContent": {"ok": False, "reason": "validated_final_visible_text_missing"},
            "_meta": {"runtime_response": response},
            "isError": True,
        }
    return {
        "content": [{"type": "text", "text": final_text}],
        "structuredContent": {
            "ok": True,
            "final_visible_text": final_text,
            "turn_id": response.get("turn_id") or response.get("trace", {}).get("turn_id"),
            "trace_id": response.get("trace_id") or response.get("trace", {}).get("trace_id"),
            "final_visible_integrity": integrity,
        },
        "_meta": {"transport": "secure_loopback_gateway"},
        "isError": False,
    }
