#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from latka_jazn.tools.memory_restore import (
    DEVELOPER_CONFIRMATION,
    SYSTEM_CONFIRMATION,
    MemoryRestoreOrchestrator,
    MemoryRestoreSettings,
    confirmation_token,
    discover_restore_sources,
)
from latka_jazn.tools.memory_restore_ui import MemoryRestoreCursorApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kursorowy i bezgłowy restore pięciu baz pamięci Jaźni z eksportów ChatGPT i dzienników."
    )
    parser.add_argument("--config", type=Path, help="Konfiguracja JSON MemoryRestoreSettings.")
    parser.add_argument("--no-ui", action="store_true", help="Uruchom bez interfejsu kursorowego.")
    parser.add_argument("--plan-only", action="store_true", help="Zbuduj plan i nie zapisuj baz.")
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--target-root", type=Path)
    parser.add_argument("--mode", choices=("developer", "system"))
    parser.add_argument("--source", action="append", type=Path, dest="sources", help="Wybrany plik źródłowy; można podać wiele razy.")
    parser.add_argument("--all-discovered", action="store_true", help="Wybierz wszystkie obsługiwane pliki z katalogu.")
    parser.add_argument("--confirm", help=f"Jawny token zapisu: {DEVELOPER_CONFIRMATION} albo {SYSTEM_CONFIRMATION}:<pełna ścieżka systemu>.")
    parser.add_argument("--write-example-config", type=Path)
    return parser


def _settings(args: argparse.Namespace) -> MemoryRestoreSettings:
    if args.config:
        settings = MemoryRestoreSettings.from_json(args.config)
    else:
        settings = MemoryRestoreSettings(
            source_directory=str((args.source_dir or ROOT.parent).expanduser().resolve()),
            target_root=str((args.target_root or (ROOT.parent / "jazn_memory_test_03")).expanduser().resolve()),
            mode=args.mode or "developer",
        )
    payload = settings.to_dict()
    if args.source_dir:
        payload["source_directory"] = str(args.source_dir.expanduser().resolve())
    if args.target_root:
        payload["target_root"] = str(args.target_root.expanduser().resolve())
    if args.mode:
        payload["mode"] = args.mode
    return MemoryRestoreSettings(**payload).normalized()


def _print_event(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False, sort_keys=True, default=str), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.write_example_config:
        path = args.write_example_config.expanduser().resolve()
        settings = _settings(args)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(path)
        return 0

    settings = _settings(args)
    if not args.no_ui and not args.plan_only and not args.sources and not args.all_discovered:
        try:
            return MemoryRestoreCursorApp(ROOT, settings=settings).run()
        except KeyboardInterrupt:
            print("Przerwano przez Ctrl+X/Ctrl+C.", file=sys.stderr)
            return 130

    if args.sources:
        sources = [path.expanduser().resolve() for path in args.sources]
    elif args.all_discovered:
        sources = [item.path for item in discover_restore_sources(settings.source_directory, recursive=settings.recursive_scan)]
    else:
        raise SystemExit("Tryb bez UI wymaga --source (wielokrotnie) albo --all-discovered.")

    orchestrator = MemoryRestoreOrchestrator(settings, tool_root=ROOT, callback=_print_event)
    if args.plan_only:
        payload = orchestrator.plan(sources).to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return 0 if payload.get("ok") else 2
    result = orchestrator.run(sources, confirmation=args.confirm or "")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
