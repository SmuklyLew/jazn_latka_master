from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
import json
import re
import sqlite3
import tempfile

from latka_jazn.tools.chat_export_importer import ChatExportImporter
from latka_jazn.tools.chat_export_reader import ChatExportReader
from latka_jazn.tools.memory_rebuild_catalog import CatalogStore
from latka_jazn.tools.memory_rebuild_common import DATABASE_FILENAMES
from latka_jazn.tools.memory_rebuild_coordinator import MemoryRebuildCoordinator, detect_source
from latka_jazn.tools.memory_rebuild_journal import JournalReader
from latka_jazn.tools.memory_restore_storage import (
    backup_database_set, compare_database_sets, database_set_summary, resolve_database_paths,
)
from latka_jazn.tools.memory_restore_types import (
    MemoryRestorePlan, MemoryRestoreSettings, ProgressCallback, RestoreSource, SCHEMA_VERSION,
    atomic_json, confirmation_token, discover_restore_sources, is_known_non_memory_source,
    journal_inspection_is_plausible, target_preflight, utc_stamp,
)

class MemoryRestoreOrchestrator:
    def __init__(self, settings: MemoryRestoreSettings, *, tool_root: str | Path | None = None, callback: ProgressCallback | None = None) -> None:
        self.settings = settings.normalized()
        self.tool_root = Path(tool_root).expanduser().resolve() if tool_root else Path(__file__).resolve().parents[2]
        self.callback = callback
        self.coordinator = MemoryRebuildCoordinator(self.settings.target_root)
        self.importer = ChatExportImporter()
        self._event_stream = None
        self.report_dir: Path | None = None

    def emit(self, event: dict[str, Any]) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "timestamp_utc": datetime.now(timezone.utc).isoformat(), **event}
        if self._event_stream is not None:
            self._event_stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            self._event_stream.flush()
        if self.callback:
            self.callback(payload)

    def discover(self) -> list[RestoreSource]:
        return discover_restore_sources(self.settings.source_directory, recursive=self.settings.recursive_scan)

    def plan(self, selected_sources: Sequence[str | Path]) -> MemoryRestorePlan:
        selected = [Path(item).expanduser().resolve() for item in selected_sources]
        preflight = target_preflight(self.settings, tool_root=self.tool_root)
        current = database_set_summary(self.settings.target_root)
        chats: list[dict[str, Any]] = []
        journals: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="memory-restore-plan-") as temporary:
            planning_db = Path(temporary) / DATABASE_FILENAMES["archive_chats"]
            current_archive = resolve_database_paths(self.settings.target_root)["archive_chats"]
            if current_archive.is_file():
                source_con = sqlite3.connect(f"file:{current_archive}?mode=ro", uri=True)
                target_con = sqlite3.connect(planning_db)
                try:
                    source_con.backup(target_con)
                finally:
                    target_con.close(); source_con.close()
            for index, source in enumerate(selected, 1):
                self.emit({"event": "source_inspection_started", "index": index, "total": len(selected), "source": str(source)})
                try:
                    detected = detect_source(source)
                    if detected["kind"] == "chat_export" and detected.get("canonical_conversations_available"):
                        plan = self.importer.plan(source, planning_db).to_dict()
                        plan.pop("conversations", None)
                        chats.append({**detected, "plan": plan})
                    elif detected["kind"] == "journal":
                        if is_known_non_memory_source(source):
                            rejected.append({**detected, "ok": False, "reason": "known_non_memory_json_sidecar"})
                        else:
                            inspection = JournalReader(source).inspect()
                            if journal_inspection_is_plausible(source, inspection):
                                journals.append({**detected, "inspection": inspection})
                            else:
                                rejected.append({**detected, "ok": False, "reason": "json_does_not_look_like_journal", "inspection": inspection})
                    else:
                        rejected.append({**detected, "ok": False, "reason": "chat_export_without_conversations_json"})
                except Exception as exc:
                    rejected.append({"path": str(source), "ok": False, "error_type": type(exc).__name__, "error": str(exc)})
                self.emit({"event": "source_inspection_completed", "index": index, "total": len(selected), "source": str(source)})
        return MemoryRestorePlan(self.settings, selected, chats, journals, rejected, preflight, current)

    def _import_chat_source(self, source: Path) -> dict[str, Any]:
        """Import one export with live progress while preserving coordinator catalog semantics."""
        with ChatExportReader(source, verify_crc=True) as reader:
            if not reader.info.conversations_member:
                raise ValueError("chat.html alone cannot be imported; conversations.json is required")
            details = asdict(reader.info)
            source_hash = reader.info.sha256
            source_size = reader.info.size_bytes
        with CatalogStore(self.coordinator.paths.import_catalog) as catalog:
            source_id = catalog.source(source, source_hash, "chat_export", source_size, details)
            operation = catalog.begin("import_chats", source_id, DATABASE_FILENAMES["archive_chats"])
            try:
                imported = self.importer.import_one(
                    source,
                    self.coordinator.paths.archive_chats,
                    dry_run=False,
                    full_validation=self.settings.full_validation,
                    progress_callback=self.emit,
                    progress_every_conversations=self.settings.progress_every_conversations,
                ).to_dict()
                imported["ok"] = imported.get("validation", {}).get("ok", True)
                imported["operation_id"] = operation
                catalog.finish(operation, imported, "verified" if imported["ok"] else "needs_review")
                return {
                    "ok": imported["ok"],
                    "database": str(self.coordinator.paths.archive_chats),
                    "dry_run": False,
                    "results": [imported],
                    "automatic_l2": False,
                    "automatic_l3": False,
                }
            except BaseException as exc:
                catalog.fail(operation, exc)
                raise

    def _prepare_report_dir(self) -> Path:
        root = Path(self.settings.target_root)
        run_id = f"restore_{utc_stamp()}"
        report_dir = (
            root / "workspace_runtime" / "memory_restore" / run_id
            if self.settings.mode == "system"
            else root / "reports" / "memory_restore" / run_id
        )
        report_dir.mkdir(parents=True, exist_ok=False)
        self.report_dir = report_dir
        return report_dir

    def _report(self, name: str, payload: Any) -> Path:
        if self.report_dir is None:
            raise RuntimeError("report directory is not initialized")
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "report"
        path = self.report_dir / f"{safe}.json"
        atomic_json(path, payload)
        return path

    def run(
        self,
        selected_sources: Sequence[str | Path],
        *,
        confirmation: str,
        prepared_plan: MemoryRestorePlan | None = None,
    ) -> dict[str, Any]:
        expected = confirmation_token(self.settings)
        if confirmation != expected:
            raise PermissionError(f"explicit confirmation required: {expected}")
        plan = prepared_plan or self.plan(selected_sources)
        selected_resolved = [Path(item).expanduser().resolve() for item in selected_sources]
        if [Path(item).resolve() for item in plan.selected_sources] != selected_resolved:
            raise ValueError("prepared plan does not match selected sources")
        refreshed_preflight = target_preflight(self.settings, tool_root=self.tool_root)
        plan.target_preflight = refreshed_preflight
        if not plan.ok:
            return {"ok": False, "plan": plan.to_dict(), "error": "restore_plan_blocked"}
        report_dir = self._prepare_report_dir()
        event_path = report_dir / "events.jsonl"
        summary: dict[str, Any] = {
            "ok": False, "schema_version": SCHEMA_VERSION, "mode": self.settings.mode,
            "target_root": self.settings.target_root, "report_dir": str(report_dir),
            "automatic_experience": False, "automatic_l2": False, "automatic_l3": False,
            "steps": [], "errors": [],
        }
        atomic_json(report_dir / "settings.json", self.settings.to_dict())
        atomic_json(report_dir / "plan.json", plan.to_dict())
        with event_path.open("a", encoding="utf-8") as stream:
            self._event_stream = stream
            try:
                self.emit({"event": "restore_started", "source_count": len(selected_sources), "target_root": self.settings.target_root})
                if self.settings.create_backup:
                    backup_root = (
                        Path(self.settings.target_root) / "workspace_runtime" / "memory_restore" / "backups" / f"before_{report_dir.name}"
                        if self.settings.mode == "system"
                        else Path(self.settings.target_root) / "backups" / f"before_{report_dir.name}"
                    )
                    backup = backup_database_set(self.settings.target_root, backup_root, self.emit)
                    self._report("01_backup", backup); summary["steps"].append({"name": "backup", "ok": backup["ok"], "path": str(backup_root)})
                    if not backup["ok"]:
                        raise RuntimeError("database_backup_failed")
                init = self.coordinator.init(); self._report("00_init", init); summary["steps"].append({"name": "init", "ok": init["ok"]})
                if not init["ok"]:
                    raise RuntimeError("database_initialization_failed")
                chat_results = []
                for index, item in enumerate(plan.chats, 1):
                    source = Path(item["path"])
                    self.emit({"event": "chat_import_started", "index": index, "total": len(plan.chats), "source": str(source)})
                    try:
                        result = self._import_chat_source(source)
                        chat_results.append(result)
                        self._report(f"chat_{index:03d}_{source.stem}", result)
                        if not result.get("ok"):
                            raise RuntimeError(f"chat_import_failed:{source}")
                        if self.settings.verify_after_each:
                            verification = self.coordinator.verify(full=self.settings.full_validation)
                            self._report(f"verify_after_chat_{index:03d}_{source.stem}", verification)
                            if not verification.get("ok"):
                                raise RuntimeError(f"verification_failed_after:{source}")
                    except Exception as exc:
                        error = {"source": str(source), "error_type": type(exc).__name__, "error": str(exc)}
                        summary["errors"].append(error); self.emit({"event": "chat_import_failed", **error})
                        if not self.settings.continue_on_error:
                            raise
                    else:
                        self.emit({"event": "chat_import_completed", "index": index, "total": len(plan.chats), "source": str(source)})
                summary["steps"].append({"name": "import_chats", "ok": not any("chat_import" in item.get("error", "") for item in summary["errors"]), "count": len(chat_results)})
                journal_results = []
                for index, item in enumerate(plan.journals, 1):
                    source = Path(item["path"])
                    self.emit({"event": "journal_import_started", "index": index, "total": len(plan.journals), "source": str(source)})
                    try:
                        dry = self.coordinator.import_journal(source, dry_run=True)
                        self._report(f"journal_{index:03d}_{source.stem}_dry_run", dry)
                        if not dry.get("ok", True):
                            raise RuntimeError(f"journal_dry_run_failed:{source}")
                        result = self.coordinator.import_journal(source, dry_run=False)
                        journal_results.append(result); self._report(f"journal_{index:03d}_{source.stem}", result)
                        if not result.get("ok", True):
                            raise RuntimeError(f"journal_import_failed:{source}")
                        if self.settings.verify_after_each:
                            verification = self.coordinator.verify(full=self.settings.full_validation)
                            self._report(f"verify_after_journal_{index:03d}_{source.stem}", verification)
                            if not verification.get("ok"):
                                raise RuntimeError(f"verification_failed_after:{source}")
                    except Exception as exc:
                        error = {"source": str(source), "error_type": type(exc).__name__, "error": str(exc)}
                        summary["errors"].append(error); self.emit({"event": "journal_import_failed", **error})
                        if not self.settings.continue_on_error:
                            raise
                    else:
                        self.emit({"event": "journal_import_completed", "index": index, "total": len(plan.journals), "source": str(source)})
                summary["steps"].append({"name": "import_journals", "ok": True, "count": len(journal_results)})
                verification = self.coordinator.verify(full=True); self._report("90_verify_full", verification)
                summary["steps"].append({"name": "verify_full", "ok": verification["ok"]})
                if not verification["ok"]:
                    raise RuntimeError("final_database_verification_failed")
                if self.settings.audit_classifiers:
                    audit = self.coordinator.audit_classifiers(limit=100); self._report("91_audit_classifiers", audit)
                    summary["steps"].append({"name": "audit_classifiers", "ok": audit["ok"]})
                if self.settings.reclassify_journal_dry_run and plan.journals:
                    reclassify = self.coordinator.reclassify_journal(dry_run=True, limit=100); self._report("92_reclassify_journal_dry_run", reclassify)
                    summary["steps"].append({"name": "reclassify_journal_dry_run", "ok": reclassify.get("ok", True), "changed": reclassify.get("changed", 0)})
                    if self.settings.apply_reclassification and reclassify.get("changed"):
                        applied = self.coordinator.reclassify_journal(dry_run=False, limit=100); self._report("93_reclassify_journal", applied)
                        summary["steps"].append({"name": "reclassify_journal", "ok": applied.get("ok", True), "changed": applied.get("changed", 0)})
                if self.settings.analyse_topics and plan.chats:
                    from latka_jazn.tools.chat_export_topics import ChatExportTopicStore
                    with ChatExportTopicStore(self.coordinator.paths.archive_chats) as topics:
                        analysis = {"ok": True, "analysis": topics.analyse_all(force=self.settings.force_topics), "summary": topics.summary(), "automatic_l2": False, "automatic_l3": False}
                    self._report("94_analyse_topics", analysis); summary["steps"].append({"name": "analyse_topics", "ok": True})
                if self.settings.candidate_limit > 0:
                    candidates = self.coordinator.build_experience_candidates("all", self.settings.candidate_limit)
                    self._report("95_candidate_sample", candidates); summary["steps"].append({"name": "candidate_sample", "ok": candidates["ok"], "limit": self.settings.candidate_limit})
                if self.settings.baseline_roots:
                    comparison = compare_database_sets(self.settings.target_root, self.settings.baseline_roots)
                    self._report("96_baseline_comparison", comparison); summary["steps"].append({"name": "baseline_comparison", "ok": comparison["ok"]})
                final_status = self.coordinator.status(); self._report("99_status", final_status)
                summary["status"] = final_status
                summary["ok"] = not summary["errors"] and all(step.get("ok", True) for step in summary["steps"])
                self.emit({"event": "restore_completed", "ok": summary["ok"], "report_dir": str(report_dir)})
            except Exception as exc:
                summary["errors"].append({"error_type": type(exc).__name__, "error": str(exc)})
                self.emit({"event": "restore_failed", "error_type": type(exc).__name__, "error": str(exc)})
            finally:
                self._event_stream = None
                atomic_json(report_dir / "summary.json", summary)
        return summary

__all__ = ["MemoryRestoreOrchestrator"]
