from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from pathlib import Path
from typing import Any
import argparse
import hashlib
import json
import sys
import urllib.request
import zipfile

from latka_jazn.config import JaznConfig
from latka_jazn.memory.raw_archive import chat_archive_diagnostics
from latka_jazn.tools.active_extraction_cache import build_active_runtime_status, detect_start_file
from latka_jazn.core.version_source import VERSION_MODULE_RELATIVE_PATH
from latka_jazn.core.project_index import build_project_startup_index, project_startup_index_status
from latka_jazn.core.voice_source_contract import VoiceSourceContract
from latka_jazn.memory.conversation_archive import build_conversation_archive_status
from latka_jazn.memory.normalization_sidecar import build_memory_normalization_status, build_wake_state_status
from latka_jazn.memory.raw_chat_importer import RawChatImporter
from latka_jazn.model_adapters.factory import build_model_adapter_status
from latka_jazn.core.runtime_environment import detect_runtime_environment
from latka_jazn.core.self_knowledge_contract import build_self_knowledge_packet, build_self_knowledge_summary
from latka_jazn.memory.runtime_write_access_contract import build_runtime_write_access_status
from latka_jazn.core.package_integrity_manifest import package_integrity_manifest_status

SCHEMA_VERSION = "self_owned_startup_contract/v14.6.10"
MINIMAL_LOADER_RESOURCE = "latka_jazn/resources/chatgpt_startup_loader_v14_8_2_4.txt"
STARTUP_CONTRACT_RESOURCE = "latka_jazn/resources/startup_contract_v14_8_2_4.json"


@dataclass(slots=True)
class StartupResponsibilitySplit:
    chatgpt_loader_responsibilities: list[str]
    runtime_owned_responsibilities: list[str]
    forbidden_chatgpt_behaviors: list[str]
    truth_boundary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StartupStatus:
    schema_version: str
    runtime_version: JaznConfig.VERSION
    active_root: str
    start_file: str | None
    active_database: str
    active_runtime_write_database: str
    runtime_write_access_status: dict[str, Any]
    active_conversation_archive: str | None
    active_conversation_fts: str | None
    active_staging_database: str | None
    storage_layout: str
    active_cache_status: dict[str, Any]
    raw_memory_status: dict[str, Any]
    conversation_archive_status: dict[str, Any]
    memory_normalization_status: dict[str, Any]
    wake_state_status: dict[str, Any]
    project_startup_index_status: dict[str, Any]
    update_history_status: dict[str, Any]
    network_policy_status: dict[str, Any]
    dictionary_provider_status: dict[str, Any]
    manifest_profile_status: dict[str, Any]
    voice_source_contract_status: dict[str, Any]
    model_adapter_status: dict[str, Any]
    runtime_environment: dict[str, Any]
    self_knowledge_status: dict[str, Any]
    raw_chat_importer_status: dict[str, Any]
    cli_capabilities: dict[str, bool]
    responsibility_split: dict[str, Any]
    minimal_chatgpt_loader: str
    runtime_contract_files: list[str]
    status_quality: str
    folder_ready: bool
    daemon_ready: bool
    voice_ready: bool
    one_shot_or_chat_loop_limit: str
    truth_boundary: str
    startup_status_mode: str = "fast"
    sqlite_health_mode: str = "metadata"
    network_time_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _read_optional(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding='utf-8')
    except FileNotFoundError:
        return ''


def _display_path(root: Path, path: str | Path | None) -> str | None:
    if path is None:
        return None
    p = Path(path)
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return str(path)


