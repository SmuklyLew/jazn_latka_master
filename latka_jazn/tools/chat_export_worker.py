from __future__ import annotations

import argparse
import json
import sys

from latka_jazn.tools.chat_export_performance import install_performance_overrides

install_performance_overrides()

from latka_jazn.tools.chat_export_importer import ChatExportImporter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quick-validation", action="store_true")
    parser.add_argument("--progress-jsonl", action="store_true")
    parser.add_argument("--progress-every", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        def progress(payload: dict) -> None:
            if args.progress_jsonl:
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)

        result = ChatExportImporter().import_one(
            args.source,
            args.database,
            dry_run=args.dry_run,
            full_validation=not args.quick_validation,
            progress_callback=progress if args.progress_jsonl else None,
            progress_every_conversations=max(1, args.progress_every),
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0 if result.ok else 2
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
