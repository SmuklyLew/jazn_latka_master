from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import ast, json, hashlib

SCHEMA_VERSION = "module_responsibility_map/v14.6.10"


@dataclass(slots=True)
class ModuleResponsibility:
    path: str
    classes: list[str]
    functions: list[str]
    responsibilities: list[str]
    handles_intents: list[str]
    lifecycle_status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModuleResponsibilityMap:
    KEYWORDS = {
        "memory": ("memory", "pamięć", ["memory_audit_request", "memory_recall_content_question"]),
        "conversation": ("dialogue", "rozmowa", ["ordinary_conversation", "self_state_question", "system_diagnostic_question"]),
        "nlp": ("nlp", "rozpoznanie intencji", ["dialogue_intent_classification", "creative_text_analysis"]),
        "source": ("source_origin", "granica źródeł", ["runtime_source_question", "identity_boundary_question"]),
        "startup": ("startup", "rozruch/cache", ["startup_runtime_issue", "module_map_request"]),
        "package": ("packaging", "eksport paczek", ["download_packaging_issue", "system_update_execution_request"]),
        "test": ("tests", "regresja zachowania", ["behavioral_regression"]),
    }

    def __init__(self, root: Path) -> None:
        self.root = root
        self.output_path = root / "workspace_runtime" / "module_responsibility_map_v14_6_10.json"
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def _analyse_py(self, path: Path) -> tuple[list[str], list[str]]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return [], []
        classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
        functions = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        return classes, functions

    def _responsibilities_for(self, rel: str) -> tuple[list[str], list[str], str]:
        low = rel.lower()
        resp: list[str] = []
        intents: list[str] = []
        status = "active"
        for key, (label, description, tags) in self.KEYWORDS.items():
            if key in low or label in low:
                resp.append(description)
                intents.extend(tags)
        if not resp:
            resp.append("pomocniczy moduł systemu")
        if "v14_5" in low or "legacy" in low:
            status = "legacy_or_historical_resource"
        return resp, sorted(set(intents)), status

    def build(self, *, write: bool = True) -> dict[str, Any]:
        modules: list[dict[str, Any]] = []
        for path in sorted(self.root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(self.root).as_posix()
            classes, funcs = self._analyse_py(path)
            resp, intents, status = self._responsibilities_for(rel)
            modules.append(ModuleResponsibility(rel, classes, funcs, resp, intents, status).to_dict())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "root": str(self.root),
            "module_count": len(modules),
            "modules": modules,
            "truth_boundary": "To mapa odpowiedzialności modułów i funkcji. Pomaga runtime wybierać narzędzia, ale nie oznacza pełnego semantycznego zrozumienia każdego bajtu pliku.",
            "sha256": hashlib.sha256(json.dumps(modules, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        }
        if write:
            self.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return payload