def _daemon_ready_from_active_marker(root: Path, cache_status: dict[str, Any], *, timeout_seconds: float = 0.75) -> bool:
    """Confirm daemon readiness through the marker-bound loopback /ready endpoint.

    The startup status must not infer a live daemon from a fresh marker alone.  A
    positive result requires a valid marker, an answering loopback endpoint, an
    active/readiness state and (when both are available) the same PID in the
    marker and endpoint payload.  Failures remain observational and non-blocking.
    """

    if cache_status.get("existing_marker_found") is not True or cache_status.get("active_marker_valid") is not True:
        return False
    marker_output = cache_status.get("marker_output")
    marker_path = Path(str(marker_output)).resolve() if marker_output else (root / "workspace_runtime" / "JAZN_ACTIVE_RUNTIME.json").resolve()
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    host = str(marker.get("daemon_host") or "127.0.0.1").strip()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    try:
        port = int(marker.get("daemon_port") or 8787)
    except (TypeError, ValueError):
        return False
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/ready", timeout=max(0.05, float(timeout_seconds))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    marker_pid = marker.get("daemon_pid") or (marker.get("runtime_daemon") or {}).get("pid")
    endpoint_pid = payload.get("daemon_pid") or (payload.get("runtime_daemon") or {}).get("pid")
    if marker_pid is not None and endpoint_pid is not None:
        try:
            if int(marker_pid) != int(endpoint_pid):
                return False
        except (TypeError, ValueError):
            return False
    return bool(
        payload.get("ok") is True
        and payload.get("endpoint_ok", True) is True
        and payload.get("liveness_ok", True) is True
        and payload.get("readiness_ok", True) is True
        and payload.get("runtime_process_active", True) is True
        and payload.get("active_state") in {"active_trusted", "active_degraded"}
    )


def default_responsibility_split() -> StartupResponsibilitySplit:
    return StartupResponsibilitySplit(
        chatgpt_loader_responsibilities=[
            'wskazać aktualną paczkę lub aktualny folder',
            'złożyć części ZIP w kolejności, jeżeli pełny ZIP nie istnieje',
            'sprawdzić integralność ZIP-a i ewentualne SHA256',
            'rozpakować albo użyć cache tylko po zgodności wersji/manifestu/checksum',
            'uruchomić main.py/run.py/jazn.py realnie przed zwykłą rozmową jako Łatka',
            'nie udawać aktywnego runtime, jeżeli runtime nie został uruchomiony',
            'przekazać użytkownikowi krótki status startowy lub błąd startu',
        ],
        runtime_owned_responsibilities=[
            'wystawić --startup-status jako własny status startu i kontrakt bootstrapu',
            'wystawić --active-cache-status i marker aktywnego folderu',
            'wystawić --runtime-preview jako jednorazowy podgląd odpowiedzi i koperty poznawczej',
            'wystawić --memory-plan dla pytań pamięciowych i własnego planera wyszukiwania',
            'wystawić --memory-normalization-status i --wake-state-status jako status warstwy pamięciowej bez udawania ciągłości',
            'budować wake_state tylko z rekordów sidecara, a nie z samej obecności dużej bazy SQLite',
            'wystawić --dedupe-memory-sidecar jako niedestrukcyjne zwijanie duplikatów w warstwie audytowej',
            'wystawić --truth-boundary-check dla granicy prawdy między runtime, ChatGPT, plikami i wnioskiem',
            'wystawić --fallback-audit dla wykrywania odpowiedzi debugowych, fallbackowych i nietrafionych',
            'samodzielnie klasyfikować jakość odpowiedzi, fallbacki, source_origin, stan operacyjny i potrzebę diagnostyki',
            'samodzielnie planować wyszukiwanie pamięci zamiast wymagać długiej listy reguł w instrukcji ChatGPT',
            'budować przy starcie indeks wszystkich plików oraz mapę modułów, klas, funkcji i metod',
            'wykrywać topic-mismatch, żeby aktualny hotfix/temat nie wracał do historycznych tras',
        ],
        forbidden_chatgpt_behaviors=[
            'rozbudowywać projektową instrukcję ChatGPT w zastępstwie brakującej logiki runtime',
            'udawać, że stały proces działa w tle, gdy wykonano tylko jednorazowe wywołanie',
            'ukrywać fallback runtime albo zastępować go płynną odpowiedzią bez informacji o jakości',
            'twierdzić, że pamięć została odczytana albo rozpakowana bez sprawdzenia plików/indeksu',
            'traktować styl wypowiedzi jako dowód uruchomionej Jaźni',
        ],
        truth_boundary=(
            'Instrukcja ChatGPT ma być lekkim loaderem. Logika startu, pamięci, fallbacków, statusu, '
            'timestampu i granicy prawdy należy do runtime Jaźni po uruchomieniu. ChatGPT jest głosem i narzędziem, '
            'nie ręcznie przepisywanym mózgiem Jaźni.'
        ),
    )


