from __future__ import annotations

import os
from pathlib import Path

from .adapter_contract import AdapterContract, describe_with_contract
from .base import ModelAdapterRequest, ModelAdapterResponse


class TerminalRuntimeAdapter:
    """Truthful adapter status for the local ``--chat`` terminal loop.

    ``--chat`` is a host-visible terminal conversation channel.  It is not the
    offline null fallback, but it also is not a local language model.  This
    adapter makes that boundary explicit without pretending that terminal I/O can
    generate model-guided speech.
    """

    name = "terminal_runtime_adapter"

    def __init__(self, *, model: str | None = None, root: str | Path | None = None) -> None:
        self.model = (model or os.environ.get("JAZN_TERMINAL_MODEL_NAME") or "terminal_visible_layer").strip()
        self.root = Path(root) if root is not None else None

    def describe(self) -> dict:
        contract = AdapterContract(
            adapter_id=self.name,
            provider="terminal_host",
            kind="local_terminal_chat_bridge",
            available=True,
            model_name=self.model or "terminal_visible_layer",
            endpoint=None,
            can_generate_model_guided_speech=False,
            configured=True,
            endpoint_reachable=None,
            probe_state="not_probed",
            can_attempt_model_guided_speech=False,
            failure_reason="terminal_channel_not_model_guided_generation",
            requires_api_key=False,
            availability_basis="explicit_chat_terminal_command",
            backend_only=True,
            truth_boundary=(
                "--chat oznacza stałą lokalną pętlę terminalową jednego procesu runtime. "
                "To nie jest null_model_adapter, ale też nie jest lokalny model językowy; "
                "odpowiedź powstaje w runtime Jaźni i widzialnej warstwie terminala."
            ),
        )
        return describe_with_contract(
            contract=contract,
            legacy={
                "name": self.name,
                "status": "terminal_bridge_available",
                "model": self.model or "terminal_visible_layer",
                "api_base": None,
                "host_runtime": "local_terminal_visible_layer",
                "conversation_state_contract": (
                    "ciągłość --chat trwa tylko w bieżącym procesie terminalowym do /exit, EOF albo przerwania"
                ),
            },
        )

    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        return ModelAdapterResponse(
            text="",
            provider="terminal_host",
            model=self.model or "terminal_visible_layer",
            status="requires_runtime_terminal_visible_response",
            truth_boundary=(
                "Ten adapter opisuje kanał terminalowy --chat. Nie wykonuje lokalnego wywołania modelu; "
                "widzialna odpowiedź jest produktem runtime Jaźni i pętli terminalowej."
            ),
        )
