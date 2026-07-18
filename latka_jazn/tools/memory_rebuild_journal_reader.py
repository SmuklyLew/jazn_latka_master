from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import re
import unicodedata

from latka_jazn.tools.chat_export_reader import sha256_file
from latka_jazn.tools.memory_rebuild_common import (
    CONTENT_FIELDS, FANOUT_FIELDS, bounded, canonical_json, norm, schema_version, sha_text,
)

CLASSIFICATION_SCHEMA_VERSION = schema_version("journal_classification")

_LABEL_FIELDS = ("type", "entry_type", "kind", "category", "mode", "tags")

_BOOK_LABEL_RE = re.compile(
    r"(?:^|\s)(?:fabula|fabuly|fragment fabuly|fragment ksiazki|scena|roleplay|manuskrypt|"
    r"rozdzial|analiza fabuly|historia wyobrazona|scena ksiazkowa)(?:$|\s)",
    re.IGNORECASE,
)
_SYMBOLIC_LABEL_RE = re.compile(
    r"(?:^|\s)(?:sen|sny|prompt|marzenie|wizja|wyobraznia|wyobrazenie|"
    r"wizualizacja|grafika|ilustracja)(?:$|\s)",
    re.IGNORECASE,
)
_SOURCE_LABEL_RE = re.compile(
    r"(?:^|\s)(?:system|meta|regula|polecenie|procedura|synchronizacja|instrukcja|"
    r"konfiguracja|notatka systemowa|log systemowy|telemetria)(?:$|\s)",
    re.IGNORECASE,
)
_MEDIA_REACTION_LABEL_RE = re.compile(
    r"(?:^|\s)(?:przezycie filmowe|reakcja|reakcja na|wrazenia z filmu|"
    r"wrazenia muzyczne|odbior filmu|odbior muzyki)(?:$|\s)",
    re.IGNORECASE,
)
_MEDIA_ANALYSIS_LABEL_RE = re.compile(
    r"(?:^|\s)(?:analiza|analiza utworu|refleksja filmowa|"
    r"film|muzyka|utwor|obraz|wideo|video)(?:$|\s)",
    re.IGNORECASE,
)
_EXPERIENTIAL_LABEL_RE = re.compile(
    r"(?:^|\s)(?:wspomnienie|mikrowspomnienie|emocje|refleksja|mikrorefleksja|"
    r"autorefleksja|introspekcja|pragnienie|przezycie|fragment przezycia|doznanie|"
    r"wyznanie|wdziecznosc|relacja|mikroprzelom|pytanie z ciszy)(?:$|\s)",
    re.IGNORECASE,
)
_KNOWLEDGE_LABEL_RE = re.compile(
    r"(?:^|\s)(?:wiedza|badania|notatka naukowa|cytat naukowy|slownik|"
    r"refleksja naukowa|filozofia|analiza semantyczna)(?:$|\s)",
    re.IGNORECASE,
)
_EVENT_LABEL_RE = re.compile(
    r"(?:^|\s)(?:akcja|wydarzenie|decyzja|informacja|potwierdzenie|rozmowa|"
    r"powitanie|plan|plany wakacyjne)(?:$|\s)",
    re.IGNORECASE,
)


def _fold(value: Any) -> str:
    text = norm(value).replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[_+,\-/]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def label_values(raw: dict[str, Any]) -> tuple[str, ...]:
    parts: list[str] = []
    for key in _LABEL_FIELDS:
        value = raw.get(key)
        values = value if isinstance(value, (list, tuple, set)) else (value,)
        for item in values:
            folded = _fold(item)
            if folded:
                parts.append(f"{key}:{folded}")
    return tuple(dict.fromkeys(parts))


def _labels(raw: dict[str, Any]) -> str:
    return " ".join(item.split(":", 1)[1] for item in label_values(raw))


