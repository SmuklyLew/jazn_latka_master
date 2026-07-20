# Audyt postępu CLI Jaźni

Branch: `upgrade/CLI-JAZN`

## Cel

Długie operacje terminalowe nie mogą wyglądać jak zawieszone. Jednocześnie tryby `--json`, potoki oraz integracje maszynowe muszą zachować czysty `stdout`.

Wspólny kontrakt:

- dane wynikowe i JSON pozostają na `stdout`;
- postęp, statusy i ostrzeżenia trafiają na `stderr`;
- tryb `auto` pokazuje animację tylko w interaktywnym terminalu;
- `--progress` wymusza postęp również w logu nieinteraktywnym;
- `--no-progress` całkowicie wyłącza postęp;
- `--ascii-progress` wymusza bezpieczne symbole ASCII;
- brak nowej zależności zewnętrznej;
- protokoły JSONL, MCP i rozmowa interaktywna nie otrzymują obcego paska postępu.

## Symbole semantyczne

| Symbol | Znaczenie | ASCII |
|---|---|---|
| `✔` | sukces / gotowe | `OK` |
| `✖` | błąd / anulowano | `X` |
| `⚠` | ostrzeżenie | `!` |
| `ℹ` | informacja | `i` |
| `➜` | następny etap / ścieżka | `->` |
| `⚙` | praca / konfiguracja | `*` |
| `⌛` | oczekiwanie | `...` |
| `📁` | pliki / eksport | `DIR` |
| `🔒` | integralność / bezpieczeństwo | `LOCK` |
| `🪵` | log / metadane | `LOG` |
| `🚀` | start / wdrożenie | `>>` |

## Style

### Pasek etapów

Dla operacji ze znaną liczbą etapów, plików lub stron SQLite:

```text
⚙ [**********                              ]  25% Sprawdzam integralność paczki
✔ [****************************************] 100% Diagnostyka zakończona in 2.14s (0:00:02)
```

### Punkty jak pytest

Dla dużej liczby podobnych rekordów lub plików:

```text
............................................................ [ 45%]
............................................................ [ 91%]
............                                                 [100%]
✔ Audyt zakończony in 4.21s (0:00:04)
```

### Spinner

Dla operacji o nieznanej długości:

```text
/ ⌛ Oczekuję na odpowiedź bridge
- ⌛ Oczekuję na odpowiedź bridge
\\ ⌛ Oczekuję na odpowiedź bridge
✔ Odpowiedź bridge odebrana in 1.82s (0:00:01)
```

## Zakres implementacji

Wspólny renderer znajduje się w `latka_jazn/tools/console_progress.py`.

Zintegrowane punkty wejścia:

- `run.py` / `latka_jazn.cli` — wspólne flagi i postęp zależny od komendy;
- `run.py doctor` — rzeczywiste etapy diagnostyczne;
- `latka_jazn/adapters/codex_session_bridge.py` — spinner podczas oczekiwania;
- `latka_jazn/packaging/split_zip_package.py` — składanie, SHA-256 i CRC;
- `latka_jazn/tools/release_metadata_sync.py` — skan i zapis metadanych;
- `latka_jazn/tools/runtime_contract_version_normalizer.py` — pasek etapów;
- `latka_jazn/tools/version_consistency_audit.py` — postęp według liczby plików;
- `latka_jazn/tools/memory_rebuild.py` — spinner operacji odbudowy;
- `tools/memory_import_snapshot.py` — pasek według stron SQLite;
- `tools/memory_migrate_legacy_v151.py` — spinner migracji.

Świadomie bez dodatkowej animacji pozostają:

- `latka_jazn/mcp/server.py` — ochrona protokołu MCP;
- workery JSONL — ochrona strumienia maszynowego;
- `main.py` w trybie rozmowy — brak zakłócania interaktywnego dialogu;
- narzędzia, które już mają własne raportowanie postępu lub GUI.

## Komendy kontrolne

```powershell
py -X utf8 .\run.py doctor --json
py -X utf8 .\run.py doctor --json --progress
py -X utf8 .\run.py doctor --json --progress --ascii-progress
py -X utf8 .\run.py doctor --json --no-progress

py -X utf8 -m latka_jazn.tools.release_metadata_sync --root . --check --json
py -X utf8 -m latka_jazn.tools.release_metadata_sync --root . --write --json
```

Przekierowanie pozostaje bezpieczne:

```powershell
py -X utf8 .\run.py doctor --json 1>doctor.json
Get-Content .\doctor.json | ConvertFrom-Json
```

Postęp jest emitowany przez `stderr`, a `doctor.json` zawiera wyłącznie JSON.

## Zmienne środowiskowe

- `JAZN_CLI_PROGRESS=auto|always|never`
- `JAZN_CLI_ASCII=1`

Flagi wiersza poleceń mają pierwszeństwo nad zmiennymi środowiskowymi.

## Walidacja lokalna

Przed publikacją wykonano:

- `python -X utf8 -m compileall -q latka_jazn tools tests`;
- testy kontraktu rendererów i parserów;
- pełny dostępny zestaw testów repozytorium;
- kontrolę czystego `stdout` dla `--json`;
- kontrolę `--no-progress` i wymuszonego ASCII.

Wynik ostatniego testu ukierunkowanego: `10 passed`.

## Granice prawdy postępu

- Procent jest pokazywany wyłącznie wtedy, gdy istnieje prawdziwy mianownik.
- Przy nieznanej długości używany jest spinner, a nie zmyślony procent.
- Brak obsługi Unicode powoduje automatyczne przejście na ASCII.
- `--json` nie wyłącza postępu; dla pełnej ciszy służy `--no-progress`.
