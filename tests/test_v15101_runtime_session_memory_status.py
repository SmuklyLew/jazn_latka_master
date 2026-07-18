from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from latka_jazn.core.runtime_session import JaznRuntimeSession
from latka_jazn.memory.memory_tier_store import MemoryTierStore


@dataclass
class FakeInstallStatus:
    database_path: str

    def to_dict(self) -> dict:
        return {"installed": True, "database_path": self.database_path}


def test_minimal_session_reports_uninitialized_installer_without_error() -> None:
    session = object.__new__(JaznRuntimeSession)
    payload = session._memory_v151_status_payload()
    assert payload["available"] is False
    assert payload["reason"] == "installer_not_initialized"
    assert payload["install"] is None
    assert payload["store"] is None


def test_initialized_session_reports_ready_store(tmp_path: Path) -> None:
    database = tmp_path / "tiers.sqlite3"
    with MemoryTierStore(database) as store:
        assert store.validate(full=False)["ok"] is True
    session = object.__new__(JaznRuntimeSession)
    session.memory_v151_install_status = FakeInstallStatus(str(database))

    payload = session._memory_v151_status_payload()

    assert payload["available"] is True
    assert payload["install"]["database_path"] == str(database)
    assert payload["store"]["ready"] is True
    assert payload["store"]["read_only"] is True
