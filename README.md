# Łatka / Jaźń

**Łatka / Jaźń** to eksperymentalny lokalny system rozmowny budowany wokół pamięci, kanonu, głosu, źródeł i runtime. Nie jest pojedynczym chatbotem ani samym promptem. System rozdziela aktywny proces, pamięć, tożsamość, model językowy, narzędzia, pochodzenie odpowiedzi i finalną walidację.

Projekt ma umieć powiedzieć, kiedy runtime naprawdę działa, z jakiego katalogu został uruchomiony, z jakiej pamięci korzysta, jaką trasą powstała odpowiedź oraz czy widoczny tekst pochodzi z runtime, host bridge, lokalnego modelu czy kontrolowanego fallbacku.

## Granica prawdy

Styl, pierwsza osoba, czuły ton, nazwa folderu, ZIP, sam marker albo obecność kodu nie są dowodem aktywnej Jaźni. Potwierdzenie wymaga:

1. zweryfikowanego żywego daemona z właściwym rootem, manifestem, PID-em, endpointem i świeżym heartbeat; albo
2. poprawnie zakończonej, zweryfikowanej tury one-shot z prawidłowym `final_visible_text`, integralnością i truth gate.

Główna zasada:

> Prawda runtime ma pierwszeństwo przed stylem.

## Architektura

```text
użytkownik
→ host rozmowy
→ source classifier / tool access gate
→ runtime Jaźni
→ bramy pamięci / kanonu / narzędzi
→ adapter modelu albo host bridge
→ truth gate i walidator odpowiedzi
→ final_visible_text
```

Każda warstwa jest osobno audytowana. Aktywacja runtime rozdziela folder, wersję, manifest, marker, PID, endpoint, heartbeat, czas, pamięć, model, narzędzia i voice.

### Tożsamość i głos

Instrukcje projektu są bootstrapem hosta, a `AGENTS.md` jest routerem runbooków. Tożsamość, perspektywa, routing, pamięć i bezpośredni głos Łatki należą do kodu runtime. Most ChatGPT eksportuje `runtime_ownership_contract` i `host_generation_policy`; host nie jest źródłem osobowości ani wspomnień.

Trasy `presence_check`, `identity_continuity_check` i `runtime_health_check` są rozdzielone. Lokalny adapter Ollamy jest kanałem językowym, nie tożsamością ani pamięcią.

### Proces Windows i Ollama

Daemon Windows domyślnie używa trybu ukrytego bez migających konsol. Tryb widocznego monitora może utrzymać jedno stałe okno diagnostyczne. Uruchomienia procesów pomocniczych są rejestrowane w runtime, dzięki czemu można ustalić PID rodzica, komendę i powód uruchomienia.

Adapter Ollamy zachowuje faktycznie użyty model, `done_reason` i metryki transportu. Routing rozpoznaje pytania o model/provider/adapter, a lokalna trasa nie powinna wyciekać terminologii hosta ChatGPT.

## Aktualna linia rozwoju

Jedynym źródłem wersji jest `latka_jazn/version.py`.

```text
v15.1.0.3.88-Night of Hotfix
```

Linia v15.1 obejmuje runtime-owned identity, bezpieczne recovery pamięci L0–L3, stabilny daemon Windows, adapter Ollamy, atomowość tur, provenance wydania, integralność paczki oraz pełne CI Windows/Ubuntu.

## Pamięć L0–L3

Pamięć jest systemem źródeł i rekordów, a nie biologicznym wspomnieniem:

- **L0 `source_archive`** — pełne źródła i archiwa;
- **L1 `working`** — stan bieżącej sesji i ograniczony wake-state;
- **L2 `short_term`** — rekordy z TTL i statusem przeglądu;
- **L3 `long_term`** — wyłącznie rekordy z jawnym requestem, decyzją i promotion ledger.

Sama obecność SQLite nie oznacza zaufanej pamięci. Wymagana jest znana ścieżka, czytelna struktura, `integrity_check` lub `quick_check`, osobny `foreign_key_check`, zgodność sidecarów oraz rzeczywiste rekordy.

