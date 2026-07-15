from __future__ import annotations

from pathlib import Path
from typing import Any

from latka_jazn.core.private_data_export_gate import PrivateDataExportGate
from latka_jazn.core.tool_execution_controller import ToolExecutionController
from latka_jazn.tools.package_export import build_package_plan, export_package


def export_payload(
    *,
    root: Path,
    profile: str,
    output: Path | None,
    confirm_private_data: str | None,
    preview_only: bool,
    source_origin: str = "run.py export",
) -> dict[str, Any]:
    mode = {"source-safe": "github_source_safe"}.get(profile, profile)
    preview_output = output if output is not None else root / "exports" / ".preview.zip"
    package_plan = build_package_plan(root, mode, preview_output)
    candidate_paths = [path for path, _ in package_plan]
    gate = PrivateDataExportGate(root / "workspace_runtime/private_export_confirmations.json")
    preview = gate.preview(profile=profile, paths=candidate_paths)
    if preview_only:
        token = gate.issue_confirmation(preview) if preview.requires_confirmation else None
        return {
            "ok": not preview.blocked,
            "preview": preview.to_dict(),
            "confirmation_token": token,
            "artifact_created": False,
            "tool_execution": None,
        }

    if preview.blocked:
        return {
            "ok": False,
            "preview": preview.to_dict(),
            "confirmation": {
                "allowed": False,
                "reason": "source_safe_contains_private_or_high_risk_data",
                "blocked_items": list(preview.blocked_items),
            },
            "artifact_created": False,
            "tool_execution": None,
        }

    confirmation_evidence = "profile_does_not_require_second_confirmation"
    if preview.requires_confirmation:
        decision = gate.consume_confirmation(token=confirm_private_data or "", preview=preview)
        if not decision.get("allowed"):
            return {
                "ok": False,
                "preview": preview.to_dict(),
                "confirmation": decision,
                "artifact_created": False,
                "tool_execution": None,
            }
        confirmation_evidence = str(decision.get("reason") or "one_time_private_confirmation_consumed")

    controller = ToolExecutionController()
    plan = controller.plan(
        tool_name="package_export",
        action="create_export_package",
        source_kind="user_document",
        source_content=f"Explicit operator CLI export request for profile {profile}.",
        source_origin=source_origin,
        actor="operator_cli",
        reason="explicit_export_command",
        write_action=True,
        user_confirmed=True,
    )
    execution = controller.execute(plan, export_package, root, mode, output)
    if not execution.ok or execution.result is None:
        return {
            "ok": False,
            "preview": preview.to_dict(),
            "artifact_created": False,
            "confirmation_evidence": confirmation_evidence,
            "tool_execution": execution.to_dict(),
        }
    report = execution.result
    return {
        "ok": True,
        "preview": preview.to_dict(),
        "report": report.to_dict(),
        "artifact_created": True,
        "confirmation_evidence": confirmation_evidence,
        "tool_execution": execution.to_dict(),
    }
