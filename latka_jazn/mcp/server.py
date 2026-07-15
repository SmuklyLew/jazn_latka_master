from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from latka_jazn.bridge.auth_policy import AuthPolicy
from latka_jazn.bridge.secure_host_runtime_gateway import GatewayConfig, GatewayError, SecureHostRuntimeGateway
from latka_jazn.core.host_visible_finalization import HostVisibleFinalizationContract
from latka_jazn.mcp.tools import jazn_audit_lookup, jazn_finalize_reply, jazn_generate_visible_reply, jazn_status
from latka_jazn.runtime.host_bridge_audit import HostBridgeAuditEvent, HostBridgeAuditStore
from latka_jazn.runtime.idempotency import IdempotencyStore, build_idempotency_key, payload_hash
from latka_jazn.runtime.mcp_tool_audit import McpToolAuditEvent, McpToolAuditStore
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version

SCHEMA_VERSION = schema_version("jazn_mcp_server")
SIDE_EFFECT_TOOLS = {"jazn_generate_visible_reply", "jazn_finalize_reply"}
READ_ONLY_TOOLS = {"jazn_status", "jazn_audit_lookup"}
DENIED_APPROVAL_STATES = {"denied", "rejected", "not_approved"}

TOOL_DEFINITIONS = [
    {
        "name": "jazn_generate_visible_reply",
        "title": "Generate validated Jaźń reply",
        "description": "Generate a validated Jaźń runtime reply through the private loopback gateway.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "session_id": {"type": "string"},
                "request_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["message"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": False,
            "idempotentHint": True,
        },
    },
    {
        "name": "jazn_status",
        "title": "Read Jaźń runtime status",
        "description": "Read private runtime/gateway status without mutation.",
        "inputSchema": {
            "type": "object",
            "properties": {"request_id": {"type": "string"}, "idempotency_key": {"type": "string"}},
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
            "idempotentHint": True,
        },
        "_meta": {"ui": {"visibility": ["app"]}, "openai/visibility": "private"},
    },
    {
        "name": "jazn_finalize_reply",
        "title": "Finalize host-visible Jaźń reply",
        "description": "Validate, audit, and idempotently finalize host-authored visible text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "required_timestamp_header": {"type": "string"},
                "turn_id": {"type": "string"},
                "trace_id": {"type": "string"},
                "final_text": {"type": "string"},
                "supplied_turn_id": {"type": "string"},
                "supplied_trace_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["required_timestamp_header", "turn_id", "trace_id", "final_text"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": False,
            "idempotentHint": True,
        },
    },
    {
        "name": "jazn_audit_lookup",
        "title": "Read redacted Jaźń audit",
        "description": "Read redacted audit evidence for one turn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "turn_id": {"type": "string"},
                "trace_id": {"type": "string"},
                "request_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["turn_id"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
            "idempotentHint": True,
        },
        "_meta": {"ui": {"visibility": ["app"]}, "openai/visibility": "private"},
    },
]


