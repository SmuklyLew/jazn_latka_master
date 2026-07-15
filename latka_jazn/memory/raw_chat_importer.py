from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import hashlib

SCHEMA_VERSION="raw_chat_importer/v14.7.0"

def _sha(path: Path) -> str | None:
    if not path.exists() or not path.is_file(): return None
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

@dataclass(slots=True)
class RawChatStatus:
    status: str
    chat_html_present: bool
    archive_present: bool
    sqlite_index_available: bool
    chat_html_sha256: str | None = None
    archive_sha256: str | None = None
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Nie wolno twierdzić, że chat.html istnieje, jeśli dostępne jest tylko chat.html.7z. Import surowej pamięci wymaga jawnego rozpakowania albo istniejącego indeksu."
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class RawChatImporter:
    def __init__(self, root: Path) -> None:
        self.root=Path(root)
    def inspect(self) -> RawChatStatus:
        raw=self.root/'memory'/'raw'/'chat.html'; archive=self.root/'memory'/'raw'/'chat.html.7z'
        dbs=list((self.root/'workspace_runtime').glob('*.sqlite3')) if (self.root/'workspace_runtime').exists() else []
        chat=raw.exists(); arc=archive.exists(); db=bool(dbs)
        if chat and db: status='fully_extracted'
        elif chat: status='raw_only'
        elif arc and db: status='archive_indexed'
        elif arc: status='archive'
        else: status='unavailable'
        return RawChatStatus(status=status, chat_html_present=chat, archive_present=arc, sqlite_index_available=db, chat_html_sha256=_sha(raw), archive_sha256=_sha(archive))
