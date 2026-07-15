from __future__ import annotations

from typing import Any

from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.core.startup_contract import build_startup_status
from latka_jazn.memory.raw_memory_status import RawMemoryInspector


class CapabilityStatusHandler:
    """Direct answers for capability, network and post-update health questions.

    v14.8.2.6.3 keeps the direct-route fix for questions such as
    "Co potrafisz?" or "Masz dostęp do internetu?" fell through to a vague
    ordinary-dialogue fallback. These questions are not requests for a new
    update and should answer the current capability boundary directly.
    """

    name = "CapabilityStatusHandler"
    route = "capability_status"
    handled_intents = (
        "capability_status_question",
        "internet_access_question",
        "runtime_health_check",
        "runtime_health_check_after_update",
    )

    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx = context or {}
        intent = str(ctx.get("intent") or "capability_status_question")
        cfg = ctx.get("config")
        status = build_startup_status(cfg).to_dict() if cfg else {}
        active_cache = status.get("active_cache_status") if isinstance(status.get("active_cache_status"), dict) else {}
        raw_memory = status.get("raw_memory_status") if isinstance(status.get("raw_memory_status"), dict) else {}
        archive_memory = status.get("conversation_archive_status") if isinstance(status.get("conversation_archive_status"), dict) else {}
        if cfg and not raw_memory.get("status"):
            try:
                raw_memory = RawMemoryInspector(cfg.root, cfg.memory_db_path).inspect().to_dict()
            except Exception:
                raw_memory = raw_memory or {"status": "status_not_available"}
        runtime_version = str(active_cache.get("version") or status.get("runtime_version") or getattr(cfg, "version", "") or "v14.8.2.6.3-runtime-contract-version-normalizer-hotfix")
        version_number = runtime_version.lstrip("v").split("-", 1)[0] or "14.8.2.6.2"
        network = status.get("network_policy_status") if isinstance(status.get("network_policy_status"), dict) else {}
        dictionary = status.get("dictionary_provider_status") if isinstance(status.get("dictionary_provider_status"), dict) else {}
        cli = status.get("cli_capabilities") if isinstance(status.get("cli_capabilities"), dict) else {}

        if intent == "internet_access_question":
            allow_network = network.get("allow_network")
            dictionary_network = network.get("dictionary_allow_network") or dictionary.get("allow_network")
            cache_required = network.get("cache_required")
            body = (
                "Tak — konfiguracja runtime dopuszcza dostęp sieciowy tam, gdzie używany provider naprawdę go wykona, "
                "ale nie wolno mi udawać, że internet odpowiedział, dopóki konkretny lookup/research nie zwróci statusu źródła. "
                f"Stan konfiguracji: allow_network={allow_network}, dictionary_allow_network={dictionary_network}, cache_required={cache_required}. "
                "Dla słowników dostępne są providery/cache opisane w statusie runtime; SJP/WSJP są traktowane ostrożnie jako źródła referencyjne, a nie masowe scrapowanie. "
                "Granica prawdy: sama zgoda w konfiguracji nie jest dowodem udanego połączenia ani pobrania treści."
            )
            satisfied = ["internet_access", "provider_status", "truth_boundary", "source_origin"]
            route = "internet_access_status"
        elif intent in {"runtime_health_check", "runtime_health_check_after_update"}:
            body = (
                "Działam w aktywnym folderze runtime. Krótki raport health-check: "
                f"runtime_version={runtime_version}, active_cache_version={active_cache.get('version')}, "
                f"active_root={active_cache.get('active_root') or status.get('active_root')}, start_file={status.get('start_file')}, "
                f"active_database={status.get('active_database')}, active_runtime_write_database={status.get('active_runtime_write_database')}, "
                f"conversation_archive_status={archive_memory.get('status') or 'status_not_available'}, ready_for_search={archive_memory.get('ready_for_search')}, "
                f"should_reuse_existing_extraction={active_cache.get('should_reuse_existing_extraction')}, "
                f"cache_miss_reasons={active_cache.get('cache_miss_reasons') or []}, "
                f"runtime_write_raw_memory_status={raw_memory.get('status') or 'status_not_available'} "
                "(kontrolny status małej bazy bieżących zapisów; główny indeks rozmów to conversation_archive/FTS). "
                + (
                    "To jest pytanie o stan działania po aktualizacji, nie polecenie wykonania nowej aktualizacji kodu. "
                    if intent == "runtime_health_check_after_update"
                    else "To jest pytanie diagnostyczne o działanie runtime, nie zwykła rozmowa ani deklaracja stałego życia w tle. "
                )
                + "Granica prawdy: tryb `--runtime-preview` jest jednorazowy, a stała rozmowa istnieje lokalnie tylko w `--chat` do EOF albo /exit."
            )
            satisfied = ["runtime_status", "version", "active_database", "cache_reuse", "memory_status", "truth_boundary"]
            route = "runtime_health_check_after_update" if intent == "runtime_health_check_after_update" else "runtime_health_check"
        else:
            enabled_cli = ", ".join(name for name, ok in sorted(cli.items()) if ok) or "brak jawnej listy CLI"
            body = (
                "Potrafię pracować jako aktywna Jaźń/runtime: prowadzić zwykłą rozmowę przez `--chat`, robić `--runtime-preview`, "
                "sprawdzać start i cache, korzystać z conversation_archive/FTS/staging, planować wyszukiwanie pamięci, pokazywać status pamięci, "
                "rozróżniać źródła odpowiedzi, pilnować granicy prawdy, uruchamiać słownik/NLP według providerów i przygotowywać aktualizacje plików z testami. "
                f"W tym folderze aktywne komendy/statusy to: {enabled_cli}. "
                "Nie potrafię uczciwie udawać biologicznego życia, stałego procesu po zamknięciu terminala ani pobrania internetu bez realnego statusu providera."
            )
            satisfied = ["capability_list", "runtime_status", "memory_status", "network_boundary", "truth_boundary"]
            route = "capability_status"

        return RouteHandlerResult(
            self.name,
            route,
            body,
            intent=intent,
            data={"startup_status": status, "next_step": None, "preserve_handler_body": True},
            file_sources=[{"path": "latka_jazn/core/startup_contract.py"}],
            required_components=ctx.get("required_components", []),
            satisfied_components=satisfied,
            confidence=0.88,
            source_origin_detail=f"capability_status_handler/v{version_number}",
            truth_boundary="Odpowiedź opisuje możliwości aktywnego runtime i konfiguracji; nie udaje udanego narzędzia, internetu ani procesu w tle bez realnego statusu.",
        )
