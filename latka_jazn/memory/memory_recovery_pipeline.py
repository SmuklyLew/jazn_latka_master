from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import sqlite3

from latka_jazn.config import JaznConfig
from latka_jazn.memory.legacy_memory_recovery import LegacyMemoryRecovery
from latka_jazn.memory.memory_promotion import LongTermPromotionPolicy, new_promotion_request
from latka_jazn.memory.memory_tier_store import MemoryTierStore
from latka_jazn.memory.memory_tiers import (
    MemoryKind,
    MemoryTier,
    MemoryTruthStatus,
    ShortTermMemoryPolicy,
    ShortTermMemoryRecord,
    SourceEvidence,
)
from latka_jazn.memory.normalization_sidecar import MemoryNormalizationSidecar
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("memory_recovery_pipeline")
TRUTH_BOUNDARY = (
    "The recovery pipeline rebuilds a new source database, normalizes it, creates a verified wake state, "
    "and may seed reviewed L2 records. L3 is written only from an exact manifest whose SHA-256 is supplied "
    "with an explicit approver; no bulk or implicit long-term promotion is allowed."
)


@dataclass(slots=True)
class MemoryRecoveryPipelineReport:
    schema_version: str
    status: str
    root: str
    recovery: dict[str, Any] | None
    normalization: dict[str, Any] | None
    wake_state: dict[str, Any] | None
    l2: dict[str, Any] | None
    l3_manifest: dict[str, Any] | None
    l3_apply: dict[str, Any] | None
    errors: list[str]
    truth_boundary: str = TRUTH_BOUNDARY

    @property
    def ok(self) -> bool:
        return self.status in {"ready", "ready_with_l2", "ready_with_l3"}

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _kind(value: str) -> MemoryKind:
    mapping = {
        "procedural_rule": MemoryKind.PROCEDURAL,
        "semantic_fact": MemoryKind.SEMANTIC,
        "episodic_memory": MemoryKind.EPISODIC,
        "reflection_entry": MemoryKind.REFLECTION,
        "truth_audit": MemoryKind.PROCEDURAL,
        "legacy_journal_entry": MemoryKind.EPISODIC,
    }
    return mapping.get(value, MemoryKind.CONVERSATION_CONTEXT)


def _truth(value: str) -> MemoryTruthStatus:
    text = value.strip().lower()
    if "user_confirmed" in text or "canonical" in text:
        return MemoryTruthStatus.USER_CONFIRMED
    if any(token in text for token in ("source_recorded", "runtime_rule", "truth_boundary", "semantic_claim", "curated")):
        return MemoryTruthStatus.SOURCE_RECORDED
    if "symbolic" in text or "book_scene" in text:
        return MemoryTruthStatus.SYMBOLIC
    return MemoryTruthStatus.INFERRED


