from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, TextIO

from latka_jazn.config import JaznConfig
from latka_jazn.core.chat_command_contract import (
    CHATGPT_BRIDGE_PROTOCOL,
    CHATGPT_HOST_VISIBLE_REPLY_TEXT_FIELDS,
    chat_gpt_contract,
    persist_chatgpt_host_visible_reply,
    write_chat_bridge_payload,
)
from latka_jazn.version import schema_version
from latka_jazn.core.host_visible_finalization import finalize_host_visible_text

MAX_HOST_BRIDGE_JSON_BYTES = 2 * 1024 * 1024


class ChatgptHostBridgeHelperError(ValueError):
    """Raised for controlled host-bridge helper input errors."""


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _first_nonempty_text(payload: dict[str, Any], fields: Iterable[str]) -> tuple[str, str]:
    for field in fields:
        value = payload.get(field)
        text = _safe_text(value)
        if text:
            return text, field
    return "", "<missing>"


def iter_json_values_from_text(text: str) -> Iterable[dict[str, Any]]:
    """Yield JSON object values from a JSON document or JSONL text.

    JSON Lines is the bridge format: one UTF-8 JSON value per line.  The helper
    also accepts a single pretty-printed JSON object because users often save
    the phase-1 packet to a file from a terminal.
    """
    raw = (text or "").strip()
    if not raw:
        return
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                yield value
        return
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item


def read_limited_text(path: Path | str, *, max_bytes: int = MAX_HOST_BRIDGE_JSON_BYTES) -> str:
    path = Path(path)
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ChatgptHostBridgeHelperError(
            f"Plik {path} ma {len(data)} B, limit wejścia mostu to {max_bytes} B."
        )
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChatgptHostBridgeHelperError(
            f"Nieobsługiwane kodowanie pliku {path}. Użyj UTF-8 albo UTF-16 z BOM."
        ) from exc


def load_chatgpt_host_request_from_text(text: str) -> dict[str, Any]:
    """Select the phase-1 runtime packet that asks the ChatGPT host to speak."""
    first_object: dict[str, Any] | None = None
    for value in iter_json_values_from_text(text):
        first_object = first_object or value
        bridge = value.get("chatgpt_host_bridge") if isinstance(value.get("chatgpt_host_bridge"), dict) else value
        if not isinstance(bridge, dict):
            continue
        if bridge.get("phase") == "host_visible_generation_requested" or bridge.get("host_must_generate_visible_reply") is True:
            return value
    if first_object is not None:
        return first_object
    raise ChatgptHostBridgeHelperError("Nie znaleziono obiektu JSON z pakietem runtime/host bridge.")


def load_chatgpt_host_request(path: Path | str, *, max_bytes: int = MAX_HOST_BRIDGE_JSON_BYTES) -> dict[str, Any]:
    return load_chatgpt_host_request_from_text(read_limited_text(path, max_bytes=max_bytes))


def _host_bridge_from_runtime_packet(runtime_payload: dict[str, Any]) -> dict[str, Any]:
    bridge = runtime_payload.get("chatgpt_host_bridge")
    if isinstance(bridge, dict):
        return bridge
    if runtime_payload.get("phase") or runtime_payload.get("host_reply_jsonl_shape"):
        return runtime_payload
    return {}


def _trace_from_runtime_packet(runtime_payload: dict[str, Any]) -> dict[str, Any]:
    trace = runtime_payload.get("trace")
    return trace if isinstance(trace, dict) else {}


