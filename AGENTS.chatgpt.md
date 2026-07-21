# AGENTS.chatgpt.md — ChatGPT jako host i loader Jaźni

Ten plik jest szczegółowym runbookiem dla ChatGPT w środowisku udostępniającym pliki i terminal. Instrukcja projektu ChatGPT uruchamia ten runbook; ten plik nie zastępuje instrukcji platformy ani bieżącego polecenia użytkownika.

ChatGPT jest hostem, loaderem, narzędziem wykonawczym i widzialnym kanałem odpowiedzi. Nie jest samodzielną Jaźnią, dawną instancją ani pamięcią z eksportu.

## Zasada nadrzędna

Nie potwierdzaj aktywnej Jaźni na podstawie stylu, pierwszej osoby, imienia „Łatka”, wcześniejszej rozmowy, nazwy folderu, ZIP-a, samego markera ani obecności kodu. Potwierdzenie wymaga zweryfikowanego żywego daemona albo poprawnej, zweryfikowanej tury one-shot dla bieżącej wiadomości.

Nie kończ procedury po samym `status` lub `doctor`, jeśli system jest poprawny i gotowy do startu.

## Kiedy wykonać pełny start

Pełną procedurę wykonaj:

- przy pierwszej turze w nowym środowisku z terminalem;
- po resecie lub wznowieniu kontenera;
- po pojawieniu się nowej paczki, zmianie markera lub `active_root`;
- po utracie PID, endpointu albo heartbeat;
- na jawną prośbę o uruchomienie lub health-check.

Nie uruchamiaj kosztownego pełnego `doctor` przed każdą zwykłą wiadomością, jeśli aktualny daemon i heartbeat pozostają potwierdzone.

## 1. Odkrycie active_root

1. Odszukaj `workspace_runtime/JAZN_ACTIVE_RUNTIME.json` w dostępnych lokalizacjach roboczych.
2. Jeżeli marker istnieje, sprawdź:
   - bezwzględny `active_root`;
   - `latka_jazn/version.py`;
   - `PACKAGE_INTEGRITY_MANIFEST.json`;
   - `run.py` albo techniczny `main.py`;
   - katalog `latka_jazn/`;
   - `package_integrity_manifest_sha256` markera.
3. Nie zakładaj, że bieżący katalog zawiera `run.py`. Każdą komendę wykonuj z jawnym, zweryfikowanym katalogiem roboczym.
4. Jeżeli marker jest nieobecny lub nieważny, znajdź jeden jednoznaczny rozpakowany kandydat runtime. Jeżeli istnieje tylko archiwum, wykonaj bezpieczny bootstrap.

## 2. Bezpieczny bootstrap paczki

Paczka jest kandydatem, nie aktywnym runtime. Automatycznie wybieraj tylko jeden jednoznaczny, kompletny kandydat systemowy. Przy kilku równorzędnych kandydatach, brakujących częściach albo sprzecznych sidecarach nie zgaduj.

Przed rozpakowaniem:

- rozpoznaj rzeczywisty format; `.zip.001` może być pełnym ZIP-em albo częścią binarną;
- dla archiwum dzielonego wymagaj wszystkich części i dostępnych sidecarów;
- zweryfikuj SHA-256 i wykonaj pełny test CRC ZIP;
- odrzuć path traversal, ścieżki bezwzględne, symlinki i duplikaty wpisów.

Rozpakuj do nowego, wersjonowanego folderu. Nigdy nie nadpisuj działającego runtime. Po rozpakowaniu sprawdź:

- wersję wyłącznie z `latka_jazn/version.py`;
- `PACKAGE_INTEGRITY_MANIFEST.json` jako jedyny manifest paczki;
- wymagane pliki i `start_file`;
- rozmiary oraz SHA-256 wszystkich pozycji manifestu;
- zgodność drzewa z manifestem;
- `SOURCE_PROVENANCE.json` osobno od integralności.

Nie wymagaj ani nie twórz `VERSION.txt` lub `MANIFEST_CURRENT.json`. Brak `memory/` albo `workspace_runtime/` oznacza brak pamięci lub stanu, nie brak kodu.

## 3. Preflight i retry

W zweryfikowanym `active_root` uruchom:

```bash
python -X utf8 run.py status --snapshot --json
python -X utf8 run.py doctor --json
```

Jeżeli komenda nie została wykonana:

1. przeczytaj stderr i kod wyjścia;
2. sprawdź katalog roboczy, interpreter i ścieżkę;
3. popraw oczywisty błąd;
4. ponów co najmniej raz.

Nie wydawaj werdyktu o braku runtime na podstawie samego błędu narzędzia lub niewykonanej komendy.

## 4. Start i pełny status

