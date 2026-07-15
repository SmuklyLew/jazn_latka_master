from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from latka_jazn.core.runtime_environment import (
    CHATGPT_ADAPTER,
    LMSTUDIO_ADAPTER,
    NULL_ADAPTER,
    OPENAI_ADAPTER,
    OPENAI_COMPATIBLE_ADAPTER,
    detect_runtime_environment,
)
from latka_jazn.version import schema_version

LLM_ROUTE_AUTO = "auto"
ROUTE_LOCAL = "local_openai_compatible"
ROUTE_CHATGPT_BRIDGE = "chatgpt_host_bridge"
ROUTE_OPENAI_PAID = "openai_api_paid"
ROUTE_NULL = "null_fallback"

_ALLOWED_MODES = {"auto", "local", "chatgpt_bridge", "openai_api", "none"}


def _env_get(env: Mapping[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = env.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "tak", "on"}


def _normalize_mode(value: str | None) -> str:
    raw = str(value or "auto").strip().lower().replace("-", "_") or "auto"
    aliases = {
        "local_llm": "local",
        "local_openai_compatible": "local",
        "chatgpt": "chatgpt_bridge",
        "chat_gpt": "chatgpt_bridge",
        "bridge": "chatgpt_bridge",
        "openai": "openai_api",
        "openai_paid": "openai_api",
        "paid_openai": "openai_api",
        "null": "none",
        "fallback": "none",
        "off": "none",
    }
    mode = aliases.get(raw, raw)
    return mode if mode in _ALLOWED_MODES else "auto"


def _normalise_base_url(url: str, *, wants_v1: bool = True) -> str:
    base = (url or "").strip().rstrip("/")
    if wants_v1 and base and not base.endswith("/v1") and not base.endswith("/api"):
        base += "/v1"
    return base


def _local_candidates(config: Any, env: Mapping[str, str]) -> list[dict[str, Any]]:
    lm_model = _env_get(env, "JAZN_LM_STUDIO_MODEL", "JAZN_LMSTUDIO_MODEL", default=str(getattr(config, "lm_studio_model_name", "") or "").strip())
    lm_base = _env_get(
        env,
        "JAZN_LM_STUDIO_API_BASE",
        "JAZN_LMSTUDIO_API_BASE",
        default=str(getattr(config, "lm_studio_api_base", "http://127.0.0.1:1234/v1") or "http://127.0.0.1:1234/v1"),
    )
    local_model = _env_get(
        env,
        "JAZN_LOCAL_LLM_MODEL",
        "JAZN_LOCAL_MODEL_NAME",
        default=str(getattr(config, "local_model_name", "") or "").strip(),
    )
    local_base = _env_get(
        env,
        "JAZN_LOCAL_LLM_BASE_URL",
        "JAZN_LOCAL_LLM_API_BASE",
        "JAZN_LOCAL_MODEL_API_BASE",
        default=str(getattr(config, "local_model_api_base", "http://127.0.0.1:11434") or "http://127.0.0.1:11434"),
    )
    provider = _env_get(env, "JAZN_LOCAL_LLM_PROVIDER", default="openai_compatible").lower()
    candidates = [
        {
            "provider": "lmstudio",
            "adapter": LMSTUDIO_ADAPTER,
            "base_url": _normalise_base_url(lm_base, wants_v1=True),
            "model": lm_model,
            "api_key": "",
        },
        {
            "provider": provider or "openai_compatible",
            "adapter": OPENAI_COMPATIBLE_ADAPTER,
            "base_url": _normalise_base_url(local_base, wants_v1=(provider != "ollama_native")),
            "model": local_model,
            "api_key": _env_get(env, "JAZN_LOCAL_LLM_API_KEY", default="ollama" if provider == "ollama" else ""),
        },
    ]
    return [item for item in candidates if item["base_url"]]


def _extract_first_model_id(data: dict[str, Any]) -> str:
    models = data.get("data")
    if isinstance(models, list):
        for item in models:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                return str(item["id"]).strip()
    return ""


def discover_openai_compatible_model(base_url: str, *, api_key: str = "", timeout_seconds: float = 2.0) -> tuple[str, str | None]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        request = Request(f"{base_url.rstrip('/')}/models", headers=headers, method="GET")
        with urlopen(request, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        if isinstance(data, dict):
            model = _extract_first_model_id(data)
            if model:
                return model, None
        return "", "models_response_empty"
    except HTTPError as exc:
        return "", f"http_error_{exc.code}"
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return "", "provider_unavailable"


def probe_openai_compatible_endpoint(candidate: dict[str, Any], *, timeout_seconds: float = 2.0) -> dict[str, Any]:
    base_url = str(candidate.get("base_url") or "").rstrip("/")
    model = str(candidate.get("model") or "").strip()
    api_key = str(candidate.get("api_key") or "")
    if not base_url:
        return {"available": False, "reason": "base_url_missing"}
    if not model:
        discovered, error = discover_openai_compatible_model(base_url, api_key=api_key, timeout_seconds=timeout_seconds)
        if not discovered:
            return {"available": False, "reason": error or "model_missing", "model": ""}
        model = discovered
    return {"available": True, "reason": "configured", "model": model}


@dataclass(slots=True)
class LlmRouteStatus:
    ok: bool
    route_mode: str
    selected_route: str
    paid_route: bool
    paid_route_allowed: bool
    local: dict[str, Any]
    chatgpt_bridge: dict[str, Any]
    openai_api: dict[str, Any]
    reason: str
    error: str | None = None
    selected_adapter: str | None = None
    schema_version: str = schema_version("llm_route_status")
    truth_boundary: str = (
        "Routing LLM wybiera backend językowy, nie tożsamość Jaźni. ChatGPT Plus nie jest API key; "
        "OpenAI API jest trasą płatną i wymaga OPENAI_API_KEY oraz JAZN_ALLOW_PAID_OPENAI=1."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_llm_route_status(
    config: Any,
    *,
    command: str | None = None,
    env: Mapping[str, str] | None = None,
    infer_host_environment: bool = False,
    probe_local: bool = True,
) -> LlmRouteStatus:
    env_map: Mapping[str, str] = env if env is not None else os.environ
    mode = _normalize_mode(env_map.get("JAZN_LLM_ROUTE") or str(getattr(config, "llm_route_mode", "auto") or "auto"))
    paid_allowed = _truthy(env_map.get("JAZN_ALLOW_PAID_OPENAI")) if "JAZN_ALLOW_PAID_OPENAI" in env_map else bool(getattr(config, "allow_paid_openai_api", False))
    api_key_present = bool(str(env_map.get("OPENAI_API_KEY") or "").strip())
    openai_model = _env_get(env_map, "JAZN_OPENAI_MODEL", "JAZN_MODEL_NAME", default=str(getattr(config, "openai_paid_model_name", getattr(config, "model_name", "")) or ""))

    candidates = _local_candidates(config, env_map)
    local_status: dict[str, Any] = {
        "checked": True,
        "available": False,
        "candidates": [],
        "selected_candidate": None,
    }
    selected_local: dict[str, Any] | None = None
    for candidate in candidates:
        item = dict(candidate)
        probe = probe_openai_compatible_endpoint(candidate, timeout_seconds=2.0) if probe_local else {
            "available": bool(candidate.get("model") and candidate.get("base_url")),
            "reason": "probe_skipped",
            "model": candidate.get("model") or "",
        }
        item.update(probe)
        if probe.get("model"):
            item["model"] = probe["model"]
        local_status["candidates"].append(item)
        if item.get("available") and selected_local is None:
            selected_local = item
    if selected_local:
        local_status.update({
            "available": True,
            "provider": selected_local.get("provider"),
            "adapter": selected_local.get("adapter"),
            "base_url": selected_local.get("base_url"),
            "model": selected_local.get("model"),
            "selected_candidate": selected_local,
        })
    else:
        local_status["reason"] = "no_local_openai_compatible_endpoint_available"

    environment = detect_runtime_environment(
        config,
        command=command,
        env=env_map,
        infer_host_environment=infer_host_environment,
    )
    bridge_available = bool(environment.is_chatgpt_host_bridge)
    chatgpt_status = {
        "checked": True,
        "available": bridge_available,
        "adapter": CHATGPT_ADAPTER,
        "basis": list(environment.detection_basis),
        "environment_host": environment.environment_host,
        "truth_boundary": "Host bridge oznacza, że widzialną wypowiedź tworzy host ChatGPT; lokalny Python nie wywołuje modelu ChatGPT.",
    }
    openai_status = {
        "checked": True,
        "api_key_present": api_key_present,
        "allowed": paid_allowed,
        "model": openai_model or None,
        "paid_route": bool(api_key_present and paid_allowed),
    }

    def _status(ok: bool, route: str, reason: str, *, error: str | None = None, adapter: str | None = None) -> LlmRouteStatus:
        return LlmRouteStatus(
            ok=ok,
            route_mode=mode,
            selected_route=route,
            paid_route=(route == ROUTE_OPENAI_PAID),
            paid_route_allowed=paid_allowed,
            local=local_status,
            chatgpt_bridge=chatgpt_status,
            openai_api=openai_status,
            reason=reason,
            error=error,
            selected_adapter=adapter,
        )

    if mode == "none":
        return _status(True, ROUTE_NULL, "JAZN_LLM_ROUTE=none")
    if mode == "local":
        if selected_local:
            return _status(True, ROUTE_LOCAL, "forced local route available", adapter=str(selected_local.get("adapter") or OPENAI_COMPATIBLE_ADAPTER))
        return _status(False, ROUTE_NULL, "forced local route unavailable", error="local_llm_unavailable", adapter=NULL_ADAPTER)
    if mode == "chatgpt_bridge":
        if bridge_available:
            return _status(True, ROUTE_CHATGPT_BRIDGE, "forced ChatGPT host bridge available", adapter=CHATGPT_ADAPTER)
        return _status(False, ROUTE_NULL, "forced ChatGPT host bridge unavailable", error="chatgpt_bridge_unavailable", adapter=NULL_ADAPTER)
    if mode == "openai_api":
        if not api_key_present:
            return _status(False, ROUTE_NULL, "forced OpenAI API route blocked: OPENAI_API_KEY missing", error="openai_api_key_missing", adapter=NULL_ADAPTER)
        if not paid_allowed:
            return _status(False, ROUTE_NULL, "forced OpenAI API route blocked: JAZN_ALLOW_PAID_OPENAI is not 1", error="paid_openai_not_allowed", adapter=NULL_ADAPTER)
        return _status(True, ROUTE_OPENAI_PAID, "forced paid OpenAI API route allowed", adapter=OPENAI_ADAPTER)

    if selected_local:
        return _status(True, ROUTE_LOCAL, "local OpenAI-compatible endpoint available", adapter=str(selected_local.get("adapter") or OPENAI_COMPATIBLE_ADAPTER))
    if bridge_available:
        return _status(True, ROUTE_CHATGPT_BRIDGE, "local endpoint unavailable; ChatGPT host bridge available", adapter=CHATGPT_ADAPTER)
    if api_key_present and paid_allowed:
        return _status(True, ROUTE_OPENAI_PAID, "local and bridge unavailable; paid OpenAI API explicitly allowed", adapter=OPENAI_ADAPTER)
    return _status(True, ROUTE_NULL, "no local endpoint, no host bridge, and paid OpenAI API unavailable or not allowed", adapter=NULL_ADAPTER)


def apply_llm_route_to_config(config: Any, status: LlmRouteStatus) -> Any:
    route = status.selected_route
    if route == ROUTE_LOCAL:
        candidate = status.local.get("selected_candidate") if isinstance(status.local, dict) else None
        if isinstance(candidate, dict):
            adapter = str(candidate.get("adapter") or OPENAI_COMPATIBLE_ADAPTER)
            setattr(config, "model_adapter", adapter)
            if adapter == LMSTUDIO_ADAPTER:
                setattr(config, "lm_studio_model_name", str(candidate.get("model") or ""))
                setattr(config, "lm_studio_api_base", str(candidate.get("base_url") or ""))
            else:
                setattr(config, "local_model_name", str(candidate.get("model") or ""))
                setattr(config, "local_model_api_base", str(candidate.get("base_url") or ""))
        return config
    if route == ROUTE_CHATGPT_BRIDGE:
        setattr(config, "model_adapter", CHATGPT_ADAPTER)
        return config
    if route == ROUTE_OPENAI_PAID:
        setattr(config, "model_adapter", OPENAI_ADAPTER)
        return config
    setattr(config, "model_adapter", NULL_ADAPTER)
    return config
