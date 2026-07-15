from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json, uuid
from typing import Any
import re

SCHEMA_VERSION = "runtime_session_state/v14.8.2.4"

@dataclass(slots=True)
class RuntimeSessionState:
    session_id: str
    created_at: str
    last_turn_at: str | None = None
    last_user_text: str | None = None
    last_intent: str | None = None
    last_route: str | None = None
    source_client: str = "unknown"
    expires_at: str | None = None
    schema_version: str = SCHEMA_VERSION
    def to_dict(self) -> dict[str, Any]: return asdict(self)

    @classmethod
    def create(cls, *, session_id: str | None = None, source_client: str = "unknown", ttl_seconds: int = 21600) -> "RuntimeSessionState":
        now = datetime.now(timezone.utc)
        return cls(session_id=session_id or str(uuid.uuid4()), created_at=now.isoformat(), source_client=source_client, expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat())

    def update(self, *, user_text: str, intent: str | None = None, route: str | None = None) -> None:
        self.last_turn_at = datetime.now(timezone.utc).isoformat()
        self.last_user_text = user_text
        self.last_intent = intent
        self.last_route = route

class RuntimeSessionStateStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "workspace_runtime" / "runtime_session_state.json"
        self.last_load_metadata: dict[str, Any] = {
            "session_loaded_from": "new",
            "session_reused": False,
            "session_resurrected_from_disk": False,
        }
        self.last_save_status: dict[str, Any] = {
            "session_state_saved": False,
            "session_state_path": str(self.path),
        }

    def _path_for_session(self, session_id: str | None) -> Path:
        if not session_id:
            return self.path
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id).strip("._") or "session"
        return self.root / "workspace_runtime" / "runtime_sessions" / f"{safe}.json"

    def load_or_create(self, session_id: str | None = None, *, source_client: str = "unknown", no_carryover: bool = False) -> RuntimeSessionState:
        requested_path = self._path_for_session(session_id)
        legacy_path = self.path
        self.path = requested_path
        self.last_load_metadata = {
            "session_loaded_from": "new",
            "session_reused": False,
            "session_resurrected_from_disk": False,
        }
        if not no_carryover:
            candidates = [requested_path]
            if session_id and legacy_path != requested_path:
                candidates.append(legacy_path)
            for candidate in candidates:
                if not candidate.exists():
                    continue
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                    if session_id is None or data.get("session_id") == session_id:
                        self.path = requested_path
                        self.last_load_metadata = {
                            "session_loaded_from": "workspace_runtime",
                            "session_reused": True,
                            "session_resurrected_from_disk": True,
                        }
                        return RuntimeSessionState(**{k: data.get(k) for k in RuntimeSessionState.__dataclass_fields__ if k in data})
                except Exception:
                    self.last_load_metadata = {
                        "session_loaded_from": "new",
                        "session_reused": False,
                        "session_resurrected_from_disk": False,
                        "load_error": True,
                    }
                    pass
        state = RuntimeSessionState.create(session_id=session_id, source_client=source_client)
        self.path = self._path_for_session(state.session_id)
        return state

    def save(self, state: RuntimeSessionState) -> dict[str, Any]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            self.last_save_status = {
                "session_state_saved": True,
                "session_state_path": str(self.path),
            }
        except OSError as exc:
            self.last_save_status = {
                "session_state_saved": False,
                "session_state_path": str(self.path),
                "session_state_save_error_type": exc.__class__.__name__,
                "session_state_save_error": str(exc),
                "truth_boundary": "Sesja działa w pamięci procesu, ale zapis stanu sesji nie został potwierdzony.",
            }
        return dict(self.last_save_status)
