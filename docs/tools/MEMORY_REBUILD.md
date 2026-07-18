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

## Zalecana kolejność odbudowy

Najpierw należy wgrać **wszystkie dostępne eksporty rozmów** do `archive_chats.sqlite3`, a dopiero potem wykonywać analizę tematów i budować kandydatów doświadczeń. Dzięki temu segmentacja i deduplikacja widzą pełniejszy kontekst, najnowsze rewizje oraz starsze podzbiory rozmów.

Zalecana kolejność:

1. `init` w nowym katalogu testowym.
2. `inspect`, `plan-chats` i `import-chats` dla wszystkich eksportów.
3. Ponowny import wybranych ZIP-ów w celu potwierdzenia idempotencji.
4. Pełne `verify` archiwum rozmów.
5. Import dziennika i jego weryfikacja.
6. `analyse-topics` dopiero po zakończeniu importu rozmów.
7. `build-experience-candidates` dopiero po skompletowaniu L0.
8. Ręczny przegląd i ewentualne zatwierdzanie pojedynczych kandydatów.

Dziennik może zostać zaimportowany wcześniej, ale tworzenie pochodnych kandydatów warto odłożyć do czasu skompletowania rozmów.

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

`chat.html` jest używany jako źródło pomocnicze do `assetsJson`. Sam HTML bez `conversations.json` nie jest importowany jako bezstratne archiwum, ponieważ nie zachowuje pełnego drzewa i alternatywnych gałęzi. Najbezpieczniejszym źródłem pozostaje cały oficjalny ZIP eksportu zawierający oba pliki.

## Plan i import rozmów

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master plan-chats `
  D:\Eksporty\chat-export-small.zip --details

python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master import-chats `
  D:\Eksporty\chat-export-small.zip
```

Można podać wiele eksportów. Narzędzie zachowuje istniejącą deduplikację SHA-256, rozmów, węzłów, starszych podzbiorów i rewizji. Import większego/nowszego eksportu jako pierwszego zwykle ogranicza liczbę późniejszych zmian aktywnej wersji rozmów, ale poprawność nie zależy od kolejności.

## Import żywego dziennika

Obsługiwane są:

- obiekt JSON z `meta` i `entries`;
- lista wpisów JSON;
- JSONL/NDJSON;
- znaczniki czasu `event_time_start`, `timestamp`, `datetime` i starsze pole `data`.

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master import-journal `
  D:\Eksporty\dziennik.json
```

Jeden wpis pozostaje jednym wpisem. Stare pola fan-out są oznaczane do kontroli, lecz nie tworzą automatycznie osobnych wspomnień, emocji i refleksji. Typy sceniczne, fabularne, sny, prompty i wpisy systemowo-meta zachowują oddzielną granicę prawdy.

## Kandydaci doświadczeń

```powershell
python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master `
  build-experience-candidates --from all

python -X utf8 tools\memory_rebuild.py --root D:\.AI\jazn_latka_master `
  review-experiences --limit 50
```

Filtr odrzuca między innymi:

- krótkie potwierdzenia, tracebacki i techniczny szum;
- `book_scene`, `symbolic` i `draft`;
- sceny, fabułę, sny, prompty oraz wpisy systemowe/meta;
- wpisy bez czasu źródłowego;
- analizy mediów bez jawnego charakteru przeżycia lub reakcji;
- nieufne `inferred`, które nie mają jawnego typu refleksyjnego lub doświadczeniowego;
- segmenty rozmów w trybach technicznych, systemowych, redakcyjnych i roleplay.

Raport podaje osobne liczniki przyczyn odrzucenia. Kandydat nadal nie jest doświadczeniem. Kandydaci `pending_review` nie uczestniczą w recall `experience.sqlite3`; wyszukiwanie tej warstwy zwraca dopiero ręcznie zatwierdzone doświadczenia.

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
2. zatwierdzone rekordy z `experience.sqlite3`;
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
7. Zaimportuj pozostałe eksporty rozmów i uruchom pełne `verify`.
8. Zaimportuj dziennik.
9. Uruchom `analyse-topics`.
10. Utwórz małą próbkę kandydatów doświadczeń.
11. Sprawdź próbkę ręcznie.
12. Uruchom pełne `verify`.
13. Dopiero później buduj pełną kolejkę kandydatów i zatwierdzaj pojedyncze rekordy.
