from __future__ import annotations
SCHEMA_VERSION="dictionary_source_policy/v14.8.0"
DICTIONARY_SOURCE_POLICY = {
    'allow_network_default': True,
    'no_mass_scraping': True,
    'cache_required_for_online_lookup': True,
    'store_license_note': True,
    'store_source_url': True,
    'store_retrieved_at_utc': True,
    'timeout_required': True,
    'fallback_offline_required': True,
    'reference_only_sources_must_not_claim_definitions': True,
    'sjp_and_wsjp_are_reference_link_sources_by_default': True,
    'sources_priority': ['local_cache','local_jazn_mini_lexicon','morfeusz_optional','wiktionary_mediawiki_api','sjp_reference','wsjp_reference','plwordnet_optional','languagetool_optional'],
}