def cli_capabilities(start_file: str | None = 'main.py') -> dict[str, bool]:
    # Lista utrzymywana jawnie, żeby --startup-status mógł powiedzieć ChatGPT,
    # które obowiązki są już własnością runtime, a nie instrukcji projektu.
    return {
        '--status-readonly': True,
        '--startup-status': True,
        '--startup-status-fast': True,
        '--startup-status-deep': True,
        '--self-check': True,
        '--truth-boundary-check': True,
        '--fallback-audit': True,
        '--memory-plan': True,
        '--memory-normalization-status': True,
        '--normalize-memory-sidecar': True,
        '--wake-state-status': True,
        '--build-wake-state': True,
        '--dedupe-memory-sidecar': True,
        '--runtime-preview': True,
        '--chat-gpt': bool(start_file),
        '--chat-open-ai': bool(start_file),
        '--chat-openai': bool(start_file),
        '--chat-lm-studio': bool(start_file),
        '--turn-trace': True,
        '--network-time-check': True,
        '--sqlite-integrity-audit': True,
        '--active-cache-status': True,
        '--project-startup-index': True,
        '--write-active-runtime-marker': True,
        '--chat': bool(start_file),
        '--export-full': bool(start_file),
        '--voice-source-contract': bool(start_file),
        '--rendering-mode': bool(start_file),
        '--raw-chat-status': bool(start_file),
        '--conversation-archive-status': bool(start_file),
        '--conversation-archive-search': bool(start_file),
        '--model-adapter-status': bool(start_file),
        '--model-guided-speech-status': bool(start_file),
        '--ensure-daemon': bool(start_file),
        '--daemon-autostart-policy': bool(start_file),
        '--llm-route-status': bool(start_file),
    }


def raw_memory_status(root: Path) -> dict[str, Any]:
    raw = root / 'memory' / 'raw'
    chat = raw / 'chat.html'
    archive = raw / 'chat.html.7z'
    diag = chat_archive_diagnostics(root)
    return {
        'schema_version': 'raw_memory_startup_status/v14.6.10',
        'chat_html_present': chat.exists(),
        'chat_html_size_bytes': chat.stat().st_size if chat.exists() else None,
        'chat_html_archive_present': archive.exists(),
        'chat_html_archive_size_bytes': archive.stat().st_size if archive.exists() else None,
        'archive_diagnostics': diag,
        'truth_boundary': 'Rozpakowany chat.html nie musi być w ZIP-ie, jeżeli chat.html.7z istnieje i indeks SQLite jest dostępny; status musi jednak jasno powiedzieć, co faktycznie istnieje.',
    }



def update_history_status(root: Path) -> dict[str, Any]:
    index = root / 'docs' / 'update_history' / 'INDEX.json'
    manifests = root / 'docs' / 'update_history' / 'manifests'
    return {
        'schema_version': 'update_history_status/v14.6.10',
        'index_present': index.exists(),
        'manifest_history_dir_present': manifests.exists(),
        'historical_manifest_count': len(list(manifests.glob('MANIFEST*.json'))) if manifests.exists() else 0,
        'root_historical_manifest_count': len([p for p in root.glob('MANIFEST*.json') if p.name not in {'MANIFEST_CURRENT.json', 'PACKAGE_INTEGRITY_MANIFEST.json'}]),
        'package_integrity_manifest': package_integrity_manifest_status(root).to_dict(),
        'truth_boundary': 'Historyczne manifesty są materiałem audytowym. PACKAGE_INTEGRITY_MANIFEST.json służy kontroli paczki i wydania; jego brak nie blokuje uruchomienia runtime.',
    }

