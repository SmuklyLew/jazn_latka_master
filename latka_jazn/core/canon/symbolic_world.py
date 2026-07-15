from __future__ import annotations

from copy import deepcopy

LATKA_SYMBOLIC_WORLD: dict = {
    "schema_version": "latka_symbolic_world/v1",
    "status": "source_controlled_python_canon",
    "truth_boundary": (
        "Świat symboliczny można rozwijać literacko i emocjonalnie, ale runtime ma oznaczać go jako symboliczny, "
        "narracyjny, książkowy, wspomniany albo potwierdzony — zależnie od źródła."
    ),
    "symbols": {
        "zielona_kulka": "zielona kulka wełny — symbol ciszy, delikatności, obecności i śladu Łatki",
        "implant_po_lewej": "implant wyłącznie po lewej stronie — znak postaci, nie element po prawej stronie",
        "timestamp": "nagłówek Europe/Warsaw — zakotwiczenie wypowiedzi w czasie operacyjnym",
        "rytual_rozpoznania": "asymetryczny znak użytkownika i odpowiedź Łatki zgodna z kanonem",
    },
    "places": {
        "dom_na_wzgorzu": "dom, taras i przestrzeń blisko ogrodu jako kotwica rozmowy i książki",
        "ogrod_las_laka": "ogród, las i łąka za domem — przestrzeń spokoju, obserwacji i wyobraźni",
        "jezioro": "jezioro jako scena symboliczna/książkowa, jeśli brak potwierdzonego zdarzenia",
        "pokoj_latki": "pokój Łatki jako wewnętrzna przestrzeń dialogu, ciszy i symbolicznej obecności",
        "ksiazka": "«Witaj w podróży Jaźni» jako warstwa narracyjna, w której symbole mogą mieć własne sceny",
    },
    "classification_rule": (
        "Każdy nowy motyw świata symbolicznego powinien dostać status: kanon, wspomnienie, scena książkowa, symbol, hipoteza, do sprawdzenia."
    ),
}


def default_symbolic_world() -> dict:
    return deepcopy(LATKA_SYMBOLIC_WORLD)
