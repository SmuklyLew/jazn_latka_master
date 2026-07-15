from __future__ import annotations
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
import hashlib, json, re
from datetime import datetime, timezone

SCHEMA_VERSION = "turn_logic_auditor/v14.8.2.4"

@dataclass(slots=True)
class TurnLogicAudit:
    user_text: str
    normalized_text: str
    detected_intent: str
    route: str
    handler: str
    speech_act: str = "unknown"
    question_object: str = "unknown"
    memory_policy: dict[str, Any] = field(default_factory=dict)
    allowed_sources: list[str] = field(default_factory=list)
    forbidden_sources: list[str] = field(default_factory=list)
    current_turn_terms: list[str] = field(default_factory=list)
    response_terms_not_grounded: list[str] = field(default_factory=list)
    stale_context_risk: bool = False
    logic_warnings: list[str] = field(default_factory=list)
    logic_errors: list[str] = field(default_factory=list)
    must_regenerate: bool = False
    repair_hint: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(slots=True)
class TurnInvariant:
    id: str
    description: str
    severity: str
    check: Callable[[dict[str, Any]], bool]

class TurnLogicAuditor:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else None

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    @staticmethod
    def _terms(text: str) -> list[str]:
        return [t for t in re.findall(r"[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ-]{4,}", (text or "").lower())[:80]]

    def audit(self, *, user_text: str, response_text: str = "", detected_intent: str, route: str, handler: str, policy: dict[str, Any] | None = None, speech_act: str = "unknown", question_object: str = "unknown") -> TurnLogicAudit:
        norm = self._norm(user_text)
        body = self._norm(response_text)
        audit = TurnLogicAudit(user_text=user_text, normalized_text=norm, detected_intent=detected_intent, route=route, handler=handler, speech_act=speech_act, question_object=question_object, memory_policy=policy or {}, current_turn_terms=self._terms(user_text))
        if speech_act == "question" and question_object in {"runtime", "runtime_status", "runtime_health"} and detected_intent == "ordinary_conversation":
            audit.logic_errors.append("runtime_question_collapsed_to_ordinary")
        if any(x in norm for x in ("bezpośrednio z łatką", "bezposrednio z latka")) and detected_intent != "direct_latka_voice_request":
            audit.logic_errors.append("direct_latka_voice_wrong_intent")
        if any(x in norm for x in ("za kogo się uważasz", "za kogo sie uwazasz", "czujesz się istotą", "czujesz sie istota")) and detected_intent != "identity_memory_existence_compound_question":
            audit.logic_errors.append("identity_memory_existence_wrong_intent")
        # Pytania o status Jaźni/runtime nie mogą iść do ordinary/fallback.
        if any(x in norm for x in ("czy teraz rozmawiam z", "z kim rozmawiam", "jaźnią łatki", "jaznia latki", "chatgpt czy jaźń", "chatgpt czy jazn")) and detected_intent not in {"runtime_activation_status_question", "identity_boundary_question", "identity_direct_question"}:
            audit.logic_errors.append("runtime_status_question_wrong_intent")
        # Pytania o --chat nie mogą być aktualizacją ani ordinary.
        if any(x in norm for x in ("runtime-preview", "--chat", "skrypt chat", "tryb chat", "stdin")) and detected_intent != "runtime_chat_mode_request":
            audit.logic_errors.append("chat_mode_question_wrong_intent")
        # kod źródłowy nie jest source-origin.
        if any(x in norm for x in ("kod źródłowy", "kodzie źródłowym", "kod zrodlowy", "kodzie zrodlowym")) and detected_intent == "runtime_source_question":
            audit.logic_errors.append("source_code_false_runtime_source")
        # ordinary conversation should not be metareport.
        if detected_intent in {"ordinary_conversation", "standalone_greeting"} and any(x in body for x in ("jaźń jako warstwa", "warstwa pamięci", "diagnostyk", "runtime jako")):
            audit.logic_errors.append("ordinary_dialogue_meta_report")
        # stale fragments not grounded in user text.
        stale_terms = ["drzwi", "zlecen", "v14.6.1", "v14.6.2", "warszaw", "miodowa"]
        for term in stale_terms:
            if term in body and term not in norm and detected_intent in {"ordinary_conversation", "self_state_question", "reciprocal_self_state_question", "sleep_closure_statement"}:
                audit.response_terms_not_grounded.append(term)
        if "ta aktualizacja ma trzy rdzenie" in body and not any(x in norm for x in ("aktualiz", "hotfix", "patch", "napraw", "popraw", "rozbuduj")):
            audit.response_terms_not_grounded.append("stale_update_summary")
        if "timestamp potrafił istnieć" in body and not any(x in norm for x in ("timestamp", "znacznik czasu", "gubisz czas", "turn_id", "trace_id")):
            audit.response_terms_not_grounded.append("timestamp_repair")
        if audit.response_terms_not_grounded:
            audit.logic_errors.append("response_contains_unapproved_old_context")
            audit.stale_context_risk = True
        if audit.logic_errors:
            audit.must_regenerate = True
            audit.repair_hint = "Regenerate using current-turn intent, TurnResponsePolicy and no unapproved memory/carryover."
        return audit

    def append(self, audit: TurnLogicAudit) -> Path | None:
        if not self.root:
            return None
        path = self.root / "memory" / "layered" / "turn_logic_audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = audit.to_dict() | {"written_at_utc": datetime.now(timezone.utc).isoformat(), "audit_sha256": hashlib.sha256(json.dumps(audit.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True)+"\n")
        return path
