#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from latka_jazn.tools.console_progress import TerminalProgress, add_progress_arguments
from latka_jazn.tools.sqlite_archive_snapshot import create_sqlite_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Utwórz spójny, zweryfikowany snapshot bazy archiwum rozmów SQLite."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--full-check", action="store_true")
    parser.add_argument("--json", action="store_true")
    add_progress_arguments(parser)
    args = parser.parse_args(argv)
    display = TerminalProgress.from_namespace(args, "sqlite-snapshot", style="bar")

    def progress(done: int, total: int) -> None:
        safe_total = max(1, int(total))
        safe_done = min(max(0, int(done)), safe_total)
        copy_progress = round(90 * safe_done / safe_total)
        display.update(
            copy_progress,
            100,
            f"Kopiowanie stron SQLite: {safe_done}/{safe_total}",
            symbol="folder",
        )

    try:
        report = create_sqlite_snapshot(
            args.source,
            args.destination,
            progress=progress,
            full_integrity_check=args.full_check,
        )
    except Exception as exc:
        display.fail(f"Tworzenie snapshotu przerwane: {type(exc).__name__}")
        payload = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"Błąd: {payload}")
        return 1

    payload = report.to_dict()
    display.update(98, 100, "Integralność, klucze obce i SHA-256 sprawdzone", symbol="lock", force=True)
    display.finish(report.ok, "Snapshot SQLite gotowy")
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else "\n".join(f"{k}: {v}" for k, v in payload.items()))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
