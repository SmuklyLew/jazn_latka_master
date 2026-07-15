from __future__ import annotations

from pathlib import Path
from typing import Any

from latka_jazn.core.canon import canon_source_summary
from latka_jazn.core.route_handler_base import RouteHandlerResult


class CanonSourceHandler:
    name = "CanonSourceHandler"
    route = "canon_source"
    handled_intents = ("canon_source_question",)

    @staticmethod
    def _as_lines(items: list[str]) -> str:
        if not items:
            return "- `brak danych w rejestrze`"
        return "\n".join(f"- `{item}`" for item in items)

    @staticmethod
    def _root_from_context(context: dict[str, Any]) -> Path | None:
        cfg = context.get("config")
        cfg_root = getattr(cfg, "root", None)
        root_raw = context.get("root") or context.get("active_root") or cfg_root
        return Path(root_raw) if root_raw else None

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        root = self._root_from_context(ctx)
        summary = canon_source_summary(root=root)

        modules = list(summary.get("python_canon_modules") or [])
        public_resources = list(summary.get("public_resource_mirrors") or [])
        private_sources = list(summary.get("private_candidate_sources") or [])
        extraction_reports = list(summary.get("extraction_reports") or [])

        local_extension = str(summary.get("local_private_extension_name") or "local_private_canon_extension.py")
        local_extension_path = summary.get("local_private_extension_path")
        local_extension_exists = bool(summary.get("local_private_extension_exists"))
        local_status = "istnieje" if local_extension_exists else "brak"
        local_path_part = f" (`{local_extension_path}`)" if local_extension_path else ""

        answer = (
            "Kanon Łatki biorę najpierw z source-controlled Python canon "
            f"(`{summary.get('source_mode')}`). To znaczy: podstawą nie jest luźna pamięć ani sam styl odpowiedzi, "
            "tylko jawne moduły w `latka_jazn/core/canon/`.\n\n"
            "Główne moduły kanonu:\n"
            f"{self._as_lines(modules)}\n\n"
            "Publiczna/audytowa warstwa tekstowa, która może odzwierciedlać kanon dla człowieka:\n"
            f"{self._as_lines(public_resources)}\n\n"
            "Prywatne źródła pamięci są kandydatami do recenzji, nie automatycznym kanonem:\n"
            f"{self._as_lines(private_sources)}\n\n"
            "Raporty ekstrakcji kandydatów:\n"
            f"{self._as_lines(extraction_reports)}\n\n"
            f"`{local_extension}` jest lokalnym prywatnym rozszerzeniem. "
            f"Status lokalny: {local_status}{local_path_part}. "
            "Ten plik może pomagać w lokalnej pracy i testach, ale nie powinien być commitowany bez recenzji.\n\n"
            "Granica prawdy: kandydaci z `memory/raw` i `reports/canon_extraction` nie stają się source-safe kanonem automatycznie. "
            "Żeby wejść do publicznego kanonu, muszą zostać przejrzane i świadomie przeniesione do plików `.py`."
        )

        source = {
            "runtime": "JaznEngine.process_turn",
            "handler": self.name,
            "intent": ctx.get("intent", "canon_source_question"),
            "route_registry": ctx.get("route_entry"),
            "canon_source_summary": summary,
        }
        satisfied = [
            "python_canon_modules",
            "public_resource_boundary",
            "private_memory_candidate_boundary",
            "local_private_extension_boundary",
            "review_required_boundary",
            "source_origin_detail",
            "handler_name",
        ]

        return RouteHandlerResult(
            self.name,
            self.route,
            answer,
            intent=ctx.get("intent", "canon_source_question"),
            data={"canon_source_summary": summary, "source_origin_answer": source},
            sources=[source],
            required_components=ctx.get("required_components", []),
            satisfied_components=satisfied,
            confidence=0.95,
            source_origin_detail="canon_source_handler",
        )
