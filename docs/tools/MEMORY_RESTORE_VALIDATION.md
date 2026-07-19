# Walidacja `memory_restore`

Ten plik utrwala zakres końcowej walidacji PR-a po automatycznym odświeżeniu manifestu.

Wymagane przed scaleniem:

- pełny pytest na Ubuntu i Windows;
- `package-smoke --profile system` na obu systemach;
- `package-smoke --profile release` na obu systemach;
- testy integralności, hardeningu i finalization;
- test `tests/test_memory_restore_tool.py`;
- brak baz SQLite, eksportów ZIP, `memory/` i `workspace_runtime/` w zmianach.

Rzeczywisty pełny `test_03` na wszystkich prywatnych eksportach jest etapem operatorskim po scaleniu narzędzia. CI używa wyłącznie syntetycznych fixture’ów bez danych prywatnych.
