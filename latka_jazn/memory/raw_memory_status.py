from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import hashlib, shutil, sqlite3

SCHEMA_VERSION = "raw_memory_status/v14.8.2.5"

def _sha(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

@dataclass(slots=True)
class RawMemoryStatus:
    archive_present: bool
    html_present: bool
    extractor_available: bool
    sqlite_present: bool
    legacy_messages_count: int
    sqlite_index_available: bool
    source_sha256_recorded: bool
    status: str
    archive_sha256: str | None = None
    html_sha256: str | None = None
    sqlite_path: str | None = None
    conversation_messages_count: int = 0
    legacy_chunks_count: int = 0
    sqlite_schema_kind: str = "unknown"
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Archiwum raw chat, rozpakowany HTML i indeks SQLite to różne stany. SQLite v4 może mieć messages/legacy_chunks zamiast legacy_messages. Dowolny plik SQLite bez realnych rekordów nie jest dowodem dostępnej pamięci rozmów."
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class RawMemoryInspector:
    def __init__(self, root: Path, sqlite_path: Path | None = None) -> None:
        self.root=Path(root)
        self.sqlite_path=Path(sqlite_path) if sqlite_path else None

    def _table_count(self, con: sqlite3.Connection, table: str) -> int:
        try:
            return int(con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] or 0)
        except Exception:
            return 0

    def _meta_present(self, con: sqlite3.Connection) -> bool:
        checks = [
            ("meta", "key", ('chat_html_source_sha256','raw_chat_source_sha256','import_source_sha256','chat_html_import_sha256')),
            ("schema_meta", "key", ('script','script_version','db_kind','db_label')),
            ("imports_log", "source_sha256", None),
            ("legacy_imports_log", "import_id", None),
        ]
        for table, column, keys in checks:
            try:
                if keys:
                    marks=','.join('?' for _ in keys)
                    row=con.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({marks})", keys).fetchone()
                else:
                    row=con.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL").fetchone()
                if row and int(row[0] or 0)>0:
                    return True
            except Exception:
                continue
        return False

    def _inspect_sqlite(self, db: Path) -> tuple[int, int, int, bool, str]:
        if not db.exists():
            return 0, 0, 0, False, "missing"
        try:
            con=sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True, timeout=10.0)
            try:
                legacy = self._table_count(con, 'legacy_messages')
                messages = self._table_count(con, 'messages')
                chunks = self._table_count(con, 'legacy_chunks')
                meta = self._meta_present(con)
                if messages > 0 and chunks > 0:
                    kind = "chat_context_v4_with_legacy_chunks"
                elif messages > 0:
                    kind = "chat_context_v4_messages"
                elif legacy > 0:
                    kind = "legacy_messages_runtime"
                else:
                    kind = "empty_or_unknown"
                return legacy, messages, chunks, meta, kind
            finally:
                con.close()
        except Exception:
            return 0, 0, 0, False, "sqlite_read_error"

    def inspect(self) -> RawMemoryStatus:
        raw_dir=self.root/'memory'/'raw'
        html=raw_dir/'chat.html'
        archive=raw_dir/'chat.html.7z'
        extractor=bool(shutil.which('7z'))
        try:
            import py7zr  # type: ignore
            extractor=True
        except Exception:
            pass
        db=self.sqlite_path or self.root/'memory'/'sqlite'/'chat_context.sqlite3'
        if not db.exists():
            candidates=[]
            for parent in [self.root/'memory'/'sqlite', self.root/'workspace_runtime']:
                if parent.exists():
                    candidates.extend(parent.glob('*.sqlite3'))
            db=sorted(candidates)[-1] if candidates else db
        legacy_count, message_count, chunk_count, sha_meta, kind = self._inspect_sqlite(db)
        sqlite_present=db.exists()
        usable_records = max(legacy_count, message_count, chunk_count)
        index_available=bool(sqlite_present and usable_records>0 and (sha_meta or archive.exists() or html.exists() or kind.startswith('chat_context_v4')))
        if html.exists() and index_available:
            status='rozpakowana'
        elif index_available:
            status='indeks_dostępny'
        elif archive.exists():
            status='archiwum'
        elif sqlite_present and usable_records == 0:
            status='indeks_pusty'
        else:
            status='niedostępna'
        return RawMemoryStatus(
            archive_present=archive.exists(), html_present=html.exists(), extractor_available=extractor,
            sqlite_present=sqlite_present, legacy_messages_count=legacy_count, conversation_messages_count=message_count,
            legacy_chunks_count=chunk_count, sqlite_index_available=index_available, source_sha256_recorded=sha_meta,
            status=status, archive_sha256=_sha(archive), html_sha256=_sha(html), sqlite_path=str(db) if db.exists() else None,
            sqlite_schema_kind=kind,
        )
