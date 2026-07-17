from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from latka_jazn.bridge.secure_host_runtime_gateway import GatewayConfig
from latka_jazn.config import JaznConfig
from latka_jazn.core.bridge_discovery import discover_runtime_bridges
from latka_jazn.core.runtime_daemon import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT, status_daemon
from latka_jazn.core.startup_contract import build_startup_status
from latka_jazn.core.package_integrity_manifest import package_integrity_manifest_status
from latka_jazn.core.source_provenance import read_source_provenance
from latka_jazn.tools.package_integrity import verify_package_integrity_manifest
from latka_jazn.core.tool_execution_controller import ToolExecutionController
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version


def status_payload(
    root: Path,
    *,
    probe_endpoint: bool = True,
    daemon_host: str = DEFAULT_DAEMON_HOST,
    daemon_port: int = DEFAULT_DAEMON_PORT,
    marker_output: Path | None = None,
) -> dict[str, Any]:
    cfg = JaznConfig(root=root)
    startup = build_startup_status(cfg, mode="fast", infer_host_environment=True).to_dict()
    return {
        "schema_version": schema_version("runpy_status"),
        "runtime_version": PACKAGE_VERSION_FULL,
        "root": str(root),
        "startup": startup,
        "daemon": status_daemon(
            cfg, host=daemon_host, port=daemon_port,
            marker_output=marker_output, probe_endpoint=probe_endpoint,
        ),
    }


def _read_manifest(root: Path) -> tuple[dict[str, Any], str | None]:
    status = package_integrity_manifest_status(root)
    if not status.present or not status.path:
        return {}, "package_integrity_manifest_missing"
    path = Path(status.path)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return {}, "manifest_not_object"
    return value, None


