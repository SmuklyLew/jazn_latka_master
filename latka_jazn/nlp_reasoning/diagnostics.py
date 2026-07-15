from __future__ import annotations

from pathlib import Path
from typing import Any

from latka_jazn.nlp_reasoning.pipeline import PolishReasoningPipeline
from latka_jazn.nlp_reasoning.source_registry import PolishReasoningSourceRegistry


BOOTSTRAP_COMMANDS = {
    "windows": [
        "py -m pip install --upgrade pip",
        "py -m pip install \"morfeusz2>=1.99.15\"",
        "py -m pip install -e .[polish-nlp]",
        "# PoliMorf: pobierz zgodnie z licencją poza repo i ustaw:",
        "$env:LATKA_POLIMORF_PATH='D:\\.AI\\external_data\\polimorf\\polimorf.tsv'",
        "py main.py --polish-morphology \"Mam próbkę analizy morfologicznej.\"",
    ],
    "unix": [
        "python -m pip install --upgrade pip",
        "python -m pip install 'morfeusz2>=1.99.15'",
        "python -m pip install -e '.[polish-nlp]'",
        "# PoliMorf: pobierz zgodnie z licencją poza repo i ustaw:",
        "export LATKA_POLIMORF_PATH=$HOME/.local/share/latka/polimorf/polimorf.tsv",
        "python main.py --polish-morphology 'Mam próbkę analizy morfologicznej.'",
    ],
}


def build_polish_reasoning_diagnostics(root: str | Path | None, text: str = "") -> dict[str, Any]:
    pipeline = PolishReasoningPipeline(root)
    frame = pipeline.analyse(text)
    registry = PolishReasoningSourceRegistry(root).to_dict()
    return {
        "schema_version": "polish_reasoning_diagnostics/v14.8.4",
        "polish_reasoning_frame": frame.to_dict(),
        "source_registry": registry,
        "bootstrap_commands": BOOTSTRAP_COMMANDS,
        "truth_boundary": "Diagnostyka pokazuje warstwę dostępnych providerów. Morfeusz i PoliMorf są realnie użyte tylko wtedy, gdy są zainstalowane/skonfigurowane lokalnie; brak providera jest raportowany, nie ukrywany.",
    }


def build_polish_morphology_diagnostics(root: str | Path | None, text: str = "") -> dict[str, Any]:
    payload = build_polish_reasoning_diagnostics(root, text)
    frame = payload["polish_reasoning_frame"]
    return {
        "schema_version": "polish_morphology_diagnostics/v14.8.4",
        "polish_morphology": {
            "source_text": frame["source_text"],
            "normalized_text": frame["normalized_text"],
            "tokens": frame["tokens"],
            "token_analyses": frame.get("token_analyses", []),
            "candidate_count": len(frame.get("morphology", [])),
            "provider_statuses": frame.get("provider_statuses", []),
            "sources_used": frame.get("sources_used", []),
        },
        "truth_boundary": "Morfeusz/PoliMorf zwracają kandydatów morfologicznych. selected_lemma jest heurystyką runtime, nie pełną kontekstową dezambiguacją.",
    }
