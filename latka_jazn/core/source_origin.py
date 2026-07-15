from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any


@dataclass(slots=True)
class SourceOriginPacket:
    """Jawny opis źródeł odpowiedzi Jaźni.

    Ten pakiet nie ma udawać, że runtime wie więcej niż naprawdę wie.
    Ma odróżnić: odpowiedź bezpośrednią runtime, cognitive-frame dla ChatGPT,
    bieżący czat, pamięć SQLite/warstwową, NLP, wnioskowanie i źródła zewnętrzne.
    """

    schema_version: str
    primary: str
    contributing_sources: list[str]
    confidence: float
    truth_boundary: str
    evidence_counts: dict[str, int] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SourceOriginAnalyzer:
    schema_version = "source_origin/v14.6.2"

    def analyse(
        self,
        *,
        runtime_mode: str,
        client_context: dict[str, Any] | None = None,
        intent_tags: list[str] | None = None,
        memory_context: dict[str, Any] | None = None,
        nlp_report: dict[str, Any] | None = None,
        web_sources_used: bool = False,
        inference_used: bool = True,
    ) -> SourceOriginPacket:
        client_context = client_context or {}
        intent_tags = intent_tags or []
        memory_context = memory_context or {}
        nlp_report = nlp_report or {}

        sources: list[str] = []
        flags: list[str] = []
        evidence = {
            "episodic_memories": int(((memory_context.get("counts") or {}).get("episodes") or 0)),
            "legacy_messages": int(((memory_context.get("counts") or {}).get("legacy_messages") or 0)),
            "raw_chat_fallback": int(((memory_context.get("counts") or {}).get("raw_chat_fallback") or 0)),
            "selected_lemmas": len(nlp_report.get("selected_lemmas") or []),
            "low_confidence_terms": len(nlp_report.get("unknown_or_low_confidence_terms") or []),
        }

        if runtime_mode == "cognitive_frame":
            sources.extend(["runtime", "chatgpt_cognitive_bridge"])
            primary = "runtime_cognitive_frame"
        elif runtime_mode == "direct_conversation":
            sources.append("runtime")
            primary = "runtime_direct_response"
        elif runtime_mode == "runtime_preview":
            sources.extend(["runtime", "runtime_preview", "cognitive_frame"])
            primary = "runtime_preview"
        else:
            sources.append(runtime_mode or "runtime")
            primary = runtime_mode or "runtime"

        lifecycle = str(client_context.get("lifecycle") or "unspecified")
        if lifecycle.startswith("one_shot"):
            flags.append("one_shot_runtime_not_background_process")
        if client_context.get("debug_direct"):
            flags.append("debug_direct_requested")
        if web_sources_used:
            sources.append("web_sources")
        if inference_used:
            sources.append("inference")
        if evidence["episodic_memories"] or evidence["legacy_messages"] or evidence["raw_chat_fallback"]:
            sources.append("memory")
        if nlp_report:
            sources.append("polish_nlp")
        if "truth_boundary" in intent_tags:
            flags.append("truth_boundary_relevant")
        if evidence["low_confidence_terms"]:
            flags.append("nlp_low_confidence_terms_present")

        contributing = self._unique(sources)
        confidence = 0.72
        if evidence["episodic_memories"] or evidence["legacy_messages"]:
            confidence += 0.10
        if web_sources_used:
            confidence += 0.10
        if flags and "one_shot_runtime_not_background_process" in flags:
            confidence -= 0.04
        confidence = max(0.10, min(0.96, round(confidence, 3)))

        return SourceOriginPacket(
            schema_version=self.schema_version,
            primary=primary,
            contributing_sources=contributing,
            confidence=confidence,
            truth_boundary=(
                "Odpowiedź może korzystać z runtime, pamięci, NLP i wnioskowania, ale nie może mówić "
                "o stałym procesie w tle, jeśli klient uruchomił tylko jednorazowe wywołanie. "
                "Źródło i tryb działania mają być jawne, gdy użytkownik o to pyta."
            ),
            evidence_counts=evidence,
            flags=flags,
        )

    @staticmethod
    def _unique(items: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out
