from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from latka_jazn.cli_commands import audit as audit_commands
from latka_jazn.cli_commands import diagnostics, export as export_commands, host as host_commands, lifecycle
from latka_jazn.version import PACKAGE_VERSION_FULL


class StableArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        raise SystemExit(2)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", dest="as_json")


def build_parser() -> argparse.ArgumentParser:
    parser = StableArgumentParser(prog="run.py", description="Canonical Jaźń v15 operator CLI", allow_abbrev=False)
    parser.add_argument("--version", action="version", version=PACKAGE_VERSION_FULL)
    sub = parser.add_subparsers(dest="command")

    for name in ("status", "doctor", "bridge-discovery", "self-test", "package-smoke"):
        child = sub.add_parser(name, allow_abbrev=False)
        _add_common(child)

    for name in ("start", "stop", "restart"):
        child = sub.add_parser(name, allow_abbrev=False)
        _add_common(child)
        child.add_argument("remainder", nargs=argparse.REMAINDER)

    for name in ("chat", "chat-gpt"):
        child = sub.add_parser(name, allow_abbrev=False)
        _add_common(child)
        child.add_argument("remainder", nargs=argparse.REMAINDER)

    child = sub.add_parser("host-finalize", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--timestamp-header", required=True)
    child.add_argument("--turn-id", required=True)
    child.add_argument("--trace-id", required=True)
    source = child.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", default="")
    source.add_argument("--text-file", type=Path)
    child.add_argument("--supplied-turn-id")
    child.add_argument("--supplied-trace-id")
    child.add_argument("--max-bytes", type=int, default=2 * 1024 * 1024)

    child = sub.add_parser("audit-tail", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--limit", type=int, default=20)

    child = sub.add_parser("explain-turn", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--turn-id", required=True)
    child.add_argument("--trace-id")

    child = sub.add_parser("replay-turn", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--turn-id", required=True)
    child.add_argument("--trace-id")
    child.add_argument("--dry-run", action="store_true", default=True)

    child = sub.add_parser("export", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--profile", choices=("system", "memory", "full", "nlp", "source-safe"), required=True)
    child.add_argument("--output", type=Path)
    child.add_argument("--preview", action="store_true")
    child.add_argument("--confirm-private-data")

    return parser


def _emit(payload: Any, *, as_json: bool) -> None:
    if as_json or not isinstance(payload, str):
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print(payload)


def _legacy_main(args: list[str]) -> int:
    from main import main as legacy_main
    return int(legacy_main(args))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    known = {
        "status", "doctor", "start", "stop", "restart", "chat", "chat-gpt",
        "host-finalize", "bridge-discovery", "audit-tail", "explain-turn",
        "replay-turn", "export", "package-smoke", "self-test",
    }
    if args and args[0].startswith("--") and args[0] not in {"--version", "--help", "-h"}:
        return _legacy_main(args)
    if args and args[0] not in known and args[0] not in {"--version", "--help", "-h"}:
        return _legacy_main(args)

    parser = build_parser()
    ns = parser.parse_args(args)
    if not ns.command:
        parser.print_help()
        return 0
    root = Path(ns.root).resolve()

    if ns.command == "status":
        payload = diagnostics.status_payload(root)
        _emit(payload, as_json=ns.as_json)
        return 0
    if ns.command == "doctor":
        payload = diagnostics.doctor_payload(root)
        _emit(payload, as_json=ns.as_json)
        return 0 if payload.get("ok") else 1
    if ns.command == "bridge-discovery":
        payload = diagnostics.bridge_payload(root)
        _emit(payload, as_json=ns.as_json)
        return 0
    if ns.command in {"start", "stop", "chat", "chat-gpt"}:
        return _legacy_main(["--root", str(root), *lifecycle.legacy_args(ns.command, list(ns.remainder))])
    if ns.command == "restart":
        stopped = _legacy_main(["--root", str(root), "--daemon-stop"])
        started = _legacy_main(["--root", str(root), "--daemon-start"])
        return started if started else stopped
    if ns.command == "host-finalize":
        payload = host_commands.finalize_payload(ns)
        _emit(payload, as_json=True)
        return 0 if payload.get("accepted") else 2
    audit_db = root / "memory/sqlite/runtime_write_v1/runtime_audit.sqlite3"
    if ns.command == "audit-tail":
        _emit(audit_commands.audit_tail(audit_db, ns.limit), as_json=True)
        return 0
    if ns.command == "explain-turn":
        _emit(audit_commands.explain(audit_db, ns.turn_id, ns.trace_id), as_json=True)
        return 0
    if ns.command == "replay-turn":
        _emit(audit_commands.replay(audit_db, ns.turn_id, ns.trace_id), as_json=True)
        return 0
    if ns.command == "export":
        payload = export_commands.export_payload(
            root=root,
            profile=ns.profile,
            output=ns.output,
            confirm_private_data=ns.confirm_private_data,
            preview_only=ns.preview,
        )
        _emit(payload, as_json=True)
        return 0 if payload.get("ok", True) else 2
    if ns.command == "self-test":
        command = [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "-m", "not live_model and not live_mcp"]
        return subprocess.call(command, cwd=root)
    if ns.command == "package-smoke":
        command = [sys.executable, "-X", "utf8", str(root / "tools/release_readiness_v15.py"), "--root", str(root)]
        return subprocess.call(command, cwd=root)
    parser.error(f"unknown command: {ns.command}")
    return 2
