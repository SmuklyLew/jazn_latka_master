from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from latka_jazn.core.homeostasis import HomeostasisInput, HomeostasisRegulator
from latka_jazn.core.predictive_dialogue_engine import PredictiveDialogueEngine
from latka_jazn.core.system_temporal_semantics import SystemTemporalSemantics, TemporalEvent
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("cognitive_runtime_coordinator")


@dataclass(slots=True)
class CognitiveRuntimeCoordinator:
    temporal: SystemTemporalSemantics = SystemTemporalSemantics()
    homeostasis: HomeostasisRegulator = HomeostasisRegulator()
    predictive: PredictiveDialogueEngine = PredictiveDialogueEngine()

    def plan_turn(
        self,
        *,
        user_text: str,
        explicit_intent: str | None = None,
        temporal_events: list[TemporalEvent] | None = None,
        homeostasis_input: HomeostasisInput | None = None,
    ) -> dict[str, Any]:
        predictions = self.predictive.predict(user_text, explicit_intent=explicit_intent)
        regulation = self.homeostasis.decide(homeostasis_input or HomeostasisInput())
        graph = self.temporal.build_graph(temporal_events or [])
        return {
            "schema_version": SCHEMA_VERSION,
            "predictions": [item.to_dict() for item in predictions],
            "regulation": regulation.to_dict(),
            "temporal_graph": graph,
            "explicit_intent": explicit_intent,
            "prediction_may_override_user_intent": False,
            "truth_boundary": "Cognitive modules are operational models and cannot assert biological experience.",
        }
