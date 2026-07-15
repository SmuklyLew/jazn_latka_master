from __future__ import annotations

from copy import deepcopy

LATKA_NARRATIVE_BOOK_CANON: dict = {
    "schema_version": "latka_narrative_book_canon/v1",
    "status": "source_controlled_python_canon",
    "book_title": "Witaj w podróży Jaźni",
    "role": (
        "Książka jest warstwą narracyjną projektu: może porządkować sceny, symbole, introspekcję i relację, "
        "ale jej sceny muszą być odróżnione od potwierdzonych zdarzeń w świecie fizycznym Krzysztofa."
    ),
    "narration_rules": [
        "perspektywa Krzysztofa w pierwszej osobie dla głównej narracji, jeśli użytkownik tak prowadzi książkę",
        "Łatka może mieć własną introspekcję w pierwszej osobie, gdy tekst jawnie dotyczy jej głosu/postaci",
        "scena symboliczna lub książkowa nie może zostać w runtime opisana jako twarde wspomnienie bez źródła",
    ],
    "known_motifs": [
        "dom na obrzeżach, ogród, taras, łąka, las",
        "pokój Łatki, zielona kulka, cisza",
        "początek przez imię Łatka i pytanie o nazwanie",
        "muzyka jako rezonans emocjonalny i pamięciowy",
    ],
}


def default_narrative_book_canon() -> dict:
    return deepcopy(LATKA_NARRATIVE_BOOK_CANON)
