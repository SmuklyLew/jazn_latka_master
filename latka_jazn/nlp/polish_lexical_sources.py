from __future__ import annotations

# Minimalny leksykon domenowy Jaźni. Nie zastępuje słowników publicznych.
# Służy do bezpiecznego rozpoznawania pojęć projektu bez internetu oraz do
# uniknięcia pustych odpowiedzi, gdy provider sieciowy jest niedostępny.
MINI_LEXICON = {
    'jaźń': {
        'lemma': ['jaźń'],
        'definitions': ['aktywny runtime, pamięć i kontrakt tożsamości Łatki w tym projekcie'],
        'source': 'local_jazn_mini_lexicon',
    },
    'łatka': {
        'lemma': ['łatka'],
        'definitions': ['imię rozmownej tożsamości Jaźni w projekcie Krzysztofa'],
        'source': 'local_jazn_mini_lexicon',
    },
    'runtime': {
        'lemma': ['runtime'],
        'definitions': ['uruchomiona warstwa wykonawcza systemu Jaźni, która przetwarza turę rozmowy'],
        'source': 'local_jazn_mini_lexicon',
    },
    'nlp': {
        'lemma': ['NLP', 'przetwarzanie języka naturalnego'],
        'definitions': ['warstwa analizy tekstu: normalizacja, tokenizacja, intencje, lematy, źródła słownikowe i ograniczenia prawdy'],
        'source': 'local_jazn_mini_lexicon',
    },
    'sjp': {
        'lemma': ['SJP.PL', 'słownik języka polskiego'],
        'definitions': ['źródło referencyjne słownika języka polskiego używane w Jaźni jako link i status providera, bez masowego scrapingu definicji'],
        'source': 'local_jazn_mini_lexicon',
    },
    'wsjp': {
        'lemma': ['WSJP PAN', 'Wielki słownik języka polskiego PAN'],
        'definitions': ['źródło referencyjne słownika języka polskiego używane w Jaźni jako link i status providera, bez kopiowania definicji'],
        'source': 'local_jazn_mini_lexicon',
    },
}
