from __future__ import annotations

import re

from latka_jazn.nlp_reasoning.models import ReplyPolicy, SemanticFrame
from latka_jazn.nlp_reasoning.normalizer import fold_polish


def infer_semantic_frame(source_text: str, normalized_text: str) -> tuple[SemanticFrame, ReplyPolicy]:
    folded = fold_polish(normalized_text)
    evidence: list[str] = []
    tone: list[str] = []
    speech_act = "question" if "?" in normalized_text else "statement"
    intent = "ordinary_conversation"
    question_object = None
    requires_time = False
    requires_memory = False
    requires_diagnostic = False
    allow_online_lookup = False
    allow_poetic = False
    repeat_key = None

    if any(x in folded for x in ("ktora jest godzina", "ktora godzina", "jaki jest czas")):
        speech_act = "question"
        intent = "current_time_question"
        question_object = "current_time"
        requires_time = True
        evidence.append("pytanie o aktualny czas po normalizacji literówki")
        repeat_key = "current_time"
    elif any(x in folded for x in ("co dokladnie odpowiedzial runtime", "co runtime odpowiedzial", "cytat runtime")):
        intent = "runtime_exact_quote_request"
        question_object = "exact_runtime_text"
        requires_diagnostic = True
        evidence.append("pytanie o dokładny tekst odpowiedzi runtime")
        repeat_key = "runtime_exact_quote"
    elif any(x in folded for x in ("sprawdz wszystko w systemie", "wszystko w systemie", "co nie dziala w systemie", "jak naprawic system", "audyt systemu jazni")):
        speech_act = "directive"
        intent = "system_repair_plan_request"
        question_object = "system_repair_plan"
        requires_diagnostic = True
        evidence.append("polecenie pełnego audytu i naprawy systemu Jaźni")
        repeat_key = "system_repair_plan"
    elif any(x in folded for x in ("jak sie czujesz", "jak samopoczucie", "co u ciebie")):
        intent = "self_state_question"
        question_object = "self_state"
        evidence.append("pytanie o modelowany stan operacyjny Jaźni")
        repeat_key = "self_state"
    elif any(x in folded for x in ("wspomn", "pamiet", "przezyci", "z calego 2025", "z 2025 roku")):
        intent = "memory_experience_question"
        question_object = "memory_experience"
        requires_memory = True
        evidence.append("pytanie albo follow-up o wspomnienia/przeżycia")
        repeat_key = "memory_experience"
    elif any(x in folded for x in ("moduly", "modul", "czego brakuje", "co masz")):
        intent = "module_inventory_request"
        question_object = "module_inventory"
        requires_diagnostic = True
        evidence.append("pytanie o moduły/możliwości/braki runtime")
        repeat_key = "module_inventory"
    elif any(x in folded for x in ("slownik", "sjp", "wsjp", "morfeusz", "nkjp", "plwordnet", "slowosiec")):
        intent = "dictionary_or_language_resource_question"
        question_object = "dictionary_resource"
        allow_online_lookup = True
        evidence.append("pytanie o zasoby językowe/słownikowe")
        repeat_key = "language_resource"
    elif any(x in folded for x in ("mrocz", "noc", "nocy", "ciemna")) and any(x in folded for x in ("witaj", "czesc", "dobry wieczor")):
        intent = "atmospheric_opening"
        question_object = "poetic_greeting"
        tone = ["poetic", "night", "dark_atmospheric"]
        allow_poetic = True
        evidence.append("nastrojowe, nocne otwarcie rozmowy; odpowiedź nie może być meta-raportem")
        repeat_key = "greeting_poetic_night"
    elif any(x in folded for x in ("denerwuja mnie", "zawiesilas sie", "taka sama odpowiedz", "powtarzasz")):
        intent = "runtime_behavior_diagnostic_request"
        requires_diagnostic = True
        evidence.append("użytkownik zgłasza błąd rozmowy lub powtórzenie")
        repeat_key = "runtime_repetition_bug"
    elif folded.strip() in {"dobranoc", "ide spac", "musze isc spac"}:
        intent = "sleep_closure_statement"
        question_object = "sleep_close"
        evidence.append("zamknięcie rozmowy")
        repeat_key = "sleep_close"
    elif re.fullmatch(r"(hejka|hej|czesc|witaj|dzien dobry|dobry wieczor)[!.,;: ]*", folded):
        intent = "standalone_greeting"
        question_object = "greeting"
        evidence.append("samodzielne powitanie")
        repeat_key = "greeting"
    elif (
        "?" not in normalized_text
        and len(folded.split()) <= 8
        and re.search(r"\b(super|swietnie|dobrze|fajnie|dziekuje|dzieki|ok|okej)\b", folded)
    ):
        speech_act = "feedback"
        intent = "positive_feedback_current_turn"
        question_object = "current_turn_feedback"
        evidence.append("krótki pozytywny feedback użytkownika")

    reply = ReplyPolicy(
        allow_poetic_reply=allow_poetic,
        avoid_meta_commentary=True,
        needs_citation=allow_online_lookup,
        llm_allowed=True,
        repeat_guard_key=repeat_key,
        source_grounding_required=allow_online_lookup,
    )
    frame = SemanticFrame(
        speech_act=speech_act,
        primary_intent=intent,
        tone=tone,
        question_object=question_object,
        requires_memory=requires_memory,
        requires_time=requires_time,
        requires_diagnostic=requires_diagnostic,
        allow_online_lookup=allow_online_lookup,
        evidence=evidence or ["brak mocnej intencji specjalistycznej; ordinary dialogue"],
    )
    return frame, reply
