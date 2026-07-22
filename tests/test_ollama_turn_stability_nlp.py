from __future__ import annotations

import io
import json
from unittest.mock import patch

from latka_jazn.config import JaznConfig
from latka_jazn.core.handlers.capability_status_handler import CapabilityStatusHandler
from latka_jazn.core.route_registry import RouteRegistry
from latka_jazn.model_adapters.base import ModelAdapterRequest
from latka_jazn.model_adapters.local_llm_adapter import LocalLlmAdapter
from latka_jazn.nlp.dialogue_intent_classifier import DialogueIntentClassifier
from latka_jazn.nlp.response_language_guard import assess_response_language


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def _ollama_payload(text: str, *, model: str = "gemma3:4b", reason: str = "stop") -> FakeResponse:
    return FakeResponse(json.dumps({
        "model": model,
        "message": {"role": "assistant", "content": text},
        "done": True,
        "done_reason": reason,
        "prompt_eval_count": 12,
        "eval_count": 8,
    }).encode("utf-8"))


def test_language_guard_rejects_clear_english_and_accepts_polish() -> None:
    assert assess_response_language("Okay, that is great to hear. What do you want to do next?").accepted_for_polish is False
    assert assess_response_language("Rozumiem. Możemy spokojnie kontynuować rozmowę po polsku.").accepted_for_polish is True


def test_ollama_retries_once_after_english_candidate_and_keeps_actual_model() -> None:
    adapter = LocalLlmAdapter(model="gemma3", api_base="http://127.0.0.1:11434")
    with patch("latka_jazn.model_adapters.local_llm_adapter.urlopen", side_effect=[
        _ollama_payload("Okay, that is great to hear. What do you want next?"),
        _ollama_payload("Rozumiem. Możemy kontynuować po polsku.", model="gemma3:4b"),
    ]) as mocked:
        response = adapter.generate(ModelAdapterRequest(prompt="Rozumiem."))
    assert mocked.call_count == 2
    assert response.status == "completed"
    assert response.provider == "ollama"
    assert response.adapter_id == "local_llm_adapter"
    assert response.model == "gemma3:4b"
    assert response.transport["retry_count"] == 1
    assert response.transport["attempts"][-1]["done_reason"] == "stop"


def test_ollama_blocks_second_english_candidate() -> None:
    adapter = LocalLlmAdapter(model="gemma3")
    with patch("latka_jazn.model_adapters.local_llm_adapter.urlopen", side_effect=[
        _ollama_payload("Okay, that is great to hear. What do you want next?"),
        _ollama_payload("This answer is still entirely in English and should be rejected."),
    ]):
        response = adapter.generate(ModelAdapterRequest(prompt="Rozumiem."))
    assert response.status == "language_mismatch"
    assert response.text == ""
    assert response.transport["retry_count"] == 1


def test_model_status_question_has_direct_route_and_factual_handler() -> None:
    report = DialogueIntentClassifier().classify("Jaki masz teraz dostępny model?")
    assert report.primary_intent == "model_adapter_status_question"
    entry = RouteRegistry().resolve(report.primary_intent)
    assert entry.route == "model_adapter_status"
    result = CapabilityStatusHandler().handle(
        "Jaki masz teraz dostępny model?",
        {
            "intent": report.primary_intent,
            "config": JaznConfig(),
            "model_adapter_status": {
                "provider": "ollama",
                "adapter_id": "local_llm_adapter",
                "model": "gemma3:4b",
                "endpoint": "http://127.0.0.1:11434",
                "configured": True,
                "endpoint_reachable": True,
                "probe_state": "probed_ok",
                "last_probe_error": None,
            },
            "required_components": entry.required_components,
        },
    )
    assert result.route == "model_adapter_status"
    assert "provider=ollama" in result.body
    assert "model=gemma3:4b" in result.body
    assert "endpoint_reachable=True" in result.body


def test_runtime_source_wording_is_host_neutral() -> None:
    from latka_jazn.core.runtime_response_synthesizer import RuntimeResponseSynthesizer

    result = RuntimeResponseSynthesizer().synthesize(
        user_text="Skąd pochodzi ta odpowiedź?",
        detected_intent="runtime_source_question",
        original_body="",
        route="runtime_source",
    )
    assert "interpretacji ChatGPT" not in result.body
    assert "zewnętrznej warstwy językowej" in result.body