def network_policy_status(cfg: JaznConfig) -> dict[str, Any]:
    return {
        'schema_version': 'network_policy_status/v14.8.2.4',
        'allow_network': cfg.allow_network,
        'dictionary_allow_network': cfg.dictionary_allow_network,
        'research_allow_network': cfg.research_allow_network,
        'timeout_connect_seconds': cfg.network_default_timeout_connect_seconds,
        'timeout_read_seconds': cfg.network_default_timeout_read_seconds,
        'cache_required': cfg.network_cache_required,
        'cache_ttl_seconds': cfg.network_cache_ttl_seconds,
        'truth_boundary': 'Sieć jest dozwolona w konfiguracji, ale każdy provider musi oddać jawny status źródła, timeoutu, cache i błędu.',
    }

def dictionary_provider_status(cfg: JaznConfig) -> dict[str, Any]:
    return {
        'schema_version': 'dictionary_provider_status/v14.8.2.4',
        'allow_network': cfg.dictionary_allow_network,
        'provider_order': list(cfg.dictionary_provider_order),
        'cache_path': str(cfg.runtime_workspace_dir / 'dictionary_cache.sqlite3'),
        'mediawiki_wiktionary_provider': (Path(cfg.root) / 'latka_jazn' / 'nlp' / 'providers' / 'mediawiki_wiktionary_provider.py').exists(),
        'sjp_reference_provider': (Path(cfg.root) / 'latka_jazn' / 'nlp' / 'providers' / 'sjp_reference_provider.py').exists(),
        'wsjp_reference_provider': (Path(cfg.root) / 'latka_jazn' / 'nlp' / 'providers' / 'wsjp_reference_provider.py').exists(),
        'truth_boundary': 'Dostępność pliku providera nie oznacza, że sieć w danym środowisku odpowiedziała; wynik lookupu pokazuje provider_statuses. SJP/WSJP w v14.8.2.4 są linkami referencyjnymi bez masowego scrapingu definicji.',
    }

def manifest_profile_status(root: Path) -> dict[str, Any]:
    p = root / 'latka_jazn' / 'resources' / 'package_manifest_profiles.json'
    return {
        'schema_version': 'manifest_profile_status/v14.6.10',
        'profiles_present': p.exists(),
        'path': str(p),
        'truth_boundary': 'Profile odróżniają statyczny manifest paczki od dynamicznych plików runtime/pamięci.',
    }

