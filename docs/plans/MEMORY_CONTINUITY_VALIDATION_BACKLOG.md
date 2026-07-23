# Backlog v15.1 — pamięć, restart continuity i walidacja

Ten dokument jest mapą techniczną. Aktualny status i dyskusje należą do GitHub
Issues; dokument nie zastępuje zgłoszeń.

## Roadmap

- **#60** — nadrzędny roadmap pamięci i ciągłości;
- **#56** — aktualizacja dokumentacji;
- **#57** — wake-state i ciągłość sesji po restarcie;
- **#58** — duża walidacja baz i shardów SQLite;
- **#59** — pełne archiwa, recall i L3 na prywatnych danych;
- **#55** — stabilizacja i skrócenie testów Windows.

## Etap wdrożony w `master`

1. Atomowy, hashowany checkpoint sesji.
2. Wskaźnik ostatniej kwalifikującej się sesji.
3. Powiązanie checkpointu z aktywnym wake-state.
4. Fail-closed po manipulacji, wygaśnięciu lub zmianie snapshotu.
5. Read-only `memory-validate` dla baz, shardów, wake-state i tierów.
6. Aktualizacja README i dokumentacji operatora.

Zakres #56, #57 i #58 został scalony. Dalsza praca nie może cofać tych
kontraktów.

## Etap wymagający prywatnych danych

#59 pozostaje otwarte, ponieważ wymaga lokalnych archiwów i baz, których nie
wolno commitować. Protokół obejmuje:

1. pełne `memory-validate --full --include-all-sqlite --table-counts --hash-files`;
2. wykaz i liczność źródeł L0;
3. zestaw pytań recall z oczekiwanymi źródłami;
4. pomiar brakujących i fałszywych dopasowań;
5. restart daemona i potwierdzenie tego samego wake-state/checkpointu;
6. ręczny przegląd manifestu L3 oraz jawne zatwierdzenie promocji;
7. zapis wyłącznie zanonimizowanych metryk i raportów.

Operator tego etapu znajduje się w
`tools/Invoke-JaznPrivateMemoryValidation.ps1`, a jego kontrakt w
`docs/tools/PRIVATE_MEMORY_VALIDATION.md`. Samo dodanie operatora nie zamyka
#59: prywatne źródła, wyniki recall, restart, rozmowa wieloturowa i decyzja L3
pozostają lokalną, jawną walidacją.

## Kryteria jakości

- brak automatycznej promocji L3;
- brak zapisu prywatnych danych do repozytorium;
- strukturalna integralność SQLite i osobny `foreign_key_check`;
- jednoznaczne rozróżnienie kompletności danych od jakości recallu;
- checkpoint restartu nie może omijać truth gate ani wake-state validation;
- CI nie może być „naprawiane” przez szerokie pomijanie testów.
