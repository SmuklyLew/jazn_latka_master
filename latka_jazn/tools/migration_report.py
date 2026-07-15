from __future__ import annotations
from pathlib import Path
import json

def load_manifest(root: Path) -> dict:
    p = root / "memory" / "RAW_MEMORY_MANIFEST.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
