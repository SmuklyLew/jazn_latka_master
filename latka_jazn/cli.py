from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from latka_jazn.cli_commands import audit as audit_commands
from latka_jazn.cli_commands import diagnostics, export as export_commands, host as host_commands, lifecycle
from latka_jazn.tools.console_progress import TerminalProgress, add_progress_arguments
from latka_jazn.version import PACKAGE_VERSION_FULL


class StableArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        raise SystemExit(2)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", dest="as_json")
    add_progress_arguments(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = StableArgumentParser(prog="run.py", description="Canonical Jaźń v15 operator CLI", allow_abbrev=False)
    parser.add_argument("--version", action="version", version=PACKAGE_VERSION_FULL)
    sub = parser.add_subparsers(dest="command")

    child = sub.add_parser("doctor", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--daemon-host", default="127.0.0.1")
    child.add_argument("--daemon-port", type=int, default=8787)
    child.add_argument("--daemon-marker-output", type=Path)

    for name in ("bridge-discovery", "self-test"):
        child = sub.add_parser(name, allow_abbrev=False)
        _add_common(child)

    child = sub.add_parser("status", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--snapshot", action="store_true", help="Nie wykonuj próby endpointu; pokaż tylko obserwację markera/PID.")
    child.add_argument("--daemon-host", default="127.0.0.1")
    child.add_argument("--daemon-port", type=int, default=8787)
    child.add_argument("--daemon-marker-output", type=Path)

    child = sub.add_parser("package-smoke", allow_abbrev=False)
    _add_common(child)
    child.add_argument(
        "--profile",
        choices=("development", "system", "release", "export-without-git", "memory", "full"),
        default="system",
    )

    child = sub.add_parser("release-metadata", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--allow-dirty", action="store_true")

    child = sub.add_parser("release-build", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--output", type=Path)

    for name in ("memory-prepare", "memory-status"):
        child = sub.add_parser(name, allow_abbrev=False)
        _add_common(child)
        child.add_argument("--deep-verify", action="store_true")
        if name == "memory-prepare":
            child.add_argument("--dry-run", action="store_true")
            child.add_argument("--force", action="store_true")

    child = sub.add_parser("memory-recover", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--force-recovery", action="store_true")
    child.add_argument("--normalize-limit", type=int)
    child.add_argument("--prepare-l2", action="store_true")
    child.add_argument("--l2-limit", type=int, default=120)
    child.add_argument("--build-l3-manifest", action="store_true")
    child.add_argument("--l3-limit", type=int, default=25)
    child.add_argument("--approve-l3-manifest-sha")
    child.add_argument("--approved-by")

    child = sub.add_parser("memory-validate", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--full", action="store_true", help="Użyj PRAGMA integrity_check zamiast quick_check.")
    child.add_argument("--include-all-sqlite", action="store_true", help="Waliduj wszystkie bazy pod memory/sqlite.")
    child.add_argument("--max-errors", type=int, default=100)
    child.add_argument("--table-counts", action="store_true", help="Policz rekordy wszystkich tabel (wolniejsze dla dużych baz).")
    child.add_argument("--hash-files", action="store_true", help="Policz pełne SHA-256 plików SQLite.")
    child.add_argument("--output", type=Path, help="Zapisz raport JSON pod runtime root.")

    child = sub.add_parser("model-status", allow_abbrev=False)
    _add_common(child)
    child.add_argument("--probe", action="store_true")

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
    child.add_argument("--text-sha256", required=True)
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


def _progress(namespace: argparse.Namespace, task: str, *, style: str) -> TerminalProgress:
    return TerminalProgress.from_namespace(namespace, task, style=style)


def _spinner_call(
    namespace: argparse.Namespace,
    *,
    task: str,
    label: str,
    final_label: str,
    operation: Callable[[], Any],
    ok_from_payload: Callable[[Any], bool],
    symbol: str = "work",
) -> Any:
    progress = _progress(namespace, task, style="spinner")
    progress.start_spinner(label, symbol=symbol)
    try:
        payload = operation()
    except Exception as exc:
        progress.fail(f"{final_label}: {type(exc).__name__}")
        raise
    ok = bool(ok_from_payload(payload))
    progress.finish(ok, final_label)
    return payload


def _legacy_main(args: list[str]) -> int:
    from main import main as legacy_main
    return int(legacy_main(args))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    known = {
        "status", "doctor", "start", "stop", "restart", "chat", "chat-gpt",
        "host-finalize", "bridge-discovery", "audit-tail", "explain-turn",
        "replay-turn", "export", "package-smoke", "release-metadata", "release-build", "self-test", "memory-prepare", "memory-status", "memory-recover", "memory-validate", "model-status",
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
        payload = diagnostics.status_payload(
            root, probe_endpoint=not ns.snapshot,
            daemon_host=ns.daemon_host, daemon_port=ns.daemon_port,
            marker_output=ns.daemon_marker_output,
        )
        _emit(payload, as_json=ns.as_json)
        return 0
    if ns.command == "doctor":
        progress = _progress(ns, "doctor", style="bar")
        try:
            payload = diagnostics.doctor_payload(
                root,
                daemon_host=ns.daemon_host,
                daemon_port=ns.daemon_port,
                marker_output=ns.daemon_marker_output,
                progress=progress.callback(),
            )
        except Exception as exc:
            progress.fail(f"Diagnostyka przerwana: {type(exc).__name__}")
            raise
        progress.finish(bool(payload.get("ok")), "Diagnostyka zakończona")
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
        payload = _spinner_call(
            ns,
            task="export",
            label="Przygotowuję plan eksportu" if ns.preview else "Eksportuję wybrany profil",
            final_label="Podgląd eksportu gotowy" if ns.preview else "Eksport zakończony",
            symbol="folder",
            operation=lambda: export_commands.export_payload(
                root=root,
                profile=ns.profile,
                output=ns.output,
                confirm_private_data=ns.confirm_private_data,
                preview_only=ns.preview,
            ),
            ok_from_payload=lambda item: item.get("ok", True),
        )
        _emit(payload, as_json=True)
        return 0 if payload.get("ok", True) else 2
    if ns.command == "self-test":
        command = [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "-m", "not live_model and not live_mcp"]
        return subprocess.call(command, cwd=root)
    if ns.command == "package-smoke":
        from latka_jazn.tools.release_readiness import build_release_readiness_report

        payload = _spinner_call(
            ns,
            task="package-smoke",
            label=f"Sprawdzam gotowość paczki ({ns.profile})",
            final_label="Kontrola paczki zakończona",
            operation=lambda: build_release_readiness_report(root, profile=ns.profile),
            ok_from_payload=lambda item: int(item.get("exit_code", 2)) == 0,
            symbol="lock",
        )
        _emit(payload, as_json=ns.as_json)
        return int(payload.get("exit_code", 2))
    if ns.command == "release-metadata":
        from latka_jazn.tools.release_metadata import generate_release_metadata

        payload = _spinner_call(
            ns,
            task="release-metadata",
            label="Odczytuję Git i przygotowuję metadane wydania",
            final_label="Metadane wydania przygotowane",
            operation=lambda: generate_release_metadata(root, allow_dirty=ns.allow_dirty),
            ok_from_payload=lambda item: int(item.get("exit_code", 2)) == 0,
            symbol="log",
        )
        _emit(payload, as_json=ns.as_json)
        return int(payload.get("exit_code", 2))
    if ns.command == "release-build":
        from latka_jazn.tools.release_bundle import build_release_bundle

        payload = _spinner_call(
            ns,
            task="release-build",
            label="Buduję i weryfikuję paczkę wydania",
            final_label="Budowanie wydania zakończone",
            operation=lambda: build_release_bundle(root, ns.output),
            ok_from_payload=lambda item: int(item.get("exit_code", 2)) == 0,
            symbol="launch",
        )
        _emit(payload, as_json=ns.as_json)
        return int(payload.get("exit_code", 2))
    if ns.command == "memory-recover":
        from latka_jazn.memory.memory_recovery_pipeline import MemoryRecoveryPipeline

        progress = _progress(ns, "memory-recover", style="bar")
        pipeline = MemoryRecoveryPipeline(root)
        payload = pipeline.run(
            force_recovery=ns.force_recovery,
            normalize_limit=ns.normalize_limit,
            prepare_l2=ns.prepare_l2,
            l2_limit=ns.l2_limit,
            build_l3_manifest=ns.build_l3_manifest,
            l3_limit=ns.l3_limit,
            approve_l3_manifest_sha=ns.approve_l3_manifest_sha,
            approved_by=ns.approved_by,
            progress=progress.callback(),
        ).to_dict()
        progress.finish(bool(payload.get("ok")), "Odzysk i przygotowanie pamięci zakończone")
        _emit(payload, as_json=ns.as_json)
        return 0 if payload.get("ok") else 1
    if ns.command == "memory-validate":
        from latka_jazn.tools.memory_validation import validate_large_memory

        progress = _progress(ns, "memory-validate", style="bar")
        payload = validate_large_memory(
            root,
            full=ns.full,
            include_all_sqlite=ns.include_all_sqlite,
            max_errors=ns.max_errors,
            table_counts=ns.table_counts,
            hash_files=ns.hash_files,
            output=ns.output,
            progress=progress.callback(symbol="lock"),
        )
        progress.finish(bool(payload.get("ok")), "Walidacja pamięci zakończona")
        _emit(payload, as_json=ns.as_json)
        return 0 if payload.get("ok") else 1
    if ns.command == "model-status":
        from latka_jazn.config import JaznConfig
        from latka_jazn.model_adapters.factory import build_model_adapter, build_model_adapter_status

        cfg = JaznConfig(root=root)
        adapter = build_model_adapter(cfg)
        if ns.probe and hasattr(adapter, "probe"):
            payload = adapter.probe()
        else:
            payload = build_model_adapter_status(cfg, command="model-status", infer_host_environment=False)
        _emit(payload, as_json=ns.as_json)
        return 0 if (not ns.probe or payload.get("probe_ok") is True) else 1
    if ns.command in {"memory-prepare", "memory-status"}:
        from latka_jazn.config import JaznConfig
        from latka_jazn.memory.normalization_sidecar import MemoryNormalizationSidecar

        cfg = JaznConfig(root=root)
        sidecar = MemoryNormalizationSidecar(
            root,
            source_db_path=cfg.normalization_source_db_path,
            sidecar_db_path=cfg.normalization_sidecar_db_path,
            runtime_version=cfg.version,
        )
        if ns.command == "memory-prepare":
            payload = _spinner_call(
                ns,
                task="memory-prepare",
                label="Przygotowuję warstwę pamięci L1/L2/L3",
                final_label="Przygotowanie pamięci zakończone",
                operation=lambda: sidecar.prepare(
                    dry_run=ns.dry_run,
                    force=ns.force,
                    deep_verify=ns.deep_verify or not ns.dry_run,
                ).to_dict(),
                ok_from_payload=lambda item: item.get("status") in {"ready", "dry_run_ok"},
                symbol="work",
            )
            code = 0 if payload.get("status") in {"ready", "dry_run_ok"} else 1
        else:
            payload = _spinner_call(
                ns,
                task="memory-status",
                label="Sprawdzam stan pamięci" + (" i integralność SQLite" if ns.deep_verify else ""),
                final_label="Kontrola pamięci zakończona",
                operation=lambda: sidecar.wake_state_status(deep_verify=ns.deep_verify).to_dict(),
                ok_from_payload=lambda item: item.get("status") == "ready",
                symbol="lock",
            )
            code = 0 if payload.get("status") == "ready" else 1
        _emit(payload, as_json=ns.as_json)
        return code
    parser.error(f"unknown command: {ns.command}")
    return 2
