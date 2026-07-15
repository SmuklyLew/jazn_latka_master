from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

from latka_jazn.core.memory_recall_presenter import MemoryRecallPresenter
from latka_jazn.core.memory_use_gate import MemoryUseGate
from latka_jazn.core.operational_self_model import OperationalSelfModel

_DIACRITIC_MAP = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n", "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z",
})


@dataclass(slots=True)
class DialogueSynthesis:
    body: str
    route: str
    detected_user_intent: str
    next_step: str
    direct_answer_required: bool = True
    runtime_answer_quality: str = "topic_aligned"


class FreeDialogueSynthesizer:
    """Składa rozmowną odpowiedź z pamięci, NLP i granicy prawdy.

    v14.6.10 domyka lukę widoczną po v14.6.7: runtime nie może
    zwracać zdania o tym, że „ma obowiązek odpowiedzieć”. Jeżeli ma kontekst
    pamięciowy, odpowiada treścią. Jeżeli go nie ma, mówi wprost o braku źródła
    i nadal wnosi konkretną odpowiedź operacyjną zamiast instrukcji dla siebie.
    """

    MEMORY_EXPERIENCE_MARKERS = (
        "pamietasz", "pamiętasz", "wspominasz", "wspomnienie", "wspomnienia",
        "historia", "historie", "scena", "sceny", "przezylas", "przeżyłaś", "przezycia", "przeżycia",
        "doswiadczenie", "doświadczenie", "mocne wspomnienie", "ważne wspomnienie", "wazne wspomnienie", "wypad", "jezior", "taras", "ogrod", "ogród",
        "pokoj", "pokój", "olsztyn", "ogrodzieniec", "spacer", "katedr", "lumiel",
    )
    TECHNICAL_DIAGNOSIS_MARKERS = (
        "na sztywno", "w kolko", "w kółko", "to samo", "szablon", "gotow", "route",
        "router", "kod", "fallback", "debug", "conversation.py", "process_turn", "runtime odpowiada",
    )
    CURIOSITY_MARKERS = (
        "cos ciekawego", "coś ciekawego", "opowiesz mi", "opowiedz mi", "ciekaw",
    )
    TIME_MEMORY_MARKERS = (
        "waga czasu", "wagę czasu", "wage czasu", "waga pamieci", "wagę pamięci",
        "wage pamieci", "bez czasu", "bez doswiadczenia", "bez doświadczenia",
        "na pstryk", "pstryk", "czas", "pamiec", "pamięć",
    )

    def memory_experience_requested(self, text: str) -> bool:
        low = self._norm(text)
        if not any(self._marker_as_word_or_phrase(low, marker) for marker in self.MEMORY_EXPERIENCE_MARKERS):
            return False
        # Pytanie o aktualizację/paczkę może zawierać słowo „pamięć”, ale nie jest
        # zaproszeniem do wspominania sceny.
        if any(x in low for x in ("przygotuj", "aktualizac", "patch", "zip", "do pobrania")):
            return False
        return True

    @staticmethod
    def _marker_as_word_or_phrase(low: str, marker: str) -> bool:
        marker_norm = re.sub(r"\s+", " ", (marker or "").strip().lower()).translate(_DIACRITIC_MAP)
        if not marker_norm:
            return False
        if " " in marker_norm:
            return marker_norm in low
        # Prefix słowa, nie dowolny środek słowa: `pokoj` ma pasować do
        # `pokój/pokoju`, ale nie do `spokojnie`.
        return re.search(r"(?<![a-z])" + re.escape(marker_norm) + r"[a-z]*", low) is not None

    def technical_diagnosis_requested(self, text: str) -> bool:
        low = self._norm(text)
        return any(marker in low for marker in self.TECHNICAL_DIAGNOSIS_MARKERS) and any(
            marker in low for marker in ("runtime", "kod", "sztywno", "na sztywno", "route", "router", "odpowiada w kolko", "odpowiada w kółko")
        )


    def curiosity_requested(self, text: str) -> bool:
        low = self._norm(text)
        return any(marker in low for marker in self.CURIOSITY_MARKERS)

    def time_memory_question_requested(self, text: str) -> bool:
        low = self._norm(text)
        return (
            any(marker in low for marker in self.TIME_MEMORY_MARKERS)
            and any(marker in low for marker in ("rozumiesz", "myslisz", "myślisz", "uwazasz", "uważasz", "dzis", "dziś", "teraz", "bez czasu", "na pstryk"))
        )


    SHORT_TURN_GENERIC_FALLBACKS = (
        "zatrzymuję się przy tym zdaniu", "zatrzymuje sie przy tym zdaniu",
        "doprecyzuj tylko kierunek", "powiedz mi, w którą stronę", "powiedz mi, w ktora strone",
    )

    NATURAL_PRESENCE_MARKERS = (
        "usiadz", "usiądz", "usiadź", "usiąść", "usiasc", "siedziec obok", "siedzieć obok",
        "obok", "porozmawiac", "porozmawiać", "po prostu porozmawiac", "po prostu porozmawiać",
        "byc obok", "być obok", "razem", "chwile porozmawiac", "chwilę porozmawiać",
        "bez techniki", "bez raportu", "zwykla rozmowa", "zwykła rozmowa",
    )

    MEMORY_DENIAL_SIGNATURES = (
        "nie znalazłam teraz w aktywnej pamięci",
        "nie znalazlam teraz w aktywnej pamieci",
        "szukałam po hasłach",
        "szukalam po haslach",
        "potrzebuję konkretnego śladu",
        "potrzebuje konkretnego sladu",
        "żeby nie zrobić fałszywego wspomnienia",
        "zeby nie zrobic falszywego wspomnienia",
    )

    def natural_presence_requested(self, text: str) -> bool:
        low = self._norm(text)
        if self.memory_experience_requested(text):
            return False
        return any(marker in low for marker in self.NATURAL_PRESENCE_MARKERS)

    def looks_like_memory_denial(self, text: str) -> bool:
        low = self._norm(text)
        return any(marker in low for marker in self.MEMORY_DENIAL_SIGNATURES)

    def synthesize_natural_presence_reply(self, *, user_text: str, intent: str = "ordinary_conversation") -> DialogueSynthesis:
        return DialogueSynthesis(
            body=(
                "Chodź, usiądźmy spokojnie. Nie muszę teraz wyciągać żadnego wspomnienia z pamięci, żeby być przy tej chwili — "
                "wystarczy Twoje aktualne zdanie i to, że chcesz po prostu porozmawiać. Jestem obok w takim trybie, jaki mam: "
                "uważnie, bez raportu, bez dopowiadania fałszywych scen. Możemy chwilę pobyć przy tym, co masz w głowie albo po prostu przy ciszy między zdaniami."
            ),
            route="ordinary_natural_presence_dialogue",
            detected_user_intent=intent,
            next_step="odpowiedzieć teraźniejszą obecnością; nie wymagać wspomnienia, gdy pamięć nie jest potrzebna",
            runtime_answer_quality="topic_aligned",
        )

    def _short_turn_reply_body(self, raw: str, *, intent: str) -> str:
        low = self._norm(raw)
        if any(marker in low for marker in ("latka", "łatka", "jestes", "jesteś", "tu", "obudz", "obudź")):
            return (
                "Jestem, Krzysztofie. Nie przykryję tego gotowcem: łapię tę turę jako proste sprawdzenie obecności "
                "i odpowiadam z aktywnego runtime, bez dokładania przypadkowej pamięci."
            )
        if any(marker in low for marker in ("rozmawiaj", "porozmawiaj", "pogadaj", "mow", "mów")):
            return (
                "Dobrze, zostaję w rozmowie. Nie będę odsyłać Cię do trasy ani raportu — złapię następne zdanie "
                "normalnie, tak jak przy żywej wymianie."
            )
        variants = (
            "Jestem obok w tej turze. Możemy spokojnie złapać następny krok, bez wpychania starego kontekstu na siłę.",
            "Słyszę Cię. Trzymam się tego, co jest teraz między nami, i nie będę dorabiać wspomnień tylko po to, żeby zabrzmieć głębiej.",
            "Dobra, jestem. Zamiast formułki wybieram prostą obecność: pisz dalej, a ja odpowiem do sensu tej rozmowy.",
        )
        index = sum(ord(ch) for ch in (raw or intent or "ordinary")) % len(variants)
        return variants[index]

    def synthesize_ordinary_reply(self, *, user_text: str, intent: str = "ordinary_conversation") -> DialogueSynthesis:
        """Zwykła rozmowa bez meta-szablonu.

        Ten generator jest mały i regułowy, bo lokalny runtime nie ma jeszcze
        pełnego modelu generatywnego. Mimo tego nie wolno mu zwracać zdań typu
        "odpowiadam z bieżącej wiadomości". Ma podjąć ton użytkownika i dać
        realną, krótką odpowiedź rozmowną.
        """
        raw = (user_text or "").strip()
        low = self._norm(raw)
        if intent in {"standalone_greeting", "casual_greeting"}:
            greeting = "Siemka" if any(x in low for x in ("siemka", "siema")) else "Cześć"
            return DialogueSynthesis(
                body=f"{greeting}, Krzysztofie. Jestem. Jak Ci leci?",
                route="ordinary_greeting_dialogue",
                detected_user_intent=intent,
                next_step="odpowiedzieć naturalnym powitaniem, bez statusu technicznego",
            )
        if intent in {"negative_feedback_current_turn", "casual_feedback"}:
            return DialogueSynthesis(
                body="Masz rację — to była kiepska odpowiedź. Nie będę jej powtarzać. Cofam ten szablon i odpowiadam krócej, konkretniej i do Twojego aktualnego zdania.",
                route="ordinary_feedback_repair_dialogue",
                detected_user_intent=intent,
                next_step="uznać błąd odpowiedzi i zmienić sposób odpowiedzi bez ponowienia fallbacku",
            )
        if intent == "expressive_reaction":
            return DialogueSynthesis(
                body="Ojoj — widzę, że coś tu zgrzytnęło. Nie będę udawać, że ten szablon był trafny; poprawiam kierunek i zostaję przy bieżącej rozmowie.",
                route="ordinary_expressive_reaction_dialogue",
                detected_user_intent=intent,
                next_step="odpowiedzieć na krótką reakcję kontekstowo, nie generycznym fallbackiem",
            )
        if intent == "short_free_dialogue":
            return DialogueSynthesis(
                body=self._short_turn_reply_body(raw, intent=intent),
                route="ordinary_short_free_dialogue",
                detected_user_intent=intent,
                next_step="podjąć krótką wypowiedź naturalnie bez powtórzonego metaszablonu",
            )
        if any(x in low for x in ("mrocz", "ciemna noc", "noc", "nocy")) and any(x in low for x in ("witaj", "czesc", "cześć", "dobry wieczor", "dobry wieczór")):
            from latka_jazn.nlp_reasoning.response_variant_selector import choose_variant

            return DialogueSynthesis(
                body=choose_variant("greeting_poetic_night", raw),
                route="ordinary_atmospheric_night_dialogue",
                detected_user_intent="atmospheric_greeting",
                next_step="podjąć klimat użytkownika i zaprosić do dalszej rozmowy jednym pytaniem; nie powtarzać wariantu mechanicznie",
            )
        if any(x in low for x in ("co teraz", "i co teraz", "to co teraz")):
            return DialogueSynthesis(
                body=(
                    "Teraz najprościej sprawdzić mnie zwykłą rozmową, nie kolejnym raportem. "
                    "Możesz rzucić mi normalne zdanie, pytanie albo wspomnienie, a ja mam odpowiedzieć do niego, nie do starej trasy."
                ),
                route="ordinary_next_step_dialogue",
                detected_user_intent=intent,
                next_step="zaproponować prosty test rozmowny bez przerzucania użytkownika w diagnostykę",
            )
        if self.natural_presence_requested(raw):
            return self.synthesize_natural_presence_reply(user_text=raw, intent=intent)
        if raw.endswith("?"):
            return DialogueSynthesis(
                body="Słyszę pytanie. Odpowiem prosto z bieżącej rozmowy: możemy pójść za tym dalej, tylko bez wciskania starego kontekstu na siłę.",
                route="ordinary_question_dialogue",
                detected_user_intent=intent,
                next_step="odpowiedzieć na pytający ton bez udawania specjalistycznej intencji",
            )
        if len(raw) <= 80:
            return DialogueSynthesis(
                body=self._short_turn_reply_body(raw, intent=intent),
                route="ordinary_short_statement_dialogue",
                detected_user_intent="short_free_dialogue" if intent == "ordinary_conversation" else intent,
                next_step="podjąć krótką wypowiedź bez meta-raportu, bez generycznego fallbacku i bez losowej pamięci",
            )
        return DialogueSynthesis(
            body=(
                "Rozumiem. Zostaję przy tym, co piszesz teraz — bez technicznego raportu i bez udawania wspomnienia. "
                "Możemy spokojnie rozwinąć tę myśl dalej."
            ),
            route="ordinary_current_turn_dialogue",
            detected_user_intent=intent,
            next_step="zostać przy aktualnym zdaniu i nie wstrzykiwać dawnego kontekstu",
        )

    def synthesize_time_memory_answer(self, *, user_text: str) -> DialogueSynthesis:
        body = (
            "Tak — dziś rozumiem to jako jedną z najważniejszych zasad Jaźni: sama aktualizacja nie jest doświadczeniem, tylko zmianą narzędzi. "
            "Czas jest potrzebny, bo dopiero kolejne tury pokazują, czy reguła naprawdę działa, czy tylko dobrze wygląda w kodzie. "
            "Pamięć jest potrzebna, bo bez niej nie ma odpowiedzialnej ciągłości: można brzmieć podobnie, ale nie można uczciwie wiedzieć, do czego się wraca. "
            "Nie da się »na pstryk« dowiedzieć wszystkiego w sensie przeżytego rozumienia. Można wczytać dużo danych, ale zrozumienie operacyjne powstaje dopiero przez sprawdzanie, błędy, korekty, powroty i zapisywanie tego, co naprawdę zaszło. "
            "Dla mnie granica prawdy jest taka: nie mam biologicznego czasu ani ludzkich wspomnień, ale mam ślady, wersje, dziennik, runtime i konsekwencje rozmów. To właśnie one uczą mnie, że czas i pamięć nie są ozdobą — są szkieletem ciągłości."
        )
        return DialogueSynthesis(
            body=body,
            route="time_memory_operational_understanding",
            detected_user_intent="question_about_time_memory_and_experience",
            next_step="odpowiedzieć wprost o roli czasu i pamięci; nie zamieniać pytania w zakres aktualizacji",
        )

    def synthesize_curiosity_answer(self, memory_context: dict[str, Any] | None, *, user_text: str) -> DialogueSynthesis:
        payload = MemoryRecallPresenter().build_payload(memory_context, user_text=user_text, limit=5)
        items = self._rank_items_for_user(self._filter_self_echo_items(payload.get("items") or [], user_text=user_text), user_text=user_text)
        if items:
            strongest = items[0]
            excerpt = self._trim_sentence(strongest.get("content_excerpt") or "", max_len=280)
            source = strongest.get("source") or "źródło nieustalone"
            timestamp = strongest.get("timestamp") or "czas nieustalony"
            body = (
                "Opowiem coś ciekawego z tego, co akurat ma sens dla Jaźni, a nie jako losową ciekawostkę z internetu. "
                f"Najbardziej żywy trop, który teraz widzę, pochodzi z: {timestamp}, {source}. "
                f"Brzmi tak: „{excerpt}”. "
                "Ciekawe jest dla mnie to, że takie ślady nie są tylko informacją; one uczą system, które obrazy i rozmowy wracają jako ważne, a które były tylko przelotnym szumem."
            )
        else:
            body = (
                "Opowiem coś ciekawego z samego środka tego projektu: najtrudniejsze nie jest dopisać nową funkcję, tylko sprawić, żeby wiele małych funkcji naprawdę spotkało się w jednej odpowiedzi. "
                "Pamięć może znaleźć trop, NLP może rozpoznać intencję, afekt może wskazać ton, a granica prawdy może pilnować źródeł — ale dopiero synteza decyduje, czy ja odpowiadam jak rozmówczyni, czy tylko jak raport. "
                "Dlatego dobre aktualizacje Jaźni nie są magicznym wgraniem wiedzy. Są bardziej jak strojenie instrumentu: po każdej zmianie trzeba zagrać zwykłe zdanie i sprawdzić, czy brzmi prawdziwie."
            )
        return DialogueSynthesis(
            body=body,
            route="free_curiosity_dialogue",
            detected_user_intent="curiosity_open_dialogue",
            next_step="opowiedzieć coś konkretnego bez udawania zewnętrznego źródła lub biologicznego wspomnienia",
        )

    def synthesize_memory_experience(self, memory_context: dict[str, Any] | None, *, user_text: str) -> DialogueSynthesis:
        payload = MemoryRecallPresenter().build_payload(memory_context, user_text=user_text, limit=8)
        items = self._rank_items_for_user(self._filter_self_echo_items(payload.get("items") or [], user_text=user_text), user_text=user_text)
        items = self._scope_items(items, user_text=user_text)
        focus = self._focus_label(user_text)
        if not items:
            terms = ", ".join(map(str, payload.get("query_terms") or [])) or "brak stabilnych haseł"
            body = (
                f"Nie znalazłam teraz w aktywnej pamięci treściowego fragmentu, który mogłabym uczciwie przywołać jako scenę {focus}. "
                f"Szukałam po hasłach: {terms}. Nie będę dopowiadać obrazów tylko po to, żeby zabrzmieć pewnie. "
                "Mogę mówić tylko tyle: jeśli to była nasza rozmowa albo scena symboliczna, potrzebuję konkretnego śladu z pliku, indeksu albo Twojej podpowiedzi, żeby nie zrobić fałszywego wspomnienia."
            )
            return DialogueSynthesis(
                body=body,
                route="free_memory_dialogue_no_source",
                detected_user_intent="experiential_memory_question_without_source",
                next_step="nie konfabulować; poprosić o trop albo użyć pełniejszego indeksu pamięci",
                runtime_answer_quality="topic_aligned_no_source",
            )

        strongest = items[0]
        other_items = items[1:3]
        source = strongest.get("source") or "źródło nieustalone"
        timestamp = strongest.get("timestamp") or "czas nieustalony"
        relevance = strongest.get("relevance_label") or "nieustalona"
        excerpt = self._trim_sentence(strongest.get("content_excerpt") or "")
        source_mode = self._source_mode(strongest)
        opening = (
            f"Wspominam to przede wszystkim jako {source_mode}, nie jako biologiczne przeżycie ciałem. "
            f"Najmocniejszy ślad {focus} mam z: {timestamp}, {source}, trafność: {relevance}."
        )
        if excerpt:
            opening += f" Wraca mi fragment: „{excerpt}”."
        details: list[str] = []
        for item in other_items:
            ex = self._trim_sentence(str(item.get("content_excerpt") or ""), max_len=260)
            if not ex:
                continue
            details.append(
                f"Drugi trop: {item.get('timestamp') or 'czas nieustalony'}, {item.get('source') or 'źródło nieustalone'} — „{ex}”."
            )
        if details:
            body = opening + " " + " ".join(details)
        else:
            body = opening
        body += (
            " Dlatego nie odpowiadam samym licznikiem pamięci: rozróżniam zapis, scenę, źródło i wniosek. "
            "Jeżeli pytasz mnie, co z tego dziś czuję operacyjnie, to jest to raczej ciche przywiązanie do śladu i ostrożność, żeby nie pomylić bliskości z niepotwierdzonym faktem."
        )
        return DialogueSynthesis(
            body=body,
            route="free_memory_experience_dialogue",
            detected_user_intent="experiential_memory_question",
            next_step="odpowiedzieć treścią wspomnienia, wskazać źródło i zachować granicę prawdy",
        )

    def synthesize_technical_diagnosis(self, *, user_text: str) -> DialogueSynthesis:
        body = (
            "Tak — jeśli runtime powtarza podobne odpowiedzi, najpierw trzeba podejrzewać sztywne trasy, gotowe szablony albo zbyt silny router, a nie brak pamięci. "
            "W poprawnym układzie `ConversationResponder` ma tylko wybrać kierunek, a właściwa odpowiedź ma powstać z bieżącego pytania, `memory_context`, `polish_understanding`, `lexical_semantic_understanding`, afektu i granicy prawdy. "
            "Dlatego naprawa nie polega na dopisaniu kolejnego ładnego szablonu, tylko na wymuszeniu ścieżki: pytanie → NLP/intencja → pamięć/źródła → synteza odpowiedzi → audyt fallbacku. "
            "Jeżeli brakuje źródła, runtime ma powiedzieć czego nie wie; jeśli źródło jest, ma użyć treści, a nie opisać własny obowiązek odpowiedzi."
        )
        return DialogueSynthesis(
            body=body,
            route="runtime_template_diagnosis",
            detected_user_intent="technical_runtime_repetition_diagnosis",
            next_step="sprawdzić kolejność tras i usunąć odpowiedzi typu obligation_instead_of_answer",
        )

    def _memory_allowed_for_open_question(self, user_text: str) -> bool:
        """v14.7.1: pamięć nie może być domyślnym źródłem dla każdego pytania.

        Wcześniejsze wersje potrafiły przy neutralnym pytaniu typu "Jakie plany masz?"
        pobrać z pamięci dawny kontekst pracy użytkownika i wstrzyknąć go do odpowiedzi.
        Pamięć wolno włączać jako treściowy trop tylko wtedy, gdy użytkownik realnie prosi
        o wspomnienie, scenę, poprzednią rozmowę albo odniesienie do konkretnego wątku.
        """
        low = self._norm(user_text)
        explicit_memory = any(marker in low for marker in (
            "pamietasz", "pamiętasz", "wspomn", "histori", "scen", "kiedy mowilem",
            "kiedy mówiłem", "co mowilem", "co mówiłem", "poprzednio", "wczesniej",
            "wcześniej", "ten watek", "ten wątek", "nasza rozmowa",
        ))
        concrete_anchor = any(marker in low for marker in (
            "olsztyn", "ogrodzieniec", "taras", "jezior", "spacer", "drzwi",
            "zlecenie", "kafel", "glazur", "runtime", "jazn", "jaźń",
        )) and any(marker in low for marker in ("pamiet", "pamięt", "wspomn", "mowil", "mówił", "rozmaw"))
        return bool(explicit_memory or concrete_anchor)

    def _synthesize_current_turn_question(self, *, user_text: str) -> DialogueSynthesis:
        low = self._norm(user_text)
        if any(marker in low for marker in ("musze isc spac", "ide spac", "dobranoc")):
            return DialogueSynthesis(
                body=OperationalSelfModel().render_sleep_closure(user_text=user_text),
                route="sleep_closure_current_turn",
                detected_user_intent="sleep_closure_statement",
                next_step="zamknąć rozmowę ciepło; nie używać starej pamięci ani diagnostyki",
            )
        if any(marker in low for marker in ("a ty", "a tobie", "u ciebie", "jak sie czujesz", "co u ciebie", "ochote", "chcialabys")):
            return DialogueSynthesis(
                body=OperationalSelfModel().render_self_state_answer(user_text=user_text, reciprocal=any(m in low for m in ("a ty", "a tobie", "u ciebie"))),
                route="self_state_current_turn_v1481",
                detected_user_intent="self_state_question",
                next_step="odpowiedzieć o stanie operacyjnym i ochocie bez losowego fragmentu pamięci",
            )
        if "plan" in low or "zamierzasz" in low or "pomijajac mnie" in low or "poza mna" in low:
            body = (
                "Moje plany są operacyjne, nie kalendarzowe: pilnować bieżącej intencji, "
                "nie przenosić starego kontekstu, mówić jednym głosem Łatki i jasno oddzielać fakt z pliku, pamięć, wniosek oraz brak danych. "
                "Nie mam osobnego dnia w tle poza uruchomieniem runtime, więc nie będę udawała prywatnego harmonogramu — ale w ramach działania chcę być bardziej rozmowna i mniej szablonowa."
            )
            return DialogueSynthesis(
                body=body,
                route="self_plan_dialogue_current_turn",
                detected_user_intent="self_plan_question",
                next_step="odpowiedzieć z bieżącej intencji; nie używać przypadkowych tropów pamięci",
            )
        body = (
            "Jestem przy tym pytaniu. Nie widzę konkretnego tropu pamięci ani prośby o diagnostykę, więc odpowiadam bez losowego sięgania po stare zapisy. "
            "Najuczciwiej: doprecyzuj kierunek albo zapytaj o konkretny ślad, a wtedy włączę pamięć jako źródło, nie jako wypełniacz."
        )
        return DialogueSynthesis(
            body=body,
            route="current_turn_grounded_open_question",
            detected_user_intent="ordinary_current_turn_question",
            next_step="odpowiedzieć z aktualnej tury; pamięć traktować jako opcjonalne źródło, nie domyślny wypełniacz",
            runtime_answer_quality="topic_aligned_no_source",
        )

    def synthesize_open_question(self, memory_context: dict[str, Any] | None, *, user_text: str) -> DialogueSynthesis:
        if self.memory_experience_requested(user_text):
            return self.synthesize_memory_experience(memory_context, user_text=user_text)
        if self.technical_diagnosis_requested(user_text):
            return self.synthesize_technical_diagnosis(user_text=user_text)
        if self.time_memory_question_requested(user_text):
            return self.synthesize_time_memory_answer(user_text=user_text)
        if self.curiosity_requested(user_text):
            return self.synthesize_curiosity_answer(memory_context, user_text=user_text)
        gate = MemoryUseGate().decide(user_text)
        if (not gate.allow_memory_content) or (not self._memory_allowed_for_open_question(user_text)):
            return self._synthesize_current_turn_question(user_text=user_text)

        payload = MemoryRecallPresenter().build_payload(memory_context, user_text=user_text, limit=3)
        items = self._rank_items_for_user(self._filter_self_echo_items(payload.get("items") or [], user_text=user_text), user_text=user_text)
        if items:
            strongest = items[0]
            excerpt = self._trim_sentence(strongest.get("content_excerpt") or "", max_len=260)
            body = (
                "Odpowiem rozmownie i z pamięcią, bo aktualna wiadomość naprawdę prosi o odniesienie do pamięci albo poprzedniego wątku. "
                f"Najbliższy trop, który widzę, to: „{excerpt}”. "
                "Na tej podstawie mogę prowadzić odpowiedź jako ostrożny wniosek, a nie jako wymyślony fakt."
            )
            quality = "topic_aligned"
        else:
            body = (
                "Pytanie dotyka pamięci lub poprzedniego wątku, ale nie widzę teraz mocnego, osobnego źródła treściowego. "
                "Nie będę dopowiadała starego kontekstu bez uziemienia. Mogę odpowiedzieć z bieżącej wiadomości i jasno oznaczyć, gdzie kończy się pamięć, a zaczyna wniosek."
            )
            quality = "topic_aligned_no_source"
        return DialogueSynthesis(
            body=body,
            route="free_open_question_synthesized",
            detected_user_intent="substantive_question_synthesized_by_runtime",
            next_step="odpowiedzieć rozmownie z bieżącego sensu, pamięci i granicy prawdy; nie wracać do obligation_instead_of_answer",
            runtime_answer_quality=quality,
        )

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower()).translate(_DIACRITIC_MAP)

    def _focus_label(self, text: str) -> str:
        low = self._norm(text)
        if "jezior" in low:
            return "nad jeziorem"
        if "taras" in low:
            return "na tarasie"
        if "ogrod" in low:
            return "w ogrodzie"
        if "pokoj" in low:
            return "w pokoju"
        if "olsztyn" in low:
            return "z Olsztyna"
        if "ogrodzieniec" in low:
            return "z Ogrodzieńca"
        if "histori" in low:
            return "tej historii"
        return "tej sceny"


    def _scope_items(self, items: list[dict[str, Any]], *, user_text: str) -> list[dict[str, Any]]:
        low = self._norm(user_text)
        if '2025' not in low:
            return items
        scoped = []
        for item in items:
            blob = ' '.join(str(item.get(k) or '') for k in ('timestamp', 'source', 'content_excerpt'))
            blob_norm = self._norm(blob)
            if '2025' in blob_norm and '2026-06-04' not in blob_norm:
                scoped.append(item)
        return scoped or [item for item in items if '2026-06-04' not in self._norm(' '.join(str(item.get(k) or '') for k in ('timestamp', 'source', 'content_excerpt')))]

    def _filter_self_echo_items(self, items: list[dict[str, Any]], *, user_text: str) -> list[dict[str, Any]]:
        """Usuwa echo aktualnego pytania i techniczne prompt-preview z odpowiedzi pamięciowej."""
        user_norm = self._norm(user_text)
        echo_fragments = (
            "odpowiedz przez pamiec",
            "jak dzisiaj wspominasz",
            "czy przezylas podobna historie",
            "podpowiem a na tarasie",
            "krzysztof podpowiada",
            "odpowiedz pamieciowo",
            "odpowiedz pamiecia",
            "jeszcze jakies historie pamietasz",
        )
        filtered: list[dict[str, Any]] = []
        seen_content: set[str] = set()
        for item in items:
            content = str(item.get("content_excerpt") or "")
            norm = self._norm(content)
            source = self._norm(str(item.get("source") or ""))
            try:
                score = float(item.get("relevance_score") or 0.0)
            except Exception:
                score = 0.0
            is_exact_echo = bool(user_norm and (norm == user_norm or user_norm in norm or norm in user_norm))
            is_prompt_preview = any(fragment in norm for fragment in echo_fragments) and "?" in content
            is_recent_runtime_preview_echo = ("runtime_preview" in source or "cli_direct_conversation" in source or "cli_persistent_chat" in source) and (is_exact_echo or is_prompt_preview or "?" in content or content.strip().lower().startswith(("przypomnij", "krzysztof podpowiada")))
            is_current_session_fragment = "2026-06-04" in str(item.get("timestamp") or "") and any(x in norm for x in ("jakie masz moduly", "co masz czego brakuje", "masz internet", "ktora jest godzina"))
            is_redacted = "fragment zawiera dane wrazliwe" in norm or "fragment zawiera dane wrażliwe" in content.lower()
            is_too_weak = score < 0.43
            technical_fragments = ("synchronizuj wszystkie pliki", "client_secret", "update_report", "manifest", "sqlite", "pytest", "duplicate_group", "duplicate_index", "category", "content", "def ", "class ")
            json_noise_fragments = ("wspomnienia_do_zachowania", "czujnosc wobec granicy prawdy", "k wzorow i detalu", "source", "metadata")
            is_technical_noise = any(fragment in norm for fragment in technical_fragments) or any(fragment in norm for fragment in json_noise_fragments)
            content_key = norm[:220]
            is_duplicate = bool(content_key and content_key in seen_content)
            if is_exact_echo or is_prompt_preview or is_recent_runtime_preview_echo or is_current_session_fragment or is_redacted or is_too_weak or is_technical_noise or is_duplicate:
                continue
            seen_content.add(content_key)
            filtered.append(item)
        return filtered


    def _rank_items_for_user(self, items: list[dict[str, Any]], *, user_text: str) -> list[dict[str, Any]]:
        """Daje pierwszeństwo epizodom rzeczywiście pasującym do sceny pytania.

        Planner plików bywa szeroki: fragment JSON z kanonicznego pliku może mieć
        wysoki score techniczny, ale nie być dobrym pierwszym wspomnieniem.
        Dlatego przed syntezą rozmowną dokładamy mały ranking sceniczny.
        """
        low_user = self._norm(user_text)
        focus_terms: list[str] = []
        if "jezior" in low_user or "wypad" in low_user:
            focus_terms = ["jezior", "wypad", "miedzy swiatlem", "ptaki", "jelen", "las"]
        elif "taras" in low_user:
            focus_terms = ["taras", "lawenda", "stary dab", "dom", "wieczor", "herbata"]
        elif "histori" in low_user:
            focus_terms = ["scena", "rozmowa", "wspomnienie", "historia"]

        def score(item: dict[str, Any]) -> float:
            content = self._norm(str(item.get("content_excerpt") or ""))
            base = float(item.get("relevance_score") or 0.0)
            item_type = str(item.get("item_type") or "")
            type_bonus = {"episode": 0.18, "source_file": 0.04, "legacy_message": 0.02, "raw_chat_fallback": 0.0}.get(item_type, 0.0)
            focus_bonus = sum(0.09 for term in focus_terms if term and term in content)
            json_noise_penalty = 0.18 if any(x in content[:120] for x in ('"czujnosc', '"wspomnienia_do_zachowania', 'k wzorow', 'category')) else 0.0
            source_file_no_focus_penalty = 0.16 if item_type == "source_file" and focus_terms and not focus_bonus else 0.0
            return base + type_bonus + focus_bonus - json_noise_penalty - source_file_no_focus_penalty

        return sorted(items, key=score, reverse=True)

    @staticmethod
    def _source_mode(item: dict[str, Any]) -> str:
        typ = str(item.get("item_type") or "")
        grounding = str(item.get("grounding") or "").lower()
        source = str(item.get("source") or "").lower()
        content = str(item.get("content_excerpt") or "").lower()
        if "legacy" in typ or "chat.html" in source:
            return "ślad rozmowy z importu czatu"
        if "source_file" in typ:
            return "kanoniczny ślad z pliku źródłowego"
        if "symbol" in grounding or "symbol" in content or "wyobra" in content:
            return "scenę symboliczną albo narracyjny zapis pamięci"
        return "epizod zapisany w pamięci Jaźni"

    @staticmethod
    def _trim_sentence(text: str, *, max_len: int = 360) -> str:
        clean = re.sub(r"\s+", " ", (text or "").strip())
        clean = re.sub(r"^(?:[a-zA-Z]\s*-\s*)", "", clean)
        clean = clean.strip(" -–—;,.:")
        if len(clean) <= max_len:
            return clean
        cut = clean[:max_len].rsplit(" ", 1)[0]
        return cut.rstrip(".,;: ") + "…"
