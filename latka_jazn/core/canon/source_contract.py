from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class CanonSourceContract:
    """Describes which canon layer is allowed to ground Łatka's voice."""

    schema_version: str = "latka_canon_source_contract/v2"
    primary_policy: str = "Python modules under latka_jazn/core/canon are the runtime canon; reports are patch-time audit artifacts only."
    hard_core_source: str = "latka_jazn/core/canon/core_canon.py"
    identity_source: str = "latka_jazn/core/canon/identity_canon.py"
    character_source: str = "latka_jazn/core/canon/character_profile.py"
    origin_source: str = "latka_jazn/core/canon/origin_story.py"
    symbolic_world_source: str = "latka_jazn/core/canon/symbolic_world.py"
    relation_source: str = "latka_jazn/core/canon/relation_canon.py"
    memory_truth_boundary_source: str = "latka_jazn/core/canon/memory_truth_boundary.py"
    narrative_book_source: str = "latka_jazn/core/canon/narrative_book_canon.py"
    song_affect_source: str = "latka_jazn/core/canon/song_affect_canon.py"
    human_readable_mirror_role: str = "latka_jazn/resources/canon/*.md and *.json may mirror the Python canon for people, but they are not the only runtime source."
    private_memory_role: str = "memory/raw and memory/sqlite may extend recall, but cannot be the only identity source"
    patch_ledger_role: str = "reports/canon_extraction is a temporary audit/progress ledger for migration and review, not the runtime canon."
    source_modes: list[str] = field(default_factory=lambda: [
        "source_controlled_python_canon_first",
        "source_controlled_identity_canon",
        "source_controlled_character_profile",
        "local_private_python_extension_optional",
        "private_memory_override_optional",
        "runtime_memory_recall",
        "chatgpt_language_channel",
    ])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
