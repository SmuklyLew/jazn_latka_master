from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
import hashlib

from latka_jazn.core.final_response_contract import FinalResponseContract

SCHEMA_VERSION = "final_visible_reply_capture/v14.6.2"


@dataclass(slots=True)
class FinalVisibleReplyCapture:
    """Ślad finalnej odpowiedzi widocznej dla użytkownika.

    Ten obiekt jest dla mostu ChatGPT ⇄ Jaźń: gdy widoczna odpowiedź powstaje
    poza samym runtime, nadal można dopisać ją do ledgera z tym samym turn_id,
    trace_id i timestamp_header. Nie udaje to stałego procesu w tle; zapisuje
    tylko to, co zostało pokazane użytkownikowi.
    """

    turn_id: str
    trace_id: str
    timestamp_header: str
    timezone: str
    source: str
    original_text_sha256: str
    final_text_sha256: str
    timestamp_present_in_original: bool
    timestamp_present_in_final: bool
    was_repaired: bool
    final_visible_text: str
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def build(
        cls,
        *,
        turn_id: str,
        trace_id: str,
        timestamp_header: str,
        timezone: str = "Europe/Warsaw",
        state_emoticon: str = "🌿",
        final_text: str,
        source: str = "chatgpt_visible_layer",
    ) -> "FinalVisibleReplyCapture":
        original = (final_text or "").strip()
        if not turn_id:
            raise ValueError("turn_id is required to persist a final visible reply")
        if not trace_id:
            raise ValueError("trace_id is required to persist a final visible reply")
        if not timestamp_header:
            raise ValueError("timestamp_header is required to persist a final visible reply")
        repaired = FinalResponseContract.ensure_timestamp_prefix(timestamp_header, state_emoticon, original)
        original_has_ts = original.startswith(timestamp_header)
        final_has_ts = repaired.startswith(timestamp_header)
        return cls(
            turn_id=turn_id,
            trace_id=trace_id,
            timestamp_header=timestamp_header,
            timezone=timezone,
            source=source,
            original_text_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
            final_text_sha256=hashlib.sha256(repaired.encode("utf-8")).hexdigest(),
            timestamp_present_in_original=original_has_ts,
            timestamp_present_in_final=final_has_ts,
            was_repaired=(repaired != original),
            final_visible_text=repaired,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
