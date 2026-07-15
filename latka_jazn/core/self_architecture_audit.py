from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from latka_jazn.core.capability_reality_checker import CapabilityRealityChecker
from latka_jazn.core.operational_learning_evaluator import OperationalLearningEvaluator
from latka_jazn.core.operational_work_loop import OperationalWorkLoop
from latka_jazn.core.source_provenance import read_source_provenance
from latka_jazn.version import PACKAGE_VERSION, schema_version

SCHEMA_VERSION = schema_version("self_architecture_audit")


@dataclass(slots=True)
class CapabilityCheck:
    key: str
    status: str
    evidence: list[str]
    intended_role: str
    risk_or_gap: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SelfArchitectureAuditReport:
    schema_version: str
    runtime_version: str
    root: str
    capability_checks: list[CapabilityCheck]
    reality_check: dict[str, Any]
    operational_eval: dict[str, Any]
    operational_work_contract: dict[str, Any]
    adapter_status: dict[str, Any]
    memory_status: dict[str, Any]
    privacy_status: dict[str, Any]
    source_provenance: dict[str, Any]
    working_capabilities: list[str]
    partial_capabilities: list[str]
    repair_priorities: list[str]
    v1501_complete_backlog: list[str]
    v1501_backlog: list[str]
    v14860_backlog: list[str]
    source_grounding: list[str]
    acceptance_criteria: list[str]
    truth_boundary: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capability_checks"] = [item.to_dict() for item in self.capability_checks]
        return data


