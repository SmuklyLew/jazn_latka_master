from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any
import hashlib

SCHEMA_VERSION = "source_text_preservation_contract/v14.6.10"

@dataclass(slots=True)
class SourceTextPreservationContract:
    source_kind: str
    source_text_sha256: str
    preserve_exact_text_required: bool
    revision_allowed: bool
    change_list_required: bool
    added_text_origin_required: bool
    rule: str
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Kontrakt chroni tekst użytkownika; formatowanie nie oznacza zgody na redakcję ani dopisywanie wersów."
    def to_dict(self) -> dict[str, Any]: return asdict(self)

    @classmethod
    def build(cls, text: str, *, intent: str) -> "SourceTextPreservationContract":
        low = (text or '').lower()
        explicit_preserve = any(x in low for x in ('nie zmieniaj','1:1','bez zmian','zachowaj','bez redakcji'))
        preserve = intent in {"creative_text_formatting", "creative_source_preservation_request"} or explicit_preserve
        revision = (intent in {"creative_text_revision"} or any(x in low for x in ('przerób','przerob','zredaguj','zmień','zmien'))) and not explicit_preserve
        return cls(
            source_kind='creative_or_user_supplied_text' if intent.startswith('creative') else 'user_supplied_text',
            source_text_sha256=hashlib.sha256((text or '').encode('utf-8')).hexdigest(),
            preserve_exact_text_required=preserve and not revision,
            revision_allowed=revision,
            change_list_required=revision,
            added_text_origin_required=True,
            rule='Nie zmieniaj tekstu użytkownika bez wyraźnej prośby; jeśli zmieniasz, wypisz zmiany i pochodzenie dodatków.',
        )
