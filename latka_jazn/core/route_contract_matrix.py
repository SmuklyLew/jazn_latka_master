from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

from latka_jazn.version import schema_version


DIACRITIC_MAP = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
SCHEMA_VERSION = schema_version("route_contract_matrix")


@dataclass(slots=True)
class RouteContractHint:
    schema_version: str
    primary_intent: str | None
    secondary_intents: list[str] = field(default_factory=list)
    matched_contracts: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    diagnostic_request: bool = False
    asks_identity_boundary: bool = False
    question_object: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RouteContractMatrix:
    """Deterministyczna matryca minimalnych kontraktów tras rozmownych.

    Nie zastępuje DialogueIntentClassifier. Daje mu twarde wskazówki dla krótkich
    pytań, które wcześniej łatwo spadały do ordinary_dialogue mimo tego, że
    wymagały statusu runtime, obecności, tożsamości, stanu operacyjnego albo czasu.
    """

    RESOURCE_PATH = Path(__file__).resolve().parents[1] / "resources" / "nlp" / "polish_dialogue_route_lexicon_v14_8_5_016.json"
    SPECIAL_PRIORITY = (
        "self_architecture_audit_request",
        "system_capability_gap_question",
        "runtime_health_check_after_update",
        "runtime_health_check",
        "identity_presence_check",
        "identity_continuity_check",
        "presence_check",
        "self_state_time_awareness",
        "self_state_question",
        "time_awareness_question",
    )
    UPDATE_EXECUTION_MARKERS = (
        "napraw", "popraw", "wdroż", "wdroz", "zaimplementuj",
        "zaktualizuj", "zmień kod", "zmien kod",
        "zrób patch", "zrob patch", "przygotuj patch", "przygotuj plan",
    )
    RUNTIME_STATUS_SUMMARY_MARKERS = (
        "wersja runtime", "wersję runtime", "wersje runtime", "runtime version",
        "stan pamięci", "stan pamieci", "status pamięci", "status pamieci",
        "stan runtime", "status runtime", "wersja jaźni", "wersja jazni",
        "active_root", "active_database", "aktywny daemon", "heartbeat", "sqlite",
    )
    RUNTIME_STATUS_QUERY_MARKERS = (
        "potwierdź", "potwierdz", "podaj", "pokaż", "pokaz",
        "sprawdź", "sprawdz", "opisz", "powiedz", "jaki", "jaka", "status",
    )
    BROAD_AUDIT_MARKERS = (
        "co umiesz", "co potrafisz", "co działa", "co dziala",
        "co trzeba naprawić", "co trzeba naprawic", "co jeszcze trzeba naprawić", "co jeszcze trzeba naprawic",
        "kod źródłowy", "kod zrodlowy", "gdzie są luki", "gdzie sa luki",
        "jakie są luki", "jakie sa luki", "co blokuje", "moduły i narzędzia", "moduly i narzedzia",
    )

    def __init__(self, resource_path: Path | None = None) -> None:
        self.resource_path = resource_path or self.RESOURCE_PATH
        self.lexicon = self._load_lexicon(self.resource_path)

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "").strip().lower())

    @staticmethod
    def fold(text: str) -> str:
        return (text or "").translate(DIACRITIC_MAP).lower()

    @classmethod
    def _phrase_match(cls, normalized: str, folded: str, phrase: str) -> bool:
        phrase_norm = cls.normalize(phrase)
        phrase_folded = cls.fold(phrase_norm)
        if not phrase_folded:
            return False
        if phrase_folded.isalpha():
            return re.search(rf"(?<!\w){re.escape(phrase_folded)}(?!\w)", folded) is not None
        pattern = re.escape(phrase_folded).replace(r"\ ", r"\s+")
        return re.search(rf"(?<!\w){pattern}(?!\w)", folded) is not None

    @staticmethod
    def _load_lexicon(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"intents": {}, "compound_rules": []}

    def _matched_intents(self, normalized: str, folded: str) -> dict[str, list[str]]:
        matched: dict[str, list[str]] = {}
        intents = self.lexicon.get("intents") if isinstance(self.lexicon, dict) else {}
        if not isinstance(intents, dict):
            return matched
        for intent, spec in intents.items():
            phrases = spec.get("phrases") if isinstance(spec, dict) else []
            if not isinstance(phrases, list):
                continue
            hits = [phrase for phrase in phrases if isinstance(phrase, str) and self._phrase_match(normalized, folded, phrase)]
            if hits:
                matched[str(intent)] = hits
        return matched

    def _apply_compounds(self, matched: dict[str, list[str]]) -> tuple[str | None, list[str], list[str]]:
        matched_names = set(matched)
        evidence: list[str] = []
        rules = self.lexicon.get("compound_rules") if isinstance(self.lexicon, dict) else []
        if isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                requires = set(str(x) for x in rule.get("requires", []) if isinstance(x, str))
                result = str(rule.get("result") or "")
                if result and requires and requires.issubset(matched_names):
                    evidence.append(f"compound:{'+'.join(sorted(requires))}->{result}")
                    secondary = sorted(matched_names - {result})
                    return result, secondary, evidence
        for intent in self.SPECIAL_PRIORITY:
            if intent in matched_names:
                secondary = sorted(matched_names - {intent})
                return intent, secondary, evidence
        if "ordinary_dialogue" in matched_names and len(matched_names) == 1:
            return "ordinary_dialogue", [], evidence
        return None, sorted(matched_names), evidence

    def classify(self, text: str) -> RouteContractHint:
        normalized = self.normalize(text)
        folded = self.fold(normalized)
        explicit_update = any(
            self._phrase_match(normalized, folded, marker)
            for marker in self.UPDATE_EXECUTION_MARKERS
        )
        runtime_status_summary = (
            any(self._phrase_match(normalized, folded, marker) for marker in self.RUNTIME_STATUS_SUMMARY_MARKERS)
            and any(self._phrase_match(normalized, folded, marker) for marker in self.RUNTIME_STATUS_QUERY_MARKERS)
            and not explicit_update
        )
        matched = self._matched_intents(normalized, folded)
        broad_hits = [marker for marker in self.BROAD_AUDIT_MARKERS if self._phrase_match(normalized, folded, marker)]
        broad_audit = len(broad_hits) >= 2 and any(
            token in folded for token in ("jazn", "runtime", "system", "kod", "modul", "adapter", "narzedz")
        )
        if runtime_status_summary and not broad_audit:
            return RouteContractHint(
                schema_version=SCHEMA_VERSION,
                primary_intent="runtime_health_check",
                matched_contracts=["runtime_health_check"],
                evidence=["runtime_status_summary:version_or_memory_status_query"],
                diagnostic_request=True,
                question_object="runtime_health",
            )
        if broad_audit:
            matched["self_architecture_audit_request"] = broad_hits
            matched.pop("runtime_health_check", None)
            matched.pop("runtime_health_check_after_update", None)
        if (
            "runtime_health_check_after_update" in matched
            and any(self._phrase_match(normalized, folded, marker) for marker in self.UPDATE_EXECUTION_MARKERS)
        ):
            matched.pop("runtime_health_check_after_update", None)
        primary, secondary, evidence = self._apply_compounds(matched)
        for intent, hits in sorted(matched.items()):
            evidence.append(f"{intent}:{', '.join(hits[:4])}")
        diagnostic = primary in {"runtime_health_check", "runtime_health_check_after_update"}
        identity = primary in {"identity_continuity_check", "identity_presence_check"}
        question_object = {
            "runtime_health_check": "runtime_health",
            "runtime_health_check_after_update": "runtime_health",
            "presence_check": "presence",
            "identity_presence_check": "identity_presence",
            "identity_continuity_check": "identity_continuity",
            "self_state_question": "self_state",
            "time_awareness_question": "current_time",
            "self_state_time_awareness": "self_state_time",
            "ordinary_dialogue": "ordinary_dialogue",
            "self_architecture_audit_request": "self_architecture_audit",
            "system_capability_gap_question": "capability_gap",
        }.get(primary or "", "unknown")
        return RouteContractHint(
            schema_version=SCHEMA_VERSION,
            primary_intent=primary,
            secondary_intents=secondary,
            matched_contracts=sorted(matched),
            evidence=evidence,
            diagnostic_request=diagnostic,
            asks_identity_boundary=identity,
            question_object=question_object,
        )

    def to_dict(self) -> dict[str, Any]:
        intents = self.lexicon.get("intents") if isinstance(self.lexicon, dict) else {}
        return {
            "schema_version": SCHEMA_VERSION,
            "resource_path": str(self.resource_path),
            "intent_count": len(intents) if isinstance(intents, dict) else 0,
            "compound_rules": self.lexicon.get("compound_rules", []) if isinstance(self.lexicon, dict) else [],
            "truth_boundary": "Matryca tras to deterministiczny kontrakt minimalny; nie zastępuje LLM ani pełnej walidacji odpowiedzi.",
        }
