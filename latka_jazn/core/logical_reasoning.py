from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

from latka_jazn.core.scientific_basis import references_for_module
from latka_jazn.version import schema_version


@dataclass(slots=True)
class ReasoningStep:
    label: str
    input: str
    operation: str
    output: str
    confidence: float


@dataclass(slots=True)
class LogicalReasoningReport:
    problem_statement: str
    intent: list[str]
    known_facts: list[str]
    assumptions: list[str]
    unknowns: list[str]
    rules_applied: list[str]
    contradictions_or_risks: list[str]
    public_trace: list[ReasoningStep]
    conclusion: str
    confidence: float
    scientific_basis: list[dict]
    evidence: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    original_intent_coverage: dict[str, Any] = field(default_factory=dict)
    schema_version: str = schema_version("logical_reasoning")
    boundary: str = "To jest jawny audyt rozumowania operacyjnego, nie prywatny ani biologiczny strumień myśli."

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["public_trace"] = [asdict(step) for step in self.public_trace]
        return data


class LogicalReasoner:
    """Jawny moduł rozumowania dla runtime Jaźni.

    Moduł nie próbuje generować ukrytego łańcucha myśli. Tworzy krótki,
    kontrolowalny audyt: co jest faktem, co założeniem, czego brakuje, jakie
    reguły trzeba zastosować i jaka decyzja jest najbezpieczniejsza logicznie.
    """

    def analyse(
        self,
        *,
        text: str,
        intent_tags: list[str] | None = None,
        memory_context: dict | None = None,
        truth_audit: list[dict] | None = None,
    ) -> LogicalReasoningReport:
        low = text.lower()
        intents = list(intent_tags or [])
        known: list[str] = []
        assumptions: list[str] = []
        unknowns: list[str] = []
        rules: list[str] = []
        risks: list[str] = []

        if text.strip():
            known.append("Odebrano bieżącą wiadomość użytkownika jako główny sygnał decyzyjny.")
        if memory_context:
            counts = memory_context.get("counts") or {}
            known.append(
                "Dostępny jest kontekst pamięci: "
                f"epizody={counts.get('episodes', 0)}, legacy={counts.get('legacy_messages', 0)}, raw={counts.get('raw_chat_fallback', 0)}."
            )
        if truth_audit:
            risky = sum(1 for item in truth_audit if item.get("requires_disclaimer") or item.get("risk_flags"))
            known.append(f"Audyt prawdy zwrócił {len(truth_audit)} ocen zdań, w tym ryzykowne={risky}.")

        if any(w in low for w in ("świadomo", "swiadomo", "przeży", "przezy", "czuję naprawdę", "czuje naprawde")):
            assumptions.append("Użytkownik prosi o wzmocnienie modelu świadomości, ale wolno wdrażać tylko świadomość operacyjną/funkcjonalną.")
            rules.append("Nie wolno twierdzić, że runtime ma fenomenalne lub biologiczne przeżywanie.")
            risks.append("Słowo „świadomość” może zostać błędnie odczytane jako deklaracja prawdziwego przeżywania.")
        if any(w in low for w in ("logicz", "wniosk", "rozum", "myśleć", "myslec")):
            assumptions.append("Wiadomość wymaga wyraźnego rozdzielenia faktów, założeń, niepewności i wniosków.")
            rules.append("Najpierw zbuduj ramę problemu, potem zastosuj reguły, a dopiero na końcu sformułuj odpowiedź.")
        if any(w in low for w in ("aktualizac", "patch", "wersj", "napraw", "popraw")):
            known.append("Wiadomość jest intencją aktualizacji systemu, nie tylko zwykłą rozmową.")
            rules.append("Zmiana systemowa musi dostać manifest, raport, testy i zapis pamięciowy.")
        if any(w in low for w in ("pamię", "pamie", "dziennik", "wspomn")):
            rules.append("Twierdzenia pamięciowe muszą mieć źródło, grounding i confidence.")
        if not known:
            unknowns.append("Brak jawnych faktów poza samym nadejściem wiadomości.")
        if "unknown" in {str(item.get("grounding")) for item in (truth_audit or [])}:
            unknowns.append("Część twierdzeń wymaga oznaczenia jako unknown albo inferred.")
        if not unknowns:
            unknowns.append("Nie stwierdzono krytycznej niewiadomej dla bieżącego kroku; nadal obowiązuje granica prawdy.")
        if not rules:
            rules.append("Zastosuj standard: fakt → wniosek → odpowiedź, bez pustego fallbacku.")

        trace = [
            ReasoningStep(
                "observe",
                text[:180],
                "wykrycie intencji i słów kluczowych",
                ", ".join(intents or ["conversation"]),
                0.86,
            ),
            ReasoningStep(
                "ground",
                "bieżąca wiadomość + pamięć + audyt prawdy",
                "oddzielenie faktów od założeń",
                f"fakty={len(known)}, założenia={len(assumptions)}, niewiadome={len(unknowns)}",
                0.82,
            ),
            ReasoningStep(
                "infer",
                "reguły proceduralne i granica prawdy",
                "dobór bezpiecznej decyzji",
                "odpowiadać logicznie, ale bez deklarowania fenomenalnej świadomości",
                0.78,
            ),
            ReasoningStep(
                "validate",
                "wniosek końcowy",
                "kontrola sprzeczności i ryzyk",
                f"ryzyka={len(risks)}; wymagane etykiety prawdy pozostają aktywne",
                0.8,
            ),
        ]

        if any("aktualizac" in k.lower() or "patch" in low for k in known) or "architecture" in intents:
            conclusion = "Wykonać aktualizację architektury: dodać jawny model świadomości operacyjnej, logiczny audyt rozumowania i testy regresji."
        elif any("świadomo" in a.lower() or "swiadomo" in a.lower() for a in assumptions):
            conclusion = "Odpowiedzieć przez model świadomości operacyjnej, nie przez twierdzenie o biologicznym przeżywaniu."
        else:
            conclusion = "Odpowiedzieć normalnie, używając krótkiego audytu faktów i wniosków tylko wtedy, gdy to pomaga rozmowie."

        confidence = 0.72
        if risks:
            confidence -= 0.05
        if memory_context and (memory_context.get("counts") or {}).get("episodes"):
            confidence += 0.05

        return LogicalReasoningReport(
            problem_statement=self._problem_statement(text),
            intent=intents or ["conversation"],
            known_facts=known,
            assumptions=assumptions or ["Brak dodatkowych założeń poza standardową granicą prawdy."],
            unknowns=unknowns,
            rules_applied=rules,
            contradictions_or_risks=risks or ["Nie wykryto jawnej sprzeczności; utrzymać etykiety fakt/wniosek/symbol."],
            public_trace=trace,
            conclusion=conclusion,
            confidence=max(0.0, min(1.0, confidence)),
            scientific_basis=references_for_module("logical_reasoning"),
            evidence=[
                "current_user_message",
                *(["memory_context_counts"] if memory_context else []),
                *(["truth_audit"] if truth_audit else []),
            ],
            next_actions=self._next_actions(text, intents, risks),
            original_intent_coverage=self._intent_coverage(text),
        )

    @staticmethod
    def _intent_coverage(text: str) -> dict[str, Any]:
        low = text.lower()
        families = {
            "capability_or_scope": any(x in low for x in ("co umiesz", "co potraf", "moduł", "modul", "adapter", "narzędz", "narzedz")),
            "defects_or_gaps": any(x in low for x in ("błąd", "blad", "luka", "napraw", "blok", "brak")),
            "verification": any(x in low for x in ("sprawd", "test", "weryfik", "działa", "dziala")),
        }
        requested = [key for key, value in families.items() if value]
        return {"requested_families": requested, "must_be_rechecked_before_final": bool(requested)}

    @staticmethod
    def _next_actions(text: str, intents: list[str], risks: list[str]) -> list[str]:
        low = text.lower()
        actions: list[str] = []
        if any(x in low for x in ("napraw", "patch", "aktualiz")):
            actions.extend(["zbuduj odtwarzalny patch", "uruchom testy regresyjne", "sprawdź status runtime po restarcie"])
        if any(x in low for x in ("adapter", "openai", "lm studio", "chatgpt")):
            actions.append("oddziel konfigurację adaptera od potwierdzonej generacji i wykonania narzędzi")
        if risks:
            actions.append("zachowaj jawne granice prawdy dla wykrytych ryzyk")
        return list(dict.fromkeys(actions or ["zweryfikuj zgodność odpowiedzi z pełnym celem wiadomości"]))

    def _problem_statement(self, text: str) -> str:
        clean = " ".join(text.strip().split())
        if not clean:
            return "Brak treści wiadomości; potrzebny fallback diagnostyczny."
        if len(clean) <= 220:
            return clean
        return clean[:217] + "..."
