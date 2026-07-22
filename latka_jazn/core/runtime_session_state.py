from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
import hashlib
import json
import os
import re
import uuid

from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("runtime_session_state")
CONTINUITY_SCHEMA_VERSION = schema_version("runtime_session_continuity")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fsync_directory(path: Path) -> None:
    """Best-effort directory sync after an atomic replacement.

    Windows does not expose a portable directory fsync through Python, while
    POSIX systems do. Failing this durability enhancement must not turn a
    successfully replaced checkpoint into a false failure.
    """
    flags = getattr(os, "O_RDONLY", 0)
    directory_fd: int | None = None
    try:
        directory_fd = os.open(path, flags)
        os.fsync(directory_fd)
    except (AttributeError, OSError):
        return
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        session_id: str | None = None,
        source_client: str = "unknown",
        ttl_seconds: int = 21600,
    ) -> "RuntimeSessionState":
        now = datetime.now(timezone.utc)
        return cls(
            session_id=session_id or str(uuid.uuid4()),
            created_at=now.isoformat(),
            source_client=source_client,
            expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
        )

    def update(self, *, user_text: str, intent: str | None = None, route: str | None = None) -> None:
        self.last_turn_at = datetime.now(timezone.utc).isoformat()
        self.last_user_text = user_text
        self.last_intent = intent
        self.last_route = route

    def clear_carryover(self) -> None:
        self.last_turn_at = None
        self.last_user_text = None
        self.last_intent = None
        self.last_route = None


