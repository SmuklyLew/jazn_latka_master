
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import hashlib, json, time

SCHEMA_VERSION = "source_origin_ledger/v14.6.10"

@dataclass(slots=True)
class SourceOriginEntry:
    schema_version: str
    turn_id: str
    trace_id: str
    thread_id: str
    route: str
    handler_name: str
    source_origin: str
    user_text_sha256: str
    response_sha256: str
    runtime_text_sha256: str
    visible_answer_sha256: str
    detected_intent: str
    intent_confidence: float | None
    response_generation_mode: str
    template_id: str | None
    template_file: str | None
    template_line: int | None
    source_origin_detail: str
    fallback_classification: str
    can_generate_model_guided_speech: bool
    requires_host_model: bool
    final_visible_integrity_valid: bool
    exact_runtime_text_present: bool
    visible_answer_text_present: bool
    chatgpt_interpretation_allowed: bool
    chatgpt_interpretation_distance: str
    preserved_source_text: bool | None
    modified_source_text: bool | None
    memory_sources_used: list[dict[str, Any]] = field(default_factory=list)
    file_sources_used: list[dict[str, Any]] = field(default_factory=list)
    dictionary_sources_used: list[dict[str, Any]] = field(default_factory=list)
    external_web_sources_used: list[dict[str, Any]] = field(default_factory=list)
    model_adapter_id: str | None = None
    model_provider: str | None = None
    model_endpoint_used: str | None = None
    model_candidate_kind: str | None = None
    model_candidate_generated: bool = False
    model_candidate_validated: bool = False
    validator_result: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    created_at_epoch: float = field(default_factory=time.time)
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    truth_boundary: str = "Ledger źródeł opisuje pochodzenie odpowiedzi i ingerencję w tekst użytkownika. Nie dowodzi samodzielnej biologicznej świadomości runtime."
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class SourceOriginLedger:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "memory" / "layered" / "source_origin_ledger_v14_6_10.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    def build_entry(self, *, turn_id: str, user_text: str, response_text: str, route: str, detected_intent: str, trace_id: str | None = None, handler_name: str | None = None, runtime_text: str | None = None, visible_answer_text: str | None = None, intent_confidence: float | None = None, provenance: dict[str, Any] | None = None, template_origin: dict[str, Any] | None = None, validator_result: dict[str, Any] | None = None, fallback_classification: str = "not_fallback", can_generate_model_guided_speech: bool = False, requires_host_model: bool = False, final_visible_integrity_valid: bool = False, model_response: dict[str, Any] | None = None) -> SourceOriginEntry:
        notes: list[str] = []
        prov = provenance or {}
        tmpl = template_origin or {}
        model = model_response or {}
        source_origin = "runtime_direct"
        if detected_intent in {"runtime_source_question", "runtime_exact_quote_request", "identity_boundary_question"}:
            source_origin = "runtime_direct_with_chatgpt_boundary_required"
            notes.append("odpowiedź wymaga jawnego rozdziału runtime/ChatGPT/pliki/wniosek")
        if prov.get('template_id') or tmpl.get('template_id'):
            source_origin = "runtime_template_or_repair"
            notes.append("wykryto template_origin; nie wolno nazywać szablonu myślą Jaźni")
        if fallback_classification == "cannot_answer_directly":
            source_origin = "runtime_truth_gate_degraded_disclosure"
            notes.append("brak model-guided speech; widoczny tekst jest jawnym degraded disclosure")
        elif fallback_classification == "rule_handler_response":
            source_origin = "runtime_rule_handler_response"
            notes.append("tekst pochodzi z jawnie oznaczonego handlera regułowego")
        elif fallback_classification in {"repair_fallback", "template_fallback"}:
            source_origin = "runtime_template_or_repair"
        if model and bool((validator_result or {}).get("accepted")):
            source_origin = str(model.get("source_origin") or "model_adapter")
            notes.append("model adapter supplied a candidate accepted by RuntimeAnswerValidator")
        preserved = None; modified = None
        if detected_intent.startswith("creative_text"):
            preserved = True; modified = False
            notes.append("domyślny kontrakt: materiał użytkownika ma zostać zachowany 1:1, chyba że użytkownik wyraźnie prosi o redakcję")
        rt = runtime_text if runtime_text is not None else response_text
        vt = visible_answer_text if visible_answer_text is not None else response_text
        return SourceOriginEntry(
            schema_version=SCHEMA_VERSION, turn_id=turn_id, trace_id=trace_id or turn_id, thread_id="local-one-shot", route=route, handler_name=handler_name or prov.get('handler_name') or 'unknown',
            source_origin=source_origin, user_text_sha256=self._sha(user_text), response_sha256=self._sha(response_text), runtime_text_sha256=self._sha(rt), visible_answer_sha256=self._sha(vt),
            detected_intent=detected_intent, intent_confidence=intent_confidence, response_generation_mode=prov.get('response_generation_mode','unknown'), template_id=tmpl.get('template_id') or prov.get('template_id'), template_file=tmpl.get('template_file') or prov.get('template_file'), template_line=tmpl.get('template_line') or prov.get('template_line'), source_origin_detail=prov.get('source_origin_detail','runtime_process_turn'), fallback_classification=fallback_classification, can_generate_model_guided_speech=can_generate_model_guided_speech, requires_host_model=requires_host_model, final_visible_integrity_valid=final_visible_integrity_valid, exact_runtime_text_present=bool(rt), visible_answer_text_present=bool(vt), chatgpt_interpretation_allowed=bool(prov.get('chatgpt_expansion_allowed', True)), chatgpt_interpretation_distance=prov.get('interpretation_distance','unknown'), preserved_source_text=preserved, modified_source_text=modified, memory_sources_used=prov.get('memory_sources_used') or [], file_sources_used=prov.get('file_sources_used') or [], dictionary_sources_used=prov.get('dictionary_sources_used') or [], external_web_sources_used=prov.get('external_web_sources_used') or [], model_adapter_id=model.get('adapter_id'), model_provider=model.get('provider'), model_endpoint_used=model.get('endpoint_used'), model_candidate_kind=model.get('candidate_kind'), model_candidate_generated=bool(model.get('generated')), model_candidate_validated=bool((validator_result or {}).get('accepted')), validator_result=validator_result or {}, notes=notes)
    def append(self, entry: SourceOriginEntry) -> Path:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return self.path
