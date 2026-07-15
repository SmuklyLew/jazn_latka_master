from __future__ import annotations
from dataclasses import dataclass, asdict
from latka_jazn.core.scientific_basis import all_references, reference_by_key, references_for_module

@dataclass(frozen=True, slots=True)
class SourceReference:
    key: str
    title: str
    domain: str
    url: str
    pillar: str
    use_for: str
    caution: str

# Kompatybilność z v14.4: dawny interfejs SOURCE_LIBRARY zostaje,
# ale dane są zasilane pełną biblioteką scientific_basis v14.5.
SOURCE_LIBRARY: tuple[SourceReference, ...] = tuple(
    SourceReference(
        key=r["key"],
        title=r["title"],
        domain=r["domain"],
        url=r["url"],
        pillar=", ".join(r["used_by_modules"]),
        use_for=r["operational_claim"],
        caution=r["caution"],
    ) for r in all_references()
)

class SourceLibrary:
    def list(self) -> list[dict]:
        return [asdict(s) for s in SOURCE_LIBRARY]

    def by_pillar(self, pillar: str) -> list[dict]:
        low = pillar.lower()
        return [asdict(s) for s in SOURCE_LIBRARY if low in s.pillar.lower() or low in s.domain.lower() or low in s.use_for.lower()]

    def by_module(self, module_key: str) -> list[dict]:
        return references_for_module(module_key)

    def by_key(self, key: str) -> dict | None:
        return reference_by_key(key)