def doctor_payload(
    root: Path,
    *,
    daemon_host: str = DEFAULT_DAEMON_HOST,
    daemon_port: int = DEFAULT_DAEMON_PORT,
    marker_output: Path | None = None,
) -> dict[str, Any]:
    status = status_payload(
        root,
        daemon_host=daemon_host,
        daemon_port=daemon_port,
        marker_output=marker_output,
    )
    startup = status.get("startup") or {}
    daemon = status.get("daemon") or {}
    manifest, manifest_error = _read_manifest(root)
    marker = startup.get("active_cache_status") or {}
    model = startup.get("model_adapter_status") or {}
    conversation_memory = startup.get("conversation_archive_status") or {}
    runtime_memory = startup.get("runtime_write_access_status") or {}
    daemon_marker = daemon.get("marker") or {}
    timestamp = daemon.get("timestamp_contract") or daemon_marker.get("timestamp_contract") or {}
    package_integrity = package_integrity_manifest_status(root)

    controller = ToolExecutionController()
    read_plan = controller.plan(
        tool_name="doctor_probe",
        action="read_status",
        source_kind="generated_report",
        source_content="doctor self-test",
        source_origin="run.py doctor",
        actor="operator_cli",
        reason="read_only_gate_self_test",
        write_action=False,
    )
    denied_write_plan = controller.plan(
        tool_name="doctor_probe",
        action="write_status",
        source_kind="generated_report",
        source_content="doctor self-test",
        source_origin="run.py doctor",
        actor="operator_cli",
        reason="unconfirmed_write_gate_self_test",
        write_action=True,
        user_confirmed=False,
    )
    try:
        GatewayConfig().validate()
        mcp_policy_error = None
    except Exception as exc:  # pragma: no cover - defensive serialization path
        mcp_policy_error = f"{type(exc).__name__}: {exc}"

    required_checks = {
        "root_exists": root.is_dir(),
        "main_exists": (root / "main.py").is_file(),
        "run_exists": (root / "run.py").is_file(),
        "version_py_exists": (root / "latka_jazn/version.py").is_file(),
        "package_exists": (root / "latka_jazn").is_dir(),
        "startup_status_available": bool(startup),
        "daemon_status_available": bool(daemon),
        "model_status_available": bool(model),
        "memory_status_available": bool(conversation_memory or runtime_memory),
        "tool_read_allowed": read_plan.allowed,
        "tool_unconfirmed_write_denied": not denied_write_plan.allowed,
        "mcp_loopback_policy_valid": mcp_policy_error is None,
        "privacy_gate_available": (root / "latka_jazn/core/private_data_export_gate.py").is_file(),
        "finalization_gate_available": (root / "latka_jazn/core/host_visible_finalization.py").is_file(),
    }
    manifest_verification = verify_package_integrity_manifest(root)
    provenance = read_source_provenance(root, profile="system_smoke").to_dict()
    package_integrity_checks = {
        "present": package_integrity.present,
        "parse_ok": manifest_error is None,
        "version_matches": str(manifest.get("runtime_version") or manifest.get("version") or "").lstrip("v")
        == PACKAGE_VERSION_FULL.lstrip("v"),
        "primary_present": package_integrity.primary_present,
        "legacy_alias_absent": not package_integrity.legacy_present,
        "canonical_source_name": package_integrity.source_name == "PACKAGE_INTEGRITY_MANIFEST.json",
        "verification_ok": bool(manifest_verification.get("ok")),
        "verification_errors": list(manifest_verification.get("errors") or []),
        "runtime_start_blocking": True,
    }
    installation_ok = all(required_checks.values())
    activation_ready = bool(
        installation_ok
        and package_integrity_checks["present"]
        and package_integrity_checks["parse_ok"]
        and package_integrity_checks["version_matches"]
        and package_integrity_checks["primary_present"]
        and package_integrity_checks["legacy_alias_absent"]
        and package_integrity_checks["canonical_source_name"]
        and package_integrity_checks["verification_ok"]
    )
    live_runtime_ready = bool(
        (daemon.get("active_state") or daemon.get("runtime_active_state")) == "active_trusted"
        and daemon.get("pid_alive")
        and daemon.get("endpoint_reachable")
        and daemon.get("heartbeat_fresh")
    )
    release_metadata_current = bool(
        package_integrity_checks["verification_ok"]
        and provenance.get("version_matches_runtime")
        and provenance.get("status") in {"clean_checkout_verified", "verified_export_without_git_history"}
    )
    release_ready = bool(activation_ready and release_metadata_current)

    live_evidence = {
        "marker_found": bool(marker.get("existing_marker_found") or daemon.get("marker_found")),
        "marker_valid": bool(marker.get("active_marker_valid") or daemon.get("marker_valid")),
        "daemon_active_state": daemon.get("active_state") or daemon.get("runtime_active_state") or "inactive",
        "daemon_pid_alive": bool(daemon.get("pid_alive")),
        "endpoint_probe_performed": bool(daemon.get("endpoint_probe_performed")),
        "endpoint_reachable": bool(daemon.get("endpoint_reachable")),
        "heartbeat_fresh": bool(daemon.get("heartbeat_fresh")),
        "timestamp_status_available": bool(timestamp),
        "timestamp_trusted": timestamp.get("trusted"),
        "time_trust_state": timestamp.get("time_trust_state") or daemon.get("time_trust_state") or "unknown",
    }
    subsystem_status = {
        "package_integrity_manifest": {
            **package_integrity.to_dict(),
            "ok": bool(manifest_verification.get("ok")),
            "error": manifest_error,
            "version": manifest.get("version") or manifest.get("runtime_version"),
            "start_file": manifest.get("start_file"),
            "verification": manifest_verification,
            "runtime_start_blocking": True,
        },
        "source_provenance": provenance,
        "model": {
            "available": bool(model),
            "adapter_id": model.get("adapter_id") or model.get("selected_adapter"),
            "status": model.get("status"),
            "requires_api_key": model.get("requires_api_key"),
        },
        "memory": {
            "conversation_archive": conversation_memory,
            "runtime_write": runtime_memory,
        },
        "tool_gates": {
            "read_plan": read_plan.to_dict(),
            "unconfirmed_write_plan": denied_write_plan.to_dict(),
        },
        "mcp": {
            "server_file_exists": (root / "latka_jazn/mcp/server.py").is_file(),
            "loopback_policy_valid": mcp_policy_error is None,
            "policy_error": mcp_policy_error,
            "public_ingress_default": False,
            "transport": "local stdio/loopback; optional outbound tunnel",
        },
        "privacy": {
            "gate_file_exists": (root / "latka_jazn/core/private_data_export_gate.py").is_file(),
            "private_profiles_require_second_confirmation": ["memory", "full"],
        },
        "time": timestamp,
    }
    return {
        "schema_version": schema_version("runpy_doctor"),
        # Backward compatibility: ``ok`` continues to mean structural installation health.
        # Read activation/release/live readiness from the explicit fields below.
        "ok": installation_ok,
        "installation_ok": installation_ok,
        "activation_ready": activation_ready,
        "release_metadata_current": release_metadata_current,
        "release_ready": release_ready,
        "live_runtime_ready": live_runtime_ready,
        "readiness": {
            "installation_ok": installation_ok,
            "activation_ready": activation_ready,
            "release_metadata_current": release_metadata_current,
            "release_ready": release_ready,
            "live_runtime_ready": live_runtime_ready,
        },
        "checks": required_checks,
        "package_integrity_checks": package_integrity_checks,
        "live_evidence": live_evidence,
        "subsystems": subsystem_status,
        "status": status,
        "read_only": True,
        "truth_boundary": (
            "Doctor reports structural installation health separately from activation, release metadata and live runtime readiness. "
            "A green legacy ok/installation_ok is not proof of an activatable package or live daemon; read activation_ready, "
            "release_ready and live_runtime_ready explicitly."
        ),
    }


def bridge_payload(root: Path) -> dict[str, Any]:
    payload = discover_runtime_bridges(JaznConfig(root=root))
    payload["v15_secure_mcp"] = {
        "server": "python -X utf8 -m latka_jazn.mcp.server",
        "transport": "stdio/local + optional outbound Secure MCP Tunnel",
        "public_ingress_default": False,
        "auth_required": True,
    }
    payload["finalization_gate"] = "latka_jazn.core.host_visible_finalization.HostVisibleFinalizationGate"
    payload["audit"] = "memory/sqlite/runtime_write_v1/runtime_audit.sqlite3"
    payload["fallback"] = "copy-paste helper using the same finalization gate"
    return payload
