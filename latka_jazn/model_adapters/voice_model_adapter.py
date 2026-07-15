from __future__ import annotations
from .base import ModelAdapterRequest, ModelAdapterResponse

class VoiceModelAdapter:
    name='voice_model_adapter'
    def generate(self, request: ModelAdapterRequest) -> ModelAdapterResponse:
        return ModelAdapterResponse(text='', provider=self.name, model='not_configured', status='not_configured')
