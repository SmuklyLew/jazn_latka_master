"""Warstwa NLP Jaźni v14.6.10.

Moduły w tym pakiecie są bezpiecznym fundamentem: działają bez ciężkich
zależności, ale mają stabilny kontrakt dla przyszłych providerów Stanza,
Morfeusz2 albo LLM-context. Nie udają pełnej lematyzacji polszczyzny, jeśli
aktywne jest tylko lokalne fallbackowe rozpoznanie. v14.6.10 dodaje topic guard dla wersji, tematu i aktualnego zakresu odpowiedzi.
"""

from .polish_lemmatizer import PolishLemmatizationEngine, LemmatizationReport, LemmatizedToken, LemmaCandidate
from .polish_normalizer import PolishTextNormalizer
from .polish_tokenizer import PolishTokenizer, PolishToken
from .topic_mismatch_guard import TopicMismatchGuard, TopicMismatchReport
from .intent_feature_engine import IntentFeatureEngine, IntentDecisionFrame, IntentCandidate
from .nlp_capability_audit import NLPCapabilityAudit, NLPCapabilityReport, NLPLayerStatus

__all__ = [
    "PolishLemmatizationEngine",
    "LemmatizationReport",
    "LemmatizedToken",
    "LemmaCandidate",
    "PolishTextNormalizer",
    "PolishTokenizer",
    "PolishToken",
    "TopicMismatchGuard",
    "TopicMismatchReport",
    "IntentFeatureEngine",
    "IntentDecisionFrame",
    "IntentCandidate",
    "NLPCapabilityAudit",
    "NLPCapabilityReport",
    "NLPLayerStatus",
]
