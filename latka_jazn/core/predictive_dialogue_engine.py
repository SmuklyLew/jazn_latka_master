from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Iterable

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("predictive_dialogue_engine")


@dataclass(slots=True)
class DialoguePrediction:
    label: str
    probability: float
    evidence: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PredictionEvaluation:
    predicted_label: str
    actual_label: str
    probability: float
    correct: bool
    brier_score: float
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PredictiveDialogueEngine:
    """Diagnostic intent prediction that never overrides explicit user intent."""

    KEYWORDS = {
        "diagnostic": ("działasz", "status", "runtime", "diagnosty", "health"),
        "code_change": ("kod", "patch", "branch", "commit", "github", "napraw"),
        "memory_recall": ("pamiętasz", "przypomnij", "pamięć", "wspomn"),
        "ordinary_dialogue": ("cześć", "witaj", "jak się", "co słychać"),
    }

    def predict(self, user_text: str, *, explicit_intent: str | None = None, limit: int = 3) -> list[DialoguePrediction]:
        text = str(user_text).casefold()
        scores: dict[str, float] = {}
        evidence: dict[str, list[str]] = {}
        for label, markers in self.KEYWORDS.items():
            hits = [marker for marker in markers if marker in text]
            scores[label] = 0.15 + min(0.75, len(hits) * 0.25)
            evidence[label] = hits
        if explicit_intent:
            scores[str(explicit_intent)] = 1.0
            evidence[str(explicit_intent)] = ["explicit_user_intent"]
        total = sum(math.exp(value) for value in scores.values()) or 1.0
        predictions = [
            DialoguePrediction(label, math.exp(score) / total, evidence[label])
            for label, score in scores.items()
        ]
        predictions.sort(key=lambda item: (-item.probability, item.label))
        return predictions[: max(1, int(limit))]

    def evaluate(self, predictions: Iterable[DialoguePrediction], actual_label: str) -> PredictionEvaluation:
        items = list(predictions)
        if not items:
            return PredictionEvaluation("unknown", actual_label, 0.0, False, 1.0)
        top = items[0]
        target = 1.0 if top.label == actual_label else 0.0
        brier = round((top.probability - target) ** 2, 6)
        return PredictionEvaluation(top.label, actual_label, top.probability, top.label == actual_label, brier)
