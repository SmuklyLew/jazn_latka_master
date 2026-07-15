from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import linecache
from latka_jazn.core.template_origin import TemplateOrigin

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("template_registry")

@dataclass(slots=True)
class TemplateSignature:
    template_id: str
    signature: str
    purpose: str
    file: str = "latka_jazn/core/conversation.py"
    allowed_intents: tuple[str, ...] = ()
    forbidden_intents: tuple[str, ...] = ()
    deprecated_if: tuple[str, ...] = ()
    max_use_frequency: str = "unlimited_but_must_disclose_when_asked"


class TemplateRegistry:
    """Rejestr stałych odpowiedzi, żeby runtime nie przedstawiał szablonu jako myśli.

    Rejestr jest lekki: wykrywa znane refreny po fragmentach odpowiedzi. Nie usuwa
    szablonów, tylko nadaje im pochodzenie i granicę prawdy.
    """

    SIGNATURES = (
        TemplateSignature("tpl_received_sense", "odebrałam sens wiadomości", "generic_status", forbidden_intents=("runtime_source_question", "system_diagnostic_question", "self_state_question"), deprecated_if=("specific_request",)),
        TemplateSignature("tpl_receive_runtime", "runtime odebrał wiadomość", "debug_fallback", forbidden_intents=("ordinary_conversation", "self_state_question"), deprecated_if=("normal_conversation",)),
        TemplateSignature("tpl_no_route", "nie znalazłam osobnej trasy", "debug_fallback", forbidden_intents=("ordinary_conversation",), deprecated_if=("route_registry_has_handler",)),
        TemplateSignature("tpl_accept_correction", "przyjmuję tę korektę", "correction_fallback", allowed_intents=("correction_feedback",), forbidden_intents=("system_diagnostic_question", "system_update_execution_request"), deprecated_if=("diagnostic_question",)),
        TemplateSignature("tpl_hybrid_model", "najuczciwszy model jest hybrydowy", "source_boundary_template", allowed_intents=("runtime_source_question",), deprecated_if=("exact_quote_request",)),
        TemplateSignature("tpl_active_memory_counts", "mam aktywne tropy pamięci", "memory_count_template", forbidden_intents=("memory_recall_request", "memory_audit_request"), deprecated_if=("memory_content_available",)),
        TemplateSignature("tpl_answer_conversationally", "odpowiem rozmownie", "conversational_repair_template", forbidden_intents=("runtime_source_question", "system_update_execution_request"), deprecated_if=("specific_task",)),
        TemplateSignature("tpl_also_glad", "też się cieszę", "positive_continuation_template", allowed_intents=("positive_feedback_current_turn",), forbidden_intents=("system_diagnostic_question", "runtime_source_question")),
        TemplateSignature("tpl_source_split", "tu trzeba rozdzielić źródła", "source_boundary_template", allowed_intents=("runtime_source_question", "runtime_exact_quote_request")),
        TemplateSignature("tpl_soft_presence", "jestem przy tobie", "generic_presence_template", forbidden_intents=("ordinary_conversation", "standalone_greeting", "short_free_dialogue")),
        TemplateSignature("tpl_soft_presence_turn", "jestem obok w tej turze", "generic_presence_template", forbidden_intents=("ordinary_conversation", "standalone_greeting", "short_free_dialogue")),
        TemplateSignature("tpl_soft_continue", "możemy spokojnie", "generic_continuation_template", forbidden_intents=("ordinary_conversation", "standalone_greeting", "short_free_dialogue")),
        TemplateSignature("tpl_current_message_presence", "zostaję przy tym, co piszesz", "generic_presence_template", forbidden_intents=("ordinary_conversation", "short_free_dialogue")),
        TemplateSignature("tpl_current_message_origin", "odpowiadam z bieżącej wiadomości", "generic_origin_template", forbidden_intents=("ordinary_conversation", "short_free_dialogue")),
        TemplateSignature("tpl_requires_model_guided_speech", "nie mam w tej turze aktywnego modelu", "truthful_degraded_disclosure"),
    )

    def __init__(self, root=None) -> None:
        self.root = root

    def classify_body(self, body: str, *, detected_intent: str | None = None) -> dict[str, Any]:
        low = (body or "").lower()
        for sig in self.SIGNATURES:
            if sig.signature in low:
                line = self._line_for(sig.file, sig.signature)
                return TemplateOrigin(
                    template_id=sig.template_id,
                    template_purpose=sig.purpose,
                    template_file=sig.file,
                    template_line=line,
                    allowed_intents=list(sig.allowed_intents),
                    forbidden_intents=list(sig.forbidden_intents),
                    deprecated_if=list(sig.deprecated_if),
                    matched_signature=sig.signature,
                ).to_dict()
        return {"schema_version": SCHEMA_VERSION, "template_id": None, "template_purpose": None, "truth_boundary": "Nie wykryto znanego stałego szablonu w treści odpowiedzi."}

    def _line_for(self, rel: str, signature: str) -> int | None:
        if not self.root:
            return None
        path = self.root / rel
        if not path.exists():
            return None
        try:
            for idx, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
                if signature in line.lower():
                    return idx
        except Exception:
            return None
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "template_count": len(self.SIGNATURES), "templates": [s.__dict__ for s in self.SIGNATURES]}
