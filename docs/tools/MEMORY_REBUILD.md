# Odbudowa pamięci Jaźni — importer pięciu baz

Narzędzie `tools/memory_rebuild.py` buduje od zera pięć baz o stałych nazwach:

```text
memory/sqlite/
├── archive_chats.sqlite3
├── journal.sqlite3
├── memory_jazn.sqlite3
├── experience.sqlite3
└── import_catalog.sqlite3
```

## Granica działania

- `archive_chats.sqlite3` zachowuje pełne eksporty ChatGPT, drzewa rozmów, gałęzie i FTS.
- `journal.sqlite3` jest żywym dziennikiem z wersjami wpisów i źródłami.
- `experience.sqlite3` przechowuje kandydatów oraz ręcznie zatwierdzone doświadczenia.
- `memory_jazn.sqlite3` jest kanoniczną bazą L1/L2/L3 istniejącego runtime.
- `import_catalog.sqlite3` rejestruje źródła, operacje, walidacje i relacje między bazami.

Importer nie promuje automatycznie treści do L2 ani L3. Sam import rozmowy, wpisu dziennika lub utworzenie kandydata doświadczenia nie dowodzi aktywnego wspomnienia.

## Pierwsze uruchomienie

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master init
```

Komenda jest idempotentna. Nie usuwa istniejących danych.

## Inspekcja źródeł

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master --json inspect `
  D:\Eksporty\chat-export.zip `
  D:\Eksporty\dziennik.json
```

`chat.html` jest używany jako źródło pomocnicze do `assetsJson`. Sam HTML bez `conversations.json` nie jest importowany jako bezstratne archiwum, ponieważ nie zachowuje pełnego drzewa i alternatywnych gałęzi.

## Plan i import rozmów

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master plan-chats `
  D:\Eksporty\chat-export-small.zip --details

python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master import-chats `
  D:\Eksporty\chat-export-small.zip
```

Można podać wiele eksportów. Narzędzie zachowuje istniejącą deduplikację SHA-256, rozmów, węzłów, starszych podzbiorów i rewizji.

## Import żywego dziennika

Obsługiwane są:

- obiekt JSON z `meta` i `entries`;
- lista wpisów JSON;
- JSONL/NDJSON.

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master import-journal `
  D:\Eksporty\dziennik.json
```

Jeden wpis pozostaje jednym wpisem. Stare pola fan-out są oznaczane do kontroli, lecz nie tworzą automatycznie osobnych wspomnień, emocji i refleksji.

## Kandydaci doświadczeń

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master `
  build-experience-candidates --from all

python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master `
  review-experiences --limit 50
```

Filtr odrzuca między innymi krótkie potwierdzenia, powitania, tracebacki i techniczny szum. Kandydat nie jest jeszcze doświadczeniem.

Zatwierdzenie wymaga dwukrotnego podania identyfikatora:

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master `
  approve-experience `
  --candidate-id ID `
  --confirm-candidate-id ID `
  --approved-by Krzysztof `
  --reason "sprawdzone ze źródłem"
```

Zatwierdzenie doświadczenia nie tworzy L2 ani L3.

## Weryfikacja

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master verify
```

Poprawny wynik wymaga dla wszystkich baz:

- `integrity_check=ok`;
- pustego `foreign_key_check`;
- braku błędów schematu.

Tryb szybki:

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master verify --quick
```

## Status i wyszukiwanie

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master status
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master search "jezioro"
```

Kolejność wyszukiwania:

1. `memory_jazn.sqlite3`;
2. `experience.sqlite3`;
3. `journal.sqlite3`;
4. `archive_chats.sqlite3`.

`import_catalog.sqlite3` nie uczestniczy w recall autobiograficznym.

## Bezpieczna kolejność testów

1. Utwórz nowy pusty katalog testowy.
2. Uruchom `init`.
3. Wykonaj `inspect` na jednym małym eksporcie.
4. Wykonaj `plan-chats`.
5. Uruchom `import-chats`.
6. Zaimportuj ten sam ZIP ponownie i sprawdź brak duplikatów.
7. Zaimportuj mały dziennik.
8. Utwórz kandydatów doświadczeń.
9. Sprawdź próbkę ręcznie.
10. Uruchom pełne `verify`.
11. Dopiero później przejdź do większych eksportów.
