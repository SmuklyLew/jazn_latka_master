#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from latka_jazn.tools.chat_export_importer import ChatExportImporter
from latka_jazn.tools.chat_export_reader import sha256_file
from latka_jazn.tools.chat_export_store import ChatExportArchiveStore
from latka_jazn.tools.chat_export_topics import ChatExportTopicStore
from latka_jazn.tools.sqlite_archive_snapshot import create_sqlite_snapshot


ProgressCallback = Callable[[dict[str, Any]], None]


def emit(payload: Any, *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key}: {value}")
    else:
        print(payload)


def existing_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise argparse.ArgumentTypeError(f"path does not exist: {path}")
    return path


def database_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory_import_to_db",
        description="Bezstratny import eksportów ChatGPT do deduplikowanego archiwum SQLite.",
    )
    parser.add_argument("--json", action="store_true", help="zwracaj wynik maszynowy JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="zbadaj eksport bez zapisu")
    inspect.add_argument("sources", nargs="+", type=existing_path)

    plan = sub.add_parser("plan", help="porównaj eksport z bazą bez zapisu")
    plan.add_argument("--database", required=True, type=database_path)
    plan.add_argument("sources", nargs="+", type=existing_path)
    plan.add_argument("--details", action="store_true", help="dołącz plan każdej rozmowy")

    imp = sub.add_parser("import", help="importuj eksporty transakcyjnie")
    imp.add_argument("--database", required=True, type=database_path)
    imp.add_argument("sources", nargs="+", type=existing_path)
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument("--worker-timeout", type=float, default=300.0)
    imp.add_argument("--continue-on-error", action="store_true")
    imp.add_argument("--no-final-full-check", action="store_true")
    imp.add_argument(
        "--snapshot-before",
        type=database_path,
        help="przed pierwszym zapisem utwórz zweryfikowany snapshot istniejącej bazy",
    )
    imp.add_argument(
        "--snapshot-full-check",
        action="store_true",
        help="przed atomową publikacją snapshotu wykonaj pełny integrity_check",
    )
    imp.add_argument(
        "--progress-every",
        type=int,
        default=5,
        help="worker emituje postęp co N rozmów",
    )
    imp.add_argument("--no-progress", action="store_true", help="nie pokazuj etapów workera")

    verify = sub.add_parser("verify", help="sprawdź integralność bazy")
    verify.add_argument("--database", required=True, type=database_path)
    verify.add_argument("--quick", action="store_true")

    exports = sub.add_parser("exports", help="pokaż znane eksporty i aliasy")
    exports.add_argument("--database", required=True, type=database_path)

    conversations = sub.add_parser("conversations", help="pokaż rozmowy w archiwum")
    conversations.add_argument("--database", required=True, type=database_path)
    conversations.add_argument("--limit", type=int, default=100)
    conversations.add_argument("--offset", type=int, default=0)

    branches = sub.add_parser("branches", help="pokaż gałęzie jednej rozmowy")
    branches.add_argument("--database", required=True, type=database_path)
    branches.add_argument("conversation_id")

    search = sub.add_parser("search", help="wyszukaj tekst przez FTS5")
    search.add_argument("--database", required=True, type=database_path)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)

    topics = sub.add_parser("topics", help="analizuj i podsumuj tematy rozmów")
    topics.add_argument("--database", required=True, type=database_path)
    topics.add_argument("--force", action="store_true", help="przelicz także świeże profile")
    topics.add_argument("--limit", type=int)
    topics.add_argument("--summary-only", action="store_true", help="nie uruchamiaj analizy, pokaż istniejący indeks")

    review = sub.add_parser("review", help="zarządzaj kolejką ręcznego przeglądu pamięci")
    review.add_argument("--database", required=True, type=database_path)
    review.add_argument("--queue-domains", nargs="+", metavar="DOMAIN")
    review.add_argument("--reason", default="manual archive review")
    review.add_argument("--candidate-type", default="long_term_review")
    review.add_argument("--status", default="pending_review", help="status do wyświetlenia; 'all' pokazuje wszystkie")
    review.add_argument("--limit", type=int, default=200)

    return parser


