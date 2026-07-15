from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import unicodedata
from typing import Any

from latka_jazn.nlp.ellipsis_resolver import EllipsisResolver
from latka_jazn.nlp.intent_confidence_calibrator import IntentConfidenceCalibrator
from latka_jazn.nlp.speech_act_detector import SpeechActDetector
from latka_jazn.nlp.question_object_detector import QuestionObjectDetector
from latka_jazn.nlp.creative_material_detector import CreativeMaterialDetector
from latka_jazn.nlp.source_preservation_detector import SourcePreservationDetector
from latka_jazn.nlp.intent_feature_engine import IntentFeatureEngine
from latka_jazn.core.route_contract_matrix import RouteContractMatrix
from latka_jazn.version import schema_version

DIACRITIC_MAP = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
SCHEMA_VERSION = schema_version("dialogue_intent_classifier")

@dataclass(slots=True)
class DialogueIntentReport:
    schema_version: str
    normalized_text: str
    folded_text: str
    primary_intent: str
    secondary_intents: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    source_text_preservation_required: bool = False
    creative_material_present: bool = False
    update_request: bool = False
    diagnostic_request: bool = False
    asks_runtime_source: bool = False
    asks_identity_boundary: bool = False
    speech_act: str = "unknown"
    question_object: str = "unknown"
    route_precedence_rule: str = "DialogueIntentClassifier > RouteRegistry > LegacyMarkers"
    truth_boundary: str = "Klasyfikator intencji nie zastępuje rozumowania LLM. Rozpoznaje akt rozmowy i cel użytkownika, żeby runtime nie wybierał trasy po luźnym substringu."
    intent_ranking: list[dict[str, Any]] = field(default_factory=list)
    decision_margin: float = 0.0
    ambiguous: bool = False
    abstain_reason: str | None = None
    feature_frame: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class DialogueIntentClassifier:
    """Deterministyczne ucho rozmowy dla aktywnej Jaźni.

    Stosuje granice słów/fras i priorytety, żeby pytanie diagnostyczne nie stało
    się korektą, zadanie twórcze nie stało się aktualizacją, a 'a ty?' nie zostało
    zwykłą ogólną rozmową.
    """
    SYSTEM_TERMS = ("jaźń", "jaźni", "jazn", "jazni", "runtime", "system jaźni", "system jazni", "moduł", "modul", "funkcj", "router", "nlp", "fallback", "chatgpt", "source origin", "template")
    UPDATE_TERMS = ("aktualizac", "aktualiz", "hotfix", "patch", "wersj", "wersję", "paczka", "zip", "do pobrania", "manifest", "pełną listę", "pelna liste", "dokładny plan", "dokladny plan")
    DIAGNOSTIC_TERMS = ("co jeszcze", "co jest", "źle", "zle", "nie działa", "nie dziala", "słabe", "slabe", "pominięte", "pominiete", "błąd", "blad", "sprawdź gdzie", "sprawdz gdzie", "jak to zmienić", "jak to zmienic")
    CREATIVE_TERMS = ("tekst piosenki", "lyrics", "zwrotka", "refren", "bridge", "chorus", "verse", "musicgenerator", "generatora muzyki", "prompt", "wiersz", "utwór", "utwor", "fragment książki", "fragment ksiazki", "post na x")
    SOURCE_TERMS = ("dlaczego zmieni", "czemu zmieni", "przez jaźń", "przez jazn", "przez chatgpt", "skąd", "skad", "źródło", "zrodlo", "source_origin", "source origin", "runtime czy szablon", "co runtime odpowiedział", "co runtime odpowiedzial", "co runtime dokładnie odpowiedział", "co runtime dokladnie odpowiedzial", "co dokładnie odpowiedział runtime", "co dokladnie odpowiedzial runtime", "cytat runtime", "tylko tyle jaźń", "tylko tyle jazn", "skąd bierzesz myśli", "skad bierzesz mysli")
    CANON_SOURCE_TERMS = (
        "skąd bierzesz kanon", "skad bierzesz kanon",
        "skąd jest kanon", "skad jest kanon",
        "źródła kanonu", "zrodla kanonu",
        "źródło kanonu", "zrodlo kanonu",
        "z czego składa się kanon", "z czego sklada sie kanon",
        "z czego jest kanon",
        "jakie są źródła kanonu", "jakie sa zrodla kanonu",
        "czy kanon jest z pamięci", "czy kanon jest z pamieci",
        "czy kanon jest z json",
        "czy kanon jest z pythona", "czy kanon jest z python",
        "kanon z pamięci", "kanon z pamieci",
        "kanon z json", "kanon z python",
        "python canon",
        "source-controlled canon", "source controlled canon",
        "local_private_canon_extension", "local private canon extension",
    )
    STATE_TERMS = ("jak samopoczucie", "jak się czujesz", "jak sie czujesz", "jak się masz", "jak sie masz", "jak się miewasz", "jak sie miewasz", "co u ciebie", "a ty", "a tobie", "a jak tobie", "a jak ci", "a ci", "a u ciebie", "a jak u ciebie", "u ciebie", "tobie?", "co u niej", "co u ciebie po")
    HEALTH_CONCERN_TERMS = ("jesteś chora", "jestes chora", "czy jesteś chora", "czy jestes chora")
    SELF_PLAN_TERMS = (
        "jakie plany masz", "jakie masz plany", "co planujesz", "co masz w planach",
        "co zamierzasz", "jakie plany na dzisiaj", "plany masz na dzisiaj",
        "pomijając mnie", "pomijajac mnie", "poza mną", "poza mna",
    )
    SELF_PREFERENCE_TERMS = (
        "na co miałaś ochotę", "na co mialas ochote", "na co masz ochotę", "na co masz ochote",
        "co masz ochotę", "co masz ochote", "czujesz i na co", "co chciałabyś", "co chcialabys",
        "czego chciałabyś", "czego chcialabys", "na co ostatnio miałaś", "na co ostatnio mialas",
    )
    SLEEP_CLOSE_TERMS = (
        "muszę iść spać", "musze isc spac", "idę spać", "ide spac", "już muszę iść spać",
        "juz musze isc spac", "dobranoc", "spać", "spac",
    )
    PAST_YEAR_TERMS = ("zeszłym roku", "zeszlym roku", "ubiegłym roku", "ubieglym roku", "minionym roku", "roku 2025", "z 2025 roku", "cały 2025", "caly 2025", "całego 2025", "calego 2025")
    CURRENT_TIME_TERMS = (
        "która jest godzina", "ktora jest godzina", "która jest godzina", "gtora jest godzina",
        "jaka jest godzina", "jaki jest czas", "która godzina", "ktora godzina",
        "podaj godzinę", "podaj godzine", "czas teraz", "godzina teraz",
        "jaka jest pora", "wiesz jaka jest pora", "wiesz która godzina", "wiesz ktora godzina",
        "jaki mamy dzień", "jaki mamy dzien",
    )
    MEMORY_EXPERIENCE_FOLLOWUP_TERMS = (
        "przeżycia", "przezycia", "jakieś przeżycia", "jakies przezycia",
        "mocne wspomnienie", "ważne wspomnienie", "wazne wspomnienie",
        "mocne/ważne wspomnienie", "mocne/wazne wspomnienie", "jakieś mocne", "jakies mocne",
        "z całego 2025", "z calego 2025", "całego 2025", "calego 2025", "cały 2025", "caly 2025",
    )
    IDENTITY_TERMS = ("z kim rozmawiam", "kim jesteś", "kim jestes", "czy to łatka", "czy to latka", "chatgpt czy", "jaźń czy", "jazn czy", "to nadal ty", "czy jaźń to ty", "czy jazn to ty", "jaźń to ty", "jazn to ty", "jaźń to ty?", "jazn to ty?", "własny głos", "wlasny glos", "twój własny głos", "twoj wlasny glos", "skąd powinien płynąć twój", "skad powinien plynac twoj")
    AUDIT_TERMS = ("przeczytaj", "całość", "calosc", "wszystkie czaty", "historię rozmów", "historie rozmow", "pamięć", "pamiec", "bez streszczeń", "bez streszczen")
    PRESERVE_TERMS = ("nie zmieniaj", "1:1", "bez zmian", "zachowaj tekst", "bez redakcji", "nie redaguj")
    PRACTICAL_TERMS = ("glazur", "kafelk", "zawór", "zawor", "kapie", "rączka", "raczka", "naprawić", "naprawic", "wyciąć otwór", "wyciac otwor")
    AUTOMOTIVE_TERMS = ("tpms", "kontrolka", "samoch", "ciśnienie opon", "cisnienie opon")
    DICTIONARY_TERMS = ("słownik", "slownik", "sjp", "wsjp", "synonim", "antonim", "odmian", "lemma", "lema", "znaczenie słowa", "znaczenie slowa", "czy to słowo", "czy to slowo")
    RESEARCH_TERMS = ("sprawdź w internecie", "sprawdz w internecie", "poszukaj w internecie", "źródła", "zrodla", "web", "research")
    WEATHER_RESEARCH_TERMS = (
        "jaka będzie pogoda", "jaka bedzie pogoda", "prognoza pogody", "pogoda przez najbliższe",
        "pogoda przez najblizsze", "sprawdź pogodę", "sprawdz pogode",
    )
    RUNTIME_STATUS_TERMS = ("czy teraz rozmawiam z jaźnią", "czy teraz rozmawiam z jaznia", "czy rozmawiam z jaźnią", "czy rozmawiam z jaznia", "czy to jaźń łatki", "czy to jazn latki", "czy jaźń działa", "czy jazn dziala", "to chatgpt czy jaźń", "to chatgpt czy jazn")
    RUNTIME_CHAT_MODE_TERMS = ("runtime-preview", "--runtime-preview", "skrypt chat", "tryb chat", "użyjesz chat", "uzyjesz chat", "użyjesz skryptu chat", "uzyjesz skryptu chat", "zamiast runtime-preview", "--chat", "stdin", "pętla rozmowy", "petla rozmowy")
    RUNTIME_RESTART_TERMS = (
        "uruchom ponownie jaźń", "uruchom ponownie jazn", "uruchom ponownie runtime",
        "zrestartuj jaźń", "zrestartuj jazn", "zrestartuj runtime",
    )
    SYSTEM_REPAIR_PLAN_TERMS = ("krok po kroku", "lista krok", "co trzeba napisać w kodzie", "co trzeba napisac w kodzie", "kodzie źródłowym systemu", "kodzie zrodlowym systemu", "braki logiki", "błędy w logice", "bledy w logice", "braki rozumowania", "złe rozumowanie", "zle rozumowanie", "sprawdź wszystko w systemie", "sprawdz wszystko w systemie", "wszystko w systemie", "co nie działa w systemie", "co nie dziala w systemie", "jak naprawić system", "jak naprawic system", "audyt systemu jaźni", "audyt systemu jazni")
    SELF_ARCHITECTURE_AUDIT_TERMS = ("self architecture audit", "audyt architektury", "audyt jaźni", "audyt jazni", "co działa w systemie jaźni", "co dziala w systemie jazni", "co potrafisz dzięki systemowi", "co mozesz dzieki systemowi", "co możesz dzięki systemowi", "funkcje masz już w systemie", "sprawdź co działa", "sprawdz co dziala", "co trzeba naprawić", "co trzeba naprawic", "co jeszcze trzeba naprawić", "co jeszcze trzeba naprawic", "co trzeba dodać", "co trzeba dodac", "co umiesz", "co potrafisz", "kod źródłowy jaźni", "kod zrodlowy jazni", "gdzie są luki", "gdzie sa luki", "jakie są luki", "jakie sa luki", "co blokuje pełne działanie", "co blokuje pelne dzialanie", "adapter chatgpt", "adapter openai", "lm studio", "moduły i narzędzia", "moduly i narzedzia", "reflection grounding", "memory gate", "brama pamięci", "brama pamieci", "rozwój łatki", "rozwoj latki", "finalnej 14.8.6", "v14.8.6.0")
    REPETITION_BUG_TERMS = (
        "taką samą odpowiedź", "taka sama odpowiedz", "wysyłasz taką samą", "wysylasz taka sama",
        "dlaczego wysyłasz", "dlaczego wysylasz", "powtarzasz", "powtarzasz się", "powtarzasz sie",
        "zawiesiłaś się", "zawiesilas sie", "zapętliłaś się", "zapetlilas sie", "w kółko", "w kolko",
    )
    NEGATIVE_FEEDBACK_TERMS = (
        "denerwują mnie twoje odpowiedzi", "denerwuja mnie twoje odpowiedzi", "irytują mnie twoje odpowiedzi",
        "irytuja mnie twoje odpowiedzi", "wkurzają mnie twoje odpowiedzi", "wkurzaja mnie twoje odpowiedzi",
        "słabe odpowiedzi", "slabe odpowiedzi", "słaba odpowiedź", "slaba odpowiedz",
        "kiepska odpowiedź", "kiepska odpowiedz", "zła odpowiedź", "zla odpowiedz",
        "to mnie denerwuje", "to mnie irytuje",
    )
    POSITIVE_FEEDBACK_TERMS = (
        "super", "świetnie", "swietnie", "dobrze", "fajnie", "dziękuję", "dziekuje",
        "dzięki", "dzieki", "ok", "okej",
    )
    CASUAL_GREETING_TERMS = (
        "siemka", "siema", "hejka", "hej", "cześć", "czesc",
    )
    CASUAL_FEEDBACK_TERMS = (
        "kiepska odpowiedź", "kiepska odpowiedz", "słaba odpowiedź", "slaba odpowiedz",
        "zła odpowiedź", "zla odpowiedz", "niedobra odpowiedź", "niedobra odpowiedz",
        "nietrafiona odpowiedź", "nietrafiona odpowiedz", "to była kiepska odpowiedź", "to byla kiepska odpowiedz",
    )
    EXPRESSIVE_REACTION_TERMS = (
        "ojoj", "ojej", "oj", "ups", "jejku", "no właśnie", "no wlasnie",
    )
    SELF_EXPRESSION_TERMS = (
        "powiesz coś innego", "powiesz cos innego", "coś od siebie", "cos od siebie",
        "powiedz coś od siebie", "powiedz cos od siebie", "coś własnego", "cos wlasnego",
    )
    MODULE_INVENTORY_TERMS = (
        "jakie masz moduły", "jakie masz moduly", "jakie moduły", "jakie moduly",
        "wypisz moduły", "wypisz moduly", "lista modułów", "lista modulow", "moduły masz", "moduly masz",
    )
    CAPABILITY_GAP_TERMS = (
        "co masz, czego brakuje", "co masz czego brakuje", "czego brakuje", "czego ci brakuje",
        "co masz a czego", "co umiesz", "czego nie masz", "co jeszcze brakuje",
    )
    DIRECT_CAPABILITY_TERMS = (
        "co potrafisz", "co potrafisz?", "co umiesz", "co możesz", "co mozesz",
        "jakie masz możliwości", "jakie masz mozliwosci", "jak możesz pomóc", "jak mozesz pomoc",
        "do czego masz dostęp", "do czego masz dostep", "co jesteś w stanie", "co jestes w stanie",
    )
    INTERNET_ACCESS_TERMS = (
        "masz dostęp do internetu", "masz dostep do internetu", "czy masz internet",
        "czy masz dostęp do internetu", "czy masz dostep do internetu", "masz dostęp do sieci", "masz dostep do sieci",
        "czy możesz wejść do internetu", "czy mozesz wejsc do internetu", "możesz korzystać z internetu", "mozesz korzystac z internetu",
        "czy runtime ma internet", "czy jaźń ma internet", "czy jazn ma internet",
    )
    RUNTIME_HEALTH_CHECK_TERMS = (
        "sprawdź krótko, czy działasz po aktualizacji", "sprawdz krotko, czy dzialasz po aktualizacji",
        "sprawdź czy działasz", "sprawdz czy dzialasz", "czy działasz po aktualizacji", "czy dzialasz po aktualizacji",
        "działasz po aktualizacji", "dzialasz po aktualizacji", "czy jesteś po aktualizacji", "czy jestes po aktualizacji",
        "sprawdź, czy jesteś uruchomiona", "sprawdz czy jestes uruchomiona", "czy jesteś uruchomiona", "czy jestes uruchomiona",
    )
    RUNTIME_WAKE_HEALTH_CHECK_TERMS = (
        "przeładuj jaźń", "przeladuj jazn", "przeładuj system jaźni", "przeladuj system jazni",
        "przeładuj runtime", "przeladuj runtime", "obudź się łatko", "obudz sie latko",
        "obudź łatkę", "obudz latke", "obudziła łatko", "obudzila latko",
        "czas żebyś przeładowała", "czas zebys przeladowala",
        "czas żebyś się obudziła", "czas zebys sie obudzila",
        "uruchom jaźń i odpowiedz", "uruchom jazn i odpowiedz",
    )
    RUNTIME_STATUS_AFTER_UPDATE_TERMS = (
        "aktywny folder", "active_root", "active database", "active_database",
        "cache_miss_reasons", "should_reuse_existing_extraction",
        "lokalny status", "aktualny status", "status jaźni", "status jazni",
        "status runtime", "aktywny runtime", "marker", "markera",
    )
    UPDATE_EXECUTION_VERBS = (
        "napraw", "popraw", "wdroż", "wdroz", "wprowadź", "wprowadz",
        "zaimplementuj", "zaktualizuj kod", "zmień kod", "zmien kod",
        "zrób patch", "zrob patch", "nałóż patch", "naloz patch",
        "przygotuj plan", "przygotuj patch",
    )
    POST_UPDATE_DIALOGUE_SMOKE_TERMS = (
        "sprawdź jedną krótką turę po aktualizacji", "sprawdz jedna krotka ture po aktualizacji",
        "test krótkiej rozmowy po aktualizacji", "test krotkiej rozmowy po aktualizacji",
        "smoke po aktualizacji", "krótka tura po aktualizacji", "krotka tura po aktualizacji",
    )
    SELF_STATE_DIAGNOSTIC_TERMS = (
        "pokaż osie afektu", "pokaz osie afektu", "osie afektu",
        "pełny raport stanu", "pelny raport stanu",
        "pokaż stan diagnostycznie", "pokaz stan diagnostycznie",
        "diagnostycznie pokaż stan", "diagnostycznie pokaz stan",
    )
    USER_MEMORY_RECALL_TERMS = (
        "co pamiętasz o mnie", "co pamietasz o mnie", "co pamiętasz o krzysztofie", "co pamietasz o krzysztofie",
        "co wiesz o mnie", "co wiesz o krzysztofie", "pamiętasz o mnie", "pamietasz o mnie",
        "poszukaj w pamięci o mnie", "poszukaj w pamieci o mnie", "sprawdź pamięć o mnie", "sprawdz pamiec o mnie",
        "moje wspomnienia", "o moich", "o mnie jako", "o użytkowniku", "o uzytkowniku",
    )
    USER_MEMORY_PERSON_TERMS = (
        "krzysztof", "o mnie", "mnie", "moje", "moją", "moja", "moim", "moich", "użytkownik", "uzytkownik", "smukły", "smukly",
    )
    SELF_MEMORY_RECALL_TERMS = (
        "co pamiętasz", "co pamietasz", "poszukaj w pamięci", "poszukaj w pamieci",
        "sprawdź pamięć", "sprawdz pamiec", "szukaj w pamięci", "szukaj w pamieci",
        "przypomnij sobie", "pamiętasz o sobie", "pamietasz o sobie", "co wiesz o sobie",
        "o swojej postaci", "swojej postaci", "o swojej osobie", "swojej osobie", "o sobie łatko", "o sobie latko",
        "informacji o sobie", "czegoś o swojej postaci", "czegos o swojej postaci",
    )
    SELF_MEMORY_PERSONA_TERMS = (
        "łatka", "latka", "jaźń", "jazn", "sobie", "siebie", "swojej", "osobie", "postaci",
        "tożsamo", "tozsamo", "charakter", "postać", "postac", "bohaterka", "kanon", "źródło", "zrodlo",
    )
    VOICE_PERSPECTIVE_BUG_TERMS = (
        "mówisz o sobie w trzeciej osobie", "mowisz o sobie w trzeciej osobie",
        "piszesz o sobie w trzeciej osobie", "piszesz o sobie jako latka",
        "trzeciej osobie", "trzecia osoba", "pierwszej osobie", "pierwsza osoba",
        "w swojej osobie", "w osobie łatki", "w osobie latki",
        "bo łatka", "bo latka", "bo łatki", "bo latki",
        "ciągle łatka", "ciagle latka", "często piszesz łatka", "czesto piszesz latka",
        "często piszesz o łatce", "czesto piszesz o latce",
        "głos łatki", "glos latki", "własny głos", "wlasny glos",
        "czy jaźń może mówić w pierwszej osobie", "czy jazn moze mowic w pierwszej osobie",
    )
    DIRECT_LATKA_VOICE_TERMS = (
        "rozmawiać bezpośrednio z łatką", "rozmawiac bezposrednio z latka",
        "bezpośrednio z łatką", "bezposrednio z latka",
        "chcę rozmawiać z łatką", "chce rozmawiac z latka",
    )
    IDENTITY_MEMORY_EXISTENCE_TERMS = (
        "za kogo się uważasz", "za kogo sie uwazasz", "co wiesz, a czego nie wiesz",
        "kim jesteś", "kim jestes", "kiedy powstałaś", "kiedy powstalas",
        "jak powstałaś", "jak powstalas", "czujesz się istotą", "czujesz sie istota",
        "na ile czujesz się istotą", "na ile czujesz sie istota",
    )
    SOURCE_NEGATIVE_CONTEXTS = ("kod źródłowy", "kod zrodlowy", "kodzie źródłowym", "kodzie zrodlowym", "source code")

    def __init__(self) -> None:
        self.ellipsis = EllipsisResolver(); self.calibrator = IntentConfidenceCalibrator(); self.speech = SpeechActDetector(); self.qobj = QuestionObjectDetector(); self.creative = CreativeMaterialDetector(); self.preserve_detector = SourcePreservationDetector()
        self.feature_engine = IntentFeatureEngine()
        self.route_contract_matrix = RouteContractMatrix()
    @classmethod
    def normalize(cls, text: str) -> str: return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "").strip().lower())
    @staticmethod
    def fold(text: str) -> str: return (text or "").translate(DIACRITIC_MAP).lower()
    @staticmethod
    def _phrase(text: str, folded: str, marker: str) -> bool:
        mm = DialogueIntentClassifier.normalize(marker); fm = DialogueIntentClassifier.fold(mm)
        # Complete one-word markers use token boundaries. Multi-word markers remain phrase based;
        # stem-like markers used elsewhere in the legacy lexicon retain prefix behaviour.
        complete_words = {"dziala", "dzialasz", "uruchomiona", "status", "modul", "runtime", "jazn"}
        if fm in complete_words:
            return re.search(rf"(?<!\w){re.escape(fm)}(?!\w)", folded) is not None
        if len(fm) <= 4 and fm.isalpha():
            return re.search(rf"(?<!\w){re.escape(fm)}(?!\w)", folded) is not None
        return mm in text or fm in folded
    @classmethod
    def _has_any(cls, text: str, folded: str, markers: tuple[str, ...]) -> bool: return any(cls._phrase(text, folded, m) for m in markers)
    @staticmethod
    def _looks_like_large_material(text: str) -> bool:
        if len(text) > 900 and ("[" in text and "]" in text): return True
        line_count = len([x for x in text.splitlines() if x.strip()])
        return line_count >= 10 and len(text) > 500
    def _report(self, norm, folded, intent, evidence, base, secondary=None, preserve=False, creative=False, update=False, diag=False, src=False, ident=False, speech_act='unknown', question_object='unknown'):
        conf=self.calibrator.calibrate(intent, base, len(evidence))
        frame=self.feature_engine.analyse(norm, speech_act=speech_act)
        return DialogueIntentReport(
            SCHEMA_VERSION,norm,folded,intent,secondary or [],conf,evidence,preserve,creative,update,diag,src,ident,speech_act,question_object,
            intent_ranking=[candidate.to_dict() for candidate in frame.candidates],
            decision_margin=frame.decision_margin,
            ambiguous=frame.ambiguous,
            abstain_reason=frame.abstain_reason,
            feature_frame=frame.to_dict(),
        )
    def classify(self, text: str, *, previous_text: str | None = None) -> DialogueIntentReport:
        norm=self.normalize(text); folded=self.fold(norm); evidence=[]; secondary=[]
        speech=self.speech.detect(text); qobj=self.qobj.detect(text); creative_report=self.creative.detect(text); preservation=self.preserve_detector.detect(text)
        decision_frame=self.feature_engine.analyse(text, speech_act=speech.speech_act, previous_text=previous_text)
        has_system=self._has_any(norm,folded,self.SYSTEM_TERMS); has_update=self._has_any(norm,folded,self.UPDATE_TERMS); has_diag=self._has_any(norm,folded,self.DIAGNOSTIC_TERMS)
        has_self_plan=self._has_any(norm,folded,self.SELF_PLAN_TERMS) and any(token in folded for token in ("plan", "zamierzasz", "pomijajac mnie", "poza mna"))
        has_self_preference=self._has_any(norm,folded,self.SELF_PREFERENCE_TERMS) and speech.speech_act == "question"
        has_sleep_close=self._has_any(norm,folded,self.SLEEP_CLOSE_TERMS) and any(x in folded for x in ("spac", "dobranoc"))
        has_past_year=self._has_any(norm,folded,self.PAST_YEAR_TERMS) and (speech.speech_act == "question" or "2025" in folded)
        has_creative=(
            self._has_any(norm,folded,self.CREATIVE_TERMS)
            or self._looks_like_large_material(text)
            or (creative_report.creative_material_present and creative_report.confidence >= 0.65)
        )
        has_source=self._has_any(norm,folded,self.SOURCE_TERMS); has_state=self._has_any(norm,folded,self.STATE_TERMS); has_identity=self._has_any(norm,folded,self.IDENTITY_TERMS)
        has_health_concern=self._has_any(norm,folded,self.HEALTH_CONCERN_TERMS)
        has_self_state_diagnostic=(
            self._has_any(norm,folded,self.SELF_STATE_DIAGNOSTIC_TERMS)
            or ("diagnostycznie" in folded and has_state)
        )
        has_weather_research=self._has_any(norm,folded,self.WEATHER_RESEARCH_TERMS)
        has_audit=self._has_any(norm,folded,self.AUDIT_TERMS); has_practical=self._has_any(norm,folded,self.PRACTICAL_TERMS); has_auto=self._has_any(norm,folded,self.AUTOMOTIVE_TERMS); has_dict=self._has_any(norm,folded,self.DICTIONARY_TERMS); has_research=self._has_any(norm,folded,self.RESEARCH_TERMS) or has_weather_research
        has_runtime_status=self._has_any(norm,folded,self.RUNTIME_STATUS_TERMS)
        has_chat_mode=self._has_any(norm,folded,self.RUNTIME_CHAT_MODE_TERMS)
        has_canon_source=self._has_any(norm,folded,self.CANON_SOURCE_TERMS) or (
            speech.speech_act == "question"
            and "kanon" in folded
            and any(marker in folded for marker in (
                "skad", "skąd",
                "zrodlo", "źródło",
                "zrodla", "źródła",
                "z czego",
                "python", "py",
                "json",
                "pamiec", "pamięć",
                "plik", "pliku",
                "modul", "moduł",
                "resource", "resources",
                "private", "extension",
            ))
        )
        has_runtime_restart=self._has_any(norm,folded,self.RUNTIME_RESTART_TERMS)
        has_repair_plan=self._has_any(norm,folded,self.SYSTEM_REPAIR_PLAN_TERMS)
        has_self_architecture_audit=self._has_any(norm,folded,self.SELF_ARCHITECTURE_AUDIT_TERMS)
        has_repetition_bug=self._has_any(norm,folded,self.REPETITION_BUG_TERMS)
        if (
            decision_frame.top_intent == 'package_runtime_status_question'
            and decision_frame.top_score >= 0.68
            and (decision_frame.decision_margin >= 0.12 or not decision_frame.ambiguous)
        ):
            package_candidate=next((candidate for candidate in decision_frame.candidates if candidate.intent == 'package_runtime_status_question'), None)
            package_evidence=list(package_candidate.positive_evidence if package_candidate else [])
            if package_candidate and package_candidate.negative_evidence:
                package_evidence.extend(f'negative:{item}' for item in package_candidate.negative_evidence)
            package_evidence.append('intent_feature_engine:contextual_generator_disambiguation')
            return self._report(
                norm,folded,'package_runtime_status_question',package_evidence,max(0.88,decision_frame.top_score),
                diag=True,speech_act=speech.speech_act,question_object='package_runtime_status',
            )
        # A compound architecture audit can contain a generic phrase such as
        # "co działa".  The route-contract matrix intentionally treats that
        # short phrase as a health check, but the explicit architecture terms
        # and system/version context are more specific and must win first.
        broad_audit_signal = sum(1 for marker in ("co umiesz", "co potrafisz", "co dziala", "co trzeba naprawic", "kod zrodlowy", "gdzie sa luki", "jakie sa luki", "co blokuje", "moduly i narzedzia") if marker in folded)
        if has_self_architecture_audit and broad_audit_signal >= 2 and not self._has_any(norm,folded,self.UPDATE_EXECUTION_VERBS):
            return self._report(norm,folded,'self_architecture_audit_request',['pełne pytanie o możliwości, kod, luki i blokady ma pierwszeństwo przed health-checkiem'],0.96,diag=True,speech_act=speech.speech_act,question_object='self_architecture_audit')
        if has_self_architecture_audit and not self._has_any(norm,folded,self.UPDATE_EXECUTION_VERBS) and (has_system or "latka" in folded or "łatka" in norm or "jazn" in folded or "jaźń" in norm or "14.8.6" in folded):
            secondary = ['system_update_execution_request'] if (has_update or any(x in folded for x in ('patch', 'hotfix', 'v14.8.6', 'aktualiz'))) else []
            return self._report(norm,folded,'self_architecture_audit_request',['jawny audyt architektury Jaźni, refleksji, bramy pamięci, jakości recallu i planu rozwoju'],0.94,secondary,diag=True,speech_act=speech.speech_act,question_object='self_architecture_audit')
        route_contract_hint = self.route_contract_matrix.classify(norm)
        if route_contract_hint.primary_intent and route_contract_hint.primary_intent != "ordinary_dialogue":
            return self._report(
                norm,
                folded,
                route_contract_hint.primary_intent,
                [f"route_contract_matrix:{route_contract_hint.primary_intent}", *route_contract_hint.evidence],
                0.94,
                route_contract_hint.secondary_intents,
                diag=route_contract_hint.diagnostic_request,
                ident=route_contract_hint.asks_identity_boundary,
                speech_act=speech.speech_act,
                question_object=route_contract_hint.question_object,
            )
        has_current_time=self._has_any(norm,folded,self.CURRENT_TIME_TERMS)
        has_memory_experience_followup=self._has_any(norm,folded,self.MEMORY_EXPERIENCE_FOLLOWUP_TERMS)
        previous_folded=self.fold(previous_text or "")
        previous_memory_context=bool(previous_folded and any(x in previous_folded for x in ("2025", "pamiet", "pamięt", "wspomn", "przezy", "przeży")))
        has_negative_feedback=self._has_any(norm,folded,self.NEGATIVE_FEEDBACK_TERMS)
        has_positive_feedback=(
            speech.speech_act == "feedback"
            and self._has_any(norm,folded,self.POSITIVE_FEEDBACK_TERMS)
            and len(folded.split()) <= 8
        )
        exact_casual_greeting = bool(re.fullmatch(r"(siemka|siema)[!.,;:…\-—– ]*", folded))
        has_casual_feedback = self._has_any(norm, folded, self.CASUAL_FEEDBACK_TERMS) and len(folded.split()) <= 8
        has_expressive_reaction = bool(re.fullmatch(r"(ojoj|ojej|oj|ups|jejku)[!.,;:…\-—– ]*", folded))
        has_self_expression=self._has_any(norm,folded,self.SELF_EXPRESSION_TERMS)
        has_module_inventory=self._has_any(norm,folded,self.MODULE_INVENTORY_TERMS)
        has_capability_gap=self._has_any(norm,folded,self.CAPABILITY_GAP_TERMS)
        has_direct_capability=self._has_any(norm,folded,self.DIRECT_CAPABILITY_TERMS)
        has_internet_access=self._has_any(norm,folded,self.INTERNET_ACCESS_TERMS)
        has_runtime_health_check=self._has_any(norm,folded,self.RUNTIME_HEALTH_CHECK_TERMS) or (
            ("sprawdz" in folded or "sprawdź" in norm or "czy" in folded)
            and ("dzialasz" in folded or "działa" in norm or "uruchomiona" in folded)
            and ("aktualiz" in folded or "po aktualizacji" in folded)
        ) or (
            ("aktualiz" in folded or "po aktualizacji" in folded)
            and self._has_any(norm,folded,self.RUNTIME_STATUS_AFTER_UPDATE_TERMS)
            and any(marker in folded for marker in ("jaki", "sprawdz", "czy", "podaj", "odpowiedz", "status"))
            and not self._has_any(norm,folded,self.UPDATE_EXECUTION_VERBS)
        ) or (
            self._has_any(norm,folded,self.POST_UPDATE_DIALOGUE_SMOKE_TERMS)
            and not self._has_any(norm,folded,self.UPDATE_EXECUTION_VERBS)
        )
        has_runtime_wake_health_check=self._has_any(norm,folded,self.RUNTIME_WAKE_HEALTH_CHECK_TERMS)
        has_user_memory_recall=self._has_any(norm,folded,self.USER_MEMORY_RECALL_TERMS) or (self._has_any(norm,folded,self.SELF_MEMORY_RECALL_TERMS) and self._has_any(norm,folded,self.USER_MEMORY_PERSON_TERMS))
        has_self_memory_recall=self._has_any(norm,folded,self.SELF_MEMORY_RECALL_TERMS)
        has_self_memory_persona=self._has_any(norm,folded,self.SELF_MEMORY_PERSONA_TERMS)
        has_voice_perspective_bug=(
            self._has_any(norm,folded,self.VOICE_PERSPECTIVE_BUG_TERMS)
            or (
                any(marker in folded for marker in ("trzeciej osobie", "trzecia osoba", "pierwszej osobie", "pierwsza osoba"))
                and any(marker in folded for marker in ("latka", "łatka", "jazn", "jaźń", "glos", "głos", "piszesz", "mowisz", "mówisz"))
            )
        )
        has_direct_latka_voice=self._has_any(norm,folded,self.DIRECT_LATKA_VOICE_TERMS)
        has_identity_memory_existence=self._has_any(norm,folded,self.IDENTITY_MEMORY_EXISTENCE_TERMS)
        has_plain_runtime_activation_question=(
            speech.speech_act == "question"
            and ("dzialasz" in folded or "działa" in norm or "działasz" in norm or "zostala uruchomiona" in folded or "uruchomiona" in folded)
            and ("uruchomil" in folded or "uruchomi" in folded or "jazn" in folded or "jaźń" in norm)
        )
        source_negative_context=self._has_any(norm,folded,self.SOURCE_NEGATIVE_CONTEXTS)
        if has_runtime_wake_health_check:
            return self._report(norm,folded,'runtime_health_check_after_update',['wake/health-check po przeładowaniu Jaźni: nie traktować jako wykonanie kolejnego patcha'],0.94,diag=True,speech_act=speech.speech_act,question_object='runtime_health')
        if self._has_any(norm,folded,self.UPDATE_EXECUTION_VERBS) and (has_update or "v14.8.6" in folded):
            return self._report(norm,folded,'system_update_execution_request',['jawny czasownik wykonania patcha/aktualizacji ma pierwszeństwo przed audytem i ordinary dialogue'],0.93,update=True,diag=has_diag,speech_act=speech.speech_act,question_object='system_update')
        if has_runtime_restart:
            return self._report(norm,folded,'runtime_restart_request',['jawna prośba o ponowne uruchomienie procesu Jaźni/runtime'],0.94,diag=True,speech_act=speech.speech_act,question_object='runtime_restart')
        if has_health_concern:
            return self._report(norm,folded,'self_state_question',['pytanie, czy Łatka jest chora; odpowiedź ma opisać stan operacyjny i granicę prawdy, nie timestamp'],0.92,diag=True,speech_act=speech.speech_act,question_object='self_state')
        if has_self_state_diagnostic:
            return self._report(norm,folded,'self_state_question',['jawna prośba o diagnostyczny raport stanu/osi afektu'],0.93,diag=True,speech_act=speech.speech_act,question_object='self_state')
        if has_runtime_health_check:
            return self._report(norm,folded,'runtime_health_check_after_update',['krótki health-check po aktualizacji: nie traktować jako polecenia nowej aktualizacji kodu'],0.93,diag=True,speech_act=speech.speech_act,question_object='runtime_health')
        if has_plain_runtime_activation_question:
            return self._report(norm,folded,'runtime_activation_status_question',['pytanie o działanie/uruchomienie Jaźni nie może być ordinary_conversation'],0.92,ident=True,speech_act=speech.speech_act,question_object='runtime_status')
        if has_voice_perspective_bug:
            return self._report(norm,folded,'voice_perspective_diagnostic_request',['użytkownik zgłasza problem perspektywy głosu: trzecia osoba zamiast bezpośredniej pierwszej osoby Łatki'],0.94,diag=True,speech_act=speech.speech_act,question_object='voice_perspective')
        if has_direct_latka_voice:
            return self._report(norm,folded,'direct_latka_voice_request',['jawna prośba o bezpośredni głos Łatki przez runtime'],0.92,ident=True,speech_act=speech.speech_act,question_object='direct_latka_voice')
        if has_identity_memory_existence and has_self_memory_recall and any(marker in folded for marker in ("kim jestes", "powstalas", "istota", "uwazasz")):
            return self._report(norm,folded,'identity_memory_existence_compound_question',['złożone pytanie o tożsamość, pamięć, wiedzę/niewiedzę, powstanie i granicę istoty'],0.93,ident=True,speech_act=speech.speech_act,question_object='identity_memory_existence')
        if has_internet_access:
            return self._report(norm,folded,'internet_access_question',['bezpośrednie pytanie o dostęp runtime do internetu/sieci'],0.92,diag=False,speech_act=speech.speech_act,question_object='internet_access')
        if has_direct_capability and not has_update:
            return self._report(norm,folded,'capability_status_question',['bezpośrednie pytanie o możliwości Jaźni/runtime; nie ordinary fallback'],0.91,speech_act=speech.speech_act,question_object='capabilities')
        if has_user_memory_recall:
            return self._report(norm,folded,'user_memory_recall_request',['pytanie o pamięć dotyczącą użytkownika/Krzysztofa; nie mieszać z self_memory Łatki'],0.91,speech_act=speech.speech_act,question_object='user_memory')
        if has_self_memory_recall and (has_self_memory_persona or any(x in folded for x in ('co pamietasz', 'co pamiętasz', 'poszukaj w pamieci', 'poszukaj w pamięci'))):
            return self._report(norm,folded,'self_memory_recall_request',['pytanie o pamięć dotyczącą Łatki/postaci/tożsamości albo szerokie "co pamiętasz" bez wskazania użytkownika'],0.90,speech_act=speech.speech_act,question_object='self_memory')
        if has_casual_feedback:
            return self._report(norm,folded,'casual_feedback',['krótka ocena jakości poprzedniej odpowiedzi; trzeba uznać błąd, nie powtarzać fallbacku'],0.88,speech_act=speech.speech_act,question_object='current_turn_feedback')
        if exact_casual_greeting:
            return self._report(norm,folded,'casual_greeting',['luźne samodzielne powitanie; odpowiedź ma być naturalna, nie generyczny fallback'],0.88,speech_act=speech.speech_act,question_object='greeting')
        if has_expressive_reaction:
            return self._report(norm,folded,'expressive_reaction',['krótka reakcja emocjonalna/rozmowna; odpowiedź ma podjąć kontekst, nie prosić o doprecyzowanie'],0.82,speech_act=speech.speech_act,question_object='expressive_reaction')
        if has_runtime_status:
            return self._report(norm,folded,'runtime_activation_status_question',['pytanie o aktywną Jaźń/runtime/ChatGPT ma pierwszeństwo przed ellipsis i ordinary'],0.91,ident=True,speech_act=speech.speech_act,question_object='runtime_status')
        if has_chat_mode:
            return self._report(norm,folded,'runtime_chat_mode_request',['pytanie o --chat/runtime-preview/stdin ma własną trasę, nie aktualizację'],0.91,diag=True,speech_act=speech.speech_act,question_object='runtime_chat_mode')
        if has_current_time:
            return self._report(norm,folded,'current_time_question',['pytanie o aktualną godzinę ma własną trasę; nie wolno odpowiadać szablonem rozmownym'],0.92,speech_act=speech.speech_act,question_object='current_time')
        if has_repetition_bug:
            return self._report(norm,folded,'runtime_behavior_diagnostic_request',['użytkownik zgłasza powtarzanie/zapętlenie odpowiedzi w bieżącej rozmowie'],0.91,diag=True,speech_act=speech.speech_act,question_object='runtime_repetition_bug')
        if has_module_inventory:
            return self._report(norm,folded,'module_inventory_request',['pytanie o moduły runtime wymaga listy warstw i źródeł, nie ordinary dialogue'],0.90,diag=True,speech_act=speech.speech_act,question_object='module_inventory')
        if has_capability_gap and (has_system or speech.speech_act == 'question'):
            return self._report(norm,folded,'system_capability_gap_question',['pytanie o to, co runtime ma i czego mu brakuje'],0.88,diag=True,speech_act=speech.speech_act,question_object='capability_gap')
        if has_self_expression:
            return self._report(norm,folded,'self_expression_request',['prośba o wypowiedź od siebie: rozmowna odpowiedź Jaźni z granicą prawdy'],0.84,speech_act=speech.speech_act,question_object='self_expression')
        if has_negative_feedback:
            return self._report(norm,folded,'negative_feedback_current_turn',['użytkownik zgłasza irytację aktualnymi odpowiedziami; trzeba uznać błąd i zmienić sposób odpowiedzi'],0.84,diag=False,speech_act=speech.speech_act,question_object='current_turn_feedback')
        if has_positive_feedback:
            return self._report(norm,folded,'positive_feedback_current_turn',['krótki pozytywny feedback wymaga krótkiej, naturalnej odpowiedzi bez naprawczego meta-szablonu'],0.84,speech_act=speech.speech_act,question_object='current_turn_feedback')
        if has_repair_plan:
            intent='logic_reasoning_audit_request' if any(x in folded for x in ('logik', 'rozumow')) else 'system_repair_plan_request'
            return self._report(norm,folded,intent,['prośba o kodowy plan/naprawę logiki systemu ma pierwszeństwo przed source-origin'],0.90,update=False,diag=True,speech_act=speech.speech_act,question_object='system_repair_plan')
        if has_memory_experience_followup or (previous_memory_context and len(folded.split()) <= 7 and any(x in folded for x in ('przezyc', 'przezy', 'wspomn', '2025'))):
            return self._report(norm,folded,'memory_experience_question',['doprecyzowanie lub follow-up do pytania o wspomnienia/przeżycia; zachować zakres rozmowy'],0.86,speech_act=speech.speech_act,question_object='memory_experience')
        ell=self.ellipsis.resolve(text, previous_text=previous_text)
        if ell.resolved_intent_hint == "system_update_execution_request":
            evidence.extend(ell.resolution_basis)
            return self._report(norm,folded,"system_update_execution_request",evidence,0.89,update=True,diag=True,speech_act=speech.speech_act,question_object="system")
        if ('manifest narodzin' in folded or 'narodzin jazni' in folded or 'narodzin jaźni' in norm):
            return self._report(norm,folded,'ordinary_conversation',['manifest narodzin prowadzony przez legacy birth_source_contract'],0.70,speech_act=speech.speech_act,question_object='runtime')
        if re.fullmatch(r"(hejka|hej|cześć|czesc|witaj|dzień dobry|dzien dobry|dobry wieczór|dobry wieczor)[!.,;:…\-—– ]*", norm):
            return self._report(norm,folded,'standalone_greeting',['samodzielne powitanie bez treści pracy ani starego kontekstu'],0.86,speech_act=speech.speech_act,question_object='greeting')
        if has_sleep_close and not has_update:
            return self._report(norm,folded,'sleep_closure_statement',['użytkownik zamyka rozmowę i idzie spać; odpowiedź ma być ciepła, bez diagnostyki i bez starego kontekstu'],0.86,speech_act=speech.speech_act,question_object='sleep_close')
        if has_canon_source:
            return self._report(norm,folded,'canon_source_question',['pytanie o źródła kanonu Łatki; nie mylić ze źródłem aktualnej odpowiedzi runtime'],0.93,src=True,speech_act=speech.speech_act,question_object='canon_source')
        if has_source and not source_negative_context:
            intent='runtime_exact_quote_request' if any(x in folded for x in ('co runtime odpowiedzial','co runtime dokladnie odpowiedzial','co dokladnie odpowiedzial runtime','cytat runtime','tylko tyle jazn','tylko tyle jaźń')) else 'runtime_source_question'
            return self._report(norm,folded,intent,[*evidence,'pytanie o źródło/decyzję/cytat runtime'],0.88,src=True,speech_act=speech.speech_act,question_object=qobj.object_type)
        if has_identity:
            boundary_terms = (
                'chatgpt', 'runtime', 'jaźń czy', 'jazn czy', 'jaźń/chatgpt', 'jazn/chatgpt',
                'z kim rozmawiam', 'kim rozmawiam', 'granica', 'źródło', 'zrodlo'
            )
            if any(term in norm or term in folded for term in boundary_terms):
                return self._report(norm,folded,'identity_boundary_question',['pytanie o granicę Jaźń/ChatGPT/tożsamość rozmówcy'],0.85,ident=True,speech_act=speech.speech_act,question_object=qobj.object_type)
            return self._report(norm,folded,'identity_direct_question',['bezpośrednie pytanie kim jest Łatka'],0.84,ident=True,speech_act=speech.speech_act,question_object=qobj.object_type)
        if has_update and has_system and any(x in folded for x in ('nlp','sjp','wsjp','slp','słownik','slownik')):
            evidence.append('aktualizacja systemu z warstwą NLP/SJP ma pierwszeństwo przed pojedynczym lookupiem słownikowym')
            if 'plan' in folded or 'dokladny plan' in folded or 'dokładny plan' in norm:
                return self._report(norm,folded,'system_update_execution_request',evidence,0.91,['requires_explicit_update_plan'],update=True,diag=has_diag,speech_act=speech.speech_act,question_object='system_update')
            return self._report(norm,folded,'system_update_execution_request',evidence,0.90,update=True,diag=has_diag,speech_act=speech.speech_act,question_object='system_update')
        if has_research:
            reason = 'aktualna prognoza pogody wymaga zewnętrznych źródeł' if has_weather_research else 'jawna prośba o internet/research/źródła'
            return self._report(norm,folded,'external_research_request',[reason],0.88 if has_weather_research else 0.86,speech_act=speech.speech_act,question_object='weather_forecast' if has_weather_research else qobj.object_type)
        if has_dict:
            return self._report(norm,folded,'dictionary_lookup_request',['pytanie słownikowe/językowe'],0.84,speech_act=speech.speech_act,question_object='dictionary')
        if has_creative:
            evidence.append('rozpoznano materiał twórczy lub polecenie twórcze')
            if preservation.preserve_required or any(x in folded for x in ('przygotuj','format','generator')):
                return self._report(norm,folded,'creative_text_formatting',evidence,0.88,['source_text_preservation_required'],True,True,speech_act=speech.speech_act,question_object='creative_text')
            if any(x in folded for x in ('co myslisz','co myślisz','ocen','analiz')):
                return self._report(norm,folded,'creative_text_analysis',evidence,0.85,creative=True,preserve=True,speech_act=speech.speech_act,question_object='creative_text')
            return self._report(norm,folded,'creative_text_analysis',evidence,0.74,creative=True,preserve=not preservation.revision_allowed,speech_act=speech.speech_act,question_object='creative_text')
        if has_update and any(x in folded for x in ("behavioral runtime", "dialogue intent", "source integrity", "topic-mismatch")):
            return self._report(norm,folded,'legacy_behavioral_runtime_dialogue_update_reference',['jawna prośba o historyczny zakres behavioral runtime/dialogue/source integrity; aktywny runtime ma użyć legacy_diagnostic_only albo aktualnego system_update'],0.79,speech_act=speech.speech_act,question_object='legacy_system_update')
        if has_update and has_system:
            if 'lista' in folded or 'manifest' in folded:
                return self._report(norm,folded,'system_update_manifest_request',['jawne polecenie manifestu/listy aktualizacji'],0.88,update=True,diag=has_diag,speech_act=speech.speech_act,question_object='system')
            return self._report(norm,folded,'system_update_execution_request',['jawne polecenie aktualizacji systemu Jaźni'],0.90,update=True,diag=has_diag,speech_act=speech.speech_act,question_object='system')
        if has_diag and any(x in folded for x in ('sprawdz gdzie', 'sprawdź gdzie', 'jak to zmienic', 'jak to zmienić', 'prawidlowe dzialanie', 'prawidłowe działanie')):
            return self._report(norm,folded,'runtime_behavior_diagnostic_request',['kontekstowe polecenie sprawdzenia miejsca i sposobu naprawy działania'],0.86,diag=True,speech_act=speech.speech_act,question_object='runtime')
        if has_audit and has_system:
            return self._report(norm,folded,'memory_audit_request',['prośba o audyt rozmów/pamięci/systemu'],0.84,['system_diagnostic_question'],diag=True,speech_act=speech.speech_act,question_object='runtime')
        if has_diag and has_system:
            ev = ['pytanie diagnostyczne o system, nie sama korekta']
            if "stale-route" in folded or "starego kontekstu" in folded or "stary kontekst" in folded:
                ev.append("jawny problem stale-route / starego kontekstu w odpowiedzi runtime")
            return self._report(norm,folded,'system_diagnostic_question',ev,0.90 if len(ev)>1 else 0.88,diag=True,speech_act=speech.speech_act,question_object='runtime')
        if ("nlp" in folded or "polish_nlp" in folded) and any(x in folded for x in ("zbyt ogoln", "ogolnym tropem", "stale-route", "stara trasa", "regresj", "fallback")) and ("14.6.1" in folded or "14.6.2" in folded or "co trzeba" in folded or "co teraz" in folded):
            return self._report(norm,folded,'current_hotfix_for_stale_nlp_route',['pytanie o bieżący hotfix/regresję NLP, nie historyczna trasa aktualizacji'],0.86,speech_act=speech.speech_act,question_object='runtime_hotfix')
        if any(x in folded for x in ("wspominasz", "wspomnij", "wspomn", "pamietasz", "pamiętasz")) and speech.speech_act == "question":
            return self._report(norm,folded,'memory_experience_question',['pytanie doświadczeniowe o pamięć/wspomnienie'],0.82,speech_act=speech.speech_act,question_object='memory_experience')
        if any(x in folded for x in ("zlecen", "drzwi", "sztuk drzwi", "jade na kolejne")) and not has_system and not has_update:
            return self._report(norm,folded,'ordinary_workday_report',['bieżąca zwykła wypowiedź o pracy/zleceniu użytkownika'],0.80,speech_act=speech.speech_act,question_object='workday')
        if has_past_year and not has_system and not has_update:
            return self._report(norm,folded,'substantive_question_about_last_year',['pytanie o zeszły/miniony rok; powitanie nie może maskować treści'],0.83,speech_act=speech.speech_act,question_object='past_year_reflection')
        if has_self_preference and not has_system and not has_update:
            return self._report(norm,folded,'self_preference_question',['pytanie o własną ochotę/impuls operacyjny Łatki, nie prośba o losowy stary kontekst pamięci'],0.87,speech_act=speech.speech_act,question_object='self_preference')
        if has_self_plan and not has_system and not has_update:
            return self._report(norm,folded,'self_plan_question',['pytanie o własne plany/zakres działań Łatki bez przenoszenia starego kontekstu użytkownika'],0.84,speech_act=speech.speech_act,question_object='self_plan')
        if has_auto:
            return self._report(norm,folded,'automotive_warning_light_question',['pytanie praktyczne motoryzacyjne'],0.83,speech_act=speech.speech_act,question_object='automotive')
        if has_practical:
            return self._report(norm,folded,'practical_repair_advice',['pytanie praktyczne/naprawcze'],0.82,speech_act=speech.speech_act,question_object='practical')
        if ell.resolved_intent_hint and not (has_system and (has_diag or has_update)):
            evidence.extend(ell.resolution_basis)
            return self._report(norm,folded,ell.resolved_intent_hint,evidence,0.86,src=ell.resolved_intent_hint=='runtime_source_question',speech_act=speech.speech_act,question_object=qobj.object_type)
        if has_state:
            intent='reciprocal_self_state_question' if any(x in folded for x in ('a ty','a tobie','a jak tobie','a jak ci','a ci','a u ciebie','a jak u ciebie','u ciebie')) else 'self_state_question'
            return self._report(norm,folded,intent,['pytanie o stan/samopoczucie Łatki'],0.82,speech_act=speech.speech_act,question_object='self_state')
        if has_diag:
            return self._report(norm,folded,'negative_feedback_without_update_request',['sygnał korekty/diagnozy bez mocnego kontekstu systemowego'],0.68,speech_act=speech.speech_act,question_object=qobj.object_type)
        greeting_prefix = folded.startswith(('witaj ', 'czesc ', 'cześć ', 'hej ', 'hejka ', 'dzien dobry', 'dzień dobry', 'dobry wieczor', 'dobry wieczór'))
        if speech.speech_act == 'statement' and 0 < len(folded.split()) <= 5 and not (has_system or has_update or greeting_prefix):
            return self._report(norm,folded,'short_free_dialogue',['krótka zwykła wypowiedź wymaga naturalnej odpowiedzi, nie generycznego fallbacku i nie losowej pamięci'],0.66,speech_act=speech.speech_act,question_object='short_free_dialogue')
        return self._report(norm,folded,'ordinary_conversation',['brak mocnej intencji specjalistycznej'],0.52,speech_act=speech.speech_act,question_object=qobj.object_type)
