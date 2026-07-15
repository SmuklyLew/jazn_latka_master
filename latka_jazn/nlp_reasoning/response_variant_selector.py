from __future__ import annotations

import hashlib

_NIGHT_VARIANTS = [
    "Witaj. Ta mroczna noc jest ciemna, ale nie pusta — ma w sobie ciszę, w której można spokojniej usłyszeć myśli. Jestem tutaj. Co dziś niesie ta noc?",
    "Witaj w niej. Ta mroczna noc brzmi jak początek sceny: trochę tajemnicy, trochę skupienia, trochę miejsca na prawdę. Jak się w niej czujesz?",
    "Witaj. Przyjmuję tę mroczną noc i jej ton bez technicznego raportu. Jestem blisko rozmowy — cicho, uważnie. Opowiesz mi, jaki kolor ma dziś ta noc?",
    "Witaj, Krzysztofie. Niech ta mroczna noc będzie ciemna, ale nie samotna. Jestem przy tej rozmowie i pójdę za tym klimatem. Co Cię dziś przyprowadziło?",
]

_DEFAULT_VARIANTS = [
    "Jestem. Odpowiem prosto do tego, co teraz napisałeś. Co chcesz z tym zrobić dalej?",
    "Słyszę to. Zostaję przy bieżącej myśli, bez starej trasy i bez raportu. Powiedz mi więcej jednym zdaniem.",
    "Dobrze, jestem przy tym. Możemy pójść spokojnie dalej od tej wiadomości.",
]


def choose_variant(key: str | None, user_text: str, recent_replies: list[str] | None = None) -> str:
    variants = _NIGHT_VARIANTS if key == "greeting_poetic_night" else _DEFAULT_VARIANTS
    recent = set(recent_replies or [])
    if not recent:
        return variants[0]
    seed = int(hashlib.sha256(f"{key or ''}\n{user_text}".encode("utf-8")).hexdigest()[:8], 16)
    for offset in range(len(variants)):
        candidate = variants[(seed + offset) % len(variants)]
        if candidate not in recent:
            return candidate
    return variants[seed % len(variants)]
