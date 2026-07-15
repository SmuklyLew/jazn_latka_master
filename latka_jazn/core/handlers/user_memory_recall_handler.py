from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

from latka_jazn.core.memory_recall_presenter import MemoryRecallPresenter
from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("user_memory_recall_handler")


class UserMemoryRecallHandler:
    name = "UserMemoryRecallHandler"
    route = "user_memory_recall"
    handled_intents = ("user_memory_recall_request",)

    USER_MARKERS = (
        "krzysztof", "użytkownik", "uzytkownik", "o mnie", "mnie", "moje", "moją", "moja",
        "moim", "moich", "smukły lew", "smukly lew", "kasia", "katarzyna", "praca",
        "spacer", "muzyka", "książka", "ksiazka", "ogród", "ogrod", "kot", "pies",
    )
    SELF_ONLY_MARKERS = (
        "o sobie", "swojej postaci", "swojej osobie", "łatce", "latce", "jaźni", "jazni",
        "tożsamość", "tozsamosc", "własny głos", "wlasny glos",
    )
    TECHNICAL_NOISE_MARKERS = (
        "traceback", "pytest", "sha256", "manifest", "patch", "runtime-preview", "schema_version",
        "git ", "commit", "zip", "main.py", "engine.py", "class ", "def ",
    )

    @staticmethod
    def _low(value: Any) -> str:
        return str(value or "").lower()

    @staticmethod
    def _clean_excerpt(text: Any, *, max_len: int = 220) -> str:
        value = str(text or "").replace("\r", " ").replace("\n", " ")
        value = re.sub(r"\s+", " ", value).strip(" ,;:-")
        if not value:
            return "fragment pusty albo nieczytelny"
        if len(value) <= max_len:
            return value
        return value[:max_len].rsplit(" ", 1)[0].strip() + "…"

    @classmethod
    def _is_technical_noise(cls, item: dict[str, Any]) -> bool:
        text = cls._low(" ".join(str(item.get(k) or "") for k in ("source", "content_excerpt", "meaning_assessment")))
        return any(marker in text for marker in cls.TECHNICAL_NOISE_MARKERS) and not any(marker in text for marker in cls.USER_MARKERS)

    @classmethod
    def _is_self_only(cls, item: dict[str, Any]) -> bool:
        text = cls._low(" ".join(str(item.get(k) or "") for k in ("source", "content_excerpt", "meaning_assessment")))
        return any(marker in text for marker in cls.SELF_ONLY_MARKERS) and not any(marker in text for marker in cls.USER_MARKERS)

    @classmethod
    def _is_near_duplicate(cls, a: str, b: str) -> bool:
        return bool(a and b and (a[:220] == b[:220] or SequenceMatcher(None, a[:700], b[:700]).ratio() >= 0.86))

    def _filter_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        normalized: list[str] = []
        for item in items:
            if self._is_technical_noise(item) or self._is_self_only(item):
                continue
            norm = self._low(item.get("content_excerpt"))[:900]
            if any(self._is_near_duplicate(norm, prev) for prev in normalized):
                continue
            kept.append(item)
            normalized.append(norm)
        kept.sort(key=lambda i: float(i.get("relevance_score") or 0.0), reverse=True)
        return kept

    @staticmethod
    def _date_label(item: dict[str, Any]) -> str:
        return str(item.get("timestamp") or "bez pewnej daty w rekordzie")

    def _render_items(self, items: list[dict[str, Any]], counts: dict[str, Any]) -> str:
        lines = [
            "Z pamięci o Tobie/Krzysztofie mogę uczciwie przywołać takie tropy — pokazuję sens, źródło i granicę prawdy, nie sam licznik.",
        ]
        for idx, item in enumerate(items[:5], start=1):
            source = item.get("source") or "źródło nieustalone"
            score = item.get("relevance_score")
            label = item.get("relevance_label") or "nieoznaczona"
            score_txt = f" ({score:.2f})" if isinstance(score, (int, float)) else ""
            excerpt = self._clean_excerpt(item.get("content_excerpt"))
            meaning = item.get("meaning_assessment") or "treściowy trop pamięciowy; wymaga ostrożnej interpretacji"
            lines.append(
                f"{idx}. {meaning} Źródło: {source}; {self._date_label(item)}; trafność: {label}{score_txt}. Krótki ślad: „{excerpt}”"
            )
        lines.append(
            "Granica prawdy: to są odczytane ślady z pamięci/indeksu. Nie dopowiadam prywatnych faktów, jeśli nie ma ich w pokazanym fragmencie."
        )
        return "\n".join(lines)

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        memory_context = ctx.get("memory_context") if isinstance(ctx.get("memory_context"), dict) else {}
        payload = MemoryRecallPresenter().build_payload(memory_context, user_text=text, limit=12)
        items = self._filter_items(payload.get("items") or [])
        counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
        if items:
            body = self._render_items(items, counts)
            satisfied = ["memory_content", "source_or_index_status", "truth_boundary", "user_memory_not_self_memory"]
            confidence = 0.84
        else:
            body = (
                "Szukałam pamięci o Tobie/Krzysztofie, ale w tej turze nie dostałam bezpiecznego fragmentu treści do przywołania. "
                "Nie zastąpię tego pamięcią o Łatce ani technicznym raportem. Najlepsze hasła do poszerzenia zapytania: Krzysztof, spacer, muzyka, praca, książka, ogród, Kasia, koty."
            )
            satisfied = ["source_or_index_status", "truth_boundary", "user_memory_not_self_memory"]
            confidence = 0.70
        return RouteHandlerResult(
            self.name,
            self.route,
            body,
            intent=ctx.get("intent", "user_memory_recall_request"),
            data={"memory_recall_payload": payload, "filtered_item_count": len(items), "presentation_schema_version": SCHEMA_VERSION},
            memory_sources=items,
            required_components=ctx.get("required_components", []),
            satisfied_components=satisfied,
            confidence=confidence,
            source_origin_detail=SCHEMA_VERSION,
            truth_boundary="Pamięć o użytkowniku/Krzysztofie jest oddzielona od pamięci o Łatce. Odpowiedź musi pokazywać źródła i nie może konfabulować.",
        )
