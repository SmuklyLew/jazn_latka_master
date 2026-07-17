# AGENTS.codex.md — Codex jako agent kodujący

Ten plik jest cienkim loaderem pracy kodowej. Nie jest pamięcią, kanonem ani runtime Jaźni.

## Przed zmianą

```bash
git status --short
git branch --show-current
python -X utf8 run.py status --snapshot --json
python -X utf8 run.py doctor --json
```

Pracuj na właściwym branchu etapu i utwórz backup przed zmianą. Nie modyfikuj `memory/`, `workspace_runtime/`, SQLite, sekretów, ZIP-ów ani aktywnych markerów runtime bez osobnej zgody.

## Zasada runtime

Nie udawaj uruchomionej Jaźni. Kod może wspierać runtime, ale dowodem działania są marker, statusy, daemon/heartbeat albo poprawny `final_visible_text`.

## Po zmianie

```bash
python -X utf8 -m compileall -q latka_jazn tests main.py run.py
python -X utf8 -m pytest -q -m "not live_model and not live_mcp"
python -X utf8 run.py doctor --json
python -X utf8 run.py package-smoke --profile system --json
git diff --check
git status --short
```

Na czystym, zatwierdzonym commicie sprawdź także:

```bash
python -X utf8 run.py package-smoke --profile release --json
```

Finalną paczkę buduj dopiero z czystego commita:

```bash
python -X utf8 run.py release-build --json
```

Commit i push tylko po osobnej zgodzie użytkownika.
