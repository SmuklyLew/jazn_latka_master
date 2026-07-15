from __future__ import annotations

from pathlib import Path
from typing import Any

from latka_jazn.core.cognitive_debugger import CognitiveDebugger
from latka_jazn.runtime.host_bridge_audit import HostBridgeAuditStore


def audit_tail(path: Path, limit: int = 20) -> dict[str, Any]:
    import sqlite3, json
    if not path.exists():
        return {"events": [], "database": str(path), "exists": False}
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        try:
            rows = connection.execute(
                "SELECT * FROM host_bridge_audit ORDER BY created_at_utc DESC,audit_id DESC LIMIT ?",
                (max(0, int(limit)),),
            ).fetchall()
        except sqlite3.DatabaseError:
            rows = []
        events = []
        for row in rows:
            item = dict(row)
            if item.get("metadata_json"):
                item["metadata"] = json.loads(item.pop("metadata_json"))
            events.append(item)
        return {"events": events, "database": str(path), "exists": True}
    finally:
        connection.close()


def explain(path: Path, turn_id: str, trace_id: str | None = None) -> dict[str, Any]:
    return CognitiveDebugger(path).explain_turn(turn_id, trace_id=trace_id, include_private=False)


def replay(path: Path, turn_id: str, trace_id: str | None = None) -> dict[str, Any]:
    return CognitiveDebugger(path).replay_turn(turn_id, trace_id=trace_id, dry_run=True)
