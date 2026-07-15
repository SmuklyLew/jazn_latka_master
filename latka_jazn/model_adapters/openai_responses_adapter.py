from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .adapter_contract import AdapterContract, describe_with_contract
from .base import AdapterStatusSnapshot, ModelAdapterRequest, ModelAdapterResponse
from .openai_state_tracker import OpenAIStateTracker


DEFAULT_EXECUTION_INSTRUCTIONS = (
    "Jesteś zewnętrzną warstwą językową i rozumującą systemu Jaźni Łatki. "
    "Tożsamość, pamięć, bieżący stan, czas, uprawnienia i granice prawdy pochodzą wyłącznie "
    "z kontekstu runtime. Nie dopisuj wspomnień ani wykonanych działań. Model może jedynie "
    "zażądać wywołania narzędzia; narzędzie wykonuje runtime po autoryzacji, a wynik musi wrócić "
    "do kolejnej tury. Nie ujawniaj ukrytego toku rozumowania. Zwróć naturalną odpowiedź po polsku."
)


class OpenaiResponsesAdapter:
    name = "openai_responses_adapter"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "",
        api_base: str = "https://api.openai.com/v1",
        timeout_seconds: float = 45.0,
        max_output_tokens: int = 800,
        root: str | Path | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.model = str(model or "").strip()
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.state_tracker = OpenAIStateTracker(Path(root)) if root is not None else None
        self._last_generation_succeeded = False
        self._last_probe_error: str | None = None

    def describe(self) -> dict[str, Any]:
        configured = bool(self.api_key and self.model)
        failure_reason = None
        if not self.api_key:
            failure_reason = "openai_api_key_missing"
        elif not self.model:
            failure_reason = "model_name_missing"
        contract = AdapterContract(
            adapter_id=self.name,
            provider="openai",
            kind="remote_responses_api",
            available=configured,
            model_name=self.model or None,
            endpoint=self.api_base,
            can_generate_model_guided_speech=bool(configured and self._last_generation_succeeded),
            configured=configured,
            endpoint_reachable=True if self._last_generation_succeeded else None,
            probe_state="probed_ok" if self._last_generation_succeeded else "not_probed",
            last_probe_error=self._last_probe_error,
            can_attempt_model_guided_speech=configured,
            failure_reason=failure_reason,
            requires_api_key=True,
            truth_boundary=(
                "OpenAI Responses API jest zewnętrznym backendem generowania. Nie jest źródłem "
                "tożsamości ani pamięci Jaźni. Żądanie funkcji jest niewykonanym planem narzędzia, "
                "dopóki runtime nie autoryzuje, nie wykona i nie zweryfikuje wyniku."
            ),
        )
        return describe_with_contract(
            contract=contract,
            legacy={
                "name": self.name,
                "status": "configured" if configured else "not_configured",
                "model": self.model or "not_configured",
                "api_base": self.api_base,
                "state_tracker": "enabled" if self.state_tracker else "disabled",
                "supports_responses_api": True,
                "supports_tool_requests": True,
                "supports_structured_output": True,
                "conversation_state_contract": (
                    "previous_response_id zapewnia ciągłość transportu Responses API, nie tożsamość ani pamięć runtime"
                ),
            },
        )

    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        if not self.api_key or not self.model:
            return self._response(status="not_configured")
        payload = self._build_payload(request)
        state, session_id = self._load_state_for_request(request)
        if request.previous_response_id:
            payload["previous_response_id"] = request.previous_response_id
        elif state and state.previous_response_id:
            payload["previous_response_id"] = state.previous_response_id
        try:
            data = self._post_json(payload)
            text = str(data.get("output_text") or "").strip() or self._extract_output_text(data)
            tool_calls = self._extract_tool_calls(data)
            structured = self._parse_structured_output(text, request.response_schema)
            state_payload = None
            if self.state_tracker and session_id and data.get("id"):
                state_payload = self.state_tracker.update_from_response(
                    session_id=session_id,
                    response=data,
                    store_policy=False,
                ).to_dict()
            self._last_generation_succeeded = bool(text or tool_calls)
            status = "completed" if text else ("tool_calls_pending_authorization" if tool_calls else str(data.get("status") or "empty_output"))
            return self._response(
                text=text,
                status=status,
                model=str(data.get("model") or self.model),
                sources=[{"response_id": data.get("id"), "status": data.get("status"), "openai_state": state_payload}],
                usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
                tool_calls=tool_calls,
                structured_output=structured,
                continuation_id=str(data.get("id") or "") or None,
                reasoning_metadata={"effort_requested": request.reasoning_effort, "hidden_reasoning_exposed": False},
            )
        except HTTPError as exc:
            self._last_probe_error = f"http_error_{exc.code}"
            return self._response(status=self._last_probe_error)
        except OSError as exc:
            self._last_probe_error = self._status_from_error(str(exc))
            return self._response(status=self._last_probe_error)
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError):
            self._last_probe_error = "adapter_error"
            return self._response(status="adapter_error")

    def _build_payload(self, request: ModelAdapterRequest) -> dict[str, Any]:
        context = json.dumps(request.system_context or {}, ensure_ascii=False, separators=(",", ":"))
        payload: dict[str, Any] = {
            "model": self.model,
            "store": False,
            "max_output_tokens": request.max_output_tokens or self.max_output_tokens,
            "instructions": request.instructions or DEFAULT_EXECUTION_INSTRUCTIONS,
            "input": f"{request.prompt}\n\nKONTEKST_JAZNI_JSON:\n{context}",
            "metadata": {"runtime_adapter": self.name, **{str(k): str(v) for k, v in request.metadata.items() if v is not None}},
        }
        if request.reasoning_effort:
            payload["reasoning"] = {"effort": str(request.reasoning_effort)}
        if request.response_schema:
            schema = dict(request.response_schema)
            name = str(schema.pop("name", "jazn_structured_response"))
            payload["text"] = {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}}
        if request.tools:
            payload["tools"] = list(request.tools)
            payload["tool_choice"] = request.tool_choice or "auto"
            payload["parallel_tool_calls"] = bool(request.parallel_tool_calls)
        return payload

    @staticmethod
    def _extract_tool_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or str(item.get("type") or "") not in {"function_call", "tool_call"}:
                continue
            raw_arguments = item.get("arguments")
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else (raw_arguments or {})
            except json.JSONDecodeError:
                arguments = {"_raw": raw_arguments, "_parse_error": True}
            calls.append({
                "call_id": item.get("call_id") or item.get("id"),
                "name": item.get("name"),
                "arguments": arguments,
                "executed": False,
                "authorized": False,
                "source": "openai_responses_api",
            })
        return calls

    @staticmethod
    def _parse_structured_output(text: str, schema: dict[str, Any] | None) -> dict[str, Any] | list[Any] | None:
        if not schema or not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, (dict, list)) else None

    def _status_snapshot(self) -> AdapterStatusSnapshot:
        configured = bool(self.api_key and self.model)
        return AdapterStatusSnapshot(
            adapter_id=self.name,
            provider="openai",
            configured=configured,
            endpoint_reachable=True if self._last_generation_succeeded else None,
            probe_state="probed_ok" if self._last_generation_succeeded else ("not_probed" if configured else "not_configured"),
            last_probe_error=self._last_probe_error,
            can_attempt_model_guided_speech=configured,
            can_generate_model_guided_speech=bool(configured and self._last_generation_succeeded),
            capabilities={"responses": True, "tool_requests": True, "structured_output": True},
        )

    def _response(
        self,
        *,
        status: str,
        text: str = "",
        model: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        usage: dict[str, Any] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        structured_output: dict[str, Any] | list[Any] | None = None,
        continuation_id: str | None = None,
        reasoning_metadata: dict[str, Any] | None = None,
    ) -> ModelAdapterResponse:
        return ModelAdapterResponse(
            text=text,
            provider="openai",
            model=model or self.model or "not_configured",
            status=status,
            sources=sources or [],
            adapter_id=self.name,
            endpoint_used="/responses" if status not in {"not_configured"} else None,
            status_snapshot=self._status_snapshot(),
            transport={"api_base": self.api_base, "store": False},
            usage=usage,
            tool_calls=tool_calls or [],
            structured_output=structured_output,
            continuation_id=continuation_id,
            reasoning_metadata=reasoning_metadata or {},
            requires_runtime_authorization=bool(tool_calls),
        )

    def _load_state_for_request(self, request: ModelAdapterRequest):
        context = request.system_context or {}
        client_context = context.get("client_context") if isinstance(context.get("client_context"), dict) else {}
        session_id = str(request.session_id or client_context.get("session_id") or context.get("session_id") or "").strip() or None
        if not self.state_tracker or not session_id:
            return None, session_id
        return self.state_tracker.load(session_id), session_id

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        if os.name == "nt":
            return self._post_json_windows(payload)
        req = Request(
            f"{self.api_base}/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("OpenAI response must be a JSON object")
        return data

    def _post_json_windows(self, payload: dict[str, Any]) -> dict[str, Any]:
        script = (
            "$ErrorActionPreference='Stop';"
            "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;"
            "[Console]::InputEncoding=[Text.UTF8Encoding]::new($false);"
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
            "$body=[Console]::In.ReadToEnd();"
            "$headers=@{Authorization=('Bearer '+$env:OPENAI_API_KEY)};"
            "try{$r=Invoke-RestMethod -Uri $env:JAZN_ADAPTER_API_URL -Method Post -Headers $headers "
            "-ContentType 'application/json; charset=utf-8' -Body $body -TimeoutSec ([int]$env:JAZN_ADAPTER_TIMEOUT);"
            "$r|ConvertTo-Json -Depth 100 -Compress}"
            "catch{if($_.ErrorDetails.Message){[Console]::Error.Write($_.ErrorDetails.Message)}"
            "else{[Console]::Error.Write($_.Exception.Message)};exit 1}"
        )
        env = dict(os.environ)
        env["OPENAI_API_KEY"] = self.api_key
        env["JAZN_ADAPTER_API_URL"] = f"{self.api_base}/responses"
        env["JAZN_ADAPTER_TIMEOUT"] = str(max(1, int(self.timeout_seconds)))
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=self.timeout_seconds + 10,
            check=False,
        )
        if completed.returncode != 0:
            raise OSError((completed.stderr or "PowerShell HTTP transport failed").strip())
        return json.loads(completed.stdout)

    @staticmethod
    def _status_from_error(message: str) -> str:
        low = (message or "").lower()
        if "insufficient_quota" in low or "current quota" in low:
            return "insufficient_quota"
        if "invalid_api_key" in low or "incorrect api key" in low or "(401)" in low:
            return "authentication_error"
        if "(429)" in low or "rate_limit" in low:
            return "rate_limited"
        return "adapter_error"

    @staticmethod
    def _extract_output_text(data: dict[str, Any]) -> str:
        parts: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or str(item.get("type") or "") in {"reasoning", "function_call", "tool_call"}:
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict) or "reasoning" in str(content.get("type") or ""):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
