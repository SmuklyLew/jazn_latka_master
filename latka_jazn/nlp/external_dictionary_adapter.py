from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import re
from latka_jazn.nlp.dictionary_entry import DictionaryLookupResult, NormalizedLexeme, SemanticRelations, LexicalSource
from latka_jazn.nlp.polish_lexical_sources import MINI_LEXICON
from latka_jazn.nlp.network_dictionary_cache import NetworkDictionaryCache
from latka_jazn.nlp.lexical_license_guard import LexicalLicenseGuard
from latka_jazn.nlp.providers.mediawiki_wiktionary_provider import MediaWikiWiktionaryProvider
from latka_jazn.nlp.providers.optional_morfeusz_provider import OptionalMorfeuszProvider
from latka_jazn.nlp.providers.languagetool_http_provider import LanguageToolHttpProvider
from latka_jazn.nlp.providers.plwordnet_optional_provider import PlWordNetOptionalProvider
from latka_jazn.nlp.providers.wsjp_reference_provider import WSJPReferenceProvider
from latka_jazn.nlp.providers.sjp_reference_provider import SJPReferenceProvider

class ExternalDictionaryAdapter:
    """Kontrolowany adapter słownikowy v14.8.0.

    Domyślnie `allow_network=True`, ale każdy provider zwraca jawny status,
    timeout, źródło, licencję/cache i granicę prawdy. Brak internetu nie jest
    udawany jako sprawdzenie online.
    """
    def __init__(self, root: Path, *, allow_network: bool = True, user_agent: str = 'LatkaJazn/14.8.0 (+local-runtime; network-nlp-sjp-lexical-bridge)', timeout_seconds: float = 4.0, max_retries: int = 0, cache_ttl_seconds: int = 604800) -> None:
        self.root=Path(root); self.allow_network=bool(allow_network); self.user_agent=user_agent; self.timeout_seconds=float(timeout_seconds); self.max_retries=int(max_retries)
        self.cache=NetworkDictionaryCache(root, ttl_seconds=cache_ttl_seconds); self.license_guard=LexicalLicenseGuard()
        self.morfeusz=OptionalMorfeuszProvider()
        self.wiktionary=MediaWikiWiktionaryProvider(allow_network=self.allow_network, user_agent=self.user_agent, timeout_seconds=self.timeout_seconds, max_retries=self.max_retries)
        self.languagetool=LanguageToolHttpProvider(allow_network=False, user_agent=self.user_agent, timeout_seconds=min(self.timeout_seconds,3.0))
        self.plwordnet=PlWordNetOptionalProvider(self.root)
        self.sjp=SJPReferenceProvider()
        self.wsjp=WSJPReferenceProvider()

    def close(self) -> None:
        """Release provider/cache resources explicitly.

        This is important on Windows, where open SQLite file handles prevent
        TemporaryDirectory cleanup during tests.
        """
        for obj in (getattr(self, "cache", None),):
            close = getattr(obj, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _norm(term: str) -> str:
        return re.sub(r'\s+', ' ', (term or '').strip().lower())
    @staticmethod
    def _merge_unique(target: list[str], values: list[str]):
        seen={x.lower() for x in target}
        for v in values or []:
            if v and v.lower() not in seen:
                target.append(v); seen.add(v.lower())
    def _result_from_cache(self, data: dict[str,Any], term: str, normalized: str, lang: str) -> DictionaryLookupResult:
        allowed=set(DictionaryLookupResult.__dataclass_fields__.keys())
        data={k:v for k,v in data.items() if k in allowed}
        data.setdefault('term', term); data.setdefault('normalized_term', normalized); data.setdefault('language', lang); data['cache_status']='hit'
        return DictionaryLookupResult(**data)
    def lookup(self, term: str, lang: str = 'pl', pos: str | None = None) -> DictionaryLookupResult:
        normalized=self._norm(term)
        now=datetime.now(timezone.utc).isoformat()
        cached=self.cache.get_any(normalized, lang, preferred_sources=('composite_network_dictionary','local_jazn_mini_lexicon'))
        if cached:
            return self._result_from_cache(cached, term, normalized, lang)
        definitions=[]; lemmas=[]; pos_candidates=[]; forms=[]; examples=[]; sources=[]; statuses=[]; errors=[]; semantic=[]; suggestions=[]
        confidence=0.0; source_name='not_found'; license_note=None; source_url=None
        if normalized in MINI_LEXICON:
            item=MINI_LEXICON[normalized]
            self._merge_unique(lemmas, item.get('lemma', [])); self._merge_unique(definitions, item.get('definitions', []))
            source_name=item.get('source','local_jazn_mini_lexicon'); license_note='local project mini lexicon'; confidence=max(confidence,0.70)
            sources.append(LexicalSource(source_name, None, license_note, now, 'miss_then_local', 0.70).to_dict())
            statuses.append({'provider':'local_jazn_mini_lexicon','status':'ok'})
        # Optional morphological provider: safe when unavailable.
        mor=self.morfeusz.lookup(normalized, lang)
        statuses.append({'provider':mor.provider,'status':mor.status,'message':mor.error})
        self._merge_unique(lemmas, mor.lemmas); self._merge_unique(pos_candidates, mor.part_of_speech)
        if mor.status == 'ok': confidence=max(confidence,mor.confidence)
        if self.allow_network:
            wiki=self.wiktionary.lookup(normalized, lang)
            statuses.append({'provider':wiki.provider,'status':wiki.status,'source_url':wiki.source_url,'elapsed_ms':wiki.elapsed_ms,'message':wiki.error})
            if wiki.status in {'ok','page_found_no_short_definition'}:
                self._merge_unique(definitions, wiki.definitions); self._merge_unique(lemmas, wiki.lemmas)
                source_name=wiki.provider; source_url=wiki.source_url; license_note=wiki.license_hint; confidence=max(confidence,wiki.confidence)
                sources.append(LexicalSource(wiki.provider,wiki.source_url,wiki.license_hint,wiki.retrieved_at_utc,'miss_then_network',wiki.confidence,wiki.truth_boundary).to_dict())
            elif wiki.error:
                errors.append({'provider':wiki.provider,'status':wiki.status,'error':wiki.error})
            # Optional/reference providers that do not scrape or download heavy resources.
            sjp=self.sjp.lookup(normalized, lang); statuses.append({'provider':sjp.provider,'status':sjp.status,'source_url':sjp.source_url})
            sources.append(LexicalSource(sjp.provider, sjp.source_url, sjp.license_hint, sjp.retrieved_at_utc, 'reference_link_only', sjp.confidence, sjp.truth_boundary).to_dict())
            wsjp=self.wsjp.lookup(normalized, lang); statuses.append({'provider':wsjp.provider,'status':wsjp.status,'source_url':wsjp.source_url})
            sources.append(LexicalSource(wsjp.provider, wsjp.source_url, wsjp.license_hint, wsjp.retrieved_at_utc, 'reference_link_only', wsjp.confidence, wsjp.truth_boundary).to_dict())
            plwn=self.plwordnet.lookup(normalized, lang); statuses.append({'provider':plwn.provider,'status':plwn.status,'message':plwn.error})
        else:
            statuses.append({'provider':'wiktionary_mediawiki_api','status':'network_disabled','message':'allow_network=False'})
            sjp=self.sjp.lookup(normalized, lang); statuses.append({'provider':sjp.provider,'status':sjp.status,'source_url':sjp.source_url})
            sources.append(LexicalSource(sjp.provider, sjp.source_url, sjp.license_hint, sjp.retrieved_at_utc, 'reference_link_only_offline', sjp.confidence, sjp.truth_boundary).to_dict())
            wsjp=self.wsjp.lookup(normalized, lang); statuses.append({'provider':wsjp.provider,'status':wsjp.status,'source_url':wsjp.source_url})
            sources.append(LexicalSource(wsjp.provider, wsjp.source_url, wsjp.license_hint, wsjp.retrieved_at_utc, 'reference_link_only_offline', wsjp.confidence, wsjp.truth_boundary).to_dict())
        found=bool(definitions or lemmas or forms or semantic)
        cache_status='miss_then_network' if self.allow_network else 'miss_network_disabled'
        if not found and not self.allow_network:
            source_name='not_checked_online'
            errors.append({'provider':'external_dictionary_adapter','status':'network_disabled','error':'Nie wykonano lookupu online, bo allow_network=False.'})
        if not found and self.allow_network:
            cache_status='miss_not_found_or_provider_unavailable'
        truth_boundary = 'Słownik korzysta z cache, mini-leksykonu, providerów online i referencji SJP/WSJP tylko jawnie. Brak wyniku nie dowodzi, że słowo nie istnieje; link referencyjny nie jest definicją.'
        if not found and not self.allow_network:
            truth_boundary = 'Nie wykonano lookupu online, bo allow_network=False. Brak lokalnego wyniku nie dowodzi, że słowo nie istnieje.'
        result=DictionaryLookupResult(
            term=term, normalized_term=normalized, language=lang, lemma_candidates=lemmas, pos_candidates=pos_candidates,
            definitions=definitions, inflection=forms, examples=examples, source_name=source_name, source_url_or_id=source_url,
            retrieved_at_utc=now, license_note=license_note or self.license_guard.note_for(source_name), confidence=confidence,
            cache_status=cache_status, found=found, lemmas=lemmas, forms=forms, part_of_speech=pos_candidates,
            semantic_relations=semantic, spelling_suggestions=suggestions, sources=sources, provider_statuses=statuses, errors=errors,
            truth_boundary=truth_boundary
        )
        source_key='composite_network_dictionary' if self.allow_network else 'local_jazn_mini_lexicon'
        if found or self.allow_network:
            self.cache.put(normalized, lang, source_key, result.to_dict(), raw={'provider_statuses':statuses}, license_note=result.license_note or '', confidence=result.confidence)
        return result
    def normalize(self, term: str, lang: str = 'pl') -> NormalizedLexeme:
        return NormalizedLexeme(term=term, normalized=self._norm(term), language=lang, provider='external_dictionary_adapter/v14.8.0', confidence=0.60)
    def related_terms(self, term: str, relation: str | None = None) -> SemanticRelations:
        lookup=self.lookup(term)
        rel=[]
        for item in lookup.semantic_relations:
            if relation is None or item.get('relation') == relation:
                rel.extend(item.get('terms') or [])
        return SemanticRelations(term=term, relation=relation, related_terms=rel, source_name='external_dictionary_adapter', confidence=lookup.confidence)
