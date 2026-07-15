from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Generic, TypeVar

from latka_jazn.core.source_classifier import SourceClassification, SourceClassifier
from latka_jazn.core.tool_access_gate import ToolAccessDecision, ToolAccessGate
from latka_jazn.core.tool_call_provenance import ToolCallProvenance, build_tool_call_provenance
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("tool_execution_controller")
T = TypeVar("T")


class ToolExecutionDenied(PermissionError):
    pass


@dataclass(slots=True)
class ToolExecutionPlan:
    tool_name: str
    action: str
    source: SourceClassification
    gate: ToolAccessDecision
    provenance: ToolCallProvenance
    external_call_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    @property
    def allowed(self) -> bool:
        valid, _ = self.provenance.validate()
        return self.gate.allowed and self.source.safe_to_use and valid

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed"] = self.allowed
        return payload


@dataclass(slots=True)
class ToolExecutionResult(Generic[T]):
    ok: bool
    plan: ToolExecutionPlan
    result: T | None = None
    error: str | None = None
    completed_at_utc: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.completed_at_utc:
            self.completed_at_utc = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "plan": self.plan.to_dict(),
            "result": self.result,
            "error": self.error,
            "completed_at_utc": self.completed_at_utc,
        }


class ToolExecutionController:
    """Mandatory source -> guard -> permission -> provenance -> execution path."""

    def __init__(
        self,
        *,
        source_classifier: SourceClassifier | None = None,
        access_gate: ToolAccessGate | None = None,
    ) -> None:
        self.source_classifier = source_classifier or SourceClassifier()
        self.access_gate = access_gate or ToolAccessGate()

    def plan(
        self,
        *,
        tool_name: str,
        action: str,
        source_kind: str,
        source_content: str = "",
        source_origin: str = "user_request",
        actor: str = "runtime",
        reason: str = "explicit_tool_request",
        write_action: bool | None = None,
        user_confirmed: bool = False,
        validated_source: bool = False,
        external_call_id: str | None = None,
    ) -> ToolExecutionPlan:
        source = self.source_classifier.classify(
            source_kind,
            content=source_content,
            origin=source_origin,
            validated=validated_source,
        )
        gate = self.access_gate.decide(
            tool_name,
            action=action,
            write_action=write_action,
            user_confirmed=user_confirmed,
            source=source,
        )
        provenance = build_tool_call_provenance(
            tool_name=tool_name,
            action=action,
            actor=actor,
            reason=reason,
            input_origin=source_origin,
            gate=gate,
        )
        return ToolExecutionPlan(tool_name, action, source, gate, provenance, external_call_id=external_call_id)

    def execute(self, plan: ToolExecutionPlan, callback: Callable[..., T], /, *args: Any, **kwargs: Any) -> ToolExecutionResult[T]:
        if not plan.allowed:
            raise ToolExecutionDenied(
                f"tool execution denied: source={plan.source.reason}, gate={plan.gate.reason}"
            )
        try:
            result = callback(*args, **kwargs)
            return ToolExecutionResult(ok=True, plan=plan, result=result)
        except Exception as exc:
            return ToolExecutionResult(ok=False, plan=plan, error=f"{type(exc).__name__}: {exc}")
