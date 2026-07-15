from __future__ import annotations
from datetime import datetime, timezone
from latka_jazn.nlp.providers.base import ProviderLemmaCandidate, ProviderLookupResult

class OptionalMorfeuszPolishProvider:
    """Opcjonalny adapter Morfeusz2 dla istniejącego silnika lematyzacji."""
    name = "optional_morfeusz2_pl"
    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self._morfeusz = None
        self.available = False
        if enabled:
            self._try_init()
    def _try_init(self) -> None:
        try:
            import morfeusz2  # type: ignore
            self._morfeusz = morfeusz2.Morfeusz()
            self.available = True
        except Exception:
            self._morfeusz = None
            self.available = False
    def analyse_token(self, token: str, *, folded: str, context: str = "") -> list[ProviderLemmaCandidate]:
        if not self.available or self._morfeusz is None or not token.strip():
            return []
        out: list[ProviderLemmaCandidate] = []
        try:
            for _start, _end, interp in self._morfeusz.analyse(token):
                if not interp or len(interp) < 3: continue
                lemma = str(interp[1]).split(":", 1)[0].lower()
                tag = str(interp[2])
                if lemma:
                    out.append(ProviderLemmaCandidate(lemma=lemma, confidence=0.82, provider=self.name, pos=tag.split(":",1)[0], morph={"tag":tag}, explanation="wynik opcjonalnego analizatora Morfeusz2"))
        except Exception:
            return []
        best: dict[str, ProviderLemmaCandidate] = {}
        for c in out:
            if c.lemma not in best: best[c.lemma]=c
        return list(best.values())[:5]

class OptionalMorfeuszProvider:
    """Lekki status/lookup provider używany przez ExternalDictionaryAdapter."""
    name='morfeusz_optional'
    def __init__(self):
        try:
            import morfeusz2  # type: ignore
            self._morfeusz=morfeusz2.Morfeusz()
            self.available=True
            self.error=None
        except Exception as exc:
            self._morfeusz=None; self.available=False; self.error=repr(exc)
    def lookup(self, term: str, language: str='pl') -> ProviderLookupResult:
        if language != 'pl':
            return ProviderLookupResult(self.name,'language_not_supported',term,language,error='Morfeusz provider supports Polish only.')
        if not self.available or self._morfeusz is None:
            return ProviderLookupResult(self.name,'provider_unavailable',term,language,error=self.error,retrieved_at_utc=datetime.now(timezone.utc).isoformat())
        lemmas=[]; poses=[]; raw=[]
        try:
            for item in self._morfeusz.analyse(term):
                raw.append(repr(item))
                interp=item[2] if len(item) > 2 else ()
                if len(interp) >= 3:
                    lemmas.append(str(interp[1]).split(':')[0])
                    poses.append(str(interp[2]).split(':')[0])
        except Exception as exc:
            return ProviderLookupResult(self.name,'provider_error',term,language,error=repr(exc),retrieved_at_utc=datetime.now(timezone.utc).isoformat())
        return ProviderLookupResult(self.name,'ok' if lemmas else 'no_analysis',term,language,lemmas=sorted(set(lemmas)),part_of_speech=sorted(set(poses)),retrieved_at_utc=datetime.now(timezone.utc).isoformat(),confidence=0.80 if lemmas else 0.1,raw={'analysis':raw[:20]})
