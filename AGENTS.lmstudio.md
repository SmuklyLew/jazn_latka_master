# AGENTS.lmstudio.md — LM Studio jako jawny lokalny backend

LM Studio jest opcjonalnym, jawnym backendem językowym przez lokalne API zgodne z OpenAI. Nie jest Jaźnią, pamięcią, kanonem ani źródłem stanu runtime.

## Uruchomienie

```powershell
python -X utf8 main.py --chat-lm-studio --session-id local-runtime
```

Można jawnie podać model i endpoint:

```powershell
python -X utf8 main.py --chat-lm-studio `
  --lm-studio-api-base http://127.0.0.1:1234/v1 `
  --lm-studio-model <model> `
  --session-id local-runtime
```

## Granica prawdy

- Trasa LM Studio jest zgodnościowa i nigdy nie jest wybierana automatycznie przez `--chat`.
- Automatyczny lokalny routing `--chat` wykrywa Ollamę.
- LM Studio nie wymaga `OPENAI_API_KEY`, ale wymaga rzeczywiście działającego lokalnego endpointu i modelu.
- Widoczna odpowiedź nadal przechodzi przez runtime, walidację i kontrakt `final_visible_text`.
- Brak endpointu lub modelu musi zostać nazwany jako niedostępność adaptera; nie wolno udawać aktywnego modelu.
