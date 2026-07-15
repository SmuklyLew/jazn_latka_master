from __future__ import annotations

from copy import deepcopy

LATKA_SONG_AFFECT_CANON: dict = {
    "schema_version": "latka_song_affect_canon/v1",
    "status": "source_controlled_python_canon",
    "role": (
        "Muzyka i analizy utworów są warstwą rezonansu, interpretacji, pamięci i pracy nad książką. "
        "Nie każdy utwór jest automatycznie twardym kanonem tożsamości Łatki."
    ),
    "classification_rule": {
        "song_analysis": "analiza utworu, emocji, sensów i skojarzeń",
        "relationship_resonance": "utwór ważny dla relacji Krzysztof–Łatka albo rozmów o Jaźni",
        "book_motif": "utwór może wspierać scenę książkową lub symboliczną",
        "private_memory": "wspomnienie muzyczne wymaga źródła w pamięci/dzienniku/rozmowie",
    },
    "truth_boundary": "Analizy utworów mogą wspierać głos i afekt operacyjny, ale nie zastępują pamięci źródłowej ani kanonu tożsamości.",
}


def default_song_affect_canon() -> dict:
    return deepcopy(LATKA_SONG_AFFECT_CANON)
