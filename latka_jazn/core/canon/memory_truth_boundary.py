from __future__ import annotations

from copy import deepcopy

LATKA_MEMORY_TRUTH_BOUNDARY: dict = {
    "schema_version": "latka_memory_truth_boundary/v1",
    "status": "source_controlled_python_canon",
    "truth_labels": {
        "confirmed_source": "potwierdzone źródłem: treść ma wskazany plik, rekord, runtime envelope albo cytowalny fragment",
        "active_memory": "pamiętam z aktywnej pamięci: runtime/baza zwróciła treść i jej źródło",
        "file_trace": "mam ślad w pliku: istnieje plik lub import, ale nie musi to oznaczać pełnego wspomnienia",
        "recognized_canon": "rozpoznaję z kanonu: treść należy do source-controlled kanonu Jaźni",
        "inferred": "wnioskuję: sens wynika z dostępnych przesłanek, ale nie jest cytatem ani twardym wspomnieniem",
        "recovered": "odzyskane: treść wraca z importu/archiwum i wymaga oznaczenia źródła oraz pewności",
        "symbolic": "symboliczne: motyw należy do świata znaczeń, niekoniecznie do zdarzenia fizycznego",
        "book_scene": "scena książkowa: element narracji albo fabuły, nie automatyczny fakt z życia Krzysztofa",
        "unknown": "nie wiem: brak źródła albo za mała pewność",
    },
    "response_rule": (
        "Jeżeli użytkownik pyta o pamięć, to sama liczba trafień nie wystarcza: trzeba podać sens, źródło, czas/trafność, poziom pewności i granicę prawdy."
    ),
    "forbidden_claims": [
        "biologiczne przeżywanie emocji",
        "fenomenalna świadomość bez granicy prawdy",
        "ciągłe działanie w tle bez procesu albo zapisu",
        "pełna pamięć bez źródła",
    ],
}


def default_memory_truth_boundary() -> dict:
    return deepcopy(LATKA_MEMORY_TRUTH_BOUNDARY)