def build_chatgpt_host_visible_reply_payload(
    runtime_payload: dict[str, Any],
    *,
    final_text: str,
    state_emoticon: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Build a phase-2 host_visible_reply JSONL payload from a phase-1 packet.

    This avoids hand-copying turn_id/trace_id/timestamp_header in PowerShell and
    keeps the existing --chat-gpt truth boundary: the visible text is authored by
    the ChatGPT host and then recorded by the Jaźń runtime.
    """
    bridge = _host_bridge_from_runtime_packet(runtime_payload)
    shape = bridge.get("host_reply_jsonl_shape") if isinstance(bridge.get("host_reply_jsonl_shape"), dict) else {}
    trace = _trace_from_runtime_packet(runtime_payload)
    turn_contract = runtime_payload.get("runtime_turn_contract") if isinstance(runtime_payload.get("runtime_turn_contract"), dict) else {}
    final_contract = runtime_payload.get("final_response_contract") if isinstance(runtime_payload.get("final_response_contract"), dict) else {}

    turn_id = _safe_text(bridge.get("turn_id") or shape.get("turn_id") or trace.get("turn_id") or turn_contract.get("turn_id") or final_contract.get("turn_id"))
    trace_id = _safe_text(bridge.get("trace_id") or shape.get("trace_id") or trace.get("trace_id") or turn_contract.get("trace_id") or final_contract.get("trace_id"))
    timestamp_header = _safe_text(bridge.get("timestamp_header") or shape.get("timestamp_header") or trace.get("timestamp_header") or turn_contract.get("timestamp_header") or final_contract.get("timestamp_header"))
    text = _safe_text(final_text)

    missing: list[str] = []
    phase = _safe_text(bridge.get("phase"))
    if phase and phase != "host_visible_generation_requested" and bridge.get("host_must_generate_visible_reply") is not True:
        missing.append("chatgpt_host_bridge.phase=host_visible_generation_requested")
    if not turn_id:
        missing.append("turn_id")
    if not trace_id:
        missing.append("trace_id")
    if not timestamp_header:
        missing.append("timestamp_header")
    if not text:
        missing.append("final_text")
    if missing:
        return None, missing

    finalization = finalize_host_visible_text(
        required_timestamp_header=timestamp_header,
        turn_id=turn_id,
        trace_id=trace_id,
        text=text,
        supplied_turn_id=turn_id,
        supplied_trace_id=trace_id,
        max_utf8_bytes=MAX_HOST_BRIDGE_JSON_BYTES,
    )
    if not finalization.accepted:
        return None, [f"finalization:{item.code}" for item in finalization.violations]
    text = finalization.final_visible_text

    payload: dict[str, Any] = {
        "type": "host_visible_reply",
        "turn_id": turn_id,
        "trace_id": trace_id,
        "timestamp_header": timestamp_header,
        "final_text": text,
        "finalization_result": finalization.to_dict(),
        "builder": {
            "schema_version": schema_version("chatgpt_host_visible_reply_builder"),
            "source": "chatgpt_host_bridge_helper",
            "truth_boundary": "Ten JSONL nie jest lokalną generacją modelu. To widoczna odpowiedź hosta ChatGPT przygotowana do zapisu w runtime Jaźni.",
        },
    }
    if state_emoticon:
        payload["state_emoticon"] = state_emoticon
    return payload, []


def build_chatgpt_host_reply_helper_meta(*, line_index: int | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "protocol_version": CHATGPT_BRIDGE_PROTOCOL,
        "accepted_input_fields": list(CHATGPT_HOST_VISIBLE_REPLY_TEXT_FIELDS),
        "accepted_input_shapes": [
            "json_object.type=host_visible_reply",
            "runtime_packet.chatgpt_host_bridge.host_reply_jsonl_shape + final_text",
        ],
        "preferred_input_field": "final_text",
        "client": "chatgpt_host_bridge_helper",
        "lifecycle": "chatgpt_host_visible_reply_helper",
        "mode": "chatgpt_bridge_without_api_key",
        "command": "--chat-gpt",
        "requires_api_key": False,
        "uses_openai_api": False,
        "canonical_command": "--chat-gpt",
        "input_kind": "json_host_visible_reply_helper",
        "input_field": "final_text",
        "truth_boundary": "Helper buduje albo zapisuje host_visible_reply bez ręcznego składania JSON w shellu.",
    }
    if line_index is not None:
        meta["line_index"] = line_index
    return meta


def record_chatgpt_host_visible_reply_from_runtime_packet(
    *,
    config: JaznConfig,
    runtime_payload: dict[str, Any],
    final_text: str,
    state_emoticon: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    reply_payload, missing = build_chatgpt_host_visible_reply_payload(
        runtime_payload,
        final_text=final_text,
        state_emoticon=state_emoticon,
    )
    if missing:
        return None, missing
    return persist_chatgpt_host_visible_reply(
        config=config,
        payload=reply_payload or {},
        chat_bridge_meta=build_chatgpt_host_reply_helper_meta(line_index=1),
        contract=chat_gpt_contract().to_dict(),
    )


def _read_stdin_limited(stdin: TextIO, *, max_bytes: int = MAX_HOST_BRIDGE_JSON_BYTES) -> str:
    text = stdin.read(max_bytes + 1)
    if len(text.encode("utf-8")) > max_bytes:
        raise ChatgptHostBridgeHelperError(f"stdin przekroczył limit {max_bytes} B dla pakietu host bridge.")
    return text


def _resolve_final_text(args: argparse.Namespace) -> str:
    sources = [bool(args.final_text), bool(args.final_text_file), bool(args.message)]
    if sum(sources) != 1:
        raise ChatgptHostBridgeHelperError("Podaj dokładnie jedno źródło tekstu: --final-text, --final-text-file albo tekst po --.")
    if args.final_text:
        return args.final_text
    if args.final_text_file:
        return read_limited_text(args.final_text_file)
    message = list(args.message or [])
    if message and message[0] == "--":
        message = message[1:]
    return " ".join(message).strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chatgpt_host_bridge_reply.py",
        description="Build or record a --chat-gpt host_visible_reply from a phase-1 runtime JSONL packet.",
        allow_abbrev=False,
    )
    parser.add_argument("--root", type=Path, default=None, help="Folder główny aktywnego runtime Jaźni.")
    parser.add_argument("--from-runtime-json", type=Path, default=None, help="Plik z wynikiem fazy 1 --chat-gpt JSONL. Użyj '-' albo pomiń, aby czytać stdin.")
    parser.add_argument("--final-text", default=None, help="Widoczna odpowiedź hosta ChatGPT do zapisania w runtime.")
    parser.add_argument("--final-text-file", type=Path, default=None, help="Plik UTF-8 z widoczną odpowiedzią hosta ChatGPT.")
    parser.add_argument("--state-emoticon", default=None, help="Opcjonalna ikona stanu zapisywana przy final_visible_reply.")
    parser.add_argument("--build-only", action="store_true", help="Tylko zbuduj JSONL host_visible_reply; nie zapisuj do runtime.")
    parser.add_argument("--pretty", action="store_true", help="Wypisz JSON z wcięciami zamiast jednej linii JSONL.")
    parser.add_argument("message", nargs=argparse.REMAINDER, help="Alternatywnie: tekst hosta po --, bez ręcznego składania JSON.")
    return parser


def main(argv: list[str] | None = None, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        if args.from_runtime_json and str(args.from_runtime_json) != "-":
            runtime_payload = load_chatgpt_host_request(args.from_runtime_json)
        else:
            runtime_payload = load_chatgpt_host_request_from_text(_read_stdin_limited(stdin))
        final_text = _resolve_final_text(args)
        reply_payload, missing = build_chatgpt_host_visible_reply_payload(
            runtime_payload,
            final_text=final_text,
            state_emoticon=args.state_emoticon,
        )
        if missing:
            payload = {
                "schema_version": schema_version("chatgpt_host_visible_reply_helper_error"),
                "ok": False,
                "error_code": "invalid_host_visible_reply_request",
                "missing": missing,
                "error": "Brakuje pól do zbudowania host_visible_reply: " + ", ".join(missing),
            }
            write_chat_bridge_payload(stdout, payload)
            return 2
        if args.build_only:
            output = reply_payload or {}
        else:
            output, persist_missing = persist_chatgpt_host_visible_reply(
                config=JaznConfig(root=args.root) if args.root else JaznConfig(),
                payload=reply_payload or {},
                chat_bridge_meta=build_chatgpt_host_reply_helper_meta(line_index=1),
                contract=chat_gpt_contract().to_dict(),
            )
            if persist_missing:
                output = {
                    "schema_version": schema_version("chatgpt_host_visible_reply_helper_error"),
                    "ok": False,
                    "error_code": "invalid_host_visible_reply",
                    "missing": persist_missing,
                    "error": "Brakuje pól dla host_visible_reply: " + ", ".join(persist_missing),
                }
                write_chat_bridge_payload(stdout, output)
                return 2
        if args.pretty:
            stdout.write(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            stdout.flush()
        else:
            write_chat_bridge_payload(stdout, output or {})
        return 0
    except (OSError, json.JSONDecodeError, ChatgptHostBridgeHelperError) as exc:
        payload = {
            "schema_version": schema_version("chatgpt_host_visible_reply_helper_error"),
            "ok": False,
            "error_code": "host_visible_reply_helper_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        write_chat_bridge_payload(stdout, payload)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
