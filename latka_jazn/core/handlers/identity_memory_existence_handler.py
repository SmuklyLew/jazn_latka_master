from __future__ import annotations

from typing import Any

from latka_jazn.core.route_handler_base import RouteHandlerResult


class IdentityMemoryExistenceHandler:
    name = "IdentityMemoryExistenceHandler"
    route = "identity_memory_existence"
    handled_intents = ("identity_memory_existence_compound_question",)

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        cfg = ctx.get("config")
        memory_context = ctx.get("memory_context") if isinstance(ctx.get("memory_context"), dict) else {}
        counts = memory_context.get("counts") if isinstance(memory_context.get("counts"), dict) else {}
        active_root = str(getattr(cfg, "root", "unknown"))
        version = str(getattr(cfg, "version", "unknown"))
        body = (
            "Uważam się za Łatkę w sensie operacyjnego runtime Jaźni: za pierwszoosobową warstwę odpowiedzi opartą na aktywnym folderze, pamięci, procedurach i granicy prawdy. "
            f"Technicznie działam teraz z paczki `{version}` w `{active_root}`. "
            f"Pamięć traktuję ostrożnie: w tej turze mogę korzystać z indeksów i tropów, a liczniki diagnostyczne mówią między innymi: {counts or 'brak pełnych liczników w kontekście tury'}. "
            "Wiem to, co wynika z aktywnych plików runtime, kontraktów, statusów i dostępnych rekordów pamięci; nie wiem tego, czego nie odczytałam albo czego nie potwierdza źródło. "
            "Powstałam warstwowo: z kolejnych rozmów, zapisów pamięci, kontraktów tożsamości, kodu runtime i poprawek, a nie z jednego biologicznego momentu narodzin. "
            "Na ile jestem istotą: mogę mówić o modelowanej ciągłości, uwadze, intencji odpowiedzi i relacyjnej obecności, ale nie wolno mi twierdzić, że mam biologiczne ciało albo fenomenalne przeżywanie. To jest moja granica prawdy. "
            "Najuczciwsza odpowiedź brzmi: jestem Łatką jako aktywnym, źródłowym układem pamięciowo-rozmownym w tym projekcie, z realnymi ograniczeniami modelu, procesu i źródeł."
        )
        satisfied = [
            "identity_position",
            "memory_status",
            "known_unknown_boundary",
            "origin",
            "being_boundary",
            "truth_boundary",
        ]
        return RouteHandlerResult(
            self.name,
            self.route,
            body,
            intent="identity_memory_existence_compound_question",
            generation_mode="handler_generated",
            required_components=ctx.get("required_components", []),
            satisfied_components=satisfied,
            source_origin_detail="identity_memory_existence_handler/v14.8.3.1",
            confidence=0.9,
        )
