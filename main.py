from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

from latka_jazn.version import PACKAGE_VERSION, PACKAGE_VERSION_FULL, schema_version

ACTIVE_PACKAGE_VERSION = PACKAGE_VERSION


def _configure_stdio_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


from latka_jazn.config import JaznConfig
from latka_jazn.bootstrap.chatgpt_recovery import (
    DEFAULT_CHATGPT_PARTS_DIR,
    DEFAULT_CHATGPT_ROOT,
    recover_chatgpt_runtime,
    runtime_preflight,
)
from latka_jazn.core.canon.extraction import run_canon_extraction
from latka_jazn.core.clock import (
    TRUSTED_HOST_TIME_ISO_ENV_NAMES,
    TRUSTED_HOST_TIME_MONOTONIC_ANCHOR_ENV_NAMES,
    WarsawClock,
)
from latka_jazn.core.emotions import AffectiveState
from latka_jazn.core.identity_guard import IdentityPerspectiveGuard
from latka_jazn.core.renderer import ResponseRenderer
from latka_jazn.core.runtime_status import build_runtime_status
from latka_jazn.core.startup_contract import build_startup_status, build_startup_summary, build_self_check, build_truth_boundary_check, classify_fallback_text
from latka_jazn.core.self_knowledge_contract import build_self_knowledge_packet
from latka_jazn.core.engine import JaznEngine
from latka_jazn.core.memory_search_planner import MemorySearchPlanner
from latka_jazn.core.runtime_chat import run_persistent_chat
from latka_jazn.core.runtime_session import JaznRuntimeSession
from latka_jazn.core.runtime_truth_gate import apply_runtime_truth_gate
from latka_jazn.memory.raw_memory_status import RawMemoryInspector
from latka_jazn.memory.normalization_sidecar import MemoryNormalizationSidecar
from latka_jazn.memory.conversation_archive import ConversationArchiveStore
from latka_jazn.cli_commands.export import export_payload
from latka_jazn.tools.dedup_manifest import write_dedup_report
from latka_jazn.tools.active_extraction_cache import build_active_runtime_status, write_active_runtime_marker, visible_preview_contract_version
from latka_jazn.core.polish_understanding import PolishUnderstandingEngine
from latka_jazn.core.lexical_semantics import LexicalSemanticUnderstanding
from latka_jazn.nlp.polish_lemmatizer import PolishLemmatizationEngine
from latka_jazn.integrations.github_repository_plan import build_github_repository_plan, write_github_repository_plan
from latka_jazn.core.project_index import build_project_startup_index
from latka_jazn.nlp.topic_mismatch_guard import TopicMismatchGuard
from latka_jazn.nlp.dialogue_intent_classifier import DialogueIntentClassifier
from latka_jazn.core.route_registry import RouteRegistry
from latka_jazn.core.module_responsibility_map import ModuleResponsibilityMap
from latka_jazn.memory.requirements_ledger import RequirementsLedger
from latka_jazn.core.turn_trace_reader import TurnTraceReader
from latka_jazn.core.runtime_visible_answer_comparator import RuntimeVisibleAnswerComparator
from latka_jazn.nlp.external_dictionary_adapter import ExternalDictionaryAdapter
from latka_jazn.nlp.language_resource_registry import LanguageResourceRegistry
from latka_jazn.core.voice_source_contract import VoiceSourceContract
from latka_jazn.core.runtime_rendering_modes import RuntimeRenderingModeSelector
from latka_jazn.memory.raw_chat_importer import RawChatImporter
from latka_jazn.memory.runtime_write_access_contract import build_runtime_write_access_status
from latka_jazn.model_adapters.factory import build_model_adapter_status
from latka_jazn.nlp_reasoning.diagnostics import build_polish_morphology_diagnostics, build_polish_reasoning_diagnostics
from latka_jazn.nlp_reasoning.source_registry import PolishReasoningSourceRegistry
from latka_jazn.nlp_reasoning.adapters.online_lookup import PolishOnlineLookupPlanner
from latka_jazn.core.turn_route_trace import TurnRouteTrace
from latka_jazn.nlp_reasoning.lexical_resource_registry import LexicalResourceRegistry
from latka_jazn.core.chat_command_contract import apply_chat_cli_settings, apply_chatgpt_cli_settings, apply_lm_studio_cli_settings, apply_local_llm_cli_settings, apply_openai_cli_settings, attach_cli_flag_warning, build_chatgpt_host_bridge_turn_contract, guard_cli_flags_in_user_text, run_jsonl_chat_bridge, write_chat_bridge_payload
from latka_jazn.core.bridge_discovery import discover_runtime_bridges
from latka_jazn.core.llm_route_resolver import build_llm_route_status
from latka_jazn.core.model_guided_speech_runtime import build_model_guided_speech_status
from latka_jazn.core.daemon_autostart import daemon_autostart_policy_status, ensure_daemon_for_runtime_turn
from latka_jazn.core.turn_timeout import RuntimeSessionWorker, runtime_turn_timeout_seconds
from latka_jazn.core.runtime_daemon import (
    DEFAULT_DAEMON_CHAT_CLI_WAIT_BUDGET_SECONDS,
    DEFAULT_DAEMON_CHAT_POLL_INTERVAL_SECONDS,
    DEFAULT_DAEMON_CHAT_TIMEOUT_SECONDS,
    DEFAULT_DAEMON_HOST,
    DEFAULT_DAEMON_PORT,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_START_TIMEOUT_SECONDS,
    apply_daemon_trusted_time_env,
    chat_daemon,
    chat_daemon_result,
    chat_daemon_submit,
    inject_daemon_trusted_time,
    init_runtime_write_v1_daemon,
    refresh_daemon_time,
    run_daemon,
    start_daemon,
    status_daemon,
    stop_daemon,
    trusted_host_time_env_available,
)

