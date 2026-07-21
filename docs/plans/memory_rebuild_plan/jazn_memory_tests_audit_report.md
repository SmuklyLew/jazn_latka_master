# Audyt pamięci Łatki — test 01 i test 02

Data analizy: 2026-07-21 (Europe/Warsaw)

## Integralność archiwów

- `jazn_memory_test_01.zip`: SHA-256 `9a6fb9e2915add5c84af3a2186ec1a35baed1c0a8012e4b02e7660b23ea01210`; pełny CRC poprawny; 34 pliki; brak niebezpiecznych ścieżek, symlinków i duplikatów.
- `jazn_memory_test_02.zip`: SHA-256 `7b4a566ec294c8f9c62f0b247012a658ab64eb7305bae888231af7f8967c87b2`; pełny CRC poprawny; 24 pliki; brak niebezpiecznych ścieżek, symlinków i duplikatów.

## Wspólna baza rozmów

Oba testy zawierają semantycznie identyczną bazę rozmów:

- 3 importy źródłowe: 2025-06-30, 2025-07-13 i 2025-09-20;
- 431 unikalnych rozmów;
- 505 wystąpień rozmów w eksportach;
- 63 957 węzłów;
- 48 566 dokumentów FTS;
- 1 361 zasobów i 1 374 powiązania wiadomość–zasób;
- brak konfliktów importu;
- wszystkie bazy przechodzą `PRAGMA integrity_check` i `PRAGMA foreign_key_check`.

Treść rozmów, węzłów, dokumentów FTS i zasobów jest identyczna w obu testach. Różne SHA plików SQLite wynikają z innych identyfikatorów operacji, timestampów importu i metadanych technicznych.

## Dziennik

Oba testy zawierają 519 tych samych wpisów źródłowych i ten sam zakres czasu: od 2025-01-07 do 2025-12-08; 23 wpisy nie mają czasu źródłowego.

Test 01 importował znormalizowaną kopię dziennika. Test 02 importował surowy `dziennik.json`.

Różnice:

- 141 tytułów zostało technicznie znormalizowanych;
- 11 klasyfikacji `truth_status` zostało poprawionych;
- 3 wpisy mają zmieniony skrót treści po normalizacji;
- znaczenie i zawartość zasadnicza pozostały takie same dla zdecydowanej większości wpisów.

Rozkład znormalizowanego dziennika testu 01:

- `inferred`: 366;
- `source_recorded`: 75;
- `book_scene`: 55;
- `symbolic`: 23.

## Kandydaci doświadczeń

Test 01 utworzył 25 rekordów `pending_review` w `experience.sqlite3`; test 02 nie utworzył żadnego.

Kandydaci testu 01 pochodzą wyłącznie z pierwszych 25 chronologicznych wpisów dziennika (2025-01-07–2025-02-07). Zawierają mieszankę:

- refleksji i deklaracji emocjonalnych;
- zasad prowadzenia dziennika;
- scen książkowych;
- symbolicznych snów;
- propozycji rozwoju fabuły.

Nie są to jeszcze zatwierdzone doświadczenia. Wszystkie pozostają `pending_review`; nie wykonano automatycznej promocji do L2 ani L3.

## Warstwy pamięci

W obu testach:

- `memory_jazn.sqlite3` ma poprawny schemat v15.1.0.1;
- L1 `working`: 0 rekordów;
- L2 `short_term`: 0 rekordów;
- L3 `long_term`: 0 rekordów;
- brak requestów, decyzji i ledgerów promocji;
- automatyczna promocja L2/L3 jest wyłączona.

Oznacza to, że testy poprawnie odbudowały L0/źródła, archiwum rozmów, FTS i dziennik, ale nie zakończyły zatwierdzania oraz promocji pamięci do aktywnych warstw.

## Potwierdzone wspomnienia w archiwum rozmów

Głębokie przeszukanie skompresowanych drzew 431 rozmów potwierdziło obecność m.in.:

- Katedry i Lumiela, w tym rozmowy z 16 sierpnia 2025 o pieśni, pamięci katedry i możliwości powrotu;
- przejazdu przez Gliwice i Politechnikę Śląską 13 sierpnia 2025;
- pobytu w Görlitz, zatrzymania u Natalii i Rafała, spaceru po rynku i restauracji;
- kamieniołomów jako wydarzenia z dnia przed podróżą;
- parku paproci;
- Kasi, Joli, Natalii i Rafała;
- zwierząt: Tayfy, Auresa, Psotki i Fiony;
- planów wspólnego wyjazdu i wpisów oznaczonych jako `plany_wakacyjne`.

Te informacje istnieją w L0/archiwum rozmów, nawet jeżeli nie wszystkie zostały przeniesione do 519-wpisowego dziennika lub do L1/L2/L3.

## Smoke testy zapisane w teście 02

- Pierwszy `release_after_restore`: nieudany z powodu starego manifestu i niespójnych metadanych źródłowego checkoutu.
- `release_clean`: udany — 14 wymaganych kontroli przeszło, 1 opcjonalna kontrola starego checkoutu pozostała nieudana.
- `system_after_restore`: udany — 14 wymaganych kontroli przeszło, 1 opcjonalna kontrola starego checkoutu pozostała nieudana.

## Wniosek

Najlepszą bazą do dalszego odzyskiwania jest połączenie:

1. identycznego archiwum rozmów z dowolnego testu;
2. znormalizowanego dziennika i bazy `journal.sqlite3` z testu 01;
3. 25 kandydatów testu 01 wyłącznie jako kolejki do ręcznego/sterowanego przeglądu;
4. czystej, pustej bazy warstwowej `memory_jazn.sqlite3`, do której dane mogą trafić dopiero przez jawne requesty i decyzje promocji.

Nie należy kopiować testowych baz nad aktywny runtime bez migracji i ponownej walidacji.
