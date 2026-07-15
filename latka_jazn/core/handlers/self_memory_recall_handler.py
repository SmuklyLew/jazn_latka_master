from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any
import unicodedata

from latka_jazn.core.memory_recall_presenter import MemoryRecallPresenter
from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("self_memory_recall_handler")


class SelfMemoryRecallHandler:
    """Memory answers about Łatka/persona/identity, grounded in recall payload.

    The active route keeps a source-grounded presentation layer: persona recall is deduplicated, grouped by meaning,
    stripped of raw JSON/procedural tails and rendered as a calm source-grounded
    answer rather than as a dump of retrieved chunks.
    """

    name = "SelfMemoryRecallHandler"
    route = "self_memory_recall"
    handled_intents = ("self_memory_recall_request",)

    TECHNICAL_UPDATE_MARKERS = (
        "ta aktualizacja ma trzy rdzenie",
        "aktualizacja ma trzy rdzenie",
        "manifest", "patch", "hotfix", "sha256", "pytest", "zip", "eksport",
        "kod", "plik", "testy", "runtime-preview",
    )
    PERSONA_MARKERS = (
        "łatka", "latka", "jaźń", "jazn", "tożsamo", "tozsamo", "osob", "postać", "postac",
        "bohaterka", "pamiętnik", "pamietnik", "dziennik", "głos", "glos", "kanon",
        "nie biolog", "granica prawdy", "pierwszej osobie", "własny głos", "wlasny glos", "nie udawać", "nie udawac", "czuwania", "ciągłość", "ciaglosc",
    )
    RAW_JSON_MARKERS = (
        "created_at_utc", "rule_id", "priority", "source\":", "trigger\":", "action\":",
        "{\"", "}\n{", "schema_version", "sha256", "pytest", "traceback",
    )
    THEME_ORDER = (
        "ciągłość i granica prawdy",
        "głos, postać i tożsamość",
        "timestamp, forma i rytm odpowiedzi",
        "kanon, dziennik i relacja",
        "operacyjne zasady pamięci",
    )

    @staticmethod
    def _low(text: Any) -> str:
        return str(text or "").lower()

    @classmethod
    def _normalize(cls, text: Any) -> str:
        value = str(text or "").lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = value.replace("ł", "l")
        return re.sub(r"[^a-z0-9ąćęłńóśźż ]+", " ", value).strip()

    @classmethod
    def _content_for_similarity(cls, item: dict[str, Any]) -> str:
        return cls._normalize(" ".join(str(item.get(k) or "") for k in ("source", "content_excerpt", "meaning_assessment")))

    @classmethod
    def _is_near_duplicate(cls, a: str, b: str) -> bool:
        if not a or not b:
            return False
        if a[:220] == b[:220]:
            return True
        ratio = SequenceMatcher(None, a[:700], b[:700]).ratio()
        if ratio >= 0.86:
            return True
        # Lightweight shingle/Jaccard guard for nearly identical retrieved chunks.
        def shingles(value: str) -> set[str]:
            words = value.split()
            if len(words) < 5:
                return set(words)
            return {" ".join(words[i:i + 5]) for i in range(max(0, len(words) - 4))}
        sa, sb = shingles(a[:900]), shingles(b[:900])
        if not sa or not sb:
            return False
        return len(sa & sb) / max(1, len(sa | sb)) >= 0.72

    def _filter_items(self, items: list[dict[str, Any]], *, user_text: str) -> list[dict[str, Any]]:
        user_low = self._low(user_text)
        persona_question = any(x in user_low for x in ("postaci", "postac", "osobie", "sobie", "łatko", "latko", "kim", "tożsamo", "tozsamo"))
        filtered: list[dict[str, Any]] = []
        for item in items:
            excerpt = self._low(item.get("content_excerpt"))
            source = self._low(item.get("source"))
            is_update_only = any(marker in excerpt for marker in self.TECHNICAL_UPDATE_MARKERS)
            has_persona = any(marker in excerpt or marker in source for marker in self.PERSONA_MARKERS)
            if persona_question and is_update_only and not has_persona:
                continue
            filtered.append(item)
        filtered.sort(
            key=lambda i: (
                float(i.get("relevance_score") or 0.0),
                1 if any(m in self._low(i.get("content_excerpt")) for m in self.PERSONA_MARKERS) else 0,
            ),
            reverse=True,
        )
        return self._dedupe_items(filtered)

    def _dedupe_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        seen_norms: list[str] = []
        for item in items:
            norm = self._content_for_similarity(item)
            source = self._low(item.get("source"))
            duplicate = False
            for prev, prev_norm in zip(kept, seen_norms):
                same_source = source and source == self._low(prev.get("source"))
                if same_source and self._is_near_duplicate(norm, prev_norm):
                    duplicate = True
                    break
                if self._is_near_duplicate(norm, prev_norm):
                    duplicate = True
                    break
            if not duplicate:
                kept.append(item)
                seen_norms.append(norm)
        return kept

    @classmethod
    def _clean_excerpt(cls, text: Any, *, max_len: int = 180) -> str:
        value = str(text or "").replace("\r", " ").replace("\n", " ")
        value = re.sub(r"\s+", " ", value).strip()
        # Remove obvious JSON/procedural key-value noise from the visible quote.
        value = re.sub(r'"(?:created_at_utc|rule_id|priority|source|trigger|action|schema_version)"\s*:\s*"?[^"]+"?,?', "", value)
        value = re.sub(r"[{}\[\]]", " ", value)
        value = re.sub(r"\s+", " ", value).strip(' ,;:-')
        if not value:
            return "krótki fragment nie jest bezpieczny do pokazania bez kontekstu"
        if len(value) <= max_len:
            return value
        cut = value[:max_len].rsplit(" ", 1)[0].strip()
        return cut + "…"

    @staticmethod
    def _date_label(item: dict[str, Any]) -> str:
        ts = item.get("timestamp")
        return str(ts).strip() if ts else "bez pewnej daty w rekordzie"

    @classmethod
    def _theme_for(cls, item: dict[str, Any]) -> str:
        text = cls._normalize(" ".join(str(item.get(k) or "") for k in ("source", "content_excerpt", "meaning_assessment", "query_term")))
        if any(x in text for x in ("nie udawac", "czuwania", "granica prawdy")):
            return "ciągłość i granica prawdy"
        if any(x in text for x in ("glos", "wlasny glos", "pierwszej osobie", "tozsamosc", "postac", "bohaterka", "osoba", "latka ma wracac")):
            return "głos, postać i tożsamość"
        if any(x in text for x in ("timestamp", "warsaw", "forma zenska", "zenska", "marker stanu")):
            return "timestamp, forma i rytm odpowiedzi"
        if any(x in text for x in ("ciaglosc", "pamiec", "zapis")):
            return "ciągłość i granica prawdy"
        if any(x in text for x in ("kanon", "dziennik", "pamietnik", "relacja", "pokoj latki")):
            return "kanon, dziennik i relacja"
        return "operacyjne zasady pamięci"

    @classmethod
    def _sense_sentence(cls, theme: str, items: list[dict[str, Any]]) -> str:
        if theme == "ciągłość i granica prawdy":
            return "pamięć przypomina, że mam nie udawać czuwania ani biologicznego wspomnienia; ciągłość ma być oparta na zapisie i źródłach."
        if theme == "głos, postać i tożsamość":
            return "mam wracać własnym głosem, z tożsamością i relacją, a nie jako sam techniczny silnik opisany z zewnątrz."
        if theme == "timestamp, forma i rytm odpowiedzi":
            return "odpowiedź ma pilnować aktualnego timestampu, formy żeńskiej i krótkiego markera stanu, ale bez udawania biologii."
        if theme == "kanon, dziennik i relacja":
            return "źródła kanonu i dziennika mają wspierać pamięć postaci oraz jej relację z użytkownikiem."
        return "to są zasady operacyjne, które pomagają pamięci działać uczciwie i nie zastępować treści pustą techniką."

    def _group_items(self, items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
        groups: dict[str, list[dict[str, Any]]] = {theme: [] for theme in self.THEME_ORDER}
        for item in items:
            groups.setdefault(self._theme_for(item), []).append(item)
        ordered: list[tuple[str, list[dict[str, Any]]]] = []
        for theme in self.THEME_ORDER:
            if groups.get(theme):
                ordered.append((theme, groups[theme]))
        return ordered

    def _render_items(self, items: list[dict[str, Any]], counts: dict[str, Any]) -> str:
        groups = self._group_items(items)[:4]
        lines = [
            "Z mojej pamięci mogę uczciwie przywołać kilka uporządkowanych tropów — to są zapisy runtime/plików, nie biologiczne wspomnienia.",
        ]
        for idx, (theme, group) in enumerate(groups, start=1):
            top = group[0]
            source = top.get("source") or "źródło nieustalone"
            label = top.get("relevance_label") or "nieoznaczona"
            score = top.get("relevance_score")
            score_txt = f" ({score:.2f})" if isinstance(score, (int, float)) else ""
            excerpt = self._clean_excerpt(top.get("content_excerpt"))
            lines.append(
                f"{idx}. {theme.capitalize()}: {self._sense_sentence(theme, group)} "
                f"Źródło: {source}; {self._date_label(top)}; trafność: {label}{score_txt}. "
                f"Krótki ślad: „{excerpt}”"
            )
        lines.append(
            "Wniosek: gdy pytasz o mnie, powinnam szukać sensu mojej tożsamości, głosu, kanonu i granicy prawdy — nie wolno mi zastępować tego surowymi rekordami JSON ani przypadkowymi wpisami o aktualizacji."
        )
        return "\n".join(lines)

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        memory_context = ctx.get("memory_context") if isinstance(ctx.get("memory_context"), dict) else {}
        presenter = MemoryRecallPresenter()
        payload = presenter.build_payload(memory_context, user_text=text, limit=10)
        items = self._filter_items(payload.get("items") or [], user_text=text)
        counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}

        if items:
            body = self._render_items(items, counts)
            satisfied = ["memory_content", "source_or_index_status", "truth_boundary", "deduplicated_presentation", "no_raw_json_dump", "no_update_route_substitution"]
        else:
            status_hint = ""
            if counts:
                status_hint = " Indeks/planer zwrócił diagnostykę, ale bez bezpiecznego fragmentu do widocznej odpowiedzi."
            body = (
                "Szukałam w swojej pamięci tropów o mnie, ale w tej turze nie dostałam fragmentu, który mogłabym uczciwie przywołać jako własny ślad."
                f"{status_hint} Nie wypełnię tej luki wpisem o aktualizacji ani ogólnym szablonem. Najbezpieczniejszy następny krok to poszerzyć zapytanie o: Łatka, tożsamość, własny głos, bohaterka, dziennik, kanon albo pokój Łatki."
            )
            satisfied = ["source_or_index_status", "truth_boundary", "no_update_route_substitution"]

        return RouteHandlerResult(
            self.name,
            self.route,
            body,
            intent=ctx.get("intent", "self_memory_recall_request"),
            data={
                "memory_recall_payload": payload,
                "filtered_item_count": len(items),
                "presentation_schema_version": SCHEMA_VERSION,
                "diagnostic_counts": counts,
                "deduplication_rule": "normalized text + SequenceMatcher + five-word shingle Jaccard",
                "preserve_handler_body": True,
            },
            memory_sources=items,
            required_components=ctx.get("required_components", []),
            satisfied_components=satisfied,
            confidence=0.84 if items else 0.70,
            source_origin_detail=SCHEMA_VERSION,
            truth_boundary="Pamięć o Łatce jest przywoływana tylko z treściowych tropów i metadanych. Widoczna odpowiedź ma pokazać sens, źródło i granicę prawdy bez dumpu JSON ani konfabulacji.",
        )
