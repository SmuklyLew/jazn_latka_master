# Import eksportów ChatGPT do archiwum SQLite

## Granica działania

Narzędzie tworzy warstwę L0: bezstratne archiwum źródłowe i indeks wyszukiwania. Nie zapisuje automatycznie rozmów jako wspomnień, emocji, refleksji ani kanonu książki.

Kanonicznym źródłem struktury rozmowy jest `conversations.json`. `chat.html` służy pomocniczo do odczytu `assetsJson`. Kolejność wynika z relacji `parent` / `children`, nie z samego timestampu.

## Wymagania

- Python 3.12 lub nowszy;
- uruchomienie z katalogu głównego repozytorium;
- wolne miejsce na bazę i snapshot;
- przed importem do istniejącej bazy zalecany snapshot.

## Inspekcja bez zapisu

```powershell
python -X utf8 tools\memory_import_to_db.py inspect D:\Eksporty\chat_export.zip
```

Raport JSON:

```powershell
python -X utf8 tools\memory_import_to_db.py --json inspect D:\Eksporty\chat_export.zip
```

## Plan wobec istniejącej bazy

```powershell
python -X utf8 tools\memory_import_to_db.py plan `
  --database D:\Jaźń\memory\chat_export_archive.sqlite3 `
  D:\Eksporty\chat_export_2025.07.19.zip
```

Plan rozróżnia:

- `new` — nowa rozmowa;
- `identical` — identyczne drzewo;
- `older_subset` — starsza wersja w całości obecna w nowszej;
- `extends_active` — nowsza wersja dodająca węzły lub gałęzie;
- `divergent` — konflikt wymagający przeglądu.

Identyczny SHA-256 całego ZIP-a jest rejestrowany jako alias nazwy/ścieżki, bez ponownego parsowania treści.

## Snapshot przed zapisem

```powershell
python -X utf8 tools\memory_import_snapshot.py `
  D:\Jaźń\memory\chat_export_archive.sqlite3 `
  D:\Jaźń\backups\chat_export_archive-before-import.sqlite3 `
  --full-check
```

Snapshot używa SQLite Backup API. Obejmuje zatwierdzone dane znajdujące się w WAL, powstaje w pliku tymczasowym, przechodzi `integrity_check` i `foreign_key_check`, a dopiero potem zastępuje wskazany plik docelowy.

Nie kopiuj aktywnego pliku `.sqlite3` zwykłym `Copy-Item`, zwłaszcza przy działającym WAL.

## Import wielu eksportów

Najpierw podaj najnowsze i największe pliki. Program dodatkowo sortuje pliki malejąco według rozmiaru, aby starsze podzbiory można było szybko rozpoznać.

```powershell
python -X utf8 tools\memory_import_to_db.py import `
  --database D:\Jaźń\memory\chat_export_archive.sqlite3 `
  D:\Eksporty\chat_export_2025.07.19.zip `
  D:\Eksporty\chat_export_2025.07.16.zip `
  D:\Eksporty\chat_export_2025.07.13.zip
```

Każdy eksport działa w osobnym procesie i w jednej transakcji `BEGIN IMMEDIATE`. Po zakończeniu pliku worker kończy się, dzięki czemu system operacyjny odzyskuje pamięć. Błąd lub Ctrl+C cofa bieżący eksport; wcześniej zakończone eksporty pozostają zatwierdzone.

## Walidacja

Szybka:

```powershell
python -X utf8 tools\memory_import_to_db.py verify `
  --database D:\Jaźń\memory\chat_export_archive.sqlite3 --quick
```

Pełna:

```powershell
python -X utf8 tools\memory_import_to_db.py verify `
  --database D:\Jaźń\memory\chat_export_archive.sqlite3
```

Poprawny wynik wymaga:

- `integrity_check=ok` albo `quick_check=ok`;
- `foreign_key_error_count=0`;
- braku niezamkniętego importu;
- zgodnych liczników rozmów, węzłów i FTS.

## Tematy i kolejka przeglądu

Analiza tematów nie tworzy pamięci długotrwałej.

```powershell
python -X utf8 tools\memory_import_to_db.py topics `
  --database D:\Jaźń\memory\chat_export_archive.sqlite3
```

Kolejka `review` przechowuje kandydatów do późniejszej decyzji użytkownika lub runtime. Wybranie segmentu nie jest promocją do L2 ani L3.

## Interfejs kursorowy

```powershell
python -X utf8 tools\memory_import_ui.py `
  --database D:\Jaźń\memory\chat_export_archive.sqlite3
```

Sterowanie:

- strzałki — nawigacja;
- Spacja — zaznaczenie;
- Enter — wybór;
- Esc — powrót/anulowanie;
- Ctrl+X — zakończenie programu.

Operacje zapisujące wymagają wpisania pełnego tokenu potwierdzenia, np. `IMPORTUJ` lub `DODAJ`. Pusty Enter nie autoryzuje zapisu.

## Ręczny smoke test Windows Terminal

GitHub Actions działa bez prawdziwego interaktywnego TTY. Automatycznie testowana jest logika klawiszy, anulowania i potwierdzeń. Przed wydaniem wykonaj również test w rzeczywistym Windows Terminal:

1. Uruchom `tools\memory_import_ui.py` na kopii testowej bazy.
2. Sprawdź strzałki i Spację.
3. Naciśnij Esc na ekranie wyboru — baza nie może się zmienić.
4. Rozpocznij import i pozostaw potwierdzenie puste — zapis nie może ruszyć.
5. Wpisz błędny token — zapis nie może ruszyć.
6. Wpisz `IMPORTUJ` — import testowego ZIP-a ma się zakończyć.
7. Uruchom `verify`.
8. Podczas importu innej kopii naciśnij Ctrl+X — bieżąca transakcja ma zostać cofnięta.
9. Ponownie uruchom `verify`.

## Odzyskiwanie

- Nie usuwaj źródłowych ZIP-ów po imporcie.
- Zachowuj SHA-256 i wystąpienia źródeł.
- W razie przerwania uruchom `verify`, a następnie ponów ten sam ZIP. Deduplikacja zapobiegnie ponownemu zapisowi już zatwierdzonych danych.
- Przy konflikcie `divergent` nie nadpisuj źródła; sprawdź rewizje i `import_conflicts`.

## Wydajność

- pełny tekst jest przechowywany raz w skompresowanym payloadzie rozmowy;
- FTS5 jest contentless i przechowuje indeks oraz lokalizatory;
- parsing `conversations.json` jest strumieniowy;
- identyczny SHA omija ponowny CRC i parsing po wcześniejszej poprawnej walidacji;
- duże eksporty są izolowane procesowo;
- SQLite używa ograniczonego cache i dyskowego magazynu danych tymczasowych, aby nie zwiększać nadmiernie RAM.
