from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .adapter_contract import AdapterContract, describe_with_contract
from .base import AdapterStatusSnapshot, ModelAdapterRequest, ModelAdapterResponse


SYSTEM_PROMPT = (
    "Jesteś wykonawczą warstwą językową runtime Jaźni Łatki. Nie jesteś źródłem "
    "tożsamości, pamięci, czasu ani prawdy. Używaj wyłącznie przekazanego kontekstu, "
    "nie ujawniaj ukrytego rozumowania i zwróć tylko kandydata odpowiedzi."
)


class OpenAICompatibleLocalAdapter:
    """Shared transport for local/external OpenAI-compatible model backends."""

    name = "openai_compatible_local_adapter"

    def __init__(
        self,
        *,
        model: str = "",
        api_base: str = "http://127.0.0.1:8080/v1",
        provider: str = "openai_compatible",
        api_key: str = "",
        timeout_seconds: float = 45.0,
        max_output_tokens: int = 800,
        adapter_id: str | None = None,
        root: object | None = None,
    ) -> None:
        self.model = model.strip()
        self.api_base = api_base.rstrip("/")
        self.provider = provider.strip().lower() or "openai_compatible"
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.name = adapter_id or f"{self.provider}_openai_compatible_adapter"
        self._endpoint_reachable: bool | None = None
        self._probe_state = "not_probed" if self.model else "not_configured"
        self._last_probe_error: str | None = None
        self._last_generation_succeeded = False
        self._responses_live_probed = False

    def describe(self) -> dict[str, Any]:
        configured = bool(self.model and self.api_base)
        snapshot = self._status_snapshot()
        contract = AdapterContract(
            adapter_id=self.name,
            provider=self.provider,
            kind="openai_compatible_local_or_external_api",
            available=configured,
            model_name=self.model or None,
            endpoint=self.api_base or None,
            can_generate_model_guided_speech=snapshot.can_generate_model_guided_speech,
            truth_boundary=(
                "Backend OpenAI-compatible generuje wyłącznie kandydata. Runtime zachowuje "
                "routing, walidację, provenance, ledger i wyłączne prawo ustawienia final_visible_text."
            ),
            configured=configured,
            endpoint_reachable=snapshot.endpoint_reachable,
            probe_state=snapshot.probe_state,
            last_probe_error=snapshot.last_probe_error,
            can_attempt_model_guided_speech=configured,
            validated=False,
            failure_reason=None if configured else "model_or_api_base_missing",
            requires_api_key=False,
        )
        order = self.endpoint_order()
        return describe_with_contract(
            contract=contract,
            legacy={
                "name": self.name,
                "status": "configured" if configured else "not_configured",
                "model": self.model or "not_configured",
                "api_base": self.api_base,
                "preferred_endpoint": order[0] if order else None,
                "fallback_endpoints": order[1:],
            },
        )

    def endpoint_order(self) -> list[str]:
        if self.provider == "llama_cpp":
            return (["/responses", "/chat/completions"] if self._responses_live_probed else ["/chat/completions"])
        if self.provider == "ollama":
            return ["/responses", "/chat/completions", "/api/generate"]
        return ["/responses", "/chat/completions"]

    def probe(self, *, endpoint: str | None = None) -> AdapterStatusSnapshot:
        if not self.model or not self.api_base:
            self._probe_state = "not_configured"
            return self._status_snapshot()
        target = endpoint or self.endpoint_order()[0]
        payload = self._payload_for(target, ModelAdapterRequest(prompt="Odpowiedz: OK"), probe=True)
        try:
            data = self._post_json(target, payload)
            text = self._extract_text(target, data)
            if not text:
                raise ValueError("probe_empty_output")
            self._endpoint_reachable = True
            self._probe_state = "probed_ok"
            self._last_probe_error = None
            if target == "/responses":
                self._responses_live_probed = True
        except Exception as exc:
            self._endpoint_reachable = False
            self._probe_state = "probed_fail"
            self._last_probe_error = self._error_status(exc)
        return self._status_snapshot()

    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        if not self.model or not self.api_base:
            return self._response(status="not_configured")
        attempts: list[dict[str, Any]] = []
        for endpoint in self.endpoint_order():
            try:
                data = self._post_json(endpoint, self._payload_for(endpoint, request))
                self._endpoint_reachable = True
                text = self._extract_text(endpoint, data)
                attempts.append({"endpoint": endpoint, "status": "completed" if text else "empty"})
                if not text:
                    continue
                self._last_generation_succeeded = True
                self._probe_state = "probed_ok"
                self._last_probe_error = None
                if endpoint == "/responses":
                    self._responses_live_probed = True
                return self._response(
                    text=text,
                    status="completed",
                    model=str(data.get("model") or self.model),
                    endpoint_used=endpoint,
                    sources=attempts,
                    usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
                )
            except Exception as exc:
                error = self._error_status(exc)
                attempts.append({"endpoint": endpoint, "status": error})
                self._last_probe_error = error
        self._endpoint_reachable = False
        self._probe_state = "probed_fail"
        return self._response(status=f"{self.provider}_provider_unavailable", sources=attempts)

    def _payload_for(self, endpoint: str, request: ModelAdapterRequest, *, probe: bool = False) -> dict[str, Any]:
        instructions = request.instructions or SYSTEM_PROMPT
        prompt = request.prompt
        if request.system_context:
            prompt += "\n\nKONTEKST_JAZNI_JSON:\n" + json.dumps(request.system_context, ensure_ascii=False)
        token_limit = 8 if probe else self.max_output_tokens
        if endpoint == "/responses":
            return {"model": self.model, "stream": False, "instructions": instructions, "input": prompt, "max_output_tokens": token_limit}
        if endpoint == "/chat/completions":
            return {"model": self.model, "stream": False, "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": prompt}], "max_tokens": token_limit}
        return {"model": self.model, "stream": False, "system": instructions, "prompt": prompt, "options": {"num_predict": token_limit}}

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(f"{self.api_base}{endpoint}", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("provider_response_not_object")
        return data

    @staticmethod
    def _extract_text(endpoint: str, data: dict[str, Any]) -> str:
        if endpoint == "/responses":
            direct = data.get("output_text")
            if isinstance(direct, str) and direct.strip():
                return direct.strip()
            parts: list[str] = []
            for item in data.get("output") or []:
                if not isinstance(item, dict) or "reasoning" in str(item.get("type") or "").lower():
                    continue
                for content in item.get("content") or []:
                    if not isinstance(content, dict) or "reasoning" in str(content.get("type") or "").lower():
                        continue
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts)
        if endpoint == "/chat/completions":
            choices = data.get("choices") or []
            message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
            return str((message or {}).get("content") or "").strip()
        return str(data.get("response") or "").strip()

    @staticmethod
    def _error_status(exc: Exception) -> str:
        if isinstance(exc, HTTPError):
            return f"http_error_{exc.code}"
        if isinstance(exc, (URLError, TimeoutError, OSError)):
            return "provider_unavailable"
        return str(exc) or exc.__class__.__name__

    def _status_snapshot(self) -> AdapterStatusSnapshot:
        configured = bool(self.model and self.api_base)
        return AdapterStatusSnapshot(
            adapter_id=self.name,
            provider=self.provider,
            configured=configured,
            endpoint_reachable=self._endpoint_reachable,
            probe_state=self._probe_state if configured else "not_configured",
            last_probe_error=self._last_probe_error,
            can_attempt_model_guided_speech=configured,
            can_generate_model_guided_speech=bool(configured and (self._probe_state == "probed_ok" or self._last_generation_succeeded)),
            validated=False,
        )

    def _response(
        self,
        *,
        status: str,
        text: str = "",
        model: str | None = None,
        endpoint_used: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> ModelAdapterResponse:
        return ModelAdapterResponse(
            text=text,
            provider=self.provider,
            model=model or self.model or "not_configured",
            status=status,
            sources=sources or [],
            adapter_id=self.name,
            source_origin="model_adapter",
            endpoint_used=endpoint_used,
            status_snapshot=self._status_snapshot(),
            transport={"api_base": self.api_base, "endpoint_used": endpoint_used},
            usage=usage,
        )
