from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import codecs
import hashlib
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("text_io_contract")
DEFAULT_MAX_BYTES = 8 * 1024 * 1024


class TextIOContractError(ValueError):
    """Raised when input cannot be decoded under the explicit text contract."""


@dataclass(slots=True)
class TextIOResult:
    text: str
    encoding_family: str
    bom_present: bool
    newline_style: str
    normalizations: list[str] = field(default_factory=list)
    original_sha256: str = ""
    normalized_sha256: str = ""
    byte_length: int = 0
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _newline_style(text: str) -> str:
    crlf = text.count("\r\n")
    without_crlf = text.replace("\r\n", "")
    cr = without_crlf.count("\r")
    lf = without_crlf.count("\n")
    kinds = sum(bool(value) for value in (crlf, cr, lf))
    if kinds == 0:
        return "none"
    if kinds > 1:
        return "mixed"
    if crlf:
        return "CRLF"
    if cr:
        return "CR"
    return "LF"


def decode_text_bytes(
    data: bytes,
    *,
    normalize_newlines: bool = True,
    strip_trailing_nul: bool = False,
) -> TextIOResult:
    """Decode UTF-8/UTF-8-BOM/UTF-16LE/UTF-16BE deterministically.

    UTF-16 without a BOM is deliberately rejected because byte order cannot be
    proven safely. The original byte hash and normalized UTF-8 hash are both
    retained for audit and idempotency.
    """

    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")
    raw = bytes(data)
    bom_present = False
    if raw.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
        family = "utf-8-bom"
        bom_present = True
    elif raw.startswith(codecs.BOM_UTF16_LE):
        encoding = "utf-16-le"
        family = "utf-16-le"
        bom_present = True
        raw_for_decode = raw[len(codecs.BOM_UTF16_LE) :]
    elif raw.startswith(codecs.BOM_UTF16_BE):
        encoding = "utf-16-be"
        family = "utf-16-be"
        bom_present = True
        raw_for_decode = raw[len(codecs.BOM_UTF16_BE) :]
    else:
        encoding = "utf-8"
        family = "utf-8"

    try:
        if family in {"utf-16-le", "utf-16-be"}:
            text = raw_for_decode.decode(encoding, errors="strict")
        else:
            text = raw.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise TextIOContractError(
            "Unsupported or ambiguous text encoding; use UTF-8, UTF-8 BOM, "
            "UTF-16LE BOM, or UTF-16BE BOM."
        ) from exc

    newline_style = _newline_style(text)
    normalizations: list[str] = []
    if strip_trailing_nul and text.endswith("\x00"):
        text = text.rstrip("\x00")
        normalizations.append("strip_trailing_nul")
    if normalize_newlines:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if normalized != text:
            normalizations.append("newlines_to_lf")
        text = normalized

    normalized_bytes = text.encode("utf-8")
    return TextIOResult(
        text=text,
        encoding_family=family,
        bom_present=bom_present,
        newline_style=newline_style,
        normalizations=normalizations,
        original_sha256=_sha(bytes(data)),
        normalized_sha256=_sha(normalized_bytes),
        byte_length=len(data),
    )


def read_text_contract(
    path: Path | str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    normalize_newlines: bool = True,
) -> TextIOResult:
    file_path = Path(path)
    size = file_path.stat().st_size
    if size > max_bytes:
        raise TextIOContractError(f"input_too_large:{size}>{max_bytes}")
    return decode_text_bytes(file_path.read_bytes(), normalize_newlines=normalize_newlines)


def write_utf8_atomic(path: Path | str, text: str, *, newline: str = "\n") -> dict[str, Any]:
    """Write UTF-8 atomically without a BOM and return auditable metadata."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        normalized = normalized.replace("\n", newline)
    payload = normalized.encode("utf-8")
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return {
        "schema_version": SCHEMA_VERSION,
        "path": str(target),
        "encoding_family": "utf-8",
        "bom_present": False,
        "newline_style": "LF" if newline == "\n" else "CRLF",
        "sha256": _sha(payload),
        "byte_length": len(payload),
    }
