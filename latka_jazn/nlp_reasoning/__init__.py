"""Warstwowy polski stos NLP/lexical reasoning dla Jaźni.

v14.8.4 dodaje realny provider Morfeusz2/SGJP, opcjonalny provider PoliMorf,
normalizację literówek, wybór lemma oraz jasną granicę prawdy: kandydaci
morfologiczni nie są jeszcze pełną dezambiguacją znaczenia wypowiedzi.
"""
from .pipeline import PolishReasoningPipeline
from .source_registry import PolishReasoningSourceRegistry
from .models import MorphCandidate, PolishReasoningFrame, ProviderStatus, SelectedLemma, TokenMorphAnalysis

__all__ = [
    "PolishReasoningPipeline",
    "PolishReasoningSourceRegistry",
    "PolishReasoningFrame",
    "ProviderStatus",
    "MorphCandidate",
    "SelectedLemma",
    "TokenMorphAnalysis",
]
