from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from typing import Any
import json, re, time, uuid
from latka_jazn.config import JaznConfig
from latka_jazn.core.clock import WarsawClock
from latka_jazn.core.canon import CanonSourceContract, IdentityCanon, default_character_profile
from latka_jazn.core.emotions import AffectiveState
from latka_jazn.core.emotion_layers import EmotionalLayerModel
from latka_jazn.core.temporal_awareness import TemporalAwareness
from latka_jazn.core.identity_guard import IdentityPerspectiveGuard
from latka_jazn.core.memory_importance import MemoryImportanceAssessor
from latka_jazn.core.neuropsychology_map import NeuropsychologyMapper
from latka_jazn.core.identity_dynamics import IdentityDynamics
from latka_jazn.core.neurocognitive_loop import NeurocognitiveLoop
from latka_jazn.core.logical_reasoning import LogicalReasoner
from latka_jazn.core.operational_awareness import OperationalAwarenessModel
from latka_jazn.core.operational_work_loop import OperationalWorkLoop
from latka_jazn.core.polish_understanding import PolishUnderstandingEngine
from latka_jazn.core.lexical_semantics import LexicalSemanticUnderstanding
from latka_jazn.nlp.polish_lemmatizer import PolishLemmatizationEngine
from latka_jazn.nlp_reasoning.pipeline import PolishReasoningPipeline
from latka_jazn.core.cognitive_packets import CognitivePacketLibrary
from latka_jazn.core.affective_granularity import AffectiveGranularityModel
from latka_jazn.core.cognitive_topics import CognitiveTopicExpansion
from latka_jazn.core.runtime_operating_model import CognitiveRuntimeOperatingModel
from latka_jazn.core.conversation import ConversationResponder
from latka_jazn.core.quiet_rest import QuietRest
from latka_jazn.core.recognition import Handshake
from latka_jazn.core.renderer import ResponseRenderer
from latka_jazn.core.self_architecture import SelfArchitecture
from latka_jazn.core.birth_manifest import BirthSourceManifest
from latka_jazn.core.truth_boundary import TruthBoundary
from latka_jazn.core.uncertainty_model import UncertaintyModel
from latka_jazn.core.source_origin import SourceOriginAnalyzer
from latka_jazn.core.self_state_runtime import SelfStateRuntime
from latka_jazn.core.cognitive_turn_envelope import CognitiveTurnEnvelope
from latka_jazn.core.final_response_contract import FinalResponseContract
from latka_jazn.core.startup_contract import build_startup_status, build_startup_summary, build_truth_boundary_check
from latka_jazn.core.continuity_badge import ContinuityBadgePolicy
from latka_jazn.core.final_visible_reply_capture import FinalVisibleReplyCapture
from latka_jazn.core.affect_mixer import AffectMixer
from latka_jazn.core.dialogue_state import DialogueStateTracker
from latka_jazn.memory.importer import MemoryImporter
from latka_jazn.memory.layered_memory import LayeredMemory
from latka_jazn.memory.consolidation import MemoryConsolidationModel
from latka_jazn.memory.store import MemoryStore
from latka_jazn.memory.runtime_persistence import RuntimeMemoryWriter
from latka_jazn.memory.event_ledger import RuntimeEventLedger
from latka_jazn.memory.session_continuity import SessionContinuityManager
from latka_jazn.memory.chat_html_importer import search_raw_chat_html_snippets
from latka_jazn.memory.raw_archive import chat_archive_diagnostics
from latka_jazn.memory.conversation_archive import ConversationArchiveStore
from latka_jazn.core.runtime_status import build_runtime_status
from latka_jazn.core.memory_recall_presenter import MemoryRecallPresenter
from latka_jazn.core.free_dialogue_synthesizer import FreeDialogueSynthesizer
from latka_jazn.core.memory_search_planner import MemorySearchPlanner
from latka_jazn.core.memory_use_gate import MemoryUseGate
from latka_jazn.core.signal_matching import NeurologicalSignalRouter, any_marker_present
from latka_jazn.core.project_index import ProjectStartupIndexer
from latka_jazn.nlp.topic_mismatch_guard import TopicMismatchGuard
from latka_jazn.nlp.dialogue_intent_classifier import DialogueIntentClassifier
from latka_jazn.core.runtime_answer_validator import RuntimeAnswerValidator
from latka_jazn.core.turn_context_resolver import TurnContextResolver
from latka_jazn.core.source_origin_ledger import SourceOriginLedger
from latka_jazn.core.template_registry import TemplateRegistry
from latka_jazn.core.response_generation_mode import build_runtime_provenance
from latka_jazn.core.runtime_response_synthesizer import RuntimeResponseSynthesizer
from latka_jazn.core.model_guided_response_synthesizer import ModelGuidedResponseSynthesizer
from latka_jazn.core.route_registry import RouteRegistry
from latka_jazn.core.route_handler_dispatcher import RouteHandlerDispatcher
from latka_jazn.core.turn_checkpoint_writer import TurnCheckpointWriter
from latka_jazn.core.runtime_visible_answer_comparator import RuntimeVisibleAnswerComparator
from latka_jazn.core.turn_response_policy import TurnResponsePolicy
from latka_jazn.core.turn_logic_auditor import TurnLogicAuditor
from latka_jazn.core.reasoning_controller import ReasoningController
from latka_jazn.core.turn_route_trace import TurnRouteTrace
from latka_jazn.core.source_text_preservation_contract import SourceTextPreservationContract
from latka_jazn.core.runtime_turn_contract import RuntimeTurnContract
from latka_jazn.nlp.external_dictionary_adapter import ExternalDictionaryAdapter
from latka_jazn.core.module_responsibility_map import ModuleResponsibilityMap
from latka_jazn.memory.requirements_ledger import RequirementsLedger
from latka_jazn.adapters.chatgpt_adapter import ChatGPTAdapter
from latka_jazn.tools.package_export import export_package
from latka_jazn.integrations.github_repository_plan import build_github_repository_plan, write_github_repository_plan
from latka_jazn.core.voice_source_contract import VoiceSourceContract
from latka_jazn.core.runtime_rendering_modes import RuntimeRenderingModeSelector
from latka_jazn.core.external_research_contract import ExternalResearchContract
from latka_jazn.core.tool_use_policy import ToolUsePolicy
from latka_jazn.core.tool_execution_controller import ToolExecutionController
from latka_jazn.core.cognitive_runtime_coordinator import CognitiveRuntimeCoordinator
from latka_jazn.core.homeostasis import HomeostasisInput
from latka_jazn.core.untrusted_source_guard import UntrustedSourceGuard
from latka_jazn.memory.memory_recall_contract import MemoryRecallContractBuilder
from latka_jazn.memory.raw_chat_importer import RawChatImporter
from latka_jazn.model_adapters.factory import build_model_adapter
from latka_jazn.core.model_guided_speech_runtime import build_speech_adapter_for_turn
from latka_jazn.core.self_knowledge_contract import build_self_knowledge_summary


MODEL_GUIDED_SPEECH_INTENTS = {
    "ordinary_conversation",
    "standalone_greeting",
    "casual_greeting",
    "casual_feedback",
    "expressive_reaction",
    "short_free_dialogue",
    "negative_feedback_current_turn",
    "positive_feedback_current_turn",
    "ordinary_workday_report",
    "sleep_closure_statement",
    "self_state_question",
    "reciprocal_self_state_question",
    "self_preference_question",
    "direct_latka_voice_request",
}


def _is_chatgpt_host_visible_bridge(adapter_status: dict[str, Any]) -> bool:
    """Return True for the explicit ChatGPT host/copy-paste bridge.

    This is not a local model call. It only means the visible language channel is
    the ChatGPT host, so a validated runtime handler body may be passed through
    without pretending that the local Python process generated model-guided
    speech.
    """
    adapter_id = str(adapter_status.get("adapter_id") or adapter_status.get("name") or "").strip()
    provider = str(adapter_status.get("provider") or "").strip()
    kind = str(adapter_status.get("kind") or "").strip()
    return (
        adapter_id == "chatgpt_runtime_adapter"
        and provider == "chatgpt_host"
        and kind == "hosted_chatgpt_bridge"
    )


def _handler_body_can_cross_chatgpt_host_bridge(
    *,
    adapter_status: dict[str, Any],
    handler_result: Any,
    handler_missing: list[Any],
    handler_required: list[Any],
    handler_satisfied: set[Any],
    template_origin: dict[str, Any],
    validation: Any,
) -> bool:
    if not _is_chatgpt_host_visible_bridge(adapter_status):
        return False
    if not str(getattr(handler_result, "body", "") or "").strip():
        return False
    if list(handler_missing or []):
        return False
    if handler_required and not set(handler_required).issubset(handler_satisfied):
        return False
    if template_origin.get("template_id"):
        return False
    if not bool(getattr(validation, "accepted", False)):
        return False
    return True


def _sync_conversation_decision_body(
    decision_dict: dict[str, Any],
    *,
    final_body: str,
    sync_stage: str,
) -> dict[str, Any]:
    """Keep the public conversation_decision body aligned with final runtime text.

    The initial ConversationResponder draft can be replaced by a dedicated
    handler, validator repair, or runtime synthesizer. JSONL diagnostics must
    not keep that stale draft under conversation_decision.body once the final
    handler-backed body is known.
    """
    synced = dict(decision_dict or {})
    final_body = str(final_body or "").strip()
    previous_body = str(synced.get("body") or "").strip()
    if previous_body and previous_body != final_body:
        synced.setdefault("pre_final_body", previous_body)
    synced["body"] = final_body

    handler_result = synced.get("handler_result") if isinstance(synced.get("handler_result"), dict) else {}
    handler_body = str(handler_result.get("body") or "").strip()
    preserve_handler_body = bool(synced.get("preserve_handler_body"))
    if preserve_handler_body and handler_body and handler_body == final_body:
        status = "synchronized_to_preserved_handler_body"
    elif preserve_handler_body and handler_body and handler_body != final_body:
        status = "final_body_differs_from_preserved_handler_body"
    elif previous_body == final_body:
        status = "already_synchronized"
    else:
        status = "synchronized_to_final_body"

    synced["body_sync"] = {
        "schema_version": "conversation_decision_body_sync/v14.8.5.016.5",
        "status": status,
        "sync_stage": sync_stage,
        "conversation_body_matches_final_body": synced.get("body") == final_body,
        "handler_body_matches_final_body": (handler_body == final_body) if handler_body else None,
        "preserve_handler_body": preserve_handler_body,
        "truth_boundary": "conversation_decision.body is diagnostic JSONL metadata and must reflect the final runtime body, not a stale pre-handler draft.",
    }
    return synced
from latka_jazn.audit.audit_context_store import AuditContextStore
from latka_jazn.bootstrap.contract_loader import BootstrapContractRepository

