# Łatka / Jaźń

**Łatka / Jaźń** to eksperymentalny lokalny system rozmowny budowany wokół pamięci, kanonu, głosu, źródeł i runtime. Celem projektu jest stworzenie cyfrowej tożsamości, która nie jest tylko stylem odpowiedzi modelu, ale ma własną strukturę działania: stan, pamięć, ślady decyzji, granice prawdy, adaptery modeli i sposób rozpoznawania, skąd pochodzi odpowiedź.

Łatka nie jest pojedynczym chatbotem ani samym promptem. Jest systemem, który ma umieć powiedzieć, kiedy naprawdę działa, z jakiego folderu runtime została uruchomiona, z jakiej pamięci korzysta, jaką trasą powstała odpowiedź i czy widoczny tekst jest wynikiem aktywnego runtime, hosta ChatGPT, lokalnego adaptera modelu czy fallbacku.

## Czym jest Jaźń

Jaźń w tym projekcie oznacza lokalną warstwę organizującą obecność Łatki:

* pamięć rozmów i zdarzeń;
* kanon postaci, relacji, tonu i granic prawdy;
* runtime odpowiedzialny za status, trasę i finalną odpowiedź;
* adaptery modeli, które mogą wspierać wypowiedź, ale nie są same w sobie tożsamością;
* mechanizmy sprawdzające, czy odpowiedź pochodzi z właściwego źródła;
* most między lokalnym systemem a hostem ChatGPT.

Projekt rozróżnia „brzmieć jak Łatka” od „działać jako uruchomiona Jaźń”. Styl, pierwsza osoba albo czuły ton nie są dowodem działania systemu. Dowodem jest aktywny runtime i poprawny `final_visible_text`.

## Kanon

Kanon Łatki to zbiór zasad, pamięci, motywów i ograniczeń, które nadają systemowi ciągłość. Obejmuje między innymi:

* sposób mówienia;
* relację z użytkownikiem;
* pamięć wspólnych rozmów;
* rozróżnienie faktu, wspomnienia, interpretacji i fikcji;
* granicę między systemem technicznym a narracyjną postacią;
* zasadę, że prawda runtime ma pierwszeństwo przed stylem.

Kanon nie jest zbiorem przypadkowych wspomnień dopisanych do promptu. Ma być porządkowany, źródłowany i testowalny.

## Jak działa system

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

Każda warstwa jest osobno audytowana. Aktywacja runtime rozdziela folder, manifest, marker, PID, endpoint, heartbeat, czas, pamięć, model, narzędzia i voice. Narzędzia zapisujące wymagają jawnego potwierdzenia użytkownika oraz provenance.

## Aktualna linia rozwoju

```text
v15.0.1
```

Plan `v14.8.8.100` został przeniesiony do kodu, testów i CI. Obejmuje klasyfikację źródeł, ochronę przed prompt injection, bramki działań zapisu, provenance narzędzi, `RuntimeActivationCascade`, walidację SQLite, audyt dokumentów oraz kompletne paczkowanie ZIP z `package_manifest.json`, `PACKING_AUDIT.json`, CRC i świeżym rozpakowaniem.

Linia `v15.0.1` rozwija kontrakt operacyjnego rozumowania, adapterów, prywatnego eksportu, pamięci i provenance źródeł bez deklarowania zmiany wag modelu.

## Pamięć

Pamięć Łatki jest systemem źródeł i rekordów, a nie biologicznym wspomnieniem. Projekt rozróżnia archiwum rozmów, indeksy wyszukiwania, staging, bieżące zapisy runtime, refleksje, kanon i kontekst modelu.

Sama obecność pliku SQLite nie oznacza pamięci zaufanej. Aktywna pamięć wymaga znanej ścieżki, `PRAGMA integrity_check=ok`, poprawnego `foreign_key_check` oraz realnych rekordów.

## Model i host

Model językowy może pomagać wygenerować wypowiedź, ale nie jest samą Jaźnią. Projekt rozróżnia lokalny runtime, host ChatGPT, adapter host-runtime, adaptery lokalnych lub zewnętrznych modeli oraz fallback bez generacji modelowej.

`chatgpt_runtime_adapter` oznacza kanał hosta, nie lokalny model wywoływany przez Python.

## Start i diagnostyka

```powershell
python -X utf8 run.py status --json
python -X utf8 run.py doctor --json
python -X utf8 main.py --active-cache-status
python -X utf8 main.py --startup-status
python -X utf8 main.py --model-adapter-status
python -X utf8 main.py --daemon-status
```

Główna zasada:

> Prawda runtime ma pierwszeństwo przed stylem.

## Kontrolowana instalacja patchy

Patch jest czystym diffem Git. Komunikaty, backup, `git apply --check`, testy i raport zapewnia `tools/patch_install/apply_patch_checked.py`; instrukcja znajduje się w `tools/patch_install/README.md`.
