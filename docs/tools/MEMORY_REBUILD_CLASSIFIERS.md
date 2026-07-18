# Audyt klasyfikatorów po dwóch testach memory_rebuild

## Cel

Ta poprawka powstała po porównaniu dwóch pełnych przebiegów `memory_rebuild`.
Nie zmienia pięciu baz, nie nadpisuje `raw_json` i nie promuje wpisów do L2/L3.
Rozbudowuje wyłącznie deterministyczną klasyfikację oraz jej audyt.

## Granice prawdy

Klasyfikator może opisać:

- granicę prawdy wpisu (`user_confirmed`, `source_recorded`, `book_scene`,
  `symbolic`, `draft`, `inferred`);
- profil źródła (`book_work`, `symbolic`, `system_meta`, `media_analysis`,
  `knowledge_reference`, `experiential`, `event_record`, `unclassified`);
- wiele domen jednocześnie;
- jawne dowody reguł i powody wymagające ręcznej kontroli.

Klasyfikator nie może:

- zamienić sceny książkowej, snu albo promptu w wydarzenie fizyczne;
- uznać wyniku domeny za wspomnienie;
- zatwierdzić doświadczenia;
- zapisać czegokolwiek do L2 lub L3.

Surowy rekord pozostaje w `raw_json`. Klasyfikacja jest pochodną, którą można
ponownie policzyć po zmianie reguł.

## Poprawki wynikające z testu 02

Dodano bezpieczne rozpoznawanie między innymi:

- `historia_wyobrazona` jako `book_scene` / `book_work`;
- `notatka systemowa` jako `source_recorded` / `system_meta`;
- złożonych etykiet rozdzielanych przez `_`, `+`, przecinek, ukośnik i myślnik;
- krótkich terminów jako całych tokenów, aby `lek` nie pasował do `lekkość`,
  a `sen` nie pasował do `sensoryczna`;
- domen `daily_life`, `system_identity`, `image`, `video` i `reading`;
- wieloetykietowego kontekstu, np. jeden wpis może należeć jednocześnie do
  `relationship`, `book`, `work` i `nature`.

Tytuł rozmowy jest teraz tylko słabym kontekstem. Treść konkretnej wiadomości
ma większą wagę, dzięki czemu wielotematyczna rozmowa nie pozostaje na zawsze
w domenie wynikającej z historycznego tytułu.

## Audyt bez zapisu

Po zaimportowaniu źródeł uruchom:

```powershell
py -X utf8 tools\memory_rebuild.py `
  --root "D:\.AI\jazn_memory_test_02" `
  --json audit-classifiers `
  --limit 100 |
  Tee-Object "D:\.AI\jazn_memory_test_02\10_audit_classifiers.json"
```

Raport pokazuje:

- zapisany i ponownie wyliczony rozkład `truth_status`;
- liczbę rozbieżności;
- profile wpisów;
- domeny wieloetykietowe;
- wpisy bez timestampu;
- niejednoznaczne lub sprzeczne etykiety;
- stan analizy segmentów rozmów;
- segmenty o niskiej pewności.

Komenda nie uruchamia `analyse-topics`, nie tworzy kandydatów i nie modyfikuje
źródeł. Przed `build-experience-candidates` oczekiwane jest:

```text
truth_mismatch_count: 0
automatic_experience: false
automatic_l2: false
automatic_l3: false
```

`classification_review_count` może być większe od zera. Oznacza kolejkę kontroli,
a nie błąd integralności.

Jeżeli `truth_mismatch_count` jest większe od zera, najpierw wykonaj próbę bez zapisu:

```powershell
py -X utf8 tools\memory_rebuild.py `
  --root "D:\.AI\jazn_memory_test_02" `
  --json reclassify-journal `
  --dry-run |
  Tee-Object "D:\.AI\jazn_memory_test_02\11_reclassify_journal_dry_run.json"
```

Następnie zastosuj wyłącznie zmianę pochodnej klasyfikacji:

```powershell
py -X utf8 tools\memory_rebuild.py `
  --root "D:\.AI\jazn_memory_test_02" `
  --json reclassify-journal |
  Tee-Object "D:\.AI\jazn_memory_test_02\12_reclassify_journal.json"
```

Ta operacja aktualizuje `truth_status` w `journal_entries` i odpowiadającym
dokumencie FTS. Nie zmienia `raw_json`, `content_sha256`, numeru rewizji,
źródłowego pliku ani warstw pamięci. Sama operacja jest zapisana w
`import_catalog.sqlite3`.

## Zalecana kolejność odbudowy

1. Zaimportuj wszystkie eksporty zawierające `conversations.json`.
2. Sprawdź deduplikację, rewizje, konflikty i integralność.
3. Zaimportuj dziennik.
4. Uruchom `audit-classifiers` i ewentualnie `reclassify-journal`.
5. Dopiero po kompletnym L0 uruchom `analyse-topics`.
6. Ponownie uruchom `audit-classifiers`.
7. Zbuduj ograniczoną próbkę kandydatów.
8. Przejrzyj kandydatów ręcznie.
9. Zatwierdzaj pojedyncze rekordy z jawnym powodem.

`chat.html` pozostaje źródłem pomocniczym dla zasobów. Bez
`conversations.json` nie odtwarza bezstratnie drzewa ani alternatywnych gałęzi.

## Podstawa projektowa

Rozwiązanie zachowuje charakter narzędzia rebuild:

- jawne reguły działają jak niezależne funkcje etykietujące; ich dowody są
  widoczne i mogą być porównywane;
- klasyfikacja jest wieloetykietowa, ponieważ pojedynczy wpis może mieć kilka
  prawdziwych domen;
- wynik nie jest przedstawiany jako prawdopodobieństwo, jeżeli nie został
  skalibrowany na niezależnym zbiorze ręcznie oznaczonych danych;
- przy przyszłym uczeniu modelu podział walidacyjny powinien grupować rekordy
  według rozmowy lub źródła, aby warianty tej samej rozmowy nie trafiały
  jednocześnie do treningu i testu;
- FTS5 służy do wyszukiwania i rankingu źródeł, a nie do ustanawiania prawdy.

Inspiracje techniczne:

- Ratner i in., *Data Programming* oraz *Snorkel: Rapid Training Data Creation
  with Weak Supervision* — jawne, potencjalnie konfliktowe funkcje etykietujące;
- dokumentacja Python `re` — granice słów w Unicode;
- dokumentacja scikit-learn — klasyfikacja wieloetykietowa, kalibracja i
  `GroupKFold`;
- oficjalna dokumentacja SQLite FTS5 — `MATCH`, `bm25`, `rank` i integralność
  indeksu pełnotekstowego.

Nie dodano zależności od scikit-learn ani Snorkel. Są to zasady projektowe dla
przyszłego, opcjonalnego etapu; bieżący rebuild pozostaje lekki, deterministyczny
i możliwy do audytu.