class RuntimeSessionStateStore:
    """Atomic, hash-bound persistence for runtime conversation continuity.

    The per-session checkpoint is the canonical record. A second atomically
    replaced pointer at ``workspace_runtime/runtime_session_state.json`` keeps
    the newest eligible session discoverable after a process restart when the
    caller does not provide ``--session-id``.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.latest_path = self.root / "workspace_runtime" / "runtime_session_state.json"
        self.path = self.latest_path
        self.carryover_enabled = True
        self.loaded_continuity: dict[str, Any] | None = None
        self.last_load_metadata: dict[str, Any] = self._new_load_metadata()
        self.last_save_status: dict[str, Any] = {
            "session_state_saved": False,
            "session_state_path": str(self.path),
            "latest_session_pointer_saved": False,
            "continuity_checkpoint_written": False,
        }

    @staticmethod
    def _new_load_metadata() -> dict[str, Any]:
        return {
            "session_loaded_from": "new",
            "session_reused": False,
            "session_resurrected_from_disk": False,
            "restart_continuity_verified": False,
            "restart_continuity_status": "new_session",
            "session_carryover_blocked": False,
        }

    def _path_for_session(self, session_id: str | None) -> Path:
        if not session_id:
            return self.latest_path
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id).strip("._") or "session"
        return self.root / "workspace_runtime" / "runtime_sessions" / f"{safe}.json"

    @staticmethod
    def _state_from_payload(payload: Mapping[str, Any]) -> RuntimeSessionState:
        fields = RuntimeSessionState.__dataclass_fields__
        values = {name: payload.get(name) for name in fields if name in payload}
        return RuntimeSessionState(**values)

    @staticmethod
    def _checkpoint_hash_material(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in checkpoint.items() if key != "checkpoint_sha256"}

    def _validate_payload(self, payload: Any) -> tuple[RuntimeSessionState | None, dict[str, Any] | None, str | None]:
        if not isinstance(payload, dict):
            return None, None, "session_state_not_object"
        try:
            state = self._state_from_payload(payload)
        except (TypeError, ValueError) as exc:
            return None, None, f"session_state_schema_error:{type(exc).__name__}"
        if not state.session_id or not state.created_at:
            return None, None, "session_state_required_fields_missing"

        checkpoint = payload.get("_continuity")
        if checkpoint is None:
            return state, None, None
        if not isinstance(checkpoint, dict):
            return None, None, "continuity_checkpoint_not_object"
        state_sha = _sha256_json(state.to_dict())
        if str(checkpoint.get("state_sha256") or "") != state_sha:
            return None, checkpoint, "continuity_state_hash_mismatch"
        stored_checkpoint_sha = str(checkpoint.get("checkpoint_sha256") or "")
        computed_checkpoint_sha = _sha256_json(self._checkpoint_hash_material(checkpoint))
        if not stored_checkpoint_sha or stored_checkpoint_sha != computed_checkpoint_sha:
            return None, checkpoint, "continuity_checkpoint_hash_mismatch"
        if str(checkpoint.get("session_id") or "") != state.session_id:
            return None, checkpoint, "continuity_session_id_mismatch"
        return state, dict(checkpoint), None

    def _load_path(self, path: Path) -> tuple[RuntimeSessionState | None, dict[str, Any] | None, str | None]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return None, None, f"session_state_read_error:{type(exc).__name__}"
        return self._validate_payload(payload)

    @staticmethod
    def _is_expired(state: RuntimeSessionState) -> bool:
        expires = _parse_utc(state.expires_at)
        return bool(expires is not None and datetime.now(timezone.utc) >= expires)

    def load_or_create(
        self,
        session_id: str | None = None,
        *,
        source_client: str = "unknown",
        no_carryover: bool = False,
    ) -> RuntimeSessionState:
        requested_path = self._path_for_session(session_id)
        self.path = requested_path
        self.carryover_enabled = not no_carryover
        self.loaded_continuity = None
        self.last_load_metadata = self._new_load_metadata()

        errors: list[dict[str, str]] = []
        if not no_carryover:
            candidates = [requested_path]
            if session_id and self.latest_path != requested_path:
                candidates.append(self.latest_path)
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                state, checkpoint, error = self._load_path(candidate)
                if error is not None or state is None:
                    errors.append({"path": str(candidate), "error": error or "unknown_load_error"})
                    continue
                if session_id is not None and state.session_id != session_id:
                    errors.append({"path": str(candidate), "error": "requested_session_id_mismatch"})
                    continue
                if self._is_expired(state):
                    errors.append({"path": str(candidate), "error": "session_state_expired"})
                    continue
                self.path = self._path_for_session(state.session_id)
                self.loaded_continuity = checkpoint
                self.last_load_metadata = {
                    "session_loaded_from": (
                        "latest_session_pointer" if candidate == self.latest_path else "session_checkpoint"
                    ),
                    "session_state_source_path": str(candidate),
                    "session_reused": True,
                    "session_resurrected_from_disk": True,
                    "restart_continuity_verified": False,
                    "restart_continuity_status": (
                        "checkpoint_integrity_valid" if checkpoint is not None else "legacy_checkpoint_unverified"
                    ),
                    "session_carryover_blocked": False,
                    "continuity_checkpoint_present": checkpoint is not None,
                    "continuity_generation": int((checkpoint or {}).get("generation") or 0),
                    "continuity_turn_count": int((checkpoint or {}).get("turn_count") or 0),
                }
                if errors:
                    self.last_load_metadata["ignored_checkpoint_errors"] = errors
                return state

        if errors:
            self.last_load_metadata.update(
                restart_continuity_status="checkpoint_rejected",
                session_carryover_blocked=True,
                ignored_checkpoint_errors=errors,
            )
        state = RuntimeSessionState.create(session_id=session_id, source_client=source_client)
        self.path = self._path_for_session(state.session_id)
        return state

    @staticmethod
    def _wake_context(status: Any) -> dict[str, Any]:
        if status is None:
            return {}
        if hasattr(status, "to_dict") and callable(status.to_dict):
            raw = status.to_dict()
        elif isinstance(status, Mapping):
            raw = dict(status)
        else:
            return {}
        return {
            "status": raw.get("status"),
            "ok": bool(raw.get("ok")),
            "snapshot_id": raw.get("snapshot_id"),
            "snapshot_sha256": raw.get("snapshot_sha256"),
            "source_run_id": raw.get("source_run_id"),
            "validation_status": raw.get("validation_status"),
        }

    def verify_loaded_continuity(self, state: RuntimeSessionState, wake_state_status: Any) -> dict[str, Any]:
        if not self.last_load_metadata.get("session_resurrected_from_disk"):
            status = {
                "status": "new_session",
                "verified": False,
                "carryover_allowed": True,
                "checkpoint_present": False,
            }
            self.last_load_metadata.update(
                restart_continuity_status=status["status"],
                restart_continuity_verified=False,
            )
            return status

        checkpoint = self.loaded_continuity
        if checkpoint is None:
            status = {
                "status": "legacy_checkpoint_unverified",
                "verified": False,
                "carryover_allowed": True,
                "checkpoint_present": False,
                "truth_boundary": (
                    "Legacy session state was loaded for compatibility, but it is not hash-bound to wake-state. "
                    "The next successful save upgrades it to a verified checkpoint."
                ),
            }
            self.last_load_metadata.update(
                restart_continuity_status=status["status"],
                restart_continuity_verified=False,
            )
            return status

        current_wake = self._wake_context(wake_state_status)
        stored_wake = checkpoint.get("wake_state") if isinstance(checkpoint.get("wake_state"), dict) else {}
        stored_id = str(stored_wake.get("snapshot_id") or "")
        stored_sha = str(stored_wake.get("snapshot_sha256") or "")
        current_id = str(current_wake.get("snapshot_id") or "")
        current_sha = str(current_wake.get("snapshot_sha256") or "")
        wake_matches = bool(
            stored_wake.get("ok") is True
            and current_wake.get("ok") is True
            and stored_id
            and stored_sha
            and stored_id == current_id
            and stored_sha == current_sha
        )
        if wake_matches:
            status = {
                "status": "verified",
                "verified": True,
                "carryover_allowed": True,
                "checkpoint_present": True,
                "generation": int(checkpoint.get("generation") or 0),
                "checkpoint_sha256": checkpoint.get("checkpoint_sha256"),
                "wake_snapshot_id": current_id,
                "wake_snapshot_sha256": current_sha,
            }
            self.last_load_metadata.update(
                restart_continuity_status="verified",
                restart_continuity_verified=True,
                continuity_checkpoint_sha256=checkpoint.get("checkpoint_sha256"),
                continuity_generation=int(checkpoint.get("generation") or 0),
            )
            return status

        state.clear_carryover()
        status = {
            "status": "wake_state_binding_mismatch",
            "verified": False,
            "carryover_allowed": False,
            "checkpoint_present": True,
            "stored_wake_snapshot_id": stored_id or None,
            "current_wake_snapshot_id": current_id or None,
            "stored_wake_snapshot_sha256": stored_sha or None,
            "current_wake_snapshot_sha256": current_sha or None,
            "truth_boundary": (
                "The persisted conversation checkpoint is not bound to the currently verified wake-state. "
                "Previous user text, route and intent were cleared instead of being replayed across restart."
            ),
        }
        self.last_load_metadata.update(
            session_reused=False,
            restart_continuity_status=status["status"],
            restart_continuity_verified=False,
            session_carryover_blocked=True,
            continuity_blocked_reason="wake_state_binding_mismatch",
        )
        return status

    def _previous_checkpoint(self, path: Path, session_id: str) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        state, checkpoint, error = self._load_path(path)
        if error is None and state is not None and state.session_id == session_id and checkpoint is not None:
            return checkpoint
        return None

    def _build_payload(
        self,
        state: RuntimeSessionState,
        *,
        continuity_context: Any = None,
        turn_count: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        state_payload = state.to_dict()
        previous = self._previous_checkpoint(self.path, state.session_id)
        generation = int((previous or {}).get("generation") or 0) + 1
        checkpoint: dict[str, Any] = {
            "schema_version": CONTINUITY_SCHEMA_VERSION,
            "session_id": state.session_id,
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            "generation": generation,
            "turn_count": max(0, int(turn_count or 0)),
            "state_sha256": _sha256_json(state_payload),
            "previous_checkpoint_sha256": (previous or {}).get("checkpoint_sha256"),
            "wake_state": self._wake_context(continuity_context),
            "truth_boundary": (
                "This checkpoint proves file integrity and binding to a verified wake-state snapshot. "
                "It does not prove that every historical memory was recovered or that a process remained alive."
            ),
        }
        checkpoint["checkpoint_sha256"] = _sha256_json(self._checkpoint_hash_material(checkpoint))
        return {**state_payload, "_continuity": checkpoint}, checkpoint

    def save(
        self,
        state: RuntimeSessionState,
        *,
        continuity_context: Any = None,
        turn_count: int | None = None,
    ) -> dict[str, Any]:
        primary = self._path_for_session(state.session_id)
        self.path = primary
        payload, checkpoint = self._build_payload(
            state,
            continuity_context=continuity_context,
            turn_count=turn_count,
        )
        primary_saved = False
        latest_saved = False
        errors: list[str] = []
        try:
            _atomic_write_json(primary, payload)
            primary_saved = True
        except OSError as exc:
            errors.append(f"primary:{type(exc).__name__}:{exc}")

        if primary_saved and self.carryover_enabled:
            try:
                _atomic_write_json(self.latest_path, payload)
                latest_saved = True
            except OSError as exc:
                errors.append(f"latest:{type(exc).__name__}:{exc}")

        saved = primary_saved and (latest_saved or not self.carryover_enabled)
        self.last_save_status = {
            "session_state_saved": saved,
            "session_state_path": str(primary),
            "latest_session_pointer_path": str(self.latest_path),
            "latest_session_pointer_saved": latest_saved,
            "latest_session_pointer_required": self.carryover_enabled,
            "continuity_checkpoint_written": primary_saved,
            "continuity_checkpoint_sha256": checkpoint.get("checkpoint_sha256") if primary_saved else None,
            "continuity_generation": checkpoint.get("generation") if primary_saved else None,
            "continuity_wake_snapshot_id": (checkpoint.get("wake_state") or {}).get("snapshot_id"),
            "continuity_wake_snapshot_sha256": (checkpoint.get("wake_state") or {}).get("snapshot_sha256"),
            "atomic_replace_used": True,
        }
        if errors:
            self.last_save_status.update(
                session_state_save_error_type="CheckpointWriteError",
                session_state_save_error="; ".join(errors),
                truth_boundary=(
                    "The runtime session may continue in process memory, but durable restart continuity "
                    "was not fully confirmed."
                ),
            )
        return dict(self.last_save_status)
