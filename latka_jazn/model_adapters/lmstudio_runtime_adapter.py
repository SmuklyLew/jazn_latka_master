from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .adapter_contract import AdapterContract, describe_with_contract
from .base import AdapterStatusSnapshot, ModelAdapterRequest, ModelAdapterResponse


LMSTUDIO_TRUTH_BOUNDARY = (
    "LM Studio jest lokalnym backendem językowym przez OpenAI-compatible API. "
    "Nie wymaga OPENAI_API_KEY i nie jest źródłem tożsamości, pamięci, stanu ani prawdy runtime Jaźni. "
    "Widoczna odpowiedź przechodzi przez istniejący runtime, walidację i truthful fallback."
)

LMSTUDIO_SYSTEM_PROMPT = (
    "Jesteś językową warstwą wykonawczą systemu Jaźni Łatki. Tożsamość, pamięć, stan, "
    "ograniczenia i prawda runtime pochodzą wyłącznie z przekazanego kontekstu. "
    "Odpowiadaj po polsku, naturalnie i krótko. Nie dodawaj timestampu. "
    "Nie ujawniaj rozumowania ani reasoning_content. Zwróć wyłącznie treść widocznej odpowiedzi."
)


class LmStudioRuntimeAdapter:
    name = "lmstudio_runtime_adapter"

    def __init__(
        self,
        *,
        model: str = "",
        api_base: str = "http://127.0.0.1:1234/v1",
        timeout_seconds: float = 45.0,
        max_output_tokens: int = 800,
        root: object | None = None,
    ) -> None:
        self.model = model.strip()
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self._endpoint_reachable: bool | None = None
        self._probe_state = "not_probed" if self.model else "not_configured"
        self._last_probe_error: str | None = None
        self._last_generation_succeeded = False
        self.api_token = os.environ.get("JAZN_LMSTUDIO_API_KEY") or os.environ.get("LM_API_TOKEN") or ""
        self._model_capabilities: dict[str, Any] = {}

    def describe(self) -> dict[str, Any]:
        configured = bool(self.model)
        contract = AdapterContract(
            adapter_id=self.name,
            provider="lmstudio",
            kind="openai_compatible_local_api",
            available=configured,
            model_name=self.model or None,
            endpoint=self.api_base,
            can_generate_model_guided_speech=bool(configured and (self._probe_state == "probed_ok" or self._last_generation_succeeded)),
            configured=configured,
            endpoint_reachable=self._endpoint_reachable,
            probe_state=self._probe_state,
            last_probe_error=self._last_probe_error,
            can_attempt_model_guided_speech=configured,
            failure_reason=None if configured else "lmstudio_model_name_missing",
            requires_api_key=False,
            truth_boundary=LMSTUDIO_TRUTH_BOUNDARY,
        )
        return describe_with_contract(
            contract=contract,
            legacy={
                "name": self.name,
                "status": "configured" if configured else "not_configured",
                "model": self.model or "not_configured",
                "api_base": self.api_base,
                "preferred_endpoint": "/responses",
                "fallback_endpoint": "/chat/completions",
                "capability_discovery_endpoint": "/api/v1/models",
                "optional_credential_env": "JAZN_LMSTUDIO_API_KEY / LM_API_TOKEN",
                "model_capabilities": dict(self._model_capabilities),
            },
        )

    def discover_model_capabilities(self) -> dict[str, Any]:
        """Discover capabilities from LM Studio native v1 model listing.

        Discovery is explicit to avoid hidden network calls in ordinary status.
        The result controls reasoning/tool parameters; unknown capability never
        becomes an implicit permission.
        """
        base = self.api_base
        if base.endswith("/v1"):
            base = base[:-3]
        request = Request(f"{base}/api/v1/models", headers=self._headers(), method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            self._last_probe_error = f"capability_discovery_failed:{type(exc).__name__}"
            return {}
        models = payload.get("models") if isinstance(payload, dict) else None
        for item in models or []:
            if not isinstance(item, dict):
                continue
            keys = {str(item.get("key") or ""), str(item.get("id") or ""), str(item.get("display_name") or "")}
            if self.model not in keys:
                continue
            caps = item.get("capabilities")
            capabilities: dict[str, Any] = {}
            if isinstance(caps, list):
                capabilities["capabilities"] = [str(value) for value in caps]
                capabilities["trained_for_tool_use"] = "tool_use" in capabilities["capabilities"]
            elif isinstance(caps, dict):
                capabilities.update(caps)
            reasoning = item.get("reasoning")
            if isinstance(reasoning, dict):
                capabilities["reasoning"] = reasoning
            capabilities.setdefault("trained_for_tool_use", bool(item.get("trained_for_tool_use")))
            self._model_capabilities = capabilities
            return dict(capabilities)
        self._last_probe_error = "model_not_found_in_capability_listing"
        return {}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def probe(self) -> AdapterStatusSnapshot:
        if not self.model:
            self._probe_state = "not_configured"
            return self._status_snapshot()
        request = ModelAdapterRequest(prompt="Odpowiedz: OK")
        for endpoint in ("/responses", "/chat/completions"):
            data, error = self._try_post(endpoint, self._probe_payload(endpoint, request))
            if error is None:
                text = self._extract_responses_text(data) if endpoint == "/responses" else self._extract_chat_text(data)[0]
                if text:
                    self._endpoint_reachable = True
                    self._probe_state = "probed_ok"
                    self._last_probe_error = None
                    return self._status_snapshot()
            self._last_probe_error = error or "probe_empty_output"
        self._endpoint_reachable = False
        self._probe_state = "probed_fail"
        return self._status_snapshot()
    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        if not self.model:
            return self._response(status="not_configured")

        attempts: list[dict[str, Any]] = []
        responses_payload = {
            "model": self.model,
            "stream": False,
            "instructions": request.instructions or LMSTUDIO_SYSTEM_PROMPT,
            "input": self._user_input(request),
            "max_output_tokens": request.max_output_tokens or self.max_output_tokens,
        }
        self._apply_optional_capabilities(responses_payload, request)
        data, error = self._try_post("/responses", responses_payload)
        if error is not None:
            attempts.append({"endpoint": "/responses", "status": error})
        else:
            text = self._extract_responses_text(data)
            truncated = self._responses_truncated(data)
            attempts.append(
                {"endpoint": "/responses", "status": "completed" if text else "empty", "response_truncated": truncated}
            )
            tool_calls = self._extract_responses_tool_calls(data)
            if text or tool_calls:
                self._mark_generation_success()
                return self._response(
                    text=text,
                    status="completed" if text else "tool_calls_pending_authorization",
                    model=str(data.get("model") or self.model),
                    sources=attempts,
                    endpoint_used="/responses",
                    tool_calls=tool_calls,
                    continuation_id=str(data.get("id") or "") or None,
                    usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
                    reasoning_metadata={"effort_requested": request.reasoning_effort, "hidden_reasoning_exposed": False},
                )

        chat_payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": request.instructions or LMSTUDIO_SYSTEM_PROMPT},
                {"role": "user", "content": self._user_input(request)},
            ],
            "max_tokens": request.max_output_tokens or self.max_output_tokens,
        }
        self._apply_optional_capabilities(chat_payload, request)
        data, error = self._try_post("/chat/completions", chat_payload)
        if error is not None:
            attempts.append({"endpoint": "/chat/completions", "status": error})
            return self._response(status="lmstudio_provider_unavailable", sources=attempts)

        text, truncated = self._extract_chat_text(data)
        tool_calls = self._extract_chat_tool_calls(data)
        attempts.append(
            {"endpoint": "/chat/completions", "status": "completed" if (text or tool_calls) else "empty", "response_truncated": truncated}
        )
        if not text and not tool_calls:
            return self._response(status="lmstudio_response_empty", sources=attempts)
        self._mark_generation_success()
        return self._response(
            text=text,
            status="completed" if text else "tool_calls_pending_authorization",
            model=str(data.get("model") or self.model),
            sources=attempts,
            endpoint_used="/chat/completions",
            tool_calls=tool_calls,
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
            reasoning_metadata={"effort_requested": request.reasoning_effort, "hidden_reasoning_exposed": False},
        )

    def _apply_optional_capabilities(self, payload: dict[str, Any], request: ModelAdapterRequest) -> None:
        capabilities = self._model_capabilities
        reasoning = capabilities.get("reasoning") if isinstance(capabilities.get("reasoning"), dict) else {}
        allowed = reasoning.get("allowed_options") if isinstance(reasoning, dict) else None
        if request.reasoning_effort and isinstance(allowed, list) and request.reasoning_effort in allowed:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        trained_for_tools = bool(capabilities.get("trained_for_tool_use"))
        if request.tools and trained_for_tools:
            payload["tools"] = list(request.tools)
            payload["tool_choice"] = request.tool_choice or "auto"
            payload["parallel_tool_calls"] = bool(request.parallel_tool_calls)
        if request.response_schema:
            schema = dict(request.response_schema)
            name = str(schema.pop("name", "jazn_structured_response"))
            payload["text"] = {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}}

    @staticmethod
    def _parse_arguments(raw: Any) -> Any:
        if not isinstance(raw, str):
            return raw or {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw, "_parse_error": True}

    @classmethod
    def _extract_responses_tool_calls(cls, data: dict[str, Any]) -> list[dict[str, Any]]:
        calls = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or str(item.get("type") or "") not in {"function_call", "tool_call"}:
                continue
            calls.append({
                "call_id": item.get("call_id") or item.get("id"),
                "name": item.get("name"),
                "arguments": cls._parse_arguments(item.get("arguments")),
                "executed": False,
                "authorized": False,
                "source": "lmstudio_responses_api",
            })
        return calls

    @classmethod
    def _extract_chat_tool_calls(cls, data: dict[str, Any]) -> list[dict[str, Any]]:
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return []
        message = choices[0].get("message") if isinstance(choices[0].get("message"), dict) else {}
        calls = []
        for item in message.get("tool_calls") or []:
            if not isinstance(item, dict):
                continue
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            calls.append({
                "call_id": item.get("id"),
                "name": function.get("name"),
                "arguments": cls._parse_arguments(function.get("arguments")),
                "executed": False,
                "authorized": False,
                "source": "lmstudio_chat_completions",
            })
        return calls

    def _probe_payload(self, endpoint: str, request: ModelAdapterRequest) -> dict[str, Any]:
        if endpoint == "/responses":
            return {"model": self.model, "stream": False, "instructions": LMSTUDIO_SYSTEM_PROMPT, "input": request.prompt, "max_output_tokens": 8}
        return {"model": self.model, "stream": False, "messages": [{"role": "system", "content": LMSTUDIO_SYSTEM_PROMPT}, {"role": "user", "content": request.prompt}], "max_tokens": 8}

    def _mark_generation_success(self) -> None:
        self._endpoint_reachable = True
        self._probe_state = "probed_ok"
        self._last_probe_error = None
        self._last_generation_succeeded = True

    def _try_post(self, endpoint: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        try:
            return self._post_json(endpoint, payload), None
        except HTTPError as exc:
            return {}, f"http_error_{exc.code}"
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            return {}, "provider_unavailable"

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self.api_base}{endpoint}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("LM Studio response must be a JSON object")
        return data

    @staticmethod
    def _extract_responses_text(data: dict[str, Any]) -> str:
        direct = data.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        parts: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or LmStudioRuntimeAdapter._is_reasoning(item):
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict) or LmStudioRuntimeAdapter._is_reasoning(content):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()

    @staticmethod
    def _extract_chat_text(data: dict[str, Any]) -> tuple[str, bool]:
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return "", False
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = message.get("content")
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content.strip())
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or LmStudioRuntimeAdapter._is_reasoning(item):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(part for part in parts if part).strip(), choice.get("finish_reason") == "length"

    @staticmethod
    def _responses_truncated(data: dict[str, Any]) -> bool:
        details = data.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        return str(data.get("status") or "").lower() == "incomplete" or reason in {
            "max_output_tokens",
            "length",
        }

    @staticmethod
    def _is_reasoning(payload: dict[str, Any]) -> bool:
        kind = str(payload.get("type") or payload.get("role") or "").lower()
        return "reasoning" in kind or "chain_of_thought" in kind

    @staticmethod
    def _user_input(request: ModelAdapterRequest) -> str:
        context = json.dumps(request.system_context or {}, ensure_ascii=False, separators=(",", ":"))
        return f"{request.prompt}\n\nKONTEKST_JAZNI_JSON:\n{context}"

    def _response(
        self,
        *,
        status: str,
        text: str = "",
        model: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        endpoint_used: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        continuation_id: str | None = None,
        usage: dict[str, Any] | None = None,
        reasoning_metadata: dict[str, Any] | None = None,
    ) -> ModelAdapterResponse:
        return ModelAdapterResponse(
            text=text,
            provider="lmstudio",
            model=model or self.model or "not_configured",
            status=status,
            sources=sources or [],
            adapter_id=self.name,
            source_origin="model_adapter",
            endpoint_used=endpoint_used,
            status_snapshot=self._status_snapshot(),
            transport={"api_base": self.api_base, "endpoint_used": endpoint_used, "authentication": "optional_token" if self.api_token else "none"},
            usage=usage,
            tool_calls=tool_calls or [],
            continuation_id=continuation_id,
            reasoning_metadata=reasoning_metadata or {},
            requires_runtime_authorization=bool(tool_calls),
            truth_boundary=LMSTUDIO_TRUTH_BOUNDARY,
        )

    def _status_snapshot(self) -> AdapterStatusSnapshot:
        configured = bool(self.model)
        return AdapterStatusSnapshot(
            adapter_id=self.name,
            provider="lmstudio",
            configured=configured,
            endpoint_reachable=self._endpoint_reachable,
            probe_state=self._probe_state if configured else "not_configured",
            last_probe_error=self._last_probe_error,
            can_attempt_model_guided_speech=configured,
            can_generate_model_guided_speech=bool(configured and (self._probe_state == "probed_ok" or self._last_generation_succeeded)),
            capabilities=dict(self._model_capabilities),
        )
