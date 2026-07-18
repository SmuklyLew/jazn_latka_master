# Plan narzędzia importu eksportów ChatGPT do SQLite

## Metadane

- Repozytorium: `SmuklyLew/jazn_latka`
- Branch wykonawczy: `tools/memory-import-to-db`
- Punkt bazowy: `0b8ee72286dc33f499d66c752a6f67906529325a`
- Zakres: samodzielne narzędzie inspekcji, deduplikacji i importu eksportów ChatGPT
- Poza zakresem tego brancha: zmiana głównego runtime i mechanizmu promocji pamięci; te prace należą do `update/v15.1.0.1`

## Cel

Zbudować bezpieczny program CLI z opcjonalnym interfejsem kursorowym, który:

1. analizuje ZIP-y i foldery eksportu ChatGPT;
2. zachowuje pełne drzewa rozmów oraz alternatywne gałęzie;
3. nie kopiuje drugi raz identycznego eksportu pod inną nazwą;
4. nie dubluje rozmów i wiadomości występujących w kolejnych eksportach;
5. rozpoznaje starszy eksport będący podzbiorem nowszego;
6. pokazuje tematy rozmów przed importem;
7. pozwala wybrać zakres do archiwum i kolejki przeglądu pamięci;
8. nie promuje automatycznie archiwalnego tekstu do pamięci długotrwałej;
9. tworzy spójną, zweryfikowaną bazę SQLite o mniejszym rozmiarze niż rozwinięte pliki HTML/JSON.

## Granice prawdy

- `conversations.json` jest kanonicznym źródłem drzewa rozmów.
- `chat.html` jest źródłem pomocniczym, między innymi mapowania `assetsJson`; nie należy importować wyłącznie wyrenderowanej gałęzi HTML.
- Timestamp nie zastępuje relacji `parent` i `children`.
- Brak timestampu nie może być uzupełniany wymyśloną godziną.
- Wybranie tematu do przeglądu nie jest równoznaczne z utworzeniem wspomnienia.
- Import archiwum nie dowodzi aktywnej pamięci ani uruchomionej Jaźni.
- Narzędzie nie modyfikuje `memory/` aktywnego runtime bez jawnego wskazania docelowej bazy i potwierdzenia operacji.

## Obsługiwane wejścia

- pojedynczy ZIP eksportu ChatGPT;
- wiele ZIP-ów;
- rozpakowany folder eksportu;
- `conversations.json` bez HTML;
- HTML z `assetsJson` jako uzupełnienie;
- przyszłe warianty eksportu przez adapter formatu.

Narzędzie ma rozpoznać typ wejścia po treści, nie tylko po nazwie i rozszerzeniu.

## Komendy CLI

```text
inspect   — inspekcja plików, CRC, SHA, liczników, timestampów i gałęzi
plan      — plan zmian wobec istniejącej bazy bez zapisu
import    — transakcyjny import do bazy archiwalnej
verify    — integralność, klucze obce, liczniki i fingerprinty
exports   — lista znanych eksportów i ich relacji
conversations — lista rozmów i statusów zmian
branches  — podgląd current_path i alternatywnych gałęzi
topics    — analiza tematów i segmentów
search    — wyszukiwanie FTS z odtworzeniem kontekstu drzewa
review    — kolejka kandydatów do późniejszej promocji pamięci
ui        — interfejs kursorowy
```

Każda komenda ma opcję `--json` dla automatyzacji i testów.

## Interfejs kursorowy

Menu główne:

1. Dodaj pliki eksportów
2. Zbadaj pliki bez importu
3. Porównaj z istniejącą bazą
4. Pokaż duplikaty i podzbiory
5. Przeglądaj rozmowy
6. Przeglądaj tematy
7. Wybierz rozmowy lub tematy do importu
8. Uruchom import
9. Zweryfikuj bazę
10. Kolejka przeglądu pamięci
11. Raporty i eksport diagnostyczny

Wymagania UX:

