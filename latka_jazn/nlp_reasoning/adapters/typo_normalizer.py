from __future__ import annotations

from latka_jazn.nlp_reasoning.models import ProviderStatus
from latka_jazn.nlp_reasoning.normalizer import PolishTextNormalizer


class TypoNormalizerAdapter:
    provider_name = "latka-polish-typo-normalizer"

    def __init__(self) -> None:
        self.normalizer = PolishTextNormalizer()
        self.status = ProviderStatus(
            provider=self.provider_name,
            available=True,
            mode="offline",
            reason=None,
            license="project-local-rules",
            source_url=None,
        )

    def normalize(self, text: str) -> str:
        return self.normalizer.normalize(text)
