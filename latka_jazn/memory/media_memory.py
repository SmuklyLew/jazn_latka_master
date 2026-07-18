from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib

from latka_jazn.memory.memory_tiers import SourceEvidence, ensure_utc, utc_now
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("media_memory")


class MediaModality(StrEnum):
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    TEXT = "text"


class ObservationBasis(StrEnum):
    ACTUAL_AUDIO = "actual_audio"
    ACTUAL_IMAGE = "actual_image"
    ACTUAL_VIDEO = "actual_video"
    TRANSCRIPT = "transcript"
    OCR_TEXT = "ocr_text"
    USER_DESCRIPTION = "user_description"
    SOURCE_TEXT = "source_text"


@dataclass(slots=True, frozen=True)
class ExperienceSource:
    source_id: str
    modality: MediaModality
    locator: str
    content_sha256: str | None
    mime_type: str | None
    available: bool
    source_evidence: tuple[SourceEvidence, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.source_id or not self.locator.strip() or not self.source_evidence:
            raise ValueError("media source requires id, locator and evidence")


@dataclass(slots=True, frozen=True)
class AdapterObservation:
    observation_id: str
    source_id: str
    basis: ObservationBasis
    content: str
    confidence: float
    observed_at_utc: datetime
    adapter_id: str | None
    adapter_version: str | None
    adapter_configured: bool
    segment_locator: str | None = None
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Obserwacja jest wynikiem adaptera lub źródła, nie nieomylną percepcją ani osobistym wspomnieniem."

    def __post_init__(self) -> None:
        ensure_utc(self.observed_at_utc)
        if not self.observation_id or not self.source_id or not self.content.strip():
            raise ValueError("observation id, source and content are required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("observation confidence must be between 0 and 1")
        actual = {ObservationBasis.ACTUAL_AUDIO, ObservationBasis.ACTUAL_IMAGE, ObservationBasis.ACTUAL_VIDEO}
        if self.basis in actual and not self.adapter_configured:
            raise ValueError("actual media observation requires a configured adapter")
        if self.adapter_configured and (not self.adapter_id or not self.adapter_version):
            raise ValueError("configured adapter requires id and version")


@dataclass(slots=True, frozen=True)
class OperationalAppraisal:
    appraisal_id: str
    observation_id: str
    novelty: float
    goal_relevance: float
    memory_relevance: float
    valence: float
    arousal: float
    uncertainty: float
    explanation: str
    created_at_utc: datetime
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Appraisal opisuje modelowany rezonans regulacyjny, nie biologiczne odczucie."

    def __post_init__(self) -> None:
        ensure_utc(self.created_at_utc)
        for name in ("novelty", "goal_relevance", "memory_relevance", "arousal", "uncertainty"):
            if not 0.0 <= float(getattr(self, name)) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if not -1.0 <= self.valence <= 1.0 or not self.explanation.strip():
            raise ValueError("valence or explanation is invalid")


@dataclass(slots=True, frozen=True)
class SourceClaim:
    claim_id: str
    document_source_id: str
    section_locator: str
    author_or_source: str
    claim_text: str
    exact_text_sha256: str
    evidence: tuple[SourceEvidence, ...]
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "SourceClaim zapisuje, co twierdzi źródło; nie uznaje tego automatycznie za prawdę ani doświadczenie."

    def __post_init__(self) -> None:
        if not all((self.claim_id, self.document_source_id, self.section_locator, self.author_or_source, self.claim_text.strip())):
            raise ValueError("complete source claim fields are required")
        if self.exact_text_sha256 != hashlib.sha256(self.claim_text.encode()).hexdigest():
            raise ValueError("source claim hash mismatch")
        if not self.evidence:
            raise ValueError("source claim requires evidence")


@dataclass(slots=True, frozen=True)
class Interpretation:
    interpretation_id: str
    source_record_id: str
    content: str
    confidence: float
    created_at_utc: datetime
    created_by: str
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Interpretacja pozostaje oddzielona od obserwacji i twierdzenia źródłowego."

    def __post_init__(self) -> None:
        ensure_utc(self.created_at_utc)
        if not self.interpretation_id or not self.source_record_id or not self.content.strip() or not self.created_by.strip():
            raise ValueError("complete interpretation fields are required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("interpretation confidence must be between 0 and 1")


def new_observation(
    source: ExperienceSource, *, basis: ObservationBasis, content: str, confidence: float,
    adapter_configured: bool, adapter_id: str | None = None, adapter_version: str | None = None,
    segment_locator: str | None = None, observed_at_utc: datetime | None = None,
) -> AdapterObservation:
    observed = ensure_utc(observed_at_utc or utc_now())
    observation_id = hashlib.sha256(
        f"{source.source_id}|{basis.value}|{segment_locator}|{hashlib.sha256(content.encode()).hexdigest()}".encode()
    ).hexdigest()
    return AdapterObservation(
        observation_id, source.source_id, basis, content, confidence, observed,
        adapter_id, adapter_version, adapter_configured, segment_locator,
    )