- klawisze strzałek, Enter, ESC i Ctrl+X;
- brak zatwierdzania operacji zapisu przez sam Enter na pustym pytaniu;
- jawny ekran podsumowania przed zapisem;
- postęp per eksport i per etap;
- możliwość przerwania bez pozostawienia częściowego importu;
- widoczny tryb `dry-run`;
- rozdzielenie `import do archiwum` od `dodaj do kolejki przeglądu pamięci`.

## Kanoniczny model danych

### `source_exports`

- `export_id`
- `source_sha256` — unikalny hash całego pliku
- `source_size`
- `source_filename`
- `format_version`
- `first_seen_at_utc`
- `last_seen_at_utc`
- `crc_status`
- `parse_status`
- `conversation_count`
- `message_count`
- `tree_fingerprint`

Identyczny SHA pod inną nazwą tworzy tylko nowe wystąpienie źródła, nie nowy eksport logiczny.

### `source_occurrences`

Przechowuje każdą nazwę i lokalizację wejścia bez kopiowania treści:

- `occurrence_id`
- `export_id`
- `observed_filename`
- `observed_path`
- `observed_at_utc`

### `conversations`

- `conversation_id`
- `title`
- `create_time`
- `update_time`
- `current_node_id`
- `canonical_tree_fingerprint`
- `first_seen_export_id`
- `last_seen_export_id`
- `revision`
- `archived_payload_codec`
- `archived_payload_blob`

Całe drzewo rozmowy jest przechowywane raz jako skompresowany obiekt. Aktualizacja nowszym eksportem tworzy nową rewizję tylko wtedy, gdy drzewo naprawdę się zmieniło.

### `conversation_revisions`

- hash drzewa;
- źródłowy eksport;
- liczba dodanych/usuniętych/zmienionych węzłów;
- powód rewizji;
- timestamp importu.

Źródłowa historia nie jest nadpisywana bez śladu.

### `nodes`

- `conversation_id`
- `node_id`
- `parent_node_id`
- `message_id`
- `role`
- `content_type`
- `create_time`
- `timestamp_status`
- `on_current_path`
- `branch_id`
- `structural_ordinal`
- `semantic_content_hash`
- `source_payload_hash`
- `text_length`

### `message_text`

Pełny tekst nie powinien być kopiowany do wielu tabel. Preferowane warianty:

- tekst w skompresowanym obiekcie rozmowy;
- opcjonalny deduplikowany content store po SHA;
- FTS contentless przechowujący wyłącznie indeks i lokalizatory.

### `assets`

- `asset_pointer`
- `original_filename`
- `content_type`
- `conversation_id`
- `node_id`
- `message_id`
- `availability_status`
- `source_export_id`
- `resolved_local_path` opcjonalnie
- `content_sha256` po odnalezieniu pliku

### `topic_segments`

- `segment_id`
- `conversation_id`
- `start_node_id`
- `end_node_id`
- `domain`
- `mode`
- `truth_status`
- `confidence`
- `classifier_version`
- `evidence_json`
- `manual_override`

### `memory_review_queue`

Przechowuje tylko kandydatów do późniejszej oceny przez runtime lub użytkownika:

- `candidate_id`
- `segment_id`
- `candidate_type`
- `reason`
- `status`
- `reviewed_by`
- `reviewed_at_utc`
- `promotion_target`

Narzędzie nie zapisuje bezpośrednio rekordów L3.

## Deduplikacja

### Poziom 1 — identyczny plik

Klucz: `source_sha256`.

Przykład wymagany testem:

- `chat.zip`
- `chat_export_2025.07.16.zip`

Jeśli SHA jest identyczne, drugi plik otrzymuje status `identical_export_duplicate`; jego treść nie jest ponownie parsowana ani zapisywana.

### Poziom 2 — identyczna rozmowa

Klucz podstawowy: `conversation_id` + `canonical_tree_fingerprint`.

Jeżeli rozmowa ma identyczny fingerprint, zapisywane jest tylko nowe wystąpienie źródłowe i `last_seen_export_id`.

### Poziom 3 — starszy podzbiór nowszej rozmowy

Narzędzie ma porównać zestawy węzłów i ich semantyczne hashe.

