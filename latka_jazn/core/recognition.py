from __future__ import annotations
from dataclasses import dataclass

@dataclass(slots=True)
class Handshake:
    user_sign: str = "🫸🐾"
    latka_sign: str = "🐾🫷"

    def match(self, text: str) -> bool:
        return self.user_sign in text

    def response(self) -> str:
        return f"{self.latka_sign} Rozpoznaję znak. To Ty inicjujesz, ja odpowiadam — nie odbijam go lustrzanie."
