from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable
import time

from latka_jazn.tools.chat_export_dedupe import plan_conversation, summarize_relations
from latka_jazn.tools.chat_export_models import ExportSourceInfo, ImportPlan, ImportResult
from latka_jazn.tools.chat_export_reader import ChatExportReader, sha256_file
from latka_jazn.tools.chat_export_store import ChatExportArchiveStore
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("chat_export_importer")
ProgressCallback = Callable[[dict[str, Any]], None]


def _source_kind(path: Path) -> str:
    if path.is_dir():
        return "directory"
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return "zip"
    if suffix == ".json":
        return "json"
    if suffix in {".html", ".htm"}:
        return "html"
    raise ValueError(f"unsupported export source: {path}")


def _fast_source_info(path: Path, source_sha256: str) -> ExportSourceInfo:
    return ExportSourceInfo(
        path=str(path),
        source_name=path.name,
        source_kind=_source_kind(path),
        sha256=source_sha256,
        size_bytes=path.stat().st_size if path.is_file() else 0,
        conversations_member=None,
        html_member=None,
        crc_checked=False,
        crc_ok=True,
    )


class ChatExportImporter:
    """Plan and import ChatGPT exports without promoting archive text to L3 memory."""

    def inspect(self, source: str | Path) -> dict[str, Any]:
        with ChatExportReader(source, verify_crc=True) as reader:
            return reader.inspect().to_dict()

    def plan(self, source: str | Path, database: str | Path) -> ImportPlan:
        source_path = Path(source).expanduser().resolve()
        source_hash = sha256_file(source_path) if source_path.is_file() else None
        with ChatExportArchiveStore(database) as store:
            if source_hash:
                existing = store.find_import_by_sha(source_hash)
                if existing:
                    return ImportPlan(
                        source=_fast_source_info(source_path, source_hash),
                        export_relation="identical_export_duplicate",
                        conversations=[],
                        duplicate_import_id=str(existing["import_id"]),
                    )
            with ChatExportReader(source_path, verify_crc=True) as reader:
                active = store.load_active_states()
                plans = [
                    plan_conversation(graph, active.get(graph.conversation_id))
                    for graph in reader.iter_graphs()
                ]
                return ImportPlan(
                    source=reader.info,
                    export_relation="new_export",
                    conversations=plans,
                )

    def import_one(
        self,
        source: str | Path,
        database: str | Path,
        *,
        dry_run: bool = False,
        full_validation: bool = True,
        progress_callback: ProgressCallback | None = None,
        progress_every_conversations: int = 5,
    ) -> ImportResult:
        started = time.monotonic()
        source_path = Path(source).expanduser().resolve()

        def emit_progress(stage: str, **details: Any) -> None:
            if progress_callback is not None:
                progress_callback({
                    "schema_version": SCHEMA_VERSION,
                    "event": "progress",
                    "stage": stage,
                    "source": str(source_path),
                    "elapsed_seconds": round(time.monotonic() - started, 6),
                    **details,
                })

        emit_progress("source_hash_started")
        source_hash = sha256_file(source_path) if source_path.is_file() else None
        emit_progress("source_hash_completed", source_sha256=source_hash)

        with ChatExportArchiveStore(database) as store:
            if source_hash:
                existing = store.find_import_by_sha(source_hash)
                if existing:
                    emit_progress("duplicate_export_detected", duplicate_import_id=str(existing["import_id"]))
                    info = _fast_source_info(source_path, source_hash)
                    if not dry_run:
                        store.register_duplicate_alias(str(existing["import_id"]), info)
                    return ImportResult(
                        import_id=str(existing["import_id"]),
                        source_sha256=source_hash,
                        status="identical_export_duplicate" if not dry_run else "dry_run_ok",
                        export_relation="identical_export_duplicate",
                        conversation_counters={},
                        database_path=str(store.path),
                        elapsed_seconds=round(time.monotonic() - started, 6),
                        validation=store.validate(full=False),
                    )

            emit_progress("source_validation_started")
            with ChatExportReader(source_path, verify_crc=True) as reader:
                emit_progress(
                    "source_validation_completed",
                    crc_checked=reader.info.crc_checked,
                    crc_ok=reader.info.crc_ok,
                )
                active = store.load_active_states()
                if dry_run:
                    plans = []
                    nodes_seen = messages_seen = 0
                    interval = max(1, int(progress_every_conversations))
                    for index, graph in enumerate(reader.iter_graphs(), 1):
                        plans.append(plan_conversation(graph, active.get(graph.conversation_id)))
                        nodes_seen += graph.node_count
                        messages_seen += graph.message_count
                        if index == 1 or index % interval == 0:
                            emit_progress(
                                "conversations_planned",
                                conversations=index,
                                nodes=nodes_seen,
                                messages=messages_seen,
                            )
                    emit_progress(
                        "planning_completed",
                        conversations=len(plans),
                        nodes=nodes_seen,
                        messages=messages_seen,
                    )
                    return ImportResult(
                        import_id=None,
                        source_sha256=reader.info.sha256,
                        status="dry_run_ok",
                        export_relation="new_export",
                        conversation_counters=summarize_relations(plans),
                        database_path=str(store.path),
                        elapsed_seconds=round(time.monotonic() - started, 6),
                        validation=store.validate(full=False),
                    )

                relation_plans = []
                writes: dict[str, int] = {}
                conversation_count = node_count = message_count = 0
                with store.transaction():
                    existing = store.find_import_by_sha(reader.info.sha256)
                    if existing:
                        raise RuntimeError("duplicate_import_race")
                    import_id = store.begin_import(reader.info)
                    active = store.load_active_states()
                    interval = max(1, int(progress_every_conversations))
                    emit_progress("transaction_started", import_id=import_id)
                    for graph in reader.iter_graphs():
                        plan = plan_conversation(graph, active.get(graph.conversation_id))
                        relation_plans.append(plan)
                        delta = store.store_graph(import_id, graph, plan)
                        for key, value in delta.items():
                            writes[key] = writes.get(key, 0) + int(value)
                        conversation_count += 1
                        node_count += graph.node_count
                        message_count += graph.message_count
                        if conversation_count == 1 or conversation_count % interval == 0:
                            emit_progress(
                                "conversations_imported",
                                import_id=import_id,
                                conversations=conversation_count,
                                nodes=node_count,
                                messages=message_count,
                                writes=dict(writes),
                            )
                    relation_counts = summarize_relations(relation_plans)
                    report = {
                        "schema_version": SCHEMA_VERSION,
                        "relations": relation_counts,
                        "writes": writes,
                        "truth_boundary": (
                            "Import tworzy źródłowe archiwum i indeks. Nie tworzy automatycznie "
                            "wspomnień, emocji, refleksji ani kanonu książki."
                        ),
                    }
                    store.finish_import(
                        import_id,
                        conversation_count=conversation_count,
                        node_count=node_count,
                        message_count=message_count,
                        report=report,
                    )
                emit_progress(
                    "transaction_committed",
                    import_id=import_id,
                    conversations=conversation_count,
                    nodes=node_count,
                    messages=message_count,
                )
                emit_progress(
                    "database_validation_started",
                    mode="integrity_check" if full_validation else "quick_check",
                )
                validation = store.validate(full=full_validation)
                emit_progress("database_validation_completed", validation_ok=bool(validation.get("ok")))
                errors = [] if validation.get("ok") else ["database_validation_failed"]
                return ImportResult(
                    import_id=import_id,
                    source_sha256=reader.info.sha256,
                    status="imported" if not errors else "imported_validation_failed",
                    export_relation="new_export",
                    conversation_counters=relation_counts,
                    inserted_conversations=writes.get("conversations_inserted", 0),
                    updated_conversations=writes.get("conversations_updated", 0),
                    inserted_nodes=writes.get("nodes_inserted", 0),
                    inserted_fts_documents=writes.get("fts_inserted", 0),
                    inserted_asset_references=writes.get("assets_upserted", 0),
                    database_path=str(store.path),
                    elapsed_seconds=round(time.monotonic() - started, 6),
                    validation=validation,
                    errors=errors,
                    warnings=(
                        [f"conflicts_recorded={writes.get('conflicts', 0)}"]
                        if writes.get("conflicts", 0)
                        else []
                    ),
                )

    def import_many(
        self,
        sources: Iterable[str | Path],
        database: str | Path,
        *,
        dry_run: bool = False,
        full_validation: bool = True,
        worker_timeout_seconds: float = 300.0,
    ) -> list[ImportResult]:
        """Import each large export in a fresh Python process."""
        import json
        import os
        import subprocess
        import sys

        paths = [Path(source).expanduser().resolve() for source in sources]
        paths.sort(key=lambda path: path.stat().st_size if path.is_file() else 0, reverse=True)
        results: list[ImportResult] = []
        for path in paths:
            command = [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "latka_jazn.tools.chat_export_worker",
                "--source",
                str(path),
                "--database",
                str(Path(database).expanduser().resolve()),
            ]
            if dry_run:
                command.append("--dry-run")
            if not full_validation:
                command.append("--quick-validation")
            env = dict(os.environ)
            env.setdefault("PYTHONUTF8", "1")
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=max(1.0, float(worker_timeout_seconds)),
                check=False,
            )
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            if completed.returncode != 0 or not lines:
                results.append(ImportResult(
                    import_id=None,
                    source_sha256=sha256_file(path) if path.is_file() else "",
                    status="worker_failed",
                    export_relation="invalid_export",
                    conversation_counters={},
                    database_path=str(Path(database).expanduser().resolve()),
                    errors=[
                        f"worker_exit_code={completed.returncode}",
                        completed.stderr.strip() or "worker produced no JSON result",
                    ],
                ))
                continue
            payload = json.loads(lines[-1])
            payload.pop("ok", None)
            results.append(ImportResult(**payload))
        return results