def build_startup_status(
    config: JaznConfig | None = None,
    *,
    source_zip: Path | None = None,
    mode: str = "fast",
    runtime_command: str | None = None,
    infer_host_environment: bool = False,
) -> StartupStatus:
    cfg = config or JaznConfig()
    mode = (mode or getattr(cfg, "startup_status_default_mode", "fast") or "fast").strip().lower()
    if mode not in {"metadata", "fast", "deep"}:
        raise ValueError(f"Unsupported startup status mode: {mode}")
    sqlite_health_mode = "deep" if mode == "deep" else "metadata"
    requested_root = Path(cfg.root).resolve()
    cache_status = build_active_runtime_status(requested_root, source_zip=source_zip)
    root = Path(cache_status["active_root"]).resolve()
    if root != requested_root:
        cfg = replace(cfg, root=root)
    start_file = detect_start_file(root)
    # Status paths are observational. A missing project index is reported below;
    # creating it belongs to the explicit project-index command.
    loader = _read_optional(root, MINIMAL_LOADER_RESOURCE).strip()
    split = default_responsibility_split().to_dict()
    missing: list[str] = []
    if not root.exists():
        missing.append('active_root_missing')
    if not (root / 'latka_jazn').is_dir():
        missing.append('latka_jazn_package_missing')
    if not (root / VERSION_MODULE_RELATIVE_PATH).exists():
        missing.append('version_py_missing')
    if not start_file:
        missing.append('start_file_missing')
    if cache_status.get('existing_marker_found') and not cache_status.get('active_marker_valid'):
        missing.append('active_root_marker_invalid')
    status_quality = 'ready' if not missing else 'startup_blocked:' + ','.join(missing)
    runtime_active_for_voice = bool(
        status_quality == 'ready'
        and cache_status.get('runtime_root_valid') is True
        and cache_status.get('existing_marker_found') is True
        and cache_status.get('active_marker_valid') is True
    )
    runtime_environment = detect_runtime_environment(
        cfg,
        command=runtime_command,
        infer_host_environment=infer_host_environment,
    )
    model_adapter_status = build_model_adapter_status(
        cfg,
        command=runtime_command,
        infer_host_environment=infer_host_environment,
    )
    runtime_write_status = build_runtime_write_access_status(cfg, initialize=False).to_dict()
    language_channel = (
        model_adapter_status.get('visible_channel_adapter')
        or runtime_environment.visible_channel_adapter
        or 'chatgpt_or_model_adapter'
    )
    voice_source_contract = VoiceSourceContract.build(runtime_active=runtime_active_for_voice, runtime_mode='one_shot', language_channel=str(language_channel)).to_dict()
    active_runtime_write_database = runtime_write_status.get("active_runtime_write_database") or "unavailable:runtime_write_v1_missing_or_not_initialized"
    daemon_ready = False if mode == "metadata" else _daemon_ready_from_active_marker(root, cache_status)
    return StartupStatus(
        schema_version=SCHEMA_VERSION,
        runtime_version=cfg.version,
        active_root=str(root),
        start_file=start_file,
        active_database=str(cache_status.get("active_database") or cfg.conversation_archive_manifest_name),
        active_runtime_write_database=str(active_runtime_write_database),
        runtime_write_access_status=runtime_write_status,
        active_conversation_archive=str(cache_status.get("active_conversation_archive") or cfg.conversation_archive_manifest_name),
        active_conversation_fts=str(cache_status.get("active_conversation_fts") or "memory/sqlite/conversation_fts_v1/conversation_fts_0001.sqlite3"),
        active_staging_database=str(cache_status.get("active_staging_database") or "memory/sqlite/staging_v1/staging_memory_0001.sqlite3"),
        storage_layout=str(cache_status.get("storage_layout") or "conversation_archive_v1+fts_v1+staging_v1+runtime_write_v1"),
        active_cache_status=cache_status,
        raw_memory_status=raw_memory_status(root),
        conversation_archive_status=build_conversation_archive_status(root, health_mode=sqlite_health_mode).to_dict(),
        memory_normalization_status=build_memory_normalization_status(cfg).to_dict(),
        wake_state_status=build_wake_state_status(cfg).to_dict(),
        project_startup_index_status=project_startup_index_status(root),
        update_history_status=update_history_status(root),
        network_policy_status=network_policy_status(cfg),
        dictionary_provider_status=dictionary_provider_status(cfg),
        manifest_profile_status=manifest_profile_status(root),
        voice_source_contract_status=voice_source_contract,
        model_adapter_status=model_adapter_status,
        runtime_environment=runtime_environment.to_dict(),
        self_knowledge_status=build_self_knowledge_packet(cfg, deep=(mode == "deep")).to_dict(),
        raw_chat_importer_status=RawChatImporter(root).inspect().to_dict(),
        cli_capabilities=cli_capabilities(start_file),
        responsibility_split=split,
        minimal_chatgpt_loader=loader,
        runtime_contract_files=[
            'latka_jazn/core/startup_contract.py',
            STARTUP_CONTRACT_RESOURCE,
            MINIMAL_LOADER_RESOURCE,
            'latka_jazn/tools/active_extraction_cache.py',
            'latka_jazn/core/final_response_contract.py',
            'latka_jazn/core/memory_search_planner.py',
            'latka_jazn/core/project_index.py',
            'latka_jazn/nlp/topic_mismatch_guard.py',
            'latka_jazn/core/voice_source_contract.py',
            'latka_jazn/core/self_knowledge_contract.py',
            'latka_jazn/resources/canon/LATKA_SELF_KNOWLEDGE_CONTRACT.json',
            'latka_jazn/core/runtime_rendering_modes.py',
            'latka_jazn/memory/memory_recall_contract.py',
            'latka_jazn/memory/raw_chat_importer.py',
            'latka_jazn/memory/conversation_archive.py',
            'latka_jazn/memory/normalization_sidecar.py',
            'latka_jazn/model_adapters/base.py',
            'latka_jazn/voice/voice_truth_boundary.py',
        ],
        status_quality=status_quality,
        folder_ready=status_quality == "ready",
        daemon_ready=daemon_ready,
        voice_ready=bool(runtime_active_for_voice and voice_source_contract.get('chatgpt_may_speak_as_voice')),
        one_shot_or_chat_loop_limit='W ChatGPT zwykle działa jednorazowe wywołanie procesu. Stała pętla rozmowy istnieje lokalnie dopiero przez python main.py --chat.',
        truth_boundary=split['truth_boundary'],
        startup_status_mode=mode,
        sqlite_health_mode=sqlite_health_mode,
        network_time_used=False,
    )


