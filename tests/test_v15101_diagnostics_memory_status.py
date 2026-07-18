from __future__ import annotations

from pathlib import Path

from latka_jazn.cli_commands import diagnostics
from latka_jazn.config import JaznConfig
from latka_jazn.memory.memory_tier_store import MemoryTierStore
from latka_jazn.memory.runtime_memory_v151_install import resolve_memory_tier_database_path


def test_memory_v151_diagnostic_reports_missing_then_ready_without_writes(tmp_path: Path) -> None:
    cfg = JaznConfig(root=tmp_path)
    missing = diagnostics._memory_v151_status(cfg)
    assert missing["exists"] is False
    assert missing["ready"] is False
    assert missing["read_only"] is True

    database = resolve_memory_tier_database_path(tmp_path)
    with MemoryTierStore(database) as store:
        assert store.validate(full=False)["ok"] is True
    before_mtime = database.stat().st_mtime_ns
    before_files = sorted(path.name for path in database.parent.iterdir())

    ready = diagnostics._memory_v151_status(cfg)

    assert ready["exists"] is True
    assert ready["ready"] is True
    assert ready["read_only"] is True
    assert database.stat().st_mtime_ns == before_mtime
    assert sorted(path.name for path in database.parent.iterdir()) == before_files


def test_status_and_doctor_expose_separate_v151_subsystem(tmp_path: Path, monkeypatch) -> None:
    cfg = JaznConfig(root=tmp_path)
    database = resolve_memory_tier_database_path(tmp_path)
    with MemoryTierStore(database) as store:
        assert store.validate(full=False)["ok"] is True

    monkeypatch.setattr(
        diagnostics,
        "build_startup_status",
        lambda *_args, **_kwargs: type("Status", (), {"to_dict": lambda self: {
            "model_adapter_status": {"status": "test"},
            "conversation_archive_status": {},
            "runtime_write_access_status": {},
            "active_cache_status": {},
        }})(),
    )
    monkeypatch.setattr(diagnostics, "status_daemon", lambda *_args, **_kwargs: {})

    status = diagnostics.status_payload(tmp_path, probe_endpoint=False)
    assert status["memory_v151"]["ready"] is True

    # Doctor uses the same status payload. Patch external release checks so this test
    # isolates memory reporting and does not turn package metadata into test fixtures.
    monkeypatch.setattr(diagnostics, "status_payload", lambda *_args, **_kwargs: status)
    monkeypatch.setattr(
        diagnostics,
        "package_integrity_manifest_status",
        lambda _root: type("P", (), {
            "present": False,
            "path": None,
            "primary_present": False,
            "legacy_present": False,
            "source_name": None,
            "to_dict": lambda self: {},
        })(),
    )
    monkeypatch.setattr(diagnostics, "verify_package_integrity_manifest", lambda _root: {"ok": False, "errors": []})
    monkeypatch.setattr(
        diagnostics,
        "read_source_provenance",
        lambda *_args, **_kwargs: type("R", (), {"to_dict": lambda self: {}})(),
    )

    doctor = diagnostics.doctor_payload(tmp_path)
    assert doctor["readiness"]["memory_v151_ready"] is True
    assert doctor["live_evidence"]["memory_v151_ready"] is True
    assert doctor["subsystems"]["memory"]["tier_v151"]["ready"] is True
    assert "runtime_write_legacy" in doctor["subsystems"]["memory"]
