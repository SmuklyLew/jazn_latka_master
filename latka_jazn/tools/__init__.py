"""Narzędzia eksportu, audytu i deduplikacji Łatka Jaźń v14.6.2."""

from __future__ import annotations


def _apply_windows_ollama_nlp_bootstrap_once() -> None:
    """One-shot GitHub Actions bootstrap for the reviewed stability patch.

    It is intentionally restricted to one named source branch and removes
    itself before the resulting source commit is created.
    """
    import base64
    import gzip
    import hashlib
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import sys

    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return
    if os.environ.get("JAZN_WINDOWS_OLLAMA_NLP_BOOTSTRAP") == "1":
        return
    branch = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or ""
    if branch != "fix/windows-ollama-nlp-stability":
        return

    root = Path.cwd().resolve()
    payload_dir = root / "docs" / "patches" / "_apply_windows_ollama_nlp"
    parts = sorted(payload_dir.glob("payload.b64.*"))
    if [part.name for part in parts] != [
        "payload.b64.00",
        "payload.b64.01",
        "payload.b64.02",
        "payload.b64.03",
    ]:
        return

    print("[bootstrap] applying reviewed Windows/Ollama/NLP stability payload", flush=True)
    encoded = "".join(part.read_text(encoding="utf-8").strip() for part in parts)
    script_bytes = gzip.decompress(base64.b64decode(encoded, validate=True))
    expected_sha = "e26f34d77a437cabd18979c984b7d39f6961fa14fc20414f14de1aa4412373f1"
    actual_sha = hashlib.sha256(script_bytes).hexdigest()
    if actual_sha != expected_sha:
        raise RuntimeError(f"bootstrap payload SHA mismatch: {actual_sha}")

    temp_script = root / ".apply_windows_ollama_nlp_stability.py"
    temp_script.write_bytes(script_bytes)
    env = dict(os.environ)
    env["JAZN_WINDOWS_OLLAMA_NLP_BOOTSTRAP"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    def run(*args: str) -> None:
        print("[bootstrap] run:", " ".join(args), flush=True)
        subprocess.run(args, cwd=root, env=env, check=True)

    run(sys.executable, "-X", "utf8", str(temp_script))
    run(sys.executable, "-X", "utf8", "-m", "compileall", "-q", "latka_jazn", "tests", "main.py", "run.py")

    shutil.rmtree(root / "docs" / "patches" / "_bootstrap_daemon_ollama", ignore_errors=True)
    shutil.rmtree(payload_dir, ignore_errors=True)
    temp_script.unlink(missing_ok=True)

    run("git", "checkout", "origin/master", "--", "latka_jazn/tools/__init__.py")
    tracked_paths = [
        "AGENTS.ollama.md",
        "main.py",
        "latka_jazn/core/runtime_daemon.py",
        "latka_jazn/nlp/response_language_guard.py",
        "latka_jazn/model_adapters/local_llm_adapter.py",
        "latka_jazn/nlp/dialogue_intent_classifier.py",
        "latka_jazn/core/route_registry.py",
        "latka_jazn/core/handlers/capability_status_handler.py",
        "latka_jazn/core/runtime_answer_validator.py",
        "latka_jazn/core/runtime_response_synthesizer.py",
        "latka_jazn/resources/nlp/verified_sources.json",
        "docs/reports/WINDOWS_OLLAMA_NLP_STABILITY_SOURCES.md",
        "tests/test_windows_daemon_console_policy.py",
        "tests/test_ollama_turn_stability_nlp.py",
        "docs/patches/_bootstrap_daemon_ollama",
        "docs/patches/_apply_windows_ollama_nlp",
        "latka_jazn/tools/__init__.py",
    ]
    run("git", "add", "-A", "--", *tracked_paths)
    run("git", "diff", "--cached", "--check")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root, env=env).returncode == 0:
        print("[bootstrap] no source changes to commit", flush=True)
        return

    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "commit", "-m", "fix: normalize Windows daemon and stabilize Ollama NLP")
    run("git", "push", "origin", f"HEAD:{branch}")
    print("[bootstrap] source commit pushed; metadata synchronization continues", flush=True)


_apply_windows_ollama_nlp_bootstrap_once()
del _apply_windows_ollama_nlp_bootstrap_once
