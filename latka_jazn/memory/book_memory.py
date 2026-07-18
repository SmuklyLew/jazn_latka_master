from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable
import hashlib
import json

from latka_jazn.memory.memory_tiers import SourceEvidence, ensure_utc, utc_now
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("book_memory")


class BookArtifactStatus(StrEnum):
    ROLEPLAY = "roleplay"
    WORKING_MATERIAL = "working_material"
    DRAFT = "draft"
    EDITED = "edited"
    CANONICAL = "canonical"
    REJECTED = "rejected"


class CanonOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(slots=True, frozen=True)
class RoleplaySession:
    roleplay_id: str
    project_id: str
    title: str
    participants: tuple[str, ...]
    started_at_utc: datetime
    source_evidence: tuple[SourceEvidence, ...]
    purpose: str
    symbolic: bool = True
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = "Roleplay jest symboliczną sesją roboczą, nie zdarzeniem fizycznym ani kanonem."

    def __post_init__(self) -> None:
        ensure_utc(self.started_at_utc)
        if not self.roleplay_id or not self.project_id or not self.title.strip():
            raise ValueError("roleplay id, project id and title are required")
        if not self.participants or not self.source_evidence:
            raise ValueError("roleplay requires participants and source evidence")
        if not self.symbolic:
            raise ValueError("book roleplay must retain symbolic truth boundary")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at_utc"] = self.started_at_utc.isoformat()
        return data


@dataclass(slots=True, frozen=True)
class BookSceneVersion:
    scene_version_id: str
    project_id: str
    chapter_id: str | None
    scene_id: str
    version: int
    title: str
    content: str
    content_sha256: str
    status: BookArtifactStatus
    created_at_utc: datetime
    created_by: str
    source_evidence: tuple[SourceEvidence, ...]
    based_on_roleplay_id: str | None = None
    previous_version_id: str | None = None
    canon_decision_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        ensure_utc(self.created_at_utc)
        if not self.scene_version_id or not self.project_id or not self.scene_id:
            raise ValueError("scene version, project and scene ids are required")
        if self.version < 1 or not self.content.strip() or not self.created_by.strip():
            raise ValueError("valid version, content and creator are required")
        if self.content_sha256 != hashlib.sha256(self.content.encode()).hexdigest():
            raise ValueError("scene content hash mismatch")
        if not self.source_evidence:
            raise ValueError("scene version requires source evidence")
        if self.status is BookArtifactStatus.ROLEPLAY:
            raise ValueError("roleplay must be stored as RoleplaySession, not scene version")
        if self.status is BookArtifactStatus.CANONICAL and not self.canon_decision_id:
            raise ValueError("canonical scene requires canon decision")

    @property
    def is_canonical(self) -> bool:
        return self.status is BookArtifactStatus.CANONICAL

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["created_at_utc"] = self.created_at_utc.isoformat()
        return data


@dataclass(slots=True, frozen=True)
class CanonDecision:
    decision_id: str
    scene_version_id: str
    outcome: CanonOutcome
    decided_at_utc: datetime
    decided_by: str
    explicit_user_approval: bool
    reason: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        ensure_utc(self.decided_at_utc)
        if not self.decision_id or not self.scene_version_id or not self.decided_by.strip() or not self.reason.strip():
            raise ValueError("complete canon decision metadata is required")
        if self.outcome is CanonOutcome.APPROVED and not self.explicit_user_approval:
            raise ValueError("canonical approval requires explicit user approval")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["outcome"] = self.outcome.value
        data["decided_at_utc"] = self.decided_at_utc.isoformat()
        return data


def new_roleplay_session(
    *, project_id: str, title: str, participants: Iterable[str], evidence: Iterable[SourceEvidence],
    purpose: str, started_at_utc: datetime | None = None,
) -> RoleplaySession:
    started = ensure_utc(started_at_utc or utc_now())
    participants_tuple = tuple(dict.fromkeys(str(item).strip() for item in participants if str(item).strip()))
    evidence_tuple = tuple(dict((item.evidence_key, item) for item in evidence).values())
    roleplay_id = _hash({"project_id": project_id, "title": title, "started": started.isoformat(), "evidence": [e.evidence_key for e in evidence_tuple]})
    return RoleplaySession(roleplay_id, project_id, title, participants_tuple, started, evidence_tuple, purpose)


def new_scene_version(
    *, project_id: str, scene_id: str, title: str, content: str, created_by: str,
    evidence: Iterable[SourceEvidence], version: int = 1, chapter_id: str | None = None,
    status: BookArtifactStatus = BookArtifactStatus.DRAFT, based_on_roleplay_id: str | None = None,
    previous_version_id: str | None = None, created_at_utc: datetime | None = None,
) -> BookSceneVersion:
    created = ensure_utc(created_at_utc or utc_now())
    evidence_tuple = tuple(dict((item.evidence_key, item) for item in evidence).values())
    content_sha = hashlib.sha256(content.encode()).hexdigest()
    scene_version_id = _hash({"project": project_id, "scene": scene_id, "version": version, "content": content_sha})
    return BookSceneVersion(
        scene_version_id=scene_version_id, project_id=project_id, chapter_id=chapter_id,
        scene_id=scene_id, version=version, title=title, content=content, content_sha256=content_sha,
        status=status, created_at_utc=created, created_by=created_by, source_evidence=evidence_tuple,
        based_on_roleplay_id=based_on_roleplay_id, previous_version_id=previous_version_id,
    )


def decide_canon(
    scene: BookSceneVersion, *, approved: bool, decided_by: str, reason: str,
    explicit_user_approval: bool, decided_at_utc: datetime | None = None,
) -> tuple[CanonDecision, BookSceneVersion]:
    decided = ensure_utc(decided_at_utc or utc_now())
    outcome = CanonOutcome.APPROVED if approved else CanonOutcome.REJECTED
    decision_id = _hash({"scene": scene.scene_version_id, "outcome": outcome.value, "decided_by": decided_by, "at": decided.isoformat()})
    decision = CanonDecision(decision_id, scene.scene_version_id, outcome, decided, decided_by, explicit_user_approval, reason)
    updated = replace(
        scene,
        status=BookArtifactStatus.CANONICAL if approved else BookArtifactStatus.REJECTED,
        canon_decision_id=decision_id,
    )
    return decision, updated
