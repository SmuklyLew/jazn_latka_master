from __future__ import annotations
SCHEMA_VERSION="lexical_license_guard/v14.6.10"
class LexicalLicenseGuard:
    def note_for(self, source: str) -> str:
        notes = {
            'plwordnet': 'Sprawdź aktualną licencję plWordNet/Słowosieci przed dystrybucją danych.',
            'wiktionary': 'Treści Wiktionary mogą być objęte licencją CC BY-SA/GFDL; zapisuj źródło i unikaj długich cytatów.',
            'sjp': 'Używać ostrożnie, bez agresywnego scrape; preferuj link lub ręczne sprawdzenie.',
            'wsjp': 'Źródło referencyjne; sprawdź warunki korzystania i cytowania.',
        }
        return notes.get((source or '').lower(), 'Zapisz źródło, datę pobrania i licencję, jeśli wynik pochodzi z zasobu zewnętrznego.')
