from __future__ import annotations

from pathlib import Path
from typing import Any
import re

from latka_jazn.tools.memory_rebuild_common import canonical_json, fts_queries, norm, now_utc, schema_version, uid
from latka_jazn.tools.memory_rebuild_journal_reader import JournalReader
from latka_jazn.tools.memory_rebuild_sql import JOURNAL_SQL
from latka_jazn.tools.memory_rebuild_store import Store


class JournalStore(Store):
    def __init__(self, path: Path) -> None:
        super().__init__(path, JOURNAL_SQL, "journal_meta", schema_version("live_journal"))

    def import_reader(self, reader: JournalReader, dry_run: bool = False) -> dict[str, Any]:
        items = reader.items()
        source_row = self.con.execute("SELECT source_id FROM journal_sources WHERE sha256=?", (reader.sha256,)).fetchone()
        if source_row and not dry_run:
            return {"ok": True, "status": "identical_source_duplicate", "source_id": source_row[0],
                    "entries_seen": len(items), "inserted": 0, "updated_revisions": 0, "linked_existing": 0}
        if dry_run:
            counts = {"new": 0, "updated_revisions": 0, "linked_existing": 0}
            for item in items:
                row = self.con.execute("SELECT content_sha256 FROM journal_entries WHERE identity_key=?", (item.identity,)).fetchone()
                key = "new" if row is None else "linked_existing" if row[0] == item.content_hash else "updated_revisions"
                counts[key] += 1
            return {"ok": True, "status": "dry_run_ok", "entries_seen": len(items), **counts}

        source_id, current = uid("journal-source", reader.sha256), now_utc()
        inserted = updated = linked = 0
        with self.transaction():
            self.con.execute(
                """INSERT INTO journal_sources(source_id,sha256,name,path,format,imported_at_utc,
                   entry_count,invalid_count,meta_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                (source_id, reader.sha256, reader.path.name, str(reader.path), reader.format,
                 current, len(items), reader.invalid, canonical_json(reader.meta)),
            )
            for item in items:
                row = self.con.execute(
                    "SELECT entry_id,content_sha256,revision,title,summary,content FROM journal_entries WHERE identity_key=?",
                    (item.identity,),
                ).fetchone()
                previous = None
                if row is None:
                    entry_id, revision = uid("journal-entry", item.identity), 1
                    self.con.execute(
                        """INSERT INTO journal_entries(entry_id,identity_key,source_record_id,title,summary,
                           content,content_sha256,raw_json,truth_status,importance,event_time_start,event_time_end,
                           timestamp_status,suspected_fanout,status,revision,created_at_utc,updated_at_utc)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?)""",
                        (entry_id, item.identity, item.record_id, item.title, item.summary, item.content,
                         item.content_hash, canonical_json(item.raw), item.truth, item.importance, item.start,
                         item.end, item.timestamp_status, int(item.fanout), revision, current, current),
                    )
                    doc = self.con.execute(
                        "INSERT INTO journal_fts_docs(entry_id,title,truth_status,event_time_start) VALUES(?,?,?,?)",
                        (entry_id, item.title, item.truth, item.start),
                    )
                    self.con.execute("INSERT INTO journal_fts(rowid,text) VALUES(?,?)",
                                     (doc.lastrowid, f"{item.title}\n{item.summary}\n{item.content}"))
                    inserted += 1
                else:
                    entry_id, revision = str(row["entry_id"]), int(row["revision"])
                    if row["content_sha256"] == item.content_hash:
                        linked += 1
                    else:
                        previous, revision = str(row["content_sha256"]), revision + 1
                        doc = self.con.execute("SELECT rowid FROM journal_fts_docs WHERE entry_id=?", (entry_id,)).fetchone()
                        if doc:
                            old_text = f"{row['title']}\n{row['summary']}\n{row['content']}"
                            self.con.execute("INSERT INTO journal_fts(journal_fts,rowid,text) VALUES('delete',?,?)", (doc[0], old_text))
                            self.con.execute("INSERT INTO journal_fts(rowid,text) VALUES(?,?)",
                                             (doc[0], f"{item.title}\n{item.summary}\n{item.content}"))
                            self.con.execute("UPDATE journal_fts_docs SET title=?,truth_status=?,event_time_start=? WHERE entry_id=?",
                                             (item.title, item.truth, item.start, entry_id))
                        self.con.execute(
                            """UPDATE journal_entries SET source_record_id=?,title=?,summary=?,content=?,content_sha256=?,
                               raw_json=?,truth_status=?,importance=?,event_time_start=?,event_time_end=?,timestamp_status=?,
                               suspected_fanout=?,revision=?,updated_at_utc=? WHERE entry_id=?""",
                            (item.record_id, item.title, item.summary, item.content, item.content_hash,
                             canonical_json(item.raw), item.truth, item.importance, item.start, item.end,
                             item.timestamp_status, int(item.fanout), revision, current, entry_id),
                        )
                        updated += 1
                self.con.execute(
                    "INSERT OR IGNORE INTO journal_entry_sources(entry_id,source_id,source_record_id,content_sha256,seen_at_utc) VALUES(?,?,?,?,?)",
                    (entry_id, source_id, item.record_id, item.content_hash, current),
                )
                self.con.execute(
                    "INSERT OR IGNORE INTO journal_revisions(revision_id,entry_id,revision,source_id,content_sha256,previous_sha256,raw_json,created_at_utc) VALUES(?,?,?,?,?,?,?,?)",
                    (uid("journal-revision", entry_id, revision, item.content_hash), entry_id, revision,
                     source_id, item.content_hash, previous, canonical_json(item.raw), current),
                )
        return {"ok": True, "status": "imported", "source_id": source_id, "source_sha256": reader.sha256,
                "entries_seen": len(items), "inserted": inserted, "updated_revisions": updated,
                "linked_existing": linked, "invalid_entries": reader.invalid}

    def counts(self) -> dict[str, int]:
        return {
            "sources": self.con.execute("SELECT COUNT(*) FROM journal_sources").fetchone()[0],
            "entries": self.con.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0],
            "revisions": self.con.execute("SELECT COUNT(*) FROM journal_revisions").fetchone()[0],
            "suspected_fanout": self.con.execute("SELECT COUNT(*) FROM journal_entries WHERE suspected_fanout=1").fetchone()[0],
        }

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        for fts_query in fts_queries(query):
            rows = self.con.execute(
                """SELECT d.entry_id,d.title,d.truth_status,d.event_time_start,bm25(journal_fts) rank
                   FROM journal_fts JOIN journal_fts_docs d ON d.rowid=journal_fts.rowid
                   WHERE journal_fts MATCH ? ORDER BY rank LIMIT ?""", (fts_query, limit)).fetchall()
            if rows:
                return [dict(row) for row in rows]
        return []


_LEK_PATTERN = re.compile(r"\blek(?:i|u|iem|owi|ów|om|ami|ach)?\b")


def _domain_term_matches(value: str, term: str) -> bool:
    if term == "lek":
        return bool(_LEK_PATTERN.search(value))
    return term in value


def infer_domains(text: str) -> list[str]:
    value = norm(text).lower()
    patterns = {
        "relationship": ("kasia", "relacj", "blisko", "zaufan", "przytul"),
        "social": ("rozmow", "spotkan", "rodzin", "przyjac"),
        "scientific": ("badani", "nauk", "eksperyment", "hipotez"),
        "intellectual": ("analiz", "wniosk", "nauczy", "zrozum"),
        "emotional": ("emoc", "uczuc", "smut", "rado", "lęk", "wzrus"),
        "creative": ("twórc", "piosenk", "tekst", "wyobraź"),
        "technical": ("python", "kod", "runtime", "github", "sqlite", "test"),
        "health": ("zdrow", "lek", "lekarz", "migren", "padacz", "aura"),
        "music": ("muzyk", "piosenk", "utwór", "melodi"),
        "book": ("rozdział", "książk", "manuskrypt", "scena", "kanon"),
        "travel": ("wyjazd", "podróż", "görlitz", "gliwic", "jezior"),
        "nature": ("las", "łąk", "jezior", "ogród", "ptak"),
        "work": ("praca", "pracodawc", "stanowisk"),
    }
    domains = [
        name
        for name, terms in patterns.items()
        if any(_domain_term_matches(value, term) for term in terms)
    ]
    return sorted(set(domains or ["daily_life"]))