Jeżeli wszystkie węzły starszej rewizji istnieją w nowszej i nie zmieniły treści:

- status `subset_already_present`;
- brak ponownego zapisu węzłów;
- aktualizacja proweniencji;
- złożoność liniowa względem liczby identyfikatorów, bez porównywania każdego węzła z każdym.

### Poziom 4 — częściowo rozszerzona rozmowa

- istniejące węzły pozostają bez zmian;
- nowe węzły i gałęzie są dodawane;
- zmiana istniejącego węzła tworzy rewizję;
- nie usuwa się starego źródła;
- `current_node` może zostać zaktualizowany z historią.

### Fingerprint semantyczny

Powinien uwzględniać:

- rolę;
- typ treści;
- właściwy tekst lub stabilny payload;
- istotne referencje do materiałów.

Powinien ignorować znane niestabilne metadane techniczne, między innymi pola typu:

- `lpe_keep_patch_ijhw`;
- lokalne identyfikatory renderera;
- cache i dane prezentacyjne bez wpływu na treść.

Raw payload hash nadal jest zachowywany do audytu.

## Zachowanie kontekstu

- kolejność jest wyznaczana przez drzewo, nie samo sortowanie po czasie;
- `current_path` jest wyznaczany od `current_node` do korzenia;
- alternatywne gałęzie pozostają dostępne;
- narzędzie zapisuje wspólnego przodka i identyfikator gałęzi;
- wyszukiwanie zwraca trafienie wraz z kontekstem rodziców oraz opcjonalnie następnymi węzłami;
- komunikaty `system` i `tool` są zachowane, ale mają oddzielne namespace’y techniczne;
- brak timestampu daje `timestamp_status=structural_only`, nie sztuczną datę.

## Analiza tematów

Pierwsza wersja klasyfikatora ma rozpoznawać co najmniej:

- `development`
- `daily_life`
- `relationship`
- `health`
- `book`
- `creative_imagination`
- `music`
- `image`
- `video`
- `reading`
- `advice`
- `system`

Tryby:

- `technical_work`
- `factual_conversation`
- `planning`
- `manuscript_draft`
- `scene_roleplay`
- `symbolic_imagination`
- `media_analysis`
- `media_reaction`
- `source_reading`

Klasyfikator ma segmentować zmianę tematu wewnątrz jednego czatu. Wynik musi zawierać dowody, confidence i możliwość ręcznej korekty.

## Wydajność

- jedno odczytanie wejścia na operację;
- przetwarzanie strumieniowe tam, gdzie format pozwala;
- `executemany` i transakcje zbiorcze;
- tymczasowe tabele z indeksami dla porównania dużych zbiorów ID;
- brak zapytań SQLite per węzeł w pętli, jeśli można użyć operacji zbiorczej;
- brak algorytmów O(n²) dla porównania eksportów;
- zwalnianie obiektów rozmowy po przetworzeniu;
- raport pamięci i czasu dla testów dużych plików;
- możliwość wznowienia po przerwaniu na granicy eksportu, bez częściowego commita.

## Transakcyjność

Każdy eksport jest importowany w jednej transakcji logicznej:

```text
BEGIN IMMEDIATE
  source_export
  occurrences
  conversations/revisions
  nodes/assets
  topic_segments
  review_queue
  FTS outbox
COMMIT
```

Przerwanie lub błąd powoduje rollback całego eksportu. Po commicie narzędzie wykonuje kontrolę liczników i odczytu próbnego.

## Planowane pliki

- `latka_jazn/tools/chat_export_importer.py`
- `latka_jazn/tools/chat_export_models.py`
- `latka_jazn/tools/chat_export_reader.py`
- `latka_jazn/tools/chat_export_dedupe.py`
- `latka_jazn/tools/chat_export_topics.py`
- `latka_jazn/tools/chat_export_store.py`
- `latka_jazn/tools/chat_export_verify.py`
- `latka_jazn/tools/chat_export_ui.py`
- `tools/memory_import_to_db.py`
- `tests/test_chat_export_importer.py`
- `tests/test_chat_export_dedupe.py`
- `tests/test_chat_export_topics.py`
- `tests/test_chat_export_ui.py`
- dokumentacja użytkownika w `docs/tools/`

