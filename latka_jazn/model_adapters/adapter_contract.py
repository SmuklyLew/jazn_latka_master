from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from latka_jazn.version import schema_version

from .base import ModelAdapterRequest, ModelAdapterResponse


@dataclass(slots=True)
class AdapterContract:
    adapter_id: str
    provider: str
    kind: str
    available: bool
    model_name: str | None
    endpoint: str | None
    can_generate_model_guided_speech: bool
    truth_boundary: str
    configured: bool | None = None
    endpoint_reachable: bool | None = None
    probe_state: str = "not_probed"
    last_probe_error: str | None = None
    can_attempt_model_guided_speech: bool | None = None
    validated: bool = False
    failure_reason: str | None = None
    requires_api_key: bool = False
    availability_basis: str = "configuration_only_no_live_probe"
    backend_only: bool = True
    schema_version: str = schema_version("model_adapter_contract")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        configured = self.available if self.configured is None else bool(self.configured)
        payload["configured"] = configured
        payload["can_attempt_model_guided_speech"] = (
            configured
            if self.can_attempt_model_guided_speech is None
            else bool(self.can_attempt_model_guided_speech)
        )
        if not configured:
            payload["probe_state"] = "not_configured"
        elif self.probe_state in {"not_configured", "configured"}:
            payload["probe_state"] = "not_probed"
        return payload


def describe_with_contract(*, contract: AdapterContract, legacy: dict[str, Any]) -> dict[str, Any]:
    payload = dict(legacy)
    contract_payload = contract.to_dict()
    payload.update(contract_payload)
    payload["adapter_contract"] = contract_payload
    payload.setdefault("endpoint_reachable", contract_payload["endpoint_reachable"])
    payload.setdefault("probe_state", contract_payload["probe_state"])
    payload.setdefault("last_probe_error", contract_payload["last_probe_error"])
    payload.setdefault("configured", contract_payload["configured"])
    payload.setdefault(
        "can_attempt_model_guided_speech",
        contract_payload["can_attempt_model_guided_speech"],
    )
    payload.setdefault("validated", contract_payload["validated"])
    return payload


def backend_config_skeletons(config: Any) -> list[dict[str, Any]]:
    return [
        {
            "adapter_id": "openai_responses_adapter",
            "provider": "openai",
            "kind": "remote_responses_api",
            "implemented": True,
            "selection": "JAZN_MODEL_ADAPTER=openai",
            "model_env": "JAZN_MODEL_NAME",
            "endpoint_env": "JAZN_MODEL_API_BASE",
            "credential_env": "OPENAI_API_KEY",
            "model_name": str(getattr(config, "model_name", "") or "") or None,
            "endpoint": str(getattr(config, "model_api_base", "") or "") or None,
            "normal_runtime_requires_credential": False,
        },
        {
            "adapter_id": "ollama_adapter",
            "provider": "ollama",
            "kind": "local_generate_api",
            "implemented": True,
            "selection": "JAZN_MODEL_ADAPTER=ollama",
            "model_env": "JAZN_LOCAL_MODEL_NAME",
            "endpoint_env": "JAZN_LOCAL_MODEL_API_BASE",
            "credential_env": None,
            "model_name": str(getattr(config, "local_model_name", "") or "") or None,
            "endpoint": str(getattr(config, "local_model_api_base", "") or "") or None,
            "normal_runtime_requires_credential": False,
        },
        {
            "adapter_id": "llama_cpp_openai_compatible_adapter",
            "provider": "llama_cpp",
            "kind": "openai_compatible_local_api_skeleton",
            "implemented": True,
            "selection": "JAZN_MODEL_ADAPTER=llama_cpp",
            "model_env": "JAZN_LLAMA_CPP_MODEL_NAME",
            "endpoint_env": "JAZN_LLAMA_CPP_API_BASE",
            "credential_env": None,
            "model_name": str(getattr(config, "llama_cpp_model_name", "") or "") or None,
            "endpoint": str(getattr(config, "llama_cpp_model_api_base", "") or "") or None,
            "normal_runtime_requires_credential": False,
        },
        {
            "adapter_id": "lmstudio_runtime_adapter",
            "provider": "lmstudio",
            "kind": "openai_compatible_local_api",
            "implemented": True,
            "selection": "JAZN_MODEL_ADAPTER=lmstudio",
            "model_env": "JAZN_LM_STUDIO_MODEL",
            "endpoint_env": "JAZN_LM_STUDIO_API_BASE",
            "credential_env": None,
            "optional_credential_env": "JAZN_LMSTUDIO_API_KEY / LM_API_TOKEN",
            "model_name": str(getattr(config, "lm_studio_model_name", "") or "") or None,
            "endpoint": str(getattr(config, "lm_studio_api_base", "") or "") or None,
            "normal_runtime_requires_credential": False,
        },
        {
            "adapter_id": "codex_development_adapter",
            "provider": "codex",
            "kind": "development_tooling_status",
            "implemented": False,
            "selection": "JAZN_MODEL_ADAPTER=codex_development_adapter",
            "model_env": None,
            "endpoint_env": None,
            "credential_env": None,
            "model_name": None,
            "endpoint": None,
            "normal_runtime_requires_credential": False,
        },
    ]


class ContractOnlyModelAdapter:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        endpoint: str,
        adapter_id: str | None = None,
        kind: str = "openai_compatible_local_api_skeleton",
        failure_reason: str = "backend_adapter_not_implemented",
        response_status: str = "backend_contract_only_not_implemented",
        truth_boundary: str | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.endpoint = endpoint
        self.name = adapter_id or f"{provider}_contract_only"
        self.kind = kind
        self.failure_reason = failure_reason
        self.response_status = response_status
        self.truth_boundary = truth_boundary or (
            "This is only a backend configuration skeleton. Runtime does not call this endpoint "
            "and does not present the backend as Jazn identity or memory."
        )

    def describe(self) -> dict[str, Any]:
        contract = AdapterContract(
            adapter_id=self.name,
            provider=self.provider,
            kind=self.kind,
            available=False,
            model_name=self.model or None,
            endpoint=self.endpoint or None,
            can_generate_model_guided_speech=False,
            failure_reason=self.failure_reason,
            truth_boundary=self.truth_boundary,
        )
        return describe_with_contract(
            contract=contract,
            legacy={
                "name": self.name,
                "status": "contract_only_not_implemented",
                "model": self.model or "not_configured",
                "api_base": self.endpoint,
            },
        )

    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        return ModelAdapterResponse(
            text="",
            provider=self.provider,
            model=self.model or "not_configured",
            status=self.response_status,
        )
