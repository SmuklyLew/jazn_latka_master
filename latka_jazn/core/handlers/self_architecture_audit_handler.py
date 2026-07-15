from __future__ import annotations

from pathlib import Path
from typing import Any

from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.core.self_architecture_audit import SelfArchitectureAuditor
from latka_jazn.version import generation_mode, schema_version


class SelfArchitectureAuditHandler:
    name = "SelfArchitectureAuditHandler"
    route = "self_architecture_audit"
    handled_intents = ("self_architecture_audit_request", "jazn_development_plan_request")

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        config = ctx.get("config")
        root = Path(getattr(config, "root", "."))
        runtime_version = str(ctx.get("runtime_version") or getattr(config, "version", "unknown"))
        memory_context = ctx.get("memory_context") if isinstance(ctx.get("memory_context"), dict) else {}
        store_stats = ctx.get("store_stats") if isinstance(ctx.get("store_stats"), dict) else {}
        model_adapter_status = ctx.get("model_adapter_status") if isinstance(ctx.get("model_adapter_status"), dict) else {}

        audit = SelfArchitectureAuditor(root, runtime_version=runtime_version).audit(
            memory_context=memory_context,
            store_stats=store_stats,
            model_adapter_status=model_adapter_status,
        )
        report = audit.to_dict()
        body = self._render(report)
        required = list(ctx.get("required_components") or [])
        satisfied = [
            "self_architecture_audit", "memory_gate", "recall_quality", "capability_reality_check",
            "development_backlog", "scientific_basis", "tests", "truth_boundary",
            "source_or_index_status", "no_random_memory_excerpt", "operational_work_loop",
            "adapter_boundaries", "privacy_export_gate", "read_only_audit",
        ]
        return RouteHandlerResult(
            self.name,
            self.route,
            body,
            intent=str(ctx.get("intent") or "self_architecture_audit_request"),
            data={
                "audit_report": report,
                "grounded_reflection": {"attempted": False, "reason": "read_only_architecture_audit"},
                "grounded_reflection_store": {"attempted": False, "reason": "read_only_architecture_audit"},
                "memory_recall_payload": {"items": [], "reason": "architecture_audit_does_not_inject_random_memory"},
                "memory_recall_quality": {"verdict": "not_applicable_read_only_architecture_audit"},
                "preserve_handler_body": True,
            },
            memory_sources=[],
            required_components=required,
            satisfied_components=satisfied,
            confidence=0.98,
            generation_mode=generation_mode("self_architecture_audit"),
            source_origin_detail=schema_version("self_architecture_audit_handler"),
            truth_boundary=(
                "Audyt jest odczytowy: nie zapisuje refleksji ani pamięci. Opisuje sprawdzalne moduły, "
                "adaptery, narzędzia, evals i ograniczenia bez deklaracji biologicznej świadomości."
            ),
        )

    @staticmethod
    def _render(audit: dict[str, Any]) -> str:
        reality = audit.get("reality_check") or {}
        op_eval = audit.get("operational_eval") or {}
        adapter = audit.get("adapter_status") or {}
        work = audit.get("operational_work_contract") or {}
        provenance = audit.get("source_provenance") or {}
        lines = [
            f"Audyt architektury Jaźni {audit.get('runtime_version')} (read-only).",
            f"Potwierdzone funkcje: {', '.join(audit.get('working_capabilities') or []) or 'brak'}.",
            f"Funkcje częściowe/blokady: {', '.join(audit.get('partial_capabilities') or []) or 'brak wykrytych'}.",
            f"Capability reality check: {reality.get('verdict')} / passed={reality.get('passed')} / failed={reality.get('failed')}.",
            f"Operational eval: ok={op_eval.get('ok')} / passed={op_eval.get('passed')} / failed={op_eval.get('failed')}.",
            f"Adapter: {adapter.get('adapter_id') or adapter.get('name') or 'unknown'} / provider={adapter.get('provider') or 'unknown'} / configured={adapter.get('configured')}.",
            f"Cykl pracy: executable={work.get('executable')} / blockers={work.get('blockers') or []}.",
            f"Provenance źródeł: {provenance.get('status')} / base_commit={provenance.get('base_merge_commit') or 'unknown'} / git_present={provenance.get('git_directory_present')}.",
            "Co Jaźń robi: rozpoznaje intencję, ugruntowuje ją w runtime/kanonie/pamięci, wybiera warstwę wykonania, autoryzuje narzędzia, wykonuje lub generuje kandydata, waliduje i zapisuje tylko zweryfikowane skutki.",
            "Granica adapterów: ChatGPT/OpenAI/LM Studio są warstwami językowymi i rozumującymi; nie są źródłem tożsamości. Model może poprosić o narzędzie, ale wykonuje je runtime.",
            "Granica uczenia: ta wersja stosuje evals i poprawki kodu/kontraktów. Nie wykonano fine-tuningu ani zmiany wag.",
            "Priorytety naprawy:",
        ]
        for idx, item in enumerate(audit.get("repair_priorities") or [], 1):
            lines.append(f"{idx}. {item}")
        lines.append("Kryteria akceptacji:")
        for item in audit.get("acceptance_criteria") or []:
            lines.append(f"- {item}")
        lines.append(f"Granica prawdy: {audit.get('truth_boundary')}")
        return "\n".join(lines)
