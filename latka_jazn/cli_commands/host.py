from __future__ import annotations

from typing import Any

from latka_jazn.core.host_visible_finalization import finalize_host_visible_text


def finalize_payload(args: Any) -> dict[str, Any]:
    text = args.text
    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8-sig")
    result = finalize_host_visible_text(
        required_timestamp_header=args.timestamp_header,
        turn_id=args.turn_id,
        trace_id=args.trace_id,
        text=text,
        supplied_turn_id=args.supplied_turn_id,
        supplied_trace_id=args.supplied_trace_id,
        max_utf8_bytes=args.max_bytes,
    )
    return result.to_dict()
