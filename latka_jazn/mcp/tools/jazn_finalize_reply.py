from __future__ import annotations

from typing import Any

from latka_jazn.core.host_visible_finalization import finalize_host_visible_text


def run(
    *,
    required_timestamp_header: str,
    turn_id: str,
    trace_id: str,
    final_text: str,
    supplied_turn_id: str | None = None,
    supplied_trace_id: str | None = None,
) -> dict[str, Any]:
    result = finalize_host_visible_text(
        required_timestamp_header=required_timestamp_header,
        turn_id=turn_id,
        trace_id=trace_id,
        text=final_text,
        supplied_turn_id=supplied_turn_id,
        supplied_trace_id=supplied_trace_id,
    )
    payload = result.to_dict()
    visible = result.final_visible_text if result.accepted else "Host-visible finalization rejected the reply."
    return {
        "content": [{"type": "text", "text": visible}],
        "structuredContent": payload,
        "_meta": {"violations": [item.to_dict() for item in result.violations]},
        "isError": not result.accepted,
    }
