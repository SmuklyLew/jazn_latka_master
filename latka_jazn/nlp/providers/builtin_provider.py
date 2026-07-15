from __future__ import annotations

from pathlib import Path
import json
from .base import ProviderLemmaCandidate

class BuiltinPolishLemmaProvider:
    name = "builtin_safe_polish_v14_6_2"
    available = True

    _RULES: tuple[tuple[str, str, float], ...] = (
        ("ami", "", 0.50), ("ach", "", 0.50), ("ego", "y", 0.46), ("emu", "y", 0.46),
        ("owa", "owy", 0.42), ("owej", "owy", 0.42), ("owych", "owy", 0.42),
        ("ścią", "ść", 0.46), ("scią", "ść", 0.40), ("ści", "ść", 0.44), ("sci", "ść", 0.38),
        ("anie", "ać", 0.42), ("enie", "ić", 0.40), ("acją", "acja", 0.45), ("acja", "acja", 0.70),
        ("ego", "", 0.35), ("ami", "", 0.35), ("ami", "a", 0.32),
    )

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root).resolve() if root else None
        self.overrides = self._load_overrides()

    def _load_overrides(self) -> dict[str, str]:
        candidates = []
        if self.root:
            candidates.append(self.root / "latka_jazn" / "resources" / "polish_lemma_overrides_v14_6_2.json")
            candidates.append(self.root / "memory" / "raw" / "polish_lemma_overrides_v14_6_2.json")
        for path in candidates:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return {str(k): str(v) for k, v in (data.get("overrides") or data).items()} if isinstance(data, dict) else {}
                except Exception:
                    pass
        return {}

    def analyse_token(self, token: str, *, folded: str, context: str = "") -> list[ProviderLemmaCandidate]:
        if not token:
            return []
        candidates: list[ProviderLemmaCandidate] = []
        if folded in self.overrides:
            candidates.append(ProviderLemmaCandidate(
                lemma=self.overrides[folded], confidence=0.94, provider=self.name,
                explanation="jawny override słownika Jaźni"
            ))
        # Zachowaj powierzchniową formę jako bezpiecznego kandydata. To chroni przed złym ucinaniem końcówek.
        candidates.append(ProviderLemmaCandidate(
            lemma=folded, confidence=0.62 if len(folded) >= 4 else 0.78, provider=self.name,
            explanation="forma znormalizowana jako bezpieczny fallback"
        ))
        if len(folded) >= 6:
            for suffix, replacement, confidence in self._RULES:
                if folded.endswith(suffix) and len(folded) - len(suffix) >= 3:
                    lemma = folded[: -len(suffix)] + replacement
                    if lemma and lemma != folded:
                        candidates.append(ProviderLemmaCandidate(
                            lemma=lemma, confidence=confidence, provider=self.name,
                            explanation=f"ostrożna reguła sufiksu -{suffix}"
                        ))
        # dedupe by lemma, highest confidence wins
        best: dict[str, ProviderLemmaCandidate] = {}
        for c in candidates:
            prev = best.get(c.lemma)
            if prev is None or c.confidence > prev.confidence:
                best[c.lemma] = c
        return sorted(best.values(), key=lambda c: (-c.confidence, c.lemma))[:5]
