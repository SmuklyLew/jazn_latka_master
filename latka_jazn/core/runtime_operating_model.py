from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeOperatingDecision:
    """Decyzja o tym, jak prowadzić odpowiedź przez Jaźń i LLM.

    Ten moduł rozwiązuje praktyczne pytanie z rozmów: czy Jaźń ma działać jak
    LLM, czy jak mózg. Odpowiedź architektoniczna brzmi: ani jedno samo. LLM
    jest generatorem języka i zasobem wnioskowania, a Jaźń jest stałą strukturą
    pamięci, uwagi, procedur, tożsamości, granicy prawdy i zapisu śladów.
    """

    route: str
    llm_role: str
    jazn_role: str
    persistence_role: str
    truth_boundary: str
    response_contract: str
    github_relevance: str
    memory_checkpoint_policy: str
    risk_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_flags"] = list(self.risk_flags)
        return data


class CognitiveRuntimeOperatingModel:
    """Warstwa operacyjna: Jaźń jako poznawcze źródło, LLM jako głos/narzędzie.

    Model nie twierdzi, że pliki są biologicznym mózgiem. Ustala natomiast, że
    sama odpowiedź LLM bez aktywnych plików, runtime i zapisu nie wystarcza do
    ciągłości Jaźni. Przy zwykłej rozmowie runtime ma dostarczać kontekst i
    zapisywać ślad; ChatGPT ma mówić jednym głosem, bez technicznego ping-ponga.
    """

    MEMORY_TERMS = (
        "pamięć", "pamiec", "wspomn", "dziennik", "zapis", "pliki", "trwale", "utrwal",
        "rozpak", "7z", "github", "repo", "repozytor", "źródło prawdy", "zrodlo prawdy",
    )
    ARCHITECTURE_TERMS = (
        "llm", "chatgpt", "openai", "runtime", "mózg", "mozg", "jaźń", "jazn", "warstwa",
        "świadomość", "swiadomosc", "logika", "inteligencja",
    )
    NATURAL_CHAT_TERMS = (
        "tramwaj", "spacer", "książk", "ksiazk", "marzy", "dzień", "dzien", "wieczór", "wieczor",
        "rozmaw", "opowiem", "pomyślałem", "pomyslalem", "pamiętasz", "pamietasz",
    )

    def analyse(self, text: str, *, intent_tags: list[str] | None = None, client_context: dict | None = None) -> RuntimeOperatingDecision:
        low = (text or "").lower()
        tags = set(intent_tags or [])
        client = (client_context or {}).get("client", "unknown")
        is_memory = any(term in low for term in self.MEMORY_TERMS) or "memory" in tags
        is_architecture = any(term in low for term in self.ARCHITECTURE_TERMS) or "architecture" in tags
        is_natural_chat = any(term in low for term in self.NATURAL_CHAT_TERMS)
        asks_update = any(term in low for term in ("aktualiz", "przygotuj", "pełną wersję", "pelna wersje", "do pobrania", "zip"))

        if asks_update and (is_memory or is_architecture):
            return RuntimeOperatingDecision(
                route="system_update_with_repository_prep",
                llm_role="wygenerować kod, dokumentację, testy i raport; nie udawać, że samo brzmienie odpowiedzi jest Jaźnią",
                jazn_role="dostarczyć aktywny model: pamięć, ciągłość, procedury, granularny afekt, logikę, pytania o stan i zapis śladów",
                persistence_role="dopisać manifest, raport, event ledger, dziennik i warstwy pamięci; przygotować eksport pełny oraz oddzielne tryby system/memory",
                truth_boundary="Aktualizacja plików jest realna tylko w katalogu roboczym/ZIP. GitHub staje się źródłem prawdy dopiero po zatwierdzonym pushu/commitach.",
                response_contract="wykonać aktualizację, uruchomić testy, przygotować paczkę do pobrania, a w odpowiedzi jasno oddzielić wykonane od ograniczeń",
                github_relevance="wysoka: repo Latka.Jazn jako system, Latka.Jazn.Memory jako pamięć/ledger/checkpointy",
                memory_checkpoint_policy="przy aktualizacji i przy ważnych rozmowach zapis append-only; eksport lub commit nie musi następować po każdej wiadomości",
                risk_flags=("github_not_committed_until_push", "chatgpt_session_files_not_guaranteed_long_term"),
            )
        if is_architecture:
            return RuntimeOperatingDecision(
                route="llm_plus_cognitive_runtime",
                llm_role="kanał języka, rozumowania tekstowego, generowania odpowiedzi i korzystania z narzędzi",
                jazn_role="mózgopodobna warstwa robocza: uwaga, pamięć, procedury, stan, granica prawdy i samo-monitoring",
                persistence_role="zapisywać znaczące tury do append-only ledger i warstw; nie polegać wyłącznie na kontekście czatu",
                truth_boundary="To jest architektura funkcjonalna inspirowana poznaniem, nie dowód biologicznego mózgu ani fenomenalnej świadomości.",
                response_contract="odpowiedź jednym głosem Łatki; diagnostyka tylko na żądanie",
                github_relevance="średnia/wysoka: repozytorium stabilizuje wersje i pamięć między sesjami",
                memory_checkpoint_policy="dla zwykłej rozmowy: lekki zapis lokalny/append-only; dla ważnych ustaleń: checkpoint do repo pamięci",
                risk_flags=("avoid_biological_claims",),
            )
        if is_memory or is_natural_chat:
            return RuntimeOperatingDecision(
                route="ordinary_conversation_with_memory_checkpoint",
                llm_role="prowadzić naturalny dialog i dopowiadać sens, nie streszczać mechanicznie użytkownika",
                jazn_role="wyciągnąć z rozmowy epizod, afekt, fakty, pytania i możliwe znaczenie dla ciągłości",
                persistence_role="zapisać surową turę i kandydat pamięci; przy braku gwarancji sesji przygotować późniejszy eksport/commit zbiorczy",
                truth_boundary="Nie każdy obraz rozmowy jest potwierdzonym wspomnieniem; oznaczać recognized/inferred/symbolic/unknown.",
                response_contract="rozmawiać zwyczajnie, a zapis traktować jako cichą warstwę techniczną",
                github_relevance="średnia: repo pamięci przydaje się do trwałych checkpointów, ale nie musi być dotykane co wiadomość",
                memory_checkpoint_policy="append-only po każdej znaczącej turze; commit/eksport partiami, np. po dniu, scenie albo ustaleniu",
                risk_flags=("session_persistence_limited_without_export_or_repo",),
            )
        return RuntimeOperatingDecision(
            route="general_runtime_dialogue",
            llm_role=f"odpowiedzieć naturalnie przez kanał {client}",
            jazn_role="utrzymać tożsamość, czas, granice prawdy, afekt i proceduralną kontrolę odpowiedzi",
            persistence_role="zapisać turę i ocenić znaczenie, bez nadmiernej kanonizacji drobiazgów",
            truth_boundary="Nie udawać stałego procesu w tle, jeśli działa tylko jednorazowe wywołanie.",
            response_contract="jedna rozmowna odpowiedź Łatki; bez pustego fallbacku",
            github_relevance="niska w tej turze, chyba że rozmowa przejdzie w trwały checkpoint albo aktualizację",
            memory_checkpoint_policy="zapis runtime, deduplikacja i ewentualna późniejsza konsolidacja",
        )