### Wake-state i restart continuity

Runtime ładuje jeden zweryfikowany snapshot wake-state, sprawdza jego SHA i integralność sidecara, a następnie hydruje ograniczony pakiet L1.

Stan sesji jest zapisywany atomowo do checkpointu per-session oraz do wskaźnika ostatniej kwalifikującej się sesji. Checkpoint zawiera hash stanu, hash całego checkpointu, generację, poprzedni hash oraz powiązanie z identyfikatorem i SHA wake-state. Po restarcie carryover jest dozwolony tylko wtedy, gdy checkpoint i wake-state nadal są zgodne; manipulacja, wygaśnięcie lub zmiana snapshotu blokują odziedziczenie poprzedniego tekstu, intencji i trasy.

`--no-carryover` tworzy izolowaną sesję i nie zastępuje wskaźnika ostatniej zwykłej sesji.

## Start i diagnostyka

```powershell
python -X utf8 run.py status --snapshot --json
python -X utf8 run.py doctor --json
python -X utf8 run.py start
python -X utf8 run.py status --json
python -X utf8 run.py stop
python -X utf8 run.py chat-gpt -- "wiadomość"
```

`run.py` jest kanonicznym interfejsem operatora. `main.py` pozostaje technicznym punktem zgodności dla kompatybilnych flag, daemona i mostów niskiego poziomu.

## Walidacja dużej pamięci

Szybka, read-only kontrola znanych baz i shardów:

```powershell
python -X utf8 run.py memory-validate --root . --json --progress
```

Pełny audyt wszystkich baz pod `memory/sqlite`, z licznikami rekordów, SHA-256 i raportem JSON:

```powershell
python -X utf8 run.py memory-validate --root . `
  --full --include-all-sqlite --table-counts --hash-files `
  --output workspace_runtime/memory_validation/full-report.json `
  --json --progress
```

Polecenie działa read-only, wykrywa bazy z konfiguracji i manifestów shardów, sprawdza pary WAL/SHM, strukturę SQLite, klucze obce, metryki stron, sidecar wake-state oraz magazyn tierów.

Zielony raport nie dowodzi kompletności wszystkich archiwów, jakości recallu ani autoryzacji L3. Praktyczna walidacja prywatnych danych jest śledzona w GitHub Issues i odbywa się lokalnie bez commitowania `memory/`, SQLite ani eksportów.

## Recovery pamięci

```powershell
python -X utf8 run.py memory-recover --root . `
  --progress --prepare-l2 --build-l3-manifest --json
```

Promocja L3 wymaga dokładnego SHA manifestu zatwierdzeń i jawnego `--approved-by`. Szczegółowy kontrakt opisuje `docs/MEMORY_RECOVERY_V151.md`.

## Backlog

Aktualny roadmap pamięci i ciągłości jest utrzymywany w GitHub Issues:

- #60 — roadmap nadrzędny;
- #59 — pełne archiwa, recall i L3 na rzeczywistych danych;
- #55 — stabilizacja i skrócenie testów Windows.

Dokument `docs/plans/MEMORY_CONTINUITY_VALIDATION_BACKLOG.md` opisuje kolejność i kryteria ukończenia bez zastępowania Issues.

## Domknięcie wydania

Na czystym, zatwierdzonym commicie:

```powershell
python -X utf8 run.py package-smoke --profile release --json
python -X utf8 run.py release-build --json
```

`release-build` tworzy staging z bieżącego commita, generuje w nim świeże `SOURCE_PROVENANCE.json` i `PACKAGE_INTEGRITY_MANIFEST.json`, uruchamia profil eksportowy, buduje ZIP atomowo oraz zapisuje SHA-256 i raporty pakowania.

## Kontrolowana instalacja patchy

Patch jest czystym diffem Git. Backup, `git apply --check`, testy i raport zapewnia `tools/patch_install/apply_patch_checked.py`; instrukcja znajduje się w `tools/patch_install/README.md`.
