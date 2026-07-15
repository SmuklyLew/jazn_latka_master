from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("openai_state_tracker")


@dataclass(slots=True)
class OpenAIConversationState:
    session_id: str
    previous_response_id: str | None = None
    last_response_id: str | None = None
    conversation_id: str | None = None
    store_policy: bool = False
    updated_at_utc: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OpenAIStateTracker:
    """Mały, jawny sidecar ciągłości OpenAI Responses API.

    To nie jest dowód uruchomionej Jaźni. To tylko zapis identyfikatorów API
    potrzebnych do wieloturowości modelu językowego, oddzielony od pamięci runtime.
    """

    def __init__(self, root: Path, *, filename: str = "workspace_runtime/openai_response_state.json") -> None:
        self.root = Path(root)
        self.path = self.root / filename
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, session_id: str) -> OpenAIConversationState:
        payload = self._read_all()
        entry = payload.get(session_id) if isinstance(payload.get(session_id), dict) else {}
        return OpenAIConversationState(
            session_id=session_id,
            previous_response_id=entry.get("previous_response_id") or entry.get("last_response_id"),
            last_response_id=entry.get("last_response_id"),
            conversation_id=entry.get("conversation_id"),
            store_policy=bool(entry.get("store_policy", False)),
            updated_at_utc=entry.get("updated_at_utc"),
        )

    def update_from_response(
        self,
        *,
        session_id: str,
        response: dict[str, Any],
        store_policy: bool = False,
        conversation_id: str | None = None,
    ) -> OpenAIConversationState:
        response_id = str(response.get("id") or "").strip() or None
        prior = self.load(session_id)
        state = OpenAIConversationState(
            session_id=session_id,
            previous_response_id=response_id or prior.previous_response_id,
            last_response_id=response_id or prior.last_response_id,
            conversation_id=conversation_id or prior.conversation_id,
            store_policy=bool(store_policy),
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        payload = self._read_all()
        payload[session_id] = state.to_dict()
        self._write_all(payload)
        return state

    def _read_all(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_all(self, payload: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
