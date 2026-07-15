from __future__ import annotations
from pathlib import Path
from typing import Any
from contextlib import closing
import sqlite3, json, hashlib, time
from datetime import datetime, timezone

SCHEMA_VERSION="network_dictionary_cache/v14.6.10"

class NetworkDictionaryCache:
    def __init__(self, root: Path, ttl_seconds: int = 604800):
        self.path = Path(root) / 'workspace_runtime' / 'dictionary_cache.sqlite3'
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path)

    def close(self) -> None:
        """Compatibility hook for engine shutdown.

        The cache does not keep a persistent connection, but every SQLite
        operation must close its temporary connection explicitly so Windows can
        remove TemporaryDirectory test roots without WinError 32.
        """
        return None

    def _init(self):
        with closing(self._connect()) as con:
            con.execute('CREATE TABLE IF NOT EXISTS dictionary_entries (term TEXT, lang TEXT, source TEXT, raw_result_json TEXT, normalized_result_json TEXT, retrieved_at_utc TEXT, expires_at_utc TEXT, license_note TEXT, confidence REAL, sha256 TEXT, PRIMARY KEY(term, lang, source))')
            con.execute('CREATE TABLE IF NOT EXISTS lookup_events (id INTEGER PRIMARY KEY AUTOINCREMENT, term TEXT, lang TEXT, source TEXT, status TEXT, created_at_utc TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS source_license_notes (source TEXT PRIMARY KEY, license_note TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS failed_lookups (term TEXT, lang TEXT, source TEXT, reason TEXT, created_at_utc TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS language_resource_config (source TEXT PRIMARY KEY, enabled INTEGER, policy_json TEXT)')
            con.commit()

    def _now(self): return datetime.now(timezone.utc).isoformat()

    def get(self, term:str, lang:str, source:str)->dict[str,Any]|None:
        with closing(self._connect()) as con:
            row=con.execute('SELECT normalized_result_json, expires_at_utc FROM dictionary_entries WHERE term=? AND lang=? AND source=?',(term,lang,source)).fetchone()
        if not row: return None
        expires=row[1]
        if expires:
            try:
                if datetime.fromisoformat(expires) < datetime.now(timezone.utc):
                    return None
            except Exception:
                pass
        return json.loads(row[0]) if row[0] else None

    def get_any(self, term:str, lang:str, preferred_sources: list[str] | tuple[str,...] | None = None)->dict[str,Any]|None:
        order=list(preferred_sources or [])
        with closing(self._connect()) as con:
            rows=con.execute('SELECT source, normalized_result_json, expires_at_utc FROM dictionary_entries WHERE term=? AND lang=?',(term,lang)).fetchall()
        if not rows: return None
        def key(row):
            try: return order.index(row[0])
            except Exception: return len(order)+1
        for source,payload,expires in sorted(rows, key=key):
            if expires:
                try:
                    if datetime.fromisoformat(expires) < datetime.now(timezone.utc): continue
                except Exception: pass
            try:
                data=json.loads(payload)
                data.setdefault('cache_status','hit')
                data.setdefault('source_name',source)
                return data
            except Exception:
                continue
        return None

    def put(self, term:str, lang:str, source:str, normalized:dict[str,Any], *, raw:dict[str,Any]|None=None, license_note:str='', confidence:float=0.0, ttl_seconds:int|None=None):
        payload=json.dumps(normalized, ensure_ascii=False, sort_keys=True); sha=hashlib.sha256(payload.encode('utf-8')).hexdigest(); now=self._now()
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        expires = datetime.fromtimestamp(time.time()+ttl, timezone.utc).isoformat() if ttl and ttl>0 else None
        with closing(self._connect()) as con:
            con.execute('REPLACE INTO dictionary_entries VALUES (?,?,?,?,?,?,?,?,?,?)',(term,lang,source,json.dumps(raw or {}, ensure_ascii=False),payload,now,expires,license_note,confidence,sha))
            con.execute('INSERT INTO lookup_events(term,lang,source,status,created_at_utc) VALUES (?,?,?,?,?)',(term,lang,source,'put',now))
            con.commit()
    def log_failure(self, term:str, lang:str, source:str, reason:str):
         with closing(self._connect()) as con:
            con.execute('INSERT INTO failed_lookups(term,lang,source,reason,created_at_utc) VALUES (?,?,?,?,?)',(term,lang,source,reason,self._now()))
            con.execute('INSERT INTO lookup_events(term,lang,source,status,created_at_utc) VALUES (?,?,?,?,?)',(term,lang,source,'failure',self._now()))
            con.commit()
