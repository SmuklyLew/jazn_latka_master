from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
import hashlib

from latka_jazn.memory.memory_tier_store import MemoryTierStore, WorkingMemoryBudget
from latka_jazn.memory.memory_tiers import (
    MemoryKind,
    MemoryTier,
    MemoryTruthStatus,
    ShortTermMemoryPolicy,
    SourceEvidence,
    WorkingMemoryRecord,
    deterministic_memory_id,
)
from latka_jazn.memory.runtime_persistence import (
    RuntimeMemoryCandidate,
    RuntimePersistedRecord,
    RuntimePersistenceResult,
)
from latka_jazn.memory.turn_memory_transaction import TurnMemoryTransaction
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("runtime_memory_v151")


class CandidateClassifier(Protocol):
    def build_candidate_from_runtime_turn(self, **kwargs) -> RuntimeMemoryCandidate: ...
    def should_persist(self, candidate: RuntimeMemoryCandidate) -> tuple[bool, str]: ...
    def candidate_fingerprint(self, candidate: RuntimeMemoryCandidate) -> str: ...


@dataclass(slots=True, frozen=True)
class RuntimeMemoryWriteContext:
    session_id: str = "runtime-session"
    turn_id: str | None = None
    actor: str = "user"
    active_goal: str | None = None
    domain: str = "unclassified"
    mode: str = "runtime_turn"
    timestamp_status: str = "runtime_recorded"

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id is required")
        if not self.actor.strip():
            raise ValueError("actor is required")


