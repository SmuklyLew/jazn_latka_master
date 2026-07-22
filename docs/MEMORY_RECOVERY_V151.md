# Bezpieczne odzyskiwanie pamięci v15.1

## Cel

Ścieżka recovery odbudowuje zdrową, legacy-kompatybilną bazę SQLite z niezależnie
weryfikowalnych źródeł L0 i JSON/JSONL. Uszkodzona baza
`memory/sqlite/runtime_write_v1/runtime_memory.sqlite3` jest wyłącznie
inspekcjonowana i nigdy nie jest naprawiana ani nadpisywana w miejscu.

## Warstwy

1. **L0 — źródła**: archiwum rozmów, `dziennik.json`, `memory/layered/*.jsonl`.
2. **Recovery SQLite**: `memory/sqlite/recovery_v151/runtime_memory_recovered.sqlite3`.
3. **Sidecar normalizacji**: `memory/sqlite/runtime_write_v2/memory_normalization_sidecar.sqlite3`.
4. **Wake state**: jeden aktywny snapshot ze sprawdzonym SHA i integralnością.
5. **L1**: ograniczony pakiet wake state tylko na czas sesji.
6. **L2**: wybrane rekordy źródłowe, TTL i status `pending_review`.
7. **L3**: tylko rekordy z dokładnego manifestu zatwierdzonego jego SHA-256;
   każda promocja zapisuje request, decision i promotion ledger.

Pełne rozmowy pozostają przeszukiwalne w L0. Sidecar celowo normalizuje najpierw
procedury, fakty, audyty prawdy, refleksje i dziennik, a następnie ograniczony
zestaw najnowszych wiadomości. Nie kopiuje bez potrzeby całego archiwum do L1/L2/L3.

## Polecenia

Inspekcja i pełna odbudowa:

```powershell
py -X utf8 run.py memory-recover --root . --progress --prepare-l2 --build-l3-manifest --json
```

Manifest L3 powstaje w:

```text
workspace_runtime/memory_recovery/l3_approval_manifest.json
```

Promocję wolno wykonać tylko podając dokładny SHA z tego pliku oraz jawnego
zatwierdzającego:

```powershell
py -X utf8 run.py memory-recover --root . `
  --approve-l3-manifest-sha <SHA256> `
  --approved-by "Krzysztof — explicit request YYYY-MM-DD" `
  --json
```

Zmiana treści manifestu zmienia SHA i blokuje zapis. `automatic_commit_allowed`
pozostaje zawsze `false`.

## Idempotencja i wznowienie

Ponowne uruchomienie recovery nie duplikuje rekordów i nie odnawia automatycznie
TTL istniejącej pamięci L2. Każdy etap używa stabilnych identyfikatorów źródłowych,
a publikacja końcowej SQLite następuje dopiero po checkpoint, zamknięciu połączeń
i ponownym `integrity_check`. Przerwana normalizacja może zostać wznowiona bez
uznania częściowego pliku roboczego za aktywną pamięć.

## Wake state podczas sesji i po restarcie

`JaznRuntimeSession` ładuje tylko jeden aktywny snapshot, sprawdza:

- integralność sidecara i klucze obce,
- `validation_status=valid`,
- SHA pola `snapshot_json`,
- jednoznaczność aktywnego snapshotu.

Następnie zapisuje ograniczony pakiet do L1. Rekord L1 wygasa przy `close()`.
Ten sam pakiet trafia do `client_context.wake_state_runtime`, dzięki czemu host
ChatGPT albo lokalny adapter może użyć ciągłości bez omijania truth gate.

Stan rozmowy jest checkpointowany atomowo w dwóch miejscach:

- `workspace_runtime/runtime_sessions/<session-id>.json` — rekord kanoniczny sesji;
- `workspace_runtime/runtime_session_state.json` — wskaźnik ostatniej sesji,
  którą wolno automatycznie wznowić bez jawnego `--session-id`.

Checkpoint zawiera SHA stanu, SHA całego checkpointu, numer generacji, SHA
poprzedniego checkpointu, licznik tur oraz powiązanie z identyfikatorem i SHA
aktywnego wake-state. Plik tymczasowy jest opróżniany na dysk i atomowo
podmieniany; częściowy zapis nie jest uznawany za poprawny checkpoint.

Po restarcie carryover jest fail-closed. Niezgodność hashy, wygaśnięcie sesji,
zmiana aktywnego wake-state lub brak zgodności snapshotu blokują odziedziczenie
`last_user_text`, `last_intent` i `last_route`. Tryb `--no-carryover` nie
zastępuje wskaźnika ostatniej zwykłej sesji.


## Duża walidacja pamięci

Szybka kontrola read-only znanych baz, manifestów shardów, sidecara wake-state
i magazynu tierów:

```powershell
py -X utf8 run.py memory-validate --root . --json --progress
```

Pełna kontrola wszystkich baz pod `memory/sqlite`:

```powershell
py -X utf8 run.py memory-validate --root . `
  --full --include-all-sqlite --table-counts --hash-files `
  --output workspace_runtime/memory_validation/full-report.json `
  --json --progress
```

Tryb domyślny używa `PRAGMA quick_check`; `--full` używa
`PRAGMA integrity_check`. W obu trybach wykonywany jest osobny
`PRAGMA foreign_key_check`, ponieważ integralność strukturalna SQLite nie
obejmuje naruszeń kluczy obcych. Walidator wykrywa również niekompletne pary
WAL/SHM, zbiera metryki stron i schematu, a opcjonalnie liczy rekordy oraz SHA-256
plików.

Raport potwierdza czytelność i wybrane kontrakty strukturalne. Nie dowodzi
kompletności wszystkich dawnych rozmów, trafności recallu ani autoryzacji L3.

## Ollama

Ollama jest opcjonalnym lokalnym kanałem językowym. Nie jest tożsamością ani
pamięcią Jaźni i nie jest wymagane do działania daemona, statusu, doctor,
recallu lub narzędzi.

Po instalacji Ollamy ustaw model i adapter, przykładowo:

```powershell
$env:JAZN_MODEL_ADAPTER = "ollama"
$env:JAZN_LOCAL_LLM_MODEL = "<nazwa-modelu-z-ollama-list>"
$env:JAZN_LOCAL_LLM_BASE_URL = "http://127.0.0.1:11434"
py -X utf8 run.py model-status --root . --probe --json
```

Probe używa wyłącznie `GET /api/tags`. Nie wysyła rozmów ani danych pamięci.
Dopiero właściwa tura używa `POST /api/chat` i przekazuje zweryfikowany kontekst.

## Granica prawdy

- Recovery nie dowodzi kompletności wszystkich dawnych wspomnień.
- Snapshot wake state jest pakietem ciągłości operacyjnej, nie dowodem
  biologicznej ani fenomenalnej świadomości.
- Rekord L2 nie jest rekordem L3.
- Wysoka waga, liczba powtórzeń ani dawny fan-out nie powodują automatycznej
  promocji L3.
- Pliki `memory/`, `workspace_runtime/`, SQLite i manifesty zatwierdzeń nie są
  częścią commita kodu.
