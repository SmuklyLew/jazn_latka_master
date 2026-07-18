from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
import sqlite3

from latka_jazn.tools.memory_rebuild_common import TRUTH_BOUNDARY, sqlite_check


class Store:
    def __init__(self, path: Path, schema: str, meta_table: str, schema_name: str) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.execute("PRAGMA busy_timeout=10000")
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.executescript(schema)
        self.con.execute(f"INSERT OR REPLACE INTO {meta_table}(key,value) VALUES('schema_version',?)", (schema_name,))
        self.con.execute(f"INSERT OR REPLACE INTO {meta_table}(key,value) VALUES('truth_boundary',?)", (TRUTH_BOUNDARY,))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.con.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.con.rollback()
            raise
        else:
            self.con.commit()

    def validate(self, full: bool = True) -> dict[str, Any]:
        return sqlite_check(self.con, full=full)

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
