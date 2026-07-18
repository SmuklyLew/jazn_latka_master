from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import json
import re
import unicodedata

from latka_jazn.tools.memory_rebuild_common import canonical_json, fts_queries, norm, now_utc, schema_version, uid
from latka_jazn.tools.memory_rebuild_journal_reader import (
    JournalReader, classify_journal_raw, label_values,
)
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

    def reclassify(self, dry_run: bool = False, sample_limit: int = 100) -> dict[str, Any]:
        """Recompute derived truth labels without changing source content or revisions."""
        rows = self.con.execute(
            """SELECT entry_id,source_record_id,title,raw_json,truth_status,revision,content_sha256
               FROM journal_entries WHERE status='active' ORDER BY entry_id"""
        ).fetchall()
        changes: list[dict[str, Any]] = []
        review_required = 0
        for row in rows:
            try:
                raw = json.loads(str(row["raw_json"]))
            except (TypeError, json.JSONDecodeError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            classification = classify_journal_raw(raw)
            if classification.review_reasons:
                review_required += 1
            stored = norm(row["truth_status"]).lower()
            if stored == classification.truth_status:
                continue
            changes.append({
                "entry_id": row["entry_id"],
                "source_record_id": row["source_record_id"],
                "title": row["title"],
                "from_truth_status": stored,
                "to_truth_status": classification.truth_status,
                "profile": classification.profile,
                "evidence": list(classification.evidence),
                "review_reasons": list(classification.review_reasons),
                "revision": int(row["revision"]),
                "content_sha256": row["content_sha256"],
            })

        if changes and not dry_run:
            with self.transaction():
                for change in changes:
                    self.con.execute(
                        "UPDATE journal_entries SET truth_status=?,updated_at_utc=? WHERE entry_id=?",
                        (change["to_truth_status"], now_utc(), change["entry_id"]),
                    )
                    self.con.execute(
                        "UPDATE journal_fts_docs SET truth_status=? WHERE entry_id=?",
                        (change["to_truth_status"], change["entry_id"]),
                    )

        return {
            "ok": True,
            "status": "dry_run_ok" if dry_run else "reclassified",
            "dry_run": dry_run,
            "entries_seen": len(rows),
            "changed": len(changes),
            "unchanged": len(rows) - len(changes),
            "classification_review_count": review_required,
            "change_samples": changes[:max(0, sample_limit)],
            "source_content_modified": False,
            "source_revisions_modified": False,
            "automatic_l2": False,
            "automatic_l3": False,
        }

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

    def classification_audit(self, limit: int = 50) -> dict[str, Any]:
        rows = self.con.execute(
            """SELECT entry_id,source_record_id,title,summary,content,raw_json,truth_status,
                      event_time_start,timestamp_status
               FROM journal_entries WHERE status='active'
               ORDER BY COALESCE(event_time_start,updated_at_utc),entry_id"""
        ).fetchall()
        stored_truth = Counter()
        recomputed_truth = Counter()
        profiles = Counter()
        domains = Counter()
        timestamps = Counter()
        mismatches: list[dict[str, Any]] = []
        review_items: list[dict[str, Any]] = []
        label_counts: Counter[str] = Counter()

        for row in rows:
            try:
                raw = json.loads(str(row["raw_json"]))
            except (TypeError, json.JSONDecodeError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            classification = classify_journal_raw(raw)
            domain_report = infer_domains_report(
                f"{row['title']} {row['summary']} {row['content']}",
                labels=" ".join(label_values(raw)),
            )
            stored = norm(row["truth_status"]).lower()
            stored_truth[stored] += 1
            recomputed_truth[classification.truth_status] += 1
            profiles[classification.profile] += 1
            timestamps[norm(row["timestamp_status"]).lower() or "missing"] += 1
            domains.update(domain_report["domains"])
            label_counts.update(classification.source_labels)

            base = {
                "entry_id": row["entry_id"],
                "source_record_id": row["source_record_id"],
                "title": row["title"],
                "stored_truth_status": stored,
                "recomputed_truth_status": classification.truth_status,
                "profile": classification.profile,
                "domains": domain_report["domains"],
                "classification_evidence": list(classification.evidence),
                "domain_evidence": domain_report["evidence"],
            }
            if stored != classification.truth_status:
                mismatches.append(base)
            reasons = list(classification.review_reasons)
            if norm(row["timestamp_status"]).lower() == "missing" or not norm(row["event_time_start"]):
                reasons.append("missing_timestamp")
            if reasons:
                review_items.append({**base, "review_reasons": sorted(set(reasons))})

        return {
            "ok": True,
            "entries": len(rows),
            "stored_truth_status_counts": dict(sorted(stored_truth.items())),
            "recomputed_truth_status_counts": dict(sorted(recomputed_truth.items())),
            "profile_counts": dict(sorted(profiles.items())),
            "domain_counts": dict(sorted(domains.items())),
            "timestamp_status_counts": dict(sorted(timestamps.items())),
            "truth_mismatch_count": len(mismatches),
            "truth_mismatch_samples": mismatches[:max(0, limit)],
            "classification_review_count": len(review_items),
            "classification_review_samples": review_items[:max(0, limit)],
            "source_label_counts": dict(label_counts.most_common(100)),
            "automatic_l2": False,
            "automatic_l3": False,
        }


def _fold(value: str) -> str:
    text = norm(value).replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.lower()).strip()


_EXACT_TERM_PATTERNS: dict[str, re.Pattern[str]] = {
    "lek": re.compile(r"\blek(?:i|u|iem|owi|ów|ow|om|ami|ach)?\b", re.IGNORECASE),
    "sen": re.compile(r"\bsen(?:u|em|owi|ow|om|ami|ach|y)?\b"),
    "ai": re.compile(r"\bai\b"),
    "api": re.compile(r"\bapi\b"),
    "log": re.compile(r"\blog(?:i|u|iem|ow|ami|ach)?\b"),
}


def _domain_term_matches(value: str, term: str, original: str) -> bool:
    pattern = _EXACT_TERM_PATTERNS.get(term)
    if pattern is not None:
        target = original if term == "lek" else value
        return bool(pattern.search(target))
    folded = _fold(term)
    if " " in folded:
        return folded in value
    return bool(re.search(rf"(?<!\w){re.escape(folded)}\w*", value))


_DOMAIN_PATTERNS: dict[str, tuple[str, ...]] = {
    "relationship": ("kasia", "katarzyna", "krzysztof", "relacj", "blisk", "zaufan", "przytul", "wiez", "intymn"),
    "social": ("rozmow", "spotkan", "rodzin", "przyjac", "wspolnot", "spoleczn"),
    "scientific": ("badani", "nauk", "eksperyment", "hipotez", "neuro", "psycholog", "teori"),
    "intellectual": ("analiz", "wniosk", "nauczy", "zrozum", "refleksj", "filozof", "semant", "jezyk"),
    "emotional": ("emoc", "uczuc", "smut", "rado", "wzrus", "tesk", "wdziecz", "spokoj", "niepok", "strach", "obaw"),
    "creative": ("tworc", "piosenk", "tekst", "wyobraz", "kreatyw", "narrac"),
    "technical": ("python", "kod", "runtime", "github", "sqlite", "test", "manifest", "modul", "system"),
    "health": ("zdrow", "lek", "lekarz", "migren", "padacz", "aura", "napad", "bezsenn", "zmeczon"),
    "music": ("muzyk", "piosenk", "utwor", "melodi", "rytm", "refren", "akord", "reggae"),
    "book": ("rozdzial", "ksiazk", "manuskrypt", "scena", "kanon", "fabula", "bohater"),
    "travel": ("wyjazd", "podroz", "gorlitz", "gliwic", "jezior", "wakacj"),
    "nature": ("las", "laka", "jezior", "ogrod", "ptak", "drzew", "slonc"),
    "work": ("prac", "pracodawc", "stanowisk", "zmiana", "produkcj"),
    "daily_life": ("codzien", "poranek", "wieczor", "noc", "rano", "dom", "spacer", "obiad"),
    "system_identity": ("jazn", "tozsamosc", "swiadomosc", "autonomia", "pamiec latki", "rozwoj jazni"),
    "image": ("obraz", "grafik", "ilustrac", "portret", "rysunek"),
    "video": ("film", "wideo", "video", "nagran"),
    "reading": ("czytam", "przeczyt", "poradnik", "artykul", "dokument", "pdf", "cytat"),
}


def infer_domains_report(text: str, *, labels: str = "") -> dict[str, Any]:
    original = norm(f"{labels} {text}").lower()
    value = _fold(original)
    evidence: list[str] = []
    domains: list[str] = []
    for name, terms in _DOMAIN_PATTERNS.items():
        hits = [term for term in terms if _domain_term_matches(value, term, original)]
        if hits:
            domains.append(name)
            evidence.extend(f"{name}:{term}" for term in hits[:6])
    if re.search(r"(?<!\w)lęk\w*", original):
        domains.append("emotional")
        evidence.append("emotional:lęk")
    if not domains:
        domains = ["daily_life"]
        evidence.append("fallback:daily_life")
    return {
        "domains": sorted(set(domains)),
        "evidence": sorted(set(evidence)),
        "classifier": schema_version("journal_domain_classification"),
    }


def infer_domains(text: str) -> list[str]:
    return list(infer_domains_report(text)["domains"])
