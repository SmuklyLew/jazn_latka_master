from __future__ import annotations

from typing import Iterable

_POS_LABELS = {
    "subst": "noun",
    "depr": "depreciative_noun",
    "ger": "gerund",
    "adj": "adjective",
    "adja": "adjectival_adjective",
    "adjp": "postprepositional_adjective",
    "adv": "adverb",
    "fin": "finite_verb",
    "bedzie": "future_auxiliary",
    "aglt": "agglutinate",
    "praet": "past_verb",
    "impt": "imperative_verb",
    "imps": "impersonal_verb",
    "inf": "infinitive",
    "pcon": "contemporary_adverbial_participle",
    "pant": "anterior_adverbial_participle",
    "pact": "active_participle",
    "ppas": "passive_participle",
    "num": "numeral",
    "ppron12": "personal_pronoun_1_2",
    "ppron3": "personal_pronoun_3",
    "siebie": "reflexive_pronoun",
    "prep": "preposition",
    "conj": "conjunction",
    "comp": "complementizer",
    "qub": "particle",
    "brev": "abbreviation",
    "interp": "punctuation",
}

_VALUE_LABELS = {
    "sg": "singular",
    "pl": "plural",
    "nom": "nominative",
    "gen": "genitive",
    "dat": "dative",
    "acc": "accusative",
    "inst": "instrumental",
    "loc": "locative",
    "voc": "vocative",
    "m1": "masculine_personal",
    "m2": "masculine_animate",
    "m3": "masculine_inanimate",
    "f": "feminine",
    "n": "neuter",
    "pri": "first_person",
    "sec": "second_person",
    "ter": "third_person",
    "imperf": "imperfective",
    "perf": "perfective",
    "pos": "positive_degree",
    "comp": "comparative_degree",
    "sup": "superlative_degree",
}

_POSITIONAL_FIELDS = {
    "subst": ["number", "case", "gender"],
    "depr": ["number", "case", "gender"],
    "ger": ["number", "case", "gender", "aspect", "negation"],
    "adj": ["number", "case", "gender", "degree"],
    "num": ["number", "case", "gender", "accommodability"],
    "fin": ["number", "person", "aspect"],
    "bedzie": ["number", "person", "aspect"],
    "praet": ["number", "gender", "aspect"],
    "impt": ["number", "person", "aspect"],
    "inf": ["aspect"],
    "pcon": ["aspect"],
    "pant": ["aspect"],
    "pact": ["number", "case", "gender", "aspect", "negation"],
    "ppas": ["number", "case", "gender", "aspect", "negation"],
    "adv": ["degree"],
    "ppron12": ["number", "case", "gender", "person"],
    "ppron3": ["number", "case", "gender", "person", "accentability", "postprepositionality"],
    "prep": ["case"],
}


def parse_morfeusz_tag(tag: str) -> dict[str, str]:
    raw = tag or ""
    parts = raw.split(":") if raw else []
    if not parts:
        return {"raw_tag": raw}
    pos = parts[0]
    features: dict[str, str] = {"raw_tag": raw, "pos": pos, "pos_label": _POS_LABELS.get(pos, pos)}
    fields = _POSITIONAL_FIELDS.get(pos, [])
    for name, value in zip(fields, parts[1:]):
        features[name] = value
        label = _VALUE_LABELS.get(value)
        if label:
            features[f"{name}_label"] = label
    if len(parts) > len(fields) + 1:
        features["extra"] = ":".join(parts[len(fields) + 1 :])
    return features


def candidate_pos(tag: str) -> str:
    return (tag or "").split(":", 1)[0]


def is_punctuation_tag(tag: str) -> bool:
    return candidate_pos(tag) == "interp"


def tag_summary(tags: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for tag in tags:
        pos = candidate_pos(tag)
        label = _POS_LABELS.get(pos, pos or "unknown")
        if label not in seen:
            seen.append(label)
    return seen
