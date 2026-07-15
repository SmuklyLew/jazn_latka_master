from __future__ import annotations

import os
from pathlib import Path

from .adapter_contract import AdapterContract, describe_with_contract
from .base import ModelAdapterRequest, ModelAdapterResponse


class ChatgptRuntimeAdapter:
    """Truthful adapter status for the ChatGPT-hosted bridge.

    The local Python runtime cannot call the surrounding ChatGPT conversation as
    an in-process function.  Still, when the user explicitly runs ``--chat-gpt``,
    the language channel is the ChatGPT host rather than the offline null
    fallback.  This adapter makes that distinction visible without pretending
    that local code performed a model call.
    """

    name = "chatgpt_runtime_adapter"

    def __init__(self, *, model: str | None = None, root: str | Path | None = None) -> None:
        self.model = (model or os.environ.get("JAZN_CHATGPT_MODEL_NAME") or "chatgpt_host_model").strip()
        self.root = Path(root) if root is not None else None

    def describe(self) -> dict:
        contract = AdapterContract(
            adapter_id=self.name,
            provider="chatgpt_host",
            kind="hosted_chatgpt_bridge",
            available=True,
            model_name=self.model or "chatgpt_host_model",
            endpoint=None,
            can_generate_model_guided_speech=False,
            configured=True,
            endpoint_reachable=None,
            probe_state="not_probed",
            can_attempt_model_guided_speech=False,
            failure_reason="host_chatgpt_bridge_requires_external_visible_reply",
            requires_api_key=False,
            availability_basis="explicit_chat_gpt_bridge_command",
            backend_only=True,
            truth_boundary=(
                "--chat-gpt oznacza, że widzialną warstwą językową jest host ChatGPT/copy-paste/JSONL. "
                "Lokalny runtime nie może sam wywołać modelu ChatGPT jak funkcji ani użyć go bez zewnętrznej odpowiedzi hosta, "
                "więc adapter raportuje kanał ChatGPT zamiast null_model_adapter, ale nie udaje lokalnego model-guided generation."
            ),
        )
        return describe_with_contract(
            contract=contract,
            legacy={
                "name": self.name,
                "status": "host_bridge_available",
                "model": self.model or "chatgpt_host_model",
                "api_base": None,
                "host_runtime": "chatgpt_visible_layer",
                "host_visible_generation_required": True,
                "host_must_generate_visible_reply": True,
                "host_bridge_phase": "host_visible_generation_requested",
                "conversation_state_contract": (
                    "ciągłość odpowiedzi hosta ChatGPT jest kanałem rozmowy, nie dowodem samodzielnej pamięci lokalnego procesu"
                ),
            },
        )

    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        return ModelAdapterResponse(
            text="",
            provider="chatgpt_host",
            model=self.model or "chatgpt_host_model",
            status="requires_host_chatgpt_visible_response",
            truth_boundary=(
                "Ten adapter opisuje dostępny kanał ChatGPT w trybie --chat-gpt. "
                "Nie wykonuje lokalnego wywołania modelu; właściwa odpowiedź modelu powstaje w warstwie hosta ChatGPT."
            ),
        )
