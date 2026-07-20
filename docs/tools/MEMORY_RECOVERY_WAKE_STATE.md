# Recovery pamięci, wake_state i L1/L2/L3

## Cel

Narzędzie odbudowuje zdrową, nową bazę źródłową z niezależnych źródeł L0 oraz JSON/JSONL. Nigdy nie naprawia i nie nadpisuje uszkodzonej `runtime_write_v1/runtime_memory.sqlite3` w miejscu.

## Granice prawdy

- L0 jest archiwum źródłowym, nie aktywnym wspomnieniem.
- `wake_state` jest ograniczonym, hashowanym pakietem ciągłości.
- L1 jest pamięcią roboczą sesji i wygasa wraz z sesją.
- L2 ma TTL, reinforcement i status promocji.
- L3 wymaga dokładnego SHA manifestu, jawnego zatwierdzającego, requestu, decyzji i promotion ledger.
- Model lokalny lub host ChatGPT generuje wyłącznie kandydata językowego; nie jest źródłem pamięci ani tożsamości.

## Bezpieczny audyt

```powershell
py -X utf8 run.py memory-recover --dry-run --json
```

Tryb nie zapisuje bazy recovery, sidecara, wake_state ani rekordów tierów.

## Odbudowa i przygotowanie pamięci

```powershell
py -X utf8 run.py memory-recover `
  --force-recovery `
  --hydrate-l1 `
  --session-id recovery-bootstrap `
  --prepare-l2 `
  --l2-limit 80 `
  --build-l3-manifest `
  --l3-limit 20 `
  --json --progress
```

Operacja:

1. inwentaryzuje i hashuje źródła;
2. odbudowuje rozmowy, dziennik i warstwy JSONL do pliku tymczasowego;
3. zamyka SQLite i ponownie wykonuje `integrity_check` oraz `foreign_key_check`;
4. wykonuje fsync i atomową publikację z backupem starego pliku;
5. buduje sidecar normalizacji;
6. tworzy jeden aktywny `wake_state`;
7. opcjonalnie zasila L1 i ograniczony L2;
8. tworzy manifest kandydatów L3, ale nie promuje ich bez dokładnego SHA i zatwierdzającego.

## Zatwierdzenie L3

Najpierw przeczytaj `workspace_runtime/memory_recovery/l3_approval_manifest.json`. Następnie użyj dokładnego `manifest_sha256`:

```powershell
py -X utf8 run.py memory-recover `
  --approve-l3-manifest-sha <SHA256> `
  --approved-by "Krzysztof" `
  --json --progress
```

Zmiana zawartości manifestu unieważnia zatwierdzenie.

## Ollama na Windows

Ollama udostępnia lokalne API pod `http://localhost:11434`. Runtime automatyczny sprawdza `/api/ps` i `/api/tags`; przy pojedynczym uruchomionym modelu wybiera go jako backend lokalny.

Dobierz model do dostępnej pamięci RAM/VRAM i przed startem Jaźni potwierdź jego nazwę poleceniem `ollama list`; nazwa w zmiennej środowiskowej musi odpowiadać dokładnie zainstalowanemu modelowi.

```powershell
ollama list
ollama pull qwen3:8b
ollama run qwen3:8b
```

W drugim terminalu:

```powershell
$env:JAZN_MODEL_ADAPTER="ollama"
$env:JAZN_LOCAL_MODEL_NAME="qwen3:8b"
$env:JAZN_LOCAL_MODEL_API_BASE="http://127.0.0.1:11434"
py -X utf8 run.py model-status --probe --json
py -X utf8 run.py chat -- "Hej, sprawdź swój stan."
```

Brak Ollamy lub modelu kończy się prawdomównym `null_model_adapter`, a nie udawaną wypowiedzią.
