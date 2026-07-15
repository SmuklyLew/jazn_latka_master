from __future__ import annotations

from pathlib import Path
from typing import Any

from latka_jazn.core.cognitive_debugger import CognitiveDebugger


def run(*, audit_database: Path | str, turn_id: str, trace_id: str | None = None) -> dict[str, Any]:
    report = CognitiveDebugger(Path(audit_database)).explain_turn(turn_id, trace_id=trace_id, include_private=False)
    return {
        "content": [{"type": "text", "text": f"Audit events for turn {turn_id}: {len(report['timeline'])}."}],
        "structuredContent": report,
        "_meta": {"private_payloads_included": False},
        "isError": False,
    }
