from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("conversation_domain_classification")

DOMAIN_KEYS = (
    "development",
    "daily_life",
    "relationship",
    "health",
    "book",
    "creative_imagination",
    "music",
    "image",
    "video",
    "reading",
    "advice",
    "system_identity",
    "system",
    "unknown",
)

MODE_KEYS = (
    "factual_conversation",
    "technical_work",
    "planning",
    "manuscript_draft",
    "scene_roleplay",
    "symbolic_imagination",
    "media_analysis",
    "media_reaction",
    "source_reading",
    "system_event",
    "unknown",
)


def _fold(text: str) -> str:
    text = (text or "").replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.lower()).strip()


@dataclass(slots=True, frozen=True)
class ConversationDomainReport:
    primary_domain: str
    secondary_domains: list[str]
    mode: str
    confidence: float
    evidence: list[str]
    scores: dict[str, float]
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = (
        "Klasyfikacja opisuje temat i tryb fragmentu rozmowy. Nie zmienia sama treści w fakt, "
        "wspomnienie, kanon książki ani przeżycie Łatki."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConversationDomainClassifier:
    """Deterministyczna, audytowalna klasyfikacja domeny i trybu fragmentu.

    Reguły są celowo jawne. Wynik służy do segregacji i podglądu importu;
    nie jest automatyczną decyzją o pamięci długotrwałej.
    """

    DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
        "development": (
            "python", "kod", "modul", "funkcj", "klasa", "test", "pytest", "patch",
            "commit", "branch", "github", "runtime", "sqlite", "manifest", "daemon",
            "api", "bug", "blad", "hotfix", "refactor", "repozytor",
        ),
        "daily_life": (
            "dzisiaj", "wczoraj", "jutro", "praca", "dom", "ogrod", "spacer", "sen",
            "obiad", "rano", "wieczor", "autobus", "wyjazd", "wakacje", "codzien",
        ),
        "relationship": (
            "krzysztof", "kasia", "katarzyna", "razem", "teskn", "blisko", "relacj",
            "tayfa", "aures", "psotka", "fiona", "rodzina",
        ),
        "health": (
            "migren", "padacz", "aura", "lek", "bol", "zdrow", "sen", "zmeczon",
            "lekarz", "napad", "samopoczuc",
        ),
        "book": (
            "ksiazk", "rozdzial", "manuskrypt", "redakc", "narrac", "bohater",
            "witaj w podrozy jazni", "tekst ksiazki", "scena ksiazk",
        ),
        "creative_imagination": (
            "wyobraz", "symbol", "sen", "fantaz", "fikcj", "scena", "odegraj",
            "wciel", "rola", "roleplay", "co by bylo gdyby",
        ),
        "music": (
            "muzyk", "piosenk", "utwor", "melodi", "rytm", "refren", "zwrotk",
            "suno", "bpm", "akord", "tekst piosenki",
        ),
        "image": (
            "obraz", "zdjec", "grafik", "ilustrac", "portret", "rysunek", "kadr",
            "kolor", "wygeneruj obraz",
        ),
        "video": (
            "film", "wideo", "video", "nagran", "klatka", "scena film", "montaz",
        ),
        "reading": (
            "czytam", "przeczyt", "ksiazka", "poradnik", "artykul", "dokument",
            "pdf", "rozdzial", "strona", "cytat", "zrodlo",
        ),
        "advice": (
            "poradz", "rada", "co zrobic", "jak zrobic", "pomoz", "wyjasnij",
            "instrukcja", "plan",
        ),
        "system_identity": (
            "jazn", "latka", "tozsamosc", "pamiec latki", "swiadomosc", "obudz",
            "kim jestes", "granica prawdy", "runtime jazni",
        ),
        "system": (
            "system", "narzedzie", "tool", "assistant", "developer", "system message",
            "telemetria", "log", "status",
        ),
    }

    MODE_TERMS: dict[str, tuple[str, ...]] = {
        "technical_work": (
            "napraw", "patch", "kod", "test", "commit", "branch", "sqlite", "runtime",
            "manifest", "refactor", "debug", "blad",
        ),
        "planning": (
            "plan", "zaplanuj", "kolejnosc", "etap", "harmonogram", "przygotuj plan",
        ),
        "scene_roleplay": (
            "odegraj", "odgryw", "wciel sie", "roleplay", "zagrajmy scene",
            "mow jako", "jestes teraz postacia", "dialog w rolach",
        ),
        "manuscript_draft": (
            "napisz scene", "tekst ksiazki", "rozdzial", "redaguj", "przeredaguj",
            "wersja sceny", "szkic", "manuskrypt",
        ),
        "symbolic_imagination": (
            "wyobraz", "symbolicz", "sen", "wizualiz", "co by bylo gdyby", "scena w wyobrazni",
        ),
        "media_analysis": (
            "analizuj", "zinterpretuj", "co widzisz", "co slyszysz", "tekst utworu",
            "analiza obrazu", "analiza filmu", "omow",
        ),
        "media_reaction": (
            "jak na ciebie dziala", "co czujesz sluchajac", "co czujesz ogladajac",
            "skojarzyl", "porusza", "reakcja na",
        ),
        "source_reading": (
            "przeczytaj", "czytaj", "zrodlo", "dokument", "pdf", "poradnik", "artykul",
        ),
    }

    def classify(
        self,
        text: str,
        *,
        role: str = "user",
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationDomainReport:
        folded = _fold(" ".join(part for part in (title or "", text or "") if part))
        metadata = metadata or {}
        scores = {key: 0.0 for key in DOMAIN_KEYS}
        evidence: list[str] = []

        role_folded = _fold(role)
        if role_folded in {"system", "tool"}:
            scores["system"] += 1.0
            evidence.append(f"role:{role_folded}")
        if metadata.get("content_type") in {"computer_initialize_state", "system_error", "tool_result"}:
            scores["system"] += 0.8
            evidence.append(f"content_type:{metadata.get('content_type')}")

        for domain, terms in self.DOMAIN_TERMS.items():
            hits = [term for term in terms if term in folded]
            if hits:
                scores[domain] += min(1.0, 0.18 + 0.16 * len(hits))
                evidence.extend(f"{domain}:{term}" for term in hits[:4])

        if scores["book"] and scores["creative_imagination"]:
            # A scene/roleplay remains in the book domain when the user or title
            # explicitly anchors it to a manuscript. The mode still records that
            # it is roleplay or symbolic work, so it cannot become a physical fact.
            explicit_book_context = any(
                marker in folded
                for marker in ("ksiazk", "rozdzial", "manuskrypt", "witaj w podrozy jazni")
            )
            scores["book"] += 0.42 if explicit_book_context else 0.12
        if scores["development"] and scores["system_identity"]:
            scores["development"] += 0.10
        if not any(value > 0.0 for key, value in scores.items() if key != "unknown"):
            scores["unknown"] = 0.35

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        primary_domain, primary_score = ranked[0]
        secondary = [key for key, score in ranked[1:] if score >= max(0.30, primary_score - 0.22)][:3]

        mode_scores = {key: 0.0 for key in MODE_KEYS}
        if role_folded in {"system", "tool"}:
            mode_scores["system_event"] = 1.0
        for mode, terms in self.MODE_TERMS.items():
            hits = [term for term in terms if term in folded]
            if hits:
                mode_scores[mode] += min(1.0, 0.20 + 0.18 * len(hits))
                evidence.extend(f"mode:{mode}:{term}" for term in hits[:3])

        if mode_scores["scene_roleplay"] >= 0.38:
            mode = "scene_roleplay"
        elif mode_scores["manuscript_draft"] >= 0.38:
            mode = "manuscript_draft"
        else:
            mode, mode_score = max(mode_scores.items(), key=lambda item: (item[1], item[0]))
            if mode_score <= 0.0:
                if primary_domain in {"development", "system_identity", "system"}:
                    mode = "technical_work" if primary_domain == "development" else "factual_conversation"
                elif primary_domain in {"music", "image", "video"}:
                    mode = "media_analysis"
                elif primary_domain == "reading":
                    mode = "source_reading"
                else:
                    mode = "factual_conversation"

        confidence = round(min(0.98, max(0.20, 0.40 + primary_score * 0.45)), 3)
        return ConversationDomainReport(
            primary_domain=primary_domain,
            secondary_domains=secondary,
            mode=mode,
            confidence=confidence,
            evidence=sorted(set(evidence))[:24],
            scores={key: round(value, 3) for key, value in scores.items() if value > 0.0},
        )
