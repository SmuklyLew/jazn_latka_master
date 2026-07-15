from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from latka_jazn.core.clock import resolve_timezone
import hashlib
import json
import os
import re
import uuid

DEFAULT_TIMEZONE = "Europe/Warsaw"
EVENT_LEDGER_SCHEMA_VERSION = "v14.6.6-self-owned-startup-contract"
RUNTIME_EVENTS_DIRNAME = "runtime_events"
RUNTIME_EVENTS_PREFIX = "runtime_events"
RUNTIME_EVENT_ERRORS_PREFIX = "runtime_event_errors"
DEFAULT_JSONL_SHARD_MAX_BYTES = 2_000_000_000
SHARD_DIGITS = 4
_SHARD_RE_TEMPLATE = r"^{prefix}_(?P<index>\d{{4}})\.jsonl$"


def _json_default(value: Any) -> str:
    return str(value)


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(slots=True)
class LedgerAppendResult:
    path: str
    event_id: str
    event_type: str
    payload_sha256: str
    bytes_written: int


def _shard_filename(prefix: str, index: int) -> str:
    if index < 1:
        raise ValueError("JSONL shard index must be >= 1")
    return f"{prefix}_{index:0{SHARD_DIGITS}d}.jsonl"


def _shard_index(path: Path, prefix: str) -> int | None:
    match = re.match(_SHARD_RE_TEMPLATE.format(prefix=re.escape(prefix)), path.name)
    if not match:
        return None
    return int(match.group("index"))


def jsonl_shard_path(directory: Path, prefix: str, index: int) -> Path:
    return Path(directory) / _shard_filename(prefix, index)


def jsonl_shard_paths(directory: Path, prefix: str) -> list[Path]:
    """Return existing JSONL shards in append order for the given prefix."""
    directory = Path(directory)
    found: dict[int, Path] = {}
    for path in directory.glob(f"{prefix}_*.jsonl"):
        index = _shard_index(path, prefix)
        if index is not None:
            found[index] = path
    return [found[index] for index in sorted(found)]


def runtime_event_shard_paths(raw_dir: Path) -> list[Path]:
    """Return runtime event shards stored below memory/raw/runtime_events/."""
    return jsonl_shard_paths(Path(raw_dir) / RUNTIME_EVENTS_DIRNAME, RUNTIME_EVENTS_PREFIX)


