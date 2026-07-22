from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .adapter_contract import AdapterContract, describe_with_contract
from .base import AdapterStatusSnapshot, ModelAdapterRequest, ModelAdapterResponse
from latka_jazn.nlp.response_language_guard import assess_response_language, user_explicitly_requested_non_polish


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
        self.model = str(model or "").strip()
        self.api_base = str(api_base or "http://127.0.0.1:11434").rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self._endpoint_reachable: bool | None = None
        self._probe_state = "not_probed" if self.model else "not_configured"
        self._last_probe_error: str | None = None
        self._last_generation_succeeded = False
        self._last_generation: dict[str, Any] = {}

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
                "last_generation": dict(self._last_generation),
            },
        )

    def probe(self) -> dict:
        if not self.model:
            self._endpoint_reachable = None
            self._probe_state = "not_configured"
            self._last_probe_error = "local_model_name_missing"
            return self.describe()
        try:
            req = Request(f"{self.api_base}/api/tags", method="GET")
            with urlopen(req, timeout=min(self.timeout_seconds, 8.0)) as response:
                data = json.loads(response.read().decode("utf-8"))
            models = data.get("models") if isinstance(data, dict) else []
            names = sorted({
                str(item.get("name") or item.get("model") or "").strip()
                for item in (models if isinstance(models, list) else [])
                if isinstance(item, dict)
            } - {""})
            installed = self.model in names or any(name.split(":", 1)[0] == self.model.split(":", 1)[0] for name in names)
            self._endpoint_reachable = True
            self._probe_state = "probed_ok" if installed else "model_missing"
            self._last_probe_error = None if installed else "configured_model_not_installed"
            payload = self.describe()
            payload.update({
                "probe_ok": installed,
                "configured_model_installed": installed,
                "installed_models": names,
                "probe_endpoint": "/api/tags",
            })
            return payload
        except HTTPError as exc:
            self._endpoint_reachable = False
            self._probe_state = "probed_fail"
            self._last_probe_error = f"http_error_{exc.code}"
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            self._endpoint_reachable = False
            self._probe_state = "probed_fail"
            self._last_probe_error = f"{type(exc).__name__}: local_provider_unavailable"
        payload = self.describe()
        payload.update({
            "probe_ok": False,
            "configured_model_installed": False,
            "installed_models": [],
            "probe_endpoint": "/api/tags",
        })
        return payload

    def _system_text(self, request: ModelAdapterRequest, *, strict_retry: bool = False) -> str:
        text = (
            "Jesteś wyłącznie językową warstwą wykonawczą Jaźni Łatki. "
            "Odpowiadaj naturalnie po polsku, bez listy przypadkowych dalszych opcji. "
            "Nie wymyślaj pamięci ani faktów poza przekazanym kontekstem. "
            "Nie wspominaj o ChatGPT ani o zewnętrznym hoście, chyba że użytkownik pyta o nie wprost. "
            f"Bieżący backend językowy: provider=ollama, adapter={self.name}, model={self.model}, endpoint={self.api_base}. "
            "Gdy użytkownik pyta o dostępny model lub adapter, podaj dokładnie te fakty."
        )
        if strict_retry:
            text += " POPRZEDNI KANDYDAT MIAŁ ZŁY JĘZYK. Odpowiedz wyłącznie po polsku i bez metakomentarza."
        return text + "\n\nKONTEKST_JAZNI_JSON:\n" + json.dumps(request.system_context or {}, ensure_ascii=False)

    def _chat_once(self, request: ModelAdapterRequest, *, strict_retry: bool) -> tuple[str, dict[str, Any]]:
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "5m",
            "messages": [
                {"role": "system", "content": self._system_text(request, strict_retry=strict_retry)},
                {"role": "user", "content": request.prompt},
            ],
            "options": {"num_predict": self.max_output_tokens},
        }
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
        metadata = {
            "requested_model": self.model,
            "actual_model": str(data.get("model") or self.model),
            "done": data.get("done"),
            "done_reason": data.get("done_reason"),
            "total_duration": data.get("total_duration"),
            "load_duration": data.get("load_duration"),
            "prompt_eval_count": data.get("prompt_eval_count"),
            "eval_count": data.get("eval_count"),
            "strict_retry": strict_retry,
        }
        return text, metadata

    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        if not self.model:
            return ModelAdapterResponse(text="", provider="ollama", model="not_configured", status="not_configured", adapter_id=self.name)
        allow_non_polish = bool(request.metadata.get("allow_non_polish")) or user_explicitly_requested_non_polish(request.prompt)
        attempts: list[dict[str, Any]] = []
        try:
            text, metadata = self._chat_once(request, strict_retry=False)
            assessment = assess_response_language(text)
            metadata["language_guard"] = assessment.to_dict()
            attempts.append(metadata)
            if text and not allow_non_polish and not assessment.accepted_for_polish:
                text, metadata = self._chat_once(request, strict_retry=True)
                assessment = assess_response_language(text)
                metadata["language_guard"] = assessment.to_dict()
                attempts.append(metadata)
            actual_model = str((attempts[-1] if attempts else {}).get("actual_model") or self.model)
            accepted = bool(text) and (allow_non_polish or assessment.accepted_for_polish)
            self._last_generation = {
                "status": "completed" if accepted else ("language_mismatch" if text else "empty_output"),
                "actual_model": actual_model,
                "attempt_count": len(attempts),
                "done_reason": (attempts[-1] if attempts else {}).get("done_reason"),
                "language": assessment.language,
            }
            if accepted:
                self._endpoint_reachable = True
                self._probe_state = "probed_ok"
                self._last_generation_succeeded = True
                self._last_probe_error = None
            else:
                self._last_generation_succeeded = False
                self._last_probe_error = "response_language_mismatch" if text else "empty_output"
            return ModelAdapterResponse(
                text=text if accepted else "",
                provider="ollama",
                model=actual_model,
                status="completed" if accepted else ("language_mismatch" if text else "empty_output"),
                adapter_id=self.name,
                endpoint_used="/api/chat",
                status_snapshot=self._status_snapshot(),
                transport={"attempts": attempts, "retry_count": max(0, len(attempts) - 1), "language_guard_enforced": not allow_non_polish},
            )
        except HTTPError as exc:
            self._endpoint_reachable = False
            self._probe_state = "probed_fail"
            self._last_probe_error = f"http_error_{exc.code}"
            self._last_generation = {"status": self._last_probe_error, "attempt_count": len(attempts)}
            return ModelAdapterResponse(text="", provider="ollama", model=self.model, status=f"http_error_{exc.code}", adapter_id=self.name, endpoint_used="/api/chat", status_snapshot=self._status_snapshot(), transport={"attempts": attempts})
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            self._endpoint_reachable = False
            self._probe_state = "probed_fail"
            self._last_probe_error = "local_provider_unavailable"
            self._last_generation = {"status": "local_provider_unavailable", "error_type": type(exc).__name__, "attempt_count": len(attempts)}
            return ModelAdapterResponse(text="", provider="ollama", model=self.model, status="local_provider_unavailable", adapter_id=self.name, endpoint_used="/api/chat", status_snapshot=self._status_snapshot(), transport={"attempts": attempts, "error_type": type(exc).__name__})

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
            capabilities={"last_generation": dict(self._last_generation)},
        )
