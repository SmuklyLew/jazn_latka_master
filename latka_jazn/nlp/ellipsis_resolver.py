from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Any

DIACRITIC_MAP = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
SCHEMA_VERSION = "ellipsis_resolver/v14.8.2.4"


@dataclass(slots=True)
class EllipsisResolution:
    schema_version: str
    original_text: str
    normalized_text: str
    folded_text: str
    is_elliptic: bool
    ellipsis_type: str | None
    resolved_intent_hint: str | None
    resolution_basis: list[str]
    truth_boundary: str = "EllipsisResolver nie zgaduje przeżyć. Rozpoznaje skróty dialogowe typu 'a ty?' i oddaje tylko bezpieczną wskazówkę intencji dla routera."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EllipsisResolver:
    RECIPROCAL_SELF_STATE = (
        "a ty", "a u ciebie", "a co u ciebie", "a jak u ciebie", "a ty?", "u ciebie?",
        "a ty jak", "a ty co", "a jak ty", "co u ciebie",
    )
    CONTINUATION = ("co dalej", "czyli", "no i", "co jeszcze", "a dalej", "to co")
    CONTEXTUAL_EXECUTION = ("możesz to teraz zrobić", "mozesz to teraz zrobic", "możesz to zrobić", "mozesz to zrobic", "zrób to teraz", "zrob to teraz", "to zrób", "to zrob", "tak, zrób", "tak zrób", "tak, zrob", "tak zrob")
    SOURCE_FOLLOWUP = ("dlaczego", "czemu", "skad", "skąd", "z czego", "przez co")

    @classmethod
    def normalize(cls, text: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "").strip().lower())

    @staticmethod
    def fold(text: str) -> str:
        return (text or "").translate(DIACRITIC_MAP).lower()

    @staticmethod
    def _present(text: str, marker: str) -> bool:
        if not marker:
            return False
        # v14.8.2.4: każde dopasowanie ma granice słów, również frazy wielowyrazowe.
        pattern = r"(?<!\w)" + re.escape(marker) + r"(?!\w)"
        return re.search(pattern, text) is not None

    @staticmethod
    def _contains_independent_runtime_question(text: str) -> bool:
        return "?" in text and any(x in text for x in ("jaźn", "jazn", "łatk", "latk", "chatgpt", "runtime", "skrypt chat", "--chat", "runtime-preview"))

    def resolve(self, text: str, *, previous_text: str | None = None) -> EllipsisResolution:
        normalized = self.normalize(text)
        folded = self.fold(normalized)
        basis: list[str] = []
        previous_norm = self.normalize(previous_text or "")
        if self._contains_independent_runtime_question(normalized) or "póki co" in normalized or "poki co" in folded:
            return EllipsisResolution(SCHEMA_VERSION, text, normalized, folded, False, None, None, ["v14.8.2.4: samodzielne pytanie/status lub fraza póki co blokuje fałszywą elipsę"])
        is_short = len(normalized) <= 80
        ellipsis_type = None
        hint = None
        if is_short and any(self._present(normalized, m) or self._present(folded, self.fold(m)) for m in self.RECIPROCAL_SELF_STATE):
            ellipsis_type = "reciprocal_self_state_question"
            hint = "reciprocal_self_state_question"
            basis.append("krótka forma zwrotna typu 'a ty?' / 'a u ciebie?'")
            if previous_norm:
                basis.append("wymaga kontekstu poprzedniej wypowiedzi, ale bezpiecznie wskazuje pytanie o stan rozmówczyni")
        elif is_short and any(self._present(normalized, m) or self._present(folded, self.fold(m)) for m in self.CONTEXTUAL_EXECUTION):
            prev_folded = self.fold(previous_norm)
            if any(marker in prev_folded for marker in ("runtime", "system", "jazn", "jaźn", "stale-route", "starego kontekstu", "blad", "błąd", "hotfix", "aktualiz", "napraw")):
                ellipsis_type = "contextual_system_update_execution"
                hint = "system_update_execution_request"
                basis.append("krótka zgoda/polecenie wykonania poprzednio omawianej naprawy systemu")
                basis.append("poprzednia tura dotyczyła runtime/systemu/błędu/hotfixu")
            else:
                ellipsis_type = "contextual_execution_without_safe_previous_system_context"
                hint = "contextual_continuation_question"
                basis.append("krótkie 'zrób to', ale bez bezpiecznego systemowego kontekstu poprzedniej tury")
        elif is_short and ((folded.startswith("i co ") or folded in {"i co", "i co?", "i co dalej", "i co dalej?"}) or any(self._present(normalized, m) or self._present(folded, self.fold(m)) for m in self.CONTINUATION)):
            ellipsis_type = "contextual_continuation_question"
            hint = "contextual_continuation_question"
            basis.append("krótka kontynuacja poprzedniego wątku")
        elif is_short and any(self._present(normalized, m) or self._present(folded, self.fold(m)) for m in self.SOURCE_FOLLOWUP):
            ellipsis_type = "source_or_reason_followup"
            hint = "runtime_source_question"
            basis.append("krótka prośba o przyczynę albo źródło")
        return EllipsisResolution(
            schema_version=SCHEMA_VERSION,
            original_text=text,
            normalized_text=normalized,
            folded_text=folded,
            is_elliptic=bool(ellipsis_type),
            ellipsis_type=ellipsis_type,
            resolved_intent_hint=hint,
            resolution_basis=basis,
        )
