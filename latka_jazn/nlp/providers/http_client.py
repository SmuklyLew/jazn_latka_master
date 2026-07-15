from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any
import json, urllib.parse, urllib.request, urllib.error, time

@dataclass(slots=True)
class HttpResult:
    ok: bool
    status_code: int | None
    url: str
    retrieved_at_utc: str
    elapsed_ms: int
    text: str | None = None
    json_data: Any = None
    error: str | None = None
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class SafeHttpClient:
    def __init__(self, *, user_agent: str, timeout_seconds: float = 6.0, max_retries: int = 0):
        self.user_agent = user_agent
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
    def get_json(self, url: str, params: dict[str, Any] | None = None) -> HttpResult:
        if params:
            sep = '&' if '?' in url else '?'
            url = url + sep + urllib.parse.urlencode(params, doseq=True)
        start=time.time(); last_error=None
        for attempt in range(self.max_retries + 1):
            try:
                req=urllib.request.Request(url, headers={'User-Agent': self.user_agent, 'Accept':'application/json'})
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    raw=resp.read(1024*1024).decode('utf-8', errors='replace')
                    try: data=json.loads(raw)
                    except Exception: data=None
                    return HttpResult(True, getattr(resp,'status',None), url, datetime.now(timezone.utc).isoformat(), int((time.time()-start)*1000), raw, data)
            except Exception as exc:
                last_error=repr(exc)
        return HttpResult(False, None, url, datetime.now(timezone.utc).isoformat(), int((time.time()-start)*1000), error=last_error)
