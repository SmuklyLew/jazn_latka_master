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


_TERM_PATTERNS: dict[str, re.Pattern[str]] = {
    "lek": re.compile(r"\blek(?:i|u|iem|owi|ów|ow|om|ami|ach)?\b", re.IGNORECASE),
    "sen": re.compile(r"\bsen(?:u|em|owi|ow|om|ami|ach|y)?\b"),
    "ai": re.compile(r"\bai\b"),
    "api": re.compile(r"\bapi\b"),
    "log": re.compile(r"\blog(?:i|u|iem|ow|ami|ach)?\b"),
}


def _term_matches(folded: str, term: str, original: str | None = None) -> bool:
    pattern = _TERM_PATTERNS.get(term)
    if pattern is not None:
        target = (original or folded) if term == "lek" else folded
        return bool(pattern.search(target))
    folded_term = _fold(term)
    if " " in folded_term:
        return folded_term in folded
    return bool(re.search(rf"(?<!\w){re.escape(folded_term)}\w*", folded))


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
    """Deterministyczna, wieloetykietowa i audytowalna klasyfikacja rozmowy.

    Treść wiadomości jest źródłem głównym. Tytuł działa wyłącznie jako słaby
    kontekst, dzięki czemu historyczny tytuł rozmowy nie nadpisuje późniejszej
    zmiany tematu. Wynik służy do segmentacji i kontroli importu, nigdy do
    automatycznej promocji pamięci.
    """

    DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
        "development": (
            "python", "kod", "modul", "funkcj", "klasa", "test", "pytest", "patch",
            "commit", "branch", "github", "runtime", "sqlite", "manifest", "daemon",
            "api", "bug", "blad", "hotfix", "refactor", "repozytor", "importer",
        ),
        "daily_life": (
            "dzisiaj", "wczoraj", "jutro", "praca", "dom", "ogrod", "spacer", "sen",
            "obiad", "rano", "poranek", "wieczor", "noc", "autobus", "wyjazd",
            "wakacje", "codzien",
        ),
        "relationship": (
            "krzysztof", "kasia", "katarzyna", "razem", "teskn", "blisko", "relacj",
            "tayfa", "aures", "psotka", "fiona", "rodzina", "wiez", "intymn", "zaufan",
        ),
        "health": (
            "migren", "padacz", "aura", "lek", "bol", "zdrow", "bezsenn", "zmeczon",
            "lekarz", "napad", "samopoczuc",
        ),
        "book": (
            "ksiazk", "rozdzial", "manuskrypt", "redakc", "narrac", "bohater",
            "witaj w podrozy jazni", "tekst ksiazki", "scena ksiazk", "fabula",
        ),
        "creative_imagination": (
            "wyobraz", "symbol", "sen", "fantaz", "fikcj", "scena", "odegraj",
            "wciel", "rola", "roleplay", "co by bylo gdyby",
        ),
        "music": (
            "muzyk", "piosenk", "utwor", "melodi", "rytm", "refren", "zwrotk",
            "suno", "bpm", "akord", "tekst piosenki", "reggae",
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
            "jazn", "latka", "tozsamosc", "pamiec latki", "swiadomosc", "autonomia",
            "obudz", "kim jestes", "granica prawdy", "runtime jazni",
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

    @staticmethod
    def _apply_terms(
        folded: str,
        original: str,
        rules: dict[str, tuple[str, ...]],
        scores: dict[str, float],
        evidence: list[str],
        *,
        prefix: str,
        base: float,
        per_hit: float,
        cap: float,
    ) -> None:
        for label, terms in rules.items():
            hits = [term for term in terms if _term_matches(folded, term, original)]
            if hits:
                scores[label] += min(cap, base + per_hit * len(hits))
                evidence.extend(f"{prefix}:{label}:{term}" for term in hits[:4])

    def classify(
        self,
        text: str,
        *,
        role: str = "user",
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationDomainReport:
        text_original = (text or "").lower()
        title_original = (title or "").lower()
        text_folded = _fold(text_original)
        title_folded = _fold(title_original)
        combined = " ".join(part for part in (title_folded, text_folded) if part)
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

        self._apply_terms(
            text_folded, text_original, self.DOMAIN_TERMS, scores, evidence,
            prefix="text", base=0.18, per_hit=0.16, cap=1.0,
        )
        if title_folded:
            self._apply_terms(
                title_folded, title_original, self.DOMAIN_TERMS, scores, evidence,
                prefix="title", base=0.04, per_hit=0.04, cap=0.20,
            )

        if scores["book"] and scores["creative_imagination"]:
            explicit_book_context = any(
                marker in combined
                for marker in ("ksiazk", "rozdzial", "manuskrypt", "witaj w podrozy jazni")
            )
            scores["book"] += 0.32 if explicit_book_context else 0.08
        if scores["development"] and scores["system_identity"]:
            scores["development"] += 0.10
        if not any(value > 0.0 for key, value in scores.items() if key != "unknown"):
            scores["unknown"] = 0.35

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        primary_domain, primary_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        secondary = [key for key, score in ranked[1:] if score >= max(0.30, primary_score - 0.22)][:3]

        mode_scores = {key: 0.0 for key in MODE_KEYS}
        if role_folded in {"system", "tool"}:
            mode_scores["system_event"] = 1.0
        self._apply_terms(
            text_folded, text_original, self.MODE_TERMS, mode_scores, evidence,
            prefix="mode_text", base=0.20, per_hit=0.18, cap=1.0,
        )
        if title_folded:
            self._apply_terms(
                title_folded, title_original, self.MODE_TERMS, mode_scores, evidence,
                prefix="mode_title", base=0.03, per_hit=0.03, cap=0.15,
            )

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

        margin = max(0.0, primary_score - second_score)
        if second_score > 0.0 and margin < 0.08:
            evidence.append("review:domain_tie")
        confidence = round(
            min(0.98, max(0.20, 0.34 + primary_score * 0.42 + min(0.16, margin * 0.4))),
            3,
        )
        return ConversationDomainReport(
            primary_domain=primary_domain,
            secondary_domains=secondary,
            mode=mode,
            confidence=confidence,
            evidence=sorted(set(evidence))[:32],
            scores={key: round(value, 3) for key, value in scores.items() if value > 0.0},
        )