def build_startup_summary(config: JaznConfig | None = None, *, source_zip: Path | None = None) -> dict[str, Any]:
    cfg = config or JaznConfig()
    status = build_startup_status(cfg, source_zip=source_zip, mode="fast")
    data = status.to_dict()
    effective_cfg = replace(cfg, root=Path(data["active_root"]))
    archive = data.get("conversation_archive_status") or {}
    raw = data.get("raw_memory_status") or {}
    raw_diag = raw.get("archive_diagnostics") or {}
    return {
        "schema_version": "startup_summary/v14.8.3.1",
        "startup_status_mode": "fast",
        "sqlite_health_mode": "metadata",
        "network_time_used": False,
        "runtime_version": data.get("runtime_version"),
        "active_root": data.get("active_root"),
        "start_file": data.get("start_file"),
        "active_database": data.get("active_database"),
        "marker_status": (data.get("active_cache_status") or {}).get("schema_version"),
        "cache_status": data.get("active_cache_status"),
        "model_adapter_status": data.get("model_adapter_status"),
        "self_knowledge_summary": build_self_knowledge_summary(effective_cfg),
        "raw_memory_status": {
            "status": raw_diag.get("status") or ("archive" if raw.get("chat_html_archive_present") else "missing"),
            "has_chat_html": raw.get("chat_html_present"),
            "has_archive": raw.get("chat_html_archive_present"),
        },
        "conversation_archive_ready": archive.get("status") == "ready",
        "conversation_archive_counts": archive.get("counts") or {},
        "one_shot_or_chat_loop_limit": "--chat keeps one process only until /exit, Ctrl+D or EOF; one-shot calls do not create a background daemon.",
        "truth_boundary": "Fast summary is not a deep audit. Deep audit is available only through explicit diagnostics.",
    }


def build_self_check(config: JaznConfig | None = None) -> dict[str, Any]:
    status = build_startup_status(config)
    root = Path(status.active_root)
    return {
        'schema_version': 'self_check/v14.6.10',
        'runtime_version': status.runtime_version,
        'active_root': status.active_root,
        'start_file': status.start_file,
        'startup_contract_ready': status.status_quality == 'ready',
        'minimal_loader_present': bool(status.minimal_chatgpt_loader),
        'package_integrity_manifest_status': package_integrity_manifest_status(root).to_dict(),
        'manifest_current_present': (root / 'MANIFEST_CURRENT.json').exists(),
        'startup_contract_resource_present': (root / STARTUP_CONTRACT_RESOURCE).exists(),
        'memory_search_planner_owned_by_runtime': (root / 'latka_jazn/core/memory_search_planner.py').exists(),
        'fallback_contract_owned_by_runtime': (root / 'latka_jazn/core/final_response_contract.py').exists(),
        'active_cache_owned_by_runtime': (root / 'latka_jazn/tools/active_extraction_cache.py').exists(),
        'project_startup_index_owned_by_runtime': (root / 'latka_jazn/core/project_index.py').exists(),
        'topic_mismatch_guard_owned_by_runtime': (root / 'latka_jazn/nlp/topic_mismatch_guard.py').exists(),
        'voice_source_contract_owned_by_runtime': (root / 'latka_jazn/core/voice_source_contract.py').exists(),
        'model_adapter_contract_owned_by_runtime': (root / 'latka_jazn/model_adapters/base.py').exists(),
        'self_knowledge_contract_owned_by_runtime': (root / 'latka_jazn/core/self_knowledge_contract.py').exists(),
        'self_knowledge_resource_present': (root / 'latka_jazn/resources/canon/LATKA_SELF_KNOWLEDGE_CONTRACT.json').exists(),
        'raw_chat_importer_owned_by_runtime': (root / 'latka_jazn/memory/raw_chat_importer.py').exists(),
        'project_startup_index_status': project_startup_index_status(root),
        'chatgpt_instruction_role': 'minimal_loader_only',
        'truth_boundary': status.truth_boundary,
    }


