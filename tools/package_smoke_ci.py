from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import os
import subprocess
import sys


def _compact_check(check: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "name": check.get("name"),
        "ok": bool(check.get("ok")),
        "required": bool(check.get("required", True)),
    }
    for key in ("error", "error_code", "returncode", "stderr", "stdout", "policy"):
        value = check.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, str) and len(value) > 2000:
                value = value[-2000:]
            compact[key] = value
    report = check.get("report")
    if isinstance(report, dict):
        compact["report"] = {
            key: report.get(key)
            for key in (
                "ok", "status", "active_state", "exit_code", "configuration_error",
                "foreign_key_error_count", "integrity_check", "limitations", "errors",
            )
            if report.get(key) not in (None, "", [], {})
        }
    for key in ("daemon", "consensus", "stop", "after"):
        value = check.get(key)
        if isinstance(value, dict):
            compact[key] = {
                field: value.get(field)
                for field in (
                    "ok", "valid", "mismatch", "active_state", "observation_state",
                    "endpoint_reachable", "pid_alive", "error", "status",
                )
                if value.get(field) is not None
            }
    return compact


def main() -> int:
    parser = argparse.ArgumentParser(description="Run package-smoke and print a compact CI failure summary.")
    parser.add_argument("--profile", default="release")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args()

    output = Path(args.output) if args.output else Path(os.environ.get("RUNNER_TEMP", ".")) / f"package-smoke-{args.profile}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "run.py", "package-smoke", "--root", args.root,
         "--profile", args.profile, "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output.write_text(completed.stdout, encoding="utf-8")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print(json.dumps({
            "ok": False,
            "profile": args.profile,
            "returncode": completed.returncode,
            "output": str(output),
            "json_error": True,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        }, ensure_ascii=False, indent=2))
        return completed.returncode or 2

    checks = [check for check in payload.get("checks") or [] if isinstance(check, dict)]
    required_failures = [_compact_check(check) for check in checks if check.get("required", True) and not check.get("ok")]
    optional_failures = [_compact_check(check) for check in checks if not check.get("required", True) and not check.get("ok")]
    print(json.dumps({
        "ok": bool(payload.get("ok")),
        "profile": payload.get("profile", args.profile),
        "exit_code": payload.get("exit_code", completed.returncode),
        "summary": payload.get("summary"),
        "required_failures": required_failures,
        "optional_failures": optional_failures,
        "full_report": str(output),
        "stderr_tail": completed.stderr[-2000:] if completed.stderr else "",
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
