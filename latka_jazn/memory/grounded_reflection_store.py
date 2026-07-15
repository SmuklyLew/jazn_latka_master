from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import uuid

from latka_jazn.core.reflection_grounding import GroundedReflection
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("grounded_reflection_store")


@dataclass(slots=True)
class GroundedReflectionStoreResult:
    schema_version: str
    attempted: bool
    appended_jsonl: bool
    sqlite_recorded: bool
    path: str
    reflection_id: str | None
    fingerprint: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GroundedReflectionStore:
    """Append-only zapis uziemionych refleksji do warstwy pamięci.

    Zapis jest celowo osobny od zwykłych reflections: pozwala testować jakość
    źródeł i granicę prawdy, zanim refleksja stanie się częścią mocniejszej pamięci.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.path = self.root / "memory" / "layered" / "grounded_reflections.jsonl"

    def append_once(self, reflection: GroundedReflection, *, store: Any | None = None, source: str = "runtime") -> GroundedReflectionStoreResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = reflection.to_dict()
        fingerprint = self._fingerprint(payload)
        if self._has_fingerprint(fingerprint):
            return GroundedReflectionStoreResult(SCHEMA_VERSION, True, False, False, str(self.path), None, fingerprint, "duplicate")
        rid = str(uuid.uuid4())
        record = {
            "schema_version": SCHEMA_VERSION,
            "reflection_id": rid,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "fingerprint": fingerprint,
            "reflection": payload,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        sqlite_recorded = False
        if store is not None and hasattr(store, "add_reflection"):
            try:
                store.add_reflection({
                    "reflection_id": rid,
                    "created_at_utc": record["created_at_utc"],
                    "episode_id": None,
                    "meaning_for_latka": reflection.reflection_text,
                    "identity_impact": reflection.identity_impact,
                    "boundary_note": reflection.truth_boundary,
                    "next_question": reflection.next_question,
                    "confidence": reflection.confidence,
                })
                sqlite_recorded = True
            except Exception:
                sqlite_recorded = False
        return GroundedReflectionStoreResult(SCHEMA_VERSION, True, True, sqlite_recorded, str(self.path), rid, fingerprint, "appended")

    def _has_fingerprint(self, fingerprint: str) -> bool:
        if not self.path.exists():
            return False
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    if fingerprint in line:
                        return True
        except Exception:
            return False
        return False

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
