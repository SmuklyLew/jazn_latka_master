from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from latka_jazn.memory.sqlite.runtime_audit_schema import connect_runtime_audit, ensure_runtime_audit_schema
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("mcp_tool_audit")


@dataclass(slots=True)
class McpToolAuditEvent:
    tool_name: str
    subject: str
    idempotency_key: str
    payload_hash: str
    approval_state: str
    outcome: str
    metadata: dict[str, Any] = field(default_factory=dict)
    audit_id: str = ""
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("tool_name", "subject", "idempotency_key", "payload_hash", "approval_state", "outcome"):
            if not str(getattr(self, name, "") or "").strip():
                raise ValueError(f"{name} is required")
        if not self.audit_id:
            raw = json.dumps(
                {
                    "tool_name": self.tool_name,
                    "subject": self.subject,
                    "idempotency_key": self.idempotency_key,
                    "payload_hash": self.payload_hash,
                    "approval_state": self.approval_state,
                    "outcome": self.outcome,
                    "metadata": self.metadata,
                    "created_at_utc": self.created_at_utc,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            self.audit_id = "mcp-audit-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class McpToolAuditStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def append(self, event: McpToolAuditEvent) -> str:
        connection = connect_runtime_audit(self.path)
        try:
            ensure_runtime_audit_schema(connection)
            with connection:
                connection.execute(
                    """
                    INSERT INTO mcp_tool_audit(
                      audit_id,tool_name,subject,idempotency_key,payload_hash,
                      approval_state,outcome,metadata_json,created_at_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event.audit_id,
                        event.tool_name,
                        event.subject,
                        event.idempotency_key,
                        event.payload_hash,
                        event.approval_state,
                        event.outcome,
                        json.dumps(event.metadata, ensure_ascii=False, sort_keys=True),
                        event.created_at_utc,
                    ),
                )
            return event.audit_id
        finally:
            connection.close()

    def list_for_key(self, idempotency_key: str) -> list[dict[str, Any]]:
        connection = connect_runtime_audit(self.path)
        try:
            ensure_runtime_audit_schema(connection)
            rows = connection.execute(
                "SELECT * FROM mcp_tool_audit WHERE idempotency_key=? ORDER BY created_at_utc,audit_id",
                (idempotency_key,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["metadata"] = json.loads(item.pop("metadata_json"))
                result.append(item)
            return result
        finally:
            connection.close()