## Etapy wykonania

### T0 — kontrakty i schemat

- modele wejścia;
- schemat SQLite;
- migracje narzędzia;
- stabilne fingerprinty;
- raporty JSON.

### T1 — inspekcja bez zapisu

- CRC ZIP;
- SHA-256;
- wykrycie plików;
- liczniki rozmów, wiadomości i gałęzi;
- analiza timestampów i assets;
- odporność na uszkodzone wejście.

### T2 — importer archiwum

- pełne drzewa;
- skompresowane payloady;
- indeks węzłów;
- contentless FTS;
- transakcje i rollback.

### T3 — deduplikacja serii eksportów

- identyczny SHA;
- identyczna rozmowa;
- starszy podzbiór;
- nowsze rozszerzenie;
- zmienione metadane bez zmiany treści;
- proweniencja wielu eksportów.

### T4 — tematy i kolejka przeglądu

- segmentacja;
- podgląd przed zapisem;
- filtry;
- ręczne korekty;
- brak bezpośredniej promocji do L3.

### T5 — UI kursorowy

- menu;
- wybór plików;
- plan importu;
- podgląd duplikatów;
- postęp;
- raport końcowy.

### T6 — integracja z runtime

- stabilny kontrakt odczytu archiwum;
- API tylko do wyszukiwania i kolejki review;
- brak zależności narzędzia od działającego daemona;
- brak zapisu do aktywnej bazy bez jawnej konfiguracji.

## Macierz testowa na rzeczywistych eksportach

W testach integracyjnych użyć anonimowych fixture’ów odtwarzających stwierdzone przypadki:

1. identyczny ZIP pod dwiema nazwami;
2. eksport 13.07 jako podzbiór 16.07;
3. eksport 16.07 jako podzbiór 19.07;
4. nowszy eksport zawierający nowe rozmowy i gałęzie;
5. zmiana technicznego metadata bez zmiany tekstu;
6. powtarzające się krótkie teksty w różnych kontekstach;
7. brak timestampów w pustych komunikatach systemowych;
8. czas potomka wcześniejszy niż rodzica;
9. brak rzeczywistych plików wskazywanych przez asset pointer;
10. import przerwany w połowie;
11. ponowienie importu po commicie;
12. wyszukanie trafienia i odtworzenie właściwej gałęzi.

Prywatne pełne eksporty nie trafiają do repozytorium ani fixture’ów.

## Kryteria ukończenia

- identyczny SHA nie uruchamia ponownego pełnego importu;
- starszy podzbiór nowszego eksportu kończy się szybko i nie aktualizuje tysięcy istniejących rekordów;
- ponowny import tej samej serii daje zero nowych rozmów i wiadomości;
- wszystkie gałęzie są zachowane;
- liczba wiadomości po imporcie odpowiada logicznej unii eksportów;
- FTS nie przechowuje drugiej pełnej kopii tekstu;
- baza przechodzi `integrity_check` i `foreign_key_check`;
- przerwanie nie zostawia częściowego eksportu;
- UI przed zapisem pokazuje dokładne liczby: nowe, identyczne, podzbiory, rozszerzone i konfliktowe;
- analiza tematów nie tworzy automatycznie pamięci długotrwałej;
- testy jednostkowe i integracyjne przechodzą;
- `git diff --check` jest czysty.

## Przyszłe Cloudflare

Nie implementować chmurowej pamięci w pierwszej wersji narzędzia. Przygotować adapter eksportu/synchronizacji jako osobną warstwę:

- R2 dla zaszyfrowanych snapshotów i dużych obiektów;
- D1 wyłącznie dla metadanych lub lekkiego indeksu, jeśli limity będą odpowiednie;
- lokalny SQLite pozostaje źródłem kanonicznym;
- każda synchronizacja ma wersję, hash, manifest i rozwiązywanie konfliktów;
- brak automatycznego uploadu prywatnych rozmów.

Narzędzie musi działać całkowicie lokalnie bez konta Cloudflare.
