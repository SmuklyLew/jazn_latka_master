from __future__ import annotations

from pathlib import Path
from typing import Any

from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.core.startup_contract import build_startup_status
from latka_jazn.core.package_integrity_manifest import package_integrity_manifest_status


class PackageRuntimeStatusHandler:
    """Odpowiada o paczce i runtime bez wymyślania wyników CRC.

    Szczegółowe wyniki archiwum są używane tylko wtedy, gdy orkiestrator
    przekaże ``package_validation`` z faktycznego testu. Sam fakt działającego
    runtime nie jest równoważny pełnej weryfikacji wszystkich części ZIP.
    """

    name = "PackageRuntimeStatusHandler"
    route = "package_runtime_status"
    handled_intents = ("package_runtime_status_question",)

    @staticmethod
    def _startup_status(ctx: dict[str, Any]) -> dict[str, Any]:
        supplied = ctx.get("startup_status")
        if isinstance(supplied, dict):
            return supplied
        cfg = ctx.get("config")
        if cfg is None:
            return {}
        try:
            return build_startup_status(cfg).to_dict()
        except Exception as exc:
            return {"status_quality": "degraded", "startup_status_error": repr(exc)}

    @staticmethod
    def _manifest_status(active_root: str | None) -> tuple[str, str | None]:
        if not active_root:
            return "unknown", None
        try:
            status = package_integrity_manifest_status(Path(active_root))
        except OSError:
            return "unreadable_nonblocking", None
        if not status.present:
            return "missing_nonblocking", status.path
        if not status.valid_json:
            return "invalid_nonblocking", status.path
        if status.source_name == "PACKAGE_INTEGRITY_MANIFEST.json":
            return "present_primary", status.path
        return "present_legacy_compatibility", status.path

    @staticmethod
    def _archive_summary(validation: dict[str, Any] | None) -> str:
        if not validation:
            return (
                "archive_integrity=not_verified_in_this_turn; nie utożsamiam aktywnego "
                "runtime z pełnym testem CRC/SHA wszystkich części ZIP"
            )
        fields = []
        for key in (
            "system_zip_crc_ok",
            "memory_zip_crc_ok",
            "parts_complete",
            "files_complete",
            "manifest_hashes_ok",
            "sqlite_integrity_ok",
        ):
            if key in validation:
                fields.append(f"{key}={validation[key]}")
        return "archive_integrity=" + (", ".join(fields) if fields else "validation_report_present_without_standard_fields")

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        intent = ctx.get("intent", "package_runtime_status_question")
        status = self._startup_status(ctx)
        active_root = str(status.get("active_root") or ctx.get("active_root") or "") or None
        version = status.get("runtime_version") or ctx.get("runtime_version") or "unknown"
        start_file = status.get("start_file") or ctx.get("start_file") or "unknown"
        quality = status.get("status_quality") or "unknown"
        manifest_status, manifest_path = self._manifest_status(active_root)
        validation = ctx.get("package_validation") if isinstance(ctx.get("package_validation"), dict) else None
        archive_summary = self._archive_summary(validation)
        known_issues = ctx.get("known_package_issues") or (validation or {}).get("known_issues") or []
        if isinstance(known_issues, str):
            known_issues = [known_issues]
        issues_text = "; ".join(str(item) for item in known_issues) if known_issues else "brak zgłoszonych problemów w przekazanym raporcie"

        body = (
            f"Status paczki/runtime: version={version}; active_root={active_root or 'unknown'}; "
            f"start_file={start_file}; status_quality={quality}; manifest={manifest_status}"
            + (f" ({manifest_path})" if manifest_path else "")
            + f". {archive_summary}. Znane problemy: {issues_text}. "
            "Source-origin: package_runtime_status_handler + startup_contract + jawnie przekazany package_validation. "
            "Granica prawdy: potwierdzam tylko dane odczytane z aktywnego runtime lub raportu testu; "
            "bez raportu nie deklaruję CRC, kompletności części ani zgodności hashy."
        )
        return RouteHandlerResult(
            self.name,
            self.route,
            body,
            intent=intent,
            data={
                "package_status": {
                    "runtime_version": version,
                    "active_root": active_root,
                    "start_file": start_file,
                    "status_quality": quality,
                    "manifest_status": manifest_status,
                    "manifest_path": manifest_path,
                },
                "archive_integrity": validation or {"status": "not_verified_in_this_turn"},
                "known_issues": known_issues,
                "startup_status": status,
            },
            file_sources=[
                {"path": "PACKAGE_INTEGRITY_MANIFEST.json", "status": manifest_status},
                {"path": "MANIFEST_CURRENT.json", "status": "transition_alias"},
                {"path": "latka_jazn/core/startup_contract.py"},
                {"path": "latka_jazn/core/handlers/package_runtime_status_handler.py"},
            ],
            required_components=ctx.get("required_components", []),
            satisfied_components=[
                "package_status",
                "runtime_status",
                "archive_integrity_boundary",
                "known_issues",
                "truth_boundary",
                "source_origin",
            ],
            confidence=0.90 if active_root else 0.72,
            source_origin_detail="package_runtime_status_handler",
            truth_boundary=(
                "Aktywny runtime nie jest sam w sobie dowodem pełnego CRC/SHA archiwum; "
                "wyniki archiwum wymagają jawnego package_validation."
            ),
        )
