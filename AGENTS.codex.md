# AGENTS.codex.md — Codex jako agent kodujący

Ten plik jest cienkim loaderem pracy kodowej. Nie jest pamięcią, kanonem ani runtime Jaźni.

## Przed zmianą

```bash
git status --short
git branch --show-current
python -X utf8 main.py --startup-status
```

Pracuj na właściwym branchu etapu. Nie modyfikuj `memory/`, `workspace_runtime/`, SQLite, sekretów, ZIP-ów ani aktywnych markerów runtime bez osobnej zgody.

## Zasada runtime

Nie udawaj uruchomionej Jaźni. Kod może wspierać runtime, ale dowodem działania są marker, statusy, daemon/heartbeat albo poprawny `final_visible_text`.

## Po zmianie

```bash
python -X utf8 -m compileall tools latka_jazn tests
python -X utf8 -m pytest <focused-tests> -q
git diff --check
git status --short
```

Commit i push tylko po osobnej zgodzie użytkownika.
