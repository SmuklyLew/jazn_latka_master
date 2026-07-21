from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

import main as main_module
from latka_jazn.config import JaznConfig
from latka_jazn.core.cli_normalization import normalize_cli_argv
from latka_jazn.core.llm_route_resolver import (
    ROUTE_CHATGPT_BRIDGE,
    ROUTE_LOCAL,
    ROUTE_NULL,
    apply_llm_route_to_config,
    build_llm_route_status,
    probe_ollama,
)
from latka_jazn.core.runtime_daemon import build_daemon_start_command
from latka_jazn.core.runtime_environment import (
    CHATGPT_ADAPTER,
    NULL_ADAPTER,
    OLLAMA_ADAPTER,
    TERMINAL_ADAPTER,
    detect_runtime_environment,
)
from latka_jazn.model_adapters.base import ModelAdapterRequest
from latka_jazn.model_adapters.local_llm_adapter import LocalLlmAdapter


class _FakeStream(StringIO):
    def __init__(self, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


class _Response:
    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._data


def _ollama_probe(*, available: bool = True) -> dict:
    return {
        "provider": "ollama",
        "adapter": OLLAMA_ADAPTER,
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3:8b" if available else "",
        "configured_model": "",
        "running_models": ["qwen3:8b"] if available else [],
        "installed_models": ["qwen3:8b"] if available else [],
        "available": available,
        "endpoint_reachable": available,
        "reason": "single_running_model" if available else "ollama_unavailable",
        "errors": [] if available else ["/api/ps:unavailable", "/api/tags:unavailable"],
    }


def test_cli_flags_are_case_insensitive_but_message_and_values_are_preserved() -> None:
    parser = main_module._build_parser()
    argv = normalize_cli_argv(
        ["--CHATGPT", "--SESSION-ID", "DomA", "--", "Czy --STATUS działa?"],
        parser,
    )
    assert argv == ["--chat-gpt", "--session-id", "DomA", "--", "Czy --STATUS działa?"]
    ns = parser.parse_args(argv)
    assert ns.chat_gpt is True
    assert ns.session_id == "DomA"
    assert ns.message == ["--", "Czy --STATUS działa?"]


def test_main_parser_exposes_operator_compatibility_flags() -> None:
    parser = main_module._build_parser()
    ns = parser.parse_args(["--doctor"])
    assert ns.doctor is True
    ns = parser.parse_args(["--package-smoke", "--package-profile", "system"])
    assert ns.package_smoke is True
    assert ns.package_profile == "system"
    ns = parser.parse_args(["--ollama"])
    assert ns.local_llm is True
    ns = parser.parse_args(["--daemon-status", "--daemon-snapshot"])
    assert ns.daemon_snapshot is True


def test_universal_chat_prefers_confirmed_chatgpt_host(monkeypatch, tmp_path: Path) -> None:
    cfg = JaznConfig(root=tmp_path)
    monkeypatch.setattr("latka_jazn.core.llm_route_resolver.probe_ollama", lambda *_a, **_k: _ollama_probe())
    status = build_llm_route_status(
        cfg,
        command="--chat",
        env={"JAZN_VISIBLE_CHANNEL": "chatgpt"},
        infer_host_environment=True,
    )
    assert status.selected_route == ROUTE_CHATGPT_BRIDGE
    assert status.selected_adapter == CHATGPT_ADAPTER


def test_universal_chat_terminal_keeps_ollama_as_backend(tmp_path: Path) -> None:
    cfg = JaznConfig(root=tmp_path, model_adapter=OLLAMA_ADAPTER, local_model_name="qwen3:8b")
    environment = detect_runtime_environment(
        cfg,
        command="--chat",
        env={},
        stdin=_FakeStream(True),
        stdout=_FakeStream(True),
        infer_host_environment=False,
    )
    assert environment.visible_channel_adapter == TERMINAL_ADAPTER
    assert environment.effective_runtime_adapter == OLLAMA_ADAPTER
    assert environment.is_terminal_chat_loop is True


def test_ollama_discovery_prefers_single_running_model(monkeypatch, tmp_path: Path) -> None:
    # Keep this discovery test independent from a developer's real Ollama
    # environment. probe_ollama receives env={} below, so the config fixture
    # must not retain JAZN_OLLAMA_MODEL loaded by JaznConfig defaults.
    cfg = JaznConfig(root=tmp_path, local_model_name="")

    def fake_urlopen(request, timeout):
        if str(request.full_url).endswith("/api/ps"):
            return _Response({"models": [{"model": "qwen3:8b"}]})
        return _Response({"models": [{"name": "qwen3:8b"}, {"name": "gemma3:4b"}]})

    monkeypatch.setattr("latka_jazn.core.llm_route_resolver.urlopen", fake_urlopen)
    result = probe_ollama(cfg, {})
    assert result["available"] is True
    assert result["model"] == "qwen3:8b"
    assert result["reason"] == "single_running_model"


def test_universal_chat_selects_ollama_when_host_is_absent(monkeypatch, tmp_path: Path) -> None:
    cfg = JaznConfig(root=tmp_path)
    monkeypatch.setattr("latka_jazn.core.llm_route_resolver.probe_ollama", lambda *_a, **_k: _ollama_probe())
    status = build_llm_route_status(cfg, command="--chat", env={}, infer_host_environment=False)
    assert status.selected_route == ROUTE_LOCAL
    apply_llm_route_to_config(cfg, status)
    assert cfg.model_adapter == OLLAMA_ADAPTER
    assert cfg.local_model_name == "qwen3:8b"


def test_universal_chat_uses_truthful_null_fallback(monkeypatch, tmp_path: Path) -> None:
    cfg = JaznConfig(root=tmp_path)
    monkeypatch.setattr("latka_jazn.core.llm_route_resolver.probe_ollama", lambda *_a, **_k: _ollama_probe(available=False))
    status = build_llm_route_status(cfg, command="--chat", env={}, infer_host_environment=False)
    assert status.selected_route == ROUTE_NULL
    assert status.selected_adapter == NULL_ADAPTER


def test_ollama_adapter_uses_native_chat_endpoint(monkeypatch) -> None:
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response({"message": {"role": "assistant", "content": "Cześć"}, "done": True})

    monkeypatch.setattr("latka_jazn.model_adapters.local_llm_adapter.urlopen", fake_urlopen)
    adapter = LocalLlmAdapter(model="qwen3:8b")
    response = adapter.generate(ModelAdapterRequest(prompt="Hej", system_context={"route": "ordinary"}))
    assert captured["url"].endswith("/api/chat")
    assert [item["role"] for item in captured["payload"]["messages"]] == ["system", "user"]
    assert response.text == "Cześć"
    assert response.endpoint_used == "/api/chat"


def test_daemon_technical_process_always_starts_through_main_py(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "run.py").write_text("raise SystemExit(2)\n", encoding="utf-8")
    command = build_daemon_start_command(tmp_path)
    assert Path(command[1]).name == "main.py"
    assert "--daemon-run" in command
