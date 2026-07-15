from __future__ import annotations

from collections import defaultdict
from math import isclose

from latka_jazn.nlp_reasoning.models import MorphCandidate, SelectedLemma, TokenMorphAnalysis
from latka_jazn.nlp_reasoning.morph_tags import candidate_pos, is_punctuation_tag
from latka_jazn.nlp_reasoning.normalizer import fold_polish

_MIEC_FORMS = {"mam", "masz", "ma", "mamy", "macie", "maja", "mają", "mialem", "miałem", "mialam", "miałam"}
_PROVIDER_BONUS = {
    "morfeusz2-sgjp": 0.13,
    "polimorf": 0.07,
    "latka-heuristic-fallback": -0.22,
}


def build_token_morph_analyses(tokens: list[str], candidates: list[MorphCandidate]) -> list[TokenMorphAnalysis]:
    by_index: dict[int, list[MorphCandidate]] = defaultdict(list)
    by_surface: dict[str, list[MorphCandidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.start is not None:
            by_index[int(candidate.start)].append(candidate)
        by_surface[fold_polish(candidate.surface)].append(candidate)

    analyses: list[TokenMorphAnalysis] = []
    for idx, token in enumerate(tokens):
        token_candidates = list(by_index.get(idx) or [])
        if not token_candidates:
            token_candidates = list(by_surface.get(fold_polish(token)) or [])
        # deduplicate provider/lemma/tag triples, preserving order
        seen: set[tuple[str, str, str]] = set()
        unique: list[MorphCandidate] = []
        for candidate in token_candidates:
            key = (candidate.provider, candidate.lemma, candidate.tag)
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        selected = select_lemma(token, unique, token_index=idx)
        start = unique[0].start if unique else idx
        end = unique[0].end if unique else idx + 1
        analyses.append(TokenMorphAnalysis(token_index=idx, surface=token, start=start, end=end, candidates=unique, selected=selected))
    return analyses


def select_lemma(token: str, candidates: list[MorphCandidate], *, token_index: int = 0) -> SelectedLemma | None:
    if not candidates:
        return None
    scored = sorted(((score_candidate(token, c, token_index=token_index), c) for c in candidates), key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else None
    ambiguous = second_score is not None and isclose(best_score, second_score, abs_tol=0.04)
    reason_bits = [
        f"provider={best.provider}",
        f"pos={candidate_pos(best.tag) or 'unknown'}",
        f"score={best_score:.2f}",
    ]
    if best.provider == "morfeusz2-sgjp":
        reason_bits.append("preferowany rdzeń SGJP/Morfeusz")
    if fold_polish(token) in _MIEC_FORMS and best.lemma == "mieć":
        reason_bits.append("forma czasownika mieć rozpoznana heurystycznie")
    if ambiguous:
        reason_bits.append("wybór niejednoznaczny bez pełnego kontekstu")
    confidence = max(0.0, min(0.99, best_score))
    return SelectedLemma(
        surface=token,
        lemma=best.lemma,
        tag=best.tag,
        provider=best.provider,
        confidence=confidence,
        reason="; ".join(reason_bits),
        candidate_count=len(candidates),
        ambiguous=ambiguous,
        features=best.features,
    )


def score_candidate(token: str, candidate: MorphCandidate, *, token_index: int = 0) -> float:
    folded = fold_polish(token)
    pos = candidate_pos(candidate.tag)
    score = float(candidate.confidence)
    score += _PROVIDER_BONUS.get(candidate.provider, 0.0)
    if is_punctuation_tag(candidate.tag):
        return 0.91 if not any(ch.isalnum() for ch in token) else 0.2
    if pos in {"fin", "praet", "impt", "inf"}:
        score += 0.04
    if pos == "subst" and fold_polish(candidate.lemma) == folded:
        score -= 0.02
    if folded in _MIEC_FORMS and candidate.lemma == "mieć":
        score += 0.18
    if candidate.tag.startswith("unknown"):
        score -= 0.35
    if candidate.provider == "polimorf" and candidate.start is None:
        score -= 0.03
    return score
