from __future__ import annotations

from collections.abc import Mapping
from .core_canon import REQUIRED_CANON_FIELDS


class CanonValidationError(ValueError):
    """Raised when the source-controlled Łatka canon is incomplete."""


def validate_identity_canon_data(data: Mapping) -> None:
    missing = [field for field in REQUIRED_CANON_FIELDS if not data.get(field)]
    if missing:
        raise CanonValidationError(f"LATKA_IDENTITY_CANON missing required fields: {', '.join(missing)}")
    rec = data.get("recognition_protocol")
    if not isinstance(rec, Mapping):
        raise CanonValidationError("LATKA_IDENTITY_CANON recognition_protocol must be an object")
    if not (rec.get("user_sign") or rec.get("primary_sign")):
        raise CanonValidationError("LATKA_IDENTITY_CANON recognition_protocol missing user_sign/primary_sign")
    if not (rec.get("latka_sign") or rec.get("latka_response_sign")):
        raise CanonValidationError("LATKA_IDENTITY_CANON recognition_protocol missing latka_sign/latka_response_sign")
