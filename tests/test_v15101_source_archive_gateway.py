from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import zlib

import pytest

from latka_jazn.memory.source_archive_gateway import SourceArchiveGateway


def archive(path: Path) -> Path:
    con = sqlite3.connect(path)
    try:
        con.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE import_sources(import_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,source_name TEXT NOT NULL);
        CREATE TABLE conversations(
          conversation_id TEXT PRIMARY KEY,title TEXT,semantic_tree_sha256 TEXT NOT NULL,
          last_seen_import_id TEXT NOT NULL,payload_codec TEXT NOT NULL,payload_blob BLOB NOT NULL,
          FOREIGN KEY(last_seen_import_id) REFERENCES import_sources(import_id));
        CREATE TABLE nodes(
          conversation_id TEXT NOT NULL,node_id TEXT NOT NULL,parent_node_id TEXT,message_id TEXT,role TEXT,
          create_time REAL,timestamp_status TEXT NOT NULL,on_current_path INTEGER NOT NULL,branch_id TEXT NOT NULL,
          text_sha256 TEXT,PRIMARY KEY(conversation_id,node_id),
          FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id));
        CREATE TABLE fts_docs(
          rowid INTEGER PRIMARY KEY,conversation_id TEXT NOT NULL,node_id TEXT NOT NULL,message_id TEXT,
          role TEXT,title TEXT,create_time REAL,text_sha256 TEXT);
        CREATE VIRTUAL TABLE message_fts USING fts5(text,content='',tokenize='unicode61 remove_diacritics 2');
        """)
        payload = {
            "id": "conv-1", "title": "Katedra", "current_node": "a",
            "mapping": {
                "r": {"id": "r", "parent": None, "children": ["u"], "message": None},
                "u": {"id": "u", "parent": "r", "children": ["a"], "message": {
                    "id": "mu", "author": {"role": "user"}, "create_time": 1.0,
                    "content": {"content_type": "text", "parts": ["Pamiętasz katedrę?"]}}},
                "a": {"id": "a", "parent": "u", "children": [], "message": {
                    "id": "ma", "author": {"role": "assistant"}, "create_time": 2.0,
                    "content": {"content_type": "text", "parts": ["To był motyw snu."]}}},
            },
        }
        con.execute("INSERT INTO import_sources VALUES('imp-1',?,'chat.zip')", ("a" * 64,))
        con.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?)", (
            "conv-1", "Katedra", "b" * 64, "imp-1", "zlib-json-v1",
            zlib.compress(json.dumps(payload, ensure_ascii=False).encode()),
        ))
        rows = [
            ("conv-1", "r", None, None, None, None, "structural_only", 1, "root", None),
            ("conv-1", "u", "r", "mu", "user", 1.0, "exact", 1, "main", "c" * 64),
            ("conv-1", "a", "u", "ma", "assistant", 2.0, "exact", 1, "main", "d" * 64),
        ]
        con.executemany("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute("INSERT INTO fts_docs VALUES(1,'conv-1','u','mu','user','Katedra',1.0,?)", ("c" * 64,))
        con.execute("INSERT INTO message_fts(rowid,text) VALUES(1,'Pamiętasz katedrę?')")
        con.commit()
    finally:
        con.close()
    return path


def test_gateway_search_context_and_evidence_are_read_only(tmp_path: Path) -> None:
    path = archive(tmp_path / "archive.sqlite3")
    before = path.stat().st_mtime_ns
    with SourceArchiveGateway(path) as gateway:
        assert gateway.validate()["ok"] is True
        hits = gateway.search("katedrę")
        assert len(hits) == 1
        context = gateway.context_for_node("conv-1", "a")
        assert [node.node_id for node in context.nodes] == ["r", "u", "a"]
        assert context.nodes[-1].text == "To był motyw snu."
        evidence = context.to_evidence(segment_id="segment-1")
        assert evidence.source_type == "chat_export_archive"
        assert evidence.source_sha256 == "a" * 64
        assert evidence.node_ids == ("r", "u", "a")
    assert path.stat().st_mtime_ns == before


def test_gateway_rejects_incomplete_schema(tmp_path: Path) -> None:
    path = tmp_path / "bad.sqlite3"
    sqlite3.connect(path).close()
    with pytest.raises(sqlite3.DatabaseError, match="missing"):
        SourceArchiveGateway(path)