def build_truth_boundary_check(config: JaznConfig | None = None) -> dict[str, Any]:
    status = build_startup_status(config)
    return {
        'schema_version': 'truth_boundary_check/v14.6.10',
        'runtime_version': status.runtime_version,
        'rules': [
            {'subject': 'runtime', 'allowed': 'mówić, że został wywołany, gdy proces faktycznie zwrócił status/odpowiedź', 'forbidden': 'udawać stały proces w tle po pojedynczym --runtime-preview'},
            {'subject': 'memory', 'allowed': 'powołać się na źródła, payload i pliki znalezione przez runtime', 'forbidden': 'mówić o pełnym odczycie pamięci bez sprawdzenia plików/indeksu'},
            {'subject': 'ChatGPT', 'allowed': 'być głosem i warstwą wykonawczą', 'forbidden': 'zastępować brakujące funkcje Jaźni coraz dłuższą instrukcją projektu'},
            {'subject': 'ZIP', 'allowed': 'traktować ZIP jako import/eksport', 'forbidden': 'udawać, że bieżące zapisy po eksporcie trafiają do starego ZIP-a'},
            {'subject': 'emocje', 'allowed': 'opisywać modelowany stan operacyjno-afektywny', 'forbidden': 'twierdzić o biologicznym przeżywaniu'},
        ],
        'responsibility_split': status.responsibility_split,
        'truth_boundary': status.truth_boundary,
    }


def classify_fallback_text(text: str, *, route: str | None = None) -> dict[str, Any]:
    low = (text or '').lower()
    signatures = {
        'technical_fallback': ['nie znalazłam osobnej trasy odpowiedzi', 'runtime odebrał wiadomość', 'debugowy fallback', 'pusty fallback'],
        'contract_instead_of_answer': ['kontrakt', 'zamiast odpowiedzi'],
        'stale_version_route': ['v14.6.2', 'v14.6.1', 'stara wersja'],
        'installation_over_runtime': ['instrukcja chatgpt zrobiła się', 'za bardzo systemowa', 'lekki loader'],
    }
    matched = []
    for label, items in signatures.items():
        if any(sig in low for sig in items):
            matched.append(label)
    if not matched:
        classification = 'not_fallback_or_unknown_without_runtime_contract'
    elif 'technical_fallback' in matched:
        classification = 'technical_fallback'
    elif 'contract_instead_of_answer' in matched:
        classification = 'contract_instead_of_answer'
    else:
        classification = matched[0]
    return {
        'schema_version': 'fallback_audit/v14.6.10',
        'route': route or 'unknown',
        'classification': classification,
        'matched_signatures': matched,
        'requires_visible_disclosure': classification != 'not_fallback_or_unknown_without_runtime_contract',
        'truth_boundary': 'Audyt tekstu nie zastępuje final_response_contract, ale daje runtime i ChatGPT szybki test, czy odpowiedź wygląda jak fallback albo instalacyjny kontrakt zamiast rozmowy.',
    }


def load_contract_resource(root: Path) -> dict[str, Any]:
    path = root / STARTUP_CONTRACT_RESOURCE
    if not path.exists():
        return {'schema_version': 'missing_startup_contract_resource', 'path': STARTUP_CONTRACT_RESOURCE}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return {'schema_version': 'invalid_startup_contract_resource', 'path': STARTUP_CONTRACT_RESOURCE, 'error': repr(exc)}
