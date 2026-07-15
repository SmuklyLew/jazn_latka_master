from __future__ import annotations

from .adapter_contract import AdapterContract, describe_with_contract
from .base import ModelAdapterRequest, ModelAdapterResponse


class NullModelAdapter:
    name = "null_model_adapter"

    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        return ModelAdapterResponse(
            text="",
            provider="none",
            model="none",
            status="requires_external_model_execution",
        )

    def describe(self) -> dict:
        contract = AdapterContract(
            adapter_id=self.name,
            provider="none",
            kind="null_adapter",
            available=True,
            model_name=None,
            endpoint=None,
            can_generate_model_guided_speech=False,
            configured=False,
            endpoint_reachable=None,
            probe_state="not_configured",
            can_attempt_model_guided_speech=False,
            failure_reason="external_model_not_configured",
            availability_basis="built_in_truthful_fallback",
            truth_boundary=(
                "Null adapter jest dostępnym, prawdomównym fallbackiem bez modelu generacyjnego. "
                "Nie wykonuje żądań sieciowych, nie wymaga klucza API i nie może generować model-guided speech."
            ),
        )
        return describe_with_contract(
            contract=contract,
            legacy={
                "name": self.name,
                "status": "available_as_truthful_fallback",
            },
        )
