from __future__ import annotations

from pathlib import Path

from latka_jazn.nlp_reasoning.adapters.morfeusz_adapter import MorfeuszReasoningAdapter
from latka_jazn.nlp_reasoning.adapters.polimorf_adapter import PolimorfDictionaryAdapter
from latka_jazn.nlp_reasoning.adapters.resource_placeholders import optional_provider_statuses
from latka_jazn.nlp_reasoning.adapters.typo_normalizer import TypoNormalizerAdapter
from latka_jazn.nlp_reasoning.lemma_selector import build_token_morph_analyses
from latka_jazn.nlp_reasoning.models import MorphCandidate, PolishReasoningFrame, ProviderStatus
from latka_jazn.nlp_reasoning.morph_tags import parse_morfeusz_tag
from latka_jazn.nlp_reasoning.normalizer import PolishTextNormalizer, fold_polish
from latka_jazn.nlp_reasoning.semantic_rules import infer_semantic_frame
from latka_jazn.nlp_reasoning.source_registry import PolishReasoningSourceRegistry


class PolishReasoningPipeline:
    def __init__(self, root: str | Path | None = None, *, use_optional_providers: bool = True, morfeusz_engine=None, polimorf_path: str | Path | None = None) -> None:
        self.root = Path(root) if root else None
        self.normalizer = PolishTextNormalizer()
        self.typo = TypoNormalizerAdapter()
        self.morfeusz = MorfeuszReasoningAdapter(engine=morfeusz_engine) if use_optional_providers else None
        self.polimorf = PolimorfDictionaryAdapter(root=self.root, path=polimorf_path) if use_optional_providers else None
        self.registry = PolishReasoningSourceRegistry(root)

    def analyse(self, text: str) -> PolishReasoningFrame:
        source_text = text or ""
        normalized = self.typo.normalize(source_text)
        folded = fold_polish(normalized)
        tokens = self.normalizer.tokenize_light(normalized)
        statuses: list[ProviderStatus] = [self.typo.status]
        morphology: list[MorphCandidate] = []

        if self.morfeusz is not None:
            statuses.append(self.morfeusz.status)
            morphology.extend(self.morfeusz.analyse(normalized))

        if self.polimorf is not None:
            # PoliMorf ma być realnym fallbackiem coverage, nie duplikatem masowego mirroru.
            # Jeżeli Morfeusz nie działa albo token nie ma żadnego kandydata, PoliMorf może dołożyć formy z lokalnego pliku.
            polimorf_candidates = self.polimorf.analyse_tokens(tokens)
            statuses.append(self.polimorf.status)
            morphology.extend(self._only_missing_surfaces(tokens, morphology, polimorf_candidates))

        morphology.extend(self._fallback_morphology(tokens, morphology))
        token_analyses = build_token_morph_analyses(tokens, morphology)
        statuses.extend(optional_provider_statuses())
        semantic_frame, reply_policy = infer_semantic_frame(source_text, normalized)
        sources_used = self._sources_used(statuses)
        return PolishReasoningFrame(
            source_text=source_text,
            normalized_text=normalized,
            folded_text=folded,
            tokens=tokens,
            morphology=morphology,
            token_analyses=token_analyses,
            semantic_frame=semantic_frame,
            reply_policy=reply_policy,
            provider_statuses=statuses,
            sources_used=sources_used,
        )

    def _only_missing_surfaces(self, tokens: list[str], existing: list[MorphCandidate], proposed: list[MorphCandidate]) -> list[MorphCandidate]:
        covered = {fold_polish(c.surface) for c in existing if c.surface}
        wanted = {fold_polish(t) for t in tokens}
        return [candidate for candidate in proposed if fold_polish(candidate.surface) in wanted and fold_polish(candidate.surface) not in covered]

    def _fallback_morphology(self, tokens: list[str], existing: list[MorphCandidate] | None = None) -> list[MorphCandidate]:
        covered = {fold_polish(c.surface) for c in (existing or []) if c.surface}
        out: list[MorphCandidate] = []
        for idx, tok in enumerate(tokens):
            if fold_polish(tok) in covered:
                continue
            if tok.isalpha():
                out.append(MorphCandidate(surface=tok, lemma=fold_polish(tok), tag="unknown:heuristic", start=idx, end=idx + 1, provider="latka-heuristic-fallback", confidence=0.35, features={"pos": "unknown", "raw_tag": "unknown:heuristic"}))
            else:
                out.append(MorphCandidate(surface=tok, lemma=tok, tag="interp", start=idx, end=idx + 1, provider="latka-heuristic-fallback", confidence=0.40, features=parse_morfeusz_tag("interp")))
        return out

    def _sources_used(self, statuses: list[ProviderStatus]) -> list[dict[str, str | bool | None]]:
        registry = self.registry.to_dict().get("sources", {})
        used: list[dict[str, str | bool | None]] = []
        for status in statuses:
            if not status.available:
                continue
            source = registry.get(status.provider, {}) if isinstance(registry, dict) else {}
            used.append(
                {
                    "source_id": status.provider,
                    "available": status.available,
                    "mode": status.mode,
                    "version": status.version,
                    "dictionary": status.dictionary,
                    "data_path": status.data_path,
                    "license": status.license or source.get("license"),
                    "source_url": status.source_url or source.get("url"),
                }
            )
        return used