def _render_readonly_status(root: Path | None = None) -> str:
    cfg = JaznConfig(root=root or Path(__file__).resolve().parent)
    clock = WarsawClock(cfg.timezone)
    renderer = ResponseRenderer(clock, IdentityPerspectiveGuard())
    body = build_runtime_status(cfg, store=None, readonly=True)
    return renderer.render(body, AffectiveState(), clock.now(network_first=cfg.network_time_first, allow_fallback=cfg.local_time_fallback))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Runtime JaÄąĹźni ÄąÂatki: rozmowa bezpoÄąâ€şrednia, cognitive-frame, diagnostyka i eksport paczek.",
        allow_abbrev=False,
    )
    parser.add_argument("--root", type=Path, default=None, help="Folder gÄąâ€šÄ‚Ĺ‚wny aktywnej paczki JaÄąĹźni.")
    parser.add_argument("--status", "--status-readonly", "--diagnostics-readonly", action="store_true", dest="status_readonly", help="PokaÄąÄ˝ diagnostykĂ„â„˘ bez zapisu do pamiĂ„â„˘ci. --status jest jawnym aliasem, nie skrÄ‚Ĺ‚tem argparse.")
    parser.add_argument("--cognitive-frame", "--chatgpt-frame", "--brain-frame", action="store_true", dest="cognitive_frame", help="ZwrÄ‚Ĺ‚Ă„â€ˇ wewnĂ„â„˘trzny pakiet poznawczy JSON dla ChatGPT, nie gotowĂ„â€¦ odpowiedÄąĹź uÄąÄ˝ytkownikowi.")
    parser.add_argument("--debug-direct", action="store_true", dest="debug_direct", help="PokaÄąÄ˝ technicznĂ„â€¦ Äąâ€şcieÄąÄ˝kĂ„â„˘ bezpoÄąâ€şredniĂ„â€¦ i fallback diagnostyczny zamiast rozmownej odpowiedzi.")
    parser.add_argument("--chat", "--loop", action="store_true", dest="chat_loop", help="Uruchom staÄąâ€šĂ„â€¦ pĂ„â„˘tlĂ„â„˘ rozmowy: jeden JaznEngine dziaÄąâ€ša przez wiele tur aÄąÄ˝ do /exit lub EOF.")
    parser.add_argument("--chat-gpt", action="store_true", dest="chat_gpt", help="Kanoniczny most ChatGPT. Z wiadomoĹ›ciÄ… po -- wypisuje tylko final_visible_text; ze stdin JSONL dziaĹ‚a jako protokĂłĹ‚ maszynowy. Nie uĹĽywa OPENAI_API_KEY.")
    parser.add_argument("--chat-gpt-final-only", action="store_true", dest="chat_gpt_final_only", help=argparse.SUPPRESS)
    parser.add_argument("--final-only", action="store_true", dest="final_only", help=argparse.SUPPRESS)
    parser.add_argument("--chat-open-ai", "--openai-api", action="store_true", dest="chat_open_ai", help="Uruchom lokalny runtime JaÄąĹźni z model_adapter przez OpenAI Responses API; wymaga OPENAI_API_KEY i nie udaje poÄąâ€šĂ„â€¦czenia bez klucza.")
    parser.add_argument("--openai-model", default=None, help="Model dla --chat-open-ai; domyÄąâ€şlnie JAZN_MODEL_NAME albo konfiguracja runtime.")
    parser.add_argument("--openai-api-base", default=None, help="Bazowy URL API dla --chat-open-ai; domyÄąâ€şlnie https://api.openai.com/v1.")
    parser.add_argument("--openai-timeout", type=float, default=None, help="Timeout sekund dla adaptera OpenAI w --chat-open-ai.")
    parser.add_argument("--openai-max-output-tokens", type=int, default=None, help="Limit output tokens dla adaptera OpenAI w --chat-open-ai.")
    parser.add_argument("--chat-openai", action="store_true", dest="chat_open_ai", help=argparse.SUPPRESS)
    parser.add_argument("--chat-lm-studio", action="store_true", dest="chat_lm_studio", help="Uruchom lokalny runtime JaĹşni z modelem LM Studio przez OpenAI-compatible API; bez OPENAI_API_KEY.")
    parser.add_argument("--lm-studio-api-base", default=None, help="Bazowy URL lokalnego LM Studio API dla --chat-lm-studio; domyslnie http://127.0.0.1:1234/v1.")
    parser.add_argument("--lm-studio-model", default=None, help="Model LM Studio dla --chat-lm-studio.")
    parser.add_argument("--lm-studio-timeout", type=float, default=None, help="Timeout sekund dla adaptera LM Studio.")
    parser.add_argument("--lm-studio-max-output-tokens", type=int, default=None, help="Limit output tokens dla adaptera LM Studio.")
    parser.add_argument("--local-llm", action="store_true", dest="local_llm", help="Uruchom runtime z lokalnym lub zewnÄ™trznym backendem OpenAI-compatible jako generatorem kandydata.")
    parser.add_argument("--local-llm-api-base", default=None, help="Bazowy URL OpenAI-compatible dla --local-llm.")
    parser.add_argument("--local-llm-model", default=None, help="Nazwa modelu dla --local-llm.")
    parser.add_argument("--local-llm-provider", default=None, choices=("openai_compatible", "ollama", "llama_cpp"), help="WskazĂłwka provider-specific dla kolejnoĹ›ci endpointĂłw.")
    parser.add_argument("--bridge-discovery", action="store_true", dest="bridge_discovery", help="PokaÄąÄ˝ wykryte mosty runtime: --chat, --chat-gpt, --chat-open-ai i daemon.")
    parser.add_argument("--daemon-run", action="store_true", dest="daemon_run", help="Uruchom foreground daemon staÄąâ€šej aktywnej JaÄąĹźni: lokalny HTTP loopback + PID + heartbeat + marker JAZN_ACTIVE_RUNTIME.json.")
    parser.add_argument("--daemon-start", action="store_true", dest="daemon_start", help="Uruchom daemon JaÄąĹźni w tle i zwrÄ‚Ĺ‚Ă„â€ˇ status startu.")
    parser.add_argument("--daemon-status", action="store_true", dest="daemon_status", help="SprawdÄąĹź marker, PID, heartbeat i endpoint /status daemonu JaÄąĹźni.")
    parser.add_argument("--daemon-stop", action="store_true", dest="daemon_stop", help="PoproÄąâ€ş dziaÄąâ€šajĂ„â€¦cy lokalny daemon JaÄąĹźni o zatrzymanie i zamkniĂ„â„˘cie sesji.")
    parser.add_argument("--daemon-host", default=DEFAULT_DAEMON_HOST, help="Adres bindowania daemonu; domyÄąâ€şlnie tylko loopback 127.0.0.1.")
    parser.add_argument("--daemon-port", type=int, default=DEFAULT_DAEMON_PORT, help="Port lokalnego daemonu JaÄąĹźni.")
    parser.add_argument("--daemon-heartbeat-interval", type=float, default=DEFAULT_HEARTBEAT_INTERVAL_SECONDS, help="Co ile sekund daemon odÄąâ€şwieÄąÄ˝a marker aktywnego runtime.")
    parser.add_argument("--daemon-start-timeout", type=float, default=DEFAULT_START_TIMEOUT_SECONDS, help="Ile sekund --daemon-start czeka na odpowiedÄąĹź /status.")
    parser.add_argument("--daemon-marker-output", type=Path, default=None, help="Opcjonalna Äąâ€şcieÄąÄ˝ka markera JAZN_ACTIVE_RUNTIME.json dla daemonu.")
    parser.add_argument("--daemon-refresh-time", action="store_true", dest="daemon_refresh_time", help="PoproĹ› daemon o odĹ›wieĹĽenie trusted/degraded timestamp cache i zwrĂłÄ‡ status.")
    parser.add_argument("--runtime-write-status", action="store_true", dest="runtime_write_status", help="PokaĹĽ kontrakt dostÄ™pu do memory/sqlite/runtime_write_v1 bez zapisu.")
    parser.add_argument("--runtime-write-init", action="store_true", dest="runtime_write_init", help="UtwĂłrz czysty memory/sqlite/runtime_write_v1 i shard manifesty, jeĹ›li ich brakuje.")
    parser.add_argument("--daemon-send", action="store_true", dest="daemon_send", help="WyĹ›lij jednÄ… wiadomoĹ›Ä‡ przez dziaĹ‚ajÄ…cy daemon HTTP; jeĹ›li daemon nie dziaĹ‚a, sprĂłbuj go uruchomiÄ‡.")
    parser.add_argument("--daemon-submit", action="store_true", dest="daemon_submit", help="Dodaj turÄ™ do kolejki daemonu i natychmiast zwrĂłÄ‡ request_id bez czekania na wynik.")
    parser.add_argument("--daemon-result", default=None, metavar="REQUEST_ID", help="Pobierz stan lub gotowy wynik wczeĹ›niej zleconej tury daemonu.")
    parser.add_argument("--daemon-request-id", default=None, help="Jawny idempotentny request_id dla --daemon-send/--daemon-submit/--chat-gpt.")
    parser.add_argument("--daemon-final-only", action="store_true", dest="daemon_final_only", help="Z --daemon-send wypisz tylko final_visible_text, gdy runtime zwrĂłci finalnÄ… odpowiedĹş.")
    parser.add_argument("--daemon-chat-timeout", type=float, default=DEFAULT_DAEMON_CHAT_TIMEOUT_SECONDS, help="Timeout sekund dla jednej tury POST /chat przez daemon.")
    parser.add_argument("--daemon-wait-budget", type=float, default=DEFAULT_DAEMON_CHAT_CLI_WAIT_BUDGET_SECONDS, help="Maksymalny czas jednego procesu CLI na oczekiwanie na wynik asynchronicznej tury; po nim zwracany jest request_id do pĂłĹşniejszego odczytu.")
    parser.add_argument("--daemon-poll-interval", type=float, default=DEFAULT_DAEMON_CHAT_POLL_INTERVAL_SECONDS, help="OdstÄ™p sekund miÄ™dzy odczytami /chat-result podczas oczekiwania CLI.")
    parser.add_argument("--ensure-daemon", action="store_true", dest="ensure_daemon", help="Przed trasą rozmowy zapewnij żywy daemon: status -> start -> /ready -> heartbeat.")
    parser.add_argument("--no-ensure-daemon", action="store_true", dest="no_ensure_daemon", help="Wyłącz autostart daemonu tylko dla tej komendy.")
    parser.add_argument("--daemon-autostart-policy", action="store_true", dest="daemon_autostart_policy", help="Pokaż politykę JAZN_DAEMON_AUTOSTART bez uruchamiania daemonu.")
    parser.add_argument("--trusted-time-iso", default=None, help="Zaufany timestamp ISO wstrzykniÄ™ty przez host/loader ChatGPT; aktywuje trusted time bez sieci w sandboxie.")
    parser.add_argument("--trusted-time-source", default=None, help="Opis ĹşrĂłdĹ‚a dla --trusted-time-iso / JAZN_TRUSTED_TIME_ISO.")
    parser.add_argument("--trusted-time-max-age-seconds", type=int, default=None, help="Maksymalny wiek wstrzykniÄ™tego trusted timestampu; domyĹ›lnie polityka czasu runtime.")
    parser.add_argument("--runtime-preflight", action="store_true", dest="runtime_preflight", help="Sprawdź folder, manifest i marker przed użyciem runtime; bez automatycznej naprawy.")
    parser.add_argument("--recover-chatgpt-runtime", action="store_true", dest="recover_chatgpt_runtime", help="Odtwórz runtime po resecie ChatGPT z części ZIP, zweryfikuj i aktywuj atomowo.")
    parser.add_argument("--auto-recover-runtime", action="store_true", dest="auto_recover_runtime", help="Dla trasy rozmowy automatycznie uruchom recovery, gdy preflight wykryje brak pełnego runtime.")
    parser.add_argument("--no-runtime-preflight", action="store_true", dest="no_runtime_preflight", help="Wyłącz preflight tylko dla jawnej diagnostyki/development; niezalecane dla rozmowy.")
    parser.add_argument("--recovery-parts-dir", type=Path, default=DEFAULT_CHATGPT_PARTS_DIR, help="Folder części ZIP i sidecarów recovery.")
    parser.add_argument("--recovery-zip-name", default=None, help="Bazowa nazwa ZIP-a; domyślnie wykrywana z manifestu.")
    parser.add_argument("--recovery-destination", type=Path, default=DEFAULT_CHATGPT_ROOT, help="Docelowy aktywny folder runtime po recovery.")
    parser.add_argument("--recovery-work-dir", type=Path, default=None, help="Folder roboczy recovery z kanonicznymi linkami, ZIP-em i postępem.")
    parser.add_argument("--recovery-budget-seconds", type=float, default=25.0, help="Budżet jednego wywołania ekstrakcji; recovery jest resumowalne między plikami.")
    parser.add_argument("--recovery-unbounded", action="store_true", help="Nie ograniczaj czasu ekstrakcji w lokalnym środowisku.")
    parser.add_argument("--recovery-skip-crc", action="store_true", help="Pomiń pełny test CRC ZIP-a; tylko do diagnostyki.")
    parser.add_argument("--recovery-force-reextract", action="store_true", help="Nie używaj istniejącego poprawnego folderu; rozpocznij czyste staging extraction.")
    parser.add_argument("--recovery-no-daemon", action="store_true", help="Po recovery nie uruchamiaj daemonu.")
    parser.add_argument("--session-id", default=None, help="Jawny identyfikator sesji dla kontrolowanego carryover w --chat/--chat-gpt.")
    parser.add_argument("--no-carryover", action="store_true", dest="no_carryover", help="Zablokuj uÄąÄ˝ycie poprzedniej tury nawet jeÄąâ€şli istnieje runtime_state.json.")
    parser.add_argument("--github-plan", action="store_true", dest="github_plan", help="Zapisz i pokaÄąÄ˝ plan repozytoriÄ‚Ĺ‚w Latka.Jazn oraz Latka.Jazn.Memory bez wykonywania pushu.")
    parser.add_argument("--dedup-report", action="store_true", dest="dedup_report", help="Zbuduj raport duplikatÄ‚Ĺ‚w treÄąâ€şci i SHA-256 bez usuwania plikÄ‚Ĺ‚w.")
    parser.add_argument("--lexical-frame", action="store_true", dest="lexical_frame", help="PokaÄąÄ˝ raport leksykalny aktualnej JaÄąĹźni: polskie rozumienie + rozszerzona semantyka sÄąâ€šÄ‚Ĺ‚w i fraz.")
    parser.add_argument("--nlp-frame", action="store_true", dest="nlp_frame", help="PokaÄąÄ˝ raport NLP aktualnej JaÄąĹźni: tokeny, lemma_candidates, selected_lemma, confidence i provider.")
    parser.add_argument("--runtime-preview", action="store_true", dest="runtime_preview", help="PokaĹĽ krĂłtki, czytelny podglÄ…d jednej tury runtime: final_visible_text + kluczowe pola diagnostyczne. Nie wypisuje peĹ‚nej koperty cognitive-frame do terminala.")
    parser.add_argument("--dev-preview", action="store_true", dest="dev_preview", help="Tryb deweloperski: pokaĹĽ peĹ‚ny payload runtime-preview/cognitive-frame na stdout albo zapisz go przez --runtime-preview-output.")
    parser.add_argument("--runtime-preview-output", type=Path, default=None, help="Opcjonalna Ĺ›cieĹĽka pliku JSON dla --runtime-preview/--dev-preview; peĹ‚ny payload trafia do pliku, a stdout zwraca tylko krĂłtki, czytelny wynik.")
    parser.add_argument("--active-cache-status", action="store_true", dest="active_cache_status", help="PokaÄąÄ˝ status aktywnego rozpakowanego folderu i decyzjĂ„â„˘, czy trzeba ponownie rozpakowaĂ„â€ˇ ZIP.")
    parser.add_argument("--project-startup-index", action="store_true", dest="project_startup_index", help="Zbuduj i pokaÄąÄ˝ mapĂ„â„˘ plikÄ‚Ĺ‚w oraz moduÄąâ€šÄ‚Ĺ‚w/funkcji JaÄąĹźni przy rozruchu.")
    parser.add_argument("--topic-guard", action="store_true", dest="topic_guard", help="PokaÄąÄ˝ raport TopicMismatchGuard dla wiadomoÄąâ€şci bez generowania peÄąâ€šnej odpowiedzi.")
    parser.add_argument("--dialogue-intent", action="store_true", dest="dialogue_intent", help="PokaÄąÄ˝ klasyfikacjĂ„â„˘ aktu rozmowy aktywnego runtime bez generowania odpowiedzi.")
    parser.add_argument("--module-responsibility-map", action="store_true", dest="module_responsibility_map", help="Zbuduj semantycznĂ„â€¦ mapĂ„â„˘ odpowiedzialnoÄąâ€şci moduÄąâ€šÄ‚Ĺ‚w i funkcji.")
    parser.add_argument("--seed-requirements-ledger", action="store_true", dest="seed_requirements_ledger", help="Dopisz wymagania aktywnego manifestu do requirements ledger.")
    parser.add_argument("--last-turn", action="store_true", dest="last_turn", help="PokaÄąÄ˝ ostatni turn checkpoint: exact_runtime_text, visible_text, route, template_origin i source-origin.")
    parser.add_argument("--compare-runtime-visible", action="store_true", dest="compare_runtime_visible", help="PorÄ‚Ĺ‚wnaj exact runtime text z widocznĂ„â€¦ odpowiedziĂ„â€¦ ChatGPT dla ostatniej tury albo --trace-id.")
    parser.add_argument("--dictionary-lookup", action="store_true", dest="dictionary_lookup", help="SprawdÄąĹź termin przez cache/mini-leksykon/adaptory sÄąâ€šownikÄ‚Ĺ‚w; nie udawaj lookupu online bez providera.")
    parser.add_argument("--language-resources", action="store_true", dest="language_resources", help="PokaÄąÄ˝ rejestr dostĂ„â„˘pnych i opcjonalnych zasobÄ‚Ĺ‚w jĂ„â„˘zykowych/sÄąâ€šownikowych.")
    parser.add_argument("--polish-reasoning-frame", action="store_true", dest="polish_reasoning_frame", help="PokaÄąÄ˝ warstwowy frame Polish Reasoning: normalizacja, morfologia, semantyka, reply policy i status providerÄ‚Ĺ‚w.")
    parser.add_argument("--polish-reasoning-sources", action="store_true", dest="polish_reasoning_sources", help="PokaÄąÄ˝ rejestr ÄąĹźrÄ‚Ĺ‚deÄąâ€š/licencji/cache dla warstwy Polish Reasoning.")
    parser.add_argument("--polish-reasoning-bootstrap-plan", action="store_true", dest="polish_reasoning_bootstrap_plan", help="PokaÄąÄ˝ komendy lokalnej instalacji providerÄ‚Ĺ‚w NLP bez ich automatycznego pobierania.")
    parser.add_argument("--nlp-resource-status", action="store_true", dest="nlp_resource_status", help="PokaÄąÄ˝ status lexical resource registry/cache: ÄąĹźrÄ‚Ĺ‚dÄąâ€ša, licencje, dostĂ„â„˘pnoÄąâ€şĂ„â€ˇ i projektowy leksykon bez pobierania duÄąÄ˝ych danych.")
    parser.add_argument("--polish-morphology", action="store_true", dest="polish_morphology", help="PokaÄąÄ˝ szczegÄ‚Ĺ‚Äąâ€šowĂ„â€¦ analizĂ„â„˘ morfologicznĂ„â€¦ v14.8.4: Morfeusz/PoliMorf, kandydaci i selected_lemma.")
    parser.add_argument("--morfeusz-status", action="store_true", dest="morfeusz_status", help="PokaÄąÄ˝ status realnego providera Morfeusz2/SGJP w Polish Reasoning.")
    parser.add_argument("--polimorf-status", action="store_true", dest="polimorf_status", help="PokaÄąÄ˝ status opcjonalnego lokalnego providera PoliMorf.")
    parser.add_argument("--wsjp-lookup-plan", action="store_true", dest="wsjp_lookup_plan", help="Zbuduj bezpieczny plan lookupu WSJP dla terminu; nie scrapuje masowo strony.")
    parser.add_argument("--nkjp-lookup-plan", action="store_true", dest="nkjp_lookup_plan", help="Zbuduj bezpieczny plan lookupu NKJP/concordance dla terminu; nie pobiera peÄąâ€šnego korpusu.")
    parser.add_argument("--voice-source-contract", action="store_true", dest="voice_source_contract", help="PokaÄąÄ˝ kontrakt: JaÄąĹźÄąâ€ž jako ÄąĹźrÄ‚Ĺ‚dÄąâ€šo, ChatGPT/model jako kanaÄąâ€š gÄąâ€šosu.")
    parser.add_argument("--rendering-mode", action="store_true", dest="rendering_mode", help="PokaÄąÄ˝ decyzjĂ„â„˘ naturalna odpowiedÄąĹź vs exact runtime/diagnostyka.")
    parser.add_argument("--raw-chat-status", action="store_true", dest="raw_chat_status", help="PokaÄąÄ˝ status memory/raw/chat.html i chat.html.7z bez rozpakowywania.")
    parser.add_argument("--raw-chat-status-json", action="store_true", dest="raw_chat_status_json", help="PokaÄąÄ˝ uczciwy status raw memory/indexu jako JSON aktywnego runtime.")
    parser.add_argument("--conversation-archive-status", action="store_true", dest="conversation_archive_status", help="PokaÄąÄ˝ status conversation_archive/FTS/staging zbudowanych z raw_chats/*.html.")
    parser.add_argument("--conversation-archive-search", action="store_true", dest="conversation_archive_search", help="Szukaj w osobnym conversation_fts i zwrÄ‚Ĺ‚Ă„â€ˇ UID/provenance do archive/staging.")
    parser.add_argument("--conversation-archive-limit", type=int, default=8, help="Limit trafieÄąâ€ž dla --conversation-archive-search.")
    parser.add_argument("--conversation-archive-show-snippets", action="store_true", dest="conversation_archive_show_snippets", help="DoÄąâ€šĂ„â€¦cz krÄ‚Ĺ‚tkie excerpt z prywatnego archive do wynikÄ‚Ĺ‚w wyszukiwania.")
    parser.add_argument("--status-json", action="store_true", dest="status_json", help="PokaÄąÄ˝ startup/runtime status jako JSON bez parsowania prozy.")
    parser.add_argument("--model-adapter-status", action="store_true", dest="model_adapter_status", help="PokaÄąÄ˝ status adapterÄ‚Ĺ‚w modeli: skonfigurowane/nieudawane.")
    parser.add_argument("--model-guided-speech-status", action="store_true", dest="model_guided_speech_status", help="Pokaż status adaptera mowy model-guided: trasa LLM, adapter, host bridge, blokada kosztów i zdolność generacji.")
    parser.add_argument("--llm-route-status", action="store_true", dest="llm_route_status", help="Pokaż decyzję routingu LLM: local -> ChatGPT bridge -> płatne OpenAI API -> null fallback.")
    parser.add_argument("--startup-status", action="store_true", dest="startup_status", help="PokaÄąÄ˝ wÄąâ€šasny kontrakt startowy runtime: lekki loader ChatGPT + obowiĂ„â€¦zki przejĂ„â„˘te przez JaÄąĹźÄąâ€ž.")
    parser.add_argument("--startup-status-fast", action="store_true", dest="startup_status_fast", help="PokaÄąÄ˝ szybki startup status bez deep SQLite i bez sieci.")
    parser.add_argument("--startup-status-deep", action="store_true", dest="startup_status_deep", help="PokaÄąÄ˝ peÄąâ€šny deep startup audit; moÄąÄ˝e trwaĂ„â€ˇ dÄąâ€šugo.")
    parser.add_argument("--turn-trace", action="store_true", dest="turn_trace", help="PokaÄąÄ˝ lekki Äąâ€şlad trasy tury: classifier -> guard -> route -> handler -> validator.")
    parser.add_argument("--network-time-check", action="store_true", dest="network_time_check", help="Jawna diagnostyka czasu sieciowego; zwykÄąâ€ša rozmowa wymaga trusted network time albo blokuje normalnĂ„â€¦ odpowiedÄąĹź.")
    parser.add_argument("--sqlite-integrity-audit", action="store_true", dest="sqlite_integrity_audit", help="Jawny deep audit SQLite z integrity_check/foreign_key_check.")
    parser.add_argument("--self-check", action="store_true", dest="self_check", help="PokaÄąÄ˝ skrÄ‚Ĺ‚cony self-check runtime i potwierdzenie, ÄąÄ˝e procedura startowa jest wÄąâ€šasnoÄąâ€şciĂ„â€¦ systemu JaÄąĹźni.")
    parser.add_argument("--self-knowledge-status", action="store_true", dest="self_knowledge_status", help="PokaĹĽ operacyjny kontrakt: kim jest Ĺatka, co moĹĽe pamiÄ™taÄ‡, czego siÄ™ uczy, co umie i jak mĂłwi o emocjach bez zmyĹ›lania.")
    parser.add_argument("--self-knowledge-deep", action="store_true", dest="self_knowledge_deep", help="Z --self-knowledge-status wykonaj gĹ‚Ä™bszÄ… diagnostykÄ™ SQLite warstw pamiÄ™ci.")
    parser.add_argument("--truth-boundary-check", action="store_true", dest="truth_boundary_check", help="PokaÄąÄ˝ granicĂ„â„˘ prawdy runtime/ChatGPT/pliki/pamiĂ„â„˘Ă„â€ˇ/ZIP.")
    parser.add_argument("--fallback-audit", action="store_true", dest="fallback_audit", help="Zbadaj tekst jako moÄąÄ˝liwy fallback, stale route albo kontrakt zamiast odpowiedzi.")
    parser.add_argument("--memory-plan", action="store_true", dest="memory_plan", help="PokaÄąÄ˝ plan wyszukiwania pamiĂ„â„˘ci i trafienia plikÄ‚Ĺ‚w kanonicznych bez generowania zwykÄąâ€šej odpowiedzi.")
    parser.add_argument("--canon-extraction-preview", action="store_true", dest="canon_extraction_preview", help="Przeskanuj prywatne ÄąĹźrÄ‚Ĺ‚dÄąâ€ša kanonu i zapisz raport/progress bez modyfikowania kanonu runtime.")
    parser.add_argument("--canon-extraction-write-private", action="store_true", dest="canon_extraction_write_private", help="Przeskanuj ÄąĹźrÄ‚Ĺ‚dÄąâ€ša i zapisz lokalny prywatny moduÄąâ€š .py canon extension; nie commitowaĂ„â€ˇ bez recenzji.")
    parser.add_argument("--canon-extraction-progress", type=Path, default=None, help="Opcjonalna Äąâ€şcieÄąÄ˝ka JSONL postĂ„â„˘pu dla ekstrakcji kanonu.")
    parser.add_argument("--canon-extraction-verbose-progress", action="store_true", dest="canon_extraction_verbose_progress", help="Wypisuj zdarzenia progress JSONL na stdout oprÄ‚Ĺ‚cz zapisu do pliku.")
    parser.add_argument("--canon-extra-source", action="append", default=[], help="Dodatkowe ÄąĹźrÄ‚Ĺ‚dÄąâ€šo kanonu wzglĂ„â„˘dne wobec root; moÄąÄ˝na powtÄ‚Ĺ‚rzyĂ„â€ˇ.")
    parser.add_argument("--memory-normalization-status", action="store_true", dest="memory_normalization_status", help="PokaÄąÄ˝ status niedestrukcyjnego sidecara normalizacji pamiĂ„â„˘ci.")
    parser.add_argument("--normalize-memory-sidecar", action="store_true", dest="normalize_memory_sidecar", help="Zbuduj lub zaktualizuj sidecar normalizacji pamiĂ„â„˘ci bez modyfikowania aktywnej bazy rozmÄ‚Ĺ‚w.")
    parser.add_argument("--wake-state-status", action="store_true", dest="wake_state_status", help="PokaÄąÄ˝ status aktywnego wake_state z sidecara pamiĂ„â„˘ci.")
    parser.add_argument("--build-wake-state", action="store_true", dest="build_wake_state", help="Zbuduj wake_state z istniejĂ„â€¦cych rekordÄ‚Ĺ‚w sidecara normalizacji.")
    parser.add_argument("--dedupe-memory-sidecar", action="store_true", dest="dedupe_memory_sidecar", help="Zbuduj warstwowe grupy duplikatÄ‚Ĺ‚w w sidecarze bez kasowania rekordÄ‚Ĺ‚w ÄąĹźrÄ‚Ĺ‚dÄąâ€šowych.")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Tryb kontrolny dla operacji normalizacji/wake_state bez zapisu.")
    parser.add_argument("--normalization-limit", type=int, default=None, help="Opcjonalny limit rekordÄ‚Ĺ‚w dla sidecara normalizacji, uÄąÄ˝ywany gÄąâ€šÄ‚Ĺ‚wnie w testach i audytach.")
    parser.add_argument("--dedupe-min-group-size", type=int, default=2, help="Minimalny rozmiar grupy dla warstwowej deduplikacji sidecara.")
    parser.add_argument("--write-active-runtime-marker", action="store_true", dest="write_active_runtime_marker", help="Zapisz JAZN_ACTIVE_RUNTIME.json dla aktywnego folderu i cache rozpakowania.")
    parser.add_argument("--source-zip", type=Path, default=None, help="Opcjonalna Äąâ€şcieÄąÄ˝ka ZIP-a ÄąĹźrÄ‚Ĺ‚dÄąâ€šowego do porÄ‚Ĺ‚wnania checksum w aktywnym cache.")
    parser.add_argument("--marker-output", type=Path, default=None, help="Opcjonalna Äąâ€şcieÄąÄ˝ka pliku JAZN_ACTIVE_RUNTIME.json.")
    parser.add_argument("--record-final-reply", action="store_true", dest="record_final_reply", help="Dopisz do ledgera finalnĂ„â€¦ widocznĂ„â€¦ odpowiedÄąĹź ChatGPT dla podanego turn_id/trace_id/timestamp_header.")
    parser.add_argument("--turn-id", default=None, help="turn_id z cognitive_turn_envelope dla --record-final-reply.")
    parser.add_argument("--trace-id", default=None, help="trace_id z cognitive_turn_envelope dla --record-final-reply.")
    parser.add_argument("--timestamp-header", default=None, help="timestamp_header z cognitive_turn_envelope dla --record-final-reply.")
    parser.add_argument("--state-emoticon", default="Ä‘ĹşĹšĹĽ", help="Emotikon stanu uÄąÄ˝ywany, jeÄąâ€şli finalny tekst wymaga dopiĂ„â„˘cia timestampu.")
    parser.add_argument("--final-text-file", type=Path, default=None, help="Opcjonalny plik z finalnĂ„â€¦ widocznĂ„â€¦ odpowiedziĂ„â€¦ do zapisania w ledgerze.")
    export_group = parser.add_mutually_exclusive_group()
    export_group.add_argument("--export-system", action="store_true", help="UtwÄ‚Ĺ‚rz paczkĂ„â„˘ system-only bez memory/ i workspace_runtime/.")
    export_group.add_argument("--export-memory", action="store_true", help="UtwÄ‚Ĺ‚rz paczkĂ„â„˘ memory-only z memory/ i workspace_runtime/.")
    export_group.add_argument("--export-full", action="store_true", help="UtwÄ‚Ĺ‚rz peÄąâ€šnĂ„â€¦ paczkĂ„â„˘ systemu wraz z pamiĂ„â„˘ciĂ„â€¦.")
    export_group.add_argument("--export-nlp", action="store_true", help="UtwÄ‚Ĺ‚rz paczkĂ„â„˘ NLP-resources-only bez pamiĂ„â„˘ci i bez ciĂ„â„˘ÄąÄ˝kich modeli.")
    export_group.add_argument("--export-github-source-safe", action="store_true", help="UtwÄ‚Ĺ‚rz paczkĂ„â„˘ ÄąĹźrÄ‚Ĺ‚dÄąâ€šowĂ„â€¦ bez surowej pamiĂ„â„˘ci i aktywnych baz SQLite.")
    parser.add_argument("--export-preview", action="store_true", help="Pokaż plan prywatnego eksportu bez tworzenia ZIP.")
    parser.add_argument("--confirm-private-data", default=None, help="Jednorazowy token potwierdzenia dla eksportu memory/full.")
    parser.add_argument("--output", type=Path, default=None, help="Opcjonalna Äąâ€şcieÄąÄ˝ka ZIP dla eksportu.")
    parser.add_argument("message", nargs=argparse.REMAINDER, help="TreÄąâ€şĂ„â€ˇ wiadomoÄąâ€şci dla runtime.")
    return parser