def command_inspect(args: argparse.Namespace) -> int:
    importer = ChatExportImporter()
    reports = []
    for index, source in enumerate(args.sources, 1):
        if not args.json:
            print(f"[{index}/{len(args.sources)}] Sprawdzam {source.name}…", file=sys.stderr, flush=True)
        try:
            reports.append(importer.inspect(source))
        except Exception as exc:
            reports.append({"path": str(source), "ok": False, "error_type": type(exc).__name__, "error": str(exc)})
    emit({"reports": reports, "ok": all(item.get("ok") for item in reports)}, json_mode=args.json)
    return 0 if all(item.get("ok") for item in reports) else 2


def command_plan(args: argparse.Namespace) -> int:
    importer = ChatExportImporter()
    reports = []
    for index, source in enumerate(args.sources, 1):
        if not args.json:
            print(f"[{index}/{len(args.sources)}] Planuję {source.name}…", file=sys.stderr, flush=True)
        try:
            plan = importer.plan(source, args.database)
            data = plan.to_dict()
            if not args.details:
                data.pop("conversations", None)
            reports.append(data)
        except Exception as exc:
            reports.append({"source": str(source), "ok": False, "error_type": type(exc).__name__, "error": str(exc)})
    emit({"plans": reports, "ok": all(item.get("ok") for item in reports)}, json_mode=args.json)
    return 0 if all(item.get("ok") for item in reports) else 2


def format_progress(payload: dict[str, Any]) -> str:
    stage = str(payload.get("stage") or "progress")
    elapsed = payload.get("elapsed_seconds")
    parts = [stage]
    for key in ("conversations", "nodes", "messages"):
        if payload.get(key) is not None:
            parts.append(f"{key}={payload[key]}")
    if payload.get("source_sha256"):
        parts.append(f"sha256={str(payload['source_sha256'])[:12]}…")
    if elapsed is not None:
        parts.append(f"czas={elapsed}s")
    return " | ".join(parts)


def _drain_stream(stream: Any, name: str, events: "queue.Queue[tuple[str, str | None]]") -> None:
    try:
        for line in iter(stream.readline, ""):
            events.put((name, line.rstrip("\r\n")))
    finally:
        events.put((name, None))


