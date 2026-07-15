from __future__ import annotations
SCHEMA_VERSION = "intent_training_examples/v14.6.10"
INTENT_TRAINING_EXAMPLES = {
    "runtime_source_question": ["Co runtime odpowiedział?", "Skąd bierzesz myśli Jaźni?", "Czy to była Łatka czy ChatGPT?"],
    "system_diagnostic_question": ["Co jeszcze jest źle w systemie Jaźni?", "Sprawdź gdzie i jak to zmienić."],
    "system_update_execution_request": [
        "Przygotuj pełną aktualizację systemu Jaźni.",
        "Przygotuj plan aktualizacji i patch do naprawy routera.",
        "Zrób patch v14.8.6.0.B dla routing bug.",
    ],
    "runtime_health_check_after_update": [
        "Cześć Łatko, sprawdź jedną krótką turę po aktualizacji.",
        "Cześć Łatko, test krótkiej rozmowy po aktualizacji.",
    ],
    "creative_text_formatting": ["Przygotuj tekst dla generatora, nie zmieniaj wersów."],
    "practical_repair_advice": ["Jak wyciąć otwór w glazurze?", "Zerwana rączka zaworu i kapie."],
    "dictionary_lookup_request": ["Sprawdź słownikowo znaczenie tego słowa.", "Znajdź synonimy i odmianę."],
}