class RuntimeMemoryV151Coordinator:
    """Compatibility coordinator that replaces legacy multi-layer fan-out.

    The existing classifier may still determine whether a turn is important, but
    persistence is limited to one L1 record and one optional L2 candidate in one
    SQLite transaction. L3 is impossible through this API.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        classifier: CandidateClassifier,
        working_budget: WorkingMemoryBudget | None = None,
        short_term_policy: ShortTermMemoryPolicy | None = None,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.classifier = classifier
        self.working_budget = working_budget or WorkingMemoryBudget()
        self.short_term_policy = short_term_policy or ShortTermMemoryPolicy()

    def build_candidate_from_runtime_turn(self, **kwargs) -> RuntimeMemoryCandidate:
        return self.classifier.build_candidate_from_runtime_turn(**kwargs)

    def should_persist(self, candidate: RuntimeMemoryCandidate) -> tuple[bool, str]:
        return self.classifier.should_persist(candidate)

    def candidate_fingerprint(self, candidate: RuntimeMemoryCandidate) -> str:
        return self.classifier.candidate_fingerprint(candidate)

    def persist_candidate(
        self,
        candidate: RuntimeMemoryCandidate,
        *,
        force: bool = False,
        context: RuntimeMemoryWriteContext | None = None,
    ) -> RuntimePersistenceResult:
        accepted, reason = self.should_persist(candidate)
        fingerprint = self.candidate_fingerprint(candidate)
        if not force and not accepted:
            return RuntimePersistenceResult(False, fingerprint, candidate.kind, reason, [])

        write_context = context or RuntimeMemoryWriteContext()
        now = datetime.now(timezone.utc)
        evidence = self._evidence(candidate, fingerprint=fingerprint, context=write_context)
        truth_status = self._truth_status(candidate.grounding)
        memory_kind = self._memory_kind(candidate)
        tags = tuple(sorted(set((candidate.memory_tags or []) + [
            "runtime_v151", candidate.kind, "pending_review",
        ])))

        working_id = deterministic_memory_id(
            tier=MemoryTier.WORKING,
            kind=MemoryKind.CONVERSATION_CONTEXT,
            content=candidate.content,
            domain=write_context.domain,
            mode=write_context.mode,
            evidence=(evidence,),
        )
        working = WorkingMemoryRecord(
            memory_id=working_id,
            tier=MemoryTier.WORKING,
            kind=MemoryKind.CONVERSATION_CONTEXT,
            content=candidate.content,
            content_sha256=hashlib.sha256(candidate.content.encode("utf-8")).hexdigest(),
            domain=write_context.domain,
            mode=write_context.mode,
            truth_status=truth_status,
            confidence=max(0.0, min(1.0, float(candidate.confidence))),
            importance=max(0.0, min(1.0, float(candidate.importance))),
            created_at_utc=now,
            updated_at_utc=now,
            evidence=(evidence,),
            tags=tags,
            session_id=write_context.session_id,
            turn_id=write_context.turn_id,
            active_goal=write_context.active_goal,
        )
        short_term = self.short_term_policy.create(
            kind=memory_kind,
            content=candidate.content,
            domain=write_context.domain,
            mode=write_context.mode,
            truth_status=truth_status,
            confidence=max(0.0, min(1.0, float(candidate.confidence))),
            importance=max(0.0, min(1.0, float(candidate.importance))),
            evidence=(evidence,),
            created_at_utc=now,
            tags=tags,
        )

        with MemoryTierStore(self.database_path) as store:
            transaction = TurnMemoryTransaction(store, working_budget=self.working_budget)
            transaction.stage_record(working)
            transaction.stage_record(short_term)
            transaction.stage_outbox(
                event_type="memory.runtime_turn_staged",
                aggregate_id=short_term.memory_id,
                payload={
                    "working_memory_id": working.memory_id,
                    "short_term_memory_id": short_term.memory_id,
                    "candidate_fingerprint": fingerprint,
                    "candidate_kind": candidate.kind,
                    "session_id": write_context.session_id,
                    "turn_id": write_context.turn_id,
                    "automatic_l3": False,
                },
                idempotency_key=f"runtime-turn:{fingerprint}:{write_context.session_id}:{write_context.turn_id or '-'}",
            )
            result = transaction.commit()

        records = [
            RuntimePersistedRecord(
                layer="working",
                path=str(self.database_path),
                record_id=working.memory_id,
                fingerprint=fingerprint,
                appended=True,
                reason="transaction_committed",
            ),
            RuntimePersistedRecord(
                layer="short_term",
                path=str(self.database_path),
                record_id=short_term.memory_id,
                fingerprint=fingerprint,
                appended=True,
                reason="pending_review",
            ),
            RuntimePersistedRecord(
                layer="outbox",
                path=str(self.database_path),
                record_id=None,
                fingerprint=fingerprint,
                appended=bool(result.outbox_written),
                reason="idempotent_event_staged",
            ),
        ]
        return RuntimePersistenceResult(True, fingerprint, candidate.kind, reason, records)

    @staticmethod
    def _evidence(
        candidate: RuntimeMemoryCandidate,
        *,
        fingerprint: str,
        context: RuntimeMemoryWriteContext,
    ) -> SourceEvidence:
        excerpt = candidate.raw_excerpt or candidate.content
        return SourceEvidence(
            source_type="runtime_turn",
            source_id=context.turn_id or fingerprint,
            conversation_id=context.session_id,
            node_ids=(context.turn_id,) if context.turn_id else (),
            exact_excerpt_sha256=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            timestamp_status=context.timestamp_status,
            metadata={
                "actor": context.actor,
                "candidate_source": candidate.source,
                "candidate_grounding": candidate.grounding,
                "candidate_title": candidate.title,
                "candidate_fingerprint": fingerprint,
                "schema_version": SCHEMA_VERSION,
            },
        )

    @staticmethod
    def _memory_kind(candidate: RuntimeMemoryCandidate) -> MemoryKind:
        if candidate.procedural_trigger and candidate.procedural_action and candidate.procedural_reason:
            return MemoryKind.PROCEDURAL
        if candidate.kind in {"reguła_proceduralna", "procedural"}:
            return MemoryKind.PROCEDURAL
        if candidate.kind in {"ustalenie", "semantic", "fakt"}:
            return MemoryKind.SEMANTIC
        return MemoryKind.EPISODIC

    @staticmethod
    def _truth_status(grounding: str) -> MemoryTruthStatus:
        value = str(grounding or "").strip().lower()
        if value in {"verified", "user_confirmed"}:
            return MemoryTruthStatus.USER_CONFIRMED
        if value in {"recognized", "recovered", "source_recorded"}:
            return MemoryTruthStatus.SOURCE_RECORDED
        if value in {"symbolic", "book_scene"}:
            return MemoryTruthStatus.SYMBOLIC
        return MemoryTruthStatus.INFERRED
