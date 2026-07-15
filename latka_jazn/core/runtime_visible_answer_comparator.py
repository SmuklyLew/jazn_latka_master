from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any
import difflib, hashlib
from latka_jazn.core.turn_trace_reader import TurnTraceReader

SCHEMA_VERSION = "runtime_visible_answer_comparator/v14.6.10"

@dataclass(slots=True)
class RuntimeVisibleComparison:
    schema_version: str
    trace_id: str
    runtime_text_exact: str
    visible_text_exact: str
    response_generation_mode: str
    chatgpt_interpretation_distance: str
    template_origin: dict[str, Any]
    validator_result: dict[str, Any]
    runtime_text_hash: str
    visible_answer_hash: str
    diff_ratio: float
    truth_boundary: str = "Porównanie pokazuje różnicę tekstów. Nie rekonstruuje prywatnych myśli ani niewykonanego procesu."
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class RuntimeVisibleAnswerComparator:
    def __init__(self, root) -> None:
        self.reader = TurnTraceReader(root)
    def compare(self, trace_id: str | None = None) -> dict[str, Any]:
        checkpoint = self.reader.by_trace_id(trace_id) if trace_id else self.reader.latest()
        if not checkpoint:
            return {"schema_version": SCHEMA_VERSION, "found": False, "reason": "no_checkpoint_found"}
        rt = checkpoint.get('runtime_text') or ''
        vt = checkpoint.get('visible_text') or ''
        ratio = difflib.SequenceMatcher(None, rt, vt).ratio() if (rt or vt) else 1.0
        mode = checkpoint.get('response_generation_mode') or 'unknown'
        distance = 'none' if ratio > 0.98 else 'low' if ratio > 0.82 else 'medium' if ratio > 0.55 else 'high'
        return RuntimeVisibleComparison(SCHEMA_VERSION, checkpoint.get('trace_id') or '', rt, vt, mode, distance, checkpoint.get('template_origin') or {}, checkpoint.get('validator') or {}, hashlib.sha256(rt.encode('utf-8')).hexdigest(), hashlib.sha256(vt.encode('utf-8')).hexdigest(), round(ratio, 5)).to_dict()
