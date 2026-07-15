from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
import hashlib

SCHEMA_VERSION = "response_generation_mode/v14.6.10"


class ResponseGenerationMode(StrEnum):
    RUNTIME_DYNAMIC = "runtime_dynamic"
    RUNTIME_TEMPLATE = "runtime_template"
    RUNTIME_REPAIR = "runtime_repair"
    RUNTIME_MEMORY_GROUNDED = "runtime_memory_grounded"
    RUNTIME_FILE_GROUNDED = "runtime_file_grounded"
    RUNTIME_DICTIONARY_GROUNDED = "runtime_dictionary_grounded"
    RUNTIME_EXTERNAL_RESEARCH_GROUNDED = "runtime_external_research_grounded"
    RUNTIME_MODEL_GUIDED = "runtime_model_guided"
    CHATGPT_INTERPRETATION_REQUIRED = "chatgpt_interpretation_required"
    CANNOT_ANSWER_DIRECTLY = "cannot_answer_directly"


@dataclass(slots=True)
class RuntimeResponseProvenance:
    schema_version: str
    response_generation_mode: str
    route_registry_id: str
    handler_name: str
    source_origin_detail: str
    runtime_support_level: str
    chatgpt_expansion_allowed: bool
    chatgpt_expansion_boundary: str
    interpretation_distance: str
    exact_runtime_text: str
    visible_answer_text: str | None = None
    runtime_text_hash: str | None = None
    visible_answer_hash: str | None = None
    template_id: str | None = None
    template_file: str | None = None
    template_line: int | None = None
    template_purpose: str | None = None
    memory_sources_used: list[dict[str, Any]] = field(default_factory=list)
    file_sources_used: list[dict[str, Any]] = field(default_factory=list)
    dictionary_sources_used: list[dict[str, Any]] = field(default_factory=list)
    external_web_sources_used: list[dict[str, Any]] = field(default_factory=list)
    truth_boundary: str = "Pochodzenie odpowiedzi opisuje źródła runtime i szablony. Nie dowodzi biologicznej świadomości ani myśli poza wykonanym procesem."

    def __post_init__(self) -> None:
        if self.runtime_text_hash is None:
            self.runtime_text_hash = hashlib.sha256((self.exact_runtime_text or '').encode('utf-8')).hexdigest()
        if self.visible_answer_text is not None and self.visible_answer_hash is None:
            self.visible_answer_hash = hashlib.sha256((self.visible_answer_text or '').encode('utf-8')).hexdigest()

    def with_visible_text(self, text: str) -> "RuntimeResponseProvenance":
        return RuntimeResponseProvenance(
            schema_version=self.schema_version,
            response_generation_mode=self.response_generation_mode,
            route_registry_id=self.route_registry_id,
            handler_name=self.handler_name,
            source_origin_detail=self.source_origin_detail,
            runtime_support_level=self.runtime_support_level,
            chatgpt_expansion_allowed=self.chatgpt_expansion_allowed,
            chatgpt_expansion_boundary=self.chatgpt_expansion_boundary,
            interpretation_distance=self.interpretation_distance,
            exact_runtime_text=self.exact_runtime_text,
            visible_answer_text=text,
            template_id=self.template_id,
            template_file=self.template_file,
            template_line=self.template_line,
            template_purpose=self.template_purpose,
            memory_sources_used=list(self.memory_sources_used),
            file_sources_used=list(self.file_sources_used),
            dictionary_sources_used=list(self.dictionary_sources_used),
            external_web_sources_used=list(self.external_web_sources_used),
            truth_boundary=self.truth_boundary,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_runtime_provenance(*, body: str, route: str, detected_intent: str, handler_name: str, template_origin: dict[str, Any] | None = None, repair: bool = False, model_guided: bool = False, fallback_classification: str | None = None, source_origin_detail: str | None = None, memory_sources: list[dict[str, Any]] | None = None, file_sources: list[dict[str, Any]] | None = None, dictionary_sources: list[dict[str, Any]] | None = None, external_web_sources: list[dict[str, Any]] | None = None) -> RuntimeResponseProvenance:
    template = template_origin or {}
    has_template = bool(template.get('template_id'))
    if fallback_classification == ResponseGenerationMode.CANNOT_ANSWER_DIRECTLY.value:
        mode = ResponseGenerationMode.CANNOT_ANSWER_DIRECTLY.value
        support = 'truthful_degraded_disclosure_without_model_guided_speech'
        distance = 'none'
        expansion_allowed = True
        boundary = 'Runtime ujawnia brak model-guided speech; host może wygenerować język dopiero po zachowaniu tego oznaczenia.'
    elif model_guided:
        mode = ResponseGenerationMode.RUNTIME_MODEL_GUIDED.value
        support = 'model_language_generated_from_runtime_cognitive_context'
        distance = 'low'
        expansion_allowed = False
        boundary = 'Model sformułował język, ale stan, pamięć, tożsamość, polityka i walidacja należą do runtime Jaźni.'
    elif repair:
        mode = ResponseGenerationMode.RUNTIME_REPAIR.value
        support = 'repair_generated_from_validator_and_route'
        distance = 'low'
        expansion_allowed = True
        boundary = 'ChatGPT może wyjaśnić naprawę, ale nie może udawać, że pierwotny template był dynamiczną myślą.'
    elif dictionary_sources:
        mode = ResponseGenerationMode.RUNTIME_DICTIONARY_GROUNDED.value
        support = 'dictionary_grounded'
        distance = 'low'
        expansion_allowed = True
        boundary = 'Dopowiedzenie wolno oprzeć tylko na oznaczonych źródłach słownikowych.'
    elif file_sources:
        mode = ResponseGenerationMode.RUNTIME_FILE_GROUNDED.value
        support = 'file_grounded'
        distance = 'low'
        expansion_allowed = True
        boundary = 'Dopowiedzenie wolno oprzeć tylko na oznaczonych plikach.'
    elif memory_sources:
        mode = ResponseGenerationMode.RUNTIME_MEMORY_GROUNDED.value
        support = 'memory_content_grounded'
        distance = 'low'
        expansion_allowed = True
        boundary = 'Dopowiedzenie musi wskazać, z których fragmentów pamięci korzysta.'
    elif has_template:
        mode = ResponseGenerationMode.RUNTIME_TEMPLATE.value
        support = 'template_only'
        distance = 'medium'
        expansion_allowed = True
        boundary = 'ChatGPT może interpretować tylko po jawnym oznaczeniu, że runtime dał szablon.'
    else:
        mode = ResponseGenerationMode.RUNTIME_DYNAMIC.value
        support = 'runtime_body_direct'
        distance = 'none'
        expansion_allowed = True
        boundary = 'Warstwa widoczna może przenieść odpowiedź i timestamp bez zmiany sensu.'
    if detected_intent in {'runtime_source_question', 'runtime_exact_quote_request'} and has_template:
        mode = ResponseGenerationMode.CHATGPT_INTERPRETATION_REQUIRED.value
        support = 'weak_template_only'
        distance = 'high'
        boundary = 'Widoczna odpowiedź musi oddzielić dokładny tekst runtime od interpretacji ChatGPT.'
    return RuntimeResponseProvenance(
        schema_version=SCHEMA_VERSION,
        response_generation_mode=mode,
        route_registry_id=str(route or 'unknown'),
        handler_name=handler_name or 'unknown_handler',
        source_origin_detail=source_origin_detail or (
            'runtime_turn_truth_gate_degraded_disclosure'
            if fallback_classification == ResponseGenerationMode.CANNOT_ANSWER_DIRECTLY.value
            else ('runtime_model_guided_synthesis' if model_guided else ('runtime_process_turn' if not has_template else 'runtime_process_turn_with_template_body'))
        ),
        runtime_support_level=support,
        chatgpt_expansion_allowed=expansion_allowed,
        chatgpt_expansion_boundary=boundary,
        interpretation_distance=distance,
        exact_runtime_text=body or '',
        template_id=template.get('template_id'),
        template_file=template.get('template_file'),
        template_line=template.get('template_line'),
        template_purpose=template.get('template_purpose'),
        memory_sources_used=memory_sources or [],
        file_sources_used=file_sources or [],
        dictionary_sources_used=dictionary_sources or [],
        external_web_sources_used=external_web_sources or [],
    )
