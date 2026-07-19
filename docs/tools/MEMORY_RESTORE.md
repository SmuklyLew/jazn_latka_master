# Restore pamięci Jaźni — `tools/restore_memory.py`

## Cel

`restore_memory.py` jest bezpiecznym orkiestratorem istniejącego `memory_rebuild`.
Nie implementuje drugiego importera i nie zmienia reguł prawdy. Steruje wyłącznie
już testowanymi operacjami:

1. wykrycie i inspekcja źródeł;
2. plan bez zapisu;
3. spójna kopia istniejących baz SQLite;
4. import każdego eksportu zawierającego `conversations.json`;
5. kontrola integralności po każdym źródle;
6. dry-run i import dziennika;
7. pełna końcowa weryfikacja pięciu baz;
8. audyt klasyfikatorów;
9. opcjonalny dry-run reklasyfikacji;
10. opcjonalna analiza tematów;
11. opcjonalna, ograniczona próbka kandydatów do ręcznego przeglądu;
12. porównanie z bazami wcześniejszych testów.

Narzędzie nigdy nie zatwierdza doświadczeń i nie promuje rekordów do L2/L3.

## Uruchomienie kursorowego interfejsu

Z katalogu repozytorium:

```powershell
py -X utf8 .\tools\restore_memory.py
```

Interfejs korzysta z istniejącego `CursorMenu`, dlatego nie dodaje nowej
zależności. Działa ze strzałkami na Windows i POSIX. Dostępny jest też tryb
tekstowy, gdy wejście nie jest terminalem TTY.

## Zalecany pełny test 3

1. Ustaw katalog zawierający wszystkie eksporty ChatGPT.
2. Włącz skanowanie podkatalogów tylko wtedy, gdy eksporty są rozproszone.
3. Zeskanuj katalog i zaznacz pliki ZIP z eksportami oraz właściwy dziennik.
4. Ustaw tryb `developer`.
5. Ustaw cel poza repozytorium, np.:

```text
D:\.AI\jazn_memory_test_03
```

6. Ustaw bazy porównawcze `test_01` i `test_02`.
7. Pokaż plan bez zapisu.
8. Sprawdź listę odrzuconych plików.
9. Wpisz `RESTORE`, aby rozpocząć zapis.
10. Po zakończeniu przeczytaj `summary.json`, `events.jsonl`, pełny `verify` i
    porównanie baz.

Plan używa tymczasowej kopii bazy archiwum i nie tworzy katalogu docelowego.

## Tryby celu

### `developer`

- cel musi znajdować się poza repozytorium;
- domyślna nazwa to `jazn_memory_test_03`;
- raporty trafiają do:

```text
<target>\reports\memory_restore\restore_<timestamp>\
```

- kopie baz trafiają do:

```text
<target>\backups\before_restore_<timestamp>\
```

Potwierdzenie zapisu:

```text
RESTORE
```

### `system`

Tryb zapisuje do pięciu baz pod aktywnym folderem systemu. Przed zapisem wymaga:

- `run.py`;
- `latka_jazn/version.py`;
- `PACKAGE_INTEGRITY_MANIFEST.json`;
- poprawnego `doctor`;
- zatrzymanego runtime/daemona.

Potwierdzenie jest związane z dokładną ścieżką:

```text
SYSTEM_RESTORE:D:\.AI\jazn_latka_master
```

Raporty i kopie są zapisywane w `workspace_runtime/memory_restore`, aby nie
trafiały do repozytorium.

## Wybór źródeł

Obsługiwane są:

- `.zip`;
- `.json`;
- `.jsonl`;
- `.ndjson`;
- `.html` / `.htm` do inspekcji.

Do bezstratnego importu rozmów wymagane jest `conversations.json`. Sam
`chat.html` nie odtwarza drzewa, rewizji i alternatywnych gałęzi.

Znane pliki techniczne, w tym `*.package.json`, manifest integralności i
proweniencja, nie są oferowane jako źródła pamięci. Dodatkowo JSON jest
akceptowany jako dziennik tylko wtedy, gdy ma wiarygodny sygnał: nazwę dziennika,
ustrukturyzowane etykiety, timestampy albo rozpoznane profile. Zapobiega to
importowaniu raportów i manifestów jako wspomnień.

## Strona ustawień

