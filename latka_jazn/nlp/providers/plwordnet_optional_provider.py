from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from latka_jazn.nlp.providers.base import ProviderLookupResult

class PlWordNetOptionalProvider:
    name='plwordnet_optional'
    def __init__(self, root: Path):
        self.root=Path(root)
        self.resource_dir=self.root/'resources'/'plwordnet'
    def lookup(self, term: str, language: str='pl') -> ProviderLookupResult:
        if language != 'pl':
            return ProviderLookupResult(self.name,'language_not_supported',term,language,error='plWordNet provider supports Polish resources.')
        if not self.resource_dir.exists():
            return ProviderLookupResult(self.name,'provider_unavailable',term,language,error='No local plWordNet resource directory; Jaźń does not download large resources at startup.',retrieved_at_utc=datetime.now(timezone.utc).isoformat(),license_hint='Check plWordNet/Słowosieć license before distributing imported data.')
        return ProviderLookupResult(self.name,'resource_present_not_indexed',term,language,error='Local resource is present but no index file has been configured yet.',retrieved_at_utc=datetime.now(timezone.utc).isoformat())