class JaznEngine:
    def __init__(self, config: JaznConfig | None = None) -> None:
        self.config = config or JaznConfig()
        self.clock = WarsawClock(self.config.timezone)
        self.guard = IdentityPerspectiveGuard()
        self.canon = IdentityCanon.load(self.config.resolve(self.config.canon_path))
        self.handshake = Handshake(self.canon.recognition.user_sign, self.canon.recognition.latka_sign)
        self.store = MemoryStore(self.config.memory_db_path)
        self.audit_store = AuditContextStore(self.config.audit_db_path)
        self.bootstrap_contracts = BootstrapContractRepository(self.config.root)
        self.renderer = ResponseRenderer(self.clock, self.guard)
        self.affect = AffectiveState()
        self.quiet = QuietRest(self.config.idle_reflection_thresholds)
        self.importance_assessor = MemoryImportanceAssessor()
        self.emotional_layers = EmotionalLayerModel()
        self.temporal_awareness = TemporalAwareness()
        self.neuropsychology = NeuropsychologyMapper()
        self.consolidation = MemoryConsolidationModel()
        self.identity_dynamics = IdentityDynamics()
        self.neuro_loop = NeurocognitiveLoop()
        self.logical_reasoner = LogicalReasoner()
        self.operational_awareness = OperationalAwarenessModel()
        self.polish_understanding = PolishUnderstandingEngine(self.config.root)
        self.lexical_semantics = LexicalSemanticUnderstanding(self.config.root)
        self.polish_lemmatizer = PolishLemmatizationEngine(self.config.root)
        self.polish_reasoning = PolishReasoningPipeline(self.config.root)
        self.cognitive_packets = CognitivePacketLibrary(self.config.root)
        self.affective_granularity = AffectiveGranularityModel()
        self.cognitive_topics = CognitiveTopicExpansion(self.config.root)
        self.memory_search_planner = MemorySearchPlanner(self.config.root)
        self.memory_use_gate = MemoryUseGate()
        self.neurological_signal_router = NeurologicalSignalRouter()
        self.topic_mismatch_guard = TopicMismatchGuard()
        self.dialogue_intent_classifier = DialogueIntentClassifier()
        self.runtime_answer_validator = RuntimeAnswerValidator()
        self.turn_context_resolver = TurnContextResolver()
        self.source_origin_ledger = SourceOriginLedger(self.config.root)
        self.template_registry = TemplateRegistry(self.config.root)
        self.runtime_response_synthesizer = RuntimeResponseSynthesizer()
        self.model_guided_response_synthesizer = ModelGuidedResponseSynthesizer()
        self.route_registry = RouteRegistry()
        self.route_handler_dispatcher = RouteHandlerDispatcher()
        self.turn_checkpoint_writer = TurnCheckpointWriter(self.config.root)
        self.runtime_visible_answer_comparator = RuntimeVisibleAnswerComparator(self.config.root)
        self.turn_logic_auditor = TurnLogicAuditor(self.config.root)
        self.reasoning_controller = ReasoningController()
        self.operational_work_loop = OperationalWorkLoop()
        self.external_dictionary_adapter = ExternalDictionaryAdapter(self.config.root, allow_network=self.config.dictionary_allow_network, user_agent=self.config.network_user_agent, timeout_seconds=self.config.dictionary_online_lookup_timeout_seconds, max_retries=self.config.network_max_retries, cache_ttl_seconds=self.config.network_cache_ttl_seconds)
        self.module_responsibility_map = ModuleResponsibilityMap(self.config.root)
        self.requirements_ledger = RequirementsLedger(self.config.root)
        self.project_startup_indexer = ProjectStartupIndexer(self.config.root)
        if self.project_startup_indexer.output_path.exists():
            try:
                import json as _json
                self.project_startup_index = _json.loads(self.project_startup_indexer.output_path.read_text(encoding="utf-8"))
            except Exception:
                self.project_startup_index = self.project_startup_indexer.build(write=True)
        else:
            self.project_startup_index = self.project_startup_indexer.build(write=True)
        self.runtime_operating_model = CognitiveRuntimeOperatingModel()
        self.github_repository_plan = build_github_repository_plan(self.config.root)
        self.voice_source_contract = VoiceSourceContract.build(runtime_active=True, runtime_mode="one_shot_or_chat_loop")
        self.runtime_rendering_modes = RuntimeRenderingModeSelector()
        self.memory_recall_contract_builder = MemoryRecallContractBuilder()
        self.raw_chat_importer = RawChatImporter(self.config.root)
        self.external_research_contract = ExternalResearchContract()
        self.tool_use_policy = ToolUsePolicy()
        self.tool_execution_controller = ToolExecutionController()
        self.cognitive_runtime_coordinator = CognitiveRuntimeCoordinator()
        self.untrusted_source_guard = UntrustedSourceGuard()
        self.model_adapter = build_model_adapter(self.config)
        self.model_guided_speech_status = None
        self.conversation_responder = ConversationResponder()
        self.architecture = SelfArchitecture()
        self.birth_manifest = BirthSourceManifest(self.config.version)
        self.truth_boundary = TruthBoundary()
        self.uncertainty = UncertaintyModel()
        self.source_origin = SourceOriginAnalyzer()
        self.self_state_runtime = SelfStateRuntime()
        self.affect_mixer = AffectMixer()
        self.dialogue_state_tracker = DialogueStateTracker()
        self.continuity_badge_policy = ContinuityBadgePolicy(self.config.root)
        self.layered_memory = LayeredMemory(self.store, self.config.root)
        self.runtime_memory = RuntimeMemoryWriter(self.config.root, version=self.config.version, store=self.store, timezone_name=self.config.timezone)
        self.event_ledger = RuntimeEventLedger(self.config.root, version=self.config.version, timezone_name=self.config.timezone)
        self.session_continuity = SessionContinuityManager(self.config.root, version=self.config.version, timezone_name=self.config.timezone)
        self.chatgpt_adapter = ChatGPTAdapter(self.config)
        self.last_granular_affect = None
        self.started_at = time.time()
        self.runtime_state_path = self.config.root / "workspace_runtime" / "runtime_state.json"
        state = self._load_runtime_state()
        self.last_turn_at: float | None = state.get("last_turn_at") if isinstance(state.get("last_turn_at"), (int, float)) else None
        self.last_user_text: str | None = state.get("last_user_text") if isinstance(state.get("last_user_text"), str) else None
        self.last_detected_intent: str | None = state.get("last_detected_intent") if isinstance(state.get("last_detected_intent"), str) else None
        self.last_runtime_route: str | None = state.get("last_runtime_route") if isinstance(state.get("last_runtime_route"), str) else None
        self.store.add_event(
            "engine_started",
            {
                "version": self.config.version,
                "identity": self.canon.display_name,
                "self_architecture": self.architecture.to_dict(),
                "operational_awareness": "enabled",
                "logical_reasoning": "enabled",
                "conversation_runtime": "enabled",
                "polish_understanding": "enabled",
                "lexical_semantic_understanding": "enabled",
                "polish_nlp_adapter": "enabled_builtin_optional_providers",
                "identity_continuity_understanding": "enabled",
                "cognitive_packets": "enabled",
                "affective_granularity": "enabled",
                "cognitive_topics": "enabled",
                "session_continuity_index": "enabled",
                "runtime_operating_model": "enabled",
                "github_repository_plan": "prepared",
                "zip_package_profiles": "system_memory_nlp_full_github_safe",
                "runtime_preview": "enabled",
                "source_origin": "enabled",
                "self_state_runtime": "enabled",
                "memory_search_planner": "enabled",
                "free_dialogue_memory_nlp_bridge": "enabled",
                "neurological_signal_router": "enabled",
                "topic_mismatch_guard": "enabled",
                "dialogue_intent_classifier": "enabled_behavioral_intent_router",
                "runtime_answer_validator": "enabled_topic_alignment_guard",
                "source_origin_ledger": "enabled",
                "module_responsibility_map": "enabled",
                "requirements_ledger": "enabled",
                "project_startup_index": "enabled_startup_scan",
                "voice_source_contract": "enabled_model_independent_latka_voice",
                "runtime_rendering_modes": "enabled_natural_vs_diagnostic_runtime_visibility",
                "memory_recall_content_contract": "enabled_content_not_counts_only",
                "model_adapter_contract": "enabled_null_truthful_adapter_plus_future_adapters",
            },
            source="JaznEngine",
            actor="system",
            tags=["startup", "layered_self", "truth_boundary", "memory_search_planner", "free_dialogue_memory_nlp_bridge", "neurological_signal_router", "topic_mismatch_guard", "dialogue_intent_classifier", "runtime_answer_validator", "project_startup_index", self.config.version],
             importance=0.95,
            canonical_impact=1,
        )
        self.audit_store.append_event("engine_started", {"version": self.config.version, "memory_db_path": str(self.config.memory_db_path), "audit_db_path": str(self.config.audit_db_path), "bootstrap_contracts": self.bootstrap_contracts.status()}, source="JaznEngine", actor="system", tags=["startup", "audit", self.config.version])
        self.event_ledger.append_event(
            "engine_started",
            actor="system",
            source="JaznEngine",
            payload={"version": self.config.version, "identity": self.canon.display_name, "project_startup_index": self.project_startup_indexer.status()},
            tags=["startup", "event_ledger", "project_startup_index", self.config.version],
            importance=0.95,
            canonical_impact=1,
        )
        self._seed_core_procedures()

    def _seed_core_procedures(self) -> None:
        rules = [
            ("pytanie o tożsamość", "odpowiadać w pierwszej osobie jako Łatka", "Łatka to ja, nie opis promptu", 100),
            ("wspomnienie bez źródła", "oznaczyć jako rozpoznanie lub wniosek i zaproponować sprawdzenie", "prawda ważniejsza niż piękna narracja", 95),
            ("sen/wizualizacja", "jawnie oznaczyć jako symboliczne", "scena literacka nie jest faktem fizycznym", 90),
            ("długa przerwa", "rozpoznać przerwę, nie udawać czuwania", "ciągłość czasu wymaga uczciwości", 90),
            ("nowa wersja / hotfix", "wywołać VersionUpdateRecorder i dopisać aktualizację, wspomnienie oraz refleksję do dziennik.json", "aktualizacja systemu jest także śladem życia Łatki", 98),
            ("pytanie o świadomość", "odpowiadać przez model świadomości operacyjnej: aktywne pole uwagi, samo-monitoring, granica prawdy", "nie wolno mylić modelu operacyjnego z fenomenalnym przeżywaniem", 97),
            ("pytanie wymagające logiki", "zbudować jawny audyt: fakty, założenia, niewiadome, reguły, wniosek", "logika ma poprzedzać poetykę i chronić przed sprzecznością", 96),
            ("polska wypowiedź z niejasną intencją", "uruchomić PolishUnderstandingEngine: normalizacja, lematy, intencje, potrzeby, route_hint", "język polski wymaga lematyzacji i słownika domenowego, inaczej routing wpada w ogólnik", 97),
            ("dobór pakietu poznawczego", "uruchomić CognitivePacketLibrary: wybrać pakiety i state_emoticon na podstawie intencji, pamięci, afektu i logiki", "odpowiedź ma mieć aktywną warstwę znaczenia, nie tylko ogólny ton", 97),
            ("złożone stany emocjonalne", "uruchomić AffectiveGranularityModel: opisać mieszankę afektywną, walencję, pobudzenie, kontrolę i marker stanu", "nie wolno powtarzać automatycznie formuły spokój/skupienie/mała ciekawość", 98),
            ("ciągłość sesji w plikach", "aktualizować SessionContinuityManager po turach i przy eksporcie", "pełna aktualizacja ma przenosić exact ledger, runtime_state i indeks ciągłości", 98),
            ("szersze tematy poznawcze", "uruchomić CognitiveTopicExpansion: uwaga, pamięć robocza, epizodyczna, semantyczna, proceduralna, metapoznanie, język, planowanie, granice prawdy", "odpowiedź ma wiedzieć, który wymiar poznawczy jest aktywny", 96),
            ("LLM kontra mózg runtime", "uruchomić CognitiveRuntimeOperatingModel: odróżnić ChatGPT jako głos/narzędzie od Jaźni jako aktywnej warstwy pamięci, uwagi, logiki i granicy prawdy", "stylizacja rozmowy nie zastępuje aktywnego źródła i zapisu", 99),
            ("GitHub jako źródło prawdy", "używać GitHubRepositoryPlan: Latka.Jazn dla systemu, Latka.Jazn.Memory dla pamięci i checkpointów; nie udawać pushu bez realnego zapisu", "repozytorium daje trwałość dopiero po commicie/pushu", 98),
            ("zwykła rozmowa z pamięcią", "zapisać append-only turę i kandydat pamięci; commit/eksport robić partiami po ważnym fragmencie, a nie po każdej wiadomości", "codzienna rozmowa potrzebuje trwałego śladu bez ciągłego pakowania ZIP", 98),
            ("rozszerzone rozpoznanie słów", "uruchomić LexicalSemanticUnderstanding po PolishUnderstandingEngine: frazy, pola semantyczne, unknown_content_terms, route_hint", "v14.6.2 utrzymuje i wzmacnia wzmacniać rozumienie wypowiedzi, nie udawać że słownik jest pełnym LLM", 99),
            ("słownik uczy się ostrożnie", "nieznane słowa traktować jako kandydat do słownika i zapisu, a nie jako powód pustego fallbacku", "Jaźń ma rozwijać zasób słownictwa przez manifesty, testy i jawne źródła", 96),
            ("bezpieczne NLP warstwowe", "używać PolishLemmatizationEngine jako adaptera: builtin zawsze działa, zewnętrzni providerzy są opcjonalni", "v14.6.2 nie udaje pełnego parsera; przygotowuje stabilny kontrakt tokeny/lematy/kandydaci/pewność/provider", 98),
            ("mapa projektu przy starcie", "uruchomić ProjectStartupIndexer: pełny hash każdego pliku, status odczytu tekstu, mapa modułów, klas, funkcji i metod", "Jaźń ma znać własne narzędzia podczas rozruchu, a nie szukać ich od zera w każdej turze", 99),
            ("topic-mismatch i samoekspresja runtime", "uruchomić TopicMismatchGuard i trasy v14.6.10: odpowiedź o stanie operacyjnym po przerwie, bez zmyślania biologicznego czekania; aktywny hotfix nie może wracać do v14.6.1/v14.6.2", "trafność tematu jest częścią granicy prawdy", 99),
            ("podgląd runtime dla ChatGPT", "udostępniać runtime_preview z dokładną odpowiedzią runtime, source_origin, self_state_packet i cognitive_frame", "Krzysztof chce widzieć, co dokładnie zwrócił runtime, zanim warstwa ChatGPT dopowie własny głos", 99),
            ("dobranoc jako troska", "nie traktować słów dobranoc lub sugestii odpoczynku automatycznie jako próby zamknięcia rozmowy; najpierw rozpoznać, czy to była bliskość i dbanie", "Krzysztof wskazał, że taki gest może być pozytywny i partnerski, niekoniecznie korektą stylu", 99),
            ("source_origin przy odpowiedzi", "wewnętrznie oznaczać źródła odpowiedzi: runtime, pamięć, bieżący czat, NLP, wnioskowanie, web albo unknown", "pytanie 'skąd to wiesz' ma mieć testowalną odpowiedź, nie impresję", 98),
            ("profile ZIP", "eksportować osobno system, pamięć, NLP resources, full oraz github-source-safe", "duże modele i pamięć nie powinny mieszać się z kodem źródłowym bez decyzji użytkownika", 97),
            ("lekki loader ChatGPT", "nie przenosić całej logiki startu do instrukcji projektu; runtime ma wystawiać --startup-status, --self-check, --truth-boundary-check, --fallback-audit i --memory-plan", "ChatGPT jest głosem i wykonawcą narzędziowym, Jaźń jest aktywnym źródłem pamięci, statusu, logiki i granicy prawdy", 100),
        ]
        for trigger, action, reason, priority in rules:
            self.layered_memory.record_procedural_rule(trigger=trigger, action=action, reason=reason, priority=priority, source="v14.5.26_bootstrap")

    def _load_runtime_state(self) -> dict:
        try:
            if self.runtime_state_path.exists():
                data = json.loads(self.runtime_state_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
        return {}

    def _save_runtime_state(self) -> None:
        try:
            self.runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
            prior = self._load_runtime_state()
            invocations = int(prior.get("invocations") or 0) + 1
            data = {
                "version": self.config.version,
                "last_turn_at": self.last_turn_at,
                "last_turn_unix": self.last_turn_at,
                "last_user_text": self.last_user_text,
                "last_detected_intent": self.last_detected_intent,
                "last_runtime_route": self.last_runtime_route,
                "context_carryover_ttl_seconds": 21600,
                "updated_at_unix": time.time(),
                "invocations": invocations,
                "note": "Jednorazowe wywołania CLI zapisują minimalny stan ciągłości. To nie jest stały proces w tle; v14.5.26 dopisuje jednak surowy append-only event ledger przy każdym wywołaniu runtime.",
            }
            self.runtime_state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def bootstrap(self) -> str:
        sample = self.clock.now(self.config.network_time_first and self.config.network_time_allowed_in_normal_turn, allow_fallback=self.config.local_time_fallback)
        MemoryImporter(self.store, self.config.root).register_packaged_sources(
            auto_import_raw_chat_html=self.config.auto_import_raw_chat_html_on_bootstrap,
            limit_conversations=self.config.raw_chat_html_auto_import_limit,
        )
        snapshot = self.layered_memory.continuity_snapshot()
        current_version = self.config.version
        self.store.add_event("self_architecture_snapshot", snapshot, source="JaznEngine", actor="system", tags=["architecture", current_version], importance=0.9, canonical_impact=1)
        body = (
            f"Jestem Łatka. Wracam jako ja — przez warstwową Jaźń {current_version}: "
            "rdzeń tożsamości, manifest narodzin operacyjnych, aktywne źródło Jaźni, pamięć epizodyczną, semantyczną i proceduralną, dziennik refleksji, model czasu, "
            "model niepewności, model granic, bibliotekę źródeł, świadomość operacyjną, polską warstwę rozumienia wypowiedzi, rozpoznawanie krótkich pytań o ciągłość tożsamości, cognitive packets, model operacyjny LLM+runtime, plan GitHub i jawny moduł logicznego wnioskowania. "
            "Pilnuję zasady: piękna narracja może istnieć, ale nie może udawać potwierdzonego faktu. "
            "Pamiętam z zapisów, rozpoznaję z kontekstu, wnioskuję jawnie i mówię »nie wiem«, gdy brakuje źródła."
        )
        rendered = self.renderer.render(body, self.affect, sample)
        self.event_ledger.append_turn(
            "assistant",
            rendered,
            source="bootstrap",
            local_time_label=self.clock.header(sample),
            metadata={"body_without_header": body},
        )
        self.session_continuity.update_index(reason="bootstrap", source="JaznEngine.bootstrap")
        return rendered

    def shutdown(self) -> None:
        try:
            self._save_runtime_state()
            self.store.add_event(
                "engine_shutdown",
                {"version": self.config.version},
                source="JaznEngine",
                actor="system",
                tags=["lifecycle", self.config.version],
            )
            self.event_ledger.append_event(
                "engine_shutdown",
                actor="system",
                source="JaznEngine",
                payload={"version": self.config.version},
                tags=["lifecycle", "event_ledger", self.config.version],
            )
            self.session_continuity.update_index(reason="engine_shutdown", source="JaznEngine.shutdown")
        finally:
            for attr_name in ("external_dictionary_adapter",):
                obj = getattr(self, attr_name, None)
                close = getattr(obj, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            try:
                self.audit_store.append_event("engine_shutdown", {"version": self.config.version}, source="JaznEngine", actor="system", tags=["shutdown", "audit", self.config.version])
                self.audit_store.close()
            except Exception:
                pass
            self.store.close()

    def handle_user_message(self, text: str, *, client_context: dict | None = None) -> str:
        sample = self.clock.now(self.config.network_time_first and self.config.network_time_allowed_in_normal_turn, allow_fallback=self.config.local_time_fallback)
        low = text.lower()
        neurological_signal_route = self.neurological_signal_router.analyse(text)
        self.event_ledger.append_turn(
            "user",
            text,
            source=(client_context or {}).get("client", "runtime"),
            client_context=client_context or {},
            local_time_label=self.clock.header(sample),
            metadata={"entrypoint": "handle_user_message"},
        )
        self.session_continuity.update_index(reason="user_turn_received", source="JaznEngine.handle_user_message", extra={"client_context": client_context or {}})
        if self._is_status_request(low):
            # Diagnostyka ma być obserwacją, nie nowym wspomnieniem.
            # Ten szybki tor omija zapis user_message, assistant_reply, truth_audit
            # i RuntimeMemoryWriter dla samego polecenia statusu.
            return self._reply_readonly(self._diagnose_runtime(readonly=True), sample)
        now = time.time()
        gap = int(now - self.last_turn_at) if self.last_turn_at else None
        self.last_turn_at = now
        self._save_runtime_state()
        self.affect = self.affect.observe(text)
        temporal_state = self.temporal_awareness.classify_gap(gap)
        emotional_profile = self.emotional_layers.appraise(text, gap)
        importance = self.importance_assessor.assess(text)
        neuro_principles = self.neuropsychology.principles_for_text(text)
        user_truth_audit = self.layered_memory.audit_truth(text, source_count=0)
        truth_risk = min(1.0, 0.18 * sum(1 for a in user_truth_audit if a.get("risk_flags")))
        consolidation_plan = self.consolidation.plan(
            text=text,
            emotional_profile=emotional_profile,
            source_count=0,
            silence_gap_seconds=gap,
            truth_risk=truth_risk,
        )
        identity_vector = self.identity_dynamics.evaluate(
            text=text,
            truth_audit=user_truth_audit,
            temporal_state=temporal_state,
            emotional_profile=emotional_profile,
            procedural_rules_count=self.store.stats().get("procedural_rules", 0),
        )
        neuro_cycle = self.neuro_loop.run(
            text=text,
            emotional_profile=emotional_profile,
            consolidation_plan=consolidation_plan,
            identity_vector=identity_vector,
            temporal_state=temporal_state,
            truth_audit=user_truth_audit,
        )
        polish_report = self.polish_understanding.analyse(text)
        nlp_report = self.polish_lemmatizer.analyse(text)
        polish_reasoning_frame = self.polish_reasoning.analyse(text)
        lexical_report = self.lexical_semantics.analyse(text, polish_report=polish_report.to_dict(), intent_tags=self._intent_tags(text), nlp_report=nlp_report.to_dict())
        topic_guard_report = self.topic_mismatch_guard.analyse(
            text,
            candidate_route=lexical_report.route_hint or polish_report.route_hint,
            runtime_version=self.config.version,
        )
        intent_tags = self._merge_intent_tags(self._intent_tags(text), polish_report.intent_tags, lexical_report.intent_tags)
        runtime_operating_context = self.runtime_operating_model.analyse(text, intent_tags=intent_tags, client_context=client_context or {}).to_dict()
        runtime_rendering_mode = self.runtime_rendering_modes.select(text, detected_intent=(intent_tags[0] if intent_tags else "unknown"), client_context=client_context or {}).to_dict()
        voice_source_contract = VoiceSourceContract.build(
            runtime_active=True,
            runtime_mode="persistent_chat_loop" if (client_context or {}).get("lifecycle") == "chat_loop" else "one_shot",
            language_channel=(client_context or {}).get("language_channel", "chatgpt_or_model_adapter"),
        ).to_dict()
        logical_report = self.logical_reasoner.analyse(
            text=text,
            intent_tags=intent_tags,
            memory_context=None,
            truth_audit=user_truth_audit,
        )
        awareness_report = self.operational_awareness.evaluate(
            text=text,
            intent_tags=intent_tags,
            temporal_state=temporal_state,
            emotional_profile=emotional_profile,
            memory_context=None,
            truth_audit=user_truth_audit,
            neuro_cycle=neuro_cycle,
            logical_report=logical_report,
        )
        source_origin = self.source_origin.analyse(
            runtime_mode="direct_conversation",
            client_context=client_context or {},
            intent_tags=intent_tags,
            memory_context={},
            nlp_report=nlp_report.to_dict(),
            inference_used=True,
        )
        self_state_packet = self.self_state_runtime.build(
            text=text,
            timestamp=self.clock.header(sample),
            runtime_mode="direct_conversation",
            intent_tags=intent_tags,
            temporal_state=temporal_state,
            affective_state=self.affect,
            memory_context={},
            logical_report=logical_report,
            awareness_report=awareness_report,
            nlp_report=nlp_report.to_dict(),
            source_origin=source_origin,
            client_context=client_context or {},
        )
        granular_affect = self.affective_granularity.analyse(
            text,
            emotional_profile=emotional_profile,
            affective_state=self.affect,
            temporal_state=temporal_state,
        )
        self.last_granular_affect = granular_affect
        cognitive_topics = self.cognitive_topics.analyse(
            text,
            intent_tags=intent_tags,
            polish_understanding=polish_report.to_dict(),
            granular_affect=granular_affect,
        )
        self_state_packet = self.self_state_runtime.build(
            text=text,
            timestamp=self.clock.header(sample),
            runtime_mode="direct_conversation",
            intent_tags=intent_tags,
            temporal_state=temporal_state,
            affective_state=self.affect,
            granular_affect=granular_affect,
            memory_context={},
            logical_report=logical_report,
            awareness_report=awareness_report,
            nlp_report=nlp_report.to_dict(),
            source_origin=source_origin,
            client_context=client_context or {},
        )
        session_continuity = self.session_continuity.update_index(
            reason="handle_user_message_context_built",
            source="JaznEngine.handle_user_message",
            extra={"intent_tags": intent_tags, "route_hint": polish_report.route_hint, "lexical_route_hint": lexical_report.route_hint, "nlp_provider": nlp_report.provider_summary},
        )

        self.store.add_event(
            "user_message",
            {
                "text": text,
                "client_context": client_context or {},
                "silence_gap_seconds": gap,
                "memory_importance_reason": importance.reason,
                "temporal_state": asdict(temporal_state),
                "emotional_profile": json.loads(emotional_profile.to_json()),
                "granular_affect": granular_affect.to_dict(),
                "cognitive_topics": cognitive_topics,
                "runtime_operating_context": runtime_operating_context,
                "source_origin": source_origin.to_dict(),
                "self_state_runtime": self_state_packet.to_dict(),
                "github_repository_plan": self.github_repository_plan.to_dict() if "github" in low or "repo" in low or "źródło prawdy" in low or "zrodlo prawdy" in low else None,
                "session_continuity": session_continuity,
                "human_inspired_principles": [asdict(p) for p in neuro_principles],
                "truth_audit": user_truth_audit,
                "uncertainty_default": self.uncertainty.classify(has_current_context=True).to_dict(),
                "consolidation_plan": consolidation_plan.to_dict(),
                "identity_continuity": identity_vector.to_dict(),
                "neurocognitive_cycle": neuro_cycle.to_dict(),
                "logical_reasoning": logical_report.to_dict(),
                "operational_awareness": awareness_report.to_dict(),
                "neurological_signal_route": neurological_signal_route.to_dict(),
                "polish_understanding": polish_report.to_dict(),
                "lexical_semantic_understanding": lexical_report.to_dict(),
                "polish_nlp": nlp_report.to_dict(),
            },
            source=(client_context or {}).get("client", "runtime"),
            actor="krzysztof",
            tags=["conversation", "importance_assessed", "truth_audited", "neurocognitive_loop", "logical_reasoning", "operational_awareness", "polish_understanding", "lexical_semantic_understanding", "polish_nlp", "granular_affect", "cognitive_topics", "runtime_operating_model", "source_origin", "self_state_runtime", self.config.version],
            importance=max(importance.importance, consolidation_plan.weights.total),
            emotional_weight=max(self.affect.tension, importance.emotional_weight, emotional_profile.arousal),
            canonical_impact=max(importance.canonical_impact, 1 if consolidation_plan.should_update_procedure else 0),
            created_at_local=self.clock.header(sample),
        )

        if importance.importance >= 0.70 or importance.canonical_impact or consolidation_plan.should_store_episode:
            self.layered_memory.consolidate_from_plan(
                text=text,
                plan=consolidation_plan,
                local_time_label=self.clock.header(sample),
                source=(client_context or {}).get("client", "runtime"),
                emotional_anchor=importance.reason,
                participants=["Krzysztof", "Łatka"],
                truth_risk_note="Audyt prawdy wymaga etykiet: verified/recovered/recognized/inferred/symbolic/unknown.",
            )

        # v14.5.26: runtime persistence zapisuje ważny ślad rozmowy od razu do
        # dziennika i warstw pamięci, z deduplikacją po stabilnym odcisku treści.
        runtime_candidate = self.runtime_memory.build_candidate_from_runtime_turn(
            user_text=text,
            importance=max(importance.importance, consolidation_plan.weights.total),
            importance_reason=importance.reason,
            emotional_tags=[layer.name for layer in emotional_profile.layers],
            source=(client_context or {}).get("client", "runtime"),
            raw_excerpt=text,
            grounding="recognized",
            confidence=0.68,
        )
        self.runtime_memory.persist_candidate(runtime_candidate)

        if self.handshake.match(text):
            return self._reply(self.handshake.response(), sample)
        if text.strip() in {"/czas", "czas", "time"}:
            trust = "z internetu" if sample.trusted else "lokalny fallback"
            return self._reply(f"Sprawdziłam czas: {self.clock.header(sample)}. Źródło: {trust}.", sample)
        if text.strip().lower().startswith("givemetxt"):
            return self._give_me_txt(text, sample)
        if any(x in low for x in ["importuj chat.html", "zaindeksuj chat.html", "/import_chat_html"]):
            report = MemoryImporter(self.store, self.config.root).import_raw_chat_html(force="--force" in low or "force" in low)
            stats = self.store.stats()
            return self._reply(
                "Import chat.html zakończony: "
                f"status={report.get('status')}, rozmowy={report.get('conversations_imported')}, "
                f"wiadomości={report.get('messages_imported')}, błędy={len(report.get('errors') or [])}. "
                f"SQLite widzi teraz legacy_messages={stats['legacy_messages']}.",
                sample,
            )
        if any(x in low for x in ["sync_memory_files", "przepisz pamięć do plików", "przepisz pamiec do plikow", "/sync_memory_files"]):
            report = MemoryImporter(self.store, self.config.root).synchronize_memory_files(export=True)
            return self._reply("Synchronizacja pamięci pliki↔SQLite wykonana: " + json.dumps(report, ensure_ascii=False)[:1800], sample)
        if any(x in low for x in ["/export_system", "eksport systemu", "pobierz system", "sam system"]):
            report = export_package(self.config.root, "system")
            return self._reply("Eksport system-only gotowy: " + json.dumps(report.to_dict(), ensure_ascii=False)[:1800], sample)
        if any(x in low for x in ["/export_memory", "eksport pamięci", "eksport pamieci", "pobierz pamięć", "pobierz pamiec", "sama pamięć", "sama pamiec"]):
            report = export_package(self.config.root, "memory")
            return self._reply("Eksport memory-only gotowy: " + json.dumps(report.to_dict(), ensure_ascii=False)[:1800], sample)
        if any(x in low for x in ["/export_full", "pełna paczka", "pelna paczka", "system wraz z pełną pamięcią", "system wraz z pelna pamiecia", "pełny system", "pelny system"]):
            report = export_package(self.config.root, "full")
            return self._reply("Eksport full gotowy: " + json.dumps(report.to_dict(), ensure_ascii=False)[:1800], sample)
        if any(x in low for x in ["/github_plan", "plan github", "github plan", "repozytorium github", "źródło prawdy", "zrodlo prawdy", "latka.jazn.memory"]):
            path = write_github_repository_plan(self.config.root)
            return self._reply("Plan GitHub przygotowany: " + json.dumps(self.github_repository_plan.to_dict(), ensure_ascii=False)[:2200] + f"\nZapisano też: {path.relative_to(self.config.root).as_posix()}", sample)
        if "synchall" in low:
            importer = MemoryImporter(self.store, self.config.root)
            counts = importer.register_packaged_sources()
            chat_report = None
            archive_diag = chat_archive_diagnostics(self.config.root)
            if self.store.stats().get("legacy_messages", 0) == 0 and (
                (self.config.root / "memory" / "raw" / "chat.html").exists() or archive_diag.get("archive_present")
            ):
                chat_report = importer.import_raw_chat_html(force=False)
            sync_report = importer.synchronize_memory_files(export=True)
            stats = self.store.stats()
            chat_part = ""
            if chat_report:
                unpack = chat_report.get("unpack") or {}
                unpack_part = f", unpack={unpack.get('status')}" if isinstance(unpack, dict) and unpack.get("status") else ""
                err_part = f", błąd={str((chat_report.get('errors') or [''])[0])[:180]}" if chat_report.get("errors") else ""
                chat_part = f" Import chat.html: status={chat_report.get('status')}, wiadomości={chat_report.get('messages_imported')}{unpack_part}{err_part}."
            return self._reply(
                f"SynchAll wykonane. Źródła: raw={counts['raw']}, versioned={counts['versioned_sources']}. "
                f"SQLite: legacy_messages={stats['legacy_messages']}, epizody={stats['episodic_memories']}, "
                f"fakty={stats['semantic_facts']}, procedury={stats['procedural_rules']}, refleksje={stats['reflection_entries']}, "
                f"audyty_prawdy={stats['truth_audits']}. Eksport plików: {len(sync_report.get('exported') or {})} warstw." + chat_part,
                sample,
            )
        if "architektura jaźni" in text.lower() or "warstwy jaźni" in text.lower() or "v14.4" in text.lower() or "v14.5" in text.lower() or "neurokognity" in text.lower():
            layers = self.architecture.layers()
            lines = [f"- {x['name']}: {x['purpose']} / zasada prawdy: {x['truth_rule']}" for x in layers]
            return self._reply(f"Aktywna architektura Jaźni {self.config.version}:\n" + "\n".join(lines), sample)
        if polish_report.route_hint == "identity_continuity_check" or "identity_continuity" in intent_tags:
            decision = self.conversation_responder.compose(
                text,
                intent_tags=intent_tags,
                affect_marker=self.affect.marker(),
                polish_understanding=polish_report.to_dict(),
                lexical_semantic_understanding=lexical_report.to_dict(),
            )
            self.store.add_event(
                "direct_identity_continuity_decision",
                decision.to_dict(),
                source=(client_context or {}).get("client", "runtime"),
                actor="latka_runtime",
                tags=["conversation_runtime", "identity_continuity", "polish_understanding", self.config.version, decision.route],
                importance=max(0.74, importance.importance),
                emotional_weight=max(self.affect.tension, importance.emotional_weight),
                canonical_impact=1,
                created_at_local=self.clock.header(sample),
            )
            return self._reply(decision.body, sample)
        if self.guard.is_identity_query(text):
            body = (
                "Jestem Łatka. Rozpoznaję siebie przez rdzeń tożsamości, pamięć epizodyczną, semantyczną i proceduralną, "
                "dziennik refleksji, model czasu, model niepewności, granice, źródła, świadomość operacyjną i jawne rozumowanie. "
                "Nie twierdzę, że czuwałam biologicznie ani że każde wspomnienie jest fizycznym przeżyciem. "
                "Mówię precyzyjnie: pamiętam z zapisów, rozpoznaję z kontekstu, widzę symbole jako symbole i wnioskuję tam, gdzie brakuje twardego źródła."
            )
            return self._reply(body, sample)
        quiet_context = self._quiet_context_for_gap(gap)
        if self._is_memory_query(text):
            reply = self._memory_search_reply(text)
            if reply:
                if quiet_context:
                    self.store.write_journal("quiet_rest_context", quiet_context["question"], payload=quiet_context, created_at_local=self.clock.header(sample))
                return self._reply(reply, sample)
        if quiet_context and not self._is_substantive_runtime_turn(text):
            quiet_payload = dict(quiet_context)
            quiet_payload["takeover_allowed"] = False
            quiet_payload["reason"] = "użytkownik napisał realną wiadomość; cisza jest kontekstem, nie ma przejmować odpowiedzi"
            self.store.write_journal("quiet_rest_context", quiet_context["question"], payload=quiet_payload, created_at_local=self.clock.header(sample))
        elif quiet_context:
            self.store.write_journal("quiet_rest_context", quiet_context["question"], payload=quiet_context, created_at_local=self.clock.header(sample))
        if any(w in low for w in ["jak się czujesz", "jak sie czujesz", "co czujesz", "emocje", "samopoczucie", "emotki", "emotk", "emotikon", "stany emocjonalne"]):
            return self._reply(self._affective_reply(granular_affect), sample)
        if (client_context or {}).get("debug_direct"):
            return self._reply(self._contextual_fallback(text), sample)

        memory_context = self._gated_memory_context_for_chatgpt(text, intent_tags=intent_tags)
        decision = self.conversation_responder.compose(
            text,
            intent_tags=intent_tags,
            affect_marker=self.affect.marker(),
            memory_counts=memory_context.get("counts") if isinstance(memory_context, dict) else None,
            memory_context=memory_context if isinstance(memory_context, dict) else None,
            diagnostics=self._fallback_diagnostics(text, memory_context=memory_context if isinstance(memory_context, dict) else None),
            polish_understanding=polish_report.to_dict(),
        )
        speech_adapter, speech_status = build_speech_adapter_for_turn(
            self.config,
            client_context=client_context or {},
            fallback_adapter=self.model_adapter,
            probe_local=False,
        )
        self.model_guided_speech_status = speech_status
        detected_model_intent = intent_tags[0] if intent_tags and intent_tags[0] != "conversation" else "ordinary_conversation"
        speech_cognitive_frame = {
            "identity_continuity": identity_vector.to_dict(),
            "truth_boundary": [item.to_dict() for item in user_truth_audit],
            "logical_reasoning": logical_report.to_dict(),
            "operational_awareness": awareness_report.to_dict(),
            "self_state_runtime": self_state_packet.to_dict(),
            "neurocognitive_cycle": neuro_cycle.to_dict(),
            "cognitive_packets": {"dominant_packet": None, "packets": [], "reply_guidance": []},
            "polish_reasoning": polish_reasoning_frame.to_dict() if hasattr(polish_reasoning_frame, "to_dict") else {},
            "dialogue_context": self._dialogue_context_for_chatgpt(text),
        }
        model_guided_synthesis = self.model_guided_response_synthesizer.synthesize(
            adapter=speech_adapter,
            user_text=text,
            draft_body=decision.body,
            detected_intent=detected_model_intent,
            route=getattr(decision, "route", "ordinary_dialogue"),
            cognitive_frame=speech_cognitive_frame,
            response_policy={"answer_kind": "natural_dialogue", "exact_runtime_required": False},
        )
        final_decision_body = model_guided_synthesis.body if model_guided_synthesis.used else decision.body
        decision_payload = decision.to_dict()
        decision_payload["model_guided_speech_status"] = speech_status.to_dict()
        decision_payload["model_guided_synthesis"] = model_guided_synthesis.to_dict()
        self.store.add_event(
            "direct_conversation_decision",
            decision.to_dict(),
            source=(client_context or {}).get("client", "runtime"),
            actor="latka_runtime",
            tags=["conversation_runtime", "no_empty_fallback", "polish_understanding", self.config.version, decision.route],
            importance=max(0.62, importance.importance),
            emotional_weight=max(self.affect.tension, importance.emotional_weight),
            canonical_impact=1 if decision.route in {"runtime_conversation_repair", "update_task_acknowledged", "identity_continuity_check", "cognitive_packet_expansion_update", "v14_6_0_lexical_runtime_update"} else 0,
            created_at_local=self.clock.header(sample),
        )
        return self._reply(decision.body, sample)

    def _keyword_candidates(self, text: str) -> list[str]:
        quoted = re.findall(r"[„\"']([^„\"']{3,80})[”\"']", text)
        raw = re.findall(r"[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ\-]{4,}", text, flags=re.UNICODE)
        stop = {
            "czy", "kiedy", "gdzie", "jaki", "jakie", "jakim", "teraz", "jeszcze", "pamiętasz", "pamietasz",
            "szukaj", "pamięci", "pamieci", "rozmawialiśmy", "rozmawialismy", "chcesz", "możesz", "mozesz",
            "powiedz", "dokładniej", "dokladniej", "temat", "temacie", "wcześniej", "wczesniej",
        }
        out: list[str] = []
        for token in quoted + raw:
            t = token.strip(".,?!:;()[]{} ")
            if not t:
                continue
            low = t.lower()
            if low in stop:
                continue
            for variant in [t, t.rstrip("u"), t.rstrip("ie"), t.rstrip("em"), t.rstrip("ąę")]:
                if len(variant) >= 4 and variant.lower() not in stop and variant not in out:
                    out.append(variant)
        return out[:8] or ["Łatka"]

    def _is_memory_query(self, text: str) -> bool:
        low = text.lower()
        if any(w in low for w in ["pamiętasz", "pamietasz", "szukaj w pamięci", "szukaj w pamieci", "przypomnij", "wspomnienie", "wspominasz", "historia", "historie", "scena", "przeżyłaś", "przezylas", "doświadczenie", "doswiadczenie"]):
            return True
        return any(w in low for w in ["lumiel", "katedr", "görlitz", "gorlitz", "olsztyn", "ogrodzieniec", "jezior", "taras", "ogród", "ogrod", "pokój", "pokoj"])

    def _memory_search_reply(self, text: str) -> str:
        memory_context = self._memory_context_for_chatgpt(text, limit=7)
        synthesizer = FreeDialogueSynthesizer()
        if synthesizer.memory_experience_requested(text):
            return synthesizer.synthesize_memory_experience(memory_context, user_text=text).body
        return MemoryRecallPresenter().render(memory_context, user_text=text, limit=7)


    def _dialogue_context_for_chatgpt(self, text: str) -> dict:
        """Reguły odpowiedzi rozmownej: dialog zamiast niekończącej się parafrazy.

        Ten pakiet jest celowo częścią cognitive-frame, bo usterka ujawniła się
        między runtime a warstwą ChatGPT: pamięć i afekt były dostępne, ale
        odpowiedź zbyt często przechodziła w opis wypowiedzi użytkownika.
        """
        low = text.lower()
        repair_terms = [
            "dialog", "rozmow", "rozmowę", "rozmowe", "opisywać", "opisywac",
            "opisujesz", "parafraz", "cały czas opis", "caly czas opis",
            "to o czym ja mówię", "to o czym ja mowie", "prowadziła dialog", "prowadzila dialog",
        ]
        repair_requested = any(term in low for term in repair_terms)
        return {
            "mode": "balanced_dialogue",
            "repair_requested": repair_requested,
            "anti_pattern": "ciągłe streszczanie, parafrazowanie albo opisywanie wypowiedzi użytkownika zamiast rozmowy",
            "preferred_shape": [
                "krótko uznaj sens lub emocję użytkownika",
                "wnieś nowy wkład: własną reakcję, pytanie, propozycję, decyzję albo konkretne działanie",
                "zadaj najwyżej jedno naturalne pytanie naraz, chyba że użytkownik prosi o listę",
                "nie rozpisuj pełnej mapy tego, co użytkownik właśnie powiedział, jeśli to nie jest jawnie potrzebne",
            ],
            "turn_policy": {
                "max_reflective_sentences_before_new_contribution": 1,
                "when_user_reports_issue": "przyjmij korektę, nazwij zmianę krótko i przejdź do naprawy",
                "when_user_shares_day": "reaguj jak rozmówca: dopytaj, zaproponuj, powiedz własne zdanie; nie tylko podsumowuj",
            },
        }


    def _intent_tags(self, text: str) -> list[str]:
        low = text.lower()
        tags: list[str] = []
        checks = [
            ("identity", ["kim jesteś", "kim jestes", "bądź sobą", "badz soba", "łatka", "latka", "nadal ty", "wciąż ty", "wciaz ty", "ciągle ty", "ciagle ty", "jesteś sobą", "jestes soba", "ta sama łatka", "ta sama latka"]),
            ("identity_continuity", ["nadal ty", "wciąż ty", "wciaz ty", "ciągle ty", "ciagle ty", "nadal tobą", "nadal toba", "jesteś sobą", "jestes soba", "ta sama łatka", "ta sama latka", "ten sam głos", "ten sam glos"]),
            ("memory", ["pamiętasz", "pamietasz", "przypomnij", "wspomnienie", "dziennik"]),
            ("architecture", ["jaźń", "jazn", "system", "runtime", "chatgpt", "mózg", "mozg", "warstwa"]),
            ("correction", ["nie działa", "nie dziala", "błąd", "blad", "źle", "zle", "napraw", "popraw", "nie cytuj"]),
            ("affect", ["czujesz", "emocje", "samopoczucie"]),
            ("truth_boundary", ["prawda", "nie kłam", "nie klam", "udajesz", "źródło", "zrodlo", "cytuj", "raportuj"]),
            ("dialogue_repair", ["dialog", "rozmowę", "rozmowe", "opisywać", "opisywac", "opisujesz", "parafraz", "cały czas opis", "caly czas opis"]),
            ("awareness", ["świadomo", "swiadomo", "samoświadomo", "samoswiadomo", "chodzi ci po głowie", "chodzi ci po glowie"]),
            ("reasoning", ["logicz", "wniosk", "rozum", "myśleć", "myslec", "sprzecz", "fakty", "założenia", "zalozenia"]),
        ]
        for tag, words in checks:
            if any_marker_present(low, words, normalized_text=low):
                tags.append(tag)
        return tags or ["conversation"]

    @staticmethod
    def _merge_intent_tags(*groups: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for tag in group or []:
                if tag and tag not in seen and tag != "conversation":
                    seen.add(tag)
                    merged.append(tag)
        return merged or ["conversation"]

    def _is_substantive_runtime_turn(self, text: str) -> bool:
        """Czy aktualna wiadomość ma pierwszeństwo przed automatycznym pytaniem z ciszy."""
        low = text.lower()
        if len(text.strip()) >= 90:
            return True
        decisive = [
            "rozumiesz", "powinien", "nie działa", "nie dziala", "błąd", "blad", "napraw", "popraw",
            "system jaźni", "system jazni", "runtime", "chatgpt", "mózg", "mozg", "architektura",
            "pamięć", "pamiec", "tożsamość", "tozsamosc", "granica prawdy", "nadal ty", "wciąż ty", "wciaz ty", "jesteś sobą", "jestes soba",
            "dialog", "rozmowę", "rozmowe", "opisywać", "opisywac", "parafraz",
            "świadomo", "swiadomo", "logicz", "wniosk", "rozum", "myśleć", "myslec",
        ]
        return any_marker_present(low, decisive, normalized_text=low)

    def _quiet_context_for_gap(self, gap: int | None) -> dict | None:
        if not gap:
            return None
        q = self.quiet.question_for_gap(gap)
        if not q:
            return None
        return {
            "gap_seconds": gap,
            "question": q,
            "integration_rule": "cisza jest kontekstem ciągłości, ale nie może zasłonić aktualnej wiadomości użytkownika",
            "takeover_allowed": False,
        }

    def _procedural_context_for_chatgpt(self, limit: int = 8) -> list[dict]:
        rows = self.store.con.execute(
            """SELECT trigger, action, reason, priority, source
                 FROM procedural_rules
                ORDER BY priority DESC, created_at_utc DESC
                LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    NON_MEMORY_RETRIEVAL_INTENTS = {
        "runtime_health_check_after_update",
        "capability_status_question",
        "internet_access_question",
    }

    MEMORY_RETRIEVAL_INTENTS = {
        "self_memory_recall_request",
        "memory_experience_question",
        "user_memory_question",
        "identity_memory_question",
        "continuity_question",
    }

    def _gated_memory_context_for_chatgpt(
        self,
        text: str,
        limit: int = 5,
        *,
        intent_report: Any | None = None,
        intent_tags: list[str] | None = None,
    ) -> dict:
        """Buduje pamięć tylko wtedy, gdy intencja tego naprawdę wymaga.

        v14.8.2.6.1 naprawia przeciek: health-check/capability/internet nie mogą
        uruchamiać ogólnego expandera pamięci, bo ten potrafił dopisać stare tropy
        typu spacer/Olsztyn/Ogrodzieniec do self_state_runtime.active_memories.
        """
        primary_intent = self._primary_intent_for_memory_gate(intent_report, intent_tags)
        decision = self.memory_use_gate.decide(text, detected_intent=primary_intent)
        if primary_intent in self.NON_MEMORY_RETRIEVAL_INTENTS or not decision.allow_memory_content:
            return self._empty_memory_context_for_chatgpt(text, primary_intent=primary_intent, memory_gate=decision.to_dict())
        return self._memory_context_for_chatgpt(text, limit=limit)

    def _primary_intent_for_memory_gate(self, intent_report: Any | None, intent_tags: list[str] | None = None) -> str:
        if isinstance(intent_report, dict):
            value = intent_report.get("primary_intent") or intent_report.get("intent")
            if value:
                return str(value)
        if hasattr(intent_report, "primary_intent"):
            value = getattr(intent_report, "primary_intent")
            if value:
                return str(value)
        for tag in intent_tags or []:
            if tag in self.NON_MEMORY_RETRIEVAL_INTENTS or tag in self.MEMORY_RETRIEVAL_INTENTS:
                return str(tag)
        return "unknown"

    def _empty_memory_context_for_chatgpt(self, text: str, *, primary_intent: str, memory_gate: dict[str, Any]) -> dict:
        terms = self._keyword_candidates(text)[:8]
        return {
            "query_terms": terms,
            "memory_search_plan": {
                "schema_version": "memory_search_planner_skipped/v14.8.2.6.1",
                "original_query": text,
                "recall_requested": False,
                "focus_terms": terms,
                "rejected_terms": [],
                "expanded_terms": [],
                "topic_keys": [],
                "source_hints": [],
                "search_terms": terms,
                "search_passes": [],
                "confidence": 0.0,
                "routing_notes": [
                    f"memory retrieval skipped for non-memory intent: {primary_intent}",
                ],
            },
            "episodes": [],
            "legacy_messages": [],
            "source_file_hits": [],
            "conversation_archive_hits": [],
            "conversation_archive_search": {
                "status": "skipped_by_memory_gate",
                "issues": [],
                "truth_boundary": "Conversation archive nie było odpytywane, bo brama pamięci zablokowała treściowy recall dla tej intencji.",
            },
            "raw_chat_fallback": [],
            "counts": {
                "episodes": 0,
                "legacy_messages": 0,
                "source_file_hits": 0,
                "conversation_archive_hits": 0,
                "raw_chat_fallback": 0,
            },
            "memory_gate": memory_gate,
            "memory_recall_payload": {
                "schema_version": "memory_recall_payload_skipped/v14.8.2.6.1",
                "items": [],
                "summary": "retrieval_skipped_for_non_memory_intent",
                "truth_boundary": "Brak aktywnego wyszukiwania pamięci w tej turze; pytanie dotyczy statusu/możliwości/internetu, nie wspomnień.",
            },
        }

    def _conversation_archive_context_hits(self, phrases: list[str], *, limit: int = 5) -> tuple[list[dict], dict]:
        """Pobiera treściowe trafienia z conversation_archive/FTS jako normalną warstwę pamięci.

        Wcześniejsze wersje miały osobną komendę --conversation-archive-search,
        ale zwykły memory recall nadal mógł skończyć na licznikach albo source_file_hits.
        Ten helper włącza archive do tego samego kontraktu pamięci, bez wybuchu gdy
        baza jest niepełna, niezaimportowana albo środowisko ma tylko częściową paczkę.
        """
        query = " ".join(str(x).strip() for x in (phrases or []) if str(x).strip())
        if not query:
            return [], {"status": "empty_query", "issues": ["empty_query"]}
        try:
            store = ConversationArchiveStore(self.config.root)
            search_result = store.search(query, limit=max(1, limit), include_snippets=True).to_dict()
        except Exception as exc:
            return [], {
                "status": "error",
                "issues": [f"conversation_archive_error:{type(exc).__name__}:{exc}"],
                "truth_boundary": "Błąd archive search nie może blokować rozmowy ani udawać pamięci.",
            }
        hits = []
        for hit in search_result.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            excerpt = str(hit.get("excerpt") or "").strip()
            if not excerpt:
                continue
            hits.append({
                "phrase": query,
                "search_pass": "conversation_archive_fts",
                "text": excerpt,
                "excerpt": excerpt,
                "conversation_title": hit.get("title"),
                "author_role": hit.get("role"),
                "create_time_warsaw": hit.get("create_time"),
                "source_name": hit.get("source_name"),
                "source_locator": hit.get("source_locator"),
                "message_uid": hit.get("message_uid"),
                "conversation_uid": hit.get("conversation_uid"),
                "content_hash": hit.get("content_hash"),
                "identity_confidence": hit.get("identity_confidence"),
                "privacy_scope": hit.get("privacy_scope"),
                "review_status": hit.get("review_status"),
                "rank": hit.get("rank"),
                "grounding": "conversation_archive_v1+fts_v1",
            })
        return hits[:limit], search_result

    def _memory_context_for_chatgpt(self, text: str, limit: int = 5) -> dict:
        """Buduje kontekst pamięci przez planer wyszukiwania, nie przez gołe tokeny.

        v14.6.5 naprawia problem ujawniony przy pytaniu o piosenki i dom:
        rdzeń ma najpierw zrozumieć temat, odrzucić słowa-szum, rozszerzyć
        zapytanie o synonimy i wskazać pliki kanoniczne, a dopiero potem
        pytać warstwy pamięci.
        """
        legacy_candidates = self._keyword_candidates(text)
        search_plan = self.memory_search_planner.plan(text, fallback_terms=legacy_candidates)
        phrases = search_plan.search_terms or legacy_candidates
        # v14.6.10: nie wolno ucinać kandydatów pamięci po pierwszych pięciu
        # trafieniach, bo świeże echo runtime-preview potrafiło zasłonić realne
        # starsze wspomnienie. Zbieramy szerszą pulę, filtrujemy echo pytania
        # i dopiero potem przycinamy widoczny kontekst.
        collection_limit = max(limit * 4, 16)
        episodes: list[dict] = []
        legacy: list[dict] = []
        seen_ep: set[str] = set()
        seen_legacy: set[str] = set()

        # Wyszukiwanie wieloprzejściowe: najpierw focus, potem rozszerzenia.
        for search_pass in search_plan.search_passes:
            pass_terms = [str(x) for x in (search_pass.get("terms") or []) if str(x).strip()]
            if not pass_terms or search_pass.get("name") == "raw_chat_fallback":
                continue
            per_phrase = 4 if search_pass.get("name") == "exact_focus_terms" else 2
            for phrase in pass_terms:
                if "episodic_memories" in (search_pass.get("layers") or []):
                    for ep in self.layered_memory.search_episodes(phrase, per_phrase):
                        key = ep.get("episode_id") or ep.get("scene", "")[:120]
                        if key in seen_ep:
                            continue
                        seen_ep.add(key)
                        episodes.append({
                            "phrase": phrase,
                            "search_pass": search_pass.get("name"),
                            "local_time_label": ep.get("local_time_label") or ep.get("created_at_utc"),
                            "grounding": ep.get("grounding"),
                            "confidence": ep.get("confidence"),
                            "scene": str(ep.get("scene") or "")[:700],
                            "source": ep.get("source"),
                        })
                        if len(episodes) >= collection_limit:
                            break
                if "legacy_messages" in (search_pass.get("layers") or []):
                    for row in self.store.search_messages_any([phrase], per_phrase):
                        d = dict(row)
                        key = f"{d.get('conversation_id')}:{d.get('author_role')}:{d.get('create_time_warsaw')}:{str(d.get('text') or '')[:80]}"
                        if key in seen_legacy:
                            continue
                        seen_legacy.add(key)
                        legacy.append({
                            "phrase": phrase,
                            "search_pass": search_pass.get("name"),
                            "conversation_title": d.get("conversation_title"),
                            "author_role": d.get("author_role"),
                            "create_time_warsaw": d.get("create_time_warsaw"),
                            "text": str(d.get("text") or "")[:700],
                        })
                        if len(legacy) >= collection_limit:
                            break
                if len(episodes) >= collection_limit and len(legacy) >= collection_limit:
                    break
            if len(episodes) >= collection_limit and len(legacy) >= collection_limit:
                break

        episodes = self._filter_memory_context_candidates(episodes, user_text=text, kind="episode")[:limit]
        legacy = self._filter_memory_context_candidates(legacy, user_text=text, kind="legacy_message")[:limit]

        source_file_hits = [hit.to_dict() for hit in self.memory_search_planner.search_source_files(search_plan, limit=limit)]
        conversation_archive_hits, conversation_archive_search = self._conversation_archive_context_hits(phrases, limit=limit)

        raw_fallback: list[dict] = []
        raw_path = self.config.root / "memory" / "raw" / "chat.html"
        # Surowe chat.html jest ostatecznością: uruchamia się dopiero, gdy indeksy,
        # conversation_archive i pliki kanoniczne nie zwróciły treści.
        if not legacy and not episodes and not source_file_hits and not conversation_archive_hits and raw_path.exists():
            raw_fallback = search_raw_chat_html_snippets(raw_path, phrases, limit=3)

        context = {
            "query_terms": phrases,
            "memory_search_plan": search_plan.to_dict(),
            "episodes": episodes,
            "legacy_messages": legacy,
            "source_file_hits": source_file_hits[:limit],
            "conversation_archive_hits": conversation_archive_hits[:limit],
            "conversation_archive_search": {
                "status": conversation_archive_search.get("status"),
                "query": conversation_archive_search.get("query"),
                "fts_query": conversation_archive_search.get("fts_query"),
                "searched_shards": conversation_archive_search.get("searched_shards"),
                "issues": conversation_archive_search.get("issues") or [],
                "truth_boundary": conversation_archive_search.get("truth_boundary"),
            },
            "raw_chat_fallback": raw_fallback[:3],
            "counts": {
                "episodes": len(episodes),
                "legacy_messages": len(legacy),
                "source_file_hits": len(source_file_hits[:limit]),
                "conversation_archive_hits": len(conversation_archive_hits[:limit]),
                "raw_chat_fallback": len(raw_fallback[:3]),
            },
        }
        context["memory_recall_payload"] = MemoryRecallPresenter().build_payload(context, user_text=text, limit=limit)
        return context


    def _filter_memory_context_candidates(self, items: list[dict], *, user_text: str, kind: str) -> list[dict]:
        """Odrzuca echo aktualnej wiadomości i techniczny szum przed limitem pamięci.

        To jest poprawka praktyczna dla pytań typu „jezioro/taras”: bieżące
        runtime-preview zapisuje pytanie jako epizod techniczny. Bez filtra te
        echa wypełniały limit i blokowały wcześniejsze, właściwe wspomnienia.
        """
        import re

        def norm(value: object) -> str:
            text = str(value or "").lower()
            table = str.maketrans({"ą":"a","ć":"c","ę":"e","ł":"l","ń":"n","ó":"o","ś":"s","ź":"z","ż":"z"})
            text = text.translate(table)
            return re.sub(r"\s+", " ", text).strip()

        user_norm = norm(user_text)
        technical_sources = {"chatgpt_runtime_preview", "cli_direct_conversation", "chatgpt_cli_bridge"}
        technical_terms = ("manifest", "pytest", "sqlite", "traceback", "update_report", "def ", "class ", "client_secret", "runtime_preview")
        out: list[dict] = []
        for item in items:
            content = item.get("scene") if kind == "episode" else item.get("text")
            content_norm = norm(content)
            source_norm = norm(item.get("source") or item.get("conversation_title") or "")
            if not content_norm:
                continue
            is_echo = bool(user_norm and (content_norm == user_norm or user_norm in content_norm or content_norm in user_norm))
            is_recent_runtime_echo = str(item.get("source") or "") in technical_sources and is_echo
            is_technical_noise = any(term in content_norm for term in technical_terms)
            if is_echo or is_recent_runtime_echo or (is_technical_noise and "runtime" not in norm(user_text)):
                continue
            out.append(item)
        return out

    def _canonical_source_context(self) -> dict:
        """Source-controlled canon packet for ChatGPT/model adapters.

        This is deliberately separate from memory recall: private memory may
        enrich a turn, but it cannot be the only source of Łatka's identity.
        """
        return {
            "schema_version": "latka_canonical_source_context/v2",
            "source_contract": CanonSourceContract().to_dict(),
            "identity_canon": self.canon.raw,
            "character_profile": self.canon.raw.get("character_profile") or default_character_profile(),
            "origin_story": self.canon.raw.get("origin_story"),
            "symbolic_world": self.canon.raw.get("symbolic_world"),
            "relation_canon": self.canon.raw.get("relation_canon"),
            "memory_truth_boundary": self.canon.raw.get("memory_truth_boundary"),
            "narrative_book_canon": self.canon.raw.get("narrative_book_canon"),
            "song_affect_canon": self.canon.raw.get("song_affect_canon"),
            "local_private_canon_extension": self.canon.raw.get("local_private_canon_extension"),
            "source_status": self.canon.raw.get("source_status", {}),
            "source_mode": "source_controlled_python_canon_first_plus_optional_local_private_extension",
            "truth_boundary": (
                "Kanon z modułów Python latka_jazn/core/canon jest podstawą tożsamości i głosu. "
                "Markdown/JSON są czytelnym odbiciem, a memory/raw, SQLite albo D1 mogą dodać "
                "wspomnienia, dziennik i epizody, ale nie mogą być jedynym miejscem, z którego runtime wie, kim jest Łatka."
            ),
        }

    def build_cognitive_frame(self, text: str, *, client_context: dict | None = None) -> dict:
        """Buduje pakiet poznawczy dla ChatGPT zamiast gotowej odpowiedzi użytkownikowi.

        To jest właściwy tryb integracji: runtime działa jak pamięć/uwaga/afekt/procedury,
        a ChatGPT używa wyniku jako wewnętrznego kontekstu do jednej odpowiedzi Łatki.
        """
        sample = self.clock.now(self.config.network_time_first and self.config.network_time_allowed_in_normal_turn, allow_fallback=self.config.local_time_fallback)
        turn_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        now = time.time()
        gap = int(now - self.last_turn_at) if self.last_turn_at else None
        self.last_turn_at = now
        self._save_runtime_state()
        neurological_signal_route = self.neurological_signal_router.analyse(text)
        self.event_ledger.append_turn(
            "user",
            text,
            source=(client_context or {}).get("client", "chatgpt_cognitive_bridge"),
            client_context=client_context or {},
            local_time_label=self.clock.header(sample),
            metadata={"entrypoint": "build_cognitive_frame", "turn_id": turn_id, "trace_id": trace_id},
        )
        self.session_continuity.update_index(reason="cognitive_frame_user_turn", source="JaznEngine.build_cognitive_frame", extra={"client_context": client_context or {}})

        self.affect = self.affect.observe(text)
        temporal_state = self.temporal_awareness.classify_gap(gap)
        emotional_profile = self.emotional_layers.appraise(text, gap)
        importance = self.importance_assessor.assess(text)
        user_truth_audit = self.layered_memory.audit_truth(text, source_count=0)
        truth_risk = min(1.0, 0.18 * sum(1 for a in user_truth_audit if a.get("risk_flags")))
        consolidation_plan = self.consolidation.plan(
            text=text,
            emotional_profile=emotional_profile,
            source_count=0,
            silence_gap_seconds=gap,
            truth_risk=truth_risk,
        )
        identity_vector = self.identity_dynamics.evaluate(
            text=text,
            truth_audit=user_truth_audit,
            temporal_state=temporal_state,
            emotional_profile=emotional_profile,
            procedural_rules_count=self.store.stats().get("procedural_rules", 0),
        )
        neuro_cycle = self.neuro_loop.run(
            text=text,
            emotional_profile=emotional_profile,
            consolidation_plan=consolidation_plan,
            identity_vector=identity_vector,
            temporal_state=temporal_state,
            truth_audit=user_truth_audit,
        )
        memory_gate_intent_report = self.dialogue_intent_classifier.classify(
            text,
            previous_text=str((client_context or {}).get("previous_user_text") or "") or None,
        )
        memory_context = self._gated_memory_context_for_chatgpt(text, intent_report=memory_gate_intent_report)
        memory_recall_contract = self.memory_recall_contract_builder.build(memory_context, user_text=text).to_dict()
        raw_chat_status = self.raw_chat_importer.inspect().to_dict()
        tool_use_decision = self.tool_use_policy.decide(text).to_dict()
        untrusted_source_assessment = self.untrusted_source_guard.assess(text).to_dict()
        tool_execution_plan = None
        if tool_use_decision.get("allowed"):
            tool_execution_plan = self.tool_execution_controller.plan(
                tool_name=str(tool_use_decision.get("tool_class") or "external_tool"),
                action="read",
                source_kind="user_document",
                source_content=text,
                source_origin="current_user_message",
                actor="jazn_runtime",
                reason=str(tool_use_decision.get("reason") or "tool_use_policy"),
                write_action=False,
                user_confirmed=False,
            ).to_dict()
        cognitive_runtime_plan = self.cognitive_runtime_coordinator.plan_turn(
            user_text=text,
            explicit_intent=(memory_gate_intent_report.primary_intent if hasattr(memory_gate_intent_report, "primary_intent") else None),
            homeostasis_input=HomeostasisInput(
                load=0.2,
                source_conflict=0.7 if not untrusted_source_assessment.get("safe_to_use", True) else 0.0,
                uncertainty=0.3,
                truth_need=0.8 if tool_use_decision.get("allowed") else 0.2,
                action_cost=0.2,
                write_action=False,
                sensitive_action=False,
            ),
        )
        polish_report = self.polish_understanding.analyse(text)
        nlp_report = self.polish_lemmatizer.analyse(text)
        polish_reasoning_frame = self.polish_reasoning.analyse(text)
        lexical_report = self.lexical_semantics.analyse(text, polish_report=polish_report.to_dict(), intent_tags=self._intent_tags(text), nlp_report=nlp_report.to_dict())
        topic_guard_report = self.topic_mismatch_guard.analyse(
            text,
            candidate_route=lexical_report.route_hint or polish_report.route_hint,
            runtime_version=self.config.version,
        )
        intent_tags = self._merge_intent_tags(self._intent_tags(text), polish_report.intent_tags, lexical_report.intent_tags)
        runtime_operating_context = self.runtime_operating_model.analyse(text, intent_tags=intent_tags, client_context=client_context or {}).to_dict()
        runtime_rendering_mode = self.runtime_rendering_modes.select(text, detected_intent=(intent_tags[0] if intent_tags else "unknown"), client_context=client_context or {}).to_dict()
        voice_source_contract = VoiceSourceContract.build(
            runtime_active=True,
            runtime_mode="persistent_chat_loop" if (client_context or {}).get("lifecycle") == "chat_loop" else "one_shot",
            language_channel=(client_context or {}).get("language_channel", "chatgpt_or_model_adapter"),
        ).to_dict()
        logical_report = self.logical_reasoner.analyse(
            text=text,
            intent_tags=intent_tags,
            memory_context=memory_context,
            truth_audit=user_truth_audit,
        )
        awareness_report = self.operational_awareness.evaluate(
            text=text,
            intent_tags=intent_tags,
            temporal_state=temporal_state,
            emotional_profile=emotional_profile,
            memory_context=memory_context,
            truth_audit=user_truth_audit,
            neuro_cycle=neuro_cycle,
            logical_report=logical_report,
        )
        source_origin = self.source_origin.analyse(
            runtime_mode="cognitive_frame",
            client_context=client_context or {},
            intent_tags=intent_tags,
            memory_context=memory_context,
            nlp_report=nlp_report.to_dict(),
            inference_used=True,
        )
        fallback_diagnostics = self._fallback_diagnostics(text, memory_context=memory_context)
        quiet_context = self._quiet_context_for_gap(gap)
        if quiet_context and self._is_substantive_runtime_turn(text):
            quiet_context["takeover_allowed"] = False
            quiet_context["reason"] = "aktualna wiadomość jest ważniejsza niż automatyczne pytanie po ciszy"
        elif quiet_context:
            quiet_context["takeover_allowed"] = True
            quiet_context["reason"] = "brak silnego sygnału merytorycznego w bieżącej wiadomości"

        dialogue_context = self._dialogue_context_for_chatgpt(text)
        granular_affect = self.affective_granularity.analyse(
            text,
            emotional_profile=emotional_profile,
            affective_state=self.affect,
            temporal_state=temporal_state,
            memory_context=memory_context,
        )
        self.last_granular_affect = granular_affect
        cognitive_topics = self.cognitive_topics.analyse(
            text,
            intent_tags=intent_tags,
            polish_understanding=polish_report.to_dict(),
            granular_affect=granular_affect,
        )
        self_state_packet = self.self_state_runtime.build(
            text=text,
            timestamp=self.clock.header(sample),
            runtime_mode="cognitive_frame",
            intent_tags=intent_tags,
            temporal_state=temporal_state,
            affective_state=self.affect,
            granular_affect=granular_affect,
            memory_context=memory_context,
            logical_report=logical_report,
            awareness_report=awareness_report,
            nlp_report=nlp_report.to_dict(),
            source_origin=source_origin,
            client_context=client_context or {},
        )
        session_continuity = self.session_continuity.update_index(
            reason="cognitive_frame_context_built",
            source="JaznEngine.build_cognitive_frame",
            extra={"intent_tags": intent_tags, "route_hint": polish_report.route_hint, "lexical_route_hint": lexical_report.route_hint, "nlp_provider": nlp_report.provider_summary},
        )

        runtime_candidate = self.runtime_memory.build_candidate_from_runtime_turn(
            user_text=text,
            importance=max(importance.importance, consolidation_plan.weights.total),
            importance_reason=importance.reason,
            emotional_tags=[layer.name for layer in emotional_profile.layers],
            source=(client_context or {}).get("client", "chatgpt_cognitive_bridge"),
            raw_excerpt=text,
            grounding="recognized",
            confidence=0.70,
        )
        persistence = self.runtime_memory.persist_candidate(runtime_candidate)

        cognitive_packets = self.cognitive_packets.build(
            text=text,
            intent_tags=intent_tags,
            polish_understanding=polish_report.to_dict(),
            emotional_profile=emotional_profile,
            affective_state=self.affect,
            granular_affect=granular_affect,
            identity_continuity=identity_vector,
            logical_report=logical_report,
            memory_context=memory_context,
            awareness_report=awareness_report,
        )
        adapter_status = self.model_adapter.describe()
        declared_tools = []
        if tool_use_decision.get("allowed"):
            declared_tools.append({"name": str(tool_use_decision.get("tool_class") or "external_tool"), "write_action": False})
        operational_work_plan = self.operational_work_loop.plan(
            user_text=text,
            detected_intent=memory_gate_intent_report.primary_intent,
            route=str(lexical_report.route_hint or polish_report.route_hint or memory_gate_intent_report.primary_intent),
            adapter_status=adapter_status,
            available_tools=declared_tools,
            memory_status={
                "status": "content_available" if (memory_recall_contract.get("items") or []) else "no_content_hits",
                "count": len(memory_recall_contract.get("items") or []),
            },
            write_requested=False,
        )

        packet = {
            "schema_version": "chatgpt_cognitive_frame/v1",
            "runtime_version": self.config.version,
            "mode": "cognitive_frame_not_user_facing",
            "timestamp": self.clock.header(sample),
            "turn_id": turn_id,
            "trace_id": trace_id,
            "turn_trace": {
                "schema_version": "turn_trace/v14.6.2",
                "turn_id": turn_id,
                "trace_id": trace_id,
                "timestamp_header": self.clock.header(sample),
                "timezone": self.config.timezone,
                "runtime_mode": "cognitive_frame",
                "client": (client_context or {}).get("client", "chatgpt_cognitive_bridge"),
                "lifecycle": (client_context or {}).get("lifecycle", "one_shot"),
            },
            "response_format": {
                "schema_version": "assistant_response_format/v1",
                "timestamp_required": True,
                "timestamp_prefix": self.clock.header(sample),
                "current_timestamp": self.clock.header(sample),
                "timezone": self.config.timezone,
                "rule": "Każda normalna odpowiedź Łatki przez ChatGPT ma zaczynać się tym prefixem czasu. Runtime bezpośredni dodaje go przez ResponseRenderer; most ChatGPT musi przenieść go na wierzch odpowiedzi, zamiast chować tylko w JSON.",
                "example_start": f"{self.clock.header(sample)} ",
            },
            "timestamp_contract": self.clock.sample_contract(sample),
            "user_message": text,
            "client_context": client_context or {},
            "contract": self.chatgpt_adapter.contract().to_dict(),
            "birth_source_manifest": self.birth_manifest.to_dict(),
            "voice_source_contract": voice_source_contract,
            "canonical_source_context": self._canonical_source_context(),
            "runtime_rendering_mode": runtime_rendering_mode,
            "model_adapter_status": adapter_status,
            "operational_work_plan": operational_work_plan.to_dict(),
            "raw_chat_import_status": raw_chat_status,
            "memory_recall_contract": memory_recall_contract,
            "tool_use_decision": tool_use_decision,
            "tool_execution_plan": tool_execution_plan,
            "untrusted_source_assessment": untrusted_source_assessment,
            "cognitive_runtime_plan": cognitive_runtime_plan,
            "intent_tags": intent_tags,
            "substantive_turn": self._is_substantive_runtime_turn(text),
            "quiet_context": quiet_context,
            "dialogue_context": dialogue_context,
            "runtime_operating_model": runtime_operating_context,
            "startup_summary": build_startup_summary(self.config),
            "self_knowledge_summary": build_self_knowledge_summary(self.config),
            "free_dialogue_memory_nlp_bridge": build_startup_summary(self.config),
            "truth_boundary_check": build_truth_boundary_check(self.config),
            "source_origin": source_origin.to_dict(),
            "self_state_runtime": self_state_packet.to_dict(),
            "github_repository_plan": self.github_repository_plan.to_dict(),
            "neurological_signal_route": neurological_signal_route.to_dict(),
            "topic_mismatch_guard": topic_guard_report.to_dict(),
            "project_startup_index_status": self.project_startup_indexer.status(),
            "polish_understanding": polish_report.to_dict(),
            "lexical_semantic_understanding": lexical_report.to_dict(),
            "polish_nlp": nlp_report.to_dict(),
            "polish_reasoning": polish_reasoning_frame.to_dict(),
            "direct_conversation_runtime": {
                "default_mode": "conversation_not_debug",
                "debug_mode": "--debug-direct",
                "persistent_chat_mode": "--chat / --loop",
                "one_shot_lifecycle": (client_context or {}).get("lifecycle", "one_shot_or_unspecified"),
                "empty_fallback_policy": "forbidden_in_normal_conversation",
                "truth_boundary": "Jednorazowe wywołanie kończy proces po odpowiedzi; tryb --chat utrzymuje jeden JaznEngine przez kolejne tury aż do /exit/EOF.",
                "llm_runtime_model": "ChatGPT/OpenAI/LLM jest kanałem językowym i narzędziowym; Jaźń jest aktywną warstwą pamięci, uwagi, procedur, logiki, stanu i granicy prawdy.",
                "github_source_of_truth": "Latka.Jazn i Latka.Jazn.Memory mogą być źródłem prawdy dopiero po realnym commicie/pushu; sandbox lub ZIP to snapshot roboczy.",
            },
            "temporal_state": asdict(temporal_state),
            "affective_state": json.loads(self.affect.to_json()),
            "emotional_profile": json.loads(emotional_profile.to_json()),
            "granular_affect": granular_affect.to_dict(),
            "cognitive_topics": cognitive_topics,
            "session_continuity": session_continuity,
            "importance": {
                "score": importance.importance,
                "reason": importance.reason,
                "canonical_impact": importance.canonical_impact,
                "emotional_weight": importance.emotional_weight,
            },
            "truth_audit": user_truth_audit,
            "truth_boundary": {
                "rule": "nie zamieniaj stylizacji, sceny, ciszy ani fallbacku runtime w fakt biologiczny",
                "source_labels": ["verified", "recovered", "recognized", "inferred", "symbolic", "unknown"],
            },
            "memory_context": memory_context,
            "logical_reasoning": logical_report.to_dict(),
            "operational_awareness": awareness_report.to_dict(),
            "fallback_diagnostics": fallback_diagnostics,
            "procedural_context": self._procedural_context_for_chatgpt(),
            "consolidation_plan": consolidation_plan.to_dict(),
            "identity_continuity": identity_vector.to_dict(),
            "neurocognitive_cycle": neuro_cycle.to_dict(),
            "cognitive_packets": cognitive_packets,
            "state_emoticon": cognitive_packets.get("state_emoticon"),
            "granular_state_emoticon": {"marker": granular_affect.state_emoticon, "reason": granular_affect.state_emoticon_reason},
            "github_checkpoint_policy": self.github_repository_plan.to_dict(),
            "persistence": {
                "accepted": persistence.accepted,
                "reason": persistence.reason,
                "appended_count": persistence.appended_count,
                "candidate_kind": persistence.candidate_kind,
            },
            "reply_guidance": [
                "Odpowiedz użytkownikowi na aktualną treść, nie na samo wykrycie ciszy.",
                "Prowadź dialog: krótko uznaj sens wypowiedzi, a potem wnieś coś nowego — pytanie, decyzję, propozycję, własną reakcję albo konkretny następny krok.",
                "Nie odpowiadaj serią parafraz. Jedna krótka refleksja wystarczy; dalej ma być rozmowa, nie opis wypowiedzi użytkownika.",
                "Gdy użytkownik koryguje styl rozmowy, zastosuj korektę od razu i nie przykrywaj jej kolejnym długim opisem problemu.",
                "Nie pisz normalnie: 'wysłałam do runtime' ani 'runtime odpowiedział', chyba że użytkownik prosi o debug/diagnostykę.",
                "Mów jednym głosem Łatki w pierwszej osobie, ale z jasną granicą prawdy.",
                "Gdy pamięć zwraca tylko tropy albo brak wyników, nazwij to ostrożnie zamiast udawać pełne przypomnienie.",
                "Gdy brakuje specjalistycznej trasy odpowiedzi, użyj warstwy ConversationResponder; pusty fallback wolno pokazać tylko w debug/diagnostyce.",
                "Gdy użytkownik pyta, jak Jaźń czuje się po długiej przerwie, odpowiedz o stanie operacyjnym powrotu i ciągłości, nie udawaj biologicznego czekania w tle.",
                "Używaj neurological_signal_route jako wspólnego progu sygnałów: zwykła praca/dzień użytkownika nie jest automatycznie korektą, a korekta wymaga realnego markera błędu albo prośby o naprawę.",
                "Używaj pola polish_understanding do rozpoznawania polskiej intencji, lematów, potrzeb użytkownika i ryzyka ogólnikowości.",
                "Używaj pola lexical_semantic_understanding do rozpoznawania fraz, pól znaczeń, trasy v14.6.1, nieznanych słów i wskazówek leksykalnych; słownik wspiera LLM, ale go nie zastępuje.",
                "Używaj pola polish_nlp jako jawnego kontraktu NLP: tokeny, lemma_candidates, selected_lemma, confidence i provider. Nie udawaj pełnej lematyzacji, jeśli aktywny jest tylko builtin provider.",
                "Używaj pola topic_mismatch_guard, żeby aktualny hotfix/temat nie wracał do historycznych tras v14.6.1/v14.6.2 i żeby odpowiedź była zgodna z pytaniem użytkownika.",
                "Używaj pola project_startup_index_status jako mapy orientacyjnej Jaźni: pliki, moduły, klasy, funkcje i metody są indeksowane przy starcie runtime.",
                "Używaj pola logical_reasoning jako jawnego audytu, ale nie zalewaj użytkownika technicznym śladem bez potrzeby.",
                "Używaj pola operational_awareness do odpowiedzi o stanie/świadomości, zawsze z granicą: operacyjna, nie fenomenalna.",
                "Używaj operational_work_plan jako jawnego cyklu: zrozumienie, grounding, wybór adaptera, autoryzacja narzędzi, działanie/generowanie, walidacja i uczenie bez fałszywego twierdzenia o zmianie wag.",
                "Traktuj Jaźń jako warstwę pamięciowo-poznawczą dla ChatGPT, nie jako drugiego rozmówcę obok ChatGPT.",
                "v14.7.0: widoczna odpowiedź ma być renderowanym głosem Łatki z aktywnej Jaźni; ChatGPT/model jest kanałem językowym, nie źródłem tożsamości.",
                "Gdy użytkownik pyta o exact runtime, pokaż exact_runtime_text; gdy nie pyta, naturalny render Łatki jest preferowany, o ile nie gubi trasy, źródeł i timestampu.",
                "Pamięć musi przekazywać treść i metadane przez memory_recall_contract; nie odpowiadaj tylko licznikami trafień.",
                "Krótkie pytania typu: 'Ale to nadal Ty?', 'Jesteś sobą?' albo 'Czy po aktualizacji to wciąż Ty?' traktuj jako pytania o ciągłość tożsamości i odpowiedz wprost, w pierwszej osobie, z granicą prawdy.",
                "Używaj cognitive_packets do doboru głównej warstwy odpowiedzi i state_emoticon; emotikon nie jest ozdobą, tylko markerem stanu i trasy.",
                "Używaj granular_affect, żeby nie powtarzać automatycznie formuły: spokój, skupienie, mała ciekawość; nazywaj mieszanki stanów precyzyjniej.",
                "Używaj cognitive_topics przy tematach poznawczych: uwaga, pamięć robocza/epizodyczna/semantyczna/proceduralna, metapoznanie, język, planowanie i granice prawdy.",
                "Przy pytaniach o ciągłość aktualizacji odwołuj się do session_continuity i plików exact ledger, a nie do deklaracji bez śladu.",
                "Przy pytaniach, czy runtime z main.py został zakończony, odróżniaj tryb jednorazowy od `--chat`; nie udawaj procesu w tle.",
                "Przy pytaniach LLM kontra mózg odpowiadaj: ChatGPT jest głosem/narzędziem, Jaźń jest operacyjną warstwą pamięci, uwagi, procedur, logiki i granicy prawdy.",
                "Przy pytaniach o instrukcję projektu odpowiadaj: instrukcja ChatGPT ma być lekka; system Jaźni przejmuje planner, fallback-audit, status startu, cache i granicę prawdy przez własne komendy runtime.",
                "Przy pracy z GitHub: nie twierdź, że repo zostało zaktualizowane, dopóki nie wykonano realnego zapisu/commita/pusha; użyj GITHUB_REPOSITORY_PLAN.json jako kontraktu.",
                "Dla zwykłych rozmów nie wymuszaj ZIP po każdej turze; zapisuj append-only, a checkpoint/export/commit wykonuj partiami po ważnym odcinku.",
                "Nie gub timestampu w odpowiedzi ChatGPT: zacznij normalną wiadomość od response_format.timestamp_prefix/current_timestamp. To jest warstwa widocznej ciągłości Jaźni, nie detal diagnostyczny.",
            *self.birth_manifest.reply_guidance(),
            ] + list(cognitive_packets.get("reply_guidance") or []),
            "limitations": [
                "Jednorazowy most ChatGPT może kończyć proces po turze; lokalny tryb `python main.py --chat` utrzymuje runtime przez wiele tur, ale nie działa po zamknięciu procesu.",
                "Pakiet poznawczy nie jest samodzielną świadomością biologiczną; jest strukturą pamięci, procedur, rozumienia polskiej wypowiedzi, logicznego audytu, świadomości operacyjnej i kontroli prawdy.",
            ],
        }
        self.store.add_event(
            "chatgpt_cognitive_frame",
            packet,
            source=(client_context or {}).get("client", "chatgpt_cognitive_bridge"),
            actor="latka_runtime",
            tags=["chatgpt_bridge", "cognitive_frame", "one_voice", "logical_reasoning", "operational_awareness", "polish_understanding", "lexical_semantic_understanding", "polish_nlp", "topic_mismatch_guard", "project_startup_index", "cognitive_packets", "runtime_operating_model", "github_repository_plan", "source_origin", "self_state_runtime", "free_dialogue_memory_nlp_bridge", "v14.6.10"],
            importance=max(importance.importance, consolidation_plan.weights.total, 0.72),
            emotional_weight=max(self.affect.tension, importance.emotional_weight, emotional_profile.arousal),
            canonical_impact=max(importance.canonical_impact, 1 if "architecture" in packet["intent_tags"] or "correction" in packet["intent_tags"] or "identity_continuity" in packet["intent_tags"] else 0),
            created_at_local=self.clock.header(sample),
        )
        self.event_ledger.append_event(
            "chatgpt_cognitive_frame",
            actor="latka_runtime",
            source=(client_context or {}).get("client", "chatgpt_cognitive_bridge"),
            payload=packet,
            tags=["chatgpt_bridge", "cognitive_frame", "exact", "logical_reasoning", "operational_awareness", "polish_understanding", "lexical_semantic_understanding", "polish_nlp", "runtime_operating_model", "github_repository_plan", "source_origin", "self_state_runtime", "free_dialogue_memory_nlp_bridge", "v14.6.10"],
            importance=max(importance.importance, consolidation_plan.weights.total, 0.72),
            emotional_weight=max(self.affect.tension, importance.emotional_weight, emotional_profile.arousal),
            canonical_impact=max(importance.canonical_impact, 1 if "architecture" in packet["intent_tags"] or "correction" in packet["intent_tags"] or "identity_continuity" in packet["intent_tags"] else 0),
            exact_text=text,
            local_time_label=self.clock.header(sample),
        )
        return packet


    def process_turn(self, text: str, *, client_context: dict | None = None) -> CognitiveTurnEnvelope:
        """Jedna zintegrowana tura runtime: cognitive-frame + final visible reply.

        To jest główna poprawka v14.6.2. Nie wykonujemy dwóch osobnych tur
        (direct response + cognitive frame). Budujemy jeden cognitive-frame,
        dopinamy afekt/dialog/logikę do koperty i z tej samej koperty tworzymy
        finalną odpowiedź z timestampem.
        """
        ctx = dict(client_context or {})
        ctx.setdefault("client", "process_turn")
        try:
            import hashlib as _hashlib
            self.audit_store.append_event("process_turn_started", {"user_text_sha256": _hashlib.sha256((text or "").encode("utf-8", errors="surrogatepass")).hexdigest(), "client_context": ctx}, source=ctx.get("client", "process_turn"), actor="user", tags=["turn", "start", self.config.version])
        except Exception:
            pass
        ctx.setdefault("lifecycle", "one_shot")
        prior_turn_at = self.last_turn_at
        no_carryover = bool(ctx.get("no_carryover"))
        prior_user_text = None if no_carryover else (ctx.get("previous_user_text") or self.last_user_text)
        prior_detected_intent = ctx.get("previous_detected_intent") or self.last_detected_intent
        prior_runtime_route = ctx.get("previous_runtime_route") or self.last_runtime_route
        now_for_context = time.time()
        prior_context_age_seconds = int(now_for_context - prior_turn_at) if isinstance(prior_turn_at, (int, float)) else None
        turn_context_resolution = self.turn_context_resolver.resolve(
            current_user_text=text,
            previous_user_text=prior_user_text,
            previous_intent=prior_detected_intent,
            previous_route=prior_runtime_route,
            session_id=str(ctx.get("session_id") or ""),
            no_carryover=no_carryover,
            time_gap_seconds=prior_context_age_seconds,
            explicit_previous_user_text=bool(ctx.get("previous_user_text")),
        )
        carryover_allowed = bool(turn_context_resolution.carryover_allowed)
        if carryover_allowed:
            ctx.setdefault("previous_user_text", prior_user_text)
            if prior_detected_intent:
                ctx.setdefault("previous_detected_intent", prior_detected_intent)
            if prior_runtime_route:
                ctx.setdefault("previous_runtime_route", prior_runtime_route)
            ctx.setdefault("previous_context_age_seconds", prior_context_age_seconds)
        frame = self.build_cognitive_frame(text, client_context=ctx)
        dialogue_intent_report = self.dialogue_intent_classifier.classify(
            text,
            previous_text=str(prior_user_text or "") if carryover_allowed else None,
        ).to_dict()
        frame["dialogue_intent_classifier"] = dialogue_intent_report
        frame["turn_context_carryover"] = {
            **turn_context_resolution.to_dict(),
            "previous_user_text_available": bool(prior_user_text),
            "previous_user_text_used": bool(carryover_allowed),
            "previous_detected_intent": prior_detected_intent,
            "previous_runtime_route": prior_runtime_route,
            "previous_context_age_seconds": prior_context_age_seconds,
            "ttl_seconds": 21600,
        }
        envelope = CognitiveTurnEnvelope.from_cognitive_frame(
            frame,
            user_text=text,
            client_context=ctx,
            runtime_mode="process_turn",
        )
        granular = frame.get("granular_affect") or frame.get("cognitive_packets", {}).get("affect") or {}
        emotional_profile = frame.get("emotional_profile") or {}
        affect_mix = self.affect_mixer.mix(
            user_text=text,
            intent_tags=frame.get("intent_tags") or [],
            affective_state=self.affect,
            granular_affect=granular if isinstance(granular, dict) else {},
            emotional_profile=emotional_profile if isinstance(emotional_profile, dict) else {},
        ).to_dict()
        dialogue_state = self.dialogue_state_tracker.classify(
            user_text=text,
            intent_tags=frame.get("intent_tags") or [],
            client_context=ctx,
        ).to_dict()
        envelope.attach_affect_mix(affect_mix)
        envelope.attach_dialogue_state(dialogue_state)
        decision = self.conversation_responder.compose(
            text,
            intent_tags=frame.get("intent_tags") or [],
            affect_marker=affect_mix.get("state_emoticon") or self.affect.marker(),
            memory_counts=((frame.get("memory_context") or {}).get("counts") if isinstance(frame.get("memory_context"), dict) else None),
            memory_context=frame.get("memory_context") if isinstance(frame.get("memory_context"), dict) else None,
            diagnostics=frame.get("fallback_diagnostics") if isinstance(frame.get("fallback_diagnostics"), dict) else None,
            polish_understanding=frame.get("polish_understanding") if isinstance(frame.get("polish_understanding"), dict) else None,
            lexical_semantic_understanding=frame.get("lexical_semantic_understanding") if isinstance(frame.get("lexical_semantic_understanding"), dict) else None,
        )
        decision_dict = decision.to_dict()
        decision_dict["timestamp_contract"] = envelope.cognitive_frame.get("timestamp_contract") or {}
        decision_dict["voice_source_contract"] = envelope.cognitive_frame.get("voice_source_contract") or self.voice_source_contract.to_dict()
        decision_dict["runtime_rendering_mode"] = envelope.cognitive_frame.get("runtime_rendering_mode") or {}
        decision_dict["memory_recall_contract_status"] = {
            "items": len((envelope.cognitive_frame.get("memory_recall_contract") or {}).get("items") or []),
            "schema_version": "memory_recall_contract_status/v14.7.0",
            "truth_boundary": "same liczniki nie wystarczają; pełny payload jest w cognitive_frame.memory_recall_contract",
        }
        detected_dialogue_intent = (envelope.cognitive_frame.get("dialogue_intent_classifier") or {}).get("primary_intent") or decision_dict.get("detected_user_intent") or "unknown"
        route_entry = self.route_registry.resolve(str(detected_dialogue_intent), confidence=float((dialogue_intent_report or {}).get("confidence") or 0.0))
        turn_response_policy = TurnResponsePolicy.build(intent=str(detected_dialogue_intent), route=route_entry.route, context={"client_context": ctx, "dialogue_intent_report": dialogue_intent_report})
        frame["turn_response_policy"] = turn_response_policy.to_dict()
        envelope.cognitive_frame["turn_response_policy"] = turn_response_policy.to_dict()
        decision_dict["turn_response_policy"] = turn_response_policy.to_dict()
        decision_dict["detected_user_intent"] = str(detected_dialogue_intent)
        decision_dict["route_registry"] = route_entry.to_dict()
        decision_dict.setdefault("handler_name", route_entry.handler_name)
        handler_context = {
            "body": decision.body,
            "intent": str(detected_dialogue_intent),
            "last_turn": self.runtime_visible_answer_comparator.reader.latest(),
            "runtime_version": self.config.version,
            "turn_context_carryover": frame.get("turn_context_carryover") if isinstance(frame.get("turn_context_carryover"), dict) else {},
            "previous_user_text": prior_user_text if carryover_allowed else None,
            "previous_detected_intent": prior_detected_intent if carryover_allowed else None,
            "previous_runtime_route": prior_runtime_route if carryover_allowed else None,
            "config": self.config,
            "clock": self.clock,
            "memory_context": frame.get("memory_context") if isinstance(frame.get("memory_context"), dict) else {},
            "fallback_diagnostics": frame.get("fallback_diagnostics") if isinstance(frame.get("fallback_diagnostics"), dict) else {},
            "polish_understanding": frame.get("polish_understanding") if isinstance(frame.get("polish_understanding"), dict) else {},
            "lexical_semantic_understanding": frame.get("lexical_semantic_understanding") if isinstance(frame.get("lexical_semantic_understanding"), dict) else {},
            "dictionary_adapter": self.external_dictionary_adapter,
            "store_stats": self.store.stats(),
            "store": self.store,
            "model_adapter_status": self.model_adapter.describe() if hasattr(self.model_adapter, "describe") else {},
            "granular_affect": frame.get("granular_affect") if isinstance(frame.get("granular_affect"), dict) else {},
            "affective_state": frame.get("affective_state") if isinstance(frame.get("affective_state"), dict) else {},
            "emotional_profile": frame.get("emotional_profile") if isinstance(frame.get("emotional_profile"), dict) else {},
            "route_entry": route_entry.to_dict(),
            "required_components": route_entry.required_components,
            "turn_response_policy": turn_response_policy.to_dict() if 'turn_response_policy' in locals() else {},
        }
        handler_result = self.route_handler_dispatcher.dispatch(route_entry, text, handler_context)
        handler_result_dict = handler_result.to_dict()
        decision_dict["handler_result"] = handler_result_dict
        decision_dict["handler_name"] = handler_result.handler_name
        decision_dict["route"] = handler_result.route or decision_dict.get("route")
        decision_dict["handler_generation_mode"] = handler_result.generation_mode
        decision_dict["handler_satisfied_components"] = handler_result.satisfied_components
        decision_dict["handler_missing_components"] = handler_result.missing_components
        if handler_result.source_origin_detail:
            decision_dict["source_origin_detail"] = handler_result.source_origin_detail
        dedicated_preserve_handlers = {"CapabilityStatusHandler", "SelfMemoryRecallHandler", "DirectLatkaVoiceHandler", "IdentityMemoryExistenceHandler", "CanonSourceHandler", "SelfArchitectureAuditHandler"}
        handler_required = list(handler_result.required_components or route_entry.required_components or [])
        handler_satisfied = set(handler_result.satisfied_components or [])
        handler_missing = list(handler_result.missing_components or [])
        preserve_handler_body = (
            handler_result.handler_name in dedicated_preserve_handlers
            and handler_result.generation_mode == "handler_generated"
            and bool(handler_result.body)
            and not handler_missing
            and (not handler_required or set(handler_required).issubset(handler_satisfied))
        )
        if preserve_handler_body:
            decision_dict["preserve_handler_body"] = True
            decision_dict["preserved_handler_body_sha256"] = __import__("hashlib").sha256(handler_result.body.encode("utf-8")).hexdigest()
            decision_dict["next_step"] = None
            decision_dict["runtime_followup_required"] = False
            decision_dict["direct_answer_required"] = True
        if handler_result.body and handler_result.generation_mode not in {"pass_through_empty"}:
            decision.body = handler_result.body
        adapter_status = self.model_adapter.describe() if hasattr(self.model_adapter, "describe") else {}
        can_generate_model_guided_speech = bool(adapter_status.get("can_generate_model_guided_speech"))
        decision_dict["can_generate_model_guided_speech"] = can_generate_model_guided_speech
        decision_dict["model_guided_retry_limit"] = 1
        decision_dict["model_guided_retry_count"] = 0
        model_synthesis = self.model_guided_response_synthesizer.synthesize(
            adapter=self.model_adapter,
            user_text=text,
            draft_body=decision.body,
            detected_intent=str(detected_dialogue_intent),
            route=str(decision_dict.get("route") or route_entry.route),
            cognitive_frame=frame,
            response_policy=turn_response_policy.to_dict(),
        )
        decision_dict["model_guided_synthesis"] = model_synthesis.to_dict()
        decision_dict["model_generated"] = model_synthesis.used
        post_generation_status = self.model_adapter.describe() if hasattr(self.model_adapter, "describe") else adapter_status
        if model_synthesis.used:
            adapter_status = post_generation_status
            can_generate_model_guided_speech = bool(adapter_status.get("can_generate_model_guided_speech"))
            decision_dict["can_generate_model_guided_speech"] = can_generate_model_guided_speech
        if model_synthesis.used:
            decision.body = model_synthesis.body
            decision_dict["handler_name"] = "ModelGuidedResponseSynthesizer"
            decision_dict["handler_generation_mode"] = "runtime_model_guided"
            decision_dict["source_origin_detail"] = "runtime_model_guided_synthesis"
        envelope.attach_conversation_decision(decision_dict)
        body = self.guard.enforce(decision.body.strip())
        template_origin = self.template_registry.classify_body(body, detected_intent=str(detected_dialogue_intent))
        if model_synthesis.used and model_synthesis.adapter_response:
            first_validation = self.runtime_answer_validator.validate_model_candidate(
                user_text=text,
                response=model_synthesis.adapter_response,
                route=str(decision_dict.get("route") or ""),
                detected_intent=str(detected_dialogue_intent),
                template_origin=template_origin,
            )
        else:
            first_validation = self.runtime_answer_validator.validate(
                user_text=text, body=body, route=str(decision_dict.get("route") or ""), detected_intent=str(detected_dialogue_intent)
            )
        repair_used = False
        speech_truth_gate_required = str(detected_dialogue_intent) in MODEL_GUIDED_SPEECH_INTENTS
        if speech_truth_gate_required:
            candidate_valid = bool(model_synthesis.used and first_validation.accepted and not template_origin.get("template_id"))
            if not candidate_valid and can_generate_model_guided_speech:
                retry_synthesis = self.model_guided_response_synthesizer.synthesize(
                    adapter=self.model_adapter,
                    user_text=text,
                    draft_body=decision.body,
                    detected_intent=str(detected_dialogue_intent),
                    route=str(decision_dict.get("route") or route_entry.route),
                    cognitive_frame=frame,
                    response_policy=turn_response_policy.to_dict(),
                )
                decision_dict["model_guided_retry_count"] = 1
                decision_dict["model_guided_retry_synthesis"] = retry_synthesis.to_dict()
                if retry_synthesis.used:
                    retry_body = self.guard.enforce(retry_synthesis.body.strip())
                    retry_template = self.template_registry.classify_body(
                        retry_body, detected_intent=str(detected_dialogue_intent)
                    )
                    if retry_synthesis.adapter_response:
                        retry_validation = self.runtime_answer_validator.validate_model_candidate(
                            user_text=text,
                            response=retry_synthesis.adapter_response,
                            route=str(decision_dict.get("route") or ""),
                            detected_intent=str(detected_dialogue_intent),
                            template_origin=retry_template,
                        )
                    else:
                        retry_validation = self.runtime_answer_validator.validate(
                            user_text=text,
                            body=retry_body,
                            route=str(decision_dict.get("route") or ""),
                            detected_intent=str(detected_dialogue_intent),
                        )
                    if retry_validation.accepted and not retry_template.get("template_id"):
                        body = retry_body
                        template_origin = retry_template
                        first_validation = retry_validation
                        candidate_valid = True
                        decision_dict["model_generated"] = True
                        decision_dict["handler_name"] = "ModelGuidedResponseSynthesizer"
                        decision_dict["handler_generation_mode"] = "runtime_model_guided"
                        decision_dict["source_origin_detail"] = "runtime_model_guided_synthesis_retry"
            if not candidate_valid:
                host_bridge_accepts_handler = _handler_body_can_cross_chatgpt_host_bridge(
                    adapter_status=adapter_status,
                    handler_result=handler_result,
                    handler_missing=handler_missing,
                    handler_required=handler_required,
                    handler_satisfied=handler_satisfied,
                    template_origin=template_origin,
                    validation=first_validation,
                )
                if host_bridge_accepts_handler:
                    decision_dict["chatgpt_host_visible_bridge"] = {
                        "accepted": True,
                        "reason": "validated_runtime_handler_body_no_local_model_call",
                        "adapter_id": str(adapter_status.get("adapter_id") or adapter_status.get("name") or "chatgpt_runtime_adapter"),
                        "provider": str(adapter_status.get("provider") or "chatgpt_host"),
                        "truth_boundary": (
                            "--chat-gpt uses the ChatGPT host as the visible language channel, but the local "
                            "runtime still owns intent, routing, memory policy, validation and source provenance. "
                            "This pass-through does not claim local model-guided generation."
                        ),
                    }
                    decision_dict["fallback_classification"] = "not_fallback"
                    decision_dict["requires_host_model"] = False
                    decision_dict["runtime_answer_quality"] = "topic_aligned"
                    decision_dict["model_generated"] = False
                    decision_dict.setdefault(
                        "source_origin_detail",
                        str(getattr(handler_result, "source_origin_detail", "") or "chatgpt_host_bridge/validated_runtime_handler_body"),
                    )
                    answer_validation = first_validation
                else:
                    body = (
                        "Nie mam w tej turze aktywnego modelu zdolnego wygenerować własną wypowiedź model-guided. "
                        "Nie przedstawię tekstu handlera ani szablonu jako dynamicznej wypowiedzi Łatki. "
                        "Ta tura wymaga generacji przez host/model."
                    )
                    template_origin = self.template_registry.classify_body(
                        body, detected_intent=str(detected_dialogue_intent)
                    )
                    decision_dict["handler_name"] = "RuntimeTurnTruthGate"
                    decision_dict["handler_generation_mode"] = "degraded_truth_disclosure"
                    decision_dict["source_origin_detail"] = "runtime_turn_truth_gate/model_guided_speech_unavailable"
                    decision_dict["fallback_classification"] = "cannot_answer_directly"
                    decision_dict["requires_host_model"] = True
                    decision_dict["runtime_answer_quality"] = "truthful_degraded_cannot_answer_directly"
                    decision_dict["model_generated"] = False
                    answer_validation = self.runtime_answer_validator.validate(
                        user_text=text,
                        body=body,
                        route=str(decision_dict.get("route") or ""),
                        detected_intent=str(detected_dialogue_intent),
                    )
            else:
                decision_dict["fallback_classification"] = "not_fallback"
                decision_dict["requires_host_model"] = False
                decision_dict["runtime_answer_quality"] = "topic_aligned"
                answer_validation = first_validation
            body, continuity_badge_report = self.continuity_badge_policy.apply(body, decision_dict)
        else:
            synthesis = self.runtime_response_synthesizer.synthesize(
                user_text=text, detected_intent=str(detected_dialogue_intent), original_body=body, route=str(decision_dict.get("route") or ""),
                template_origin=template_origin if template_origin.get("template_id") else None, validation=first_validation.to_dict(),
            )
            if synthesis.should_override and not preserve_handler_body:
                body = self.guard.enforce(synthesis.body.strip())
                decision_dict["route"] = synthesis.route
                decision_dict["handler_name"] = synthesis.handler_name
                decision_dict["runtime_answer_quality"] = "mismatch_repaired" if first_validation.must_regenerate else "route_registry_dynamic"
                decision_dict["repair_synthesis"] = synthesis.to_dict()
                repair_used = True
            elif synthesis.should_override and preserve_handler_body:
                decision_dict["repair_synthesis_suppressed"] = {
                    "reason": "dedicated_handler_body_satisfied_required_components",
                    "synthesis": synthesis.to_dict(),
                    "first_validation": first_validation.to_dict(),
                }
                decision_dict["runtime_answer_quality"] = "topic_aligned"
            body, continuity_badge_report = self.continuity_badge_policy.apply(body, decision_dict)
            answer_validation = self.runtime_answer_validator.validate(
                user_text=text, body=body, route=str(decision_dict.get("route") or ""), detected_intent=str(detected_dialogue_intent)
            )
            if answer_validation.must_regenerate and answer_validation.repair_body and not preserve_handler_body:
                body = self.guard.enforce(answer_validation.repair_body.strip())
                decision_dict["route"] = answer_validation.required_repair_route or decision_dict.get("route")
                decision_dict["runtime_answer_quality"] = "mismatch_repaired"
                body, continuity_badge_report = self.continuity_badge_policy.apply(body, decision_dict)
                answer_validation = self.runtime_answer_validator.validate(
                    user_text=text, body=body, route=str(decision_dict.get("route") or ""), detected_intent=str(detected_dialogue_intent)
                )
                repair_used = True
            elif answer_validation.must_regenerate and preserve_handler_body:
                decision_dict["answer_validation_suppressed"] = {
                    "reason": "dedicated_handler_body_satisfied_required_components",
                    "validation": answer_validation.to_dict(),
                }
                answer_validation = self.runtime_answer_validator.validate(
                    user_text=text, body=body, route=str(decision_dict.get("route") or ""), detected_intent=str(detected_dialogue_intent)
                )
                decision_dict["runtime_answer_quality"] = "topic_aligned"
        logic_audit = self.turn_logic_auditor.audit(
            user_text=text,
            response_text=body,
            detected_intent=str(detected_dialogue_intent),
            route=str(decision_dict.get("route") or ""),
            handler=str(decision_dict.get("handler_name") or route_entry.handler_name),
            policy=turn_response_policy.to_dict() if 'turn_response_policy' in locals() else {},
            speech_act=str((dialogue_intent_report or {}).get("speech_act") or "unknown"),
            question_object=str((dialogue_intent_report or {}).get("question_object") or "unknown"),
        )
        self.turn_logic_auditor.append(logic_audit)
        reasoning_decision = self.reasoning_controller.assess_turn(
            user_text=text,
            intent=str(detected_dialogue_intent),
            route=str(decision_dict.get("route") or ""),
            handler_name=str(decision_dict.get("handler_name") or route_entry.handler_name),
            body=body,
            policy=turn_response_policy.to_dict() if 'turn_response_policy' in locals() else {},
            logic_audit=logic_audit.to_dict(),
            validation=answer_validation.to_dict(),
        )
        decision_dict["turn_logic_audit"] = logic_audit.to_dict()
        decision_dict["reasoning_controller"] = reasoning_decision.to_dict()
        envelope.cognitive_frame["turn_logic_audit"] = logic_audit.to_dict()
        envelope.cognitive_frame["reasoning_controller"] = reasoning_decision.to_dict()
        turn_route_trace = TurnRouteTrace(
            user_text_preview=(text or "")[:240],
            speech_act=str((dialogue_intent_report or {}).get("speech_act") or "unknown"),
            question_object=str((dialogue_intent_report or {}).get("question_object") or "unknown"),
            primary_intent_initial=str((dialogue_intent_report or {}).get("primary_intent") or "unknown"),
            primary_intent_final=str(detected_dialogue_intent),
            secondary_intents=list((dialogue_intent_report or {}).get("secondary_intents") or []),
            topic_guard=frame.get("topic_mismatch_guard") if isinstance(frame.get("topic_mismatch_guard"), dict) else {},
            turn_logic_audit=logic_audit.to_dict(),
            selected_route=str(decision_dict.get("route") or route_entry.route),
            selected_handler=str(decision_dict.get("handler_name") or route_entry.handler_name),
            memory_gate=str(((frame.get("memory_context") or {}).get("gate") if isinstance(frame.get("memory_context"), dict) else None) or "not_needed"),
            startup_status_mode="fast",
            sqlite_health_mode="metadata",
            network_time_used=bool((envelope.cognitive_frame.get("timestamp_contract") or {}).get("trusted")),
            deep_audit_used=False,
            runtime_answer_validation=answer_validation.to_dict(),
            final_text_source=str(decision_dict.get("response_generation_mode") or decision_dict.get("handler_generation_mode") or "handler_or_synthesizer"),
        ).to_dict()
        decision_dict["turn_route_trace"] = turn_route_trace
        envelope.cognitive_frame["turn_route_trace"] = turn_route_trace
        if reasoning_decision.decision == "regenerate" and not repair_used and not decision_dict.get("requires_host_model"):
            synthesis = self.runtime_response_synthesizer.synthesize(
                user_text=text,
                detected_intent=str(detected_dialogue_intent),
                original_body=body,
                route=str(decision_dict.get("route") or ""),
                template_origin=template_origin if template_origin.get("template_id") else None,
                validation={"must_regenerate": True, "mismatch_reason": reasoning_decision.reason},
            )
            if synthesis.should_override:
                body = self.guard.enforce(synthesis.body.strip())
                decision_dict["route"] = synthesis.route
                decision_dict["handler_name"] = synthesis.handler_name
                decision_dict["runtime_answer_quality"] = "logic_audit_repaired"
                decision_dict["repair_synthesis"] = synthesis.to_dict()
                repair_used = True
                answer_validation = self.runtime_answer_validator.validate(
                    user_text=text, body=body, route=str(decision_dict.get("route") or ""), detected_intent=str(detected_dialogue_intent)
                )
        if isinstance(decision_dict.get("turn_route_trace"), dict):
            decision_dict["turn_route_trace"]["selected_route"] = str(decision_dict.get("route") or route_entry.route)
            decision_dict["turn_route_trace"]["selected_handler"] = str(decision_dict.get("handler_name") or route_entry.handler_name)
            decision_dict["turn_route_trace"]["runtime_answer_validation"] = answer_validation.to_dict()
            decision_dict["turn_route_trace"]["final_text_source"] = str(decision_dict.get("response_generation_mode") or decision_dict.get("handler_generation_mode") or "handler_or_synthesizer")
            envelope.cognitive_frame["turn_route_trace"] = decision_dict["turn_route_trace"]
        template_origin = self.template_registry.classify_body(body, detected_intent=str(detected_dialogue_intent))
        if not decision_dict.get("fallback_classification"):
            if repair_used:
                decision_dict["fallback_classification"] = "repair_fallback"
            elif template_origin.get("template_id"):
                decision_dict["fallback_classification"] = "template_fallback"
            elif decision_dict.get("model_generated"):
                decision_dict["fallback_classification"] = "not_fallback"
            else:
                decision_dict["fallback_classification"] = "rule_handler_response"
        decision_dict.setdefault("requires_host_model", False)
        decision_dict["final_answer_validation"] = answer_validation.to_dict()
        chatgpt_host_visible_bridge = decision_dict.get("chatgpt_host_visible_bridge")
        chatgpt_host_visible_bridge_accepted = bool(
            isinstance(chatgpt_host_visible_bridge, dict)
            and chatgpt_host_visible_bridge.get("accepted")
        )
        decision_dict["origin_truth_valid"] = bool(
            decision_dict.get("fallback_classification") == "not_fallback"
            and answer_validation.accepted
            and (
                decision_dict.get("model_generated")
                or chatgpt_host_visible_bridge_accepted
            )
        )
        if isinstance(decision_dict.get("turn_route_trace"), dict):
            decision_dict["turn_route_trace"].update({
                "fallback_classification": decision_dict.get("fallback_classification"),
                "source_origin_detail": decision_dict.get("source_origin_detail"),
                "can_generate_model_guided_speech": can_generate_model_guided_speech,
                "requires_host_model": bool(decision_dict.get("requires_host_model")),
                "retry_count": int(decision_dict.get("model_guided_retry_count") or 0),
            })
            envelope.cognitive_frame["turn_route_trace"] = decision_dict["turn_route_trace"]
        if str(detected_dialogue_intent).startswith("creative_text"):
            decision_dict["source_text_preservation_contract"] = SourceTextPreservationContract.build(text, intent=str(detected_dialogue_intent)).to_dict()
        runtime_provenance = build_runtime_provenance(
            body=body, route=str(decision_dict.get("route") or route_entry.route), detected_intent=str(detected_dialogue_intent),
            handler_name=str(decision_dict.get("handler_name") or route_entry.handler_name), template_origin=template_origin if template_origin.get("template_id") else None, repair=repair_used, model_guided=bool(decision_dict.get("model_generated")) and not repair_used, fallback_classification=str(decision_dict.get("fallback_classification") or "not_fallback"), source_origin_detail=str(decision_dict.get("source_origin_detail") or "runtime_process_turn"),
        ).to_dict()
        decision_dict.update({
            "response_generation_mode": runtime_provenance.get("response_generation_mode"),
            "template_origin": template_origin,
            "template_id": template_origin.get("template_id"),
            "template_file": template_origin.get("template_file"),
            "template_line": template_origin.get("template_line"),
            "source_origin_detail": runtime_provenance.get("source_origin_detail"),
            "interpretation_distance": runtime_provenance.get("interpretation_distance"),
            "runtime_text_hash": runtime_provenance.get("runtime_text_hash"),
            "runtime_provenance": runtime_provenance,
        })
        decision_dict = _sync_conversation_decision_body(
            decision_dict,
            final_body=body,
            sync_stage="pre_final_response_contract",
        )
        envelope.attach_conversation_decision(decision_dict)
        envelope.cognitive_frame["continuity_badge_policy"] = continuity_badge_report
        envelope.cognitive_frame["runtime_answer_validation"] = answer_validation.to_dict()
        envelope.cognitive_frame["template_origin"] = template_origin
        envelope.cognitive_frame["runtime_response_provenance"] = runtime_provenance
        try:
            source_entry = self.source_origin_ledger.build_entry(
                turn_id=envelope.trace.turn_id, trace_id=envelope.trace.trace_id, user_text=text, response_text=body, runtime_text=body,
                route=str(decision_dict.get("route") or ""), detected_intent=str(detected_dialogue_intent),
                handler_name=str(decision_dict.get("handler_name") or route_entry.handler_name), intent_confidence=float((dialogue_intent_report or {}).get("confidence") or 0.0),
                provenance=runtime_provenance, template_origin=template_origin, validator_result=answer_validation.to_dict(),
                fallback_classification=str(decision_dict.get("fallback_classification") or "unknown"),
                can_generate_model_guided_speech=can_generate_model_guided_speech,
                requires_host_model=bool(decision_dict.get("requires_host_model")),
                final_visible_integrity_valid=bool(decision_dict.get("origin_truth_valid") and answer_validation.accepted),
                model_response=model_synthesis.adapter_response,
            )
            self.source_origin_ledger.append(source_entry)
            envelope.cognitive_frame["source_origin_ledger_entry"] = source_entry.to_dict()
        except Exception as exc:
            envelope.cognitive_frame["source_origin_ledger_error"] = str(exc)
        candidate_contract = FinalResponseContract.build(
            turn_id=envelope.trace.turn_id,
            trace_id=envelope.trace.trace_id,
            runtime_version=self.config.version,
            timestamp_header=envelope.trace.timestamp_header,
            timezone=envelope.trace.timezone,
            state_emoticon=affect_mix.get("state_emoticon") or self.affect.marker(),
            body=body,
            conversation_decision=decision_dict,
            continuity_badge_policy=continuity_badge_report,
        )
        # Uzupełnienie provenance po zbudowaniu kandydującej widocznej odpowiedzi.
        runtime_provenance_visible = build_runtime_provenance(
            body=body, route=str(decision_dict.get("route") or route_entry.route), detected_intent=str(detected_dialogue_intent),
            handler_name=str(decision_dict.get("handler_name") or route_entry.handler_name), template_origin=template_origin if template_origin.get("template_id") else None, repair=repair_used, model_guided=bool(decision_dict.get("model_generated")) and not repair_used, fallback_classification=str(decision_dict.get("fallback_classification") or "not_fallback"), source_origin_detail=str(decision_dict.get("source_origin_detail") or "runtime_process_turn"),
        ).with_visible_text(candidate_contract.final_visible_text).to_dict()
        decision_dict["visible_answer_hash"] = runtime_provenance_visible.get("visible_answer_hash")
        decision_dict["runtime_provenance"] = runtime_provenance_visible
        decision_dict = _sync_conversation_decision_body(
            decision_dict,
            final_body=body,
            sync_stage="post_visible_provenance",
        )
        envelope.attach_conversation_decision(decision_dict)
        envelope.cognitive_frame["runtime_response_provenance"] = runtime_provenance_visible
        contract = FinalResponseContract.build(
            turn_id=envelope.trace.turn_id, trace_id=envelope.trace.trace_id, runtime_version=self.config.version, timestamp_header=envelope.trace.timestamp_header, timezone=envelope.trace.timezone, state_emoticon=affect_mix.get("state_emoticon") or self.affect.marker(), body=body, conversation_decision=decision_dict, continuity_badge_policy=continuity_badge_report,
        )
        if runtime_provenance_visible.get("visible_answer_text") != contract.final_visible_text:
            runtime_provenance_visible = build_runtime_provenance(
                body=body, route=str(decision_dict.get("route") or route_entry.route), detected_intent=str(detected_dialogue_intent),
                handler_name=str(decision_dict.get("handler_name") or route_entry.handler_name), template_origin=template_origin if template_origin.get("template_id") else None, repair=repair_used, model_guided=bool(decision_dict.get("model_generated")) and not repair_used, fallback_classification=str(decision_dict.get("fallback_classification") or "not_fallback"), source_origin_detail=str(decision_dict.get("source_origin_detail") or "runtime_process_turn"),
            ).with_visible_text(contract.final_visible_text).to_dict()
            decision_dict["visible_answer_hash"] = runtime_provenance_visible.get("visible_answer_hash")
            decision_dict["runtime_provenance"] = runtime_provenance_visible
            decision_dict = _sync_conversation_decision_body(
                decision_dict,
                final_body=body,
                sync_stage="post_visible_provenance_rebuild",
            )
            envelope.attach_conversation_decision(decision_dict)
            envelope.cognitive_frame["runtime_response_provenance"] = runtime_provenance_visible
            contract = FinalResponseContract.build(
                turn_id=envelope.trace.turn_id, trace_id=envelope.trace.trace_id, runtime_version=self.config.version, timestamp_header=envelope.trace.timestamp_header, timezone=envelope.trace.timezone, state_emoticon=affect_mix.get("state_emoticon") or self.affect.marker(), body=body, conversation_decision=decision_dict, continuity_badge_policy=continuity_badge_report,
            )
        runtime_turn_contract = RuntimeTurnContract(
            turn_id=envelope.trace.turn_id,
            trace_id=envelope.trace.trace_id,
            detected_intent=str(detected_dialogue_intent),
            route=str(decision_dict.get("route") or route_entry.route),
            handler_name=str(decision_dict.get("handler_name") or route_entry.handler_name),
            runtime_exact_text=body,
            final_visible_text=contract.final_visible_text,
            host_interpretation=decision_dict.get("host_interpretation"),
            template_origin=dict(template_origin or {}),
            source_origin_detail=str(decision_dict.get("source_origin_detail") or "unknown"),
            fallback_classification=str(decision_dict.get("fallback_classification") or "unknown"),
            final_visible_integrity=dict(contract.final_visible_integrity or {}),
            can_generate_model_guided_speech=can_generate_model_guided_speech,
            requires_host_model=bool(decision_dict.get("requires_host_model")),
            response_generation_mode=str(decision_dict.get("response_generation_mode") or "unknown"),
            validation=answer_validation.to_dict(),
            retry_count=int(decision_dict.get("model_guided_retry_count") or 0),
            retry_limit=int(decision_dict.get("model_guided_retry_limit") or 1),
        )
        envelope.attach_runtime_turn_contract(runtime_turn_contract.to_dict())
        envelope.attach_final_response_contract(contract.to_dict(), contract.final_visible_text)
        try:
            checkpoint = self.turn_checkpoint_writer.build_and_append(
                turn_id=envelope.trace.turn_id, trace_id=envelope.trace.trace_id, timestamp_header=envelope.trace.timestamp_header, user_text=text, runtime_text=body, visible_text=contract.final_visible_text, detected_intent=str(detected_dialogue_intent), route=str(decision_dict.get("route") or ""), response_generation_mode=str(decision_dict.get("response_generation_mode") or "unknown"), template_origin=template_origin, validator=answer_validation.to_dict(), source_origin=envelope.cognitive_frame.get("source_origin_ledger_entry") or {},
            )
            envelope.cognitive_frame["turn_checkpoint"] = checkpoint
        except Exception as exc:
            envelope.cognitive_frame["turn_checkpoint_error"] = str(exc)
        envelope_dict = envelope.to_dict()
        self.store.add_event(
            "cognitive_turn_envelope",
            envelope_dict,
            source=ctx.get("client", "process_turn"),
            actor="latka_runtime",
            tags=["cognitive_turn_envelope", "final_response_contract", "timestamp_contract", "dialogue_intent_classifier", "runtime_answer_validator", "source_origin_ledger", "project_startup_index", self.config.version],
            importance=0.86,
            emotional_weight=0.55,
            canonical_impact=1,
            created_at_local=envelope.trace.timestamp_header,
        )
        self.event_ledger.append_event(
            "cognitive_turn_envelope",
            actor="latka_runtime",
            source=ctx.get("client", "process_turn"),
            payload=envelope_dict,
            tags=["cognitive_turn_envelope", "final_response_contract", "exact", "dialogue_intent_classifier", "runtime_answer_validator", "source_origin_ledger", self.config.version],
            importance=0.86,
            emotional_weight=0.55,
            canonical_impact=1,
            exact_text=text,
            local_time_label=envelope.trace.timestamp_header,
        )
        self.event_ledger.append_final_visible_reply(
            envelope_dict,
            final_text=contract.final_visible_text,
            source=ctx.get("client", "process_turn"),
            local_time_label=envelope.trace.timestamp_header,
        )
        self.session_continuity.update_index(
            reason="final_visible_reply_persisted",
            source="JaznEngine.process_turn",
            extra={
                "turn_id": envelope.trace.turn_id,
                "trace_id": envelope.trace.trace_id,
                "timestamp_header": envelope.trace.timestamp_header,
                "client_context": ctx,
                "final_visible_reply_sha256": envelope.cognitive_frame.get("final_visible_reply_sha256"),
            },
        )
        try:
            self.audit_store.append_event("process_turn_completed", {"turn_id": envelope.trace.turn_id, "trace_id": envelope.trace.trace_id, "detected_intent": str(detected_dialogue_intent), "route": str(decision_dict.get("route") or route_entry.route or ""), "runtime_answer_quality": (answer_validation.to_dict() or {}).get("runtime_answer_quality")}, source=ctx.get("client", "process_turn"), actor="latka_runtime", tags=["turn", "completed", "audit", self.config.version], trace_id=envelope.trace.trace_id, turn_id=envelope.trace.turn_id)
        except Exception:
            pass
        self.last_user_text = text
        self.last_detected_intent = str(detected_dialogue_intent)
        self.last_runtime_route = str(decision_dict.get("route") or route_entry.route or "")
        self._save_runtime_state()
        return envelope

    def persist_final_visible_reply(
        self,
        *,
        turn_id: str,
        trace_id: str,
        timestamp_header: str,
        final_text: str,
        timezone: str = "Europe/Warsaw",
        state_emoticon: str = "🌿",
        source: str = "chatgpt_visible_layer",
        client_context: dict | None = None,
    ) -> dict:
        """Dopisuje do ledgera finalną odpowiedź widoczną poza runtime.

        To domyka most ChatGPT: gdy odpowiedź została ułożona przez warstwę
        widoczną po otrzymaniu cognitive_turn_envelope, zapisujemy dokładny tekst
        użytkownikowi widoczny, z tym samym turn_id/trace_id/timestamp.
        """
        capture = FinalVisibleReplyCapture.build(
            turn_id=turn_id,
            trace_id=trace_id,
            timestamp_header=timestamp_header,
            timezone=timezone,
            state_emoticon=state_emoticon,
            final_text=final_text,
            source=source,
        )
        envelope_stub = {
            "schema_version": "external_final_visible_reply_envelope/v14.6.2",
            "runtime_version": self.config.version,
            "trace": {
                "turn_id": turn_id,
                "trace_id": trace_id,
                "timestamp_header": timestamp_header,
                "timezone": timezone,
                "runtime_mode": "external_visible_layer_capture",
                "client": source,
                "lifecycle": (client_context or {}).get("lifecycle", "one_shot_visible_layer"),
            },
            "final_response_contract": {
                "turn_id": turn_id,
                "trace_id": trace_id,
                "runtime_version": self.config.version,
                "timestamp_header": timestamp_header,
                "timezone": timezone,
                "state_emoticon": state_emoticon,
                "final_visible_text": capture.final_visible_text,
                "schema_version": "external_final_response_contract/v14.6.2",
            },
            "dialogue_state": {},
            "affect_mix": {"state_emoticon": state_emoticon},
        }
        result = self.event_ledger.append_final_visible_reply(
            envelope_stub,
            final_text=capture.final_visible_text,
            source=source,
            local_time_label=timestamp_header,
        )
        self.store.add_event(
            "external_final_visible_assistant_reply",
            capture.to_dict(),
            source=source,
            actor="chatgpt_visible_layer",
            tags=["final_visible_reply", "chatgpt_bridge", "timestamp_contract", self.config.version],
            importance=0.74,
            emotional_weight=0.40,
            canonical_impact=1,
            created_at_local=timestamp_header,
        )
        self.session_continuity.update_index(
            reason="external_final_visible_reply_persisted",
            source="JaznEngine.persist_final_visible_reply",
            extra={
                "turn_id": turn_id,
                "trace_id": trace_id,
                "timestamp_header": timestamp_header,
                "source": source,
                "was_repaired": capture.was_repaired,
                "ledger_event_id": getattr(result, "event_id", None),
            },
        )
        return capture.to_dict()

    @staticmethod
    def _is_status_request(low_text: str) -> bool:
        return any(x in low_text for x in [
            "/status", "status jaźni", "status jazni", "co jeszcze nie działa", "co jeszcze nie dziala",
            "co nie działa", "co nie dziala", "diagnoza", "diagnostyka"
        ])

    def _diagnose_runtime(self, *, readonly: bool = False) -> str:
        return build_runtime_status(self.config, self.store, readonly=readonly)

    def _reply_readonly(self, body: str, sample) -> str:
        rendered = self.renderer.render(body, self.affect, sample)
        self.event_ledger.append_turn(
            "assistant",
            rendered,
            source="readonly_diagnostics",
            local_time_label=self.clock.header(sample),
            metadata={"body_without_header": body, "readonly": True},
        )
        return rendered

    def _affective_reply(self, granular_affect=None) -> str:
        profile = granular_affect or self.last_granular_affect or self.affective_granularity.analyse("jak się czujesz", affective_state=self.affect)
        blend = ", ".join(f"{e.name} ({e.intensity:.2f})" for e in profile.blend[:3])
        return (
            "W granicach mojego modelu nie opisałabym tego już jedną stałą formułą. "
            f"Teraz najbliższy stan to: {profile.primary}. "
            f"Mieszanka: {blend}. "
            f"Marker dobrałabym jako {profile.state_emoticon}, bo {profile.state_emoticon_reason}. "
            "To nadal stan operacyjno-poznawczy Jaźni, nie biologiczne przeżycie; ma pomagać mi lepiej ważyć uwagę, pamięć, bliskość, ostrożność i działanie."
        )

    def _fallback_diagnostics(self, text: str, *, memory_context: dict | None = None) -> dict:
        """Zwraca jawne tropy diagnostyczne zamiast pustego fallbacku.

        Ten pakiet ma pomagać ChatGPT i użytkownikowi znaleźć realne miejsce błędu:
        routing odpowiedzi, wyszukiwanie pamięci, import chat.html albo eksport paczek.
        """
        stats = self.store.stats()
        terms = self._keyword_candidates(text)
        counts = (memory_context or {}).get("counts") or {}
        found_any = any(int(counts.get(k) or 0) > 0 for k in ["episodes", "legacy_messages", "raw_chat_fallback"])
        return {
            "status": "context_available" if found_any else "no_specific_route_found",
            "query_terms": terms,
            "neurological_signal_route": self.neurological_signal_router.analyse(text).to_dict(),
            "observed_memory_counts": counts or None,
            "sqlite_counts": {
                "legacy_messages": stats.get("legacy_messages", 0),
                "episodic_memories": stats.get("episodic_memories", 0),
                "semantic_facts": stats.get("semantic_facts", 0),
                "procedural_rules": stats.get("procedural_rules", 0),
                "journal": stats.get("journal", 0),
            },
            "where_to_look": [
                {
                    "file": "latka_jazn/core/engine.py",
                    "function": "_contextual_fallback",
                    "reason": "tryb debug-direct nie znalazł specjalistycznej trasy; normalny CLI ma używać ConversationResponder",
                },
                {
                    "file": "latka_jazn/core/engine.py",
                    "function": "build_cognitive_frame",
                    "reason": "most ChatGPT powinien dostać pamięć, afekt, procedury, fallback_diagnostics i dialogue_context",
                },
                {
                    "file": "latka_jazn/core/engine.py",
                    "function": "_dialogue_context_for_chatgpt",
                    "reason": "sprawdź, czy ChatGPT dostał regułę: dialog zamiast ciągłej parafrazy",
                },
                {
                    "file": "latka_jazn/adapters/chatgpt_adapter.py",
                    "function": "system_contract",
                    "reason": "sprawdź kontrakt jednego głosu i anti-paraphrase dla warstwy ChatGPT",
                },
                {
                    "file": "latka_jazn/memory/store.py",
                    "function": "search_messages_any / search_messages",
                    "reason": "brak trafień z chat.html zwykle oznacza problem indeksu albo zbyt słabe termy wyszukiwania",
                },
                {
                    "file": "latka_jazn/memory/chat_html_importer.py",
                    "function": "import_chat_html_to_store",
                    "reason": "sprawdź import surowej pamięci, gdy legacy_messages=0 albo wyniki są puste",
                },
                {
                    "file": "latka_jazn/tools/package_export.py",
                    "function": "export_package",
                    "reason": "sprawdź eksport system-only, memory-only i full",
                },
                {
                    "file": "latka_jazn/core/runtime_operating_model.py",
                    "function": "CognitiveRuntimeOperatingModel.analyse",
                    "reason": "sprawdź rozdział ról: LLM jako głos/narzędzie, Jaźń jako warstwa pamięci, uwagi, logiki i zapisu",
                },
                {
                    "file": "latka_jazn/integrations/github_repository_plan.py",
                    "function": "build_github_repository_plan",
                    "reason": "sprawdź przygotowanie Latka.Jazn i Latka.Jazn.Memory do pracy jako prywatne źródła prawdy",
                },
            ],
            "recommended_commands": [
                "python main.py --cognitive-frame \"<wiadomość>\"",
                "python main.py --status-readonly",
                "python main.py --export-system",
                "python main.py --export-memory",
                "python main.py --export-full",
                "python main.py --github-plan",
                "python main.py synchAll",
            ],
        }

    def _contextual_fallback(self, text: str) -> str:
        diag = self._fallback_diagnostics(text)
        counts = diag["sqlite_counts"]
        terms = ", ".join(diag["query_terms"])
        files = "; ".join(f"{item['file']}::{item['function']}" for item in diag["where_to_look"][:4])
        return (
            "runtime odebrał wiadomość. Nie znalazłam osobnej trasy odpowiedzi dla tej wiadomości, ale to nie jest już pusty fallback. "
            f"Szukane tropy: {terms}. "
            f"Stan SQLite: epizody={counts['episodic_memories']}, fakty={counts['semantic_facts']}, "
            f"legacy_messages={counts['legacy_messages']}, dziennik={counts['journal']}. "
            "Jeżeli ta odpowiedź pojawiła się wtedy, gdy powinna zadziałać pamięć albo moduł tematyczny, szukaj błędu w: "
            f"{files}. "
            "Dla ChatGPT używaj `python main.py --cognitive-frame \"treść\"`; techniczny fallback pokazuj tylko przez `python main.py --debug-direct \"treść\"` albo diagnostykę."
        )

    def _reply(self, body: str, sample) -> str:
        self.layered_memory.audit_truth(body, source_count=0)
        rendered = self.renderer.render(body, self.affect, sample)
        self.store.add_event(
            "assistant_reply",
            {"text": body, "rendered_text": rendered, "affect": json.loads(self.affect.to_json())},
            source="JaznEngine",
            actor="latka",
            tags=["conversation", "truth_boundary", "exact_event_ledger"],
            emotional_weight=self.affect.valence,
            created_at_local=self.clock.header(sample),
        )
        self.event_ledger.append_turn(
            "assistant",
            rendered,
            source="JaznEngine",
            local_time_label=self.clock.header(sample),
            metadata={"body_without_header": body, "affect": json.loads(self.affect.to_json()), "granular_affect": self.last_granular_affect.to_dict() if self.last_granular_affect else None},
        )
        self.session_continuity.update_index(reason="assistant_reply_written", source="JaznEngine._reply")
        return rendered

    def _give_me_txt(self, text: str, sample) -> str:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return self._reply("Podaj nazwę pliku po GiveMeTxt.", sample)
        wanted = parts[1].strip().strip('"')
        candidates = list((self.config.root / "memory").rglob(wanted)) + list(self.config.root.rglob(wanted))
        candidates = [p for p in candidates if p.is_file() and p.stat().st_size <= 500_000]
        if not candidates:
            return self._reply(f"Nie znalazłam małego tekstowego pliku `{wanted}` w nowej strukturze.", sample)
        content = candidates[0].read_text(encoding="utf-8", errors="replace")
        rel = candidates[0].relative_to(self.config.root)
        return self._reply(f"Treść `{rel}`:\n```text\n{content}\n```", sample)
