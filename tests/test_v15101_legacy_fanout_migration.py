from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from latka_jazn.memory.legacy_fanout_migration import LegacyFanoutMigrationStore, LegacyMemoryScanner
from latka_jazn.memory.memory_tier_store import MemoryTierStore
from latka_jazn.memory.memory_tiers import MemoryTier

NOW = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)


def legacy(path: Path) -> Path:
    con = sqlite3.connect(path)
    try:
        con.executescript("""
        CREATE TABLE episodic_memories(episode_id TEXT PRIMARY KEY,created_at_utc TEXT,local_time_label TEXT,scene TEXT,participants_json TEXT,emotional_anchor TEXT,source TEXT,grounding TEXT,confidence REAL,raw_excerpt TEXT,tags_json TEXT);
        CREATE TABLE reflection_entries(reflection_id TEXT PRIMARY KEY,created_at_utc TEXT,episode_id TEXT,meaning_for_latka TEXT,identity_impact TEXT,boundary_note TEXT,next_question TEXT,confidence REAL);
        CREATE TABLE semantic_facts(fact_id TEXT PRIMARY KEY,created_at_utc TEXT,subject TEXT,predicate TEXT,value TEXT,source TEXT,confidence REAL,tags_json TEXT);
        CREATE TABLE procedural_rules(rule_id TEXT PRIMARY KEY,created_at_utc TEXT,trigger TEXT,action TEXT,reason TEXT,priority INTEGER,source TEXT);
        CREATE TABLE truth_audits(audit_id TEXT PRIMARY KEY,created_at_utc TEXT,text TEXT,audit_json TEXT);
        """)
        con.execute("INSERT INTO episodic_memories VALUES('e1',?,'','Ważna rozmowa','[]','Rozmowa','runtime','recognized',0.8,NULL,'[]')", (NOW.isoformat(),))
        con.execute("INSERT INTO reflection_entries VALUES('r1',?,'e1','Ten zapis runtime jest ważny, bo: rozmowa','impact','boundary','question',0.7)", (NOW.isoformat(),))
        con.execute("INSERT INTO semantic_facts VALUES('f1',?,'Krzysztof','lubi','rozmawiać','runtime',0.9,'[]')", (NOW.isoformat(),))
        con.execute("INSERT INTO procedural_rules VALUES('p1',?,'gdy użytkownik pyta','odpowiedz prawdziwie','granica prawdy',90,'runtime')", (NOW.isoformat(),))
        con.execute("INSERT INTO truth_audits VALUES('a1',?,'audit','[]')", (NOW.isoformat(),))
        con.commit()
    finally:
        con.close()
    return path


def test_scan_stages_review_candidates_without_creating_memory(tmp_path: Path) -> None:
    source = legacy(tmp_path / "legacy.sqlite3")
    with MemoryTierStore(tmp_path / "new.sqlite3") as store, LegacyMemoryScanner(source) as scanner:
        migration = LegacyFanoutMigrationStore(store)
        report = migration.stage_scan(scanner)
        assert report["inserted_candidates"] == 4
        assert report["suspected_fanout"] == 1
        assert store.stats()["memory_records"] == 0
        assert len(migration.list_candidates()) == 4
        repeated = migration.stage_scan(scanner)
        assert repeated["inserted_candidates"] == 0


def test_only_explicit_candidate_approval_creates_l2(tmp_path: Path) -> None:
    source = legacy(tmp_path / "legacy.sqlite3")
    with MemoryTierStore(tmp_path / "new.sqlite3") as store, LegacyMemoryScanner(source) as scanner:
        migration = LegacyFanoutMigrationStore(store)
        migration.stage_scan(scanner)
        candidate = next(item for item in migration.list_candidates() if item.legacy_table == "episodic_memories")
        record = migration.approve_to_l2(candidate.candidate_id, approved_by="Krzysztof", now=NOW)
        assert record.tier is MemoryTier.SHORT_TERM
        assert store.stats()["memory_records"] == 1
        assert store.stats()["long_term_memory_index"] == 0
        assert store.stats()["memory_outbox"] == 1
