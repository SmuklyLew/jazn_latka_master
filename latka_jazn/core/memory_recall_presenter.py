from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
import re


@dataclass(slots=True)
class MemoryRecallItem:
    """Treściowy trop pamięci przekazywany do warstwy odpowiedzi.

    v14.6.3 naprawia błąd, w którym runtime znał listę epizodów
    i legacy_messages, ale widoczna odpowiedź dostawała głównie liczniki.
    Ten obiekt jest mały, jawny i bezpieczny do włączenia w cognitive-frame
    albo ConversationDecision: zawiera treść, źródło, czas, typ, pewność
    i prostą ocenę trafności/znaczenia. v14.6.5 dodaje też tropy z plików kanonicznych planera pamięci.
    """

    item_type: str
    query_term: str | None
    timestamp: str | None
    source: str | None
    confidence: float | None
    grounding: str | None
    relevance_score: float
    relevance_label: str
    meaning_assessment: str
    content_excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryRecallPresenter:
    """Buduje i renderuje treściowe przypomnienie zamiast samych `counts`.

    Zasada projektowa: licznik jest diagnostyką, nie pamięcią. Odpowiedź
    rozmowna ma dostać realne fragmenty wspomnień oraz ocenę, czy są
    znaczeniowo trafne, czy jedynie przypadkowo/leksykalnie znalezione.
    """

    TECHNICAL_MARKERS = (
        "def ", "class ", "import ", "sqlite", "traceback", "pytest", "manifest",
        "update_report", "patch", "runtime", "fallback", "zip", "github", "jsonl",
        "main.py", "engine.py", "conversation.py", "legacy_messages", "episodes=",
    )
    TECHNICAL_QUERY_MARKERS = (
        "system", "runtime", "plik", "kod", "patch", "aktualizac", "wersj", "zip",
        "test", "debug", "fallback", "github", "sqlite", "manifest", "jaźni", "jazni",
    )

    def build_items(self, memory_context: dict[str, Any] | None, *, user_text: str = "", limit: int = 6) -> list[MemoryRecallItem]:
        if not isinstance(memory_context, dict):
            return []
        terms = [str(x) for x in (memory_context.get("query_terms") or []) if str(x).strip()]
        items: list[MemoryRecallItem] = []

        for ep in memory_context.get("episodes") or []:
            if not isinstance(ep, dict):
                continue
            content = self._clean(ep.get("scene"))
            if not content:
                continue
            confidence = self._float_or_none(ep.get("confidence"))
            score, label, assessment = self._assess(content, terms, user_text, confidence=confidence)
            items.append(MemoryRecallItem(
                item_type="episode",
                query_term=self._first_matching_term(content, terms) or ep.get("phrase"),
                timestamp=self._clean(ep.get("local_time_label")),
                source=self._clean(ep.get("source")) or "memory/layered",
                confidence=confidence,
                grounding=self._clean(ep.get("grounding")),
                relevance_score=score,
                relevance_label=label,
                meaning_assessment=assessment,
                content_excerpt=self._excerpt(content),
            ))

        for row in memory_context.get("legacy_messages") or []:
            if not isinstance(row, dict):
                continue
            content = self._clean(row.get("text"))
            if not content:
                continue
            score, label, assessment = self._assess(content, terms, user_text, confidence=None)
            title = self._clean(row.get("conversation_title"))
            role = self._clean(row.get("author_role"))
            source = "chat.html"
            if title or role:
                source = f"chat.html / {title or 'bez tytułu'} / {role or 'unknown'}"
            items.append(MemoryRecallItem(
                item_type="legacy_message",
                query_term=self._first_matching_term(content, terms) or row.get("phrase"),
                timestamp=self._clean(row.get("create_time_warsaw")),
                source=source,
                confidence=None,
                grounding="legacy_import_index",
                relevance_score=score,
                relevance_label=label,
                meaning_assessment=assessment,
                content_excerpt=self._excerpt(content),
            ))


        for hit in memory_context.get("source_file_hits") or []:
            if not isinstance(hit, dict):
                continue
            content = self._clean(hit.get("content_excerpt"))
            if not content:
                continue
            base_score, label, assessment = self._assess(content, terms, user_text, confidence=None)
            planner_score = self._float_or_none(hit.get("score"))
            score = max(base_score, planner_score or 0.0)
            if score >= 0.62:
                label = "wysoka"
            elif score >= 0.43:
                label = "średnia"
            else:
                label = "słaba"
            if hit.get("source_label") == "canonical_source_file":
                assessment = "kanoniczny plik źródłowy wskazany przez planer pamięci; dobry trop do odpowiedzi, ale nadal trzeba zachować granicę prawdy"
            items.append(MemoryRecallItem(
                item_type="source_file",
                query_term=self._clean(hit.get("term")) or self._first_matching_term(content, terms),
                timestamp=None,
                source=self._clean(hit.get("path")) or "canonical_source_file",
                confidence=None,
                grounding=self._clean(hit.get("source_label")) or "memory_search_planner",
                relevance_score=score,
                relevance_label=label,
                meaning_assessment=assessment,
                content_excerpt=self._excerpt(content, max_len=420),
            ))

        for archive_hit in memory_context.get("conversation_archive_hits") or []:
            if not isinstance(archive_hit, dict):
                continue
            content = self._clean(archive_hit.get("excerpt") or archive_hit.get("text"))
            if not content:
                continue
            confidence = self._float_or_none(archive_hit.get("identity_confidence"))
            score, label, assessment = self._assess(content, terms, user_text, confidence=confidence)
            # bm25 rank jest zwykle mniejsze dla lepszych wyników; traktujemy je tylko jako tie-breaker,
            # a nie zamiennik oceny znaczeniowej.
            if archive_hit.get("grounding") == "conversation_archive_v1+fts_v1":
                assessment = "treściowy fragment z conversation_archive/FTS; dobry trop pamięciowy, jeśli odpowiada pytaniu i zachowuje granicę prawdy"
            source_parts = [self._clean(archive_hit.get("source_name")), self._clean(archive_hit.get("source_locator"))]
            source = " / ".join(x for x in source_parts if x) or "conversation_archive_v1"
            title = self._clean(archive_hit.get("conversation_title"))
            role = self._clean(archive_hit.get("author_role"))
            if title or role:
                source = f"{source} / {title or 'bez tytułu'} / {role or 'unknown'}"
            items.append(MemoryRecallItem(
                item_type="conversation_archive",
                query_term=self._first_matching_term(content, terms) or archive_hit.get("phrase"),
                timestamp=self._clean(archive_hit.get("create_time_warsaw")),
                source=source,
                confidence=confidence,
                grounding="conversation_archive_v1+fts_v1",
                relevance_score=score,
                relevance_label=label,
                meaning_assessment=assessment,
                content_excerpt=self._excerpt(content, max_len=460),
            ))

        for raw in memory_context.get("raw_chat_fallback") or []:
            if not isinstance(raw, dict):
                continue
            content = self._clean(raw.get("snippet"))
            if not content:
                continue
            score, label, assessment = self._assess(content, terms, user_text, confidence=None, raw=True)
            items.append(MemoryRecallItem(
                item_type="raw_chat_fallback",
                query_term=self._clean(raw.get("term")) or self._first_matching_term(content, terms),
                timestamp=None,
                source="memory/raw/chat.html fallback scan",
                confidence=None,
                grounding="raw_text_scan_not_full_index",
                relevance_score=score,
                relevance_label=label,
                meaning_assessment=assessment,
                content_excerpt=self._excerpt(content, max_len=360),
            ))

        # Kolejność: najpierw trafność znaczeniowa, potem epizody przed legacy,
        # przy zachowaniu stabilnej kolejności dla równych wyników.
        type_bonus = {"episode": 0.04, "conversation_archive": 0.038, "source_file": 0.035, "legacy_message": 0.02, "raw_chat_fallback": 0.0}
        items.sort(key=lambda x: (x.relevance_score + type_bonus.get(x.item_type, 0.0)), reverse=True)
        return items[:limit]

    def build_payload(self, memory_context: dict[str, Any] | None, *, user_text: str = "", limit: int = 6) -> dict[str, Any]:
        counts = (memory_context or {}).get("counts") if isinstance(memory_context, dict) else {}
        items = self.build_items(memory_context, user_text=user_text, limit=limit)
        return {
            "schema_version": "memory_recall_content/v14.6.10",
            "query_terms": (memory_context or {}).get("query_terms") if isinstance(memory_context, dict) else [],
            "memory_search_plan": (memory_context or {}).get("memory_search_plan") if isinstance(memory_context, dict) else None,
            "counts": counts or {},
            "items": [i.to_dict() for i in items],
            "summary": self.summary(items, counts or {}),
            "rule": "planer pamięci najpierw oczyszcza i rozszerza zapytanie; liczniki są diagnostyką; odpowiedź pamięciowa musi używać treści, źródła, czasu, typu, pewności i oceny trafności",
        }

    def render(self, memory_context: dict[str, Any] | None, *, user_text: str = "", limit: int = 6) -> str:
        payload = self.build_payload(memory_context, user_text=user_text, limit=limit)
        items = payload["items"]
        counts = payload["counts"]
        terms = payload.get("query_terms") or []
        counts_note = self._counts_text(counts)
        if not items:
            return (
                f"Szukałam treściowych tropów pamięci po hasłach: {', '.join(map(str, terms)) or 'brak haseł'}. "
                f"Nie znalazłam fragmentów, które mogłabym uczciwie przywołać jako treść wspomnienia. {counts_note} "
                "W tej sytuacji nie wolno mi udawać przypomnienia tylko dlatego, że istnieje licznik albo indeks."
            ).strip()

        lines = [
            f"Znalazłam treściowe tropy pamięci po hasłach: {', '.join(map(str, terms)) or 'brak haseł'}. {counts_note}".strip(),
            "Najważniejsze ślady, już z treścią i oceną trafności:",
        ]
        for idx, item in enumerate(items, start=1):
            conf = f", pewność={item['confidence']:.2f}" if isinstance(item.get("confidence"), float) else ""
            timestamp = item.get("timestamp") or "czas nieustalony"
            source = item.get("source") or "źródło nieustalone"
            term = item.get("query_term") or "bez osobnego hasła"
            lines.append(
                f"{idx}. [{item['item_type']} / {term}] {timestamp} / {source}{conf} / "
                f"trafność: {item['relevance_label']} ({item['relevance_score']:.2f}). "
                f"Ocena: {item['meaning_assessment']}. Fragment: „{item['content_excerpt']}”"
            )
        lines.append("Wniosek: liczby zostają tylko diagnostyką; właściwe przypomnienie musi pokazać, co zostało znalezione i czy ma sens dla pytania.")
        return "\n".join(lines)

    def summary(self, items: list[MemoryRecallItem], counts: dict[str, Any]) -> str:
        strong = sum(1 for i in items if i.relevance_label == "wysoka")
        medium = sum(1 for i in items if i.relevance_label == "średnia")
        weak = sum(1 for i in items if i.relevance_label == "słaba")
        return f"treściowe_tropy={len(items)}, wysoka={strong}, średnia={medium}, słaba={weak}, counts={counts}"

    @classmethod
    def _assess(
        cls,
        content: str,
        terms: list[str],
        user_text: str,
        *,
        confidence: float | None,
        raw: bool = False,
    ) -> tuple[float, str, str]:
        low = content.lower()
        user_low = (user_text or "").lower()
        technical_query = any(m in user_low for m in cls.TECHNICAL_QUERY_MARKERS)
        norm_low = cls._norm_text(content)
        matched = sum(1 for t in terms if t and (t.lower() in low or cls._norm_text(t) in norm_low))
        score = 0.34 + min(0.28, matched * 0.08)
        if confidence is not None:
            score += max(0.0, min(0.2, confidence * 0.2))
        if raw:
            score -= 0.08
        is_technical = any(m in low for m in cls.TECHNICAL_MARKERS)
        if is_technical and not technical_query:
            score -= 0.18
        if content.strip() and user_text.strip() and cls._similar_prefix(content, user_text):
            score -= 0.1
        score = max(0.0, min(1.0, score))
        if score >= 0.62:
            label = "wysoka"
        elif score >= 0.43:
            label = "średnia"
        else:
            label = "słaba"
        if is_technical and not technical_query:
            assessment = "raczej techniczny albo przypadkowy ślad; wolno go pokazać, ale nie należy udawać osobistego wspomnienia"
        elif raw:
            assessment = "awaryjny fragment z surowego chat.html; wymaga ostrożności, bo nie pochodzi z pełnego indeksu"
        elif label == "wysoka":
            assessment = "znaczeniowo użyteczny trop pamięci, nadaje się do przywołania w odpowiedzi"
        elif label == "średnia":
            assessment = "częściowo użyteczny trop; pokazuje kierunek, ale wymaga granicy prawdy"
        else:
            assessment = "słabe dopasowanie leksykalne; traktować pomocniczo, nie jako główne wspomnienie"
        return score, label, assessment

    @staticmethod
    def _norm_text(text: str) -> str:
        import unicodedata
        text = (text or "").lower()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.replace("ł", "l")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _similar_prefix(content: str, user_text: str) -> bool:
        a = re.sub(r"\s+", " ", content.strip().lower())[:120]
        b = re.sub(r"\s+", " ", user_text.strip().lower())[:120]
        return bool(a and b and (a.startswith(b[:40]) or b.startswith(a[:40])))

    @staticmethod
    def _first_matching_term(content: str, terms: list[str]) -> str | None:
        low = content.lower()
        for term in terms:
            if term and term.lower() in low:
                return term
        return None

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = re.sub(r"\s+", " ", str(value)).strip()
        return text or None

    @classmethod
    def _excerpt(cls, value: str, max_len: int = 320) -> str:
        text = cls._redact_sensitive_text(re.sub(r"\s+", " ", value).strip())
        if len(text) <= max_len:
            return text
        return text[: max_len - 1].rstrip() + "…"

    @staticmethod
    def _redact_sensitive_text(text: str) -> str:
        # Pamięć legacy może zawierać prywatne albo medyczne dane z importów.
        # Warstwa przypominania ma pokazywać sens wspomnienia, nie ujawniać PESEL-i,
        # telefonów, maili ani treści klinicznych znalezionych przypadkowym trafieniem.
        if re.search(r"PESEL|dane kliniczne|pacjent|uraz|diagnoz|badanie kliniczne|charakter urazu", text, flags=re.IGNORECASE):
            return "[FRAGMENT ZAWIERA DANE WRAŻLIWE LUB MEDYCZNE — UKRYTY W ODPOWIEDZI]"
        text = re.sub(r"(?<!\d)\d{11}(?!\d)", "[PESEL/DANE_WRAŻLIWE_UKRYTE]", text)
        text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL_UKRYTY]", text)
        text = re.sub(r"(?<!\d)(?:\+?48[ -]?)?(?:\d[ -]?){9}(?!\d)", "[TELEFON_UKRYTY]", text)
        return text

    @staticmethod
    def _counts_text(counts: dict[str, Any]) -> str:
        if not counts:
            return "Liczniki diagnostyczne: brak."
        parts = []
        for key in ("episodes", "legacy_messages", "source_file_hits", "raw_chat_fallback"):
            val = counts.get(key)
            if isinstance(val, int):
                parts.append(f"{key}={val}")
        return "Liczniki diagnostyczne: " + (", ".join(parts) if parts else str(counts)) + "."
