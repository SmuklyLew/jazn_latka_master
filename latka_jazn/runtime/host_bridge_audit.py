from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from latka_jazn.memory.sqlite.runtime_audit_schema import connect_runtime_audit, ensure_runtime_audit_schema
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("host_bridge_audit")
EVENT_TYPES = {
    "accept",
    "repair",
    "reject",
    "duplicate_same_payload",
    "replay",
    "conflict",
    "transport_error",
}


@dataclass(slots=True)
class HostBridgeAuditEvent:
    event_type: str
    turn_id: str
    trace_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    contract_hash: str | None = None
    payload_hash: str | None = None
    final_hash: str | None = None
    audit_id: str = ""
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported audit event: {self.event_type}")
        if not self.turn_id or not self.trace_id:
            raise ValueError("turn_id and trace_id are required")
        if not self.audit_id:
            raw = json.dumps(
                {
                    "event_type": self.event_type,
                    "turn_id": self.turn_id,
                    "trace_id": self.trace_id,
                    "idempotency_key": self.idempotency_key,
                    "contract_hash": self.contract_hash,
                    "payload_hash": self.payload_hash,
                    "final_hash": self.final_hash,
                    "created_at_utc": self.created_at_utc,
                    "metadata": self.metadata,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            self.audit_id = "audit-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HostBridgeAuditStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def append(self, event: HostBridgeAuditEvent) -> str:
        connection = connect_runtime_audit(self.path)
        try:
            ensure_runtime_audit_schema(connection)
            with connection:
                connection.execute(
                    """
                    INSERT INTO host_bridge_audit(
                      audit_id,event_type,idempotency_key,turn_id,trace_id,
                      contract_hash,payload_hash,final_hash,metadata_json,created_at_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event.audit_id,
                        event.event_type,
                        event.idempotency_key,
                        event.turn_id,
                        event.trace_id,
                        event.contract_hash,
                        event.payload_hash,
                        event.final_hash,
                        json.dumps(event.metadata, ensure_ascii=False, sort_keys=True),
                        event.created_at_utc,
                    ),
                )
            return event.audit_id
        finally:
            connection.close()

    def list_for_turn(self, turn_id: str, trace_id: str | None = None) -> list[dict[str, Any]]:
        connection = connect_runtime_audit(self.path)
        try:
            ensure_runtime_audit_schema(connection)
            if trace_id is None:
                rows = connection.execute(
                    "SELECT * FROM host_bridge_audit WHERE turn_id=? ORDER BY created_at_utc,audit_id",
                    (turn_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM host_bridge_audit WHERE turn_id=? AND trace_id=? ORDER BY created_at_utc,audit_id",
                    (turn_id, trace_id),
                ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["metadata"] = json.loads(item.pop("metadata_json"))
                result.append(item)
            return result
        finally:
            connection.close()
