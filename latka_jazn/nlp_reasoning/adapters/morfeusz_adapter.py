from __future__ import annotations

import importlib.metadata
from typing import Any

from latka_jazn.nlp_reasoning.models import MorphCandidate, ProviderStatus
from latka_jazn.nlp_reasoning.morph_tags import parse_morfeusz_tag


class MorfeuszReasoningAdapter:
    """Realny adapter Morfeusz2/SGJP.

    Morfeusz zwraca wszystkie możliwe interpretacje fleksyjne bez kontekstowej
    dezambiguacji. Ten adapter nie wybiera jeszcze jedynej prawdy językowej —
    przekazuje kandydatów, a wybór lemma robi osobny lemma_selector.
    """

    provider_name = "morfeusz2-sgjp"

    def __init__(self, dict_name: str = "sgjp", engine: Any | None = None) -> None:
        self.dict_name = dict_name
        self._engine: Any | None = None
        self.status = ProviderStatus(
            provider=self.provider_name,
            available=False,
            mode="offline_optional",
            reason="morfeusz2 package not imported yet",
            license="Morfeusz/SGJP local installation license; verify in source registry",
            source_url="https://morfeusz.sgjp.pl/",
            dictionary=dict_name,
        )
        if engine is not None:
            self._engine = engine
            self.status = ProviderStatus(
                provider=self.provider_name,
                available=True,
                mode="offline_test_or_injected",
                reason=None,
                version="injected-engine",
                license="test/injected engine; production must verify Morfeusz/SGJP license",
                source_url="https://morfeusz.sgjp.pl/",
                dictionary=dict_name,
            )
            return
        try:
            import morfeusz2  # type: ignore

            self._engine = morfeusz2.Morfeusz(dict_name=dict_name)
            try:
                version = importlib.metadata.version("morfeusz2")
            except importlib.metadata.PackageNotFoundError:
                version = getattr(morfeusz2, "__version__", None)
            self.status = ProviderStatus(
                provider=self.provider_name,
                available=True,
                mode="offline",
                reason=None,
                version=str(version) if version else None,
                license="Morfeusz/SGJP local installation license; see registry",
                source_url="https://morfeusz.sgjp.pl/",
                dictionary=dict_name,
            )
        except Exception as exc:  # pragma: no cover - depends on local optional provider
            self.status.reason = f"provider unavailable: {type(exc).__name__}: {exc}"

    def analyse(self, text: str) -> list[MorphCandidate]:
        if self._engine is None:
            return []
        candidates: list[MorphCandidate] = []
        try:
            raw_rows = self._engine.analyse(text or "")
        except Exception as exc:  # pragma: no cover - depends on external engine
            self.status.reason = f"analysis failed: {type(exc).__name__}: {exc}"
            return []
        for row in raw_rows:
            parsed = self._parse_row(row)
            if parsed is not None:
                candidates.append(parsed)
        return candidates

    def _parse_row(self, row: Any) -> MorphCandidate | None:
        try:
            start, end, interp = row
            surface, lemma, tag, *rest = interp
        except Exception:
            return None
        tag_s = str(tag)
        qualifiers = [str(x) for x in rest if x not in (None, "")]
        return MorphCandidate(
            surface=str(surface),
            lemma=str(lemma),
            tag=tag_s,
            start=int(start),
            end=int(end),
            provider=self.provider_name,
            confidence=0.99,
            features=parse_morfeusz_tag(tag_s),
            qualifiers=qualifiers,
            raw={"dictionary": self.dict_name},
        )