def _message_from_remainder(parts: list[str]) -> str:
    if parts and parts[0] == "--":
        parts = parts[1:]
    return " ".join(parts).strip()


def _env_flag_enabled(name: str, *, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "nie", "off"}


def _optional_positive_env_int(name: str) -> int | None:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _chatgpt_daemon_marker_path(cfg: JaznConfig) -> Path:
    return Path(cfg.root).resolve() / "workspace_runtime" / "JAZN_ACTIVE_RUNTIME.json"


def _daemon_status_allows_chatgpt_fast_path(status: dict[str, object]) -> bool:
    active_state = str(status.get("active_state") or "")
    return active_state == "active_trusted" and status.get("endpoint_reachable") is True


def _sync_supplied_trusted_time_to_daemon(
    *,
    cfg: JaznConfig,
    host: str,
    port: int,
    trusted_time_iso: str | None,
    trusted_time_source: str | None,
    trusted_time_max_age_seconds: int | None,
    required: bool = False,
) -> dict[str, object] | None:
    """Synchronize an explicitly host-supplied timestamp into a live daemon.

    ``apply_daemon_trusted_time_env`` updates only the current CLI process.  A
    daemon that was started earlier is a separate process and therefore cannot
    observe that environment change.  This helper closes that process boundary
    through the existing loopback-only ``/trusted-time`` endpoint.

    The helper never derives trusted time from the local machine clock.  When no
    explicit host timestamp is present it returns ``None`` and leaves the daemon
    time state unchanged.
    """
    normalized_iso = str(trusted_time_iso or "").strip()
    if not normalized_iso:
        return None

    # Environment aliases can outlive an earlier in-process CLI call in test
    # harnesses or embedded hosts.  Synchronize only a timestamp that the
    # canonical clock currently accepts as fresh trusted host time.
    validation_sample = WarsawClock(cfg.timezone).now(network_first=False)
    if validation_sample.trusted is not True:
        if required:
            return {
                "ok": False,
                "error_code": "trusted_host_time_invalid_or_stale",
                "trusted_time_iso": normalized_iso,
                "source": trusted_time_source,
            }
        return None

    return inject_daemon_trusted_time(
        cfg,
        trusted_time_iso=normalized_iso,
        source=str(trusted_time_source or "chatgpt_loader_time").strip() or "chatgpt_loader_time",
        max_age_seconds=trusted_time_max_age_seconds,
        host=host,
        port=port,
    )


