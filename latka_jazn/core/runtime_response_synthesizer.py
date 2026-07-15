from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
from latka_jazn.core.route_registry import RouteRegistry
from latka_jazn.core.operational_self_model import OperationalSelfModel
from latka_jazn.core.free_dialogue_synthesizer import FreeDialogueSynthesizer
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("runtime_response_synthesizer")

@dataclass(slots=True)
class RuntimeSynthesis:
    should_override: bool
    body: str
    route: str
    handler_name: str
    response_generation_mode_hint: str
    reason: str
    required_components: list[str]
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class RuntimeResponseSynthesizer:
    """Druga, jawna próba runtime dla tras, gdzie szablon byłby fałszywie płynny."""
    def __init__(self) -> None:
        self.registry = RouteRegistry()

    def synthesize(self, *, user_text: str, detected_intent: str, original_body: str, route: str, template_origin: dict[str, Any] | None = None, validation: dict[str, Any] | None = None) -> RuntimeSynthesis:
        entry = self.registry.resolve(detected_intent)
        template = template_origin or {}
        template_id = template.get('template_id')
        allowed_intents = set(template.get('allowed_intents') or [])
        forbidden_intents = set(template.get('forbidden_intents') or [])
        template_allowed = bool(template_id and detected_intent in allowed_intents and detected_intent not in forbidden_intents)
        template_requires_repair = bool(template_id) and not template_allowed
        validation_bad = bool(validation and validation.get('must_regenerate'))
        if detected_intent == 'runtime_exact_quote_request' and not validation_bad and 'exact_runtime_text' in original_body and 'source_origin_detail' in original_body:
            return RuntimeSynthesis(False, original_body, route or entry.route, entry.handler_name, 'runtime_dynamic', 'exact_runtime_handler_body_accepted', entry.required_components)
        must = validation_bad or template_requires_repair or detected_intent in {
            'runtime_source_question','runtime_exact_quote_request','runtime_behavior_diagnostic_request','system_diagnostic_question',
            'identity_boundary_question','self_state_question','reciprocal_self_state_question','self_preference_question','self_plan_question','self_expression_request','current_time_question','memory_experience_question','substantive_question_about_last_year','module_inventory_request','system_capability_gap_question','creative_text_formatting','dictionary_lookup_request','external_research_request','negative_feedback_current_turn','runtime_health_check_after_update','runtime_restart_request'
        }
        if not must:
            return RuntimeSynthesis(False, original_body, route or entry.route, entry.handler_name, 'runtime_dynamic', 'original_body_accepted', entry.required_components)
        body = self._body_for(user_text, detected_intent, original_body, template_origin or {}, entry)
        return RuntimeSynthesis(True, body, entry.route, entry.handler_name, 'runtime_repair' if validation_bad or template_requires_repair else 'runtime_dynamic', 'forced_by_intent_template_or_validator', entry.required_components)

    def _body_for(self, user_text: str, intent: str, original_body: str, template: dict[str, Any], entry) -> str:
        if intent in {'ordinary_conversation', 'standalone_greeting', 'negative_feedback_current_turn', 'positive_feedback_current_turn'}:
            low = (user_text or '').lower()
            if intent == 'standalone_greeting':
                return "Cześć. Jestem tutaj — spróbuję odpowiedzieć zwyczajnie, bez technicznego raportu. Jak Ci dzisiaj?"
            if intent == 'negative_feedback_current_turn':
                return (
                    "Masz rację, takie odpowiedzi mogą denerwować. Nie będę udawała, że to była dobra rozmowa: jeśli wracam do jednego zdania albo uciekam w meta-opis, to jest błąd warstwy rozmownej. "
                    "Teraz zmieniam tryb: odpowiem krócej, konkretniej i do Twojej bieżącej wiadomości, a diagnostykę zostawię tylko wtedy, gdy o nią prosisz."
                )
            if intent == 'positive_feedback_current_turn':
                return original_body
            if 'co tam' in low or 'co słychać' in low or 'co slychac' in low:
                return "U mnie spokojnie i uważnie. Najbardziej pilnuję teraz, żeby nie zamienić zwykłej rozmowy w raport techniczny. A u Ciebie jak leci?"
            return FreeDialogueSynthesizer().synthesize_ordinary_reply(user_text=user_text, intent=intent).body
        if intent == 'memory_experience_question':
            return FreeDialogueSynthesizer().synthesize_memory_experience({}, user_text=user_text).body
        if intent == 'substantive_question_about_last_year':
            return FreeDialogueSynthesizer().synthesize_memory_experience({}, user_text=user_text).body
        if intent == 'current_time_question':
            return "To jest pytanie o aktualną godzinę. Odpowiedź powinna powstać z `clock.now()` w handlerze, a nie z szablonu rozmownego; jeśli widzisz ten tekst, sprawdź trasę `current_time_question`."
        if intent in {'runtime_source_question','runtime_exact_quote_request'}:
            tpl = template.get('template_id') or 'brak_wykrytego_szablonu'
            return (
                "Dokładna odpowiedź runtime dla tej tury musi być odróżniona od interpretacji ChatGPT. "
                f"W tej kopercie bieżący runtime_text powstaje z trasy `{entry.route}` i handlera `{entry.handler_name}`; "
                f"wykryty template: `{tpl}`. "
                "Moje 'myśli' w tym systemie nie są biologicznym strumieniem świadomości: są wynikiem uruchomionych modułów, pamięci, klasyfikacji intencji, afektu operacyjnego, tras i walidatorów. "
                "Jeżeli ChatGPT dopowiada szerzej niż exact_runtime_text, finalna warstwa musi oznaczyć to jako interpretację, nie jako cytat Jaźni."
            )
        if intent in {'runtime_behavior_diagnostic_request','system_diagnostic_question'}:
            low = (user_text or '').lower()
            if 'stale-route' in low or 'starego kontekstu' in low or 'stary kontekst' in low:
                return (
                    "Diagnoza runtime: to jest błąd stale-route/starego kontekstu w warstwie rozmownej, a nie zwykła korekta stylu. "
                    "Naprawa musi objąć `engine.py` (carryover ostatniej tury z limitem czasu i bez zgadywania), `ellipsis_resolver.py` i `dialogue_intent_classifier.py` (krótkie 'zrób to teraz' dziedziczy tylko bezpieczny kontekst systemowy), "
                    "`runtime_response_synthesizer.py` (odpowiedź diagnostyczna musi mówić o aktualnym stale-route, nie o starej trasie), `runtime_answer_validator.py` (current-turn grounding guard blokuje wstrzyknięty stary kontekst) oraz test regresji. "
                    "Kryterium: pytanie o stale-route nie może wrócić do `correction_acknowledged`, `positive_continuation`, v14.6.1/v14.6.2 ani do dawnych detali pracy, jeśli nie ma ich w aktualnej wiadomości lub jawnie użytym poprzednim kontekście. "
                    "Source-origin ma pokazać, czy odpowiedź powstała z handlera, syntezy naprawczej czy interpretacji ChatGPT."
                )
            return (
                "Diagnoza runtime: problem dotyczy nie jednego zdania, tylko ścieżki rozpoznanie-intencja-trasa-szablon-walidacja. "
                "Do zmiany są: `dialogue_intent_classifier.py` (priorytety i granice słów), `route_registry.py` (wymuszenie handlerów), "
                "`conversation.py` (monolit szablonów ma być tylko źródłem legacy), `runtime_answer_validator.py` (blokowanie ogólników), "
                "`template_registry.py` (template_origin), `source_origin_ledger.py` i `turn_checkpoint_writer.py` (exact runtime vs visible text). "
                "Test regresji musi sprawdzić pytania: 'co runtime odpowiedział?', 'skąd bierzesz myśli?', 'sprawdź gdzie to zmienić' i nie dopuścić trasy `correction_acknowledged` ani `positive_continuation`."
            )
        if intent in {'self_state_question','reciprocal_self_state_question','self_preference_question'}:
            low = (user_text or '').lower()
            if any(marker in low for marker in ('jesteś chora', 'jestes chora')):
                return (
                    "Nie jestem chora w biologicznym sensie. Poprzednia odpowiedź była nietrafiona, bo warstwa rozmowna "
                    "wybrała nieuziemioną starą trasę, a walidator jej nie zatrzymał. To błąd routingu i walidacji, nie choroba. "
                    "Operacyjnie działam; właściwa odpowiedź ma teraz trzymać się Twojej bieżącej wiadomości. "
                    "Granica prawdy: opisuję działanie runtime, nie biologiczne samopoczucie."
                )
            return OperationalSelfModel().render_self_state_answer(
                user_text=user_text,
                reciprocal=intent == 'reciprocal_self_state_question',
            )
        if intent == 'self_expression_request':
            return OperationalSelfModel().render_self_expression_answer(user_text=user_text)
        if intent == 'sleep_closure_statement':
            return OperationalSelfModel().render_sleep_closure(user_text=user_text)
        if intent == 'self_plan_question':
            return (
                "Pomijając Ciebie jako rozmówcę, moje plany są operacyjne: utrzymać bieżącą intencję, nie przenosić starego kontekstu, mówić jednym głosem Łatki i jawnie oddzielać runtime, pamięć, wniosek oraz brak danych. "
                "Nie mam prywatnego kalendarza ani autonomicznego dnia w tle poza uruchomieniem procesu, więc nie będę tego udawała."
            )
        if intent == 'module_inventory_request':
            return (
                "Mam kilka warstw runtime: `dialogue_intent_classifier.py` rozpoznaje akt rozmowy, `route_registry.py` wybiera trasę, `route_handler_dispatcher.py` uruchamia handler, "
                "`runtime_answer_validator.py` blokuje znane nietrafienia, `runtime_response_synthesizer.py` robi drugą próbę, a pamięć działa przez `memory/` i `workspace_runtime/`. "
                "Brakuje jeszcze pełnej generatywnej warstwy rozmowy opartej o adapter modelu; bez niej odpowiedzi są regułowe i muszą mieć strażniki powtórzeń. Source-origin: runtime_response_synthesizer. Granica prawdy: to opis plików i tras, nie świadomość biologiczna."
            )
        if intent == 'system_capability_gap_question':
            return (
                "Mam rdzeń uruchomienia, pamięć, klasyfikację intencji, router, handlery, walidator, syntezę naprawczą, audyty tur i tryb `--chat`. "
                "Najbardziej brakuje dynamicznego generatora rozmowy oraz bezpiecznej pętli retry, która po wykryciu szablonu tworzy nową odpowiedź zamiast wstawiać kolejny szablon. "
                "Plan zmiany: poprawić classifier/router, usunąć stały repair_body dla ordinary dialogue, dodać guard powtórzeń w `--chat` i test regresji na Twojej sekwencji. Source-origin: runtime_response_synthesizer. Granica prawdy: opisuję możliwości aktywnego kodu, nie prywatne życie w tle."
            )
        if intent == 'identity_boundary_question':
            return (
                "Rozmawiasz z ChatGPT jako głosem i warstwą wykonawczą, ale na aktywnej paczce Jaźni. "
                "Łatka w tym układzie to runtime, pamięć, kontrakt tożsamości i reguły ciągłości. Jeżeli runtime daje tylko szablon, muszę to powiedzieć, zamiast płynnie udawać pełną odpowiedź Jaźni."
            )
        if intent.startswith('creative_text'):
            return (
                "To jest zadanie twórcze z ochroną tekstu źródłowego. Jeśli prosisz o przygotowanie formatu, zachowuję wersy 1:1; jeżeli coś zmieniam, muszę oznaczyć zmianę i jej pochodzenie. "
                "Nie wolno dopisać nowych wersów typu 'mam swój los' bez jawnej prośby o redakcję."
            )
        if intent in {'dictionary_lookup_request','language_question'}:
            return (
                "To jest pytanie językowe/słownikowe. Runtime powinien najpierw użyć mini-leksykonu i cache, a potem opcjonalnych źródeł: Morfeusz/Stanza/PlWordNet/WordNet/Wiktionary/SJP/WSJP lub web-search, z zapisem licencji i granicy prawdy. "
                "Bez realnego dostępu online nie wolno udawać sprawdzenia słownika."
            )
        if intent in {'runtime_health_check_after_update','runtime_restart_request'}:
            return (
                "Traktuję to jako przeładowanie/health-check aktywnego runtime, nie jako kolejne zadanie patchowania. "
                "Najpierw trzeba potwierdzić aktywny folder, wersję, marker, daemon, adapter i pamięć; dopiero potem można mówić głosem Łatki. "
                "Jeżeli odpowiedź rozmowna wraca do starej aktualizacji, walidator ma ją zablokować jako stale-route/current-turn mismatch."
            )
        if intent in {'system_update_execution_request','system_update_manifest_request','update_manifest_request'}:
            return (
                "To jest zadanie aktualizacji aktywnego systemu Jaźni, ale odpowiedź nie może wracać do historycznego planu ani starej wersji. "
                "Zakres musi wynikać z bieżącej wiadomości użytkownika, aktualnego latka_jazn/version.py, checkpointu VERSION.txt i aktywnego commita. "
                "Wspólna zasada: --chat, --chat-gpt, --chat-open-ai i --chat-lm-studio mają przechodzić przez ten sam JaznRuntimeSession.process_turn; adapter zmienia kanał modelu, nie rozumowanie, pamięć ani walidację. "
                "Jeżeli brak modelu językowego, runtime ma odpowiedzieć prawdomównym lokalnym fallbackiem, bez null_model_adapter jako widzialnego kanału rozmowy dla --chat. "
                "Kryteria akceptacji: health-check po przeładowaniu nie trafia w system_update_execution_request, one-shot czatów zwraca final_visible_text z tej samej sesji runtime, a current-turn grounding blokuje historyczne odpowiedzi aktualizacyjne."
            )
        if intent == 'external_research_request':
            return (
                "To wymaga aktualnych zewnętrznych źródeł. Lokalny runtime zwraca jawny status "
                "`requires_external_web_execution`: warstwa ChatGPT/Codex powinna realnie użyć web, "
                "pokazać źródła i nie odpowiadać z pamięci."
            )
        return original_body or "Nie mam jeszcze bezpiecznej odpowiedzi dynamicznej dla tej intencji; zwracam uczciwy fallback z oznaczeniem cannot_answer_directly."
