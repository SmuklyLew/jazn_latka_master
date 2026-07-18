from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pytest

from latka_jazn.memory.dziennik_migration import DziennikJsonScanner
from latka_jazn.memory.legacy_fanout_migration import LegacyFanoutMigrationStore
from latka_jazn.memory.memory_tier_store import MemoryTierStore
from latka_jazn.memory.memory_tiers import MemoryKind, MemoryTier

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)


def _journal(path: Path) -> Path:
    payload = {
        "meta": {
            "schema_version": "v14.5.1-compatible-extended",
            "opis": "kanoniczny dziennik testowy",
        },
        "entries": [
            {
                "id": "journal-1",
                "timestamp": NOW.isoformat(),
                "typ": "wpis ciągłości",
                "doświadczenie_latki": "Ważna rozmowa została zapisana.",
                "emocje_latki": {"label": "spokój operacyjny"},
                "wspomnienia_do_zachowania": ["ważna rozmowa"],
                "refleksja": "To jeden wpis, nie cztery niezależne wspomnienia.",
                "granica_prawdy": "source_recorded",
                "confidence": 0.8,
            },
            {
                "id": "journal-2",
                "data": "2026-07-18 12:00:00 CEST",
                "typ": "procedura operatorska",
                "opis": "Przed migracją wykonaj snapshot bazy.",
                "grounding": "user_confirmed",
                "importance": 0.9,
            },
            "legacy-invalid-entry",
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_dziennik_scan_stages_one_candidate_per_entry_without_memory(tmp_path: Path) -> None:
    source = _journal(tmp_path / "dziennik.json")
    with MemoryTierStore(tmp_path / "new.sqlite3") as store, DziennikJsonScanner(source) as scanner:
        migration = LegacyFanoutMigrationStore(store)
        assert scanner.inventory() == {"dziennik_entries": 2, "invalid_entries": 1}

        report = migration.stage_scan(scanner)
        assert report["inserted_candidates"] == 2
        assert report["suspected_fanout"] == 1
        assert store.stats()["memory_records"] == 0
        assert store.stats()["long_term_memory_index"] == 0

        candidates = migration.list_candidates()
        assert len(candidates) == 2
        assert {candidate.legacy_record_id for candidate in candidates} == {"journal-1", "journal-2"}
        first = next(candidate for candidate in candidates if candidate.legacy_record_id == "journal-1")
        second = next(candidate for candidate in candidates if candidate.legacy_record_id == "journal-2")
        assert first.suspected_fanout is True
        assert first.raw_record["journal_meta"]["opis"] == "kanoniczny dziennik testowy"
        assert first.raw_record["entry"]["emocje_latki"]["label"] == "spokój operacyjny"
        assert second.memory_kind is MemoryKind.PROCEDURAL

        repeated = migration.stage_scan(scanner)
        assert repeated["inserted_candidates"] == 0
        assert len(migration.list_candidates()) == 2


def test_only_explicit_journal_approval_creates_l2_with_correct_source(tmp_path: Path) -> None:
    source = _journal(tmp_path / "dziennik.json")
    with MemoryTierStore(tmp_path / "new.sqlite3") as store, DziennikJsonScanner(source) as scanner:
        migration = LegacyFanoutMigrationStore(store)
        migration.stage_scan(scanner)
        candidate = next(
            item for item in migration.list_candidates()
            if item.legacy_record_id == "journal-1"
        )

        record = migration.approve_to_l2(candidate.candidate_id, approved_by="Krzysztof", now=NOW)

        assert record.tier is MemoryTier.SHORT_TERM
        assert record.domain == "legacy_journal_migration"
        assert record.evidence[0].source_type == "legacy_dziennik_json"
        assert record.evidence[0].source_sha256 == scanner.source_sha256
        assert store.stats()["memory_records"] == 1
        assert store.stats()["short_term_memory_index"] == 1
        assert store.stats()["long_term_memory_index"] == 0
        assert store.stats()["memory_outbox"] == 1


def test_dziennik_scanner_rejects_non_entries_schema(tmp_path: Path) -> None:
    source = tmp_path / "dziennik.json"
    source.write_text(json.dumps({"meta": {}, "entries": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="entries list"):
        DziennikJsonScanner(source)
