from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .adapter_contract import AdapterContract, describe_with_contract
from .base import AdapterStatusSnapshot, ModelAdapterRequest, ModelAdapterResponse


class LocalLlmAdapter:
    name = "local_llm_adapter"

    def __init__(
        self,
        *,
        model: str = "",
        api_base: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 45.0,
        max_output_tokens: int = 800,
        root: object | None = None,
    ) -> None:
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self._endpoint_reachable: bool | None = None
        self._probe_state = "not_probed" if self.model else "not_configured"
        self._last_probe_error: str | None = None
        self._last_generation_succeeded = False

    def describe(self) -> dict:
        configured = bool(self.model)
        contract = AdapterContract(
            adapter_id=self.name,
            provider="ollama",
            kind="local_generate_api",
            available=configured,
            model_name=self.model or None,
            endpoint=self.api_base,
            can_generate_model_guided_speech=bool(configured and self._last_generation_succeeded),
            configured=configured,
            endpoint_reachable=self._endpoint_reachable,
            probe_state=self._probe_state,
            last_probe_error=self._last_probe_error,
            can_attempt_model_guided_speech=configured,
            failure_reason=None if configured else "local_model_name_missing",
            truth_boundary=(
                "Ollama jest lokalnym backendem językowym. Dostępność oznacza kompletną konfigurację, "
                "nie wynik live probe; Jaźń zachowuje tożsamość, pamięć, routing i walidację."
            ),
        )
        return describe_with_contract(
            contract=contract,
            legacy={
                "name": self.name,
                "status": "configured" if configured else "not_configured",
                "model": self.model or "not_configured",
                "api_base": self.api_base,
            },
        )

    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        if not self.model:
            return ModelAdapterResponse(
                text="",
                provider=self.name,
                model="not_configured",
                status="not_configured",
            )
        system_text = (
            "Jesteś językową warstwą wykonawczą Jaźni Łatki. Odpowiadaj po polsku i naturalnie. "
            "Nie wymyślaj pamięci ani faktów poza przekazanym kontekstem."
            "\n\nKONTEKST_JAZNI_JSON:\n"
            + json.dumps(request.system_context or {}, ensure_ascii=False)
        )
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": request.prompt},
            ],
            "options": {"num_predict": self.max_output_tokens},
        }
        try:
            req = Request(
                f"{self.api_base}/api/chat",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            text = str(message.get("content") or data.get("response") or "").strip()
            if text:
                self._endpoint_reachable = True
                self._probe_state = "probed_ok"
                self._last_generation_succeeded = True
                self._last_probe_error = None
            return ModelAdapterResponse(
                text=text,
                provider=self.name,
                model=self.model,
                status="completed" if text else "empty_output",
                adapter_id=self.name,
                endpoint_used="/api/chat",
                status_snapshot=self._status_snapshot(),
            )
        except HTTPError as exc:
            self._endpoint_reachable = False
            self._probe_state = "probed_fail"
            self._last_probe_error = f"http_error_{exc.code}"
            return ModelAdapterResponse(
                text="",
                provider=self.name,
                model=self.model,
                status=f"http_error_{exc.code}",
            )
        except (URLError, TimeoutError, OSError, ValueError):
            self._endpoint_reachable = False
            self._probe_state = "probed_fail"
            self._last_probe_error = "local_provider_unavailable"
            return ModelAdapterResponse(
                text="",
                provider=self.name,
                model=self.model,
                status="local_provider_unavailable",
            )

    def _status_snapshot(self) -> AdapterStatusSnapshot:
        configured = bool(self.model)
        return AdapterStatusSnapshot(
            adapter_id=self.name,
            provider="ollama",
            configured=configured,
            endpoint_reachable=self._endpoint_reachable,
            probe_state=self._probe_state if configured else "not_configured",
            last_probe_error=self._last_probe_error,
            can_attempt_model_guided_speech=configured,
            can_generate_model_guided_speech=bool(configured and self._last_generation_succeeded),
        )
