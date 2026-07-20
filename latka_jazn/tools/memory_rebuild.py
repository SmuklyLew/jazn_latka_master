from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence
import argparse
import json

from latka_jazn.tools.chat_export_topics import ChatExportTopicStore
from latka_jazn.tools.memory_rebuild_coordinator import MemoryRebuildCoordinator, detect_source
from latka_jazn.tools.memory_rebuild_experience import ExperienceStore
from latka_jazn.tools.memory_rebuild_journal import JournalStore
from latka_jazn.tools.memory_rebuild_common import DATABASE_FILENAMES, MemoryRebuildPaths
from latka_jazn.tools.console_progress import TerminalProgress, add_progress_arguments


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild Jaźń memory into five fixed SQLite databases.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    add_progress_arguments(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    inspect = sub.add_parser("inspect"); inspect.add_argument("sources", nargs="+", type=Path)
    plan = sub.add_parser("plan-chats"); plan.add_argument("sources", nargs="+", type=Path); plan.add_argument("--details", action="store_true")
    chats = sub.add_parser("import-chats"); chats.add_argument("sources", nargs="+", type=Path); chats.add_argument("--dry-run", action="store_true"); chats.add_argument("--continue-on-error", action="store_true"); chats.add_argument("--quick-validation", action="store_true")
    journal = sub.add_parser("import-journal"); journal.add_argument("source", type=Path); journal.add_argument("--dry-run", action="store_true")
    reclassify = sub.add_parser("reclassify-journal"); reclassify.add_argument("--dry-run", action="store_true"); reclassify.add_argument("--limit", type=int, default=100)
    topics = sub.add_parser("analyse-topics"); topics.add_argument("--force", action="store_true"); topics.add_argument("--limit", type=int)
    audit = sub.add_parser("audit-classifiers"); audit.add_argument("--limit", type=int, default=50)
    build = sub.add_parser("build-experience-candidates"); build.add_argument("--from", dest="source", choices=("journal", "chats", "all"), default="all"); build.add_argument("--limit", type=int)
    review = sub.add_parser("review-experiences"); review.add_argument("--status", default="pending_review"); review.add_argument("--limit", type=int, default=100)
    approve = sub.add_parser("approve-experience"); approve.add_argument("--candidate-id", required=True); approve.add_argument("--confirm-candidate-id", required=True); approve.add_argument("--approved-by", required=True); approve.add_argument("--reason", required=True)
    verify = sub.add_parser("verify"); verify.add_argument("--quick", action="store_true")
    sub.add_parser("status")
    search = sub.add_parser("search"); search.add_argument("query"); search.add_argument("--limit", type=int, default=20)
    return parser


def emit(payload: dict[str, Any], json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    coordinator = MemoryRebuildCoordinator(args.root)
    display = TerminalProgress.from_namespace(args, f"memory-rebuild-{args.command}", style="spinner")
    labels = {
        "init": "Inicjalizuję pięć baz pamięci",
        "inspect": "Analizuję źródła pamięci bez zapisu",
        "plan-chats": "Buduję plan importu rozmów",
        "import-chats": "Importuję rozmowy do archiwum pamięci",
        "import-journal": "Importuję dziennik do pamięci",
        "reclassify-journal": "Ponownie klasyfikuję wpisy dziennika",
        "analyse-topics": "Analizuję tematy rozmów",
        "audit-classifiers": "Audytuję klasyfikatory pamięci",
        "build-experience-candidates": "Buduję kandydatów doświadczeń",
        "review-experiences": "Odczytuję kolejkę review doświadczeń",
        "approve-experience": "Zatwierdzam doświadczenie",
        "verify": "Sprawdzam integralność baz pamięci",
        "status": "Odczytuję stan przebudowy pamięci",
        "search": "Przeszukuję pamięć",
    }
    display.start_spinner(labels.get(args.command, "Przetwarzam pamięć"), symbol="wait" if args.command in {"inspect", "status", "search", "review-experiences"} else "work")
    try:
        if args.command == "init": payload = coordinator.init()
        elif args.command == "inspect": payload = coordinator.inspect(args.sources)
        elif args.command == "plan-chats": payload = coordinator.plan_chats(args.sources, args.details)
        elif args.command == "import-chats": payload = coordinator.import_chats(args.sources, args.dry_run, not args.quick_validation, args.continue_on_error)
        elif args.command == "import-journal": payload = coordinator.import_journal(args.source, args.dry_run)
        elif args.command == "reclassify-journal": payload = coordinator.reclassify_journal(args.dry_run, args.limit)
        elif args.command == "analyse-topics":
            coordinator.init()
            with ChatExportTopicStore(coordinator.paths.archive_chats) as topics:
                payload = {"ok": True, "analysis": topics.analyse_all(force=args.force, limit=args.limit),
                           "summary": topics.summary(), "automatic_l2": False, "automatic_l3": False}
        elif args.command == "audit-classifiers": payload = coordinator.audit_classifiers(args.limit)
        elif args.command == "build-experience-candidates": payload = coordinator.build_experience_candidates(args.source, args.limit)
        elif args.command == "review-experiences":
            coordinator.init()
            with ExperienceStore(coordinator.paths.experience) as experience:
                rows = experience.list_candidates(args.status, args.limit)
                payload = {"ok": True, "status": args.status, "candidate_count": len(rows), "candidates": rows,
                           "automatic_l2": False, "automatic_l3": False}
        elif args.command == "approve-experience": payload = coordinator.approve_experience(args.candidate_id, args.confirm_candidate_id, args.approved_by, args.reason)
        elif args.command == "verify": payload = coordinator.verify(not args.quick)
        elif args.command == "status": payload = coordinator.status()
        elif args.command == "search": payload = coordinator.search(args.query, args.limit)
        else: raise AssertionError(args.command)
        display.finish(bool(payload.get("ok", True)), "Operacja przebudowy pamięci zakończona")
        emit(payload, args.json)
        return 0 if payload.get("ok", True) else 2
    except KeyboardInterrupt:
        display.fail("Operacja przebudowy pamięci przerwana")
        emit({"ok": False, "status": "cancelled"}, args.json)
        return 130
    except Exception as exc:
        display.fail(f"Operacja przebudowy pamięci przerwana: {type(exc).__name__}")
        emit({"ok": False, "error_type": type(exc).__name__, "error": str(exc)}, args.json)
        return 1


__all__ = [
    "DATABASE_FILENAMES", "ExperienceStore", "JournalStore", "MemoryRebuildCoordinator",
    "MemoryRebuildPaths", "detect_source", "main",
]