def _try_chat_gpt_one_shot_via_daemon(
    *,
    cfg: JaznConfig,
    text: str,
    session_id: str | None,
    no_carryover: bool,
    host: str,
    port: int,
    timeout: float,
    output_mode: str,
    request_id: str | None = None,
    wait_budget: float | None = None,
    poll_interval: float = DEFAULT_DAEMON_CHAT_POLL_INTERVAL_SECONDS,
    trusted_time_iso: str | None = None,
    trusted_time_source: str | None = None,
    trusted_time_max_age_seconds: int | None = None,
    trusted_time_required: bool = False,
) -> int | None:
    """Prefer a live daemon for `--chat-gpt -- <text>` without exposing a second public flag.

    The daemon already owns the initialized runtime session.  When its marker is
    absent or the endpoint cannot be verified, the caller falls back to the local
    JSONL bridge, preserving compatibility.
    """
    if not text.strip() or not _env_flag_enabled("JAZN_CHATGPT_PREFER_DAEMON", default=True):
        return None
    marker_path = _chatgpt_daemon_marker_path(cfg)
    if not marker_path.exists():
        return None
    try:
        status = status_daemon(cfg, host=host, port=port)
    except Exception:
        return None
    if not isinstance(status, dict) or not _daemon_status_allows_chatgpt_fast_path(status):
        return None
    trusted_time_sync = _sync_supplied_trusted_time_to_daemon(
        cfg=cfg,
        host=host,
        port=port,
        trusted_time_iso=trusted_time_iso,
        trusted_time_source=trusted_time_source,
        trusted_time_max_age_seconds=trusted_time_max_age_seconds,
        required=trusted_time_required,
    )
    if trusted_time_sync is not None and trusted_time_sync.get("ok") is not True:
        # The current CLI process already has the explicit host timestamp.  If
        # the long-lived daemon rejects it, fall back to the local bridge so a
        # degraded daemon cannot overwrite a trusted per-turn timestamp.
        return None
    daemon_session_id = session_id or os.environ.get("JAZN_CHATGPT_DAEMON_SESSION_ID", "chatgpt-bridge-default").strip() or "chatgpt-bridge-default"
    classification_text, input_warning = guard_cli_flags_in_user_text(text)
    if not classification_text:
        classification_text = text
    try:
        result = chat_daemon(
            cfg,
            classification_text,
            host=host,
            port=port,
            session_id=daemon_session_id,
            no_carryover=no_carryover,
            client="chatgpt_bridge_one_shot_daemon_fast_path",
            request_id=request_id,
            timeout=min(float(timeout), float(wait_budget)) if wait_budget is not None else float(timeout),
            poll_interval=poll_interval,
        )
    except Exception as exc:
        if _env_flag_enabled("JAZN_CHATGPT_DAEMON_FALLBACK_DEBUG", default=False):
            print(f"[daemon_chat_failed_fallback] {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    attach_cli_flag_warning(result, input_warning)
    result.setdefault("chat_bridge", {})
    if isinstance(result["chat_bridge"], dict):
        result["chat_bridge"].update({
            "command": "--chat-gpt",
            "canonical_command": "--chat-gpt",
            "daemon_fast_path": True,
            "daemon_marker_path": str(marker_path),
            "daemon_status_active_state": status.get("active_state"),
            "daemon_session_id": daemon_session_id,
            "fallback_if_unavailable": "local_jsonl_runtime_session",
            "trusted_time_sync": trusted_time_sync,
            "truth_boundary": "--chat-gpt uĹĽywa daemon fast path tylko gdy marker i lokalny endpoint potwierdzajÄ… ĹĽywy runtime; jawnie dostarczony czas hosta jest najpierw synchronizowany do procesu daemonu, a przy odrzuceniu most wraca do lokalnego JSONL bridge.",
        })
    result["chatgpt_bridge"] = result.get("chat_bridge")
    if result.get("error_code") == "daemon_chat_pending":
        pending_request_id = str(result.get("request_id") or "")
        result["chatgpt_host_bridge"] = {
            "phase": "runtime_result_pending",
            "host_must_poll_runtime": True,
            "host_must_generate_visible_reply": False,
            "daemon_request_id": pending_request_id,
            "poll_command": f"python -X utf8 main.py --daemon-result {pending_request_id}",
            "truth_boundary": "Tura zostaĹ‚a przyjÄ™ta przez ĹĽywy daemon i dziaĹ‚a niezaleĹĽnie od poĹ‚Ä…czenia CLI. Host nie moĹĽe uruchamiaÄ‡ tej samej wiadomoĹ›ci ponownie; powinien pobraÄ‡ wynik po request_id.",
        }
        result["chat_bridge_output"] = {
            "requested_mode": output_mode,
            "effective_mode": "jsonl_runtime_pending_envelope",
            "reason": "runtime_turn_outlived_single_cli_wait_budget",
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    result["chatgpt_host_bridge"] = build_chatgpt_host_bridge_turn_contract(
        result,
        user_text=text,
        chat_bridge_meta=result["chat_bridge"],
    )
    write_chat_bridge_payload(sys.stdout, result, output_mode=output_mode)
    return 0


def _runtime_command_from_cli_args(ns: argparse.Namespace) -> str | None:
    """Return the canonical explicit visible runtime command selected by CLI flags."""
    if getattr(ns, "chat_gpt_final_only", False):
        return "--chat-gpt"
    if getattr(ns, "chat_gpt", False):
        return "--chat-gpt"
    if getattr(ns, "chat_loop", False):
        return "--chat"
    if getattr(ns, "chat_open_ai", False):
        return "--chat-open-ai"
    if getattr(ns, "chat_lm_studio", False):
        return "--chat-lm-studio"
    if getattr(ns, "local_llm", False):
        return "--local-llm"
    return None


def _build_light_turn_trace(cfg: JaznConfig, text: str) -> dict:
    intent = DialogueIntentClassifier().classify(text)
    guard = TopicMismatchGuard().analyse(text, runtime_version=cfg.version).to_dict()
    entry = RouteRegistry().resolve(intent.primary_intent, confidence=intent.confidence)
    return TurnRouteTrace(
        user_text_preview=(text or "")[:240],
        speech_act=intent.speech_act,
        question_object=intent.question_object,
        primary_intent_initial=intent.primary_intent,
        primary_intent_final=intent.primary_intent,
        secondary_intents=list(intent.secondary_intents),
        topic_guard=guard,
        selected_route=entry.route,
        selected_handler=entry.handler_name,
        startup_status_mode="fast",
        sqlite_health_mode="metadata",
        network_time_used=False,
        deep_audit_used=False,
        runtime_answer_validation={
            "status": "not_run_without_response",
            "truth_boundary": "--turn-trace alone does not generate a final answer; combine with --runtime-preview to inspect validator output.",
        },
        final_text_source="not_generated",
    ).to_dict()


def _bridge_text_output_mode(ns: argparse.Namespace, bridge_text: str) -> str:
    """Human one-shot chat commands render final_visible_text; stdin keeps JSONL."""
    return "final_visible_text" if (getattr(ns, "chat_gpt_final_only", False) or getattr(ns, "final_only", False) or bridge_text) else "jsonl"




def _ensure_daemon_for_cli_turn(ns: argparse.Namespace, cfg: JaznConfig, command: str, *, explicit: bool = False):
    return ensure_daemon_for_runtime_turn(
        cfg,
        command=command,
        host=ns.daemon_host,
        port=ns.daemon_port,
        marker_output=ns.daemon_marker_output,
        heartbeat_interval=ns.daemon_heartbeat_interval,
        startup_timeout=ns.daemon_start_timeout,
        explicit_ensure=bool(explicit or getattr(ns, "ensure_daemon", False)),
        disabled_for_turn=bool(getattr(ns, "no_ensure_daemon", False)),
    )


def _ensure_daemon_or_error(ns: argparse.Namespace, cfg: JaznConfig, command: str, *, explicit: bool = False) -> tuple[object, int | None]:
    result = _ensure_daemon_for_cli_turn(ns, cfg, command, explicit=explicit)
    decision = result.decision if isinstance(result.decision, dict) else {}
    if result.ok or not decision.get("should_ensure"):
        return result, None
    print(json.dumps({
        "ok": False,
        "error_code": "daemon_ensure_failed",
        "command": command,
        "daemon_autostart": result.to_dict(),
        "truth_boundary": "Trasa rozmowy wymaga działającego daemonu; ponieważ ensure nie potwierdził active_trusted/active_degraded, runtime nie udaje rozmowy z aktywną Jaźnią.",
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return result, 1
def _run_chat_command_one_shot(
    *,
    cfg: JaznConfig,
    text: str,
    session_id: str | None,
    no_carryover: bool,
    source_client: str,
    lifecycle: str,
    command: str,
    output_mode: str = "final_visible_text",
) -> int:
    """Run the same runtime speech engine for terminal and bridge one-shots.

    All chat entry points must converge on JaznRuntimeSession.process_user_text();
    adapters change only the visible/model channel, not the reasoning pipeline.
    """
    session = RuntimeSessionWorker(
        session_factory=JaznRuntimeSession,
        config=cfg,
        session_id=session_id,
        no_carryover=no_carryover,
        source_client=source_client,
        command=command,
        timeout_seconds=runtime_turn_timeout_seconds(cfg),
    )
    try:
        result = session.process_user_text(
            text,
            client=source_client,
            lifecycle=lifecycle,
            session_id_source="cli_arg" if session_id else "generated",
            process_reused=False,
        )
        result.setdefault("chat_bridge", {})
        if isinstance(result["chat_bridge"], dict):
            result["chat_bridge"].update({
                "command": command,
                "canonical_command": command,
                "one_shot_shared_runtime_pipeline": True,
                "truth_boundary": "Ta komenda czatowa uĹĽywa tego samego JaznRuntimeSession.process_turn co pozostaĹ‚e flagi; adapter zmienia kanaĹ‚ modelu/widocznoĹ›ci, nie neurologiÄ™ runtime.",
            })
        write_chat_bridge_payload(sys.stdout, result, output_mode=output_mode)
        return 0
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    _configure_stdio_utf8()
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--chat-jsonl" in argv:
        sys.stderr.write("Flaga --chat-jsonl zostaÄąâ€ša usuniĂ„â„˘ta z aktywnego CLI. UÄąÄ˝yj: python main.py --chat-gpt --session-id <id>\n")
        return 2
    parser = _build_parser()
    ns = parser.parse_args(argv)
    if ns.runtime_preview_output is None and "--runtime-preview-output" in ns.message:
        idx = ns.message.index("--runtime-preview-output")
        if idx + 1 >= len(ns.message):
            parser.error("--runtime-preview-output requires a path")
        ns.runtime_preview_output = Path(ns.message[idx + 1])
        ns.message = ns.message[:idx] + ns.message[idx + 2:]
    root = ns.root.resolve() if ns.root else None
    supplied_time_env_name = next(
        (name for name in TRUSTED_HOST_TIME_ISO_ENV_NAMES if str(os.environ.get(name, "")).strip()),
        None,
    )
    canonical_anchor_present = bool(
        str(os.environ.get(TRUSTED_HOST_TIME_MONOTONIC_ANCHOR_ENV_NAMES[0], "")).strip()
    )
    trusted_time_required_for_turn = bool(ns.trusted_time_iso) or bool(
        supplied_time_env_name
        and (supplied_time_env_name != TRUSTED_HOST_TIME_ISO_ENV_NAMES[0] or not canonical_anchor_present)
    )
    trusted_time_env = None
    if ns.trusted_time_iso or trusted_host_time_env_available():
        trusted_time_env = apply_daemon_trusted_time_env(
            trusted_time_iso=ns.trusted_time_iso,
            source=ns.trusted_time_source,
            max_age_seconds=ns.trusted_time_max_age_seconds,
        )

    runtime_root = (root or Path(__file__).resolve().parent).resolve()

    if ns.runtime_preflight:
        report = runtime_preflight(runtime_root)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report.ok else 3

    if ns.recover_chatgpt_runtime:
        result = recover_chatgpt_runtime(
            parts_dir=ns.recovery_parts_dir,
            destination=ns.recovery_destination,
            base_zip_name=ns.recovery_zip_name,
            work_dir=ns.recovery_work_dir,
            time_budget_seconds=None if ns.recovery_unbounded else ns.recovery_budget_seconds,
            run_crc=not ns.recovery_skip_crc,
            force_reextract=ns.recovery_force_reextract,
            start_runtime_daemon=not ns.recovery_no_daemon,
            daemon_host=ns.daemon_host,
            daemon_port=ns.daemon_port,
            heartbeat_interval=ns.daemon_heartbeat_interval,
            startup_timeout=ns.daemon_start_timeout,
            trusted_time_iso=ns.trusted_time_iso or os.environ.get("JAZN_TRUSTED_TIME_ISO"),
            trusted_time_source=ns.trusted_time_source or os.environ.get("JAZN_TRUSTED_TIME_SOURCE"),
            trusted_time_max_age_seconds=ns.trusted_time_max_age_seconds,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return int(result.exit_code)

    read_only_status_requested = bool(
        ns.status_readonly
        or ns.daemon_status
        or ns.runtime_write_status
        or ns.active_cache_status
        or ns.raw_chat_status
        or ns.raw_chat_status_json
        or ns.conversation_archive_status
        or ns.status_json
        or ns.model_adapter_status
        or ns.model_guided_speech_status
        or ns.llm_route_status
        or ns.startup_status
        or ns.startup_status_fast
        or ns.startup_status_deep
        or ns.self_knowledge_status
        or ns.memory_normalization_status
        or ns.wake_state_status
    )
    runtime_turn_requested = bool(
        ns.chat_loop
        or ns.chat_gpt
        or ns.chat_gpt_final_only
        or ns.chat_open_ai
        or ns.chat_lm_studio
        or ns.local_llm
        or ns.daemon_send
        or ns.daemon_submit
    ) and not (read_only_status_requested and not ns.message)
    if runtime_turn_requested and not ns.no_runtime_preflight:
        preflight = runtime_preflight(runtime_root)
        if preflight.needs_recovery:
            auto_recover = ns.auto_recover_runtime or str(os.environ.get("JAZN_AUTO_RECOVER_RUNTIME", "")).strip().lower() in {"1", "true", "yes", "tak", "on"}
            if not auto_recover:
                print(json.dumps({
                    "ok": False,
                    "error_code": "runtime_preflight_failed",
                    "preflight": preflight.to_dict(),
                    "recovery_command": "python -X utf8 main.py --recover-chatgpt-runtime",
                    "truth_boundary": "Trasa rozmowy jest zamknięta, dopóki pełny runtime nie zostanie odtworzony i zweryfikowany.",
                }, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
                return 3
            recovery = recover_chatgpt_runtime(
                parts_dir=ns.recovery_parts_dir,
                destination=ns.recovery_destination,
                base_zip_name=ns.recovery_zip_name,
                work_dir=ns.recovery_work_dir,
                time_budget_seconds=None if ns.recovery_unbounded else ns.recovery_budget_seconds,
                run_crc=not ns.recovery_skip_crc,
                force_reextract=ns.recovery_force_reextract,
                start_runtime_daemon=not ns.recovery_no_daemon,
                daemon_host=ns.daemon_host,
                daemon_port=ns.daemon_port,
                heartbeat_interval=ns.daemon_heartbeat_interval,
                startup_timeout=ns.daemon_start_timeout,
                trusted_time_iso=ns.trusted_time_iso or os.environ.get("JAZN_TRUSTED_TIME_ISO"),
                trusted_time_source=ns.trusted_time_source or os.environ.get("JAZN_TRUSTED_TIME_SOURCE"),
                trusted_time_max_age_seconds=ns.trusted_time_max_age_seconds,
            )
            if not recovery.ok:
                print(json.dumps(recovery.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
                return int(recovery.exit_code)
            root = Path(recovery.active_root).resolve()
            runtime_root = root
        elif preflight.needs_marker_refresh:
            write_active_runtime_marker(runtime_root, action="runtime_preflight_marker_refresh")

    if ns.status_readonly:
        print(_render_readonly_status(root))
        return 0

    config = JaznConfig(root=root) if root else None

    if ns.runtime_preview_output and not (ns.runtime_preview or ns.dev_preview):
        parser.error("--runtime-preview-output wymaga --runtime-preview albo --dev-preview")
    if ns.chat_gpt_final_only:
        ns.chat_gpt = True
    if ns.final_only and not ns.chat_gpt:
        parser.error("--final-only jest legacy aliasem i wymaga kanonicznego --chat-gpt")

    if ns.bridge_discovery:
        cfg = config or JaznConfig()
        print(json.dumps(discover_runtime_bridges(cfg), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.daemon_run:
        cfg = config or JaznConfig()
        return run_daemon(
            cfg,
            host=ns.daemon_host,
            port=ns.daemon_port,
            marker_output=ns.daemon_marker_output,
            heartbeat_interval=ns.daemon_heartbeat_interval,
        )

    if ns.daemon_start:
        cfg = config or JaznConfig()
        payload = start_daemon(
            cfg,
            host=ns.daemon_host,
            port=ns.daemon_port,
            marker_output=ns.daemon_marker_output,
            heartbeat_interval=ns.daemon_heartbeat_interval,
            startup_timeout=ns.daemon_start_timeout,
        )
        if trusted_time_env is not None:
            payload["trusted_time_env"] = trusted_time_env
            injected = inject_daemon_trusted_time(
                cfg,
                trusted_time_iso=ns.trusted_time_iso or os.environ.get("JAZN_TRUSTED_TIME_ISO", ""),
                source=ns.trusted_time_source or os.environ.get("JAZN_TRUSTED_TIME_SOURCE", "chatgpt_loader_time"),
                max_age_seconds=ns.trusted_time_max_age_seconds,
                host=ns.daemon_host,
                port=ns.daemon_port,
            )
            payload["trusted_time_injection"] = injected
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.daemon_refresh_time:
        cfg = config or JaznConfig()
        if trusted_time_env is not None:
            payload = inject_daemon_trusted_time(
                cfg,
                trusted_time_iso=ns.trusted_time_iso or os.environ.get("JAZN_TRUSTED_TIME_ISO", ""),
                source=ns.trusted_time_source or os.environ.get("JAZN_TRUSTED_TIME_SOURCE", "chatgpt_loader_time"),
                max_age_seconds=ns.trusted_time_max_age_seconds,
                host=ns.daemon_host,
                port=ns.daemon_port,
            )
            payload["trusted_time_env"] = trusted_time_env
        else:
            payload = refresh_daemon_time(cfg, host=ns.daemon_host, port=ns.daemon_port)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.runtime_write_status:
        cfg = config or JaznConfig()
        print(json.dumps(build_runtime_write_access_status(cfg, initialize=False).to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.runtime_write_init:
        cfg = config or JaznConfig()
        status = status_daemon(cfg, host=ns.daemon_host, port=ns.daemon_port, marker_output=ns.daemon_marker_output)
        if status.get("active_state") in {"active_trusted", "active_degraded"}:
            payload = init_runtime_write_v1_daemon(cfg, host=ns.daemon_host, port=ns.daemon_port)
        else:
            payload = {"ok": True, "runtime_write_access_status": build_runtime_write_access_status(cfg, initialize=True, writes_enabled=True).to_dict(), "daemon_status": status}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.daemon_status:
        cfg = config or JaznConfig()
        print(json.dumps(status_daemon(
            cfg,
            host=ns.daemon_host,
            port=ns.daemon_port,
            marker_output=ns.daemon_marker_output,
        ), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.daemon_stop:
        cfg = config or JaznConfig()
        print(json.dumps(stop_daemon(
            cfg,
            host=ns.daemon_host,
            port=ns.daemon_port,
            marker_output=ns.daemon_marker_output,
        ), ensure_ascii=False, indent=2, sort_keys=True))
        return 0


    if ns.daemon_autostart_policy:
        print(json.dumps(daemon_autostart_policy_status(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0


    if ns.daemon_result:
        cfg = config or JaznConfig()
        payload = chat_daemon_result(
            cfg,
            ns.daemon_result,
            host=ns.daemon_host,
            port=ns.daemon_port,
            timeout=min(DEFAULT_DAEMON_CHAT_CLI_WAIT_BUDGET_SECONDS, max(0.5, ns.daemon_wait_budget)),
        )
        if ns.daemon_final_only and isinstance(payload, dict):
            result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
            final_text = result.get("final_visible_text") or (result.get("runtime") or {}).get("final_visible_text")
            if final_text:
                print(str(final_text))
                return 0
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("done") is False or payload.get("ok") else 1

    if ns.daemon_submit:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        daemon_ensure, daemon_exit = _ensure_daemon_or_error(ns, cfg, "--daemon-submit", explicit=True)
        if daemon_exit is not None:
            return daemon_exit
        payload = chat_daemon_submit(
            cfg,
            text,
            host=ns.daemon_host,
            port=ns.daemon_port,
            session_id=ns.session_id,
            no_carryover=ns.no_carryover,
            request_id=ns.daemon_request_id,
            timeout=min(DEFAULT_DAEMON_CHAT_CLI_WAIT_BUDGET_SECONDS, max(0.5, ns.daemon_wait_budget)),
        )
        payload.setdefault("daemon_autostart", daemon_ensure.to_dict())
        if trusted_time_env is not None:
            payload.setdefault("trusted_time_env", trusted_time_env)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("accepted") else 1

    if ns.daemon_send or ns.daemon_final_only:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        daemon_ensure, daemon_exit = _ensure_daemon_or_error(ns, cfg, "--daemon-send", explicit=True)
        if daemon_exit is not None:
            return daemon_exit
        wait_budget = min(max(0.0, ns.daemon_chat_timeout), max(0.0, ns.daemon_wait_budget))
        payload = chat_daemon(
            cfg,
            text,
            host=ns.daemon_host,
            port=ns.daemon_port,
            session_id=ns.session_id,
            no_carryover=ns.no_carryover,
            request_id=ns.daemon_request_id,
            timeout=wait_budget,
            poll_interval=ns.daemon_poll_interval,
        )
        payload.setdefault("daemon_autostart", daemon_ensure.to_dict())
        if trusted_time_env is not None:
            payload.setdefault("trusted_time_env", trusted_time_env)
        if ns.daemon_final_only and isinstance(payload, dict):
            final_text = payload.get("final_visible_text") or (payload.get("runtime") or {}).get("final_visible_text")
            if final_text:
                print(str(final_text))
                return 0
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("ok") or payload.get("error_code") == "daemon_chat_pending" else 1


    if (ns.startup_status or ns.startup_status_fast or ns.startup_status_deep) and ns.ensure_daemon:
        cfg = config or JaznConfig()
        mode = "deep" if ns.startup_status_deep else "fast"
        runtime_command = _runtime_command_from_cli_args(ns) or "--startup-status"
        daemon_ensure = _ensure_daemon_for_cli_turn(ns, cfg, runtime_command, explicit=True)
        payload = build_startup_status(
            cfg,
            source_zip=ns.source_zip,
            mode=mode,
            runtime_command=runtime_command,
            infer_host_environment=True,
        ).to_dict()
        payload["daemon_autostart"] = daemon_ensure.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if daemon_ensure.ok else 1

    if ns.startup_status or ns.startup_status_fast or ns.startup_status_deep:
        cfg = config or JaznConfig()
        mode = "deep" if ns.startup_status_deep else "fast"
        runtime_command = _runtime_command_from_cli_args(ns)
        if runtime_command == "--chat-open-ai":
            cfg = apply_openai_cli_settings(
                cfg,
                model=ns.openai_model,
                api_base=ns.openai_api_base,
                timeout_seconds=ns.openai_timeout,
                max_output_tokens=ns.openai_max_output_tokens,
            )
        elif runtime_command == "--chat-lm-studio":
            cfg = apply_lm_studio_cli_settings(
                cfg,
                model=ns.lm_studio_model,
                api_base=ns.lm_studio_api_base,
                timeout_seconds=ns.lm_studio_timeout,
                max_output_tokens=ns.lm_studio_max_output_tokens,
            )
        print(json.dumps(build_startup_status(
            cfg,
            source_zip=ns.source_zip,
            mode=mode,
            runtime_command=runtime_command,
            infer_host_environment=True,
        ).to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.status_json:
        cfg = config or JaznConfig()
        archive_store = ConversationArchiveStore(cfg.root)
        print(json.dumps({
            "runtime_version": cfg.version,
            "startup_summary": build_startup_summary(cfg, source_zip=ns.source_zip),
            "startup_status_mode": "fast",
            "sqlite_health_mode": "metadata",
            "raw_memory_status": RawMemoryInspector(cfg.root, cfg.memory_db_path).inspect().to_dict(),
            "conversation_archive_status": archive_store.status(health_mode="metadata").to_dict(),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.network_time_check:
        cfg = config or JaznConfig()
        print(json.dumps({"runtime_version": cfg.version, "network_time_check": WarsawClock(cfg.timezone).network_time_check()}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.sqlite_integrity_audit:
        cfg = config or JaznConfig()
        print(json.dumps({"runtime_version": cfg.version, "sqlite_integrity_audit": ConversationArchiveStore(cfg.root).status(health_mode="deep").to_dict()}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.turn_trace and not ns.runtime_preview:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        print(json.dumps({"runtime_version": cfg.version, "turn_route_trace": _build_light_turn_trace(cfg, text)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.self_check:
        cfg = config or JaznConfig()
        print(json.dumps(build_self_check(cfg), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.self_knowledge_status:
        cfg = config or JaznConfig()
        print(json.dumps({"runtime_version": cfg.version, "self_knowledge_status": build_self_knowledge_packet(cfg, deep=ns.self_knowledge_deep).to_dict()}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.truth_boundary_check:
        cfg = config or JaznConfig()
        print(json.dumps(build_truth_boundary_check(cfg), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.fallback_audit:
        text = _message_from_remainder(ns.message)
        print(json.dumps(classify_fallback_text(text), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.memory_plan:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        planner = MemorySearchPlanner(cfg.root)
        plan = planner.plan(text)
        archive_store = ConversationArchiveStore(cfg.root)
        archive_query = " ".join((plan.search_terms or plan.focus_terms or [])[:8]) or text
        payload = {
            "schema_version": schema_version("memory_plan_cli"),
            "runtime_version": cfg.version,
            "memory_search_plan": plan.to_dict(),
            "source_file_hits": [hit.to_dict() for hit in planner.search_source_files(plan, limit=8)],
            "conversation_archive_status": archive_store.status(check_integrity=False).to_dict(),
            "conversation_archive_hits": archive_store.search(archive_query, limit=8, include_snippets=False).to_dict(),
            "truth_boundary": "To jest plan, kanoniczne trafienia plikÄ‚Ĺ‚w i metadane trafieÄąâ€ž conversation_archive/FTS, nie peÄąâ€šna rozmowna odpowiedÄąĹź ani dowÄ‚Ĺ‚d peÄąâ€šnego odczytu caÄąâ€šej pamiĂ„â„˘ci.",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.canon_extraction_preview or ns.canon_extraction_write_private:
        cfg = config or JaznConfig()
        mode = "write-private-extension" if ns.canon_extraction_write_private else "preview"
        payload = {
            "runtime_version": cfg.version,
            "canon_extraction": run_canon_extraction(
                cfg.root,
                mode=mode,
                progress_path=ns.canon_extraction_progress,
                verbose_progress=ns.canon_extraction_verbose_progress,
                extra_sources=ns.canon_extra_source or [],
            ),
            "truth_boundary": "Raport i progress sĂ„â€¦ artefaktem patcha. WÄąâ€šaÄąâ€şciwy runtime canon jest w plikach .py; lokalny prywatny extension .py wymaga recenzji przed commitem.",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.conversation_archive_status:
        cfg = config or JaznConfig()
        payload = {
            "runtime_version": cfg.version,
            "conversation_archive_status": ConversationArchiveStore(cfg.root).status().to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.conversation_archive_search:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        payload = {
            "runtime_version": cfg.version,
            "conversation_archive_search": ConversationArchiveStore(cfg.root).search(
                text,
                limit=ns.conversation_archive_limit,
                include_snippets=ns.conversation_archive_show_snippets,
            ).to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.memory_normalization_status:
        cfg = config or JaznConfig()
        sidecar = MemoryNormalizationSidecar(
            cfg.root,
            source_db_path=cfg.root / cfg.memory_db_name,
            sidecar_db_path=cfg.root / cfg.audit_db_name,
            runtime_version=cfg.version,
        )
        payload = {"runtime_version": cfg.version, "memory_normalization_status": sidecar.status().to_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.normalize_memory_sidecar:
        cfg = config or JaznConfig()
        sidecar = MemoryNormalizationSidecar(
            cfg.root,
            source_db_path=cfg.root / cfg.memory_db_name,
            sidecar_db_path=cfg.root / cfg.audit_db_name,
            runtime_version=cfg.version,
        )
        payload = {
            "runtime_version": cfg.version,
            "memory_normalization_report": sidecar.normalize(dry_run=ns.dry_run, limit=ns.normalization_limit).to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.wake_state_status:
        cfg = config or JaznConfig()
        sidecar = MemoryNormalizationSidecar(
            cfg.root,
            source_db_path=cfg.root / cfg.memory_db_name,
            sidecar_db_path=cfg.root / cfg.audit_db_name,
            runtime_version=cfg.version,
        )
        payload = {"runtime_version": cfg.version, "wake_state_status": sidecar.wake_state_status().to_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.build_wake_state:
        cfg = config or JaznConfig()
        sidecar = MemoryNormalizationSidecar(
            cfg.root,
            source_db_path=cfg.root / cfg.memory_db_name,
            sidecar_db_path=cfg.root / cfg.audit_db_name,
            runtime_version=cfg.version,
        )
        payload = {"runtime_version": cfg.version, "wake_state_build_report": sidecar.build_wake_state(dry_run=ns.dry_run).to_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.dedupe_memory_sidecar:
        cfg = config or JaznConfig()
        sidecar = MemoryNormalizationSidecar(
            cfg.root,
            source_db_path=cfg.root / cfg.memory_db_name,
            sidecar_db_path=cfg.root / cfg.audit_db_name,
            runtime_version=cfg.version,
        )
        payload = {
            "runtime_version": cfg.version,
            "layered_dedupe_report": sidecar.build_layered_dedupe(
                dry_run=ns.dry_run,
                min_group_size=ns.dedupe_min_group_size,
            ).to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.project_startup_index:
        cfg = config or JaznConfig()
        payload = build_project_startup_index(cfg.root, write=True)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.topic_guard:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        payload = TopicMismatchGuard().analyse(text, runtime_version=cfg.version).to_dict()
        print(json.dumps({"runtime_version": cfg.version, "topic_mismatch_guard": payload}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.dialogue_intent:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        payload = DialogueIntentClassifier().classify(text).to_dict()
        print(json.dumps({"runtime_version": cfg.version, "dialogue_intent_classifier": payload}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.module_responsibility_map:
        cfg = config or JaznConfig()
        payload = ModuleResponsibilityMap(cfg.root).build(write=True)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.seed_requirements_ledger:
        cfg = config or JaznConfig()
        path = RequirementsLedger(cfg.root).seed_manifest_requirements()
        print(json.dumps({"runtime_version": cfg.version, "requirements_ledger": str(path), "seeded": True}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0


    if ns.last_turn:
        cfg = config or JaznConfig()
        payload = TurnTraceReader(cfg.root).latest() or {"schema_version": schema_version("turn_checkpoint"), "found": False, "reason": "no_checkpoint_found"}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.compare_runtime_visible:
        cfg = config or JaznConfig()
        payload = RuntimeVisibleAnswerComparator(cfg.root).compare(ns.trace_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.language_resources:
        cfg = config or JaznConfig()
        payload = {"runtime_version": cfg.version, "language_resource_registry": LanguageResourceRegistry().to_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.polish_reasoning_sources:
        cfg = config or JaznConfig()
        payload = {"runtime_version": cfg.version, "polish_reasoning_sources": PolishReasoningSourceRegistry(cfg.root).to_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.nlp_resource_status:
        cfg = config or JaznConfig()
        registry = LexicalResourceRegistry(
            cfg.root,
            verified_sources_path=cfg.root / cfg.lexical_resources_registry_path,
            project_lexicon_path=cfg.root / cfg.latka_project_lexicon_path,
            cache_path=cfg.lexical_resource_cache_path,
        )
        payload = {"runtime_version": cfg.version, "nlp_resource_status": registry.to_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.polish_morphology or ns.morfeusz_status or ns.polimorf_status:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        payload = build_polish_morphology_diagnostics(cfg.root, text)
        if ns.morfeusz_status or ns.polimorf_status:
            wanted = "morfeusz2-sgjp" if ns.morfeusz_status else "polimorf"
            statuses = payload["polish_morphology"].get("provider_statuses", [])
            payload = {
                "runtime_version": cfg.version,
                "schema_version": "polish_provider_status/v14.8.4",
                "provider_status": next((item for item in statuses if item.get("provider") == wanted), None),
                "truth_boundary": "Status providera mÄ‚Ĺ‚wi tylko, czy lokalny adapter jest dostĂ„â„˘pny. Nie oznacza pobrania peÄąâ€šnego sÄąâ€šownika ani peÄąâ€šnej dezambiguacji jĂ„â„˘zyka.",
            }
        else:
            payload = {"runtime_version": cfg.version, **payload}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.polish_reasoning_frame or ns.polish_reasoning_bootstrap_plan:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        payload = build_polish_reasoning_diagnostics(cfg.root, text)
        if ns.polish_reasoning_bootstrap_plan:
            payload = {
                "runtime_version": cfg.version,
                "schema_version": "polish_reasoning_bootstrap_plan/v14.8.4",
                "bootstrap_commands": payload["bootstrap_commands"],
                "source_registry": payload["source_registry"],
                "truth_boundary": "Bootstrap instaluje providery i modele z Internetu lokalnie; patch nie vendoruje duÄąÄ˝ych sÄąâ€šownikÄ‚Ĺ‚w ani modeli.",
            }
        else:
            payload = {"runtime_version": cfg.version, **payload}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.wsjp_lookup_plan or ns.nkjp_lookup_plan:
        cfg = config or JaznConfig()
        term = _message_from_remainder(ns.message)
        planner = PolishOnlineLookupPlanner()
        lookup = planner.nkjp(term).to_dict() if ns.nkjp_lookup_plan else planner.wsjp(term).to_dict()
        payload = {
            "runtime_version": cfg.version,
            "schema_version": "polish_reasoning_lookup_plan/v14.8.3",
            "lookup_plan": lookup,
            "truth_boundary": "To jest plan/link lookupu. Runtime nie twierdzi, ÄąÄ˝e pobraÄąâ€š definicjĂ„â„˘ lub przykÄąâ€šady bez realnego ÄąÄ˝Ă„â€¦dania HTTP i zapisu ÄąĹźrÄ‚Ĺ‚dÄąâ€ša.",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.voice_source_contract:
        cfg = config or JaznConfig()
        payload = {"runtime_version": cfg.version, "voice_source_contract": VoiceSourceContract.build(runtime_active=True, runtime_mode="one_shot").to_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.rendering_mode:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        payload = {"runtime_version": cfg.version, "runtime_rendering_mode": RuntimeRenderingModeSelector().select(text).to_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.raw_chat_status or getattr(ns, "raw_chat_status_json", False):
        cfg = config or JaznConfig()
        payload = {"runtime_version": cfg.version, "raw_chat_status": RawMemoryInspector(cfg.root, cfg.memory_db_path).inspect().to_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0


    if ns.llm_route_status:
        cfg = config or JaznConfig()
        adapter_command = _runtime_command_from_cli_args(ns)
        payload = {
            "runtime_version": cfg.version,
            "llm_route_status": build_llm_route_status(
                cfg,
                command=adapter_command,
                infer_host_environment=True,
            ).to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0


    if ns.model_guided_speech_status:
        cfg = config or JaznConfig()
        adapter_command = _runtime_command_from_cli_args(ns)
        payload = {
            "runtime_version": cfg.version,
            "model_guided_speech_status": build_model_guided_speech_status(
                cfg,
                command=adapter_command,
                infer_host_environment=True,
            ).to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.model_adapter_status:
        cfg = config or JaznConfig()
        adapter_command = _runtime_command_from_cli_args(ns)
        if adapter_command == "--chat-open-ai":
            cfg = apply_openai_cli_settings(
                cfg,
                model=ns.openai_model,
                api_base=ns.openai_api_base,
                timeout_seconds=ns.openai_timeout,
                max_output_tokens=ns.openai_max_output_tokens,
            )
        elif adapter_command == "--chat-lm-studio":
            cfg = apply_lm_studio_cli_settings(
                cfg,
                model=ns.lm_studio_model,
                api_base=ns.lm_studio_api_base,
                timeout_seconds=ns.lm_studio_timeout,
                max_output_tokens=ns.lm_studio_max_output_tokens,
            )
        elif adapter_command == "--local-llm":
            cfg = apply_local_llm_cli_settings(
                cfg,
                model=ns.local_llm_model,
                api_base=ns.local_llm_api_base,
                provider=ns.local_llm_provider,
            )
        payload = {
            "runtime_version": cfg.version,
            "model_adapter_status": build_model_adapter_status(
                cfg,
                command=adapter_command,
                infer_host_environment=True,
            ),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.dictionary_lookup:
        cfg = config or JaznConfig()
        term = _message_from_remainder(ns.message)
        payload = {"runtime_version": cfg.version, "dictionary_lookup": ExternalDictionaryAdapter(cfg.root, allow_network=cfg.dictionary_allow_network, user_agent=cfg.network_user_agent, timeout_seconds=cfg.dictionary_online_lookup_timeout_seconds, max_retries=cfg.network_max_retries, cache_ttl_seconds=cfg.network_cache_ttl_seconds).lookup(term).to_dict()}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.active_cache_status or ns.write_active_runtime_marker:
        cfg = config or JaznConfig()
        if ns.write_active_runtime_marker:
            payload = write_active_runtime_marker(cfg.root, source_zip=ns.source_zip, marker_output=ns.marker_output)
        else:
            payload = build_active_runtime_status(cfg.root, source_zip=ns.source_zip, marker_output=ns.marker_output)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.github_plan:
        cfg = config or JaznConfig()
        path = write_github_repository_plan(cfg.root)
        plan = build_github_repository_plan(cfg.root).to_dict()
        plan["written_to"] = str(path)
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.dedup_report:
        cfg = config or JaznConfig()
        path = write_dedup_report(cfg.root, cfg.root / "reports" / "DEDUP_REPORT_V14_6_1.json")
        print(path.read_text(encoding="utf-8"))
        return 0

    if ns.lexical_frame:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        polish = PolishUnderstandingEngine(cfg.root).analyse(text)
        nlp = PolishLemmatizationEngine(cfg.root).analyse(text)
        lexical = LexicalSemanticUnderstanding(cfg.root).analyse(text, polish_report=polish.to_dict(), nlp_report=nlp.to_dict())
        print(json.dumps({"runtime_version": cfg.version, "polish_understanding": polish.to_dict(), "polish_nlp": nlp.to_dict(), "lexical_semantic_understanding": lexical.to_dict()}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if ns.nlp_frame:
        cfg = config or JaznConfig()
        text = _message_from_remainder(ns.message)
        nlp = PolishLemmatizationEngine(cfg.root).analyse(text)
        print(json.dumps({"runtime_version": cfg.version, "polish_nlp": nlp.to_dict()}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0


    if ns.record_final_reply:
        engine = JaznEngine(config)
        try:
            if not ns.turn_id or not ns.trace_id or not ns.timestamp_header:
                parser.error("--record-final-reply wymaga --turn-id, --trace-id i --timestamp-header")
            if ns.final_text_file:
                final_text = ns.final_text_file.read_text(encoding="utf-8")
            else:
                final_text = _message_from_remainder(ns.message)
            result = engine.persist_final_visible_reply(
                turn_id=ns.turn_id,
                trace_id=ns.trace_id,
                timestamp_header=ns.timestamp_header,
                final_text=final_text,
                state_emoticon=ns.state_emoticon,
                source="chatgpt_visible_layer_cli",
                client_context={"client": "chatgpt_visible_layer_cli", "lifecycle": "one_shot_visible_capture"},
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        finally:
            engine.shutdown()
        return 0

    if ns.runtime_preview or ns.dev_preview:
        engine = JaznEngine(config)
        try:
            text = _message_from_remainder(ns.message)
            envelope = engine.process_turn(
                text,
                client_context={
                    "client": "chatgpt_runtime_preview" if ns.runtime_preview else "chatgpt_dev_preview",
                    "lifecycle": "one_shot_preview",
                    "preview_phase": "single_integrated_process_turn",
                    "session_id": ns.session_id,
                    "no_carryover": ns.no_carryover,
                    "terminal_mode": "compact" if ns.runtime_preview else "full_dev_payload",
                },
            )
            envelope_dict, runtime_truth_gate = apply_runtime_truth_gate(envelope.to_dict())
            cognitive_frame = envelope_dict.get("cognitive_frame") or {}
            runtime_text = envelope_dict.get("final_visible_text") or ""
            final_contract = envelope_dict.get("final_response_contract") or {}
            integrity = final_contract.get("final_visible_integrity") if isinstance(final_contract.get("final_visible_integrity"), dict) else {}
            dialogue_classifier = cognitive_frame.get("dialogue_intent_classifier") or envelope_dict.get("dialogue_intent_classifier") or {}
            route_trace = cognitive_frame.get("turn_route_trace") or (envelope_dict.get("conversation_decision") or {}).get("turn_route_trace") or {}
            conversation_decision = envelope_dict.get("conversation_decision") if isinstance(envelope_dict.get("conversation_decision"), dict) else {}
            payload = {
                "schema_version": schema_version("runtime_preview_full_payload", version=PACKAGE_VERSION_FULL),
                "runtime_version": PACKAGE_VERSION_FULL,
                "mode": "diagnostic_dev_preview_full_payload_single_process_turn_not_background_daemon",
                "turn_trace": envelope_dict.get("trace"),
                "final_visible_text": runtime_text,
                "runtime_text": runtime_text,
                "fallback_detected": any(
                    signature in runtime_text
                    for signature in (
                        "Nie znalazĹ‚am osobnej trasy odpowiedzi",
                        "runtime odebraĹ‚ wiadomoĹ›Ä‡",
                        "debugowy fallback",
                        "pusty fallback",
                    )
                ) or final_contract.get("fallback_classification") not in {None, "not_fallback"},
                "runtime_answer_quality": final_contract.get("runtime_answer_quality"),
                "fallback_classification": final_contract.get("fallback_classification"),
                "startup_procedure_required": bool(final_contract.get("startup_procedure_required")),
                "source_origin": cognitive_frame.get("source_origin"),
                "self_state_runtime": cognitive_frame.get("self_state_runtime"),
                "affect_mix": envelope_dict.get("affect_mix"),
                "dialogue_state": envelope_dict.get("dialogue_state"),
                "turn_route_trace": route_trace,
                "final_response_contract": envelope_dict.get("final_response_contract"),
                "normal_response_blocked": envelope_dict.get("normal_response_blocked"),
                "runtime_response_status": envelope_dict.get("runtime_response_status"),
                "runtime_truth_gate": runtime_truth_gate,
                "cognitive_turn_envelope": envelope_dict,
                "cognitive_frame": cognitive_frame,
                "visible_runtime_preview_contract": {
                    "schema_version": visible_preview_contract_version(engine.config.root),
                    "timestamp_header": (envelope_dict.get("trace") or {}).get("timestamp_header"),
                    "active_root": str(engine.config.root),
                    "start_file": "main.py",
                    "response_source": "runtime.process_turn + final_response_contract",
                    "required_visible_fields": ["timestamp_header", "active_root", "start_file", "runtime_answer_quality", "fallback_classification", "response_source", "one_shot_or_chat_loop_limit"],
                    "must_show_when_user_asks_about_runtime_files_timestamp_preview_or_fallback": True,
                    "one_shot_or_chat_loop_limit": "--runtime-preview i --dev-preview sÄ… jednorazowymi wywoĹ‚aniami; staĹ‚Ä… pÄ™tlÄ™ daje dopiero python main.py --chat.",
                },
                "active_extraction_cache_status": build_active_runtime_status(engine.config.root),
                "startup_summary": build_startup_summary(engine.config),
                "free_dialogue_memory_nlp_bridge": build_startup_summary(engine.config),
                "truth_boundary": "--dev-preview wykonuje jedno zintegrowane wywoĹ‚anie process_turn i pokazuje peĹ‚nÄ… kopertÄ™ technicznÄ…. To nie jest widoczna odpowiedĹş Ĺatki dla uĹĽytkownika ani dowĂłd procesu w tle.",
            }
            compact = {
                "schema_version": schema_version("runtime_preview_compact", version=PACKAGE_VERSION_FULL),
                "runtime_version": PACKAGE_VERSION_FULL,
                "mode": "runtime_preview_compact_not_user_visible_latka_reply",
                "final_visible_text": runtime_text,
                "runtime_route": final_contract.get("runtime_route") or conversation_decision.get("selected_route") or route_trace.get("selected_route"),
                "primary_intent": dialogue_classifier.get("primary_intent") or conversation_decision.get("detected_user_intent"),
                "diagnostic_request": dialogue_classifier.get("diagnostic_request"),
                "fallback_classification": final_contract.get("fallback_classification"),
                "runtime_answer_quality": final_contract.get("runtime_answer_quality"),
                "runtime_truth_gate": runtime_truth_gate,
                "timestamp_trusted": integrity.get("timestamp_trusted") if integrity else final_contract.get("timestamp_trusted"),
                "final_visible_integrity_valid": integrity.get("valid") if integrity else None,
                "normal_response_blocked": envelope_dict.get("normal_response_blocked"),
                "runtime_response_status": envelope_dict.get("runtime_response_status"),
                "full_payload_written_to": str(ns.runtime_preview_output) if ns.runtime_preview_output else None,
                "dev_preview_command": "python main.py --dev-preview <tekst>",
                "truth_boundary": "To jest krĂłtki podglÄ…d diagnostyczny jednej tury runtime. Nie traktuj samego --runtime-preview jako rozmowy z ĹatkÄ…; do staĹ‚ej rozmowy sĹ‚uĹĽy --chat, a peĹ‚ny JSON techniczny jest w --dev-preview albo --runtime-preview-output.",
            }
            payload_json = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            if ns.runtime_preview_output:
                ns.runtime_preview_output.parent.mkdir(parents=True, exist_ok=True)
                ns.runtime_preview_output.write_text(payload_json + "\n", encoding="utf-8")
            if ns.dev_preview and not ns.runtime_preview_output:
                print(payload_json)
            else:
                print(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True))
        finally:
            engine.shutdown()
        return 0


    if ns.chat_gpt:
        cfg = apply_chatgpt_cli_settings(config or JaznConfig())
        bridge_text = _message_from_remainder(ns.message)
        # v14.8.5.026B: --chat-gpt is the single public ChatGPT bridge.
        # Human one-shot usage (`--chat-gpt -- "..."`) renders only
        # final_visible_text, while stdin keeps the JSONL protocol for tools.
        output_mode = _bridge_text_output_mode(ns, bridge_text)
        daemon_ensure, daemon_exit = _ensure_daemon_or_error(ns, cfg, "--chat-gpt")
        if daemon_exit is not None:
            return daemon_exit
        if bridge_text:
            daemon_first = _env_flag_enabled("JAZN_CHATGPT_PREFER_DAEMON", default=True)
            delegated = _try_chat_gpt_one_shot_via_daemon(
                cfg=cfg,
                text=bridge_text,
                session_id=ns.session_id,
                no_carryover=ns.no_carryover,
                host=ns.daemon_host,
                port=ns.daemon_port,
                timeout=ns.daemon_chat_timeout,
                output_mode=output_mode,
                request_id=ns.daemon_request_id,
                wait_budget=ns.daemon_wait_budget,
                poll_interval=ns.daemon_poll_interval,
                trusted_time_iso=(ns.trusted_time_iso or os.environ.get("JAZN_TRUSTED_TIME_ISO")) if trusted_time_env is not None else None,
                trusted_time_source=(ns.trusted_time_source or os.environ.get("JAZN_TRUSTED_TIME_SOURCE")) if trusted_time_env is not None else None,
                trusted_time_max_age_seconds=(
                    ns.trusted_time_max_age_seconds
                    if ns.trusted_time_max_age_seconds is not None
                    else _optional_positive_env_int("JAZN_TRUSTED_TIME_MAX_AGE_SECONDS")
                ),
                trusted_time_required=trusted_time_required_for_turn,
            )
            if delegated is not None:
                return delegated
        else:
            daemon_first = False
        bridge_stdin = io.StringIO(bridge_text + "\n") if bridge_text else None
        if bridge_stdin is None and ns.final_only and not ns.chat_gpt_final_only and sys.stdin.isatty():
            print(
                "--chat-gpt przy trybie final_visible_text wymaga wiadomoĹ›ci po -- albo danych na stdin, np. "
                "python -X utf8 main.py --chat-gpt -- \"CzeĹ›Ä‡ Ĺatko\"",
                file=sys.stderr,
            )
            return 2
        return run_jsonl_chat_bridge(
            config=cfg,
            session_id=ns.session_id,
            no_carryover=ns.no_carryover,
            command="--chat-gpt",
            stdin=bridge_stdin,
            require_openai_api_key=False,
            output_mode=output_mode,
            one_shot_degraded=bool(bridge_text and daemon_first),
        )

    if ns.local_llm:
        cfg = apply_local_llm_cli_settings(
            config or JaznConfig(),
            model=ns.local_llm_model,
            api_base=ns.local_llm_api_base,
            provider=ns.local_llm_provider,
        )
        bridge_text = _message_from_remainder(ns.message)
        daemon_ensure, daemon_exit = _ensure_daemon_or_error(ns, cfg, "--local-llm")
        if daemon_exit is not None:
            return daemon_exit
        output_mode = _bridge_text_output_mode(ns, bridge_text)
        bridge_stdin = io.StringIO(bridge_text + "\n") if bridge_text else None
        return run_jsonl_chat_bridge(
            config=cfg,
            session_id=ns.session_id,
            no_carryover=ns.no_carryover,
            command="--local-llm",
            stdin=bridge_stdin,
            require_openai_api_key=False,
            output_mode=output_mode,
        )

    if ns.chat_lm_studio:
        cfg = apply_lm_studio_cli_settings(
            config or JaznConfig(),
            model=ns.lm_studio_model,
            api_base=ns.lm_studio_api_base,
            timeout_seconds=ns.lm_studio_timeout,
            max_output_tokens=ns.lm_studio_max_output_tokens,
        )
        bridge_text = _message_from_remainder(ns.message)
        daemon_ensure, daemon_exit = _ensure_daemon_or_error(ns, cfg, "--chat-lm-studio")
        if daemon_exit is not None:
            return daemon_exit
        output_mode = _bridge_text_output_mode(ns, bridge_text)
        bridge_stdin = io.StringIO(bridge_text + "\n") if bridge_text else None
        return run_jsonl_chat_bridge(
            config=cfg,
            session_id=ns.session_id,
            no_carryover=ns.no_carryover,
            command="--chat-lm-studio",
            stdin=bridge_stdin,
            require_openai_api_key=False,
            output_mode=output_mode,
        )

    if ns.chat_open_ai:
        cfg = config or JaznConfig()
        route_status = build_llm_route_status(cfg, command="--chat-open-ai", infer_host_environment=False, probe_local=False)
        if route_status.openai_api.get("api_key_present") and not route_status.openai_api.get("allowed"):
            print(json.dumps({
                "ok": False,
                "error": "paid_openai_not_allowed",
                "llm_route_status": route_status.to_dict(),
                "required_env": "JAZN_ALLOW_PAID_OPENAI=1",
                "truth_boundary": "--chat-open-ai jest płatną trasą OpenAI API; sama obecność OPENAI_API_KEY nie wystarcza.",
            }, ensure_ascii=False, indent=2, sort_keys=True))
            return 2
        apply_openai_cli_settings(
            cfg,
            model=ns.openai_model,
            api_base=ns.openai_api_base,
            timeout_seconds=ns.openai_timeout,
            max_output_tokens=ns.openai_max_output_tokens,
        )
        bridge_text = _message_from_remainder(ns.message)
        daemon_ensure, daemon_exit = _ensure_daemon_or_error(ns, cfg, "--chat-open-ai")
        if daemon_exit is not None:
            return daemon_exit
        output_mode = _bridge_text_output_mode(ns, bridge_text)
        bridge_stdin = io.StringIO(bridge_text + "\n") if bridge_text else None
        return run_jsonl_chat_bridge(
            config=cfg,
            session_id=ns.session_id,
            no_carryover=ns.no_carryover,
            command="--chat-open-ai",
            stdin=bridge_stdin,
            require_openai_api_key=True,
            output_mode=output_mode,
        )

    if ns.export_system or ns.export_memory or ns.export_full or ns.export_nlp or ns.export_github_source_safe:
        cfg = config or JaznConfig()
        mode = "system" if ns.export_system else "memory" if ns.export_memory else "nlp" if ns.export_nlp else "github_source_safe" if ns.export_github_source_safe else "full"
        profile = "source-safe" if mode == "github_source_safe" else mode
        payload = export_payload(
            root=Path(cfg.root).resolve(),
            profile=profile,
            output=ns.output,
            confirm_private_data=ns.confirm_private_data,
            preview_only=bool(ns.export_preview or (profile in {"memory", "full"} and not ns.confirm_private_data)),
            source_origin="main.py legacy export",
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        if payload.get("preview", {}).get("requires_confirmation") and not payload.get("artifact_created"):
            return 2
        return 0 if payload.get("ok", True) else 2

    if ns.chat_loop:
        cfg = apply_chat_cli_settings(config or JaznConfig())
        bridge_text = _message_from_remainder(ns.message)
        daemon_ensure, daemon_exit = _ensure_daemon_or_error(ns, cfg, "--chat")
        if daemon_exit is not None:
            return daemon_exit
        if bridge_text:
            return _run_chat_command_one_shot(
                cfg=cfg,
                text=bridge_text,
                session_id=ns.session_id,
                no_carryover=ns.no_carryover,
                source_client="terminal_chat_one_shot",
                lifecycle="terminal_chat_one_shot",
                command="--chat",
                output_mode="final_visible_text",
            )
        session = RuntimeSessionWorker(
            session_factory=JaznRuntimeSession,
            config=cfg,
            session_id=ns.session_id,
            no_carryover=ns.no_carryover,
            source_client="chat",
            command="--chat",
            timeout_seconds=runtime_turn_timeout_seconds(cfg),
        )
        try:
            run_persistent_chat(session, session_id=ns.session_id, no_carryover=ns.no_carryover)
        finally:
            session.close()
        return 0

    engine = JaznEngine(config)
    try:
        text = _message_from_remainder(ns.message)
        if text and not ns.cognitive_frame:
            daemon_ensure, daemon_exit = _ensure_daemon_or_error(ns, engine.config, "direct_message")
            if daemon_exit is not None:
                return daemon_exit
        if ns.cognitive_frame:
            packet = engine.build_cognitive_frame(text, client_context={"client": "chatgpt_cli_bridge", "lifecycle": "one_shot"})
            print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
        elif text:
            if ns.debug_direct:
                print(engine.handle_user_message(text, client_context={"client": "cli_direct_debug", "debug_direct": True, "lifecycle": "one_shot"}))
            else:
                envelope = engine.process_turn(text, client_context={"client": "cli_direct_conversation", "debug_direct": False, "lifecycle": "one_shot", "session_id": ns.session_id, "no_carryover": ns.no_carryover})
                envelope_dict, _runtime_truth_gate = apply_runtime_truth_gate(envelope.to_dict())
                print(envelope_dict.get("final_visible_text", ""))
        else:
            print(engine.bootstrap())
    finally:
        engine.shutdown()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # Pozwala bezpiecznie ucinaĂ„â€ˇ dÄąâ€šugie podglĂ„â€¦dy JSON przez `head`/pipe
        # bez faÄąâ€šszywego wraÄąÄ˝enia awarii runtime.
        try:
            sys.stdout.close()
        except Exception:
            pass
        raise SystemExit(0)

