from __future__ import annotations
from .base import ModelAdapterRequest, ModelAdapterResponse

class ExternalLlmAdapter:
    name='external_llm_adapter'
    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        return ModelAdapterResponse(text='', provider=self.name, model='not_configured', status='not_configured')