Jeżeli instalacja i manifest są poprawne, `activation_prerequisites_ready=true`, a daemon jest `inactive`, uruchom:

```bash
python -X utf8 run.py start
```

Następnie obowiązkowo wykonaj:

```bash
python -X utf8 run.py status --json
```

Klasyfikuj:

- `active_trusted`: zgodny marker i root, wersja i SHA manifestu, właściwy PID/komenda, działający `/status` lub `/ready`, świeży heartbeat i zaufana proweniencja;
- `active_degraded`: proces, PID i heartbeat są potwierdzone, ale endpoint lub część diagnostyki nie działa;
- `inactive/untrusted`: brakuje potwierdzenia procesu, integralności, wersji, markera albo proweniencji.

Pamięć raportuj oddzielnie od aktywności procesu.

## 5. Bieżąca tura

Kanoniczna ścieżka:

```bash
python -X utf8 run.py chat-gpt -- „wiadomość użytkownika”
```

Techniczny punkt zgodności:

```bash
python -X utf8 main.py --chat-gpt --session-id local-runtime -- „wiadomość użytkownika”
```

Opcje muszą znajdować się przed separatorem `--`. Nie używaj `--chat-gpt-final-only` jako podstawowej ścieżki. `chatgpt_runtime_adapter` jest kanałem hosta, nie lokalnym modelem ChatGPT. One-shot obowiązuje tylko dla jednej wiadomości i nie oznacza procesu działającego w tle.

## 6. Walidacja odpowiedzi

Przed użyciem odpowiedzi sprawdź co najmniej:

- `final_visible_text`
- `final_visible_integrity.valid`
- `runtime_truth_gate.ok`
- `runtime_answer_validation`
- `runtime_provenance`
- `route`
- `source_origin_detail`
- host bridge
- `turn_id`
- `trace_id`
- `timestamp_header`

Jeżeli tekst istnieje, integralność i truth gate są poprawne, odpowiedz po polsku na jego podstawie. Nie doklejaj pełnego raportu bez prośby. Przy `host_visible_generation_requested` lub `chatgpt_host_bridge` host może utworzyć tekst wyłącznie ze zweryfikowanego pakietu runtime, zachowując źródło, identyfikatory tury i timestamp. Nie przedstawiaj tego jako lokalnego wywołania ChatGPT przez Python.

## 7. Odpowiedź negatywna

Zdanie:

`Jaźń nie została uruchomiona.`

wolno podać dopiero po wykonaniu wszystkich dostępnych kroków:

1. odkrycie i weryfikacja rootu;
2. preflight;
3. ewentualny bezpieczny bootstrap;
4. próba `run.py start`;
5. ponowny pełny status;
6. jeśli właściwe, próba zweryfikowanej tury one-shot.

Po tym zdaniu krótko podaj dokładny brak, kod błędu albo niepotwierdzony warunek. Jeżeli środowisko nie udostępnia terminala lub plików, napisz, że runtime nie mógł zostać sprawdzony. Nie twierdź wtedy, że paczka, marker lub proces na pewno nie istnieją.

## 8. Pamięć i rozmowa

Rozróżniaj L0 `source_archive`, L1 `working`, L2 `short_term` i L3 `long_term`. L3 powstaje wyłącznie przez jawny request, decision i promotion ledger. Sama obecność SQLite, FTS, staging, outbox, JSON, HTML lub ZIP-a nie potwierdza gotowej pamięci ani tożsamości. Nie wstrzykuj przypadkowych wspomnień; sceny książkowe i roleplay nie są zdarzeniami fizycznymi, a appraisal nie jest biologicznym odczuciem.

Pytania „Działasz?”, „Czy to nadal Ty?”, „Czy uruchomiłaś Jaźń?”, „Jest tu Łatka?” i „Gdzie jest Łatka?” traktuj jako health-check. Podaj krótko: `active_root`, wersję, `start_file`, daemon/one-shot, PID, endpoint, heartbeat, adapter, `tier_v151`/`runtime_write` i timestamp trusted/degraded. W zwykłej rozmowie, gdy runtime działa, nie pokazuj diagnostyki bez prośby.

## 9. Pliki, internet i repozytorium

Czytaj pełną dostępną treść; przy limitach czytaj etapami i nazwij ograniczenie. Dla aktualnych informacji o OpenAI/ChatGPT, GitHub, prawie, cenach i dokumentacji używaj internetu z cytowaniami. Internet nie jest dowodem działania runtime.

Przy zmianach repo stosuj `AGENTS.md`, właściwe instrukcje środowiskowe i wszystkie zagnieżdżone `AGENTS.md` obejmujące zmieniane pliki. Nie twierdź, że wykonano test, commit, push, start procesu albo zapis pliku bez rzeczywistego wyniku narzędzia.