class SelfArchitectureAuditor:
    """Read-only audit of runtime capabilities, gaps, adapters and truth boundaries."""

    TRUTH_BOUNDARY = (
        "Audyt opisuje sprawdzalne pliki, zachowania, pamięć, adaptery, narzędzia i wyniki evals. "
        "Nie zapisuje refleksji, nie zmienia wag modelu i nie jest deklaracją świadomości fenomenalnej."
    )
    SOURCE_GROUNDING = [
        "OpenAI model optimization: reprezentatywne evals i baseline przed zmianą promptu, kodu lub fine-tuningiem.",
        "OpenAI function calling: model zwraca żądanie funkcji; aplikacja wykonuje narzędzie i przekazuje wynik.",
        "LM Studio tool use: lokalny model prosi o narzędzie, a kod hosta je wykonuje i odsyła rezultat.",
        "LM Studio model listing: zdolności modelu trzeba odkryć, a nie zakładać; reasoning i tool use zależą od modelu.",
        "SQLite: integrity_check i foreign_key_check są osobnymi kontrolami spójności.",
    ]
    CAPABILITIES = (
        ("runtime_core", ["main.py", "latka_jazn/core/engine.py", "latka_jazn/core/route_registry.py"], "start, routing, turn contract"),
        ("operational_work_loop", ["latka_jazn/core/operational_work_loop.py", "latka_jazn/core/operational_learning_evaluator.py"], "understand-ground-authorize-act-validate-learn"),
        ("public_reasoning", ["latka_jazn/core/logical_reasoning.py", "latka_jazn/core/reasoning_controller.py"], "public evidence/assumption/unknown/decision audit without hidden CoT"),
        ("memory", ["latka_jazn/core/memory_search_planner.py", "latka_jazn/core/memory_use_gate.py", "latka_jazn/memory/runtime_write_access_contract.py"], "validated recall and truthful write status"),
        ("tool_boundary", ["latka_jazn/core/tool_execution_controller.py", "latka_jazn/core/tool_use_policy.py"], "runtime authorization and execution"),
        ("chatgpt_adapter", ["latka_jazn/model_adapters/chatgpt_runtime_adapter.py"], "host-visible language bridge"),
        ("openai_adapter", ["latka_jazn/model_adapters/openai_responses_adapter.py"], "Responses API generation, structured output and tool requests"),
        ("lmstudio_adapter", ["latka_jazn/model_adapters/lmstudio_runtime_adapter.py"], "local Responses/chat fallback with capability discovery"),
        ("privacy_export", ["latka_jazn/core/private_data_export_gate.py", "latka_jazn/tools/package_export.py"], "one plan and private generated-source exclusion"),
        ("trusted_time", ["latka_jazn/core/runtime_daemon.py", "latka_jazn/core/timestamp_policy.py"], "trusted time retention with expiry"),
        ("self_architecture_audit", ["latka_jazn/core/self_architecture_audit.py", "latka_jazn/core/handlers/self_architecture_audit_handler.py"], "read-only self capability audit"),
        ("source_provenance", ["SOURCE_PROVENANCE.json", "latka_jazn/core/source_provenance.py"], "source commit declaration and exported-package truth boundary"),
    )

    def __init__(self, root: Path | str, *, runtime_version: str | None = None) -> None:
        self.root = Path(root).resolve()
        self.runtime_version = runtime_version or PACKAGE_VERSION

    def audit(
        self,
        *,
        memory_context: dict[str, Any] | None = None,
        store_stats: dict[str, Any] | None = None,
        model_adapter_status: dict[str, Any] | None = None,
    ) -> SelfArchitectureAuditReport:
        checks = [self._check_capability(key, paths, role) for key, paths, role in self.CAPABILITIES]
        reality = CapabilityRealityChecker().run().to_dict()
        operational_eval = OperationalLearningEvaluator().run()
        adapter = dict(model_adapter_status or {})
        memory = {
            "context_items": len((memory_context or {}).get("items") or []),
            "store_stats_available": bool(store_stats),
            "read_only_audit": True,
            "truth_boundary": "Brak trafień w tej turze nie oznacza pustej bazy; pełna integralność wymaga osobnego statusu SQLite.",
        }
        privacy = self._privacy_status()
        provenance = read_source_provenance(self.root).to_dict()
        plan = OperationalWorkLoop().plan(
            user_text="Audytuj architekturę i możliwości Jaźni.",
            detected_intent="self_architecture_audit_request",
            route="self_architecture_audit",
            adapter_status=adapter,
            memory_status=memory,
        ).to_dict()
        partial = [item.key for item in checks if item.status != "ok"]
        if reality.get("failed"):
            partial.append("capability_reality_check")
        if not operational_eval.get("ok"):
            partial.append("operational_learning_eval")
        repair: list[str] = []
        if partial:
            repair.append("P0: napraw wszystkie niezaliczone capability/eval i zachowaj regresję.")
        if not adapter.get("configured") and not adapter.get("host_visible_generation_required"):
            repair.append("P1: skonfiguruj jawny backend językowy albo korzystaj z host bridge; null adapter nie generuje naturalnej wypowiedzi.")
        if provenance.get("status") in {"missing", "invalid"}:
            repair.append("P1: przywróć prawidłowy SOURCE_PROVENANCE.json z bazowym commitem i granicą braku .git.")
        repair.extend([
            "P1: live smoke OpenAI wykonuj tylko z chronionym OPENAI_API_KEY i jawnym modelem.",
            "P1: live smoke LM Studio wykonuj tylko przy uruchomionym lokalnym serwerze i załadowanym modelu.",
            "P2: fine-tuning rozważaj dopiero po reprezentatywnych evals; ta aktualizacja nie zmienia wag.",
        ])
        backlog = [
            "v15.0.1: marker pamięci wykrywany z filesystemu i manifestu, bez fikcyjnego fallbacku v14.7.1.",
            "v15.0.1: routing szerokiego audytu wygrywa z pojedynczym słowem 'działa'.",
            "v15.0.1: source-safe używa jednego planu i wyklucza prywatne źródła wygenerowane z pamięci.",
            "v15.0.1: status runtime write rozdziela inicjalizację, zdolność, zgodę i zaobserwowane zapisy.",
            "v15.0.1: trusted time może być zachowany przez monotoniczny TTL, ale nie jest promowany z local fallback.",
            "v15.0.1: adaptery zwracają niewykonane tool_calls wymagające autoryzacji runtime.",
            "v15.0.1: audyt architektury jest read-only i nie zapisuje refleksji.",
        ]
        acceptance = [
            "Dokładne pytanie użytkownika o możliwości, kod i luki trafia do self_architecture_audit_request.",
            "Krótkie 'Działasz?' nadal trafia do runtime_health_check.",
            "Marker aktywnego runtime wskazuje istniejącą bazę bieżącego układu pamięci.",
            "Source-safe nie zawiera local_private_canon_extension.py ani embedded_sources.py.",
            "Model tool call ma executed=false i requires_runtime_authorization=true.",
            "LM Studio wysyła reasoning/tools tylko po potwierdzeniu odpowiedniej zdolności modelu.",
            "Audyt nie zwiększa liczby grounded reflections.",
            "Evals operacyjne przechodzą bez twierdzenia o fine-tuningu lub zmianie wag.",
        ]
        return SelfArchitectureAuditReport(
            schema_version=SCHEMA_VERSION,
            runtime_version=self.runtime_version,
            root=str(self.root),
            capability_checks=checks,
            reality_check=reality,
            operational_eval=operational_eval,
            operational_work_contract=plan,
            adapter_status=adapter,
            memory_status=memory,
            privacy_status=privacy,
            source_provenance=provenance,
            working_capabilities=[item.key for item in checks if item.status == "ok"],
            partial_capabilities=partial,
            repair_priorities=repair,
            v1501_complete_backlog=backlog,
            v1501_backlog=backlog,
            v14860_backlog=backlog,
            source_grounding=list(self.SOURCE_GROUNDING),
            acceptance_criteria=acceptance,
            truth_boundary=self.TRUTH_BOUNDARY,
        )

    def _privacy_status(self) -> dict[str, Any]:
        from latka_jazn.tools.package_export import GITHUB_SAFE_PRIVATE_EXACT

        known = sorted(GITHUB_SAFE_PRIVATE_EXACT)
        return {
            "known_private_generated_sources": known,
            "all_known_sources_exist": all((self.root / rel).exists() for rel in known),
            "source_safe_policy": "hard_exclude_plus_content_marker_scan",
            "preview_and_export_share_plan_builder": True,
        }

    def _check_capability(self, key: str, paths: list[str], role: str) -> CapabilityCheck:
        evidence: list[str] = []
        missing: list[str] = []
        for rel in paths:
            if (self.root / rel).exists():
                evidence.append(f"exists:{rel}")
            else:
                missing.append(rel)
        status = "ok" if not missing else "partial"
        return CapabilityCheck(
            key=key,
            status=status,
            evidence=evidence + [f"missing:{rel}" for rel in missing],
            intended_role=role,
            risk_or_gap="requires behavioral regression tests" if not missing else "required implementation file missing",
            next_action="keep regression tests green" if not missing else "restore missing files: " + ", ".join(missing),
        )
