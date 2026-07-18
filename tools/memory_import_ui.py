#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from latka_jazn.tools.chat_export_ui import MemoryImportCursorApp

DEFAULT_DATABASE = ROOT / "memory" / "sqlite" / "chat_export_archive_v2" / "chat_export_archive.sqlite3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kursorowy importer eksportów ChatGPT do archiwum SQLite.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args(argv)
    try:
        return MemoryImportCursorApp(args.database).run()
    except KeyboardInterrupt:
        print(
            "Przerwano przez Ctrl+X. Zatwierdzone operacje pozostają zapisane; aktywna transakcja została cofnięta.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
