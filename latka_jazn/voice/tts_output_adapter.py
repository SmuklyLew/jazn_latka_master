from __future__ import annotations
class TTSOutputAdapter:
    name="tts_output_adapter"
    configured=False
    def synthesize(self, text: str):
        return {"status":"not_configured","audio":None,"truth_boundary":"TTS is future architecture in this package unless configured externally."}
