# Jaźń Łatka v15.1.0.1 — raport wydania

## Zakres

Wydanie łączy dwa wcześniej rozdzielone zakresy:

1. źródłową warstwę L0 i bezstratny importer eksportów ChatGPT;
2. transakcyjny runtime pamięci L1/L2/L3 z jawną promocją i granicami prawdy.

Kanonicznym źródłem wersji jest wyłącznie `latka_jazn/version.py`. Kanonicznym manifestem integralności jest wyłącznie `PACKAGE_INTEGRITY_MANIFEST.json`.

## Importer L0

- zachowuje całe drzewa rozmów, alternatywne gałęzie i proweniencję;
- deduplikuje identyczne eksporty, rozmowy i starsze podzbiory;
- używa jednej transakcji na eksport;
- przechowuje skompresowany payload oraz contentless FTS5;
- emituje postęp hash, CRC, rozmowy, commit i walidację;
- wykonuje spójny snapshot przez SQLite Backup API;
- nie promuje automatycznie treści do L2 ani L3;
- uruchamia się także spoza katalogu repo przez oficjalny launcher.

Pomiar pełnego eksportu zmniejszył szczytowy RSS z 535 336 KB do 415 184 KB, około 22,4%, przy zachowaniu fingerprintów i payloadu.

## Pamięć L1/L2/L3

- jedna kanoniczna baza SQLite;
- transakcja `BEGIN IMMEDIATE` obejmująca zapis logicznej tury;
- L1 z budżetem rekordów, znaków i rozmiaru wpisu;
- L2 z TTL, reinforcement, wygaśnięciem i review;
- L3 wyłącznie po request, decision i promotion ledger;
- constraint blokujący `automatic_commit_allowed=1`;
- idempotentny outbox;
- checkpoint sesji;
- read-only gateway L0;
- brak starego automatycznego fan-out w zwykłej turze.

Pełna zatwierdzona tura zapisuje najwyżej `1×L1 + 1×L2 + 1×outbox`, bez automatycznego L3.

## Migracje

### Stary SQLite

Skan jest read-only. Epizody, refleksje, semantyka i procedury trafiają do stagingu/review. Sam skan tworzy zero rekordów L1/L2/L3. Dopiero jawne `approve-l2` może utworzyć pojedynczy rekord L2.

### `dziennik.json`

Jeden wpis dziennika jest jednym kandydatem review, nawet jeśli zawiera jednocześnie doświadczenie, emocje, wspomnienie i refleksję. Pełny surowy wpis i `meta` pozostają w proweniencji. `stage-journal` nie tworzy automatycznie L2 ani L3.

Odczytowy test rzeczywistego dziennika z paczki pamięci wykazał 1508 poprawnych wpisów; 898 miało co najmniej dwa pola starego fan-out.

## Diagnostyka SQLite

Status L1/L2/L3 nie tworzy schematu ani metadanych. Dla zamkniętej bazy bez sidecarów używa `mode=ro&immutable=1`. Dla kompletnej pary WAL+SHM używa zwykłego `mode=ro`, aby nie ignorować zatwierdzonych danych WAL. Niepełna para sidecarów jest raportowana jako degraded.

`status` i `doctor` pokazują osobno stary `runtime_write` oraz `tier_v151`.

## Interfejs kursorowy Windows

Automatyczny test uruchamia rzeczywisty proces programu w natywnym Windows ConPTY i sprawdza:

- renderowanie menu w TTY;
- nawigację strzałkami;
- zatwierdzenie Enter;
- czyste zakończenie `Ctrl+X` kodem 130;
- brak tracebacku.

Granica: GitHub-hosted runner nie jest ręczną sesją w aplikacji Microsoft Windows Terminal na pulpicie użytkownika. Test potwierdza natywną warstwę konsolową Windows/ConPTY, nie ocenę wizualną konkretnego emulatora terminala.

## Kryteria końcowe

Stały workflow `v151-release-finalization`:

1. generuje kandydat `PACKAGE_INTEGRITY_MANIFEST.json` z kanonicznego planu Git;
2. porównuje semantykę manifestu bez niestabilnych pól czasu i zapisuje go tylko przy rzeczywistej zmianie wersji, listy plików, rozmiaru lub SHA-256;
3. weryfikuje zapisany manifest;
4. tworzy czysty release staging z aktualną proweniencją;
5. uruchamia `doctor` i wymaga `installation_ok`, `activation_ready`, `release_metadata_current` oraz `release_ready`;
6. uruchamia package smoke profilu systemowego i release;
7. uruchamia targetowaną pełną turę runtime;
8. sprawdza `git diff --check` oraz brak wygenerowanych zmian w checkoutcie.

## Granice prawdy

- L0 nie jest aktywnym wspomnieniem;
- L2 nie jest L3;
- outbox nie dowodzi wykonania efektu;
- roleplay nie jest zdarzeniem fizycznym;
- appraisal nie jest biologicznym odczuciem;
- poprawna baza nie dowodzi aktywnej tożsamości;
- `live_runtime_ready` wymaga osobno działającego daemona, PID, endpointu i świeżego heartbeat.
