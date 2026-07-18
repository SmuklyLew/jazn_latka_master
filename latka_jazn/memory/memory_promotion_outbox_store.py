from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any
import hashlib
import json

from latka_jazn.memory.memory_promotion import PromotionDecision, PromotionOutcome, PromotionRequest
from latka_jazn.memory.memory_tier_support import WriteSummary, iso, json_text
from latka_jazn.memory.memory_tiers import LongTermMemoryRecord, PromotionStatus, ShortTermMemoryRecord, utc_now


class PromotionOutboxStoreMixin:
    con: Any

    def write_promotion(
        self,
        source: ShortTermMemoryRecord,
        request: PromotionRequest,
        decision: PromotionDecision,
        long_term: LongTermMemoryRecord | None = None,
    ) -> WriteSummary:
        self._require_transaction()
        if source.memory_id != request.source_memory_id or source.memory_id != decision.source_memory_id:
            raise ValueError("promotion source, request and decision do not match")
        self.write_record(source)
        self.con.execute(
            """INSERT OR IGNORE INTO promotion_requests(
               request_id,source_memory_id,target_tier,requested_by,requested_at_utc,
               explicit_user_approval,reason,request_json) VALUES(?,?,?,?,?,?,?,?)""",
            (
                request.request_id, request.source_memory_id, request.target_tier.value,
                request.requested_by, iso(request.requested_at_utc), int(request.explicit_user_approval),
                request.reason,
                json_text({**asdict(request), "target_tier": request.target_tier.value,
                           "requested_at_utc": iso(request.requested_at_utc)}),
            ),
        )
        self.con.execute(
            """INSERT OR IGNORE INTO promotion_decisions(
               decision_id,request_id,source_memory_id,outcome,target_tier,decided_at_utc,
               decided_by,reasons_json,policy_version,automatic_commit_allowed,decision_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                decision.decision_id, decision.request_id, decision.source_memory_id,
                decision.outcome.value, decision.target_tier.value, iso(decision.decided_at_utc),
                decision.decided_by, json_text(decision.reasons), decision.policy_version, 0,
                json_text(decision.to_dict()),
            ),
        )
        if long_term is not None:
            if decision.outcome is not PromotionOutcome.APPROVED:
                raise ValueError("long-term record cannot accompany a non-approved decision")
            if long_term.promotion_decision_id != decision.decision_id:
                raise ValueError("long-term record references another promotion decision")
            self.write_record(long_term)
            self.con.execute(
                "UPDATE short_term_memory_index SET promotion_status=? WHERE memory_id=?",
                (PromotionStatus.APPROVED.value, source.memory_id),
            )
        event_type = "promotion_materialized" if long_term is not None else "promotion_decided"
        ledger_id = hashlib.sha256(f"{decision.decision_id}|{event_type}".encode()).hexdigest()
        payload = {
            "source_memory_id": source.memory_id,
            "request_id": request.request_id,
            "decision_id": decision.decision_id,
            "outcome": decision.outcome.value,
            "long_term_memory_id": long_term.memory_id if long_term else None,
        }
        self.con.execute(
            """INSERT OR IGNORE INTO promotion_ledger(
               ledger_id,source_memory_id,request_id,decision_id,long_term_memory_id,
               event_type,event_at_utc,payload_json) VALUES(?,?,?,?,?,?,?,?)""",
            (ledger_id, source.memory_id, request.request_id, decision.decision_id,
             long_term.memory_id if long_term else None, event_type,
             iso(decision.decided_at_utc), json_text(payload)),
        )
        self.write_outbox(
            event_type="memory.promotion", aggregate_id=source.memory_id, payload=payload,
            idempotency_key=f"promotion:{decision.decision_id}:{event_type}",
        )
        return WriteSummary(records_written=2 if long_term else 1,
                            promotions_written=1, outbox_written=1)

    def write_outbox(
        self,
        *,
        event_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
        available_at_utc: datetime | None = None,
    ) -> str:
        self._require_transaction()
        if not event_type or not aggregate_id or not idempotency_key:
            raise ValueError("outbox event_type, aggregate_id and idempotency_key are required")
        event_id = hashlib.sha256(idempotency_key.encode()).hexdigest()
        now = utc_now()
        self.con.execute(
            """INSERT OR IGNORE INTO memory_outbox(
               event_id,idempotency_key,event_type,aggregate_id,payload_json,status,
               attempts,created_at_utc,available_at_utc)
               VALUES(?,?,?,?,?,'pending',0,?,?)""",
            (event_id, idempotency_key, event_type, aggregate_id, json_text(payload),
             iso(now), iso(available_at_utc or now)),
        )
        return event_id

    def claim_outbox(self, *, limit: int = 100, now: datetime | None = None) -> list[dict[str, Any]]:
        when = iso(now or utc_now())
        with self.transaction():
            rows = self.con.execute(
                """SELECT * FROM memory_outbox
                   WHERE status IN ('pending','failed') AND available_at_utc<=?
                   ORDER BY created_at_utc,event_id LIMIT ?""",
                (when, max(1, int(limit))),
            ).fetchall()
            ids = [str(row["event_id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self.con.execute(
                    f"UPDATE memory_outbox SET status='processing',attempts=attempts+1,claimed_at_utc=? "
                    f"WHERE event_id IN ({placeholders})", (when, *ids),
                )
            return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def complete_outbox(self, event_id: str, *, error: str | None = None) -> None:
        with self.transaction():
            if error is None:
                self.con.execute(
                    "UPDATE memory_outbox SET status='processed',processed_at_utc=?,last_error=NULL WHERE event_id=?",
                    (iso(utc_now()), event_id),
                )
            else:
                self.con.execute(
                    "UPDATE memory_outbox SET status='failed',last_error=? WHERE event_id=?",
                    (error, event_id),
                )