@dataclass(slots=True, frozen=True)
class JournalClassification:
    truth_status: str
    profile: str
    evidence: tuple[str, ...]
    review_reasons: tuple[str, ...]
    source_labels: tuple[str, ...]
    schema_version: str = CLASSIFICATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "truth_status": self.truth_status,
            "profile": self.profile,
            "evidence": list(self.evidence),
            "review_reasons": list(self.review_reasons),
            "source_labels": list(self.source_labels),
            "schema_version": self.schema_version,
        }


def classify_journal_raw(raw: dict[str, Any]) -> JournalClassification:
    source_labels = label_values(raw)
    labels = _labels(raw)
    explicit = " ".join(
        _fold(raw.get(key))
        for key in ("truth_status", "grounding", "granica_prawdy", "source")
        if _fold(raw.get(key))
    )

    explicit_truth: str | None = None
    if any(marker in explicit for marker in ("user confirmed", "user_confirmed", "verified", "potwierdz")):
        explicit_truth = "user_confirmed"
    elif any(marker in explicit for marker in ("source recorded", "source_recorded", "runtime")):
        explicit_truth = "source_recorded"
    elif any(marker in explicit for marker in ("book scene", "book_scene", "scena ksiaz")):
        explicit_truth = "book_scene"
    elif any(marker in explicit for marker in ("symbol", "wyobraz")):
        explicit_truth = "symbolic"
    elif "draft" in explicit or "szkic" in explicit:
        explicit_truth = "draft"

    matches = {
        "book_scene": bool(_BOOK_LABEL_RE.search(labels)),
        "symbolic": bool(_SYMBOLIC_LABEL_RE.search(labels)),
        "source_recorded": bool(_SOURCE_LABEL_RE.search(labels)),
    }
    matched_truths = [key for key, matched in matches.items() if matched]
    inferred_truth = (
        "book_scene" if matches["book_scene"]
        else "symbolic" if matches["symbolic"]
        else "source_recorded" if matches["source_recorded"]
        else "inferred"
    )
    truth = explicit_truth or inferred_truth

    if truth == "book_scene":
        profile = "book_work"
    elif truth == "symbolic":
        profile = "symbolic"
    elif truth == "draft":
        profile = "draft"
    elif matches["source_recorded"]:
        profile = "system_meta"
    elif _MEDIA_REACTION_LABEL_RE.search(labels):
        profile = "media_reaction"
    elif _MEDIA_ANALYSIS_LABEL_RE.search(labels):
        profile = "media_analysis"
    elif _KNOWLEDGE_LABEL_RE.search(labels):
        profile = "knowledge_reference"
    elif _EXPERIENTIAL_LABEL_RE.search(labels):
        profile = "experiential"
    elif _EVENT_LABEL_RE.search(labels):
        profile = "event_record"
    else:
        profile = "unclassified"

    evidence: list[str] = []
    if explicit_truth:
        evidence.append(f"explicit_truth:{explicit_truth}")
    evidence.extend(f"label_truth:{item}" for item in matched_truths)
    evidence.append(f"profile:{profile}")

    review: list[str] = []
    if len(matched_truths) > 1:
        review.append("ambiguous_truth_labels")
    if explicit_truth and inferred_truth != "inferred" and explicit_truth != inferred_truth:
        review.append("explicit_truth_conflict")
    if profile == "unclassified" and source_labels:
        review.append("unclassified_structured_labels")
    if not source_labels:
        review.append("missing_structured_labels")

    return JournalClassification(
        truth_status=truth,
        profile=profile,
        evidence=tuple(sorted(set(evidence))),
        review_reasons=tuple(sorted(set(review))),
        source_labels=source_labels,
    )


@dataclass(slots=True, frozen=True)
class JournalItem:
    record_id: str
    identity: str
    title: str
    summary: str
    content: str
    content_hash: str
    raw: dict[str, Any]
    truth: str
    importance: float
    start: str | None
    end: str | None
    timestamp_status: str
    fanout: bool
    profile: str = "unclassified"
    classification_evidence: tuple[str, ...] = ()
    classification_review: tuple[str, ...] = ()


