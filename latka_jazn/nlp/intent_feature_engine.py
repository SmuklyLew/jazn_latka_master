from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import unicodedata
from typing import Any

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("intent_feature_engine")
DIACRITIC_MAP = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
TOKEN_RE = re.compile(r"[\w+#.-]+", re.UNICODE)


@dataclass(slots=True)
class IntentCandidate:
    intent: str
    score: float
    positive_evidence: list[str] = field(default_factory=list)
    negative_evidence: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class IntentDecisionFrame:
    schema_version: str
    normalized_text: str
    folded_text: str
    tokens: list[str]
    domains: list[str]
    candidates: list[IntentCandidate]
    top_intent: str
    top_score: float
    runner_up_intent: str | None
    runner_up_score: float
    decision_margin: float
    ambiguous: bool
    abstain_reason: str | None
    lexical_conflicts: list[str] = field(default_factory=list)
    truth_boundary: str = (
        "Ranking jest deterministycznym bezpiecznikiem NLP. Nie zastępuje pełnego modelu "
        "językowego; ujawnia dodatnie i ujemne przesłanki oraz pozwala nie wybierać "
        "nadmiernie pewnej trasy przy konflikcie domen."
    )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return data


class IntentFeatureEngine:
    """Kontekstowy ranking dla kolizyjnych intencji rozmownych.

    Warstwa jest celowo wąska: rozstrzyga miejsca, w których pojedynczy token
    (np. ``generator``) może należeć do zupełnie różnych domen. Nie próbuje
    zastąpić całego DialogueIntentClassifier ani modelu LLM.
    """

    PACKAGE_PATTERNS = (
        r"\bpacz\w*\b",
        r"\bzip\w*\b",
        r"\barchiw\w*\b",
        r"\bmanifest\w*\b",
        r"\bcrc\b",
        r"\bsha(?:256)?\b",
        r"\brozpak\w*\b",
        r"\bwypak\w*\b",
        r"\bbootstrap\w*\b",
    )
    GENERATOR_PATTERN = r"\bgenerator\w*\b"
    CREATIVE_PATTERNS = (
        r"\bpiosenk\w*\b",
        r"\blyrics\b",
        r"\bzwrotk\w*\b",
        r"\brefren\w*\b",
        r"\bchorus\b",
        r"\bverse\b",
        r"\bwiersz\w*\b",
        r"\bopowiad\w*\b",
        r"\bmuzyk\w*\b",
        r"\bobraz\w*\b",
        r"\bgrafik\w*\b",
        r"\bvideo\w*\b",
        r"\bfilm\w*\b",
        r"\bsuno\b",
        r"\bmidjourney\b",
        r"\bstable diffusion\b",
        r"\bdall-?e\b",
    )
    CREATIVE_ACTION_PATTERNS = (
        r"\bnapisz\w*\b",
        r"\bstworz\w*\b",
        r"\bprzygotuj\w*\b",
        r"\bprzerob\w*\b",
        r"\bzredaguj\w*\b",
        r"\bsformatuj\w*\b",
        r"\bzachowaj\w*\b",
        r"\bnie zmieniaj\b",
        r"\bbez zmian\b",
    )
    UPDATE_ACTION_PATTERNS = (
        r"\bnapraw\w*\b",
        r"\bpopraw\w*\b",
        r"\bwdroz\w*\b",
        r"\bwprowadz\w*\b",
        r"\bzaktualizuj\w*\b",
        r"\bzaimplementuj\w*\b",
        r"\butworz\w* branch\b",
        r"\bnapisz\w* patch\b",
        r"\bpelny patch\b",
    )
    STATUS_PATTERNS = (
        r"\bjak tam\b",
        r"\bco wyszlo\b",
        r"\bjaki wynik\b",
        r"\bstatus\w*\b",
        r"\budal\w*\b",
        r"\bdzial\w*\b",
        r"\bpo now\w*\b",
        r"\bsprawdzil\w*\b",
        r"\btest\w* przeszed\w*\b",
    )
    PROMPT_PATTERN = r"\bprompt\w*\b"
    STRUCTURED_CREATIVE_PATTERN = re.compile(r"\[(?:chorus|verse|bridge|refren|zwrotka)[^\]]*\]", re.IGNORECASE)

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "").strip().lower())

    @staticmethod
    def fold(text: str) -> str:
        return (text or "").translate(DIACRITIC_MAP).lower()

    @staticmethod
    def _matches(text: str, patterns: tuple[str, ...]) -> list[str]:
        return [pattern for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE | re.UNICODE)]

    @staticmethod
    def _clamp(score: float) -> float:
        return round(max(0.0, min(1.0, score)), 4)

    def analyse(
        self,
        text: str,
        *,
        speech_act: str = "unknown",
        previous_text: str | None = None,
    ) -> IntentDecisionFrame:
        normalized = self.normalize(text)
        folded = self.fold(normalized)
        tokens = TOKEN_RE.findall(folded)
        previous_folded = self.fold(self.normalize(previous_text or ""))

        package_hits = self._matches(folded, self.PACKAGE_PATTERNS)
        generator_present = bool(re.search(self.GENERATOR_PATTERN, folded, flags=re.UNICODE))
        creative_hits = self._matches(folded, self.CREATIVE_PATTERNS)
        creative_action_hits = self._matches(folded, self.CREATIVE_ACTION_PATTERNS)
        update_action_hits = self._matches(folded, self.UPDATE_ACTION_PATTERNS)
        status_hits = self._matches(folded, self.STATUS_PATTERNS)
        prompt_present = bool(re.search(self.PROMPT_PATTERN, folded, flags=re.UNICODE))
        structured_creative = bool(self.STRUCTURED_CREATIVE_PATTERN.search(text or ""))
        question_like = speech_act == "question" or "?" in (text or "") or bool(status_hits)
        previous_package_context = bool(previous_folded and self._matches(previous_folded, self.PACKAGE_PATTERNS))

        domains: list[str] = []
        if package_hits:
            domains.append("package_runtime")
        if creative_hits or prompt_present or structured_creative:
            domains.append("creative")
        if update_action_hits:
            domains.append("system_update")
        if generator_present:
            domains.append("generator_ambiguous_token")

        package_positive: list[str] = []
        package_negative: list[str] = []
        package_score = 0.0
        if package_hits:
            package_score += 0.38
            package_positive.append("package_or_runtime_domain")
        if generator_present and package_hits:
            package_score += 0.24
            package_positive.append("generator_grounded_by_package_domain")
        if question_like:
            package_score += 0.18
            package_positive.append("status_or_question_act")
        if status_hits:
            package_score += 0.15
            package_positive.append("status_followup_marker")
        if previous_package_context:
            package_score += 0.08
            package_positive.append("previous_turn_package_context")
        if update_action_hits:
            package_score -= 0.35
            package_negative.append("explicit_update_execution_action")
        if creative_hits and not package_hits:
            package_score -= 0.40
            package_negative.append("creative_domain_without_package_context")

        creative_positive: list[str] = []
        creative_negative: list[str] = []
        creative_score = 0.0
        if creative_hits:
            creative_score += 0.35
            creative_positive.append("creative_domain")
        if creative_action_hits:
            creative_score += 0.28
            creative_positive.append("creative_transformation_action")
        if prompt_present:
            creative_score += 0.22
            creative_positive.append("explicit_prompt_marker")
        if structured_creative:
            creative_score += 0.28
            creative_positive.append("structured_creative_material")
        if generator_present and (creative_hits or prompt_present):
            creative_score += 0.18
            creative_positive.append("generator_grounded_by_creative_domain")
        if package_hits:
            creative_score -= 0.55
            creative_negative.append("package_runtime_domain_conflict")
        if question_like and not creative_action_hits and not structured_creative:
            creative_score -= 0.12
            creative_negative.append("status_question_without_creative_action")
        if generator_present and not (creative_hits or prompt_present):
            creative_score -= 0.20
            creative_negative.append("ungrounded_generator_token")

        update_positive: list[str] = []
        update_negative: list[str] = []
        update_score = 0.0
        if update_action_hits:
            update_score += 0.52
            update_positive.append("explicit_execution_action")
        if package_hits or any(token in tokens for token in ("nlp", "kod", "runtime", "system", "jazn")):
            update_score += 0.20
            update_positive.append("system_or_package_target")
        if any(token in tokens for token in ("patch", "branch", "testy", "test")):
            update_score += 0.18
            update_positive.append("implementation_artifact")
        if question_like and not update_action_hits:
            update_score -= 0.25
            update_negative.append("question_without_execution_verb")

        ordinary_score = 0.12
        ordinary_positive = ["fallback_candidate"]
        if not (package_hits or creative_hits or update_action_hits or prompt_present):
            ordinary_score += 0.23
            ordinary_positive.append("no_specialized_domain")

        candidates = [
            IntentCandidate(
                "package_runtime_status_question",
                self._clamp(package_score),
                package_positive,
                package_negative,
                ["package_runtime"],
            ),
            IntentCandidate(
                "creative_text_formatting",
                self._clamp(creative_score),
                creative_positive,
                creative_negative,
                ["creative"],
            ),
            IntentCandidate(
                "system_update_execution_request",
                self._clamp(update_score),
                update_positive,
                update_negative,
                ["system_update"],
            ),
            IntentCandidate(
                "ordinary_conversation",
                self._clamp(ordinary_score),
                ordinary_positive,
                [],
                ["ordinary"],
            ),
        ]
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.intent))
        top = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        margin = self._clamp(top.score - (runner_up.score if runner_up else 0.0))
        conflicts: list[str] = []
        if generator_present and package_hits and (creative_hits or prompt_present):
            conflicts.append("generator_has_package_and_creative_context")
        elif generator_present and not package_hits and not (creative_hits or prompt_present):
            conflicts.append("generator_without_domain_grounding")
        if update_action_hits and question_like and status_hits:
            conflicts.append("status_question_and_execution_action_overlap")

        ambiguous = top.score < 0.56 or margin < 0.12 or bool(conflicts and margin < 0.22)
        abstain_reason: str | None = None
        if top.score < 0.56:
            abstain_reason = "no_candidate_reached_minimum_score"
        elif margin < 0.12:
            abstain_reason = "top_candidates_too_close"
        elif conflicts and margin < 0.22:
            abstain_reason = "cross_domain_lexical_conflict"

        return IntentDecisionFrame(
            schema_version=SCHEMA_VERSION,
            normalized_text=normalized,
            folded_text=folded,
            tokens=tokens,
            domains=domains,
            candidates=candidates,
            top_intent=top.intent,
            top_score=top.score,
            runner_up_intent=runner_up.intent if runner_up else None,
            runner_up_score=runner_up.score if runner_up else 0.0,
            decision_margin=margin,
            ambiguous=ambiguous,
            abstain_reason=abstain_reason,
            lexical_conflicts=conflicts,
        )
