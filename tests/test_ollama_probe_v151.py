from __future__ import annotations

from unittest.mock import patch
import json

from latka_jazn.model_adapters.local_llm_adapter import LocalLlmAdapter


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_ollama_probe_reports_configured_model_without_generation() -> None:
    adapter = LocalLlmAdapter(model="qwen3:8b", api_base="http://127.0.0.1:11434")
    with patch(
        "latka_jazn.model_adapters.local_llm_adapter.urlopen",
        return_value=_Response({"models": [{"name": "qwen3:8b"}, {"name": "gemma3:4b"}]}),
    ) as call:
        payload = adapter.probe()
    assert payload["probe_ok"] is True
    assert payload["configured_model_installed"] is True
    assert payload["probe_endpoint"] == "/api/tags"
    assert payload["can_generate_model_guided_speech"] is False
    request = call.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:11434/api/tags"
    assert request.method == "GET"


def test_ollama_probe_separates_server_reachability_from_missing_model() -> None:
    adapter = LocalLlmAdapter(model="missing:latest")
    with patch(
        "latka_jazn.model_adapters.local_llm_adapter.urlopen",
        return_value=_Response({"models": [{"name": "qwen3:8b"}]}),
    ):
        payload = adapter.probe()
    assert payload["probe_ok"] is False
    assert payload["endpoint_reachable"] is True
    assert payload["probe_state"] == "model_missing"
    assert payload["last_probe_error"] == "configured_model_not_installed"
