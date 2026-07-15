from __future__ import annotations

from pathlib import Path
from .polish_lemmatizer import PolishLemmatizationEngine

def build_polish_nlp_frame(text: str, root: Path | None = None) -> dict:
    return PolishLemmatizationEngine(root).analyse(text).to_dict()
