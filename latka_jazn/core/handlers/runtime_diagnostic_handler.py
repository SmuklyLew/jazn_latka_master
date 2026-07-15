from __future__ import annotations
from typing import Any
import json
from latka_jazn.core.route_handler_base import RouteHandlerResult
from latka_jazn.core.startup_contract import build_startup_status

class RuntimeDiagnosticHandler:
    name = "RuntimeDiagnosticHandler"
    route = "runtime_diagnostic"
    handled_intents = ('runtime_behavior_diagnostic_request', 'system_diagnostic_question')
    def handle(self, text: str, context: dict[str, Any] | None = None) -> RouteHandlerResult:
        ctx=context or {}; cfg=ctx.get('config'); intent=ctx.get('intent','system_diagnostic_question')
        status=build_startup_status(cfg).to_dict() if cfg else {}
        low=(text or '').lower()
        if intent == 'module_inventory_request':
            body=(
                "Mam moduły rozmowy i kontroli: `dialogue_intent_classifier.py` rozpoznaje intencję, `route_registry.py` wybiera trasę, "
                "`route_handler_dispatcher.py` uruchamia handler, `ordinary_dialogue_handler.py` obsługuje zwykłą rozmowę, `self_state_handler.py` odpowiada na pytania o mój stan, "
                "`runtime_answer_validator.py` blokuje znane złe odpowiedzi, `runtime_response_synthesizer.py` robi drugą próbę, a `runtime_chat.py` utrzymuje jeden silnik w trybie `--chat`. "
                "Do tego dochodzą warstwy pamięci `memory/`, baza `workspace_runtime`, dictionary/NLP i audyty tur. Source-origin: runtime_diagnostic_handler. "
                "Granica prawdy: to opis aktywnego kodu i plików, nie dowód świadomości biologicznej ani procesu działającego po zamknięciu terminala."
            )
            satisfied=['module_inventory','module_or_file','runtime_status','truth_boundary','source_origin']
        elif intent == 'system_capability_gap_question':
            body=(
                "Mam: start runtime, pamięć warstwową, klasyfikator intencji, router, handlery, walidator, syntezę naprawczą, audyt tur i tryb `--chat`. "
                "Brakuje: prawdziwie dynamicznego generatora rozmowy w lokalnym runtime albo adaptera LLM, który po walidacji potrafi stworzyć nową odpowiedź zamiast podstawić stały repair-template. "
                "Problem: obecna warstwa rozmowna może rozpoznać błąd, ale czasem naprawia szablon kolejnym szablonem. Plan zmiany: poprawić intencje, dodać osobne trasy dla feedbacku/modułów/braków, usunąć stały ordinary repair_body i dodać guard powtórzeń w `--chat`. "
                "Source-origin: runtime_diagnostic_handler. Granica prawdy: opisuję możliwości kodu, nie prywatne życie w tle."
            )
            satisfied=['capability_gap','module_or_file','problem','change_plan','truth_boundary','source_origin']
        elif any(x in low for x in ('powtarzasz', 'zawiesiłaś', 'zawiesilas', 'taką samą', 'taka sama', 'wysyłasz', 'wysylasz', 'w kółko', 'w kolko')):
            body=(
                "Tak — to wygląda jak błąd pętli rozmownej, nie jak dobra odpowiedź. Problem jest w ścieżce: klasyfikator intencji → router → handler → walidator → synteza naprawcza. "
                "Do zmiany są `dialogue_intent_classifier.py`, `route_registry.py`, `ordinary_dialogue_handler.py`, `runtime_answer_validator.py`, `runtime_response_synthesizer.py` i `runtime_chat.py`. "
                "Plan: rozpoznać pytania o powtarzanie jako diagnostykę bieżącej pętli, nie jako runtime_source; nie używać stałego repair_body dla ordinary dialogue; dodać guard, który po drugim identycznym tekście zatrzyma odpowiedź i zgłosi problem. "
                "Test regresji: Twoja sekwencja z `--chat` nie może zwrócić tej samej odpowiedzi dla różnych wiadomości. Source-origin: runtime_diagnostic_handler."
            )
            satisfied=['runtime_repetition_bug','module_or_file','problem','change_plan','regression_test','source_origin']
        else:
            short={k:status.get(k) for k in ('runtime_version','active_root','start_file','status_quality','raw_memory_status','update_history_status','network_policy_status','dictionary_provider_status')}
            body='Sprawdziłam diagnostykę runtime w aktywnym folderze. Najważniejsze: '+json.dumps(short, ensure_ascii=False)[:1800]+' Source-origin: runtime_startup_contract.'
            satisfied=['startup_status','active_root','raw_memory_status','source_origin']
        return RouteHandlerResult(self.name,self.route,body,intent=intent,data={'startup_status':status},file_sources=[{'path':'latka_jazn/core/startup_contract.py'}],required_components=ctx.get('required_components',[]),satisfied_components=satisfied,confidence=0.82,source_origin_detail='runtime_diagnostic_handler/v14.8.2.4',truth_boundary='To jest status aktywnego folderu runtime, nie dowód stałego procesu po zakończeniu CLI.')
