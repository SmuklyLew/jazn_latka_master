from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("capability_reality_checker")


@dataclass(slots=True)
class CapabilityRealityCheck:
    name: str
    status: str
    evidence: str
    risk_if_failed: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CapabilityRealityReport:
    schema_version: str
    checks: list[CapabilityRealityCheck]
    passed: int
    failed: int
    verdict: str
    truth_boundary: str
    repair_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["checks"] = [c.to_dict() for c in self.checks]
        return data


class CapabilityRealityChecker:
    """Sprawdza zachowanie funkcji, a nie samą obecność plików."""

    TRUTH_BOUNDARY = (
        "Reality check używa lekkich prób deterministycznych. Nie zastępuje pełnego pytest ani ręcznego smoke, "
        "ale chroni przed audytem opartym wyłącznie na istnieniu plików."
    )

    def run(self) -> CapabilityRealityReport:
        checks: list[CapabilityRealityCheck] = []
        self._check(checks, "classifier_self_architecture", self._classifier_check, "pytanie o rozwój wróci do memory_audit albo fallbacku")
        self._check(checks, "route_registry_self_architecture", self._route_check, "handler nie zostanie uruchomiony mimo poprawnej intencji")
        self._check(checks, "self_memory_gate", self._gate_check, "self-questions dostaną losową pamięć albo żadnej treści")
        self._check(checks, "reflection_no_fabrication", self._reflection_check, "Łatka może udawać wspomnienie bez źródła")
        self._check(checks, "recall_quality_counts_not_memory", self._quality_check, "liczniki zostaną potraktowane jako pamięć")
        passed = sum(1 for c in checks if c.status == "ok")
        failed = len(checks) - passed
        verdict = "ok" if failed == 0 else "partial"
        notes = [f"napraw: {c.name} — {c.risk_if_failed}" for c in checks if c.status != "ok"]
        return CapabilityRealityReport(SCHEMA_VERSION, checks, passed, failed, verdict, self.TRUTH_BOUNDARY, notes)

    def _check(self, checks: list[CapabilityRealityCheck], name: str, func, risk: str) -> None:
        try:
            ok, evidence = func()
            checks.append(CapabilityRealityCheck(name, "ok" if ok else "fail", evidence, risk))
        except Exception as exc:
            checks.append(CapabilityRealityCheck(name, "error", f"{type(exc).__name__}: {exc}", risk))

    @staticmethod
    def _classifier_check() -> tuple[bool, str]:
        from latka_jazn.nlp.dialogue_intent_classifier import DialogueIntentClassifier
        r = DialogueIntentClassifier().classify("Sprawdź co działa w systemie Jaźni i co dodać do v14.8.6.0")
        return r.primary_intent == "self_architecture_audit_request", r.primary_intent

    @staticmethod
    def _route_check() -> tuple[bool, str]:
        from latka_jazn.core.route_registry import RouteRegistry
        e = RouteRegistry().resolve("self_architecture_audit_request")
        return e.handler_name == "SelfArchitectureAuditHandler" and e.route == "self_architecture_audit", f"{e.route}/{e.handler_name}"

    @staticmethod
    def _gate_check() -> tuple[bool, str]:
        from latka_jazn.core.memory_use_gate import MemoryUseGate
        a = MemoryUseGate().decide("Co pamiętasz o swojej Jaźni?", detected_intent="self_architecture_audit_request")
        b = MemoryUseGate().decide("Jak się czujesz?", detected_intent="self_state_question")
        ok = a.allow_memory_content and not b.allow_memory_content
        return ok, f"self={a.allow_memory_content}/{a.memory_role}; state={b.allow_memory_content}/{b.memory_role}"

    @staticmethod
    def _reflection_check() -> tuple[bool, str]:
        from latka_jazn.core.reflection_grounding import ReflectionGroundingSynthesizer
        r = ReflectionGroundingSynthesizer().synthesize(user_text="O czym myśli Łatka?", memory_recall_payload={"items": []})
        return r.boundary_label == "current_turn_inference_no_memory_excerpt", r.boundary_label

    @staticmethod
    def _quality_check() -> tuple[bool, str]:
        from latka_jazn.core.memory_recall_quality import MemoryRecallQualityEvaluator
        q = MemoryRecallQualityEvaluator().evaluate({"counts": {"episodes": 3}, "items": []}, user_text="Co pamiętasz o sobie?")
        return q.counts_only_failure and q.verdict == "counts_only_failure", q.verdict
