# Import pamięci z HTML do SQLite

## Cel i granica prawdy

Polecenie `memory-import-html` importuje eksporty rozmów `.html` do nowej lub istniejącej,
zweryfikowanej bazy:

```text
memory/sqlite/recovery_current/runtime_memory_recovered.sqlite3
```

Po zapisie uruchamia normalizację sidecara, buduje `wake_state` i inicjalizuje lub
waliduje kanoniczną bazę transakcyjną `runtime_write_v2`. Rozmowy zachowują SHA-256
źródła, lokalizator rozmowy i wiadomości oraz wpisy provenance. Sama inicjalizacja
`runtime_write_v2` nie zapisuje rekordów L3.

Import nie uznaje automatycznie treści rozmów za fakty semantyczne, emocje, kanon
książki ani pamięć L3. Opcjonalne L2 korzysta z istniejącej polityki selekcji.
L3 może powstać tylko jako manifest kandydatów; jego zastosowanie nadal wymaga
jawnego SHA-256 i osoby zatwierdzającej.

## Obsługiwane warianty HTML

1. Eksport zawierający `var jsonData = [...]` — parsowany strumieniowo, po jednej
   rozmowie, bez ładowania całego dużego pliku do pamięci.
2. Renderowany HTML zawierający bloki `conversation` / `message` — obsługiwany jako
   źródło zdegradowane, bez oryginalnych timestampów i identyfikatorów eksportu.

Pełny tekst wiadomości jest zapisywany w źródłowej SQLite. Skracanie do excerptu
następuje dopiero w sidecarze normalizacji.

## Inspekcja bez zapisu

```powershell
py -X utf8 .\run.py memory-import-html `
  --root . `
  --dry-run `
  --json `
  D:\Eksporty\chat.html
```

Dry-run oblicza SHA-256, wykrywa format, parsuje rozmowy i liczy wiadomości. Nie tworzy
bazy, sidecara ani backupu.

## Import i wake_state

Przed zapisem zatrzymaj daemon, aby Windows nie blokował pliku SQLite:

```powershell
py -X utf8 .\run.py stop --root .

py -X utf8 .\run.py memory-import-html `
  --root . `
  --json `
  D:\Eksporty\chat_0.html `
  D:\Eksporty\chat_2.html
```

Jeżeli docelowa baza już istnieje, importer:

1. wykonuje checkpoint WAL;
2. tworzy backup przez SQLite Backup API;
3. pracuje na osobnej kopii roboczej;
4. wykonuje `integrity_check` i `foreign_key_check`;
5. dopiero wtedy atomowo podmienia bazę docelową;
6. normalizuje źródła i buduje zweryfikowany `wake_state`;
7. tworzy albo waliduje pustą/istniejącą bazę L1/L2/L3 `runtime_write_v2`;
8. tworzy osobny backup `runtime_write_v2`, jeżeli baza istniała przed importem.

Domyślne backupy:

```text
memory/backups/html_import/
```

## Import testowy z limitem

```powershell
py -X utf8 .\run.py memory-import-html `
  --root . `
  --limit-conversations 20 `
  --normalize-limit 200 `
  --json `
  D:\Eksporty\chat.html
```

Limity są przeznaczone do smoke testów. Nie stosuj ich do finalnego importu pełnej
pamięci.

## L2 i manifest L3

```powershell
py -X utf8 .\run.py memory-import-html `
  --root . `
  --prepare-l2 `
  --l2-limit 120 `
  --build-l3-manifest `
  --l3-limit 25 `
  --json `
  D:\Eksporty\chat.html
```

Samo `--build-l3-manifest` niczego nie promuje do L3.

## Walidacja po imporcie

```powershell
py -X utf8 .\run.py memory-validate `
  --root . `
  --full `
  --include-all-sqlite `
  --table-counts `
  --json

py -X utf8 .\run.py memory-status --root . --deep-verify --json
py -X utf8 .\run.py doctor --root . --json
```

Poprawny wynik wymaga:

- `integrity_check=ok`;
- `foreign_key_error_count=0`;
- `normalization.status=ok`;
- `wake_state.status=ready`;
- `memory_tiers.ready=true`;
- dokładnie jednego aktywnego snapshotu;
- SHA snapshotu zgodnego z jego JSON-em.

## Ponowny import

Źródło o identycznym SHA-256 jest pomijane jako `already_imported`. Do kontrolowanego
ponownego przetworzenia służy:

```powershell
--force-reimport
```

Nie usuwa ono wcześniejszego źródła ani provenance. Aktualizuje rekordy i dopisuje
kolejne powiązanie źródłowe bez automatycznej promocji pamięci.
