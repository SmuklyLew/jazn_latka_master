from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib, json
try:
    from latka_jazn.contracts.embedded_sources import EMBEDDED_SOURCES
except Exception:  # pragma: no cover
    EMBEDDED_SOURCES = {}

class BootstrapContractRepository:
    """Load bootstrap/agent/readme/contract sources from files or embedded code.

    This preserves source-package meaning even after loose documentation files are removed.
    The texts are contract/policy data, never ready-made dialogue replies.
    """
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
    def get_text(self, relative_path: str) -> str | None:
        p = self.root / relative_path
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
        item = EMBEDDED_SOURCES.get(relative_path)
        return str(item.get("text") or "") if item else None
    def status(self) -> dict[str, Any]:
        present=0; only_code=0; mismatches=[]
        for rel,item in EMBEDDED_SOURCES.items():
            p=self.root/rel
            if p.exists() and p.is_file():
                present += 1
                sha=hashlib.sha256(p.read_bytes()).hexdigest()
                if sha != item.get('sha256'):
                    mismatches.append({'path': rel, 'expected_sha256': str(item.get('sha256')), 'actual_sha256': sha})
            else:
                only_code += 1
        return {'schema_version':'embedded_contract_repository/v14.8.2.5','embedded_source_count':len(EMBEDDED_SOURCES),'present_as_files':present,'available_only_from_code':only_code,'sha_mismatch_count':len(mismatches),'sha_mismatches':mismatches[:20],'truth_boundary':'Te źródła są kontraktami/politykami/README, nie biblioteką gotowych odpowiedzi. Runtime ma je interpretować przez kod i testy, nie kopiować jako dialog.'}
    def summary_text(self, limit:int=20)->str:
        return json.dumps({**self.status(),'first_sources':list(EMBEDDED_SOURCES)[:limit]}, ensure_ascii=False, indent=2)