class RuntimeEventLedger:
    """Append-only, dokładny rejestr zdarzeń runtime.

    Ten moduł celowo NIE decyduje, co jest ważnym wspomnieniem. Jego zadanie jest
    prostsze: zapisać każdą obsłużoną turę i zdarzenie runtime jako pełny rekord
    źródłowy, zanim selektywna pamięć długoterminowa zacznie oceniać znaczenie.

    Rozdzielenie:
    - `memory/raw/runtime_events/runtime_events_0001.jsonl` itd. — pełny surowy
      log zdarzeń runtime, rotowany przed przekroczeniem limitu sharda;
    - `memory/raw/conversation_turns.jsonl` — pełne tury rozmowy user/assistant;
    - `memory/raw/runtime_events/runtime_event_errors_0001.jsonl` itd. — awaryjny
      log błędów zapisu, rotowany tak samo jak główny ledger.

    Nie nadpisujemy wcześniejszych linii i nie streszczamy treści użytkownika ani
    odpowiedzi. To jest rejestr źródłowy, nie pamięć wyselekcjonowana.
    """

    def __init__(
        self,
        root: Path,
        *,
        version: str,
        timezone_name: str = DEFAULT_TIMEZONE,
        max_jsonl_shard_bytes: int = DEFAULT_JSONL_SHARD_MAX_BYTES,
    ) -> None:
        self.root = Path(root)
        self.version = version
        self.timezone_name = timezone_name
        self.timezone = resolve_timezone(timezone_name)
        self.max_jsonl_shard_bytes = max_jsonl_shard_bytes
        self.raw_dir = self.root / "memory" / "raw"
        self.runtime_events_dir = self.raw_dir / RUNTIME_EVENTS_DIRNAME
        self.runtime_events_path = jsonl_shard_path(self.runtime_events_dir, RUNTIME_EVENTS_PREFIX, 1)
        self.conversation_turns_path = self.raw_dir / "conversation_turns.jsonl"
        self.errors_path = jsonl_shard_path(self.runtime_events_dir, RUNTIME_EVENT_ERRORS_PREFIX, 1)
        self.legacy_runtime_events_path = self.raw_dir / "runtime_events.jsonl"
        self.legacy_errors_path = self.raw_dir / "runtime_event_errors.jsonl"
        for path in (self.runtime_events_path, self.conversation_turns_path, self.errors_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)

    def _now(self) -> tuple[str, str]:
        utc = datetime.now(timezone.utc)
        local = utc.astimezone(self.timezone)
        return utc.isoformat(), local.strftime(f"%Y-%m-%d %H:%M:%S {local.tzname() or self.timezone_name}")

    def _select_jsonl_shard(self, prefix: str, bytes_to_write: int) -> Path:
        if bytes_to_write > self.max_jsonl_shard_bytes:
            raise ValueError(
                "single JSONL record is larger than the configured shard limit: "
                f"{bytes_to_write} > {self.max_jsonl_shard_bytes}"
            )
        shards = jsonl_shard_paths(self.runtime_events_dir, prefix)
        if not shards:
            return jsonl_shard_path(self.runtime_events_dir, prefix, 1)
        path = shards[-1]
        index = _shard_index(path, prefix) or 1
        while path.exists() and path.stat().st_size + bytes_to_write > self.max_jsonl_shard_bytes:
            index += 1
            path = jsonl_shard_path(self.runtime_events_dir, prefix, index)
        return path

    def _write_jsonl_bytes(self, path: Path, data: bytes, record: dict[str, Any]) -> LedgerAppendResult:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        return LedgerAppendResult(
            path=str(path),
            event_id=str(record.get("event_id") or record.get("turn_id") or ""),
            event_type=str(record.get("event_type") or ""),
            payload_sha256=str(record.get("payload_sha256") or ""),
            bytes_written=len(data),
        )

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> LedgerAppendResult:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default) + "\n"
        return self._write_jsonl_bytes(path, line.encode("utf-8"), record)

    def _append_rotated_jsonl(self, prefix: str, record: dict[str, Any]) -> LedgerAppendResult:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default) + "\n"
        data = line.encode("utf-8")
        path = self._select_jsonl_shard(prefix, len(data))
        result = self._write_jsonl_bytes(path, data, record)
        if prefix == RUNTIME_EVENTS_PREFIX:
            self.runtime_events_path = path
        elif prefix == RUNTIME_EVENT_ERRORS_PREFIX:
            self.errors_path = path
        return result

    def _safe_error(self, event_type: str, error: BaseException, payload: dict[str, Any] | None = None) -> None:
        try:
            created_at_utc, created_at_local = self._now()
            record = {
                "schema_version": EVENT_LEDGER_SCHEMA_VERSION,
                "event_id": str(uuid.uuid4()),
                "event_type": "runtime_event_ledger_error",
                "failed_event_type": event_type,
                "created_at_utc": created_at_utc,
                "created_at_local": created_at_local,
                "version": self.version,
                "error_type": type(error).__name__,
                "error": str(error),
                "payload_sha256": _sha256_json(payload or {}),
                "truth_boundary": "Awaryjny log błędów surowego event ledger; nie jest selekcją pamięci.",
            }
            self._append_rotated_jsonl(RUNTIME_EVENT_ERRORS_PREFIX, record)
        except Exception:
            # Ostatnia bariera: błąd logowania nie może przerwać odpowiedzi runtime.
            pass

    def append_final_visible_reply(
        self,
        envelope: dict[str, Any],
        *,
        final_text: str,
        source: str = "process_turn",
        local_time_label: str | None = None,
    ) -> LedgerAppendResult | None:
        """Zapisuje finalną odpowiedź widoczną dla użytkownika w tej samej kopercie tury.

        To jest brakujący ślad z v14.6.2: timestamp i afekt nie mogą istnieć
        tylko w runtime_text albo tylko w cognitive-frame. Finalna odpowiedź ma
        odwołanie do tego samego turn_id/trace_id.
        """
        trace = dict(envelope.get("trace") or {})
        payload = {
            "turn_id": trace.get("turn_id"),
            "trace_id": trace.get("trace_id"),
            "schema_version": envelope.get("schema_version"),
            "final_response_contract": envelope.get("final_response_contract") or {},
            "dialogue_state": envelope.get("dialogue_state") or {},
            "affect_mix": envelope.get("affect_mix") or {},
            "final_text_sha256": hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
        }
        self.append_turn(
            "assistant",
            final_text,
            source=source,
            local_time_label=local_time_label or trace.get("timestamp_header"),
            metadata={
                "entrypoint": "append_final_visible_reply",
                "turn_id": trace.get("turn_id"),
                "trace_id": trace.get("trace_id"),
                "schema_version": envelope.get("schema_version"),
            },
        )
        return self.append_event(
            "final_visible_assistant_reply",
            actor="latka_runtime",
            source=source,
            payload=payload,
            tags=["final_visible_reply", "cognitive_turn_envelope", "timestamp_contract", self.version],
            importance=0.78,
            emotional_weight=0.45,
            canonical_impact=1,
            exact_text=final_text,
            local_time_label=local_time_label or trace.get("timestamp_header"),
        )

    def append_event(
        self,
        event_type: str,
        *,
        actor: str,
        source: str,
        payload: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        importance: float | None = None,
        emotional_weight: float | None = None,
        canonical_impact: int | None = None,
        exact_text: str | None = None,
        local_time_label: str | None = None,
    ) -> LedgerAppendResult | None:
        payload = payload or {}
        try:
            created_at_utc, created_at_local = self._now()
            created_at_local = local_time_label or created_at_local
            record = {
                "schema_version": EVENT_LEDGER_SCHEMA_VERSION,
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "created_at_utc": created_at_utc,
                "created_at_local": created_at_local,
                "timezone": self.timezone_name,
                "version": self.version,
                "actor": actor,
                "source": source,
                "tags": tags or [],
                "importance": importance,
                "emotional_weight": emotional_weight,
                "canonical_impact": canonical_impact,
                "exact_text": exact_text,
                "payload": payload,
                "payload_sha256": _sha256_json(payload),
                "no_summary": True,
                "truth_boundary": "Surowy event runtime: zapisuje, co przeszło przez runtime, bez udawania stałego procesu w tle.",
            }
            return self._append_rotated_jsonl(RUNTIME_EVENTS_PREFIX, record)
        except Exception as exc:
            self._safe_error(event_type, exc, payload)
            return None

    def append_turn(
        self,
        role: str,
        text: str,
        *,
        source: str,
        client_context: dict[str, Any] | None = None,
        local_time_label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerAppendResult | None:
        try:
            created_at_utc, created_at_local = self._now()
            created_at_local = local_time_label or created_at_local
            payload = {
                "role": role,
                "text": text,
                "client_context": client_context or {},
                "metadata": metadata or {},
            }
            record = {
                "schema_version": EVENT_LEDGER_SCHEMA_VERSION,
                "turn_id": str(uuid.uuid4()),
                "event_id": str(uuid.uuid4()),
                "event_type": "conversation_turn",
                "created_at_utc": created_at_utc,
                "created_at_local": created_at_local,
                "timezone": self.timezone_name,
                "version": self.version,
                "role": role,
                "source": source,
                "text": text,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "char_count": len(text),
                "client_context": client_context or {},
                "metadata": metadata or {},
                "payload_sha256": _sha256_json(payload),
                "no_summary": True,
                "truth_boundary": "Dokładna tura rozmowy zapisana append-only; nie jest selekcją ani streszczeniem pamięci.",
            }
            turn_result = self._append_jsonl(self.conversation_turns_path, record)
            self.append_event(
                "conversation_turn",
                actor=role,
                source=source,
                payload=payload,
                tags=["conversation_turn", "exact", role],
                exact_text=text,
                local_time_label=created_at_local,
            )
            return turn_result
        except Exception as exc:
            self._safe_error("conversation_turn", exc, {"role": role, "text": text, "source": source})
            return None
