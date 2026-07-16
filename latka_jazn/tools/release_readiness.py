from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

from latka_jazn.config import JaznConfig
from latka_jazn.core.runtime_daemon import start_daemon, status_daemon, stop_daemon
from latka_jazn.core.source_provenance import read_source_provenance
from latka_jazn.memory.normalization_sidecar import MemoryNormalizationSidecar
from latka_jazn.tools.package_integrity import verify_package_integrity_manifest
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version


def _check(name: str, ok: bool, *, required: bool = True, **details: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "required": required, **details}


def _run(root: Path, *args: str, input_text: str | None = None, timeout: float = 60.0) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPYCACHEPREFIX"] = str(root / "workspace_runtime" / "smoke_pycache")
    env["JAZN_DAEMON_AUTOSTART"] = "0"
    stdin_args = {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", *args], cwd=root, **stdin_args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env, check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _copy_static_package(root: Path, destination: Path) -> None:
    manifest_path = root / "PACKAGE_INTEGRITY_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    for entry in manifest.get("files") or []:
        relative = str(entry.get("path") or "")
        source = root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(manifest_path, destination / manifest_path.name)


def _json_document(output: str) -> dict[str, Any] | None:
    try:
        value = json.loads(output.strip())
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def build_release_readiness_report(root: Path | str, *, profile: str = "system") -> dict[str, Any]:
    root = Path(root).resolve()
    checks: list[dict[str, Any]] = []
    configuration_error = False
    if profile not in {"system", "memory", "full"}:
        return {
            "schema_version": schema_version("release_readiness_report"), "ok": False,
            "exit_code": 2, "profile": profile,
            "checks": [_check("profile", False, error="unsupported profile")],
        }

    required_files = [root / "run.py", root / "main.py", root / "latka_jazn"]
    required_ok = all(path.exists() for path in required_files)
    checks.append(_check("required_package_files", required_ok, paths=[str(path) for path in required_files]))
    if not required_ok:
        configuration_error = True

    manifest = verify_package_integrity_manifest(root)
    checks.append(_check("package_integrity_manifest", bool(manifest.get("ok")), report=manifest))
    if manifest.get("configuration_error"):
        configuration_error = True

    provenance = read_source_provenance(root).to_dict()
    provenance_ok = provenance.get("status") in {
        "clean_checkout_verified", "development_dirty_verified", "verified_export_without_git_history",
    }
    checks.append(_check("source_provenance", provenance_ok, report=provenance))
    if provenance.get("status") in {"missing", "invalid"}:
        configuration_error = True

    checks.append(_check(
        "version_consistency",
        provenance.get("runtime_version") == PACKAGE_VERSION_FULL
        and manifest.get("ok")
        and not any(error.get("code") == "version_mismatch" for error in manifest.get("errors") or []),
        runtime_version=PACKAGE_VERSION_FULL,
    ))

    if not configuration_error:
        with tempfile.TemporaryDirectory(prefix="jazn-package-smoke-") as temp_name:
            isolated = Path(temp_name)
            try:
                _copy_static_package(root, isolated)
            except Exception as exc:
                checks.append(_check("isolated_package_copy", False, error=repr(exc)))
                configuration_error = True
            else:
                checks.append(_check("isolated_package_copy", True, root=str(isolated)))
                compile_result = _run(isolated, "-m", "compileall", "-q", "latka_jazn")
                checks.append(_check(
                    "compile", compile_result["returncode"] == 0,
                    returncode=compile_result["returncode"], stderr=compile_result["stderr"][-2000:],
                ))
                import_result = _run(
                    isolated, "-c",
                    "from latka_jazn.version import PACKAGE_VERSION_FULL; print(PACKAGE_VERSION_FULL)",
                )
                checks.append(_check(
                    "import", import_result["returncode"] == 0 and import_result["stdout"].strip() == PACKAGE_VERSION_FULL,
                    returncode=import_result["returncode"], stdout=import_result["stdout"].strip(),
                ))
                version_result = _run(isolated, "run.py", "--version")
                checks.append(_check(
                    "cli_version", version_result["returncode"] == 0 and PACKAGE_VERSION_FULL in version_result["stdout"],
                    returncode=version_result["returncode"],
                ))
                snapshot_result = _run(isolated, "run.py", "status", "--snapshot", "--json")
                snapshot_payload = _json_document(snapshot_result["stdout"])
                snapshot_daemon = (snapshot_payload or {}).get("daemon") or {}
                checks.append(_check(
                    "cli_status_snapshot",
                    snapshot_result["returncode"] == 0
                    and snapshot_daemon.get("endpoint_probe_performed") is False
                    and snapshot_daemon.get("observation_state") == "endpoint_not_probed",
                    returncode=snapshot_result["returncode"], daemon=snapshot_daemon,
                ))
                doctor_result = _run(isolated, "run.py", "doctor", "--json")
                doctor = _json_document(doctor_result["stdout"])
                checks.append(_check(
                    "doctor", doctor_result["returncode"] == 0 and bool((doctor or {}).get("ok")),
                    returncode=doctor_result["returncode"], report=doctor,
                ))

                chat_input = json.dumps({"text": "Działasz?"}, ensure_ascii=False) + "\n"
                chat_result = _run(
                    isolated, "main.py", "--root", str(isolated), "--no-ensure-daemon", "--chat-gpt",
                    input_text=chat_input, timeout=120.0,
                )
                chat_lines = [line for line in chat_result["stdout"].splitlines() if line.strip()]
                chat = _json_document(chat_lines[-1]) if chat_lines else None
                consensus = (chat or {}).get("final_visible_integrity_consensus") or {}
                checks.append(_check(
                    "chat_gpt_turn_and_integrity_consensus",
                    chat_result["returncode"] == 0 and bool((chat or {}).get("final_visible_text"))
                    and consensus.get("valid") is True and consensus.get("mismatch") is False,
                    returncode=chat_result["returncode"], consensus=consensus,
                    stderr=chat_result["stderr"][-2000:],
                ))

                port = _free_port()
                marker = isolated / "workspace_runtime" / "package_smoke_daemon_marker.json"
                cfg = JaznConfig(root=isolated)
                startup: dict[str, Any] | None = None
                daemon_status: dict[str, Any] | None = None
                stop_report: dict[str, Any] | None = None
                try:
                    startup = start_daemon(cfg, port=port, marker_output=marker, startup_timeout=20.0)
                    daemon_status = status_daemon(cfg, port=port, marker_output=marker, probe_endpoint=True)
                    cli_live = _run(
                        isolated, "run.py", "status", "--daemon-port", str(port),
                        "--daemon-marker-output", str(marker), "--json", timeout=60.0,
                    )
                    cli_live_payload = _json_document(cli_live["stdout"])
                    cli_live_daemon = (cli_live_payload or {}).get("daemon") or {}
                    daemon_ok = (
                        daemon_status.get("active_state") == "active_trusted"
                        and cli_live["returncode"] == 0
                        and cli_live_daemon.get("active_state") == "active_trusted"
                        and cli_live_daemon.get("endpoint_probe_performed") is True
                    )
                    checks.append(_check(
                        "isolated_daemon_start_status", daemon_ok, port=port,
                        startup=startup, status=daemon_status, cli_status=cli_live_daemon,
                    ))
                except Exception as exc:
                    checks.append(_check("isolated_daemon_start_status", False, port=port, error=repr(exc)))
                finally:
                    try:
                        stop_report = stop_daemon(cfg, port=port, marker_output=marker, timeout=20.0)
                    except Exception as exc:
                        stop_report = {"ok": False, "error": repr(exc)}
                after_cleanup = status_daemon(cfg, port=port, marker_output=marker, probe_endpoint=True)
                checks.append(_check(
                    "isolated_daemon_cleanup",
                    bool((stop_report or {}).get("ok"))
                    and after_cleanup.get("active_state") == "inactive"
                    and after_cleanup.get("endpoint_reachable") is False
                    and after_cleanup.get("pid_alive") is False,
                    stop=stop_report, after=after_cleanup,
                ))

    if profile in {"memory", "full"}:
        cfg = JaznConfig(root=root)
        sidecar = MemoryNormalizationSidecar(
            root, source_db_path=cfg.memory_db_path_readonly,
            sidecar_db_path=cfg.audit_db_path_readonly, runtime_version=cfg.version,
        )
        wake = sidecar.wake_state_status(deep_verify=True).to_dict()
        checks.append(_check("memory_wake_state", wake.get("status") == "ready", report=wake))

    failures = [item for item in checks if item.get("required") and not item.get("ok")]
    exit_code = 2 if configuration_error else (1 if failures else 0)
    return {
        "schema_version": schema_version("release_readiness_report"),
        "runtime_version": PACKAGE_VERSION_FULL,
        "profile": profile,
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "summary": {
            "passed": sum(1 for item in checks if item.get("ok")),
            "failed": len(failures),
            "total": len(checks),
        },
        "checks": checks,
        "truth_boundary": (
            "The system profile does not require private memory. Memory/full profiles verify SQLite/wake state read-only. "
            "The daemon uses a free port, isolated marker and temporary package root, and is stopped in finally."
        ),
    }
