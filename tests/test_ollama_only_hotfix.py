from __future__ import annotations

import subprocess
from pathlib import Path

from latka_jazn.config import JaznConfig
from latka_jazn.core.chat_command_contract import apply_ollama_cli_settings, command_contract
from latka_jazn.core.runtime_environment import OLLAMA_ADAPTER, detect_runtime_environment

_TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".toml", ".yml", ".yaml"}
_DYNAMIC_ROOTS = {".git", ".pytest_cache", "__pycache__", "exports", "memory", "workspace_runtime"}


def _repository_text_files(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return [
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in _TEXT_SUFFIXES
            and not any(part in _DYNAMIC_ROOTS for part in path.relative_to(root).parts)
        ]

    return [
        root / raw_path.decode("utf-8", errors="surrogateescape")
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    ]


def test_chat_ollama_selects_native_adapter(tmp_path: Path) -> None:
    cfg = JaznConfig(root=tmp_path)
    cfg.local_model_name = "test-model"
    status = detect_runtime_environment(cfg, command="--chat-ollama", env={})
    assert status.visible_channel_adapter == OLLAMA_ADAPTER
    assert status.effective_runtime_adapter == OLLAMA_ADAPTER


def test_ollama_contract_is_local_and_keyless() -> None:
    contract = command_contract("--chat-ollama")
    assert contract["command"] == "--chat-ollama"
    assert contract["requires_api_key"] is False
    assert contract["uses_openai_api"] is False
    assert "Ollama" in contract["truth_boundary"]


def test_apply_ollama_cli_settings_applies_timeout_and_token_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for name in (
        "JAZN_OLLAMA_MODEL",
        "JAZN_LOCAL_LLM_MODEL",
        "JAZN_OLLAMA_BASE_URL",
        "JAZN_LOCAL_LLM_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    cfg = JaznConfig(root=tmp_path)
    result = apply_ollama_cli_settings(
        cfg,
        model="gemma3",
        api_base="http://127.0.0.1:11434/",
        timeout_seconds=123.5,
        max_output_tokens=321,
    )

    assert result is cfg
    assert cfg.model_adapter == "ollama"
    assert cfg.local_model_name == "gemma3"
    assert cfg.local_model_api_base == "http://127.0.0.1:11434"
    assert cfg.model_timeout_seconds == 123.5
    assert cfg.model_max_output_tokens == 321


def test_active_repository_has_no_removed_local_backend_integration() -> None:
    root = Path(__file__).resolve().parents[1]
    stem = "lm" + "studio"
    forbidden = (stem, "lm_" + "studio", "lm-" + "studio", "lm " + "studio")
    excluded = {
        root / "PACKAGE_INTEGRITY_MANIFEST.json",
        root / "latka_jazn/contracts/embedded_sources.py",
    }
    offenders: list[str] = []

    for path in _repository_text_files(root):
        if path in excluded or not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(root)))

    assert sorted(offenders) == []


def test_resolve_ollama_cli_settings_selects_single_discovered_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from latka_jazn.core.chat_command_contract import resolve_ollama_cli_settings

    for name in (
        "JAZN_OLLAMA_MODEL",
        "JAZN_LOCAL_LLM_MODEL",
        "JAZN_LOCAL_MODEL_NAME",
        "JAZN_OLLAMA_BASE_URL",
        "JAZN_LOCAL_LLM_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(
        "latka_jazn.core.llm_route_resolver.probe_ollama",
        lambda config, env, timeout_seconds: {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "gemma3:latest",
            "configured_model": "",
            "running_models": [],
            "installed_models": ["gemma3:latest"],
            "available": True,
            "endpoint_reachable": True,
            "reason": "single_installed_model",
            "errors": [],
        },
    )

    cfg, probe = resolve_ollama_cli_settings(JaznConfig(root=tmp_path))

    assert cfg.model_adapter == "ollama"
    assert cfg.local_model_name == "gemma3:latest"
    assert probe["reason"] == "single_installed_model"


