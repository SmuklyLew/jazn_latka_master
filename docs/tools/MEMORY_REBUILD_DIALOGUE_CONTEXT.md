# Kontekst dialogowy w klasyfikacji memory_rebuild

## Cel poprawki

Klasyfikowanie odpowiedzi asystenta wyłącznie na podstawie jej własnego tekstu
może zgubić intencję bezpośredniego polecenia użytkownika. Przykład:

```text
Użytkownik: Odegrajmy scenę do rozdziału książki. Wciel się w Łatkę.
Asystent: Kasia weszła do kuchni. Poranne światło drżało na stole.
```

Druga wypowiedź nie zawiera słów `książka`, `scena` ani `roleplay`, lecz nadal
jest częścią zamówionej sceny. Bez kontekstu mogłaby zostać błędnie opisana jako
zwykła rozmowa faktyczna.

## Zasada kontekstu

Dla wiadomości asystenta klasyfikator otrzymuje wyłącznie tekst bezpośrednio
poprzedzającej tury użytkownika. Ten kontekst:

- nie zmienia tekstu źródłowego;
- nie jest dopisywany do archiwum rozmów;
- nie zmienia drzewa ani SHA rozmowy;
- ma ograniczoną wagę;
- jest widoczny w `evidence` jako `context:previous_user_turn`;
- służy jedynie do zachowania funkcji bieżącej pary user–assistant.

Kontekst nie jest przenoszony przez wiele tur. Każda nowa wiadomość użytkownika
zastępuje wcześniejszy kontekst dla kolejnej odpowiedzi asystenta. Dzięki temu
stary temat rozmowy nie dominuje nad późniejszą zmianą kierunku.

Tytuł rozmowy pozostaje jeszcze słabszym priorem niż poprzednia tura. Głównym
źródłem klasyfikacji nadal jest bieżąca wypowiedź.

## Granica prawdy

Kontekst może sprawić, że odpowiedź narracyjna pozostanie w segmencie:

```text
primary_domain: book
mode: scene_roleplay
truth_status: book_scene
```

Nie oznacza to, że scena wydarzyła się fizycznie. Wręcz przeciwnie: zachowanie
kontekstu chroni przed zamianą roleplayu lub tekstu książkowego w fakt albo
wspomnienie.

Klasyfikator nadal nie:

- tworzy doświadczenia;
- zatwierdza kandydatów;
- zapisuje do L2 lub L3;
- interpretuje styl pierwszej osoby jako dowód przeżycia runtime.

## Dodatkowe poprawki etykiet dziennika

Follow-up zachowuje zgodność ze starszymi etykietami:

- `fabuły` jest traktowane tak samo jak `fabuła` i pozostaje `book_scene`;
- `przeżycie_filmowe`, reakcja na film lub muzykę otrzymują profil
  `media_reaction`, a nie `media_analysis`;
- reakcja na medium może trafić do ręcznej kontroli, natomiast sama analiza
  treści nadal jest odfiltrowywana od kandydatów autobiograficznych.

## Walidacja

Test regresji wymaga, aby para user–assistant z przykładu powyżej utworzyła
jeden segment `book_scene` obejmujący obie wiadomości. Dowody segmentu muszą
zawierać informację o użyciu bezpośredniego kontekstu użytkownika.

Przy ponownej analizie istniejących segmentów użyj:

```powershell
py -X utf8 tools\memory_rebuild.py `
  --root "D:\.AI\jazn_memory_test_02" `
  --json analyse-topics `
  --force |
  Tee-Object "D:\.AI\jazn_memory_test_02\13_analyse_topics_force.json"
```

`--force` jest konieczne dla baz, które miały już utworzone profile tematów
przed zmianą reguł. W `jazn_memory_test_02`, gdzie analiza tematów nie została
jeszcze uruchomiona, zwykłe `analyse-topics` jest wystarczające.

Po analizie uruchom ponownie:

```powershell
py -X utf8 tools\memory_rebuild.py `
  --root "D:\.AI\jazn_memory_test_02" `
  --json audit-classifiers `
  --limit 100 |
  Tee-Object "D:\.AI\jazn_memory_test_02\14_audit_after_topics.json"
```

Dopiero później można zbudować ograniczoną próbkę kandydatów. Nadal nie należy
uruchamiać automatycznego zatwierdzania ani promocji pamięci.

## Podstawa techniczna

Literatura dotycząca klasyfikacji aktów dialogowych wskazuje, że funkcja
wypowiedzi zależy od kontekstu wcześniejszych tur i od roli mówcy. Dlatego
zastosowano ograniczony kontekst bezpośredniej tury użytkownika zamiast
klasyfikacji odpowiedzi w izolacji.

Przy przyszłym modelu statystycznym należy:

- zachować klasyfikację wieloetykietową;
- dzielić dane treningowe i testowe grupami rozmów lub źródeł;
- nie mieszać wariantów tej samej rozmowy pomiędzy treningiem i walidacją;
- kalibrować pewność wyłącznie na niezależnym, ręcznie oznaczonym zbiorze;
- przechowywać jawne źródła i dowody obok predykcji.

Bieżąca implementacja nie dodaje modelu ML ani nowych zależności. Pozostaje
lekka, deterministyczna i audytowalna.
