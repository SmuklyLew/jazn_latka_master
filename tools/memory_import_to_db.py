#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from latka_jazn.tools.chat_export_importer import ChatExportImporter
from latka_jazn.tools.chat_export_reader import sha256_file
from latka_jazn.tools.chat_export_store import ChatExportArchiveStore


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


def run_worker(source: Path, database: Path, *, dry_run: bool, timeout: float) -> dict[str, Any]:
    command = [
        sys.executable, "-X", "utf8", "-m", "latka_jazn.tools.chat_export_worker",
        "--source", str(source), "--database", str(database), "--quick-validation",
    ]
    if dry_run:
        command.append("--dry-run")
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=max(1.0, timeout),
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode or not lines:
        return {
            "ok": False,
            "status": "worker_failed",
            "source": str(source),
            "source_sha256": sha256_file(source) if source.is_file() else None,
            "worker_exit_code": completed.returncode,
            "stderr": completed.stderr.strip(),
        }
    return json.loads(lines[-1])


def command_import(args: argparse.Namespace) -> int:
    sources = sorted(
        args.sources,
        key=lambda path: path.stat().st_size if path.is_file() else 0,
        reverse=True,
    )
    args.database.parent.mkdir(parents=True, exist_ok=True)
    results = []
    started = time.monotonic()
    for index, source in enumerate(sources, 1):
        if not args.json:
            print(f"[{index}/{len(sources)}] Importuję {source.name}…", file=sys.stderr, flush=True)
        source_started = time.monotonic()
        try:
            result = run_worker(source, args.database, dry_run=args.dry_run, timeout=args.worker_timeout)
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
