from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any
from latka_jazn.version import schema_version

from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.core.route_registry import RouteRegistryEntry
from latka_jazn.core.handlers.dictionary_lookup_handler import DictionaryLookupHandler
from latka_jazn.core.handlers.external_research_handler import ExternalResearchHandler
from latka_jazn.core.handlers.ordinary_dialogue_handler import OrdinaryDialogueHandler
from latka_jazn.core.handlers.runtime_diagnostic_handler import RuntimeDiagnosticHandler
from latka_jazn.core.handlers.package_runtime_status_handler import PackageRuntimeStatusHandler
from latka_jazn.core.handlers.runtime_source_handler import RuntimeSourceHandler
from latka_jazn.core.handlers.canon_source_handler import CanonSourceHandler
from latka_jazn.core.handlers.memory_audit_handler import MemoryAuditHandler
from latka_jazn.core.handlers.system_update_handler import SystemUpdateHandler
from latka_jazn.core.handlers.creative_text_handler import CreativeTextHandler
from latka_jazn.core.handlers.file_operation_handler import FileOperationHandler
from latka_jazn.core.handlers.identity_boundary_handler import IdentityBoundaryHandler
from latka_jazn.core.handlers.identity_runtime_truth_handler import IdentityRuntimeTruthHandler
from latka_jazn.core.handlers.practical_advice_handler import PracticalAdviceHandler
from latka_jazn.core.handlers.self_state_handler import SelfStateHandler
from latka_jazn.core.handlers.fallback_handler import FallbackHandler
from latka_jazn.core.handlers.runtime_activation_status_handler import RuntimeActivationStatusHandler
from latka_jazn.core.handlers.runtime_chat_mode_handler import RuntimeChatModeHandler
from latka_jazn.core.handlers.system_repair_plan_handler import SystemRepairPlanHandler
from latka_jazn.core.handlers.capability_status_handler import CapabilityStatusHandler
from latka_jazn.core.handlers.self_memory_recall_handler import SelfMemoryRecallHandler
from latka_jazn.core.handlers.user_memory_recall_handler import UserMemoryRecallHandler
from latka_jazn.core.handlers.direct_latka_voice_handler import DirectLatkaVoiceHandler
from latka_jazn.core.handlers.identity_memory_existence_handler import IdentityMemoryExistenceHandler
from latka_jazn.core.handlers.self_architecture_audit_handler import SelfArchitectureAuditHandler
from latka_jazn.core.handlers.presence_status_handler import PresenceStatusHandler
from latka_jazn.core.handlers.time_awareness_handler import TimeAwarenessHandler

SCHEMA_VERSION=schema_version('route_handler_dispatcher')

@dataclass(slots=True)
class RouteDispatchReport:
    schema_version: str
    requested_handler: str
    selected_handler: str
    route: str
    intent: str
    status: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    def to_dict(self): return asdict(self)

class RouteHandlerDispatcher:
    def __init__(self) -> None:
        handlers=[
            SelfArchitectureAuditHandler(), DictionaryLookupHandler(), ExternalResearchHandler(), OrdinaryDialogueHandler(), RuntimeDiagnosticHandler(), PackageRuntimeStatusHandler(), RuntimeSourceHandler(), CanonSourceHandler(), MemoryAuditHandler(), SystemUpdateHandler(),
            CreativeTextHandler(), FileOperationHandler(), IdentityBoundaryHandler(), IdentityRuntimeTruthHandler(), PracticalAdviceHandler(), SelfStateHandler(), PresenceStatusHandler(), TimeAwarenessHandler(), RuntimeActivationStatusHandler(), RuntimeChatModeHandler(), SystemRepairPlanHandler(), CapabilityStatusHandler(), UserMemoryRecallHandler(), SelfMemoryRecallHandler(), DirectLatkaVoiceHandler(), IdentityMemoryExistenceHandler(), FallbackHandler(),
        ]
        self.handlers_by_name={h.name:h for h in handlers}
        self.handlers_by_route={h.route:h for h in handlers}
    def dispatch(self, entry: RouteRegistryEntry, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx=dict(context or {})
        ctx.setdefault('intent', entry.intent)
        ctx.setdefault('route_entry', entry.to_dict())
        ctx.setdefault('required_components', entry.required_components)
        handler=self.handlers_by_name.get(entry.handler_name) or self.handlers_by_route.get(entry.route) or self.handlers_by_route['fallback']
        try:
            result=handler.handle(text, ctx)
            result.intent = result.intent or entry.intent
            if not result.required_components:
                result.required_components=list(entry.required_components)
            result.data.setdefault('dispatch_report', RouteDispatchReport(SCHEMA_VERSION, entry.handler_name, handler.name, entry.route, entry.intent, 'ok').to_dict())
            return result
        except Exception as exc:
            fb=self.handlers_by_route['fallback']
            result=fb.handle(text, {**ctx, 'body':'Handler runtime zgłosił błąd, więc zwracam jawny fallback zamiast udawać trafną trasę.'})
            result.errors.append({'handler': getattr(handler,'name','unknown'), 'error': repr(exc)})
            result.data.setdefault('dispatch_report', RouteDispatchReport(SCHEMA_VERSION, entry.handler_name, getattr(handler,'name','unknown'), entry.route, entry.intent, 'handler_error', [{'error':repr(exc)}]).to_dict())
            return result
    def to_dict(self)->dict[str, Any]:
        return {'schema_version':SCHEMA_VERSION,'handlers':sorted(self.handlers_by_name),'routes':sorted(self.handlers_by_route)}
