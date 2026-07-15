from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from latka_jazn.memory.sqlite.runtime_audit_schema import connect_runtime_audit, ensure_runtime_audit_schema
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("idempotency")


def canonical_json_bytes(value: Mapping[str, Any] | list[Any] | str | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def payload_hash(payload: Mapping[str, Any] | list[Any] | str | bytes) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def build_idempotency_key(*, turn_id: str, trace_id: str, operation: str, contract_hash: str) -> str:
    """Build a stable request identity independent from the request payload.

    The payload hash is stored separately. This is essential: using the payload
    hash inside the key would make it impossible to detect a same-key/different-
    payload conflict.
    """

    raw = canonical_json_bytes(
        {
            "turn_id": str(turn_id),
            "trace_id": str(trace_id),
            "operation": str(operation),
            "contract_hash": str(contract_hash),
        }
    )
    return "idem-" + hashlib.sha256(raw).hexdigest()


@dataclass(slots=True)
class IdempotencyDecision:
    state: str
    idempotency_key: str
    payload_hash: str
    result: dict[str, Any] | None = None
    schema_version: str = SCHEMA_VERSION

    @property
    def execute(self) -> bool:
        return self.state == "new"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["execute"] = self.execute
        return payload


class IdempotencyStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def claim(
        self,
        *,
        idempotency_key: str,
        payload_hash_value: str,
        operation: str,
        turn_id: str,
        trace_id: str,
        contract_hash: str,
    ) -> IdempotencyDecision:
        now = datetime.now(timezone.utc).isoformat()
        connection = connect_runtime_audit(self.path)
        try:
            ensure_runtime_audit_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_hash, state, result_json FROM idempotency_records WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                connection.commit()
                if row["payload_hash"] != payload_hash_value:
                    return IdempotencyDecision("conflict", idempotency_key, payload_hash_value)
                parsed = json.loads(row["result_json"]) if row["result_json"] else None
                return IdempotencyDecision("replay", idempotency_key, payload_hash_value, parsed)
            connection.execute(
                """
                INSERT INTO idempotency_records(
                    idempotency_key, operation, turn_id, trace_id, contract_hash,
                    payload_hash, state, result_json, created_at_utc, updated_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    idempotency_key,
                    operation,
                    turn_id,
                    trace_id,
                    contract_hash,
                    payload_hash_value,
                    "claimed",
                    None,
                    now,
                    now,
                ),
            )
            connection.commit()
            return IdempotencyDecision("new", idempotency_key, payload_hash_value)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finalize(self, idempotency_key: str, result: Mapping[str, Any], *, state: str = "completed") -> None:
        serialized = json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
        now = datetime.now(timezone.utc).isoformat()
        connection = connect_runtime_audit(self.path)
        try:
            ensure_runtime_audit_schema(connection)
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE idempotency_records
                    SET state=?, result_json=?, updated_at_utc=?
                    WHERE idempotency_key=?
                    """,
                    (state, serialized, now, idempotency_key),
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"unknown idempotency key: {idempotency_key}")
        finally:
            connection.close()

    def get(self, idempotency_key: str) -> dict[str, Any] | None:
        connection = connect_runtime_audit(self.path)
        try:
            ensure_runtime_audit_schema(connection)
            row = connection.execute(
                "SELECT * FROM idempotency_records WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row is None:
                return None
            payload = dict(row)
            payload["result"] = json.loads(payload.pop("result_json")) if payload.get("result_json") else None
            return payload
        finally:
            connection.close()
