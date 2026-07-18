#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "memory_import_to_db.py"


def build_environment() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    current = env.get("PYTHONPATH", "")
    parts = [part for part in current.split(os.pathsep) if part]
    root_text = str(ROOT)
    if root_text not in parts:
        parts.insert(0, root_text)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not TARGET.is_file():
        print(f"Brak głównego programu importera: {TARGET}", file=sys.stderr)
        return 2
    command = [sys.executable, "-X", "utf8", str(TARGET), *arguments]
    try:
        completed = subprocess.run(
            command,
            env=build_environment(),
            check=False,
        )
    except KeyboardInterrupt:
        print(
            "Przerwano. Zakończony eksport pozostaje zatwierdzony; bieżąca transakcja jest cofana.",
            file=sys.stderr,
        )
        return 130
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
