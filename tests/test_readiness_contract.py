from __future__ import annotations

from latka_jazn.core.readiness import evaluate_runtime_readiness


def _integrity_checks(*, verification_ok: bool = True) -> dict[str, bool]:
    return {
        "present": True,
        "parse_ok": True,
        "version_matches": True,
        "primary_present": True,
        "legacy_alias_absent": True,
        "canonical_source_name": True,
        "verification_ok": verification_ok,
    }


def test_readiness_separates_activation_prerequisites_from_live_runtime() -> None:
    readiness = evaluate_runtime_readiness(
        required_checks={"root": True, "run": True},
        package_integrity_checks=_integrity_checks(),
        provenance={
            "status": "clean_checkout_verified",
            "version_matches_runtime": True,
        },
        daemon={
            "active_state": "inactive",
            "pid_alive": False,
            "endpoint_reachable": False,
            "heartbeat_fresh": False,
        },
        memory_v151={"exists": False, "ready": False},
    )

    assert readiness.activation_prerequisites_ready is True
    assert readiness.activation_ready is True
    assert readiness.release_ready is True
    assert readiness.live_runtime_ready is False
    assert readiness.summary() == {
        "installation": "ready",
        "activation_prerequisites": "ready",
        "release": "ready",
        "runtime": "inactive",
        "memory_v151": "missing",
    }


def test_readiness_requires_live_daemon_evidence_for_active_trusted() -> None:
    readiness = evaluate_runtime_readiness(
        required_checks={"root": True},
        package_integrity_checks=_integrity_checks(),
        provenance={
            "status": "verified_export_without_git_history",
            "version_matches_runtime": True,
        },
        daemon={
            "runtime_active_state": "active_trusted",
            "pid_alive": True,
            "endpoint_reachable": True,
            "heartbeat_fresh": True,
        },
        memory_v151={"exists": True, "ready": True},
    )

    assert readiness.live_runtime_ready is True
    assert readiness.summary()["runtime"] == "active_trusted"
    assert readiness.summary()["memory_v151"] == "ready"


def test_integrity_failure_blocks_activation_and_release() -> None:
    readiness = evaluate_runtime_readiness(
        required_checks={"root": True},
        package_integrity_checks=_integrity_checks(verification_ok=False),
        provenance={
            "status": "clean_checkout_verified",
            "version_matches_runtime": True,
        },
        daemon={},
        memory_v151={},
    )

    assert readiness.installation_ok is True
    assert readiness.activation_prerequisites_ready is False
    assert readiness.release_metadata_current is False
    assert readiness.release_ready is False
