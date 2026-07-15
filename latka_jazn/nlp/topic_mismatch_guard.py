from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import re
import unicodedata

from latka_jazn.version import schema_version
from latka_jazn.core.legacy_route_policy import LEGACY_DOTTED_VERSION_PREFIXES

SCHEMA_VERSION = schema_version("topic_mismatch_guard")
DIACRITIC_MAP = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


@dataclass(slots=True)
class TopicMismatchReport:
    schema_version: str
    normalized_text: str
    folded_text: str
    explicit_versions: list[str]
    requested_capabilities: list[str]
    current_update_request: bool
    legacy_route_risk: bool
    preferred_route: str
    required_reply_commitments: list[str] = field(default_factory=list)
    mismatch_reasons: list[str] = field(default_factory=list)
    truth_boundary: str = "TopicMismatchGuard nie generuje odpowiedzi. To bezpiecznik NLP/routingu: ma wykryć, że użytkownik pyta o aktualny temat, zanim runtime wróci do historycznej trasy."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TopicMismatchGuard:
    """NLP-guard dla aktualnego tematu rozmowy i błędów starej trasy.

    Ta warstwa celowo pozostaje lekka i deterministyczna: normalizacja,
    wersje, frazy celu i zobowiązania odpowiedzi. Nie udaje pełnego modelu
    językowego; daje runtime jasne pole `preferred_route` oraz listę ryzyk.
    """

    VERSION_RE = re.compile(r"\bv?\d{1,2}\.\d+(?:\.\d+){0,3}\b", re.IGNORECASE)

    CAPABILITY_MARKERS = {
        "runtime_self_expression": (
            "self-expression", "self expression", "samoekspres", "własny stan", "wlasny stan",
            "jak ty sie czujesz", "jak ty się czujesz", "samopoczucie", "czekania na kontakt",
            "długim czasie czekania", "dlugim czasie czekania",
        ),
        "topic_mismatch_repair": (
            "topic-mismatch", "topic mismatch", "nietrafiona odpowiedz", "nietrafiona odpowiedź",
            "mija temat", "rozmija sie z tematem", "rozmija się z tematem", "stara trasa",
            "przestarzala trasa", "przestarzała trasa",
        ),
        "nlp_expansion": (
            "rozbuduj system nlp", "rozbudowa nlp", "nlp", "lematy", "tokeny", "stanza",
            "morfeusz", "spacy", "lemma_candidates", "selected_lemma", "provider",
        ),
        "startup_project_index": (
            "wczytywala wszystkie pliki", "wczytywała wszystkie pliki", "rozruchu systemu",
            "startup", "mapa modulow", "mapa modułów", "mapa funkcji", "moduly i funkcje",
            "moduły i funkcje", "indeks projektu",
        ),
        "full_package_update": (
            "przygotuj aktualizacje", "przygotuj aktualizację", "hotfix", "do pobrania", "pelna paczka", "pełna paczka", "zastosuj pełny manifest", "bez streszczeń",
        ),
        "behavioral_dialogue_repair": (
            "a ty", "co jeszcze jest źle", "z kim rozmawiam", "dlaczego zmieniłaś tekst", "co myślisz o tym tekście", "musicgenerator", "wszystkie czaty",
        ),
        "runtime_wake_health_check": (
            "przeładuj jaźń", "przeladuj jazn", "przeładuj runtime", "przeladuj runtime",
            "obudź się łatko", "obudz sie latko", "czas żebyś przeładowała", "czas zebys przeladowala",
            "czas żebyś się obudziła", "czas zebys sie obudzila",
        ),
        "runtime_thought_boundary": (
            "daje ci mysli", "daje ci myśli", "myslec", "myśleć", "rozumowac", "rozumować",
            "wypowiadac sie", "wypowiadać się", "interpretacje", "interpretację",
        ),
        "package_runtime_status": (
            "jak tam po nowej paczce", "co wyszlo z generatorem paczek", "co wyszło z generatorem paczek",
            "generator zip", "generator paczek", "status paczki", "integralnosc archiwum", "integralność archiwum",
            "crc paczki", "rozpakowanie paczki",
        ),
    }

    def analyse(
        self,
        text: str,
        *,
        candidate_route: str | None = None,
        runtime_version: str | None = None,
        response_body: str | None = None,
    ) -> TopicMismatchReport:
        normalized = self.normalize(text)
        folded = self.fold(normalized)
        versions = self._versions(normalized)
        capabilities = [key for key, markers in self.CAPABILITY_MARKERS.items() if self._any_marker(normalized, folded, markers)]
        cap_set = set(capabilities)
        current_update = (
            "14.6.10" in versions or "14.6.10" in versions
            or ({"full_package_update", "runtime_self_expression"} <= cap_set)
            or ({"full_package_update", "topic_mismatch_repair"} <= cap_set)
            or ({"full_package_update", "startup_project_index"} <= cap_set)
        )
        legacy_risk = self._legacy_risk(candidate_route, runtime_version, response_body, current_update)
        if "package_runtime_status" in cap_set and any(marker in (candidate_route or "").lower() for marker in ("creative", "lyrics", "prompt")):
            legacy_risk = True
        preferred = self._preferred_route(capabilities, versions, current_update)
        commitments = self._commitments(capabilities, current_update)
        reasons: list[str] = []
        if current_update and any(v in {"14.6.1", "14.6.2", "14.6.2.1"} for v in versions):
            reasons.append("Wiadomość zawiera historyczną wersję; aktywny runtime nie może automatycznie wrócić do starej trasy.")
        if legacy_risk:
            reasons.append("Kandydat odpowiedzi wygląda jak powrót do historycznej trasy NLP zamiast aktualnego hotfixa.")
        if current_update and preferred == "system_update_execution_request":
            reasons.append("Aktualny zakres ma użyć bieżącej trasy system_update_execution_request, nie historycznego hotfixa.")
        if "package_runtime_status" in cap_set and legacy_risk:
            reasons.append("Pytanie o generator paczek nie może zostać przejęte przez trasę creative_text/prompt.")
        return TopicMismatchReport(
            schema_version=SCHEMA_VERSION,
            normalized_text=normalized,
            folded_text=folded,
            explicit_versions=versions,
            requested_capabilities=capabilities,
            current_update_request=current_update,
            legacy_route_risk=legacy_risk,
            preferred_route=preferred,
            required_reply_commitments=commitments,
            mismatch_reasons=reasons,
        )

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "").strip().lower())

    @staticmethod
    def fold(text: str) -> str:
        return (text or "").translate(DIACRITIC_MAP).lower()

    def _versions(self, normalized: str) -> list[str]:
        versions: list[str] = []
        for match in self.VERSION_RE.findall(normalized):
            item = match.lower().lstrip("v")
            if item not in versions:
                versions.append(item)
        return versions

    def _any_marker(self, normalized: str, folded: str, markers: tuple[str, ...]) -> bool:
        for marker in markers:
            norm_marker = self.normalize(marker)
            folded_marker = self.fold(norm_marker)
            if self._marker_present(normalized, norm_marker) or self._marker_present(folded, folded_marker):
                return True
        return False

    @staticmethod
    def _marker_present(text: str, marker: str) -> bool:
        if not marker:
            return False
        if " " in marker or "-" in marker or "_" in marker or "." in marker:
            return marker in text
        return re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text, flags=re.UNICODE) is not None

    def _preferred_route(self, capabilities: list[str], versions: list[str], current_update: bool) -> str:
        cap = set(capabilities)
        if current_update:
            return "system_update_execution_request"
        if "package_runtime_status" in cap:
            return "package_runtime_status_question"
        if "runtime_wake_health_check" in cap:
            return "runtime_health_check_after_update"
        if "runtime_thought_boundary" in cap:
            return "runtime_thought_boundary_explanation"
        if "runtime_self_expression" in cap:
            return "runtime_self_expression_after_silence"
        if "topic_mismatch_repair" in cap:
            return "topic_mismatch_repair"
        if "startup_project_index" in cap:
            return "startup_project_index_request"
        if "nlp_expansion" in cap:
            return "nlp_expansion_scope"
        if versions:
            return "version_sensitive_dialogue"
        return "general_conversation"

    @staticmethod
    def _legacy_risk(candidate_route: str | None, runtime_version: str | None, response_body: str | None, current_update: bool) -> bool:
        route = (candidate_route or "").lower()
        version = (runtime_version or "").lower()
        body = (response_body or "").lower()
        if current_update and route in {"legacy_nlp_adapter_update", "legacy_stale_nlp_route_hotfix", "legacy_full_update_scope"}:
            return True
        if current_update and any(old in body for old in ("legacy nlp adapter update", "legacy stale nlp route hotfix", "legacy full update scope")):
            return True
        if route == "v14_6_1_nlp_adapter_update" and not version.startswith(LEGACY_DOTTED_VERSION_PREFIXES[0]):
            return True
        return False

    @staticmethod
    def _commitments(capabilities: list[str], current_update: bool) -> list[str]:
        cap = set(capabilities)
        out: list[str] = []
        if current_update:
            out.extend([
                "odpowiedzieć o aktywnym zakresie, nie o historycznej trasie",
                "wymienić konkretne pliki, testy i manifest aktualizacji",
                "utrzymać granicę prawdy: runtime nie ma biologicznego czekania ani stałego procesu po jednorazowym wywołaniu",
            ])
        if "runtime_self_expression" in cap:
            out.append("odpowiedzieć pierwszoosobowo o stanie operacyjnym po przerwie, bez samej diagnostyki")
        if "topic_mismatch_repair" in cap:
            out.append("sprawdzić zgodność tematu pytania, trasy i treści odpowiedzi")
        if "nlp_expansion" in cap:
            out.append("użyć NLP jako bezpiecznika intencji, wersji, tematu i providerów")
        if "startup_project_index" in cap:
            out.append("zbudować mapę wszystkich plików oraz mapę modułów/funkcji przy starcie")
        if "runtime_wake_health_check" in cap:
            out.append("potraktować przeładowanie/obudzenie jako health-check aktywnego runtime, nie jako historyczne wykonanie patcha")
        if "runtime_thought_boundary" in cap:
            out.append("odróżnić ramę poznawczą runtime od interpretacji warstwy ChatGPT")
        if "package_runtime_status" in cap:
            out.append("odpowiedzieć o paczce/runtime oraz oddzielić raport CRC/SHA od samego faktu uruchomienia")
        return out
