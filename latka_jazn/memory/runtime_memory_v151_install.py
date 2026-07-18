from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import os

from latka_jazn.memory.runtime_memory_v151 import RuntimeMemoryV151Coordinator
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("runtime_memory_v151_install")
DEFAULT_TIER_DB = "memory/sqlite/runtime_write_v2/runtime_memory_v151.sqlite3"


@dataclass(slots=True, frozen=True)
class RuntimeMemoryInstallStatus:
    installed: bool
    database_path: str
    legacy_classifier_type: str
    layered_fanout_blocked: bool
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = (
        "Instalacja zastępuje zapis fan-out koordynatorem L1/L2. "
        "Nie promuje automatycznie L3 i nie usuwa surowego event ledgeru."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LegacyLayeredMemoryReadOnlyAdapter:
    """Preserve legacy reads while blocking automatic consolidation writes."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        self.blocked_write_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def consolidate_from_plan(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        self.blocked_write_count += 1
        return {
            "status": "blocked_legacy_fanout",
            "schema_version": SCHEMA_VERSION,
            "automatic_l3": False,
            "truth_boundary": (
                "Legacy LayeredMemory fan-out is disabled. The raw turn remains in the event ledger; "
                "selected memory enters L1/L2 through RuntimeMemoryV151Coordinator."
            ),
        }


def _tier_database_path(engine: Any) -> Path:
    config = engine.config
    configured = getattr(config, "memory_tier_db_path", None)
    if configured is not None:
        return Path(configured).expanduser().resolve()
    relative = os.environ.get("JAZN_MEMORY_TIER_DB", DEFAULT_TIER_DB).strip() or DEFAULT_TIER_DB
    path = Path(relative)
    if path.is_absolute():
        raise ValueError("JAZN_MEMORY_TIER_DB must be relative to runtime root")
    root = Path(config.root).expanduser().resolve()
    resolved = (root / path).resolve()
    resolved.relative_to(root)
    return resolved


def install_runtime_memory_v151(engine: Any) -> RuntimeMemoryInstallStatus:
    current = getattr(engine, "runtime_memory", None)
    if isinstance(current, RuntimeMemoryV151Coordinator):
        layered = getattr(engine, "layered_memory", None)
        return RuntimeMemoryInstallStatus(
            installed=False,
            database_path=str(current.database_path),
            legacy_classifier_type=type(current.classifier).__name__,
            layered_fanout_blocked=isinstance(layered, LegacyLayeredMemoryReadOnlyAdapter),
        )
    if current is None:
        raise RuntimeError("engine has no runtime memory classifier")

    database_path = _tier_database_path(engine)
    engine.runtime_memory_legacy_classifier = current
    engine.runtime_memory = RuntimeMemoryV151Coordinator(
        database_path,
        classifier=current,
    )
    layered = getattr(engine, "layered_memory", None)
    if layered is not None and not isinstance(layered, LegacyLayeredMemoryReadOnlyAdapter):
        engine.layered_memory = LegacyLayeredMemoryReadOnlyAdapter(layered)
    return RuntimeMemoryInstallStatus(
        installed=True,
        database_path=str(database_path),
        legacy_classifier_type=type(current).__name__,
        layered_fanout_blocked=isinstance(getattr(engine, "layered_memory", None), LegacyLayeredMemoryReadOnlyAdapter),
    )
