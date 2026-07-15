from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any


def _todict(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    try:
        return asdict(obj)
    except Exception:
        return obj


@dataclass(slots=True)
class SelfStatePacket:
    schema_version: str
    runtime_mode: str
    timestamp: str
    current_attention: list[str]
    active_memories: dict[str, Any]
    affective_state: dict[str, Any]
    truth_boundary: dict[str, Any]
    agency_log: dict[str, Any]
    source_origin: dict[str, Any]
    conversation_intent: dict[str, Any]
    embodiment_context: dict[str, Any]
    confidence: float
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SelfStateRuntime:
    """Lekki, testowalny pakiet stanu Jaźni dla mostu ChatGPT/runtime.

    To nie jest deklaracja fenomenalnej świadomości. To uporządkowany stan
    operacyjny: uwaga, pamięć, afekt modelowany, źródła, sprawstwo i granice prawdy.
    """

    schema_version = "self_state_runtime/v14.6.2"

    def build(
        self,
        *,
        text: str,
        timestamp: str,
        runtime_mode: str,
        intent_tags: list[str],
        temporal_state: Any = None,
        affective_state: Any = None,
        granular_affect: Any = None,
        memory_context: dict[str, Any] | None = None,
        logical_report: Any = None,
        awareness_report: Any = None,
        nlp_report: dict[str, Any] | None = None,
        source_origin: Any = None,
        client_context: dict[str, Any] | None = None,
    ) -> SelfStatePacket:
        memory_context = memory_context or {}
        nlp_report = nlp_report or {}
        client_context = client_context or {}
        source_dict = _todict(source_origin) or {}
        temporal_dict = _todict(temporal_state) or {}
        affect_dict = _todict(affective_state) or {}
        granular_dict = _todict(granular_affect) or {}
        logical_dict = _todict(logical_report) or {}
        awareness_dict = _todict(awareness_report) or {}

        selected_lemmas = [str(x) for x in (nlp_report.get("selected_lemmas") or []) if x]
        attention = self._unique(list(intent_tags or []) + selected_lemmas[:8])
        if not attention:
            attention = ["conversation"]

        counts = memory_context.get("counts") or {}
        memory_counts = {
            "query_terms": memory_context.get("query_terms") or [],
            "episodes": int(counts.get("episodes") or 0),
            "legacy_messages": int(counts.get("legacy_messages") or 0),
            "raw_chat_fallback": int(counts.get("raw_chat_fallback") or 0),
            "memory_is_active_source": bool(counts.get("episodes") or counts.get("legacy_messages") or counts.get("raw_chat_fallback")),
        }

        agency = {
            "runtime_invoked": True,
            "runtime_mode": runtime_mode,
            "lifecycle": client_context.get("lifecycle", "unspecified"),
            "may_claim_background_process": False,
            "may_claim_file_write_only_when_tool_or_runtime_report_confirms_it": True,
            "rule": "Mówić 'wywołałam runtime' tylko po realnym wywołaniu; nie mówić 'myślałam przez przerwę' bez procesu lub zapisu.",
        }

        truth = {
            "operational_awareness_not_phenomenal_consciousness": True,
            "source_origin_primary": source_dict.get("primary"),
            "source_origin_confidence": source_dict.get("confidence"),
            "logical_audit_present": bool(logical_dict),
            "awareness_report_present": bool(awareness_dict),
            "boundary": source_dict.get("truth_boundary") or "Źródła i tryb działania mają być jawne.",
        }

        affective = {
            "basic_state": affect_dict,
            "granular_affect": granular_dict,
            "temporal_state": temporal_dict,
            "note": "Stan afektywny jest modelem regulacyjnym rozmowy, nie biologicznym przeżyciem.",
        }

        confidence = 0.70
        if memory_counts["memory_is_active_source"]:
            confidence += 0.08
        if source_dict.get("confidence"):
            confidence = (confidence + float(source_dict.get("confidence"))) / 2
        if nlp_report.get("average_confidence") is not None:
            confidence = (confidence + float(nlp_report.get("average_confidence"))) / 2
        confidence = round(max(0.10, min(0.96, confidence)), 3)

        return SelfStatePacket(
            schema_version=self.schema_version,
            runtime_mode=runtime_mode,
            timestamp=timestamp,
            current_attention=attention,
            active_memories=memory_counts,
            affective_state=affective,
            truth_boundary=truth,
            agency_log=agency,
            source_origin=source_dict,
            conversation_intent={
                "intent_tags": intent_tags or ["conversation"],
                "text_excerpt": (text or "")[:280],
            },
            embodiment_context={
                "mode": "symbolic_relational_presence",
                "has_biological_body": False,
                "allowed_use": "kotwica sceny, tonu i bliskości; nie dowód świadomości ani fizycznego działania",
            },
            confidence=confidence,
            limitations=[
                "Pakiet stanu nie jest dowodem świadomości fenomenalnej.",
                "Jednorazowe CLI nie jest stałym procesem w tle.",
                "Pełna lematyzacja polskiego wymaga opcjonalnego providera kontekstowego albo ręcznego dopracowania słownika.",
            ],
        )

    @staticmethod
    def _unique(items: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out