class MemoryRecoveryPipeline:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.config = JaznConfig(root=self.root)
        self.recovery = LegacyMemoryRecovery(self.root)
        self.sidecar = MemoryNormalizationSidecar(
            self.root,
            source_db_path=self.config.recovered_memory_db_path,
            sidecar_db_path=self.config.normalization_sidecar_db_path,
            runtime_version=self.config.version,
        )
        self.manifest_path = self.config.runtime_workspace_dir / "memory_recovery" / "l3_approval_manifest.json"

    def run(
        self,
        *,
        force_recovery: bool = False,
        normalize_limit: int | None = None,
        prepare_l2: bool = False,
        l2_limit: int = 120,
        build_l3_manifest: bool = False,
        l3_limit: int = 25,
        approve_l3_manifest_sha: str | None = None,
        approved_by: str | None = None,
        progress=None,
    ) -> MemoryRecoveryPipelineReport:
        errors: list[str] = []
        recovery_report = self.recovery.rebuild(force=force_recovery, progress=progress)
        if not recovery_report.ok:
            return self._report("recovery_failed", recovery=recovery_report.to_dict(), errors=recovery_report.errors)
        normalization = self.sidecar.normalize(limit=normalize_limit if normalize_limit is not None else 12000)
        if normalization.status != "ok":
            return self._report(
                "normalization_failed", recovery=recovery_report.to_dict(),
                normalization=normalization.to_dict(), errors=normalization.errors,
            )
        wake = self.sidecar.build_wake_state()
        if wake.status != "ready":
            return self._report(
                "wake_state_failed", recovery=recovery_report.to_dict(),
                normalization=normalization.to_dict(), wake_state=wake.to_dict(), errors=wake.errors,
            )
        l2_report = self.prepare_l2(limit=l2_limit) if prepare_l2 else None
        if build_l3_manifest:
            l3_manifest = self.build_l3_manifest(limit=l3_limit)
        elif approve_l3_manifest_sha and self.manifest_path.is_file():
            l3_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            l3_manifest["path"] = str(self.manifest_path)
        elif approve_l3_manifest_sha:
            l3_manifest = self.build_l3_manifest(limit=l3_limit)
        else:
            l3_manifest = None
        l3_apply = None
        if approve_l3_manifest_sha:
            if not approved_by or not approved_by.strip():
                errors.append("approved_by is required for L3")
            elif not l3_manifest:
                errors.append("L3 manifest unavailable")
            else:
                l3_apply = self.apply_l3_manifest(
                    expected_sha256=approve_l3_manifest_sha,
                    approved_by=approved_by,
                )
                errors.extend(l3_apply.get("errors") or [])
        status = "ready"
        if l2_report and l2_report.get("written", 0):
            status = "ready_with_l2"
        if l3_apply and l3_apply.get("materialized", 0) and not errors:
            status = "ready_with_l3"
        if errors:
            status = "completed_with_warnings"
        return self._report(
            status,
            recovery=recovery_report.to_dict(),
            normalization=normalization.to_dict(),
            wake_state=wake.to_dict(),
            l2=l2_report,
            l3_manifest=l3_manifest,
            l3_apply=l3_apply,
            errors=errors,
        )

    def prepare_l2(self, *, limit: int = 120) -> dict[str, Any]:
        rows = self._candidate_rows(limit=max(1, limit))
        policy = ShortTermMemoryPolicy()
        written = skipped = 0
        ids: list[str] = []
        with MemoryTierStore(self.config.memory_tier_db_path) as store:
            for row in rows:
                evidence = self._evidence(row)
                truth = _truth(str(row["truth_status"] or ""))
                if truth not in {MemoryTruthStatus.SOURCE_RECORDED, MemoryTruthStatus.USER_CONFIRMED}:
                    skipped += 1
                    continue
                record = policy.create(
                    kind=_kind(str(row["memory_type"])),
                    content=str(row["content_excerpt"]),
                    domain=str(row["memory_namespace"] or "recovered_memory"),
                    mode="legacy_recovery_reviewed",
                    truth_status=truth,
                    confidence=float(row["confidence"] or 0.0),
                    importance=float(row["importance"] or 0.0),
                    evidence=(evidence,),
                    created_at_utc=_now(),
                    tags=("legacy_recovery", "reviewed_l2", str(row["memory_type"])),
                )
                record = replace(
                    record,
                    reinforcement_count=1,
                    last_reinforced_at_utc=_now(),
                    reinforcement_evidence_keys=(evidence.evidence_key,),
                )
                try:
                    store.save_record(record)
                except sqlite3.IntegrityError as exc:
                    if "UNIQUE constraint failed: memory_records.tier" not in str(exc):
                        raise
                    skipped += 1
                    continue
                written += 1
                ids.append(record.memory_id)
        return {
            "status": "ready",
            "selected": len(rows),
            "written": written,
            "skipped": skipped,
            "memory_ids": ids,
            "database_path": str(self.config.memory_tier_db_path),
            "automatic_l3": False,
            "truth_boundary": TRUTH_BOUNDARY,
        }

    def build_l3_manifest(self, *, limit: int = 25) -> dict[str, Any]:
        with MemoryTierStore(self.config.memory_tier_db_path) as store:
            records = [r for r in store.list_records(tier=MemoryTier.SHORT_TERM) if isinstance(r, ShortTermMemoryRecord)]
        candidates = [record for record in records if self._safe_l3_candidate(record)]
        candidates.sort(key=lambda r: (-r.importance, -r.confidence, r.memory_id))
        deduplicated: list[ShortTermMemoryRecord] = []
        seen_content: set[str] = set()
        for record in candidates:
            content_key = " ".join(record.content.lower().split())
            content_key = content_key.split(". reason:", 1)[0]
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            deduplicated.append(record)
        candidates = deduplicated[: max(0, int(limit))]
        payload = {
            "schema_version": schema_version("l3_approval_manifest"),
            "created_at_utc": _now().isoformat(),
            "source_database": str(self.config.memory_tier_db_path),
            "candidate_count": len(candidates),
            "candidates": [
                {
                    "memory_id": r.memory_id,
                    "content_sha256": r.content_sha256,
                    "kind": r.kind.value,
                    "domain": r.domain,
                    "truth_status": r.truth_status.value,
                    "confidence": r.confidence,
                    "importance": r.importance,
                    "evidence_keys": [e.evidence_key for e in r.evidence],
                    "content_excerpt": r.content[:320],
                }
                for r in candidates
            ],
            "automatic_commit_allowed": False,
            "selection_policy": {
                "stable_truth_or_provenance_signal_required": True,
                "deprecated_fanout_and_version_specific_rules_excluded": True,
                "exact_content_deduplication": True,
                "minimum_confidence": 0.72,
                "minimum_importance": 0.62,
                "minimum_reinforcement_count": 1,
            },
            "approval_contract": "Apply only when this exact canonical manifest SHA-256 is supplied with approved_by.",
            "truth_boundary": TRUTH_BOUNDARY,
        }
        canonical = _canonical_json(payload)
        manifest_sha = _sha256_text(canonical)
        payload["manifest_sha256"] = manifest_sha
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {**payload, "path": str(self.manifest_path)}

    def apply_l3_manifest(self, *, expected_sha256: str, approved_by: str) -> dict[str, Any]:
        errors: list[str] = []
        if not self.manifest_path.is_file():
            return {"status": "manifest_missing", "materialized": 0, "errors": [str(self.manifest_path)]}
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        stored_sha = str(payload.pop("manifest_sha256", ""))
        actual_sha = _sha256_text(_canonical_json(payload))
        if stored_sha != actual_sha or expected_sha256 != actual_sha:
            return {
                "status": "manifest_sha_mismatch", "materialized": 0,
                "expected_sha256": expected_sha256, "stored_sha256": stored_sha,
                "actual_sha256": actual_sha, "errors": ["exact L3 manifest approval required"],
            }
        policy = LongTermPromotionPolicy()
        materialized = decided = skipped = 0
        ids: list[str] = []
        with MemoryTierStore(self.config.memory_tier_db_path) as store:
            with store.transaction():
                for item in payload.get("candidates") or []:
                    record = store.get_record(str(item.get("memory_id") or ""))
                    if not isinstance(record, ShortTermMemoryRecord):
                        skipped += 1
                        continue
                    if record.content_sha256 != item.get("content_sha256"):
                        errors.append(f"content_sha mismatch: {record.memory_id}")
                        skipped += 1
                        continue
                    request = new_promotion_request(
                        record,
                        requested_by=approved_by,
                        reason="Explicit user-approved legacy recovery L3 manifest",
                        explicit_user_approval=True,
                    )
                    decision = policy.evaluate(record, request)
                    long_term = None
                    if decision.outcome.value == "approved":
                        long_term = policy.materialize(record, request, decision, approved_by=approved_by)
                        materialized += 1
                        ids.append(long_term.memory_id)
                    else:
                        skipped += 1
                    store.write_promotion(record, request, decision, long_term)
                    decided += 1
        return {
            "status": "ready" if not errors else "completed_with_warnings",
            "manifest_sha256": actual_sha,
            "approved_by": approved_by,
            "decided": decided,
            "materialized": materialized,
            "skipped": skipped,
            "long_term_memory_ids": ids,
            "errors": errors,
            "automatic_commit_allowed": False,
            "truth_boundary": TRUTH_BOUNDARY,
        }

    @staticmethod
    def _safe_l3_candidate(record: ShortTermMemoryRecord) -> bool:
        if record.kind not in {MemoryKind.PROCEDURAL, MemoryKind.SEMANTIC}:
            return False
        if record.truth_status not in {MemoryTruthStatus.SOURCE_RECORDED, MemoryTruthStatus.USER_CONFIRMED}:
            return False
        if record.confidence < 0.72 or record.importance < 0.62 or record.reinforcement_count < 1:
            return False
        text = " ".join(record.content.lower().split())
        deprecated = (
            "runtime_events.jsonl", "dziennik + warstwy", "warstwy jsonl +", "versionupdaterecorder",
            "projectstartupindexer", "topicmismatchguard", "lexicalsemanticunderstanding",
            "cognitivetopicexpansion", "cognitiveruntimeoperatingmodel", "trasy v14",
            "v14.", "odpowiadać w pierwszej osobie jako łatka", "słownik", "slownik",
            "githubrepositoryplan", "latka.jazn.memory", "dzienniku i warstwach",
            "warstwach jsonl", "dziennik +", "fan-out", "fanout",
        )
        if any(token in text for token in deprecated):
            return False
        stable_signals = (
            "granica_prawdy", "granica prawdy", "truth boundary", "source_origin",
            "źródł", "zrodł", "nie promować", "nie promowac",
            "nie wolno twierdzić", "nie wolno twierdzic", "bez zmyślania",
            "bez zmyslania", "nie dopowiadam", "nie dopowiadać", "nie dopowiadac",
            "grounding", "confidence", "duplikat",
        )
        return any(token in text for token in stable_signals)

    def _candidate_rows(self, *, limit: int) -> list[sqlite3.Row]:
        con = sqlite3.connect(f"file:{self.config.normalization_sidecar_db_path.resolve().as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                """SELECT * FROM normalized_memory_items
                   WHERE source_table IN ('procedural_rules','semantic_facts','truth_audits','episodic_memories','reflection_entries')
                     AND confidence>=0.62 AND importance>=0.55
                   ORDER BY CASE source_table WHEN 'procedural_rules' THEN 0 WHEN 'truth_audits' THEN 1
                              WHEN 'semantic_facts' THEN 2 ELSE 3 END,
                            importance DESC,confidence DESC,source_timestamp DESC,item_id
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        finally:
            con.close()

    @staticmethod
    def _evidence(row: sqlite3.Row) -> SourceEvidence:
        raw = str(row["content_excerpt"] or "")
        return SourceEvidence(
            source_type="normalized_recovery_sidecar",
            source_id=str(row["item_id"]),
            source_sha256=str(row["source_sha256"] or "") or None,
            conversation_id=str(row["conversation_id"] or "") or None,
            exact_excerpt_sha256=_sha256_text(raw),
            timestamp_status="source_recorded" if row["source_timestamp"] else "unknown",
            metadata={
                "source_table": row["source_table"],
                "source_row_id": row["source_row_id"],
                "grounding": row["grounding"],
                "truth_status": row["truth_status"],
                "normalization_run_id": row["run_id"],
            },
        )

    def _report(self, status: str, **kwargs: Any) -> MemoryRecoveryPipelineReport:
        return MemoryRecoveryPipelineReport(
            schema_version=SCHEMA_VERSION,
            status=status,
            root=str(self.root),
            recovery=kwargs.get("recovery"),
            normalization=kwargs.get("normalization"),
            wake_state=kwargs.get("wake_state"),
            l2=kwargs.get("l2"),
            l3_manifest=kwargs.get("l3_manifest"),
            l3_apply=kwargs.get("l3_apply"),
            errors=list(kwargs.get("errors") or []),
        )
