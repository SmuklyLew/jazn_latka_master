from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
from .store import MemoryStore
from .file_sync import MemoryFileSync
from .chat_html_importer import import_chat_html_to_store
from .raw_archive import unpack_chat_html_archive

class MemoryImporter:
    def __init__(self, store: MemoryStore, root: Path) -> None:
        self.store = store
        self.root = root

    def register_packaged_sources(
        self,
        *,
        auto_import_raw_chat_html: bool = True,
        limit_conversations: int | None = None,
    ) -> dict:
        """Rejestruje źródła pamięci i aktywuje dostępne warstwy.

        Jeżeli istnieje RAW_MEMORY_MANIFEST.json, ufa hashom utworzonym podczas
        budowania paczki. Jeżeli chat.html.7z da się rozpakować i SQLite nie ma
        jeszcze indeksu legacy_messages, importer wykonuje jednorazowy import
        chat.html do SQLite. Dzięki temu świeże uruchomienie nie zostawia pełnej
        surowej pamięci w stanie półaktywnym, gdy zależności są już dostępne.
        """
        manifest_path = self.root / "memory" / "RAW_MEMORY_MANIFEST.json"
        archive_report = unpack_chat_html_archive(self.root, overwrite=False)
        counts = {
            "raw": 0,
            "versioned_sources": 0,
            "manifest_mode": False,
            "raw_archive": archive_report.to_dict(),
            "chat_html_auto_import": "not_requested" if not auto_import_raw_chat_html else "not_needed",
        }
        if manifest_path.exists():
            rows = json.loads(manifest_path.read_text(encoding="utf-8"))
            now = datetime.now(timezone.utc).isoformat()
            for row in rows:
                stored = row.get("stored_as")
                sha = row.get("sha256")
                if not stored or not sha:
                    continue
                path = self.root / stored
                if not path.exists():
                    continue
                kind = row.get("kind", "raw_memory")
                self.store.con.execute(
                    "INSERT OR REPLACE INTO source_files VALUES(?,?,?,?,?,?)",
                    (sha, str(path), int(row.get("size_bytes") or path.stat().st_size), kind, row.get("source") or row.get("source_relative_path"), now),
                )
                if str(stored).startswith("memory/raw"):
                    counts["raw"] += 1
                else:
                    counts["versioned_sources"] += 1
            self.store.con.commit()
            counts["manifest_mode"] = True
        else:
            for folder, kind in [("memory/raw", "raw_memory"), ("memory/versioned_sources", "versioned_memory")]:
                base = self.root / folder
                if not base.exists():
                    continue
                for p in base.rglob("*"):
                    if p.is_file():
                        self.store.register_source_file(p, kind=kind)
                        counts["raw" if kind == "raw_memory" else "versioned_sources"] += 1

        # v14.5.24: same zarejestrowanie plików nie wystarcza. Runtime musi
        # przepisać istniejące JSON/JSONL do SQLite, żeby wyszukiwanie pamięci
        # widziało epizody, fakty, procedury i dziennik po świeżym starcie.
        try:
            sync_report = MemoryFileSync(self.root, self.store).synchronize_all(export=False)
            counts["file_sync_imported"] = sync_report.imported
            counts["file_sync_errors"] = len(sync_report.errors)
        except Exception as exc:
            counts["file_sync_error"] = repr(exc)

        if auto_import_raw_chat_html:
            legacy_count = self.store.stats().get("legacy_messages", 0)
            chat_path = self.root / "memory" / "raw" / "chat.html"
            if legacy_count == 0 and chat_path.exists():
                try:
                    counts["chat_html_auto_import"] = self.import_raw_chat_html(
                        force=False,
                        limit_conversations=limit_conversations,
                    )
                except Exception as exc:
                    counts["chat_html_auto_import"] = {"status": "error", "error": repr(exc)}
            elif legacy_count > 0:
                counts["chat_html_auto_import"] = {"status": "already_indexed", "legacy_messages": legacy_count}
            elif not chat_path.exists() and archive_report.status in {"missing_py7zr", "missing_archive", "error", "unpacked_but_missing_chat"}:
                counts["chat_html_auto_import"] = {
                    "status": "not_possible",
                    "reason": archive_report.status,
                    "error": archive_report.error,
                }

        self.store.add_event("packaged_sources_registered", counts, source="MemoryImporter", actor="system", tags=["migration", "integrity", "v14.5.24"], importance=0.8)
        return counts

    def import_raw_chat_html(self, *, force: bool = False, limit_conversations: int | None = None) -> dict:
        path = self.root / "memory" / "raw" / "chat.html"
        unpack_report = None
        if not path.exists():
            unpack_report = unpack_chat_html_archive(self.root, overwrite=False)
        if not path.exists():
            result = {
                "status": "missing_raw_chat_html",
                "path": str(path),
                "unpack": unpack_report.to_dict() if unpack_report else None,
                "errors": ["chat.html nie istnieje i nie udało się go rozpakować z chat.html.7z."],
            }
            self.store.add_event(
                "chat_html_import",
                result,
                source="MemoryImporter",
                actor="system",
                tags=["raw_memory", "chat_html", "v14.5.24", "missing_dependency"],
                importance=0.5,
            )
            return result
        report = import_chat_html_to_store(self.store, path, force=force, limit_conversations=limit_conversations)
        result = report.to_dict()
        if unpack_report is not None:
            result["unpack"] = unpack_report.to_dict()
        self.store.add_event(
            "chat_html_import",
            result,
            source="MemoryImporter",
            actor="system",
            tags=["raw_memory", "chat_html", "v14.5.24"],
            importance=0.9 if report.status in {"ok", "already_imported"} else 0.5,
        )
        return result

    def synchronize_memory_files(self, *, export: bool = True) -> dict:
        report = MemoryFileSync(self.root, self.store).synchronize_all(export=export)
        self.store.add_event(
            "memory_files_synchronized",
            report.to_dict(),
            source="MemoryImporter",
            actor="system",
            tags=["memory_files", "sqlite", "v14.5.24"],
            importance=0.88,
        )
        return report.to_dict()
