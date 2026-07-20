from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sqlite3

from latka_jazn.config import JaznConfig
from latka_jazn.memory.legacy_memory_recovery import LegacyMemoryRecovery
from latka_jazn.memory.memory_recovery_pipeline import MemoryRecoveryPipeline
from latka_jazn.memory.memory_tier_store import MemoryTierStore
from latka_jazn.memory.memory_tiers import MemoryTier
from latka_jazn.memory.normalization_sidecar import MemoryNormalizationSidecar
from latka_jazn.memory.wake_state_runtime import WakeStateRuntimeBridge


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_archive(root: Path) -> None:
    base = root / "memory/sqlite/conversation_archive_v1"
    base.mkdir(parents=True)
    shard = base / "conversation_archive_0001.sqlite3"
    with sqlite3.connect(shard) as con:
        con.executescript(
            """
            CREATE TABLE archive_conversations(
              conversation_uid TEXT PRIMARY KEY, source_uid TEXT, conversation_index INTEGER,
              source_conversation_id TEXT, title TEXT, create_time TEXT, update_time TEXT,
              source_format TEXT, current_node TEXT, visible_node_count INTEGER,
              source_node_count INTEGER, message_count INTEGER, occurrence_count INTEGER
            );
            CREATE TABLE archive_messages(
              message_uid TEXT PRIMARY KEY, conversation_uid TEXT, source_message_id TEXT,
              node_id TEXT, parent_node_id TEXT, role TEXT, author_label TEXT, model_slug TEXT,
              default_model_slug TEXT, content_type TEXT, create_time TEXT, is_visible_path INTEGER,
              visible_index INTEGER, content_hash TEXT, content_shard_id TEXT, normalized_hash TEXT,
              logical_hash TEXT, text_length INTEGER, first_source_uid TEXT,
              first_occurrence_uid TEXT, occurrence_count INTEGER
            );
            CREATE TABLE content_blobs(
              content_hash TEXT PRIMARY KEY, normalized_hash TEXT, text TEXT, char_count INTEGER,
              byte_count INTEGER, first_occurrence_uid TEXT, first_source_uid TEXT, created_at_utc TEXT
            );
            CREATE TABLE archive_message_occurrences(
              occurrence_uid TEXT PRIMARY KEY, message_uid TEXT, conversation_uid TEXT, source_uid TEXT,
              source_conversation_id TEXT, source_message_id TEXT, node_id TEXT, parent_node_id TEXT,
              conversation_index INTEGER, message_index INTEGER, source_order INTEGER,
              is_visible_path INTEGER, visible_index INTEGER, source_locator TEXT,
              occurrence_hash TEXT, content_hash TEXT
            );
            """
        )
        content = "Krzysztof potwierdził, że pamięć ma być przywracana tylko ze źródłami."
        digest = hashlib.sha256(content.encode()).hexdigest()
        con.execute(
            "INSERT INTO archive_conversations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("conv-1", "src-1", 1, "source-conv", "Test recovery", "2026-07-20T10:00:00+00:00",
             "2026-07-20T10:01:00+00:00", "test", "node-1", 1, 1, 1, 1),
        )
        con.execute(
            "INSERT INTO content_blobs VALUES(?,?,?,?,?,?,?,?)",
            (digest, digest, content, len(content), len(content.encode()), "occ-1", "src-1", "2026-07-20T10:00:00+00:00"),
        )
        con.execute(
            "INSERT INTO archive_messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("msg-1", "conv-1", "source-msg", "node-1", None, "user", "user", None, None, "text",
             "2026-07-20T10:00:00+00:00", 1, 1, digest, "archive_0001", digest, digest,
             len(content), "src-1", "occ-1", 1),
        )
        con.execute(
            "INSERT INTO archive_message_occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("occ-1", "msg-1", "conv-1", "src-1", "source-conv", "source-msg", "node-1", None,
             1, 1, 1, 1, 1, "chat.html#conv/msg", digest, digest),
        )
    manifest = base / "conversation_archive_manifest.sqlite3"
    with sqlite3.connect(manifest) as con:
        con.executescript(
            """
            CREATE TABLE archive_sources(
              source_uid TEXT PRIMARY KEY,path TEXT,source_name TEXT,sha256 TEXT,size_bytes INTEGER,
              imported_at_utc TEXT,parser_version TEXT,source_kind TEXT
            );
            CREATE TABLE shard_files(
              shard_id TEXT PRIMARY KEY,family TEXT,ordinal INTEGER,relative_path TEXT,row_count INTEGER,
              size_bytes INTEGER,size_mib REAL,sha256 TEXT,integrity_check TEXT,
              foreign_key_error_count INTEGER,hard_limit_bytes INTEGER,over_limit INTEGER,created_at_utc TEXT
            );
            """
        )
        con.execute(
            "INSERT INTO archive_sources VALUES(?,?,?,?,?,?,?,?)",
            ("src-1", "chat.html", "chat.html", "a" * 64, 100, "2026-07-20T10:00:00+00:00", "test", "test"),
        )
        con.execute(
            "INSERT INTO shard_files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("archive_0001", "archive", 1, shard.name, 1, shard.stat().st_size, 0.1, _sha(shard),
             "ok", 0, 999999999, 0, "2026-07-20T10:00:00+00:00"),
        )


