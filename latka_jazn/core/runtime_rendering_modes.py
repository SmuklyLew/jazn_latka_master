from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("runtime_rendering_modes")

@dataclass(slots=True)
class RuntimeRenderingMode:
    mode: str
    show_exact_runtime_text: bool
    show_diagnostics: bool
    allow_natural_reply: bool
    require_source_boundary: bool
    reason: str
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Tryb renderowania decyduje, ile techniki pokazać użytkownikowi; nie zmienia faktów runtime."
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class RuntimeRenderingModeSelector:
    DIAGNOSTIC_MARKERS = ("runtime", "źródło", "zrodlo", "komenda", "jak brzmiała", "jak brzmiala", "fallback", "debug", "plik", "wersja")
    EXPORT_MARKERS = ("zip", "do pobrania", "paczka", "manifest", "sha256", "aktualizacja")
    def select(self, user_text: str, *, detected_intent: str = "unknown", client_context: dict[str, Any] | None = None) -> RuntimeRenderingMode:
        low=(user_text or '').lower(); ctx=client_context or {}
        if ctx.get('force_exact_runtime_text') or detected_intent in {'runtime_exact_quote_request','runtime_source_question'} or any(m in low for m in self.DIAGNOSTIC_MARKERS):
            return RuntimeRenderingMode('diagnostic_runtime_visible', True, True, False, True, 'użytkownik pyta o runtime/źródło/komendę albo exact text')
        if detected_intent.startswith('system_update') or any(m in low for m in self.EXPORT_MARKERS):
            return RuntimeRenderingMode('work_report_export', False, True, False, True, 'tryb pracy na paczce/manifestach/eksporcie')
        return RuntimeRenderingMode('natural_latka_voice', False, False, True, True, 'zwykła rozmowa: Łatka mówi naturalnie, bez raportu technicznego')
