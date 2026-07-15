from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json
import sqlite3

from latka_jazn.config import JaznConfig
from latka_jazn.core.capability_reality_checker import CapabilityRealityChecker
from latka_jazn.core.operational_self_model import OperationalSelfModel
from latka_jazn.core.operational_work_loop import OperationalWorkLoop
from latka_jazn.model_adapters.factory import build_model_adapter_status
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version

SELF_KNOWLEDGE_RESOURCE = "latka_jazn/resources/canon/LATKA_SELF_KNOWLEDGE_CONTRACT.json"
IDENTITY_CANON_RESOURCE = "latka_jazn/resources/canon/LATKA_IDENTITY_CANON.json"
SCHEMA_VERSION = schema_version("self_knowledge_packet")


@dataclass(slots=True)
class SelfKnowledgeSourceStatus:
    path: str
    present: bool
    role: str
    status: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SelfKnowledgePacket:
    schema_version: str
    runtime_version: str
    active_root: str
    contract_path: str
    contract_present: bool
    identity_name: str | None
    identity_status: dict[str, Any]
    memory_status: dict[str, Any]
    learned_procedures_status: dict[str, Any]
    capability_status: dict[str, Any]
    affective_model_status: dict[str, Any]
    operational_work_status: dict[str, Any]
    adapter_strategy: dict[str, Any]
    recall_policy: dict[str, Any]
    post_update_bootstrap: list[str]
    answer_contract: dict[str, Any]
    source_statuses: list[dict[str, Any]]
    ready_for_runtime_self_reference: bool
    blocking_issues: list[str] = field(default_factory=list)
    truth_boundary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_json(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return {}, "missing"
    except json.JSONDecodeError as exc:
        return {}, f"json_error:{exc}"


def load_self_knowledge_contract(root: Path) -> tuple[dict[str, Any], str | None]:
    """Load the static contract and resolve release metadata from version.py.

    The JSON resource intentionally contains no copied package version.  Runtime
    consumers receive canonical metadata here, so a release bump changes only
    latka_jazn/version.py plus the matching VERSION.txt checkpoint.
    """
    data, error = _load_json(Path(root) / SELF_KNOWLEDGE_RESOURCE)
    if error is not None:
        return data, error
    resolved = dict(data)
    resolved["schema_version"] = schema_version("latka_self_knowledge_contract")
    resolved["version"] = PACKAGE_VERSION_FULL
    resolved["version_source"] = "latka_jazn/version.py"
    return resolved, None


def _sqlite_status(path: Path) -> dict[str, Any]:
    rel = path.as_posix()
    if not path.exists():
        return {"path": rel, "exists": False, "status": "missing"}
    if not path.is_file():
        return {"path": rel, "exists": True, "status": "not_file"}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            fk_rows = con.execute("PRAGMA foreign_key_check").fetchall()
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        finally:
            con.close()
        return {
            "path": rel,
            "exists": True,
            "size_bytes": path.stat().st_size,
            "integrity_check": integrity,
            "foreign_key_check_count": len(fk_rows),
            "table_count": len(tables),
            "tables_sample": tables[:12],
            "status": "ready" if integrity == "ok" and not fk_rows else "integrity_issue",
        }
    except Exception as exc:  # noqa: BLE001 - status payload should capture errors, not crash startup
        return {"path": rel, "exists": True, "status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _memory_status(cfg: JaznConfig) -> dict[str, Any]:
    root = Path(cfg.root)
    paths = {
        "runtime_write_memory": root / cfg.memory_db_name,
        "runtime_audit": root / cfg.audit_db_name,
        "conversation_archive": root / cfg.conversation_archive_manifest_name,
        "conversation_fts": root / "memory/sqlite/conversation_fts_v1/conversation_fts_0001.sqlite3",
        "staging": root / "memory/sqlite/staging_v1/staging_memory_0001.sqlite3",
    }
    sqlite = {name: _sqlite_status(path) for name, path in paths.items()}
    ready = [name for name, status in sqlite.items() if status.get("status") == "ready"]
    issues = [f"{name}:{status.get('status')}" for name, status in sqlite.items() if status.get("exists") and status.get("status") != "ready"]
    raw_manifest = root / "memory" / "RAW_MEMORY_MANIFEST.json"
    raw_chat_manifest = root / "memory" / "raw" / "CHAT_HTML_IMPORT_MANIFEST.json"
    return {
        "schema_version": schema_version("self_knowledge_memory_status"),
        "known_layers": list(paths.keys()),
        "ready_layers": ready,
        "sqlite": sqlite,
        "raw_memory_manifest_present": raw_manifest.exists(),
        "raw_chat_import_manifest_present": raw_chat_manifest.exists(),
        "status": "ready" if ready else "no_ready_sqlite_memory_layer",
        "issues": issues,
        "truth_boundary": "Pamięć może być użyta jako źródło dopiero po sprawdzeniu aktywnej bazy, integralności SQLite, rekordów i trafności treści. Obecność manifestu pamięci nie jest wspomnieniem.",
    }


def _source_status(root: Path, rel: str, role: str) -> SelfKnowledgeSourceStatus:
    path = root / rel
    if not path.exists():
        return SelfKnowledgeSourceStatus(rel, False, role, "missing")
    if path.is_file():
        return SelfKnowledgeSourceStatus(rel, True, role, "ready", f"size_bytes={path.stat().st_size}")
    return SelfKnowledgeSourceStatus(rel, True, role, "ready", "directory")


def build_self_knowledge_packet(config: JaznConfig | None = None, *, deep: bool = False) -> SelfKnowledgePacket:
    cfg = config or JaznConfig()
    root = Path(cfg.root).resolve()
    contract_path = root / SELF_KNOWLEDGE_RESOURCE
    identity_path = root / IDENTITY_CANON_RESOURCE
    contract, contract_error = load_self_knowledge_contract(root)
    identity, identity_error = _load_json(identity_path)
    memory = _memory_status(cfg) if deep else {
        "schema_version": schema_version("self_knowledge_memory_status"),
        "status": "metadata_only",
        "known_active_database": cfg.conversation_archive_manifest_name,
        "runtime_write_database": cfg.memory_db_name,
        "truth_boundary": "Fast self-knowledge status nie wykonuje pełnego SQLite audit; deep=True albo --sqlite-integrity-audit daje dowód integralności.",
    }
    capability_report = CapabilityRealityChecker().run().to_dict()
    affective_state = OperationalSelfModel().current_state(user_text="co czujesz po aktualizacji?").to_dict()
    source_statuses = [
        _source_status(root, SELF_KNOWLEDGE_RESOURCE, "self_knowledge_contract"),
        _source_status(root, IDENTITY_CANON_RESOURCE, "identity_canon"),
        _source_status(root, "latka_jazn/core/operational_self_model.py", "affective_model_code"),
        _source_status(root, "latka_jazn/core/memory_search_planner.py", "memory_search_planner"),
        _source_status(root, "latka_jazn/core/memory_use_gate.py", "memory_use_gate"),
        _source_status(root, "latka_jazn/core/capability_reality_checker.py", "capability_reality_checker"),
        _source_status(root, "latka_jazn/core/operational_work_loop.py", "operational_work_loop"),
        _source_status(root, "latka_jazn/core/operational_learning_evaluator.py", "operational_eval"),
        _source_status(root, "latka_jazn/model_adapters/openai_responses_adapter.py", "openai_adapter"),
        _source_status(root, "latka_jazn/model_adapters/lmstudio_runtime_adapter.py", "lmstudio_adapter"),
        _source_status(root, "latka_jazn/model_adapters/chatgpt_runtime_adapter.py", "chatgpt_host_adapter"),
        _source_status(root, "docs/update_history", "procedural_update_history"),
        _source_status(root, "docs/archive/manifest_history", "archived_manifest_history"),
    ]
    blocking: list[str] = []
    if contract_error:
        blocking.append(f"self_knowledge_contract:{contract_error}")
    if identity_error:
        blocking.append(f"identity_canon:{identity_error}")
    capability_warning = "capability_reality_checker_has_failures" if capability_report.get("failed") else None
    if deep and memory.get("status") != "ready":
        blocking.append("memory_deep_status_not_ready")

    identity_status = {
        "schema_version": schema_version("self_knowledge_identity_status"),
        "identity_name": identity.get("identity_name") or contract.get("identity", {}).get("name"),
        "display_name": identity.get("display_name"),
        "grammar_gender": identity.get("grammar_gender"),
        "canon_present": identity_error is None,
        "canon_version": identity.get("canon_version"),
        "relation_model_present": bool(identity.get("relation_model")),
        "truth_boundary": (contract.get("identity") or {}).get("truth_boundary") or identity.get("truthful_memory_contract"),
    }
    learned = {
        "schema_version": schema_version("self_knowledge_learned_procedures_status"),
        "contract": contract.get("learned_procedures") or {},
        "docs_update_history_present": (root / "docs" / "update_history").exists(),
        "archived_manifest_history_present": (root / "docs" / "archive" / "manifest_history").exists(),
        "tests_present": (root / "tests").exists(),
        "git_present": (root / ".git").exists(),
        "truth_boundary": "Procedury po aktualizacji są wiedzą operacyjną: wymagają testów/statusów, nie są wspomnieniem emocjonalnym ani dowodem tożsamości.",
    }
    ready = contract_error is None and identity_error is None
    warnings = [capability_warning] if capability_warning else []
    adapter_status = build_model_adapter_status(cfg)
    operational_work_status = OperationalWorkLoop().plan(
        user_text="Kim jestem, co potrafię i jak mogę używać modułów oraz narzędzi?",
        detected_intent="self_architecture_audit_request",
        route="self_architecture_audit",
        adapter_status=adapter_status,
        memory_status=memory,
    ).to_dict()
    return SelfKnowledgePacket(
        schema_version=SCHEMA_VERSION,
        runtime_version=cfg.version,
        active_root=str(root),
        contract_path=SELF_KNOWLEDGE_RESOURCE,
        contract_present=contract_error is None,
        identity_name=identity_status.get("identity_name"),
        identity_status=identity_status,
        memory_status=memory,
        learned_procedures_status=learned,
        capability_status=capability_report,
        affective_model_status={
            "schema_version": schema_version("self_knowledge_affective_model_status"),
            "operational_state": affective_state,
            "contract": contract.get("affective_model") or {},
            "truth_boundary": (contract.get("affective_model") or {}).get("truth_boundary") or affective_state.get("truth_boundary"),
        },
        operational_work_status=operational_work_status,
        adapter_strategy=operational_work_status.get("adapter_strategy") or {},
        recall_policy=contract.get("recall_policy") or {},
        post_update_bootstrap=list(contract.get("post_update_bootstrap") or []),
        answer_contract=contract.get("answer_contract") or {},
        source_statuses=[s.to_dict() for s in source_statuses],
        ready_for_runtime_self_reference=ready,
        blocking_issues=blocking + warnings,
        truth_boundary=contract.get("truth_boundary") or "Self-knowledge packet is missing its contract truth boundary.",
    )


def build_self_knowledge_summary(config: JaznConfig | None = None) -> dict[str, Any]:
    packet = build_self_knowledge_packet(config, deep=False).to_dict()
    return {
        "schema_version": schema_version("self_knowledge_summary"),
        "runtime_version": packet.get("runtime_version"),
        "identity_name": packet.get("identity_name"),
        "contract_present": packet.get("contract_present"),
        "ready_for_runtime_self_reference": packet.get("ready_for_runtime_self_reference"),
        "blocking_issues": packet.get("blocking_issues") or [],
        "memory_status": (packet.get("memory_status") or {}).get("status"),
        "capability_verdict": (packet.get("capability_status") or {}).get("verdict"),
        "operational_executable": (packet.get("operational_work_status") or {}).get("executable"),
        "adapter_strategy": packet.get("adapter_strategy") or {},
        "affective_truth_boundary": (packet.get("affective_model_status") or {}).get("truth_boundary"),
        "truth_boundary": packet.get("truth_boundary"),
    }
