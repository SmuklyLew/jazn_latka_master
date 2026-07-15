from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json
import os

from latka_jazn.core.runtime_root import (
    active_runtime_marker_path,
    find_runtime_root,
    find_start_file,
)
from latka_jazn.version import PACKAGE_VERSION, version_number
from latka_jazn.core.timestamp_policy import (
    TIMESTAMP_LOCAL_FALLBACK_ALLOWED_DEFAULT,
    TIMESTAMP_NETWORK_FIRST_DEFAULT,
    TIMESTAMP_NETWORK_IN_NORMAL_TURN_DEFAULT,
    TIMESTAMP_TIMEZONE,
)

DEFAULT_MAX_SQLITE_FILE_BYTES = 480 * 1024 * 1024


def _default_runtime_root() -> Path:
    return find_runtime_root(Path(__file__))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "tak", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except Exception:
        return default


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return default


def _env_float_first(*names: str, default: float) -> float:
    try:
        return float(_env_first(*names))
    except Exception:
        return default


def _env_int_first(*names: str, default: int) -> int:
    try:
        return int(_env_first(*names))
    except Exception:
        return default

@dataclass(slots=True)
class JaznConfig:
    version: str = PACKAGE_VERSION
    root: Path = field(default_factory=_default_runtime_root)
    timezone: str = TIMESTAMP_TIMEZONE
    timestamp_format: str = "[🕒 %Y-%m-%d %H:%M:%S GMT%z, %A, Europe/Warsaw]"
    memory_db_name: str = field(default_factory=lambda: os.environ.get("JAZN_RUNTIME_MEMORY_DB", "memory/sqlite/runtime_write_v1/runtime_memory.sqlite3").strip())
    audit_db_name: str = field(default_factory=lambda: os.environ.get("JAZN_AUDIT_DB", "memory/sqlite/runtime_write_v1/runtime_audit.sqlite3").strip())
    conversation_archive_manifest_name: str = field(default_factory=lambda: os.environ.get("JAZN_CONVERSATION_ARCHIVE_MANIFEST", "memory/sqlite/conversation_archive_v1/conversation_archive_manifest.sqlite3").strip())
    conversation_fts_dir_name: str = field(default_factory=lambda: os.environ.get("JAZN_CONVERSATION_FTS_DIR", "memory/sqlite/conversation_fts_v1").strip())
    conversation_staging_dir_name: str = field(default_factory=lambda: os.environ.get("JAZN_CONVERSATION_STAGING_DIR", "memory/sqlite/staging_v1").strip())
    runtime_workspace_dir_name: str = field(default_factory=lambda: os.environ.get("JAZN_RUNTIME_WORKSPACE_DIR", "workspace_runtime").strip())
    conversation_shard_manifest_name: str = field(default_factory=lambda: os.environ.get("JAZN_CONVERSATION_SHARD_MANIFEST", "memory/sqlite/runtime_write_v1/runtime_memory_shards.json").strip())
    audit_shard_manifest_name: str = field(default_factory=lambda: os.environ.get("JAZN_AUDIT_SHARD_MANIFEST", "memory/sqlite/runtime_write_v1/runtime_audit_shards.json").strip())
    max_sqlite_file_bytes: int = field(default_factory=lambda: _env_int("JAZN_MAX_SQLITE_FILE_BYTES", DEFAULT_MAX_SQLITE_FILE_BYTES))
    canon_path: str = "latka_jazn/resources/canon/LATKA_IDENTITY_CANON.json"
    private_canon_override_path: str = "memory/raw/LATKA_IDENTITY_CANON.json"
    bootstrap_path: str = "memory/raw/LATKA_BOOTSTRAP_SYSTEM.txt"
    raw_memory_dir: str = "memory/raw"
    versioned_memory_dir: str = "memory/versioned_sources"
    require_first_person_identity: bool = True
    network_time_first: bool = field(default_factory=lambda: _env_bool("JAZN_NETWORK_TIME_FIRST", TIMESTAMP_NETWORK_FIRST_DEFAULT))
    local_time_fallback: bool = TIMESTAMP_LOCAL_FALLBACK_ALLOWED_DEFAULT
    startup_status_default_mode: str = field(default_factory=lambda: os.environ.get("JAZN_STARTUP_STATUS_MODE", "fast").strip().lower())
    sqlite_health_default_mode: str = field(default_factory=lambda: os.environ.get("JAZN_SQLITE_HEALTH_MODE", "metadata").strip().lower())
    turn_trace_enabled: bool = field(default_factory=lambda: _env_bool("JAZN_TURN_TRACE", False))
    network_time_allowed_in_normal_turn: bool = field(default_factory=lambda: _env_bool("JAZN_NETWORK_TIME_IN_TURN", TIMESTAMP_NETWORK_IN_NORMAL_TURN_DEFAULT))
    auto_import_raw_chat_html_on_bootstrap: bool = True
    raw_chat_html_auto_import_limit: int | None = None
    idle_reflection_thresholds: tuple[int, ...] = (300, 600, 21600)

    allow_network: bool = field(default_factory=lambda: _env_bool("JAZN_ALLOW_NETWORK", True))
    network_default_timeout_connect_seconds: float = field(default_factory=lambda: _env_float("JAZN_NETWORK_TIMEOUT_CONNECT", 3.0))
    network_default_timeout_read_seconds: float = field(default_factory=lambda: _env_float("JAZN_NETWORK_TIMEOUT_READ", 6.0))
    network_max_retries: int = field(default_factory=lambda: _env_int("JAZN_NETWORK_MAX_RETRIES", 1))
    network_user_agent: str = f"LatkaJazn/{version_number(PACKAGE_VERSION)}"
    network_cache_required: bool = True
    network_cache_ttl_seconds: int = 604800
    network_respect_robots_and_terms: bool = True

    dictionary_allow_network: bool = field(default_factory=lambda: _env_bool("JAZN_DICTIONARY_ALLOW_NETWORK", True))
    dictionary_network_cache_required: bool = True
    dictionary_online_lookup_timeout_seconds: float = field(default_factory=lambda: _env_float("JAZN_DICTIONARY_TIMEOUT", 4.0))
    dictionary_provider_order: tuple[str, ...] = (
        "local_cache", "local_mini_lexicon", "morfeusz_optional",
        "wiktionary_mediawiki_api", "sjp_reference", "wsjp_reference", "plwordnet_optional", "languagetool_optional",
    )
    lexical_resources_registry_path: str = "latka_jazn/resources/nlp/verified_sources.json"
    latka_project_lexicon_path: str = "latka_jazn/resources/nlp/latka_project_lexicon.json"
    lexical_resource_cache_name: str = field(default_factory=lambda: os.environ.get("JAZN_LEXICAL_RESOURCE_CACHE", "workspace_runtime/dictionary_cache.sqlite3").strip())
    lexical_resource_cache_ttl_seconds: int = field(default_factory=lambda: _env_int("JAZN_LEXICAL_RESOURCE_CACHE_TTL", 604800))
    lexical_resource_status_include_optional: bool = field(default_factory=lambda: _env_bool("JAZN_LEXICAL_STATUS_OPTIONAL", True))


    research_allow_network: bool = field(default_factory=lambda: _env_bool("JAZN_RESEARCH_ALLOW_NETWORK", True))
    research_requires_chatgpt_web_when_local_provider_missing: bool = True
    test_mode: bool = field(default_factory=lambda: _env_bool("JAZN_TEST_MODE", False))

    llm_route_mode: str = field(default_factory=lambda: os.environ.get("JAZN_LLM_ROUTE", "auto").strip().lower())
    allow_paid_openai_api: bool = field(default_factory=lambda: _env_bool("JAZN_ALLOW_PAID_OPENAI", False))
    openai_paid_model_name: str = field(default_factory=lambda: _env_first("JAZN_OPENAI_MODEL", "JAZN_MODEL_NAME", default="").strip())
    model_adapter: str = field(default_factory=lambda: os.environ.get("JAZN_MODEL_ADAPTER", "null").strip().lower())
    model_name: str = field(default_factory=lambda: _env_first("JAZN_OPENAI_MODEL", "JAZN_MODEL_NAME", default="").strip())
    model_api_base: str = field(default_factory=lambda: _env_first("JAZN_OPENAI_API_BASE", "JAZN_MODEL_API_BASE", default="https://api.openai.com/v1").strip().rstrip("/"))
    model_timeout_seconds: float = field(default_factory=lambda: _env_float("JAZN_MODEL_TIMEOUT", 45.0))
    model_max_output_tokens: int = field(default_factory=lambda: _env_int("JAZN_MODEL_MAX_OUTPUT_TOKENS", 800))
    runtime_turn_timeout_seconds: float = field(default_factory=lambda: _env_float("JAZN_RUNTIME_TURN_TIMEOUT_SECONDS", 45.0))
    terminal_model_name: str = field(default_factory=lambda: os.environ.get("JAZN_TERMINAL_MODEL_NAME", "terminal_visible_layer").strip())
    local_model_name: str = field(default_factory=lambda: _env_first("JAZN_LOCAL_LLM_MODEL", "JAZN_LOCAL_MODEL_NAME", default="").strip())
    local_model_api_base: str = field(default_factory=lambda: _env_first("JAZN_LOCAL_LLM_BASE_URL", "JAZN_LOCAL_LLM_API_BASE", "JAZN_LOCAL_MODEL_API_BASE", default="http://127.0.0.1:11434").strip().rstrip("/"))
    lm_studio_model_name: str = field(default_factory=lambda: _env_first("JAZN_LM_STUDIO_MODEL", "JAZN_LMSTUDIO_MODEL").strip())
    lm_studio_api_base: str = field(default_factory=lambda: _env_first("JAZN_LM_STUDIO_API_BASE", "JAZN_LMSTUDIO_API_BASE", default="http://127.0.0.1:1234/v1").strip().rstrip("/"))
    lm_studio_timeout_seconds: float = field(default_factory=lambda: _env_float_first("JAZN_LM_STUDIO_TIMEOUT", "JAZN_LMSTUDIO_TIMEOUT_SECONDS", default=45.0))
    lm_studio_max_output_tokens: int = field(default_factory=lambda: _env_int_first("JAZN_LM_STUDIO_MAX_OUTPUT_TOKENS", "JAZN_LMSTUDIO_MAX_OUTPUT_TOKENS", default=800))
    llama_cpp_model_name: str = field(default_factory=lambda: os.environ.get("JAZN_LLAMA_CPP_MODEL_NAME", "").strip())
    llama_cpp_model_api_base: str = field(default_factory=lambda: os.environ.get("JAZN_LLAMA_CPP_API_BASE", "http://127.0.0.1:8080/v1").strip().rstrip("/"))

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()

    def _path_under_runtime_root(self, relative: str | Path) -> Path:
        path = Path(relative)
        if path.is_absolute():
            raise ValueError(f"runtime path must be relative to runtime root: {path}")
        resolved = (self.root / path).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"runtime path escapes runtime root: {path}") from exc
        return resolved

    @property
    def runtime_workspace_dir(self) -> Path:
        return self._path_under_runtime_root(self.runtime_workspace_dir_name)

    @property
    def active_runtime_marker_path(self) -> Path:
        return active_runtime_marker_path(self.root)

    @property
    def package_integrity_manifest_path(self) -> Path:
        return self._path_under_runtime_root("PACKAGE_INTEGRITY_MANIFEST.json")

    @property
    def legacy_manifest_current_path(self) -> Path:
        return self._path_under_runtime_root("MANIFEST_CURRENT.json")

    @property
    def manifest_current_path(self) -> Path:
        # Compatibility property name only; it always points at the canonical manifest.
        return self.package_integrity_manifest_path

    @property
    def start_file_path(self) -> Path | None:
        return find_start_file(self.root)

    @property
    def conversation_archive_manifest_path(self) -> Path:
        return self.root / self.conversation_archive_manifest_name

    @property
    def conversation_fts_dir(self) -> Path:
        return self.root / self.conversation_fts_dir_name

    @property
    def conversation_staging_dir(self) -> Path:
        return self.root / self.conversation_staging_dir_name

    @property
    def lexical_resource_cache_path(self) -> Path:
        return self.root / self.lexical_resource_cache_name

    def _active_shard_path(self, manifest_name: str, logical_database: str, role: str, default_db_name: str) -> Path:
        try:
            from .db.shard_manifest import SQLiteShardManager
            return SQLiteShardManager(
                self.root,
                manifest_name,
                logical_database=logical_database,
                role=role,
                default_db_path=default_db_name,
                max_file_bytes=self.max_sqlite_file_bytes,
            ).rotate_if_needed()
        except Exception:
            return self.root / default_db_name

    def _active_shard_path_readonly(self, manifest_name: str, default_db_name: str) -> Path:
        """Resolve an active shard without creating, refreshing, or rotating it."""
        manifest_path = self.root / manifest_name
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            active_shard = str(data.get("active_write_shard") or "")
            for shard in data.get("shards") or []:
                if str(shard.get("shard_id") or "") == active_shard and shard.get("path"):
                    return self.root / str(shard["path"])
        except Exception:
            pass
        return self.root / default_db_name

    @property
    def memory_db_path(self) -> Path:
        return self._active_shard_path(
            self.conversation_shard_manifest_name,
            "chat_context",
            "canonical_runtime_conversation_memory",
            self.memory_db_name,
        )

    @property
    def memory_db_path_readonly(self) -> Path:
        return self._active_shard_path_readonly(
            self.conversation_shard_manifest_name,
            self.memory_db_name,
        )

    @property
    def audit_db_path(self) -> Path:
        return self._active_shard_path(
            self.audit_shard_manifest_name,
            "chat_context_audit",
            "canonical_realtime_audit",
            self.audit_db_name,
        )

    @property
    def audit_db_path_readonly(self) -> Path:
        return self._active_shard_path_readonly(
            self.audit_shard_manifest_name,
            self.audit_db_name,
        )

    @property
    def network_timeout(self) -> tuple[float, float]:
        return (self.network_default_timeout_connect_seconds, self.network_default_timeout_read_seconds)

    def resolve(self, rel: str) -> Path:
        return self._path_under_runtime_root(rel)
