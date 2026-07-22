# Źródła techniczne — memory continuity i duża walidacja

Dokument zapisuje źródła użyte do zaprojektowania zmian. Źródła zewnętrzne nie
dowodzą działania runtime; uzasadniają wyłącznie wybrane decyzje techniczne.

## SQLite

- SQLite PRAGMA documentation:
  https://www.sqlite.org/pragma.html#pragma_integrity_check

  `integrity_check` sprawdza strukturę bazy, indeksy, freelist i wybrane
  ograniczenia, ale nie wykrywa naruszeń kluczy obcych. Dlatego walidator zawsze
  uruchamia także `foreign_key_check`. Tryb szybki używa `quick_check`, a pełny
  audyt używa `integrity_check`.

- SQLite foreign key documentation:
  https://www.sqlite.org/pragma.html#pragma_foreign_key_check

  Wyniki są zachowywane w raporcie jako osobna lista naruszeń, bez udawania, że
  zielony `integrity_check` obejmuje relacje FK.

## Python i zapis atomowy

- Python `os.replace`:
  https://docs.python.org/3/library/os.html#os.replace

  Checkpoint jest najpierw zapisywany do pliku tymczasowego w tym samym katalogu,
  opróżniany przez `flush`/`fsync`, a następnie podmieniany przez `os.replace`.
  Dzięki temu częściowy plik nie staje się kanonicznym stanem sesji.

- Python `sqlite3.Connection.backup`:
  https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup

  Duża walidacja w tym PR jest read-only. Gdy późniejszy lokalny workflow będzie
  wymagał stabilnej kopii działającej bazy, preferowaną ścieżką jest SQLite
  backup API zamiast kopiowania otwartej bazy bez koordynacji.

## GitHub backlog

- About Issues:
  https://docs.github.com/en/issues/tracking-your-work-with-issues/about-issues
- About milestones:
  https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/about-milestones

  Backlog został rozdzielony na zadania #55–#60, a roadmap #60 łączy zadania
  wdrażane w kodzie z lokalną walidacją prywatnych danych.