def test_resolve_ollama_cli_settings_keeps_multiple_models_ambiguous(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from latka_jazn.core.chat_command_contract import resolve_ollama_cli_settings

    for name in (
        "JAZN_OLLAMA_MODEL",
        "JAZN_LOCAL_LLM_MODEL",
        "JAZN_LOCAL_MODEL_NAME",
        "JAZN_OLLAMA_BASE_URL",
        "JAZN_LOCAL_LLM_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(
        "latka_jazn.core.llm_route_resolver.probe_ollama",
        lambda config, env, timeout_seconds: {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "",
            "configured_model": "",
            "running_models": [],
            "installed_models": ["gemma3:latest", "qwen3:8b"],
            "available": False,
            "endpoint_reachable": True,
            "reason": "ollama_model_ambiguous",
            "errors": [],
        },
    )

    cfg, probe = resolve_ollama_cli_settings(JaznConfig(root=tmp_path))

    assert cfg.local_model_name == ""
    assert probe["available"] is False
    assert probe["reason"] == "ollama_model_ambiguous"


def test_chat_ollama_without_message_uses_human_terminal_loop(monkeypatch) -> None:
    import io
    import main as main_module

    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    class FakeWorker:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.closed = False

        def close(self) -> None:
            self.closed = True

    calls: dict[str, object] = {}
    cfg = JaznConfig()
    cfg.local_model_name = "gemma3:latest"
    monkeypatch.setattr(
        main_module,
        "resolve_ollama_cli_settings",
        lambda *args, **kwargs: (
            cfg,
            {
                "available": True,
                "model": "gemma3:latest",
                "reason": "single_installed_model",
                "installed_models": ["gemma3:latest"],
                "running_models": [],
            },
        ),
    )
    monkeypatch.setattr(main_module, "_ensure_daemon_or_error", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(main_module, "RuntimeSessionWorker", FakeWorker)
    monkeypatch.setattr(
        main_module,
        "run_persistent_chat",
        lambda session, **kwargs: calls.update({"persistent_session": session, "persistent_kwargs": kwargs}),
    )
    monkeypatch.setattr(
        main_module,
        "run_jsonl_chat_bridge",
        lambda **kwargs: calls.update({"jsonl": kwargs}) or 0,
    )
    monkeypatch.setattr(main_module.sys, "stdin", TtyInput())

    exit_code = main_module.main(["--chat-ollama", "--no-runtime-preflight", "--no-ensure-daemon"])

    assert exit_code == 0
    assert "persistent_session" in calls
    assert "jsonl" not in calls
    worker = calls["persistent_session"]
    assert isinstance(worker, FakeWorker)
    assert worker.kwargs["command"] == "--chat-ollama"
    assert worker.kwargs["source_client"] == "ollama_terminal_chat"
    assert worker.closed is True


def test_chat_ollama_with_piped_stdin_keeps_jsonl_contract(monkeypatch) -> None:
    import io
    import main as main_module

    cfg = JaznConfig()
    cfg.local_model_name = "gemma3:latest"
    calls: dict[str, object] = {}
    monkeypatch.setattr(
        main_module,
        "resolve_ollama_cli_settings",
        lambda *args, **kwargs: (
            cfg,
            {
                "available": True,
                "model": "gemma3:latest",
                "reason": "configured_model_available",
                "installed_models": ["gemma3:latest"],
                "running_models": [],
            },
        ),
    )
    monkeypatch.setattr(main_module, "_ensure_daemon_or_error", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(
        main_module,
        "run_jsonl_chat_bridge",
        lambda **kwargs: calls.update({"jsonl": kwargs}) or 0,
    )
    monkeypatch.setattr(main_module.sys, "stdin", io.StringIO("Cześć\n"))

    exit_code = main_module.main(["--chat-ollama", "--no-runtime-preflight", "--no-ensure-daemon"])

    assert exit_code == 0
    assert calls["jsonl"]["command"] == "--chat-ollama"
    assert calls["jsonl"]["output_mode"] == "jsonl"


def test_chat_ollama_reports_ambiguous_models_before_start(monkeypatch, capsys) -> None:
    import io
    import main as main_module

    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(
        main_module,
        "resolve_ollama_cli_settings",
        lambda *args, **kwargs: (
            JaznConfig(),
            {
                "available": False,
                "reason": "ollama_model_ambiguous",
                "installed_models": ["gemma3:latest", "qwen3:8b"],
                "running_models": [],
                "errors": [],
            },
        ),
    )
    monkeypatch.setattr(main_module.sys, "stdin", TtyInput())

    exit_code = main_module.main(["--chat-ollama", "--no-runtime-preflight", "--no-ensure-daemon"])
    captured = capsys.readouterr()

    assert exit_code == 3
    assert "Dostępne modele: gemma3:latest, qwen3:8b" in captured.err
    assert "--ollama-model" in captured.err
