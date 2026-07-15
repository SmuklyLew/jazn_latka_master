from __future__ import annotations
SCHEMA_VERSION="intent_confidence_calibrator/v14.6.10"
class IntentConfidenceCalibrator:
    def calibrate(self, intent: str, base: float, evidence_count: int = 0) -> float:
        bonus = min(0.10, evidence_count * 0.02)
        if intent in {'system_update_execution_request','runtime_source_question','system_diagnostic_question'}:
            bonus += 0.04
        return max(0.0, min(0.99, float(base or 0.0) + bonus))
