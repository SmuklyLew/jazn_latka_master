from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import re
SCHEMA_VERSION = "speech_act_detector/v14.6.10"
@dataclass(slots=True)
class SpeechActReport:
    speech_act: str; confidence: float; evidence: list[str]; schema_version: str = SCHEMA_VERSION
    def to_dict(self)->dict[str,Any]: return asdict(self)
class SpeechActDetector:
    def detect(self, text: str) -> SpeechActReport:
        low=(text or '').lower(); evidence=[]
        if '?' in low or low.startswith(('czy ','co ','jak ','dlaczego ','skąd ','skad ')):
            return SpeechActReport('question',0.78,['question_mark_or_interrogative'])
        if re.search(r'\b(przygotuj|zrób|zrob|sprawdź|sprawdz|napraw|popraw)\b', low):
            return SpeechActReport('directive',0.80,['imperative_or_task_verb'])
        if re.search(r'\b(dziękuję|dziekuje|super|dobrze|ok)\b', low):
            return SpeechActReport('feedback',0.65,['feedback_marker'])
        return SpeechActReport('statement',0.52,['default_statement'])
