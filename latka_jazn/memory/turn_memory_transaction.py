from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from latka_jazn.memory.memory_promotion import PromotionDecision, PromotionRequest
from latka_jazn.memory.memory_tier_store import MemoryTierStore, WorkingMemoryBudget
from latka_jazn.memory.memory_tiers import LongTermMemoryRecord, MemoryRecord, ShortTermMemoryRecord, WorkingMemoryRecord
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("turn_memory_transaction")


@dataclass(slots=True)
class _PromotionStage:
    source: ShortTermMemoryRecord
    request: PromotionRequest
    decision: PromotionDecision
    long_term: LongTermMemoryRecord | None


@dataclass(slots=True)
class _OutboxStage:
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    idempotency_key: str


@dataclass(slots=True)
class TurnMemoryCommitResult:
    committed: bool
    records_written: int
    evidence_written: int
    promotions_written: int
    outbox_written: int
    working_records_evicted: int
    schema_version: str = SCHEMA_VERSION


@dataclass(slots=True)
class TurnMemoryTransaction:
    store: MemoryTierStore
    working_budget: WorkingMemoryBudget = field(default_factory=WorkingMemoryBudget)
    _records: list[MemoryRecord] = field(default_factory=list, init=False)
    _promotions: list[_PromotionStage] = field(default_factory=list, init=False)
    _outbox: list[_OutboxStage] = field(default_factory=list, init=False)
    _before_commit_hooks: list[Callable[[], None]] = field(default_factory=list, init=False)
    _closed: bool = field(default=False, init=False)

    def stage_record(self, record: MemoryRecord) -> None:
        self._ensure_open()
        self._records.append(record)

    def stage_promotion(
        self,
        source: ShortTermMemoryRecord,
        request: PromotionRequest,
        decision: PromotionDecision,
        long_term: LongTermMemoryRecord | None = None,
    ) -> None:
        self._ensure_open()
        self._promotions.append(_PromotionStage(source, request, decision, long_term))

    def stage_outbox(
        self,
        *,
        event_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        self._ensure_open()
        self._outbox.append(_OutboxStage(event_type, aggregate_id, payload, idempotency_key))

    def stage_before_commit_hook(self, hook: Callable[[], None]) -> None:
        """Testing/validation hook executed inside the transaction before commit."""
        self._ensure_open()
        self._before_commit_hooks.append(hook)

    def commit(self) -> TurnMemoryCommitResult:
        self._ensure_open()
        totals = {"records": 0, "evidence": 0, "promotions": 0, "outbox": 0, "evicted": 0}
        try:
            with self.store.transaction():
                working_sessions: set[str] = set()
                for record in self._records:
                    summary = self.store.write_record(record)
                    totals["records"] += summary.records_written
                    totals["evidence"] += summary.evidence_written
                    if isinstance(record, WorkingMemoryRecord):
                        working_sessions.add(record.session_id)
                for stage in self._promotions:
                    summary = self.store.write_promotion(
                        stage.source, stage.request, stage.decision, stage.long_term
                    )
                    totals["records"] += summary.records_written
                    totals["promotions"] += summary.promotions_written
                    totals["outbox"] += summary.outbox_written
                for event in self._outbox:
                    self.store.write_outbox(
                        event_type=event.event_type,
                        aggregate_id=event.aggregate_id,
                        payload=event.payload,
                        idempotency_key=event.idempotency_key,
                    )
                    totals["outbox"] += 1
                for session_id in sorted(working_sessions):
                    totals["evicted"] += self.store.enforce_working_budget(
                        session_id, self.working_budget
                    )
                for hook in self._before_commit_hooks:
                    hook()
        finally:
            self._closed = True
        return TurnMemoryCommitResult(
            committed=True,
            records_written=totals["records"],
            evidence_written=totals["evidence"],
            promotions_written=totals["promotions"],
            outbox_written=totals["outbox"],
            working_records_evicted=totals["evicted"],
        )

    def discard(self) -> None:
        self._ensure_open()
        self._records.clear()
        self._promotions.clear()
        self._outbox.clear()
        self._before_commit_hooks.clear()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("turn memory transaction is already closed")
