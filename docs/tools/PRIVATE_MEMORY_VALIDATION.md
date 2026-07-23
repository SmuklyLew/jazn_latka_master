# Prywatna walidacja pamięci Jaźni

`tools/Invoke-JaznPrivateMemoryValidation.ps1` jest lokalnym operatorem dla
Issue #59. Uruchamia kontrole na prywatnych archiwach i bazach użytkownika,
ale zapisuje do raportu wyłącznie zanonimizowane metryki, hashe i liczniki.

## Granica prawdy i prywatności

Operator:

- nie kopiuje pytań recall do raportu;
- nie kopiuje oczekiwanych terminów ani treści wyników;
- zapisuje SHA-256 pytania, liczbę trafień i wynik warunków;
- nie zapisuje ścieżek źródeł, tylko ich kolejność, rozmiar i SHA-256;
- nie modyfikuje źródłowych archiwów;
- nie wywołuje `approve-l3-manifest-sha`;
- nie promuje automatycznie rekordów do L2 ani L3;
- nie dowodzi kompletności wspomnień ani naturalności rozmowy.

Prywatne pliki robocze należą do
`workspace_runtime/private_memory_validation` i nie mogą być commitowane.

## Wymagania

- aktualny checkout zawierający merge PR #63, commit
  `109b6823ac23eefa7174b570b851bae106c04d5f` jako przodka;
- działające `run.py doctor`, `run.py status` i `run.py memory-validate`;
- kanoniczny CLI `latka_jazn.tools.memory_rebuild`;
- lokalny plik przypadków `jazn_private_recall_cases/v1`;
- prywatne źródła L0 dostępne wyłącznie lokalnie.

Domyślnie operator wymaga czystych śledzonych plików Git. `-AllowDirty` jest
jawnym wyjątkiem operatorskim i nie zmienia reguł prywatności.

## Utworzenie szablonu

```powershell
& ".\tools\Invoke-JaznPrivateMemoryValidation.ps1" `
  -Root . `
  -WriteTemplate
```

Powstaje:

```text
workspace_runtime/private_memory_validation/recall-cases.template.json
```

Skopiuj go do prywatnej nazwy, uzupełnij i nie commituj.

## Schemat przypadków

```json
{
  "schema_version": "jazn_private_recall_cases/v1",
  "minimums": {
    "counts.archive_chats.conversations": 1,
    "counts.archive_chats.nodes": 1
  },
  "source_files": [
    "D:\\PRIVATE\\chat-export.zip"
  ],
  "recall_cases": [
    {
      "id": "case-001",
      "query": "Prywatne pytanie kontrolne",
      "expected_any": ["jeden z oczekiwanych terminów"],
      "expected_all": [],
      "forbidden_any": ["termin, którego nie powinno być"],
      "expected_sources": ["archive_chats"],
      "minimum_hits": 1,
      "limit": 20
    }
  ]
}
```

Ścieżki względne w `source_files` są rozwiązywane względem katalogu pliku
przypadków. To pozwala trzymać źródła pod lokalnym katalogiem
`workspace_runtime/private_memory_validation/sources`.

## Walidacja bez restartu

```powershell
& ".\tools\Invoke-JaznPrivateMemoryValidation.ps1" `
  -Root . `
  -RecallCases ".\workspace_runtime\private_memory_validation\recall-cases.private.json"
```

Operator wykonuje kolejno:

1. `doctor` i stan runtime;
2. pełne `memory-validate --full --include-all-sqlite --table-counts --hash-files`;
3. `status` i `verify` pięciu baz memory rebuild;
4. inwentaryzację prywatnych źródeł bez utrwalania nazw;
5. deterministyczny benchmark recall;
6. kontrolę obecności manifestu L3 bez promocji;
7. opcjonalny restart i porównanie wake-state oraz checkpointu.

## Test ciągłości po restarcie

```powershell
& ".\tools\Invoke-JaznPrivateMemoryValidation.ps1" `
  -Root . `
  -RecallCases ".\workspace_runtime\private_memory_validation\recall-cases.private.json" `
  -RestartDaemon `
  -RestartTimeoutSeconds 90
```

Restart przechodzi tylko wtedy, gdy komenda restartu kończy się sukcesem,
status wraca przed timeoutem, fingerprint wake-state jest równy, a SHA-256
`runtime_session_state.json` pozostaje zgodny.

## Raport

Każde uruchomienie tworzy katalog UTC pod
`workspace_runtime/private_memory_validation` i zapisuje
`summary.sanitized.json`. Raport może potwierdzić integralność, minima,
wyniki recall i ciągłość restartu. Nie zawiera surowych pytań ani wyników.

Issue #59 wolno zamknąć dopiero po łącznym potwierdzeniu pełnego importu L0,
zaakceptowanego benchmarku recall, testu restartu, ręcznej rozmowy
wieloturowej oraz jawnego przeglądu i decyzji dotyczącej L3.
