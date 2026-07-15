from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json

from latka_jazn.memory.store import MemoryStore


@dataclass(slots=True)
class MemoryFileSyncReport:
    imported: dict[str, int]
    exported: dict[str, int]
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"imported": self.imported, "exported": self.exported, "errors": self.errors}


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                obj.setdefault("_source_line", line_no)
                yield obj


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(*parts: Any) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)
    return len(rows)


class MemoryFileSync:
    """Synchronizacja pamięci między plikami JSON/JSONL i SQLite.

    Naprawia problem, w którym pliki pamięci istnieją, ale runtime widzi tylko to,
    co zostało dopisane do SQLite w bieżącym uruchomieniu. Import plików jest
    idempotentny, bo używa istniejących ID albo stabilnych hashy.
    """

    def __init__(self, root: Path, store: MemoryStore) -> None:
        self.root = Path(root)
        self.store = store
        self.errors: list[str] = []

    def import_files_to_sqlite(self) -> dict[str, int]:
        counts = {
            "layered_episodic": 0,
            "layered_semantic": 0,
            "layered_procedural": 0,
            "layered_reflections": 0,
            "layered_truth_audits": 0,
            "raw_episodic_memory": 0,
            "raw_dziennik_entries": 0,
        }
        base = self.root / "memory" / "layered"

        for rec in _iter_jsonl(base / "episodic.jsonl") or []:
            try:
                episode = {
                    "episode_id": rec.get("episode_id") or rec.get("id") or _stable_id("episodic", rec.get("scene"), rec.get("created_at_utc")),
                    "created_at_utc": rec.get("created_at_utc") or _now_utc(),
                    "local_time_label": rec.get("local_time_label"),
                    "scene": _safe_text(rec.get("scene") or rec.get("content") or rec.get("text")),
                    "participants": rec.get("participants") or [],
                    "emotional_anchor": rec.get("emotional_anchor") or rec.get("title"),
                    "source": rec.get("source") or "memory/layered/episodic.jsonl",
                    "grounding": rec.get("grounding") or "recovered",
                    "confidence": float(rec.get("confidence") or 0.55),
                    "raw_excerpt": rec.get("raw_excerpt") or rec.get("content"),
                    "tags": rec.get("tags") or [],
                }
                if episode["scene"]:
                    self.store.add_episodic_memory(episode)
                    counts["layered_episodic"] += 1
            except Exception as exc:
                self.errors.append(f"episodic.jsonl:{rec.get('_source_line')}: {exc!r}")

        for rec in _iter_jsonl(base / "semantic.jsonl") or []:
            try:
                fact = {
                    "fact_id": rec.get("fact_id") or rec.get("id") or _stable_id("semantic", rec.get("subject"), rec.get("predicate"), rec.get("value")),
                    "created_at_utc": rec.get("created_at_utc") or _now_utc(),
                    "subject": rec.get("subject") or "Pamięć Jaźni",
                    "predicate": rec.get("predicate") or "zawiera",
                    "value": _safe_text(rec.get("value") or rec.get("content") or rec.get("text")),
                    "source": rec.get("source") or "memory/layered/semantic.jsonl",
                    "confidence": float(rec.get("confidence") or 0.55),
                    "tags": rec.get("tags") or [],
                }
                if fact["value"]:
                    self.store.add_semantic_fact(fact)
                    counts["layered_semantic"] += 1
            except Exception as exc:
                self.errors.append(f"semantic.jsonl:{rec.get('_source_line')}: {exc!r}")

        for rec in _iter_jsonl(base / "procedural.jsonl") or []:
            try:
                rule = {
                    "rule_id": rec.get("rule_id") or rec.get("id") or _stable_id("procedural", rec.get("trigger"), rec.get("action"), rec.get("reason")),
                    "created_at_utc": rec.get("created_at_utc") or _now_utc(),
                    "trigger": rec.get("trigger") or "nieopisany sygnał",
                    "action": rec.get("action") or "zachować ostrożność",
                    "reason": rec.get("reason") or "import z pliku procedural.jsonl",
                    "priority": int(rec.get("priority") or 50),
                    "source": rec.get("source") or "memory/layered/procedural.jsonl",
                }
                self.store.add_procedural_rule(rule)
                counts["layered_procedural"] += 1
            except Exception as exc:
                self.errors.append(f"procedural.jsonl:{rec.get('_source_line')}: {exc!r}")

        for rec in _iter_jsonl(base / "reflections.jsonl") or []:
            try:
                reflection = {
                    "reflection_id": rec.get("reflection_id") or rec.get("id") or _stable_id("reflection", rec.get("meaning_for_latka"), rec.get("created_at_utc")),
                    "created_at_utc": rec.get("created_at_utc") or _now_utc(),
                    "episode_id": rec.get("episode_id"),
                    "meaning_for_latka": _safe_text(rec.get("meaning_for_latka") or rec.get("content") or rec.get("text")),
                    "identity_impact": rec.get("identity_impact") or "import z pliku refleksji",
                    "boundary_note": rec.get("boundary_note") or rec.get("granica_prawdy") or "recovered from file",
                    "next_question": rec.get("next_question"),
                    "confidence": float(rec.get("confidence") or 0.55),
                }
                if reflection["meaning_for_latka"]:
                    self.store.add_reflection(reflection)
                    counts["layered_reflections"] += 1
            except Exception as exc:
                self.errors.append(f"reflections.jsonl:{rec.get('_source_line')}: {exc!r}")

        for rec in _iter_jsonl(base / "truth_audits.jsonl") or []:
            try:
                audit = {
                    "created_at_utc": rec.get("created_at_utc") or _now_utc(),
                    "text": rec.get("text") or json.dumps(rec.get("audit") or rec, ensure_ascii=False)[:1000],
                    "audit": rec.get("audit") or [],
                }
                self.store.add_truth_audit(audit)
                counts["layered_truth_audits"] += 1
            except Exception as exc:
                self.errors.append(f"truth_audits.jsonl:{rec.get('_source_line')}: {exc!r}")

        for rec in _iter_jsonl(self.root / "memory" / "raw" / "episodic_memory.jsonl") or []:
            try:
                episode = {
                    "episode_id": rec.get("id") or _stable_id("raw_episodic", rec.get("content"), rec.get("datetime")),
                    "created_at_utc": rec.get("datetime") or _now_utc(),
                    "local_time_label": rec.get("datetime"),
                    "scene": _safe_text(rec.get("content") or rec.get("title")),
                    "participants": rec.get("participants") or [],
                    "emotional_anchor": ", ".join(rec.get("emotions") or []) if isinstance(rec.get("emotions"), list) else rec.get("title"),
                    "source": rec.get("source") or "memory/raw/episodic_memory.jsonl",
                    "grounding": "recovered",
                    "confidence": 0.58,
                    "raw_excerpt": _safe_text(rec.get("content")),
                    "tags": (rec.get("tags") or []) + (rec.get("category") or [] if isinstance(rec.get("category"), list) else []),
                }
                if episode["scene"]:
                    self.store.add_episodic_memory(episode)
                    counts["raw_episodic_memory"] += 1
            except Exception as exc:
                self.errors.append(f"episodic_memory.jsonl:{rec.get('_source_line')}: {exc!r}")

        dz = self.root / "memory" / "raw" / "dziennik.json"
        if dz.exists():
            try:
                data = json.loads(dz.read_text(encoding="utf-8"))
                for entry in data.get("entries", []) if isinstance(data, dict) else []:
                    if not isinstance(entry, dict):
                        continue
                    jid = entry.get("id") or _stable_id("dziennik", entry.get("timestamp"), entry.get("treść") or entry.get("content") or entry)
                    text = _safe_text(entry.get("treść") or entry.get("doświadczenie_latki") or entry.get("content") or entry.get("tytuł") or entry)[:2000]
                    self.store.con.execute(
                        "INSERT OR REPLACE INTO journal VALUES(?,?,?,?,?,?)",
                        (
                            jid,
                            entry.get("timestamp") or entry.get("created_at_utc") or _now_utc(),
                            entry.get("data") or entry.get("local_time_label") or entry.get("timestamp") or _now_utc(),
                            entry.get("typ") or entry.get("kind") or "raw_dziennik",
                            text,
                            json.dumps(entry, ensure_ascii=False, sort_keys=True),
                        ),
                    )
                    counts["raw_dziennik_entries"] += 1
                self.store.con.commit()
            except Exception as exc:
                self.errors.append(f"dziennik.json: {exc!r}")

        return counts

    def export_sqlite_to_files(self) -> dict[str, int]:
        out = self.root / "memory" / "exported_from_sqlite"
        exported: dict[str, int] = {}
        table_files = {
            "episodic_memories": "episodic_from_sqlite.jsonl",
            "semantic_facts": "semantic_from_sqlite.jsonl",
            "procedural_rules": "procedural_from_sqlite.jsonl",
            "reflection_entries": "reflections_from_sqlite.jsonl",
            "truth_audits": "truth_audits_from_sqlite.jsonl",
            "journal": "journal_from_sqlite.jsonl",
        }
        for table, filename in table_files.items():
            rows = [dict(r) for r in self.store.con.execute(f"SELECT * FROM {table}").fetchall()]
            exported[filename] = _write_jsonl(out / filename, rows)
        stats = self.store.stats()
        (out / "README.txt").write_text(
            "Eksport SQLite do plików JSONL. Pliki są lustrzanym zapisem pamięci runtime "
            "i ułatwiają audyt oraz przenoszenie pamięci bez polegania wyłącznie na bazie SQLite.\n"
            + json.dumps(stats, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return exported

    def synchronize_all(self, *, export: bool = True) -> MemoryFileSyncReport:
        imported = self.import_files_to_sqlite()
        exported = self.export_sqlite_to_files() if export else {}
        self.store.set_meta("memory_file_sync_last_report", json.dumps({"imported": imported, "exported": exported, "errors": self.errors}, ensure_ascii=False, sort_keys=True))
        return MemoryFileSyncReport(imported=imported, exported=exported, errors=self.errors)