class JournalReader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.sha256 = sha256_file(self.path)
        self.format = self.path.suffix.lower().lstrip(".") or "json"
        self.meta: dict[str, Any] = {}
        self.invalid = 0
        self.rows = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if self.path.suffix.lower() in {".jsonl", ".ndjson"}:
            result = []
            for line in self.path.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    self.invalid += 1
                    continue
                if isinstance(value, dict):
                    result.append(value)
                else:
                    self.invalid += 1
            return result
        value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if isinstance(value, dict) and isinstance(value.get("entries"), list):
            self.meta = dict(value.get("meta") or {}) if isinstance(value.get("meta"), dict) else {}
            source = value["entries"]
        elif isinstance(value, list):
            source = value
        else:
            raise ValueError("journal must be {meta,entries}, a JSON list, or JSONL")
        result = []
        for item in source:
            if isinstance(item, dict):
                result.append(item)
            else:
                self.invalid += 1
        return result

    def items(self) -> list[JournalItem]:
        result = []
        for raw in self.rows:
            lines = [f"{key}: {norm(raw.get(key))}" for key in CONTENT_FIELDS if norm(raw.get(key))]
            content = "\n".join(lines) or canonical_json(raw)
            content_hash = sha_text(content)
            explicit = norm(raw.get("id") or raw.get("entry_id") or raw.get("uuid"))
            record_id = explicit or sha_text(canonical_json(raw))
            identity = f"id:{explicit}" if explicit else f"content:{content_hash}"
            summary = norm(
                raw.get("wpis") or raw.get("treść") or raw.get("tresc")
                or raw.get("content") or raw.get("opis")
            ) or norm(content)[:2000]
            title = norm(raw.get("tytuł") or raw.get("tytul") or raw.get("title")) or summary[:120]
            start = norm(
                raw.get("event_time_start") or raw.get("timestamp")
                or raw.get("datetime") or raw.get("data")
            ) or None
            classification = classify_journal_raw(raw)
            result.append(JournalItem(
                record_id, identity, title, summary, content, content_hash, dict(raw),
                classification.truth_status,
                bounded(raw.get("importance", raw.get("ważność", raw.get("waznosc"))), 0.6),
                start, norm(raw.get("event_time_end")) or None,
                "source_recorded" if start else "missing",
                sum(1 for key in FANOUT_FIELDS if norm(raw.get(key))) >= 2,
                classification.profile,
                classification.evidence,
                classification.review_reasons,
            ))
        return result

    def inspect(self) -> dict[str, Any]:
        items = self.items()
        truth_counts = Counter(item.truth for item in items)
        profile_counts = Counter(item.profile for item in items)
        timestamp_counts = Counter(item.timestamp_status for item in items)
        review_items = [item for item in items if item.classification_review]
        label_counts: Counter[str] = Counter()
        for item in items:
            label_counts.update(label_values(item.raw))
        return {
            "ok": True, "path": str(self.path), "sha256": self.sha256, "format": self.format,
            "valid_entries": len(items), "invalid_entries": self.invalid,
            "suspected_fanout": sum(1 for item in items if item.fanout),
            "truth_status_counts": dict(sorted(truth_counts.items())),
            "profile_counts": dict(sorted(profile_counts.items())),
            "timestamp_status_counts": dict(sorted(timestamp_counts.items())),
            "classification_schema_version": CLASSIFICATION_SCHEMA_VERSION,
            "classification_review_count": len(review_items),
            "classification_review_samples": [
                {
                    "source_record_id": item.record_id,
                    "title": item.title,
                    "truth_status": item.truth,
                    "profile": item.profile,
                    "review_reasons": list(item.classification_review),
                    "evidence": list(item.classification_evidence),
                }
                for item in review_items[:25]
            ],
            "source_label_counts": dict(label_counts.most_common(100)),
            "automatic_l2": False, "automatic_l3": False,
        }
