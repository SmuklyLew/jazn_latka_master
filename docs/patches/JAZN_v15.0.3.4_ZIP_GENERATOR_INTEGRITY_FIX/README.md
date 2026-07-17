# JAZN v15.0.3.4 — ZIP generator integrity fix

## Problem

Generator poprawnie sprawdzał strukturę i CRC archiwów, ale kopiował do ZIP-a zastany `PACKAGE_INTEGRITY_MANIFEST.json`. W rezultacie paczka z kodem `v15.0.3.4` mogła zawierać manifest `v15.0.3.2`, nieaktualne rozmiary i SHA-256 oraz pliki nieuwzględnione w manifeście.

## Zakres poprawki

- manifest jest budowany z dokładnej listy plików używanej do pakowania;
- wersja manifestu pochodzi wyłącznie z `latka_jazn/version.py`;
- świeży manifest trafia do ZIP-a jako wirtualny wpis, bez nadpisywania pliku źródłowego;
- po utworzeniu wszystkich niezależnych woluminów ZIP generator sprawdza wersję, rozmiary i SHA-256 każdego pliku względem osadzonego manifestu;
- dodatkowe pliki poza manifestem blokują paczkę, z wyjątkiem jawnie dozwolonego prefiksu `memory/` w profilu pełnym;
- lock, ustawienia generatora i kopie `*.before.py` nie trafiają do paczki.

## Zastosowanie do lokalnego generatora

Generator nie jest obecnie śledzony w branchu `master`, dlatego poprawka jest dostarczona jako patch:

```powershell
git apply --check .\docs\patches\JAZN_v15.0.3.4_ZIP_GENERATOR_INTEGRITY_FIX\jazn_v15.0.3.4_zip_generator_integrity_fix.patch
git apply .\docs\patches\JAZN_v15.0.3.4_ZIP_GENERATOR_INTEGRITY_FIX\jazn_v15.0.3.4_zip_generator_integrity_fix.patch
```

Patch oczekuje pliku:

```text
tools/_jazn_pack_generator.py
```

## Walidacja

Przeprowadzono:

- `python -X utf8 -m py_compile` dla zmienionych modułów;
- 78 istniejących testów dotyczących wydania, ścieżek, provenance i paczek;
- 4 nowe testy regresyjne integralności ZIP;
- rzeczywiste pakowanie przesłanej paczki `v15.0.3.4` do 7 niezależnych ZIP-ów;
- pełny CRC każdego woluminu;
- sprawdzenie, że osadzony manifest ma wersję `v15.0.3.4` i odpowiada faktycznym plikom;
- sprawdzenie, że źródłowy stary manifest nie został po cichu zmieniony.