def _build_structured(root: Path) -> None:
    raw = root / "memory/raw"
    layered = root / "memory/layered"
    raw.mkdir(parents=True)
    layered.mkdir(parents=True)
    (raw / "dziennik.json").write_text(
        json.dumps({"entries": [{"id": "j1", "timestamp": "2026-07-20T10:02:00+00:00", "typ": "ustalenie", "treść": "Nie promować pamięci automatycznie do L3."}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (layered / "procedural.jsonl").write_text(
        json.dumps({
            "rule_id": "r1", "created_at_utc": "2026-07-20T10:03:00+00:00",
            "trigger": "legacy recovery", "action": "require exact manifest approval",
            "reason": "truth boundary", "priority": 90, "source": "user_confirmed_test",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for name in ("episodic", "semantic", "reflections", "truth_audits"):
        (layered / f"{name}.jsonl").write_text("", encoding="utf-8")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    _build_archive(root)
    _build_structured(root)
    legacy = root / "memory/sqlite/runtime_write_v1/runtime_memory.sqlite3"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"not-a-sqlite-database")
    return root


def test_recovery_is_atomic_and_never_overwrites_corrupt_legacy(tmp_path: Path) -> None:
    root = _root(tmp_path)
    legacy = root / "memory/sqlite/runtime_write_v1/runtime_memory.sqlite3"
    before = _sha(legacy)
    recovery = LegacyMemoryRecovery(root)
    report = recovery.rebuild()
    assert report.ok, report.errors
    assert _sha(legacy) == before
    assert Path(report.output_path).is_file()
    with sqlite3.connect(report.output_path) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM procedural_rules").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM recovery_provenance").fetchone()[0] == 3
    again = recovery.rebuild()
    assert again.status == "already_current"


def test_pipeline_builds_sidecar_wake_l1_l2_and_exact_l3_ledger(tmp_path: Path) -> None:
    root = _root(tmp_path)
    pipeline = MemoryRecoveryPipeline(root)
    first = pipeline.run(prepare_l2=True, l2_limit=10, build_l3_manifest=True, l3_limit=10)
    assert first.ok, first.errors
    assert first.wake_state and first.wake_state["status"] == "ready"
    assert first.l2 and first.l2["written"] >= 1
    manifest = first.l3_manifest
    assert manifest and manifest["manifest_sha256"]

    applied = pipeline.apply_l3_manifest(
        expected_sha256=manifest["manifest_sha256"],
        approved_by="explicit_user_request_test",
    )
    assert applied["status"] == "ready", applied
    assert applied["materialized"] >= 1

    cfg = JaznConfig(root=root)
    bridge = WakeStateRuntimeBridge(cfg)
    wake = bridge.hydrate_l1(session_id="session-test")
    assert wake.status == "hydrated", wake.errors
    with MemoryTierStore(cfg.memory_tier_db_path) as store:
        assert store.validate(full=True)["ok"] is True
        assert len(store.list_records(tier=MemoryTier.WORKING, session_id="session-test")) == 1
        assert len(store.list_records(tier=MemoryTier.SHORT_TERM)) >= 1
        assert len(store.list_records(tier=MemoryTier.LONG_TERM)) >= 1
        stats = store.stats()
        assert stats["promotion_requests"] >= 1
        assert stats["promotion_decisions"] >= 1
        assert stats["promotion_ledger"] >= 1
    assert bridge.end_session("session-test") == 1


def test_memory_prepare_prefers_recovered_database(tmp_path: Path) -> None:
    root = _root(tmp_path)
    LegacyMemoryRecovery(root).rebuild()
    cfg = JaznConfig(root=root)
    assert cfg.memory_db_path == cfg.recovered_memory_db_path
    sidecar = MemoryNormalizationSidecar(root)
    assert sidecar.source_db_path == cfg.recovered_memory_db_path
