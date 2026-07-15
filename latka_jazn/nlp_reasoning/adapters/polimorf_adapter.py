from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from latka_jazn.nlp_reasoning.models import MorphCandidate, ProviderStatus
from latka_jazn.nlp_reasoning.morph_tags import parse_morfeusz_tag
from latka_jazn.nlp_reasoning.normalizer import fold_polish


class PolimorfDictionaryAdapter:
    """Opcjonalny lokalny adapter PoliMorf.

    Patch nie vendoruje słownika. Adapter czyta wskazany lokalnie plik TSV/TAB
    dopiero wtedy, gdy użytkownik pobierze zasób zgodnie z licencją i ustawi
    LATKA_POLIMORF_PATH albo umieści plik w external_data/polimorf/.

    Obsługiwany minimalny format linii: surface<TAB>lemma<TAB>tag.
    Dodatkowe kolumny są zachowywane w raw["columns"].
    """

    provider_name = "polimorf"

    def __init__(self, root: str | Path | None = None, path: str | Path | None = None) -> None:
        self.root = Path(root) if root else Path.cwd()
        self.path = self._resolve_path(path)
        self.status = ProviderStatus(
            provider=self.provider_name,
            available=False,
            mode="offline_recommended_external_data",
            reason="PoliMorf TSV/TAB not configured; set LATKA_POLIMORF_PATH or place file in external_data/polimorf/",
            license="BSD-2-Clause; verify exact downloaded source before use",
            source_url="https://zil.ipipan.waw.pl/PoliMorf",
            data_path=str(self.path) if self.path else None,
            dictionary="polimorf",
        )
        if self.path and self.path.exists() and self.path.is_file():
            self.status = ProviderStatus(
                provider=self.provider_name,
                available=True,
                mode="offline_external_data",
                reason=None,
                license="BSD-2-Clause; verify exact downloaded source before use",
                source_url="https://zil.ipipan.waw.pl/PoliMorf",
                data_path=str(self.path),
                dictionary="polimorf",
            )

    def _resolve_path(self, path: str | Path | None) -> Path | None:
        if path:
            return Path(path)
        env = os.environ.get("LATKA_POLIMORF_PATH")
        if env:
            return Path(env)
        candidates = [
            self.root / "external_data" / "polimorf" / "polimorf.tsv",
            self.root / "external_data" / "polimorf" / "polimorf.tab",
            self.root / "workspace_runtime" / "polish_reasoning" / "polimorf.tsv",
            self.root / "workspace_runtime" / "polish_reasoning" / "polimorf.tab",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def analyse_tokens(self, tokens: Iterable[str], *, limit_per_token: int = 32) -> list[MorphCandidate]:
        if not self.status.available or self.path is None:
            return []
        wanted = {fold_polish(tok): tok for tok in tokens if tok and any(ch.isalpha() for ch in tok)}
        if not wanted:
            return []
        counts = {key: 0 for key in wanted}
        out: list[MorphCandidate] = []
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    row = self._parse_line(line)
                    if row is None:
                        continue
                    surface, lemma, tag, columns = row
                    folded_surface = fold_polish(surface)
                    if folded_surface not in wanted:
                        continue
                    if counts[folded_surface] >= limit_per_token:
                        continue
                    counts[folded_surface] += 1
                    out.append(
                        MorphCandidate(
                            surface=wanted[folded_surface],
                            lemma=lemma,
                            tag=tag,
                            start=None,
                            end=None,
                            provider=self.provider_name,
                            confidence=0.82,
                            features=parse_morfeusz_tag(tag),
                            raw={"columns": columns, "data_path": str(self.path)},
                        )
                    )
                    if all(count >= limit_per_token for count in counts.values()):
                        break
        except Exception as exc:  # pragma: no cover - filesystem-specific
            self.status.reason = f"PoliMorf read failed: {type(exc).__name__}: {exc}"
            self.status.available = False
            return []
        return out

    def _parse_line(self, line: str) -> tuple[str, str, str, list[str]] | None:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None
        parts = stripped.split("\t")
        if len(parts) < 3:
            parts = stripped.split()
        if len(parts) < 3:
            return None
        surface, lemma, tag = parts[0], parts[1], parts[2]
        return surface, lemma, tag, parts