def run_worker(
    source: Path,
    database: Path,
    *,
    dry_run: bool,
    timeout: float,
    progress_every: int = 5,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "latka_jazn.tools.chat_export_worker",
        "--source",
        str(source),
        "--database",
        str(database),
        "--quick-validation",
        "--progress-jsonl",
        "--progress-every",
        str(max(1, int(progress_every))),
    ]
    if dry_run:
        command.append("--dry-run")
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
    )
    assert process.stdout is not None and process.stderr is not None
    events: "queue.Queue[tuple[str, str | None]]" = queue.Queue()
    threads = [
        threading.Thread(target=_drain_stream, args=(process.stdout, "stdout", events), daemon=True),
        threading.Thread(target=_drain_stream, args=(process.stderr, "stderr", events), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + max(1.0, float(timeout))
    closed = set()
    final_payload: dict[str, Any] | None = None
    stderr_lines: list[str] = []
    try:
        while len(closed) < 2 or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait(timeout=10)
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stream_name, line = events.get(timeout=min(0.2, remaining))
            except queue.Empty:
                continue
            if line is None:
                closed.add(stream_name)
                continue
            if stream_name == "stderr":
                stderr_lines.append(line)
                continue
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                stderr_lines.append(f"worker stdout (non-json): {line}")
                continue
            if payload.get("event") == "progress":
                if progress_callback is not None:
                    progress_callback(payload)
            else:
                final_payload = payload
        return_code = process.wait(timeout=max(1.0, deadline - time.monotonic()))
    finally:
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except Exception:
                pass
        for thread in threads:
            thread.join(timeout=1.0)

    if return_code or final_payload is None:
        return {
            "ok": False,
            "status": "worker_failed",
            "source": str(source),
            "source_sha256": sha256_file(source) if source.is_file() else None,
            "worker_exit_code": return_code,
            "stderr": "\n".join(stderr_lines).strip(),
        }
    if stderr_lines:
        final_payload.setdefault("worker_warnings", stderr_lines)
    return final_payload


def command_import(args: argparse.Namespace) -> int:
    sources = sorted(
        args.sources,
        key=lambda path: path.stat().st_size if path.is_file() else 0,
        reverse=True,
    )
    args.database.parent.mkdir(parents=True, exist_ok=True)
    snapshot: dict[str, Any] | None = None
    if args.snapshot_before is not None:
        if args.dry_run:
            raise ValueError("--snapshot-before cannot be combined with --dry-run")
        if not args.database.is_file():
            raise FileNotFoundError("--snapshot-before requires an existing database")
        if args.snapshot_before.resolve() == args.database.resolve():
            raise ValueError("snapshot path must differ from database path")
        if not args.json:
            print(f"Tworzę snapshot: {args.snapshot_before}", file=sys.stderr, flush=True)
        report = create_sqlite_snapshot(
            args.database,
            args.snapshot_before,
            full_integrity_check=args.snapshot_full_check,
            progress=(
                None
                if args.no_progress
                else lambda done, total: print(
                    f"    snapshot {done}/{total} stron",
                    file=sys.stderr,
                    flush=True,
                )
            ),
        )
        snapshot = report.to_dict()
        if not report.ok:
            raise RuntimeError("snapshot validation failed")

    def show_progress(payload: dict[str, Any]) -> None:
        if args.no_progress:
            return
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)
        else:
            print(f"    {format_progress(payload)}", file=sys.stderr, flush=True)

    results = []
    started = time.monotonic()
    for index, source in enumerate(sources, 1):
        if not args.json:
            print(f"[{index}/{len(sources)}] Importuję {source.name}…", file=sys.stderr, flush=True)
        source_started = time.monotonic()
        try:
            result = run_worker(
                source,
                args.database,
                dry_run=args.dry_run,
                timeout=args.worker_timeout,
                progress_every=args.progress_every,
                progress_callback=show_progress,
            )
        except subprocess.TimeoutExpired:
            result = {
                "ok": False,
                "status": "worker_timeout",
                "source": str(source),
                "timeout_seconds": args.worker_timeout,
            }
        result["wall_seconds"] = round(time.monotonic() - source_started, 6)
        results.append(result)
        if not args.json:
            print(
                f"    status={result.get('status')} czas={result['wall_seconds']}s "
                f"relacje={result.get('conversation_counters', {})}",
                file=sys.stderr,
                flush=True,
            )
        if not result.get("ok") and not args.continue_on_error:
            break

    validation: dict[str, Any] = {}
    if args.database.exists() and not args.dry_run:
        with ChatExportArchiveStore(args.database) as store:
            validation = store.validate(full=not args.no_final_full_check)
    ok = bool(results) and all(item.get("ok") for item in results) and (not validation or validation.get("ok"))
    payload = {
        "ok": ok,
        "database": str(args.database),
        "dry_run": args.dry_run,
        "snapshot_before": snapshot,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "results": results,
        "final_validation": validation,
    }
    emit(payload, json_mode=args.json)
    return 0 if ok else 2


def command_verify(args: argparse.Namespace) -> int:
    if not args.database.exists():
        emit({"ok": False, "error": "database_missing", "path": str(args.database)}, json_mode=args.json)
        return 2
    with ChatExportArchiveStore(args.database) as store:
        result = store.validate(full=not args.quick)
    emit(result, json_mode=args.json)
    return 0 if result["ok"] else 2


def command_exports(args: argparse.Namespace) -> int:
    with ChatExportArchiveStore(args.database) as store:
        sources = [dict(row) for row in store.con.execute(
            "SELECT * FROM import_sources ORDER BY COALESCE(completed_at_utc,started_at_utc),source_name"
        )]
        aliases = [dict(row) for row in store.con.execute(
            "SELECT * FROM import_source_aliases ORDER BY observed_at_utc,source_name"
        )]
    emit({"ok": True, "sources": sources, "aliases": aliases}, json_mode=args.json)
    return 0


def command_conversations(args: argparse.Namespace) -> int:
    with ChatExportArchiveStore(args.database) as store:
        rows = [dict(row) for row in store.con.execute(
            """SELECT conversation_id,title,create_time,update_time,current_node_id,node_count,
                      message_count,current_path_count,branch_point_count,revision
                 FROM conversations ORDER BY COALESCE(update_time,create_time) DESC
                LIMIT ? OFFSET ?""",
            (max(1, args.limit), max(0, args.offset)),
        )]
    emit({"ok": True, "conversations": rows}, json_mode=args.json)
    return 0


def command_branches(args: argparse.Namespace) -> int:
    with ChatExportArchiveStore(args.database) as store:
        rows = [dict(row) for row in store.con.execute(
            """SELECT node_id,parent_node_id,message_id,role,create_time,timestamp_status,
                      structural_ordinal,on_current_path,branch_id,has_assets
                 FROM nodes WHERE conversation_id=? ORDER BY structural_ordinal""",
            (args.conversation_id,),
        )]
    exists = bool(rows)
    emit({"ok": exists, "conversation_id": args.conversation_id, "nodes": rows}, json_mode=args.json)
    return 0 if exists else 2


def command_search(args: argparse.Namespace) -> int:
    with ChatExportArchiveStore(args.database) as store:
        rows = store.search(args.query, limit=args.limit)
    emit({"ok": True, "query": args.query, "results": rows}, json_mode=args.json)
    return 0


def command_topics(args: argparse.Namespace) -> int:
    if not args.database.exists():
        emit({"ok": False, "error": "database_missing", "path": str(args.database)}, json_mode=args.json)
        return 2
    with ChatExportTopicStore(args.database) as topics:
        analysis = None if args.summary_only else topics.analyse_all(force=args.force, limit=args.limit)
        summary = topics.summary()
        validation = topics.archive.validate(full=False)
    payload = {
        "ok": bool(validation.get("ok")),
        "database": str(args.database),
        "analysis": analysis,
        "summary": summary,
        "validation": validation,
        "truth_boundary": "Klasyfikacja tematów nie promuje rozmów do pamięci krótkotrwałej ani długotrwałej.",
    }
    emit(payload, json_mode=args.json)
    return 0 if payload["ok"] else 2


def command_review(args: argparse.Namespace) -> int:
    if not args.database.exists():
        emit({"ok": False, "error": "database_missing", "path": str(args.database)}, json_mode=args.json)
        return 2
    with ChatExportTopicStore(args.database) as topics:
        inserted = 0
        if args.queue_domains:
            topics.analyse_all(force=False)
            inserted = topics.queue_domains(
                args.queue_domains,
                reason=args.reason,
                candidate_type=args.candidate_type,
            )
        status = None if str(args.status).lower() == "all" else args.status
        review_queue = topics.review_queue(status=status, limit=args.limit)
        validation = topics.archive.validate(full=False)
    payload = {
        "ok": bool(validation.get("ok")),
        "database": str(args.database),
        "inserted": inserted,
        "queue": review_queue,
        "validation": validation,
        "truth_boundary": (
            "Kolejka zawiera wyłącznie kandydatów do ręcznego przeglądu. "
            "Nie oznacza promocji do L2 ani L3."
        ),
    }
    emit(payload, json_mode=args.json)
    return 0 if payload["ok"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "inspect": command_inspect,
        "plan": command_plan,
        "import": command_import,
        "verify": command_verify,
        "exports": command_exports,
        "conversations": command_conversations,
        "branches": command_branches,
        "search": command_search,
        "topics": command_topics,
        "review": command_review,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print(
            "Przerwano. Zakończony eksport pozostaje zatwierdzony; bieżąca transakcja jest cofana.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        emit(
            {"ok": False, "error_type": type(exc).__name__, "error": str(exc)},
            json_mode=getattr(args, "json", False),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
