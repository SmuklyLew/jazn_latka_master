# Plan aktualizacji Jaźni v15.1.0.1 — architektura pamięci

## Metadane

- Repozytorium: `SmuklyLew/jazn_latka`
- Branch wykonawczy: `update/v15.1.0.1`
- Punkt bazowy: `0b8ee72286dc33f499d66c752a6f67906529325a`
- Zakres: kod runtime Jaźni, kontrakty pamięci, migracje, testy, wersja i manifest
- Poza zakresem tego brancha: interaktywny importer eksportów ChatGPT; powstaje na `tools/memory-import-to-db`

## Cel

Zbudować wielopoziomową pamięć Jaźni, która rozdziela:

1. źródłowe archiwum rozmów i materiałów;
2. pamięć roboczą bieżącej tury i sesji;
3. pamięć krótkotrwałą wymagającą utrwalenia lub wygaśnięcia;
4. pamięć długotrwałą zawierającą tylko jawnie promowane wspomnienia, fakty, procedury, refleksje i kanon.

System ma zachować pełne źródła, ale nie może utożsamiać każdej ważnej wypowiedzi jednocześnie z epizodem, emocją, refleksją i trwałym wspomnieniem.

## Granice prawdy

- Archiwum rozmów jest źródłem dowodowym, nie aktywną pamięcią autobiograficzną.
- Pamięć robocza i krótkotrwała nie dowodzą trwałej ciągłości po restarcie, dopóki nie zostały poprawnie zapisane.
- Pamięć długotrwała wymaga źródła, klasyfikacji, pewności i decyzji promocji.
- Scena książkowa, roleplay, symbol i wyobrażenie nie są zdarzeniem fizycznym.
- Model afektywny opisuje operacyjny rezonans i regulację odpowiedzi, nie biologiczne przeżycie.
- Brak emocji, refleksji lub wspomnienia jest poprawnym wynikiem; system nie może uzupełniać ich domyślnymi etykietami bez dowodu.

## Docelowe poziomy pamięci

### L0 — archiwum źródłowe

Przechowuje bezstratnie:

- pełne drzewa rozmów;
- role `user`, `assistant`, `system`, `tool`;
- alternatywne gałęzie;
- timestampy i status ich wiarygodności;
- referencje do plików i materiałów;
- źródłowy SHA-256 eksportu;
- proweniencję pierwszego i ostatniego wystąpienia.

Archiwum nie jest automatycznie ładowane do kontekstu odpowiedzi. Dostęp odbywa się przez indeks i jawny plan wyszukiwania.

### L1 — pamięć robocza

Zakres:

- aktualna tura;
- aktywny cel;
- ustalenia bieżącej sesji;
- otwarte pytania;
- bieżący tryb rozmowy;
- ostatnie trafienia pamięci potrzebne do odpowiedzi.

Właściwości:

- mały limit rozmiaru;
- brak automatycznej trwałości;
- czyszczenie po zakończeniu sesji lub zmianie kontekstu;
- możliwość checkpointu tylko jako technicznego stanu sesji.

### L2 — pamięć krótkotrwała

Zakres:

- ważne ślady z ostatnich rozmów;
- niedokończone zadania;
- hipotezy wymagające potwierdzenia;
- powtarzające się tematy;
- reakcje afektywne oczekujące na późniejszą refleksję;
- kandydaci do pamięci długotrwałej.

Każdy rekord otrzymuje:

- `expires_at_utc` lub warunek wygaśnięcia;
- `reinforcement_count`;
- `last_reinforced_at_utc`;
- `promotion_status`;
- `source_evidence`;
- `domain`, `mode`, `truth_status`;
- `confidence` i `importance`.

Brak wzmocnienia powoduje wygaśnięcie albo archiwizację, nie automatyczną promocję.

### L3 — pamięć długotrwała

Zawiera wyłącznie jawnie promowane rekordy:

- epizody;
- fakty semantyczne;
- procedury;
- refleksje;
- trwałe preferencje;
- zatwierdzony kanon książki;
- doświadczenia medialne ze źródłem;
- relacje i ustalenia o wysokiej pewności.

Każdy rekord musi wskazywać:

