from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class RuntimeReadiness:
    """Unambiguous release, activation and live-runtime readiness state."""

    installation_ok: bool
    activation_prerequisites_ready: bool
    release_metadata_current: bool
    release_ready: bool
    live_runtime_ready: bool
    memory_v151_ready: bool
    memory_v151_exists: bool | None

    @property
    def activation_ready(self) -> bool:
        """Backward-compatible alias for activation prerequisites readiness."""

        return self.activation_prerequisites_ready

    def summary(self) -> dict[str, str]:
        if self.memory_v151_ready:
            memory_status = "ready"
        elif self.memory_v151_exists is False:
            memory_status = "missing"
        else:
            memory_status = "not_ready"

        return {
            "installation": "ready" if self.installation_ok else "not_ready",
            "activation_prerequisites": (
                "ready" if self.activation_prerequisites_ready else "not_ready"
            ),
            "release": "ready" if self.release_ready else "not_ready",
            "runtime": "active_trusted" if self.live_runtime_ready else "inactive",
            "memory_v151": memory_status,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["activation_ready"] = self.activation_ready
        payload["summary"] = self.summary()
        return payload


def evaluate_runtime_readiness(
    *,
    required_checks: Mapping[str, Any],
    package_integrity_checks: Mapping[str, Any],
    provenance: Mapping[str, Any],
    daemon: Mapping[str, Any],
    memory_v151: Mapping[str, Any],
) -> RuntimeReadiness:
    """Evaluate readiness once so all diagnostic surfaces use identical semantics."""

    installation_ok = all(bool(value) for value in required_checks.values())
    activation_prerequisites_ready = bool(
        installation_ok
        and package_integrity_checks.get("present")
        and package_integrity_checks.get("parse_ok")
        and package_integrity_checks.get("version_matches")
        and package_integrity_checks.get("primary_present")
        and package_integrity_checks.get("legacy_alias_absent")
        and package_integrity_checks.get("canonical_source_name")
        and package_integrity_checks.get("verification_ok")
    )
    live_runtime_ready = bool(
        (daemon.get("active_state") or daemon.get("runtime_active_state"))
        == "active_trusted"
        and daemon.get("pid_alive")
        and daemon.get("endpoint_reachable")
        and daemon.get("heartbeat_fresh")
    )
    release_metadata_current = bool(
        package_integrity_checks.get("verification_ok")
        and provenance.get("version_matches_runtime")
        and provenance.get("status")
        in {"clean_checkout_verified", "verified_export_without_git_history"}
    )
    release_ready = bool(
        activation_prerequisites_ready and release_metadata_current
    )

    exists_value = memory_v151.get("exists")
    memory_v151_exists = exists_value if isinstance(exists_value, bool) else None

    return RuntimeReadiness(
        installation_ok=installation_ok,
        activation_prerequisites_ready=activation_prerequisites_ready,
        release_metadata_current=release_metadata_current,
        release_ready=release_ready,
        live_runtime_ready=live_runtime_ready,
        memory_v151_ready=bool(memory_v151.get("ready")),
        memory_v151_exists=memory_v151_exists,
    )
