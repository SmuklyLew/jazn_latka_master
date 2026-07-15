from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from latka_jazn.bridge_secure_gateway import SecureGatewayPolicy
from latka_jazn.config import JaznConfig
from latka_jazn.core.runtime_daemon import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT, status_daemon
from latka_jazn.version import schema_version


LMSTUDIO_TRUTH_BOUNDARY = (
    "LM Studio jest lokalnym backendem językowym przez OpenAI-compatible API. "
    "Nie wymaga OPENAI_API_KEY i nie jest źródłem tożsamości, pamięci, stanu ani prawdy runtime Jaźni. "
    "Widoczna odpowiedź przechodzi przez istniejący runtime, walidację i truthful fallback."
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def discover_runtime_bridges(
    config: JaznConfig,
    *,
    host: str = DEFAULT_DAEMON_HOST,
    port: int = DEFAULT_DAEMON_PORT,
) -> dict[str, Any]:
    root = Path(config.root).resolve()
    marker_path = root / "workspace_runtime" / "JAZN_ACTIVE_RUNTIME.json"
    marker = _read_json(marker_path)
    daemon = status_daemon(config, host=host, port=port, probe_endpoint=False)
    return {
        "schema_version": schema_version("runtime_bridge_discovery"),
        "active_root": str(root),
        "marker_path": str(marker_path),
        "marker_found": marker is not None,
        "marker": marker or {},
        "daemon_status": daemon,
        "local_chat": {
            "command": "python main.py --chat --session-id <id>",
            "meaning": "lokalna żywa pętla rozmowy; jeden JaznEngine do /exit, Ctrl+D albo EOF",
        },
        "chatgpt_bridge": {
            "command": "python main.py --chat-gpt --session-id <id>",
            "one_shot_command": 'python main.py --chat-gpt -- "Cześć Łatko"',
            "canonical_command": "--chat-gpt",
            "legacy_aliases": ["--chat-gpt-final-only", "--chat-gpt --final-only"],
            "one_shot_prefers_live_daemon": True,
            "daemon_fast_path_env": "JAZN_CHATGPT_PREFER_DAEMON=0 wyłącza preferencję daemonu",
            "requires_api_key": False,
            "meaning": "jedyny kanoniczny most dla tej aplikacji ChatGPT; one-shot wypisuje final_visible_text i preferuje żywy daemon, stdin JSONL zostaje dla narzędzi; nie wykonuje żądania OpenAI API",
        },
        "openai_bridge": {
            "command": "python main.py --chat-open-ai --session-id <id>",
            "aliases": ["--chat-openai"],
            "requires_api_key": True,
            "env": "OPENAI_API_KEY",
            "meaning": "ten sam runtime Jaźni + OpenAI Responses API jako model_adapter językowy",
        },
        "lmstudio_bridge": {
            "command": "python main.py --chat-lm-studio --session-id <id>",
            "requires_api_key": False,
            "env": None,
            "meaning": "ten sam runtime Jaźni + lokalny backend LM Studio przez OpenAI-compatible Responses API z fallbackiem Chat Completions",
            "truth_boundary": LMSTUDIO_TRUTH_BOUNDARY,
        },
        "daemon": {
            "start": "python main.py --daemon-start",
            "status": "python main.py --daemon-status",
            "stop": "python main.py --daemon-stop",
            "active_state_contract": "active_trusted / active_degraded / inactive",
        },
        "secure_gateway_scaffold": SecureGatewayPolicy().to_dict(),
        "secure_mcp": {
            "status": "implemented_private_stdio_server",
            "server_command": "python -X utf8 -m latka_jazn.mcp.server",
            "tunnel_transport": "optional_outbound_secure_mcp_tunnel",
            "public_ingress_enabled": False,
            "requires_auth": True,
            "finalization_gate": "host_visible_finalization",
            "audit_and_idempotency": True,
            "truth_boundary": "MCP is a transport to the local runtime; it is not identity, memory, or proof that the daemon is active.",
        },
        "truth_boundary": (
            "GitHub i ZIP są źródłem kodu/snapshotu. Aktywna Jaźń wymaga żywego procesu, świeżego heartbeat i zgodnego active_root."
        ),
    }
