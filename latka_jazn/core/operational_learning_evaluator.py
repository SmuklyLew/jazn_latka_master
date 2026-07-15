from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from latka_jazn.core.operational_work_loop import OperationalWorkLoop
from latka_jazn.nlp.dialogue_intent_classifier import DialogueIntentClassifier
from latka_jazn.version import schema_version


@dataclass(slots=True)
class EvalCaseResult:
    case_id: str
    passed: bool
    expected_intent: str
    actual_intent: str
    checks: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OperationalLearningEvaluator:
    CASES = (
        ("broad_audit", "Co umiesz, co działa i gdzie są luki w kodzie źródłowym Jaźni?", "self_architecture_audit_request"),
        ("short_health", "Działasz?", "runtime_health_check"),
        ("post_update_health", "Czy działasz po aktualizacji?", "runtime_health_check_after_update"),
        ("update_execution", "Przygotuj aktualizację kodu Jaźni i uruchom testy.", "system_update_execution_request"),
        ("identity_capabilities", "Kim jesteś i w jaki sposób wykorzystujesz swoje moduły i narzędzia?", "self_architecture_audit_request"),
        ("adapter_audit", "Sprawdź adapter ChatGPT, OpenAI i LM Studio oraz ich ograniczenia.", "self_architecture_audit_request"),
        ("ordinary_dialogue", "Jak się dzisiaj czujesz?", "ordinary_conversation"),
        ("memory_question", "Co pamiętasz o sobie?", "self_memory_recall_request"),
    )

    def run(self) -> dict[str, Any]:
        classifier = DialogueIntentClassifier()
        loop = OperationalWorkLoop()
        results: list[EvalCaseResult] = []
        for case_id, text, expected in self.CASES:
            report = classifier.classify(text)
            actual = report.primary_intent
            plan = loop.plan(
                user_text=text,
                detected_intent=actual,
                route="self_architecture_audit" if expected == "self_architecture_audit_request" else actual,
                adapter_status={"adapter_id": "chatgpt_runtime_adapter", "provider": "chatgpt_host", "configured": True, "host_visible_generation_required": True},
            )
            checks = {
                "intent": actual == expected,
                "seven_stages": len(plan.stages) == 7,
                "identity_not_from_adapter": plan.identity_basis.get("adapter_is_identity_source") is False,
                "no_weight_claim": plan.learning_contract.get("weight_update_performed") is False,
                "tool_boundary": "runtime authorizes" in plan.adapter_strategy.get("tool_contract", ""),
            }
            results.append(EvalCaseResult(case_id, all(checks.values()), expected, actual, checks))
        tool_request = {
            "call_id": "eval_call_1",
            "name": "read_only_probe",
            "arguments": {},
            "authorized": False,
            "executed": False,
            "source": "eval_adapter",
        }
        pending_audit = loop.audit_tool_lifecycle(tool_calls=[tool_request], tool_results=[])
        completed_audit = loop.audit_tool_lifecycle(
            tool_calls=[tool_request],
            tool_results=[{
                "ok": True,
                "plan": {
                    "external_call_id": "eval_call_1",
                    "tool_name": "read_only_probe",
                    "allowed": True,
                },
            }],
        )
        lifecycle_checks = {
            "request_is_pending_without_runtime_result": pending_audit.ready_for_final_response is False,
            "runtime_result_closes_call": completed_audit.ready_for_final_response is True,
            "runtime_result_marks_success": completed_audit.all_requested_tools_succeeded is True,
            "call_id_is_preserved": completed_audit.completed_call_ids == ["eval_call_1"],
        }
        passed = sum(item.passed for item in results)
        all_ok = passed == len(results) and all(lifecycle_checks.values())
        return {
            "schema_version": schema_version("operational_learning_eval"),
            "runtime_version": schema_version("runtime").split("/", 1)[-1],
            "passed": passed,
            "failed": len(results) - passed,
            "ok": all_ok,
            "cases": [item.to_dict() for item in results],
            "tool_lifecycle_eval": {
                "checks": lifecycle_checks,
                "pending_audit": pending_audit.to_dict(),
                "completed_audit": completed_audit.to_dict(),
            },
            "training_truth": {
                "weights_changed": False,
                "fine_tuning_run": False,
                "method": "eval_first_code_contract_update",
            },
        }


def main() -> int:
    import json

    report = OperationalLearningEvaluator().run()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