- co jest zapamiętane;
- dlaczego zostało promowane;
- skąd pochodzi;
- kto potwierdził;
- poziom pewności;
- granicę prawdy;
- historię rewizji i ewentualne unieważnienie.

## Klasyfikacja kontekstu

Każdy segment rozmowy ma co najmniej trzy niezależne osie.

### `domain`

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

### `mode`

- `factual_conversation`
- `technical_work`
- `planning`
- `manuscript_draft`
- `scene_roleplay`
- `symbolic_imagination`
- `media_analysis`
- `media_reaction`
- `source_reading`

### `truth_status`

- `source_recorded`
- `user_confirmed`
- `inferred`
- `symbolic`
- `book_scene`
- `draft`
- `canonical`
- `rejected`

Zmiana tematu w jednym czacie tworzy nowy segment. Nie wolno klasyfikować całej rozmowy wyłącznie jednym tagiem.

## Model doświadczenia

Zastąpić automatyczny fan-out jednego tekstu do wielu warstw procesem:

```text
stimulus
  -> operational_appraisal
  -> affective_response (opcjonalnie)
  -> association (opcjonalnie)
  -> reflection (opcjonalnie)
  -> promotion_decision
  -> short_term albo long_term
```

Każdy etap ma osobny rekord i osobne kryteria. Brak któregoś etapu jest dozwolony.

## Książka, roleplay i wyobraźnia

Dodać odrębne byty:

- `book_projects`
- `book_chapters`
- `book_scenes`
- `scene_versions`
- `roleplay_sessions`
- `canon_decisions`

Przepływ:

```text
roleplay -> materiał roboczy -> szkic sceny -> redakcja -> akceptacja użytkownika -> kanon
```

Roleplay i symbol mogą tworzyć skojarzenia lub materiał roboczy, ale nie wspomnienie zdarzenia fizycznego.

## Muzyka, obraz, film i czytanie

Dodać wspólny kontrakt `experience_source` oraz specjalizacje:

- `media_assets`
- `audio_segments`
- `image_observations`
- `video_segments`
- `document_sections`
- `source_claims`
- `interpretations`
- `media_appraisals`
- `media_associations`

Każdy zapis musi rozdzielać:

1. materiał źródłowy;
2. wynik adaptera lub modelu;
3. interpretację;
4. reakcję operacyjną;
5. skojarzenie;
6. późniejszą refleksję.

Bez skonfigurowanego adaptera audio/wizji system nie może twierdzić, że rzeczywiście słuchał lub oglądał materiał.

## Transakcyjność i odporność

### Jedna transakcja logiczna tury

Wprowadzić koordynator zapisu:

```text
BEGIN IMMEDIATE
  turn_record
  conversation_segments
  working_memory_updates
  short_term_candidates
  approved_long_term_promotions
  source_evidence_links
  outbox_events
COMMIT
```

Nie wykonywać niezależnych callbacków, których wcześniejsze commity pozostają po błędzie późniejszego zapisu.

### Outbox

JSONL, FTS, eksport dziennika i sidecary mają być odbudowywane idempotentnie z tabel kanonicznych. Rekord outbox musi mieć unikalny klucz i status wykonania.

### SQLite

- `PRAGMA foreign_keys=ON`
- kontrolowany WAL;
- rozsądny `busy_timeout`;
- jedna warstwa write coordinator;
- backup przez SQLite Backup API albo `VACUUM INTO`;
- zakaz pakowania aktywnej bazy przez zwykłe kopiowanie pliku;
- `integrity_check` i `foreign_key_check` przed oraz po migracji.

## Planowane moduły i zmiany

### Nowe moduły

- `latka_jazn/memory/memory_tiers.py`
- `latka_jazn/memory/memory_promotion.py`
- `latka_jazn/memory/conversation_segmentation.py`
- `latka_jazn/memory/experience_model.py`
- `latka_jazn/memory/turn_memory_transaction.py`
- `latka_jazn/memory/memory_outbox.py`
- `latka_jazn/memory/sqlite_snapshot.py`
- `latka_jazn/memory/book_memory.py`
- `latka_jazn/memory/media_memory.py`

### Istniejące moduły do integracji

