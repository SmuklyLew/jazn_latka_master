from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping

from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version

SCHEMA_VERSION = schema_version("host_visible_finalization", version=PACKAGE_VERSION_FULL)
TIMESTAMP_PREFIX_RE = re.compile(r"^\[[^\]\n]{1,240}\]")


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True, frozen=True)
class HostVisibleFinalizationViolation:
    code: str
    message: str
    repairable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class HostVisibleFinalizationPolicy:
    max_utf8_bytes: int = 2 * 1024 * 1024
    repair_missing_timestamp: bool = True
    reject_foreign_timestamp: bool = True
    reject_empty: bool = True
    schema_version: str = schema_version("host_visible_finalization_policy")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HostVisibleFinalizationContract:
    required_timestamp_header: str
    turn_id: str
    trace_id: str
    policy: HostVisibleFinalizationPolicy = field(default_factory=HostVisibleFinalizationPolicy)
    runtime_version: str = PACKAGE_VERSION_FULL
    contract_hash: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.required_timestamp_header = str(self.required_timestamp_header or "").strip()
        self.turn_id = str(self.turn_id or "").strip()
        self.trace_id = str(self.trace_id or "").strip()
        if not self.required_timestamp_header:
            raise ValueError("required_timestamp_header is required")
        if not self.turn_id:
            raise ValueError("turn_id is required")
        if not self.trace_id:
            raise ValueError("trace_id is required")
        calculated = self.calculate_hash()
        if self.contract_hash and self.contract_hash != calculated:
            raise ValueError("contract_hash mismatch")
        self.contract_hash = calculated

    def calculate_hash(self) -> str:
        payload = {
            "required_timestamp_header": self.required_timestamp_header,
            "turn_id": self.turn_id,
            "trace_id": self.trace_id,
            "policy": self.policy.to_dict(),
            "runtime_version": self.runtime_version,
            "schema_version": self.schema_version,
        }
        return hashlib.sha256(_canonical_json(payload)).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HostVisibleFinalizationResult:
    accepted: bool
    state: str
    final_visible_text: str
    turn_id: str
    trace_id: str
    contract_hash: str
    original_text_sha256: str
    final_text_sha256: str
    violations: list[HostVisibleFinalizationViolation] = field(default_factory=list)
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = SCHEMA_VERSION

    @property
    def repaired(self) -> bool:
        return self.state == "repair"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["repaired"] = self.repaired
        return payload


class HostVisibleFinalizationGate:
    """The single fail-closed gate for host-authored visible text."""

    def finalize(
        self,
        contract: HostVisibleFinalizationContract,
        text: str,
        *,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> HostVisibleFinalizationResult:
        original = str(text or "").strip()
        violations: list[HostVisibleFinalizationViolation] = []

        if turn_id is not None and str(turn_id) != contract.turn_id:
            violations.append(HostVisibleFinalizationViolation("turn_id_mismatch", "The supplied turn_id does not match the contract."))
        if trace_id is not None and str(trace_id) != contract.trace_id:
            violations.append(HostVisibleFinalizationViolation("trace_id_mismatch", "The supplied trace_id does not match the contract."))
        if contract.policy.reject_empty and not original:
            violations.append(HostVisibleFinalizationViolation("empty_text", "Visible text is empty."))
        if len(original.encode("utf-8")) > contract.policy.max_utf8_bytes:
            violations.append(HostVisibleFinalizationViolation("text_too_large", "Visible text exceeds the contract byte limit."))

        exact_timestamp = original.startswith(contract.required_timestamp_header)
        detected_prefix = TIMESTAMP_PREFIX_RE.match(original)
        if not exact_timestamp and detected_prefix:
            if contract.policy.reject_foreign_timestamp:
                violations.append(HostVisibleFinalizationViolation("foreign_timestamp", "A timestamp-like prefix differs from the required runtime timestamp."))
        elif not exact_timestamp and original and contract.policy.repair_missing_timestamp:
            violations.append(HostVisibleFinalizationViolation("missing_timestamp_repaired", "The required timestamp was prepended.", True))

        fatal = [item for item in violations if not item.repairable]
        if fatal:
            return HostVisibleFinalizationResult(
                accepted=False,
                state="reject",
                final_visible_text="",
                turn_id=contract.turn_id,
                trace_id=contract.trace_id,
                contract_hash=contract.contract_hash,
                original_text_sha256=_sha_text(original),
                final_text_sha256=_sha_text(""),
                violations=violations,
            )

        final_text = original if exact_timestamp else f"{contract.required_timestamp_header} {original}".strip()
        state = "repair" if violations else "accept"
        return HostVisibleFinalizationResult(
            accepted=True,
            state=state,
            final_visible_text=final_text,
            turn_id=contract.turn_id,
            trace_id=contract.trace_id,
            contract_hash=contract.contract_hash,
            original_text_sha256=_sha_text(original),
            final_text_sha256=_sha_text(final_text),
            violations=violations,
        )


def finalize_host_visible_text(
    *,
    required_timestamp_header: str,
    turn_id: str,
    trace_id: str,
    text: str,
    supplied_turn_id: str | None = None,
    supplied_trace_id: str | None = None,
    max_utf8_bytes: int = 2 * 1024 * 1024,
) -> HostVisibleFinalizationResult:
    contract = HostVisibleFinalizationContract(
        required_timestamp_header=required_timestamp_header,
        turn_id=turn_id,
        trace_id=trace_id,
        policy=HostVisibleFinalizationPolicy(max_utf8_bytes=max_utf8_bytes),
    )
    return HostVisibleFinalizationGate().finalize(
        contract,
        text,
        turn_id=supplied_turn_id,
        trace_id=supplied_trace_id,
    )
