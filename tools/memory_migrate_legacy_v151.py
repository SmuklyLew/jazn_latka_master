#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from latka_jazn.memory.legacy_fanout_migration import LegacyFanoutMigrationStore, LegacyMemoryScanner
from latka_jazn.memory.memory_tier_store import MemoryTierStore


def emit(payload: dict, *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


def existing_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file does not exist: {path}")
    return path


def database_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Bezpieczna migracja starego fan-out pamięci do kolejki review v15.1.0.1."
    )
    root.add_argument("--json", action="store_true")
    sub = root.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="zbadaj starą bazę bez zapisu")
    inspect.add_argument("legacy_database", type=existing_file)

    stage = sub.add_parser("stage", help="zapisz kandydatów do kolejki review, bez tworzenia L2/L3")
    stage.add_argument("legacy_database", type=existing_file)
    stage.add_argument("--target", required=True, type=database_path)

    candidates = sub.add_parser("candidates", help="pokaż kandydatów oczekujących na decyzję")
    candidates.add_argument("--target", required=True, type=existing_file)
    candidates.add_argument("--status", default="pending_review")
    candidates.add_argument("--limit", type=int, default=100)

    approve = sub.add_parser("approve-l2", help="jawnie zatwierdź jeden kandydat do L2")
    approve.add_argument("--target", required=True, type=existing_file)
    approve.add_argument("--candidate-id", required=True)
    approve.add_argument("--confirm-candidate-id", required=True)
    approve.add_argument("--approved-by", required=True)

    verify = sub.add_parser("verify", help="sprawdź integralność nowej bazy")
    verify.add_argument("--target", required=True, type=existing_file)
    verify.add_argument("--quick", action="store_true")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "inspect":
            with LegacyMemoryScanner(args.legacy_database) as scanner:
                payload = {
                    "ok": True,
                    "source_path": str(scanner.path),
                    "source_sha256": scanner.source_sha256,
                    "inventory": scanner.inventory(),
                    "candidate_count": sum(1 for _ in scanner.candidates()),
                    "read_only": True,
                }
        elif args.command == "stage":
            args.target.parent.mkdir(parents=True, exist_ok=True)
            with MemoryTierStore(args.target) as store, LegacyMemoryScanner(args.legacy_database) as scanner:
                migration = LegacyFanoutMigrationStore(store)
                report = migration.stage_scan(scanner)
                payload = {
                    "ok": True,
                    "target": str(args.target),
                    "source_sha256": scanner.source_sha256,
                    "stage_report": report,
                    "memory_records_after_stage": store.stats()["memory_records"],
                    "automatic_l3": False,
                    "validation": store.validate(full=False),
                }
        elif args.command == "candidates":
            with MemoryTierStore(args.target) as store:
                migration = LegacyFanoutMigrationStore(store)
                rows = [item.to_dict() for item in migration.list_candidates(status=args.status)]
                payload = {
                    "ok": True,
                    "status": args.status,
                    "candidate_count": len(rows),
                    "candidates": rows[: max(1, args.limit)],
                }
        elif args.command == "approve-l2":
            if args.candidate_id != args.confirm_candidate_id:
                raise ValueError("confirm-candidate-id must exactly match candidate-id")
            if not args.approved_by.strip():
                raise ValueError("approved-by is required")
            with MemoryTierStore(args.target) as store:
                migration = LegacyFanoutMigrationStore(store)
                record = migration.approve_to_l2(
                    args.candidate_id,
                    approved_by=args.approved_by,
                )
                payload = {
                    "ok": True,
                    "candidate_id": args.candidate_id,
                    "approved_by": args.approved_by,
                    "created_memory_id": record.memory_id,
                    "created_tier": record.tier.value,
                    "automatic_l3": False,
                    "validation": store.validate(full=False),
                }
        else:
            with MemoryTierStore(args.target) as store:
                payload = store.validate(full=not args.quick)
        emit(payload, json_mode=args.json)
        return 0 if payload.get("ok") else 2
    except KeyboardInterrupt:
        print("Przerwano. Bieżąca transakcja została cofnięta.", file=sys.stderr)
        return 130
    except Exception as exc:
        emit({"ok": False, "error_type": type(exc).__name__, "error": str(exc)}, json_mode=args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