def _tool_contract_hash(tool_name: str) -> str:
    raw = json.dumps(
        {"tool_name": tool_name, "server_schema": SCHEMA_VERSION, "runtime_version": PACKAGE_VERSION_FULL},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _tool_error(reason: str, **details: Any) -> dict[str, Any]:
    structured: dict[str, Any] = {"ok": False, "reason": reason}
    structured.update(details)
    return {
        "content": [{"type": "text", "text": f"Jaźń MCP tool failed safely: {reason}."}],
        "structuredContent": structured,
        "_meta": {},
        "isError": True,
    }


class JaznMcpServer:
    def __init__(
        self,
        *,
        root: Path,
        daemon_url: str = "http://127.0.0.1:8787",
        token: str | None = None,
        allow_unauthenticated_local_test: bool = False,
        trust_stdio_parent: bool = False,
    ) -> None:
        self.root = root.resolve()
        auth = AuthPolicy(
            token,
            allow_unauthenticated_local_test=allow_unauthenticated_local_test,
            trust_stdio_parent=trust_stdio_parent,
        )
        self.gateway = SecureHostRuntimeGateway(GatewayConfig(daemon_url=daemon_url), auth_policy=auth)
        self.audit_database = self.root / "memory/sqlite/runtime_write_v1/runtime_audit.sqlite3"
        self.idempotency = IdempotencyStore(self.audit_database)
        self.host_audit = HostBridgeAuditStore(self.audit_database)
        self.mcp_audit = McpToolAuditStore(self.audit_database)

    def _authorize(self, name: str, arguments: dict[str, Any], meta: dict[str, Any]) -> str:
        token = arguments.pop("auth_token", None) or meta.get("authorization") or meta.get("token")
        subject = str(meta.get("openai/subject") or meta.get("subject") or "mcp-client")
        self.gateway.authorize(tool_name=name, token=token, subject=subject)
        return subject

    @staticmethod
    def _approval_state(name: str, metadata: dict[str, Any]) -> str:
        declared = str(metadata.get("approval_state") or "").strip().lower()
        if declared:
            return declared
        if name in READ_ONLY_TOOLS:
            return "not_required_read_only"
        # OpenAI MCP approval is normally enforced by the host before the call.
        # The server still authenticates, allowlists, rate-limits and records that
        # the approval evidence belongs to the authenticated host boundary.
        return "host_managed_authenticated"

    @staticmethod
    def _control_fields(args: dict[str, Any], metadata: dict[str, Any]) -> tuple[str | None, str | None]:
        explicit_key = args.pop("idempotency_key", None) or metadata.get("idempotency_key")
        request_id = args.pop("request_id", None) or metadata.get("request_id")
        return (
            str(explicit_key).strip() if explicit_key is not None else None,
            str(request_id).strip() if request_id is not None else None,
        )

    def _request_identity(
        self,
        name: str,
        args: dict[str, Any],
        *,
        subject: str,
        request_id: str | None,
    ) -> tuple[str, str, str]:
        if name == "jazn_finalize_reply":
            contract = HostVisibleFinalizationContract(
                required_timestamp_header=str(args["required_timestamp_header"]),
                turn_id=str(args["turn_id"]),
                trace_id=str(args["trace_id"]),
            )
            return contract.turn_id, contract.trace_id, contract.contract_hash
        if name == "jazn_generate_visible_reply":
            identity = request_id or str(args.get("session_id") or "mcp-generate")
            return identity, identity, _tool_contract_hash(name)
        if name == "jazn_audit_lookup":
            turn_id = str(args["turn_id"])
            trace_id = str(args.get("trace_id") or turn_id)
            return turn_id, trace_id, _tool_contract_hash(name)
        identity = request_id or f"{subject}:{name}"
        return identity, identity, _tool_contract_hash(name)

    @staticmethod
    def _augment_result(
        result: dict[str, Any],
        *,
        idempotency_key: str,
        idempotency_state: str,
        approval_state: str,
        audit_id: str,
        host_bridge_audit_id: str | None = None,
        replay_audit_id: str | None = None,
    ) -> dict[str, Any]:
        value = deepcopy(result)
        structured = dict(value.get("structuredContent") or {})
        structured.update(
            {
                "idempotency_key": idempotency_key,
                "idempotency_state": idempotency_state,
                "approval_state": approval_state,
                "audit_id": audit_id,
            }
        )
        if host_bridge_audit_id:
            structured["host_bridge_audit_id"] = host_bridge_audit_id
        if replay_audit_id:
            structured["replay_audit_id"] = replay_audit_id
        value["structuredContent"] = structured
        meta = dict(value.get("_meta") or {})
        meta["mcp_invocation"] = {
            "idempotency_key": idempotency_key,
            "idempotency_state": idempotency_state,
            "approval_state": approval_state,
            "audit_id": audit_id,
        }
        value["_meta"] = meta
        return value

    def _append_mcp_audit(
        self,
        *,
        name: str,
        subject: str,
        key: str,
        payload_digest: str,
        approval_state: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self.mcp_audit.append(
            McpToolAuditEvent(
                tool_name=name,
                subject=subject,
                idempotency_key=key,
                payload_hash=payload_digest,
                approval_state=approval_state,
                outcome=outcome,
                metadata=dict(metadata or {}),
            )
        )

    def _append_host_audit(
        self,
        *,
        event_type: str,
        turn_id: str,
        trace_id: str,
        key: str,
        contract_hash: str,
        payload_digest: str,
        result: dict[str, Any] | None,
        approval_state: str,
    ) -> str:
        structured = dict((result or {}).get("structuredContent") or {})
        return self.host_audit.append(
            HostBridgeAuditEvent(
                event_type=event_type,
                turn_id=turn_id,
                trace_id=trace_id,
                idempotency_key=key,
                contract_hash=contract_hash,
                payload_hash=payload_digest,
                final_hash=structured.get("final_text_sha256"),
                metadata={
                    "tool_name": "jazn_finalize_reply",
                    "approval_state": approval_state,
                    "accepted": structured.get("accepted"),
                    "state": structured.get("state"),
                },
            )
        )

    def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "jazn_status":
            return jazn_status.run(self.gateway)
        if name == "jazn_generate_visible_reply":
            return jazn_generate_visible_reply.run(
                self.gateway,
                message=str(args["message"]),
                session_id=args.get("session_id"),
            )
        if name == "jazn_finalize_reply":
            return jazn_finalize_reply.run(**args)
        if name == "jazn_audit_lookup":
            return jazn_audit_lookup.run(
                audit_database=self.audit_database,
                turn_id=str(args["turn_id"]),
                trace_id=args.get("trace_id"),
            )
        raise GatewayError("tool_not_allowlisted")

    def call_tool(self, name: str, arguments: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        metadata = dict(meta or {})
        subject = self._authorize(name, args, metadata)
        approval_state = self._approval_state(name, metadata)
        explicit_key, request_id = self._control_fields(args, metadata)
        turn_id, trace_id, contract_hash = self._request_identity(
            name,
            args,
            subject=subject,
            request_id=request_id,
        )
        key = explicit_key or build_idempotency_key(
            turn_id=turn_id,
            trace_id=trace_id,
            operation=f"mcp:{name}",
            contract_hash=contract_hash,
        )
        if len(key) > 512:
            raise ValueError("idempotency_key_too_large")
        payload_digest = payload_hash(args)

        if approval_state in DENIED_APPROVAL_STATES:
            audit_id = self._append_mcp_audit(
                name=name,
                subject=subject,
                key=key,
                payload_digest=payload_digest,
                approval_state=approval_state,
                outcome="approval_rejected",
            )
            return self._augment_result(
                _tool_error("approval_rejected"),
                idempotency_key=key,
                idempotency_state="not_executed",
                approval_state=approval_state,
                audit_id=audit_id,
            )

        if name in SIDE_EFFECT_TOOLS:
            decision = self.idempotency.claim(
                idempotency_key=key,
                payload_hash_value=payload_digest,
                operation=f"mcp:{name}",
                turn_id=turn_id,
                trace_id=trace_id,
                contract_hash=contract_hash,
            )
            if decision.state == "conflict":
                host_audit_id = None
                if name == "jazn_finalize_reply":
                    host_audit_id = self._append_host_audit(
                        event_type="conflict",
                        turn_id=turn_id,
                        trace_id=trace_id,
                        key=key,
                        contract_hash=contract_hash,
                        payload_digest=payload_digest,
                        result=None,
                        approval_state=approval_state,
                    )
                audit_id = self._append_mcp_audit(
                    name=name,
                    subject=subject,
                    key=key,
                    payload_digest=payload_digest,
                    approval_state=approval_state,
                    outcome="conflict",
                    metadata={"host_bridge_audit_id": host_audit_id},
                )
                return self._augment_result(
                    _tool_error("idempotency_conflict"),
                    idempotency_key=key,
                    idempotency_state="conflict",
                    approval_state=approval_state,
                    audit_id=audit_id,
                    host_bridge_audit_id=host_audit_id,
                )
            if decision.state == "replay":
                stored = decision.result or _tool_error("idempotency_result_unavailable")
                host_audit_id = None
                if name == "jazn_finalize_reply":
                    host_audit_id = self._append_host_audit(
                        event_type="replay",
                        turn_id=turn_id,
                        trace_id=trace_id,
                        key=key,
                        contract_hash=contract_hash,
                        payload_digest=payload_digest,
                        result=stored,
                        approval_state=approval_state,
                    )
                replay_audit_id = self._append_mcp_audit(
                    name=name,
                    subject=subject,
                    key=key,
                    payload_digest=payload_digest,
                    approval_state=approval_state,
                    outcome="replay",
                    metadata={"host_bridge_audit_id": host_audit_id},
                )
                original_audit_id = str((stored.get("structuredContent") or {}).get("audit_id") or replay_audit_id)
                return self._augment_result(
                    stored,
                    idempotency_key=key,
                    idempotency_state="replay",
                    approval_state=approval_state,
                    audit_id=original_audit_id,
                    host_bridge_audit_id=host_audit_id,
                    replay_audit_id=replay_audit_id,
                )

        try:
            result = self._dispatch(name, args)
        except (GatewayError, KeyError, TypeError, ValueError, PermissionError) as exc:
            result = _tool_error(f"{type(exc).__name__}:{exc}")

        is_error = bool(result.get("isError"))
        host_audit_id = None
        if name == "jazn_finalize_reply":
            state = str((result.get("structuredContent") or {}).get("state") or "reject")
            event_type = state if state in {"accept", "repair", "reject"} else "reject"
            host_audit_id = self._append_host_audit(
                event_type=event_type,
                turn_id=turn_id,
                trace_id=trace_id,
                key=key,
                contract_hash=contract_hash,
                payload_digest=payload_digest,
                result=result,
                approval_state=approval_state,
            )
        audit_id = self._append_mcp_audit(
            name=name,
            subject=subject,
            key=key,
            payload_digest=payload_digest,
            approval_state=approval_state,
            outcome="error" if is_error else "completed",
            metadata={"host_bridge_audit_id": host_audit_id, "turn_id": turn_id, "trace_id": trace_id},
        )
        result = self._augment_result(
            result,
            idempotency_key=key,
            idempotency_state="completed" if name in SIDE_EFFECT_TOOLS else "read_only",
            approval_state=approval_state,
            audit_id=audit_id,
            host_bridge_audit_id=host_audit_id,
        )
        if name in SIDE_EFFECT_TOOLS:
            self.idempotency.finalize(key, result, state="error" if is_error else "completed")
        return result

    def handle(self, request_value: dict[str, Any]) -> dict[str, Any] | None:
        method = request_value.get("method")
        request_id = request_value.get("id")
        if method == "notifications/initialized":
            return None
        try:
            if method == "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": request_value.get("params", {}).get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "jazn-private-mcp", "version": PACKAGE_VERSION_FULL},
                }
            elif method == "tools/list":
                result = {"tools": TOOL_DEFINITIONS}
            elif method == "tools/call":
                params = dict(request_value.get("params") or {})
                result = self.call_tool(
                    str(params.get("name") or ""),
                    dict(params.get("arguments") or {}),
                    dict(params.get("_meta") or {}),
                )
            else:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (GatewayError, KeyError, TypeError, ValueError, PermissionError) as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": str(exc)}}

    def serve_stdio(self) -> int:
        for line in sys.stdin:
            raw = line.strip()
            if not raw:
                continue
            try:
                request_value = json.loads(raw)
                if not isinstance(request_value, dict):
                    raise ValueError("request must be an object")
                response = self.handle(request_value)
            except Exception as exc:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Private stdio MCP server for Jaźń v15.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8787")
    parser.add_argument("--allow-unauthenticated-local-test", action="store_true")
    parser.add_argument(
        "--trust-secure-tunnel-association",
        action="store_true",
        help="Trust the authenticated local stdio parent (for outbound Secure MCP Tunnel only).",
    )
    args = parser.parse_args(argv)
    server = JaznMcpServer(
        root=Path(args.root),
        daemon_url=args.daemon_url,
        allow_unauthenticated_local_test=args.allow_unauthenticated_local_test,
        trust_stdio_parent=args.trust_secure_tunnel_association,
    )
    return server.serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
