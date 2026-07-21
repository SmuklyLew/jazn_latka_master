# AGENTS.ollama.md — Ollama jako lokalny backend językowy

Ten plik opisuje wyłącznie integrację lokalnego modelu Ollama z runtime Jaźni. Ollama nie jest Jaźnią, pamięcią, kanonem, routerem ani źródłem prawdy o aktywności procesu.

## Rola i granica prawdy

- Tożsamość, pamięć, routing, walidacja, narzędzia i decyzje L2/L3 należą do runtime Jaźni.
- Model Ollama nie może sam zatwierdzać pamięci, deklarować wykonania narzędzia ani potwierdzać aktywnego runtime.
- Odpowiedź modelu jest kandydatem językowym i musi przejść kontrakty runtime, truth gate oraz walidację widocznej odpowiedzi.
- Brak modelu, endpointu lub zgodnej odpowiedzi prowadzi do prawdomównego błędu albo jawnego fallbacku, nigdy do udawania działania.

## Kanoniczne uruchomienie

Uruchom lokalny runtime z natywnym adapterem Ollama:

```bash
python -X utf8 main.py --chat-ollama --session-id local-runtime
```

Można jawnie wskazać model i endpoint:

```bash
python -X utf8 main.py --chat-ollama \
  --ollama-model <nazwa-modelu> \
  --ollama-api-base http://127.0.0.1:11434 \
  --session-id local-runtime
```

Zgodne zmienne środowiskowe:

```text
JAZN_OLLAMA_MODEL=<nazwa-modelu>
JAZN_OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Nie wymagaj `OPENAI_API_KEY` dla lokalnej Ollamy.

## Kontrakt transportu

Domyślny lokalny adres API Ollamy to:

```text
http://127.0.0.1:11434/api
```

Wymagane operacje:

- wykrywanie modeli: `GET /api/tags`;
- rozmowa: `POST /api/chat`;
- wiadomości w polu `messages` z rolami i treścią;
- poprawne zakończenie odpowiedzi potwierdzone przez `done=true`;
- obsługa odpowiedzi strumieniowej lub jawne `stream=false` zgodnie z adapterem;
- respektowanie timeoutu, limitu wyjścia i jawnie wybranego modelu.

Lokalny endpoint `http://127.0.0.1:11434` nie wymaga uwierzytelnienia. Modele chmurowe Ollama i bezpośredni dostęp do `https://ollama.com/api` mogą wymagać logowania lub klucza; nie myl tego z lokalnym transportem.

## Diagnostyka

Przed użyciem modelu sprawdź:

1. czy endpoint odpowiada;
2. czy żądany model jest widoczny w `/api/tags`;
3. czy konfiguracja runtime wskazuje adapter Ollama;
4. czy odpowiedź `/api/chat` ma poprawną strukturę;
5. czy runtime zachowuje źródło modelu, metryki i przyczynę zakończenia.

Raportuj oddzielnie:

- stan daemona Jaźni;
- stan adaptera Ollama;
- dostępność endpointu;
- nazwę faktycznie użytego modelu;
- timeout lub błąd transportu.

Działająca Ollama nie dowodzi działania Jaźni, a działająca Jaźń nie dowodzi dostępności Ollamy.
