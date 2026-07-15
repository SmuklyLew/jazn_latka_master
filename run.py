from __future__ import annotations

import sys

from latka_jazn.cli import main


if __name__ == "__main__":
    argv = sys.argv[1:] or ["chat"]
    raise SystemExit(main(argv))