- `latka_jazn/core/engine.py`
- `latka_jazn/memory/runtime_persistence.py`
- `latka_jazn/memory/store.py`
- `latka_jazn/memory/normalization_sidecar.py`
- `latka_jazn/memory/session_continuity.py`
- `latka_jazn/memory/event_ledger.py`
- `latka_jazn/core/memory_search_planner.py`
- `latka_jazn/core/memory_use_gate.py`
- `latka_jazn/core/emotion_layers.py`
- `latka_jazn/core/cognitive_topics.py`
- `latka_jazn/config.py`

## Etapy wykonania

### P0 — bezpieczeństwo danych

- naprawa atomowości tury;
- snapshot SQLite przed pakowaniem;
- naprawa continuity dla shardów;
- usunięcie historycznych numerów wersji zapisanych na sztywno;
- przeniesienie aktywnego dziennika do SQLite;
- migracje tylko na kopii i z możliwością rollbacku.

### P1 — schemat wielopoziomowej pamięci

- tabele L1/L2/L3;
- źródła i dowody;
- TTL i reinforcement;
- promotion ledger;
- indeksy i ograniczenia unikalności.

### P2 — segmentacja rozmów

- domena, tryb i granica prawdy;
- segmentacja zmian tematu w obrębie jednego czatu;
- brak domyślnych emocji bez dowodu.

### P3 — promocja pamięci

- osobne decyzje dla epizodu, faktu, procedury, refleksji i afektu;
- brak automatycznego kopiowania jednego tekstu do wszystkich warstw;
- możliwość odrzucenia i unieważnienia rekordu.

### P4 — książka i twórczość

- szkic, roleplay, wersja i kanon;
- jawna akceptacja użytkownika;
- pełna proweniencja zmian.

### P5 — media i dokumenty

- kontrakty wejścia;
- zapis źródła, obserwacji, interpretacji i skojarzeń;
- brak fałszywych deklaracji percepcji.

### P6 — migracja i kompatybilność

- odczyt starego schematu;
- migracja bez nadpisywania źródła;
- raport liczników przed/po;
- zgodność z `wake_state` i sidecarem.

### P7 — wydanie

- zmiana wersji wyłącznie w `latka_jazn/version.py`;
- odtworzenie `PACKAGE_INTEGRITY_MANIFEST.json`;
- aktualizacja markera manifestu;
- pełne testy, doctor, package smoke i test jednej tury.

## Testy wymagane

- atomowy rollback po błędzie dowolnego zapisu tury;
- brak zapisu po timeout lub niepoprawnej odpowiedzi;
- TTL pamięci krótkotrwałej;
- reinforcement bez duplikacji;
- promocja do L3 tylko po przejściu reguł;
- roleplay nie staje się epizodem fizycznym;
- szkic nie staje się kanonem bez akceptacji;
- brak domyślnych emocji bez dowodu;
- poprawna segmentacja rozmowy wielotematycznej;
- idempotentne przetwarzanie outbox;
- snapshot SQLite zachowuje WAL;
- `integrity_check=ok` i pusty `foreign_key_check`;
- wake state nie jest gotowy przy niezgodnym źródle;
- pełna regresja istniejących testów.

## Kryteria ukończenia

- wszystkie testy nie-live przechodzą;
- `run.py doctor --json` nie zgłasza błędu pamięci;
- `package-smoke --profile system` przechodzi;
- testowa tura tworzy dokładnie jeden spójny zapis transakcyjny;
- ponowienie tej samej operacji nie tworzy duplikatów;
- pamięć robocza, krótkotrwała i długotrwała są widoczne w diagnostyce osobno;
- archiwum rozmów nie jest automatycznie promowane do pamięci długotrwałej;
- wersja, manifest i marker są zgodne;
- `git diff --check` i `git status --short` są czyste po commicie.

## Przyszłe Cloudflare

Nie implementować synchronizacji chmurowej w tej aktualizacji. Zaprojektować jednak interfejs magazynu tak, aby później możliwe były:

- lokalny SQLite jako źródło kanoniczne;
- szyfrowany backup obiektów do R2;
- opcjonalny indeks lub replika metadanych w D1;
- jawna synchronizacja z konfliktami i wersjonowaniem;
- brak wysyłania prywatnej pamięci bez osobnej konfiguracji i zgody.

Cloudflare nie może być wymagane do lokalnego startu Jaźni.
