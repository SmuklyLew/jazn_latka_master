from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SOURCE_REGISTRY_SCHEMA = "polish_reasoning_source_registry/v14.8.4"


class PolishReasoningSourceRegistry:
    def __init__(self, root: str | Path | None = None) -> None:
        base = Path(root) if root else Path(__file__).resolve().parents[1]
        if (base / "latka_jazn").exists():
            base = base / "latka_jazn"
        self.root = base
        self.path = self.root / "resources" / "polish_reasoning" / "sources.lock.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": SOURCE_REGISTRY_SCHEMA,
                "sources": {},
                "status": "missing_registry_file",
                "path": str(self.path),
            }
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.setdefault("schema_version", SOURCE_REGISTRY_SCHEMA)
        return data

    def to_dict(self) -> dict[str, Any]:
        data = self.load()
        sources = data.get("sources") or {}
        return {
            "schema_version": data.get("schema_version", SOURCE_REGISTRY_SCHEMA),
            "source_count": len(sources),
            "sources": sources,
            "policy": data.get("policy", {}),
            "truth_boundary": data.get(
                "truth_boundary",
                "Rejestr opisuje dopuszczalne źródła; nie oznacza, że dane zostały pobrane albo zcache'owane.",
            ),
        }
