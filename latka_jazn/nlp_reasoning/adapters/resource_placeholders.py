from __future__ import annotations

from latka_jazn.nlp_reasoning.models import ProviderStatus


def optional_provider_statuses() -> list[ProviderStatus]:
    return [
        ProviderStatus("plwordnet", False, "offline_optional_or_online", "large semantic graph; manual license review and download required", license="plWordNet/CLARIN metadata", source_url="https://clarin-pl.eu/dspace/handle/11321/554"),
        ProviderStatus("wsjp-pan", False, "online_lookup", "lookup provider only; no bulk mirror", license="IJP PAN / WSJP site terms", source_url="https://wsjp.pl/"),
        ProviderStatus("nkjp", False, "online_lookup", "corpus lookup/concordance only unless a licensed local subset is installed", license="NKJP terms", source_url="https://nkjp.pl/"),
        ProviderStatus("nkjp1m-sgjp", False, "offline_recommended", "HuggingFace dataset download required", license="CC BY 4.0", source_url="https://huggingface.co/datasets/ipipan/nkjp1m"),
        ProviderStatus("walenty", False, "offline_recommended", "download and parser required", license="CC BY-SA", source_url="https://zil.ipipan.waw.pl/Walenty"),
        ProviderStatus("spacy-pl_core_news_sm", False, "offline_optional", "python -m spacy download pl_core_news_sm", license="GPL-3.0 per model card", source_url="https://spacy.io/models/pl"),
        ProviderStatus("stanza-pl", False, "offline_optional", "python -c \"import stanza; stanza.download('pl')\"", license="library/model licenses; UD sources vary", source_url="https://stanfordnlp.github.io/stanza/"),
        ProviderStatus("herbert", False, "offline_optional", "transformers model download required", license="CC BY 4.0 model card", source_url="https://huggingface.co/allegro/herbert-base-cased"),
        ProviderStatus("pllum", False, "offline_optional_high_resource", "choose exact checkpoint/license locally", license="varies by PLLuM checkpoint", source_url="https://pllum.org.pl/"),
        ProviderStatus("bielik", False, "offline_optional_local_llm", "choose exact checkpoint/license locally", license="verify selected checkpoint", source_url="https://arxiv.org/abs/2505.02550"),
    ]