- skanowanie podkatalogów;
- walidacja po każdym źródle;
- pełny `integrity_check` albo szybszy `quick_check`;
- kontynuowanie po błędzie;
- spójna kopia istniejących baz;
- audyt klasyfikatorów;
- dry-run reklasyfikacji dziennika;
- zastosowanie reklasyfikacji;
- analiza tematów;
- wymuszenie ponownej analizy segmentów;
- limit próbki kandydatów;
- częstotliwość zdarzeń postępu.

Bezpieczne wartości domyślne:

```text
backup:                     włączony
verify_after_each:          włączony
full_validation:            włączony
continue_on_error:          wyłączony
audit_classifiers:          włączony
reclassify_journal_dry_run: włączony
apply_reclassification:     wyłączony
analyse_topics:             wyłączony
candidate_limit:            0
```

## Postęp i wznowienie

Importer przekazuje zdarzenia m.in.:

```text
source_hash_started
source_validation_completed
transaction_started
conversations_imported
transaction_committed
database_validation_completed
```

UI pokazuje bieżący plik, liczbę rozmów, węzłów, wiadomości i czas. Wszystkie
zdarzenia są równocześnie zapisywane do `events.jsonl`.

Ponowne uruchomienie na tych samych plikach jest bezpieczne: importer rozpoznaje
identyczny SHA-256 i nie duplikuje rozmów. Nowy przebieg otrzymuje osobny katalog
raportów z identyfikatorem obejmującym mikrosekundy.

## Spójne kopie SQLite

Kopia nie jest wykonywana zwykłym `Copy-Item` działającej bazy. Narzędzie używa
`sqlite3.Connection.backup()`, a następnie pełnego `integrity_check` i
`foreign_key_check` kopii.

## Porównanie z test_01 i test_02

Porównanie jest tylko do odczytu. Raport zawiera:

- integralność i liczniki wszystkich tabel;
- SHA-256 schematów i plików;
- brakujące lub zmienione rozmowy;
- brakujące lub zmienione węzły;
- brakujące źródłowe SHA eksportów;
- brakujące lub zmienione wpisy dziennika.

Dla archiwum rozmów najważniejsze warunki zachowania poprzedniego testu to:

```text
missing_conversations:        0
changed_conversations:        0
missing_nodes:                0
changed_nodes:                0
missing_import_source_hashes: 0
```

## Tryb bez UI

Plan wszystkich wykrytych źródeł:

```powershell
py -X utf8 .\tools\restore_memory.py `
  --no-ui `
  --plan-only `
  --source-dir "D:\Eksporty ChatGPT" `
  --target-root "D:\.AI\jazn_memory_test_03" `
  --mode developer `
  --all-discovered
```

Restore z jawnie wybranymi plikami:

```powershell
py -X utf8 .\tools\restore_memory.py `
  --no-ui `
  --source "D:\Eksporty ChatGPT\export-2025-09-20.zip" `
  --source "D:\Eksporty ChatGPT\export-2025-07-19.zip" `
  --source "D:\Eksporty ChatGPT\dziennik.json" `
  --source-dir "D:\Eksporty ChatGPT" `
  --target-root "D:\.AI\jazn_memory_test_03" `
  --mode developer `
  --confirm RESTORE
```

Konfigurację można zapisać i ponownie wczytać jako JSON.

## Granice bezpieczeństwa

- brak automatycznego zatwierdzania doświadczeń;
- brak automatycznej promocji do L2/L3;
- sceny książkowe i roleplay pozostają `book_scene`;
- sny i wizje pozostają `symbolic`;
- surowe źródła nie są modyfikowane;
- plan nie zapisuje do celu;
- tryb systemowy jest blokowany przy aktywnym daemonie;
- każdy przebieg ma niezależny raport i audyt;
- błąd domyślnie zatrzymuje dalszy import;
- `continue_on_error` wymaga jawnego włączenia.

## Dalszy rozwój po test_3

Po pełnym `test_3` można rozważyć:

- zapis kontrolnego checkpointu z SHA źródeł i licznikami baz;
- osobną stronę ręcznego przeglądu niejednoznacznych klasyfikacji;
- eksport raportu HTML;
- automatyczną detekcję zakresu dat eksportu;
- obsługę katalogów obserwowanych bez automatycznego zapisu;
- testy awarii zasilania i przerwania procesu na większych kopiach danych;
- opcjonalny bardziej rozbudowany frontend TUI, nadal korzystający z tego samego
  bezgłowego orkiestratora.
