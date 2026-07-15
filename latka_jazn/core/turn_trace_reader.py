from __future__ import annotations
from pathlib import Path
from typing import Any
import json

SCHEMA_VERSION = "turn_trace_reader/v14.6.10"

class TurnTraceReader:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.base = self.root / 'workspace_runtime' / 'turn_checkpoints'
    def latest(self) -> dict[str, Any] | None:
        p = self.base / 'LATEST_TURN_CHECKPOINT.json'
        if not p.exists(): return None
        return json.loads(p.read_text(encoding='utf-8'))
    def by_trace_id(self, trace_id: str) -> dict[str, Any] | None:
        for p in sorted(self.base.glob('*/turns.jsonl'), reverse=True):
            for line in p.read_text(encoding='utf-8', errors='replace').splitlines():
                if not line.strip(): continue
                try: data = json.loads(line)
                except Exception: continue
                if data.get('trace_id') == trace_id or data.get('turn_id') == trace_id:
                    return data
        return None
    def status(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "present": self.base.exists(), "checkpoint_files": len(list(self.base.glob('*/turns.jsonl'))), "latest_present": (self.base/'LATEST_TURN_CHECKPOINT.json').exists()}
