from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import importlib.util
import shutil
import subprocess
from typing import Any


@dataclass(slots=True)
class RawArchiveReport:
    status: str
    archive: str | None = None
    output: str | None = None
    error: str | None = None
    extractor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "archive": self.archive,
            "output": self.output,
            "error": self.error,
            "extractor": self.extractor,
        }


def find_chat_archive(root: Path) -> Path | None:
    root = Path(root)
    candidates = [
        root / "chat.html.7z",
        root / "memory" / "raw" / "chat.html.7z",
        root.parent / "chat.html.7z",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def py7zr_available() -> bool:
    return importlib.util.find_spec("py7zr") is not None


def system_7z_executable() -> str | None:
    for exe in ("7z", "7za", "7zr"):
        found = shutil.which(exe)
        if found:
            return found
    return None


def chat_archive_diagnostics(root: Path) -> dict[str, Any]:
    root = Path(root)
    chat = root / "memory" / "raw" / "chat.html"
    archive = find_chat_archive(root)
    seven_zip = system_7z_executable()
    return {
        "chat_html_present": chat.exists(),
        "chat_html_path": str(chat),
        "chat_html_size_bytes": chat.stat().st_size if chat.exists() else 0,
        "archive_present": archive is not None,
        "archive_path": str(archive) if archive else None,
        "archive_size_bytes": archive.stat().st_size if archive else 0,
        "py7zr_available": py7zr_available(),
        "system_7z": seven_zip,
        "can_unpack": bool(chat.exists() or (archive and (py7zr_available() or seven_zip))),
    }


def _normalize_extracted_chat(out_dir: Path, out_path: Path) -> bool:
    if out_path.exists():
        return True
    found = next(out_dir.rglob("chat.html"), None)
    if found and found != out_path:
        shutil.move(str(found), str(out_path))
        return out_path.exists()
    html = sorted(out_dir.rglob("*.html"), key=lambda p: p.stat().st_size, reverse=True)
    if html:
        shutil.move(str(html[0]), str(out_path))
        return out_path.exists()
    return False


def unpack_chat_html_archive(root: Path, *, overwrite: bool = False) -> RawArchiveReport:
    root = Path(root)
    out_dir = root / "memory" / "raw"
    out_path = out_dir / "chat.html"
    if out_path.exists() and not overwrite:
        return RawArchiveReport("already_present", output=str(out_path))
    archive = find_chat_archive(root)
    if archive is None:
        return RawArchiveReport(
            "missing_archive",
            output=str(out_path),
            error="Nie znaleziono chat.html.7z w root, memory/raw ani katalogu nadrzędnym root.",
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    seven_zip = system_7z_executable()
    if seven_zip:
        try:
            subprocess.run([seven_zip, "x", str(archive), f"-o{out_dir}", "-y"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if _normalize_extracted_chat(out_dir, out_path):
                return RawArchiveReport("unpacked", archive=str(archive), output=str(out_path), extractor=f"cli:{Path(seven_zip).name}")
            return RawArchiveReport("unpacked_but_missing_chat", archive=str(archive), output=str(out_path), extractor=f"cli:{Path(seven_zip).name}", error="Archiwum rozpakowane, ale nie znaleziono chat.html ani pliku .html.")
        except Exception as exc:
            # przechodzimy do py7zr, jeśli jest dostępny
            cli_error = repr(exc)
        else:
            cli_error = None
    else:
        cli_error = None

    try:
        import py7zr  # type: ignore
    except Exception as exc:
        msg = f"Brak py7zr: {exc!r}. Dodaj zależność `py7zr>=0.21.0` albo zainstaluj systemowy 7z/7za/7zr."
        if cli_error:
            msg += f" Próba CLI też nie powiodła się: {cli_error}"
        return RawArchiveReport("missing_py7zr", archive=str(archive), output=str(out_path), error=msg)

    try:
        with py7zr.SevenZipFile(archive, "r") as z:
            z.extractall(out_dir)
        if _normalize_extracted_chat(out_dir, out_path):
            return RawArchiveReport("unpacked", archive=str(archive), output=str(out_path), extractor="python:py7zr")
        return RawArchiveReport(
            "unpacked_but_missing_chat",
            archive=str(archive),
            output=str(out_path),
            extractor="python:py7zr",
            error="Archiwum rozpakowane, ale nie znaleziono chat.html ani pliku .html.",
        )
    except Exception as exc:
        return RawArchiveReport("error", archive=str(archive), output=str(out_path), extractor="python:py7zr", error=repr(exc))
