from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Iterable

from latka_jazn.tools.console_progress import TerminalProgress, add_progress_arguments
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version
from latka_jazn.core.version_source import (
    read_runtime_version_from_version_py,
    read_version_metadata_from_version_py,
    version_number,
)


ROOT = Path(__file__).resolve().parents[2]
ACTIVE_VERSION = read_runtime_version_from_version_py(ROOT, fallback=PACKAGE_VERSION_FULL) or PACKAGE_VERSION_FULL
ACTIVE_SEMVER = version_number(ACTIVE_VERSION)
SCHEMA_VERSION = schema_version("version_consistency_audit")
ProgressCallback = Callable[[int, int, str], None]

SOURCE_OF_TRUTH_FILES = (
    "latka_jazn/version.py",
)

CONTROLLED_GENERATED_FILES = (
    "PACKAGE_INTEGRITY_MANIFEST.json",
    "RUNTIME_STATE.json",
    "SOURCE_PROVENANCE.json",
    "docs/update_history/INDEX.json",
    "docs/archive/manifest_history/last_refresh_report.json",
    "workspace_runtime/JAZN_ACTIVE_RUNTIME.json",
    "JAZN_ACTIVE_RUNTIME.json",
    "ACTIVE_RUNTIME_CACHE_CONTRACT.json",
    "BOOTSTRAP_JAZN_CURRENT.json",
)

ACTIVE_CONTROL_FILES = (
    *SOURCE_OF_TRUTH_FILES,
    "pyproject.toml",
    "main.py",
    "README.md",
    "START_CHATGPT_FROM_HERE.txt",
)

RUNTIME_GENERATED_ROOT_FILES = (
    "ACTIVE_RUNTIME_CACHE_CONTRACT.json",
    "BOOTSTRAP_JAZN_CURRENT.json",
    "RUNTIME_STATE.json",
)

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".csv",
    ".ini",
    ".json",
    ".jsonl",
    ".md",
    ".ps1",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SKIP_PREFIXES = (
    ".git/",
    ".archives/",
    "memory/",
    "workspace_runtime/",
    "exports/",
    "reports/",
    "patchs/",
    "backups/",
    "backups_git/",
)
MAX_SCAN_BYTES = 4 * 1024 * 1024
VERSION_PATTERN = re.compile(r"v\d+\.\d+(?:\.\d+)*(?:[-_][A-Za-z0-9_.-]+)?|\d+\.\d+(?:\.\d+)+")


def classify_version_mention(
    path: Path,
    version: str,
    *,
    active_version: str = ACTIVE_VERSION,
    active_semver: str = ACTIVE_SEMVER,
) -> str:
    if path.as_posix().endswith("latka_jazn/version.py"):
        return "canonical_source"
    if version in {active_version, active_semver, "v" + active_semver}:
        return "forbidden_raw_current_version"
    if "-" in version or "_" in version:
        return "historical_or_named_version"
    return "component_schema_or_lineage_allowed"


def scan_version_mentions(
    root: Path = ROOT,
    *,
    active_version: str = ACTIVE_VERSION,
    active_semver: str = ACTIVE_SEMVER,
) -> list[dict[str, str]]:
    mentions: list[dict[str, str]] = []
    for rel in ACTIVE_CONTROL_FILES:
        path = root / rel
        if not path.is_file():
            mentions.append({"path": rel, "version": "missing", "classification": "missing"})
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in VERSION_PATTERN.finditer(text):
            value = match.group(0)
            mentions.append(
                {
                    "path": rel,
                    "version": value,
                    "classification": classify_version_mention(
                        Path(rel),
                        value,
                        active_version=active_version,
                        active_semver=active_semver,
                    ),
                }
            )
    return mentions


def _tracked_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]


def _candidate_paths(root: Path) -> Iterable[Path]:
    rels = _tracked_files(root)
    if rels:
        for rel in rels:
            path = root / rel
            if path.is_file():
                yield path
        # Local packaging tools are intentionally shipped in some full bundles
        # even when they are not tracked by the base Git snapshot.
        for rel in (
            "tools/_jazn_pack_generator.py",
            "tools/__jazn_pack_generator_settings.json",
        ):
            path = root / rel
            if path.is_file() and rel not in rels:
                yield path
        return
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def _is_scannable(path: Path, root: Path) -> bool:
    rel = path.relative_to(root).as_posix()
    if rel in SOURCE_OF_TRUTH_FILES or rel in CONTROLLED_GENERATED_FILES:
        return False
    if rel.startswith(SKIP_PREFIXES):
        return False
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    try:
        return path.stat().st_size <= MAX_SCAN_BYTES
    except OSError:
        return False


def _line_hits(text: str, token: str) -> list[int]:
    return [index for index, line in enumerate(text.splitlines(), start=1) if token in line]


def scan_forbidden_current_literals(
    root: Path = ROOT,
    *,
    progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    metadata = read_version_metadata_from_version_py(root)
    tokens = {
        metadata.package_version_full,
        metadata.package_version,
        metadata.distribution_version,
    }
    if metadata.release_name:
        tokens.add(metadata.release_name)
    tokens.discard("")

    violations: list[dict[str, Any]] = []
    candidates = list(_candidate_paths(root))
    total = max(1, len(candidates))
    for index, path in enumerate(candidates, start=1):
        if progress is not None:
            overall = 15 + round(75 * index / total)
            progress(overall, 100, f"Skanowanie wersji w plikach: {index}/{len(candidates)}")
        if not _is_scannable(path, root):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="strict")
        except (OSError, UnicodeError):
            continue
        rel = path.relative_to(root).as_posix()
        for token in sorted(tokens, key=len, reverse=True):
            lines = _line_hits(text, token)
            if lines:
                violations.append({"path": rel, "token": token, "lines": lines[:20]})
                break
    return violations


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _generated_metadata_errors(root: Path) -> list[dict[str, Any]]:
    metadata = read_version_metadata_from_version_py(root)
    errors: list[dict[str, Any]] = []

    if (root / "VERSION.txt").exists():
        errors.append({"kind": "forbidden_legacy_version_checkpoint_present", "path": "VERSION.txt"})

    pyproject_path = root / "pyproject.toml"
    pyproject = pyproject_path.read_text(encoding="utf-8") if pyproject_path.is_file() else ""
    if 'dynamic = ["version"]' not in pyproject:
        errors.append({"kind": "pyproject_version_not_dynamic", "path": "pyproject.toml"})
    if 'version = {attr = "latka_jazn.version.DISTRIBUTION_VERSION"}' not in pyproject:
        errors.append({"kind": "pyproject_not_bound_to_version_py", "path": "pyproject.toml"})

    manifest = _load_json(root / "PACKAGE_INTEGRITY_MANIFEST.json")
    if not manifest:
        errors.append({"kind": "package_integrity_manifest_missing", "path": "PACKAGE_INTEGRITY_MANIFEST.json"})
    if (root / "MANIFEST_CURRENT.json").exists():
        errors.append({"kind": "forbidden_legacy_manifest_alias_present", "path": "MANIFEST_CURRENT.json"})

    if manifest:
        for key in ("version", "runtime_version", "package_version"):
            if manifest.get(key) != metadata.package_version_full:
                errors.append({"kind": "generated_manifest_stale", "path": "PACKAGE_INTEGRITY_MANIFEST.json", "field": key})
        expected_schema = f"package_integrity_manifest/{metadata.package_version}"
        if manifest.get("schema_version") != expected_schema:
            errors.append({"kind": "generated_manifest_stale", "path": "PACKAGE_INTEGRITY_MANIFEST.json", "field": "schema_version"})

    provenance = _load_json(root / "SOURCE_PROVENANCE.json")
    if provenance:
        expected = {
            "schema_version": f"source_provenance/{metadata.package_version}",
            "runtime_version": metadata.package_version_full,
            "update_version": metadata.package_version_full,
            "version_source": "latka_jazn/version.py",
        }
        for key, value in expected.items():
            if provenance.get(key) != value:
                errors.append({"kind": "generated_provenance_stale", "path": "SOURCE_PROVENANCE.json", "field": key})
        if "version_checkpoint" in provenance:
            errors.append({"kind": "legacy_version_checkpoint_in_provenance", "path": "SOURCE_PROVENANCE.json", "field": "version_checkpoint"})

    index = _load_json(root / "docs" / "update_history" / "INDEX.json")
    if index and index.get("active_version") != metadata.package_version_full:
        errors.append({"kind": "generated_update_index_stale", "path": "docs/update_history/INDEX.json"})

    report = _load_json(root / "docs" / "archive" / "manifest_history" / "last_refresh_report.json")
    if report and report.get("version") != metadata.package_version_full:
        errors.append({"kind": "generated_manifest_report_stale", "path": "docs/archive/manifest_history/last_refresh_report.json"})

    settings = _load_json(root / "tools" / "__jazn_pack_generator_settings.json")
    if settings and not bool(settings.get("archive_name_manual", False)) and settings.get("archive_name"):
        errors.append({"kind": "generated_archive_name_persisted", "path": "tools/__jazn_pack_generator_settings.json"})

    return errors


def build_audit(
    root: Path = ROOT,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    root = Path(root).resolve()
    if progress is not None:
        progress(0, 100, "Wczytywanie źródła wersji")
    active_version = read_runtime_version_from_version_py(root, fallback=PACKAGE_VERSION_FULL) or PACKAGE_VERSION_FULL
    active_semver = version_number(active_version)
    if progress is not None:
        progress(8, 100, "Wersja aktywna rozwiązana")
    mentions = scan_version_mentions(root, active_version=active_version, active_semver=active_semver)
    if progress is not None:
        progress(15, 100, "Kontrolne wzmianki wersji sprawdzone")
    literal_violations = scan_forbidden_current_literals(root, progress=progress)
    if progress is not None:
        progress(94, 100, "Metadane generowane i kontrakty wersji sprawdzane")
    metadata_errors = _generated_metadata_errors(root)
    errors: list[dict[str, Any]] = [
        {"kind": "forbidden_raw_current_version", **item}
        for item in literal_violations
    ]
    errors.extend(metadata_errors)
    if progress is not None:
        progress(100, 100, "Audyt spójności wersji zakończony")
    return {
        "schema_version": SCHEMA_VERSION,
        "active_version": active_version,
        "active_semver": active_semver,
        "source_of_truth_files": list(SOURCE_OF_TRUTH_FILES),
        "controlled_generated_files": list(CONTROLLED_GENERATED_FILES),
        "runtime_generated_root_files": list(RUNTIME_GENERATED_ROOT_FILES),
        "mentions": mentions,
        "literal_violations": literal_violations,
        "metadata_errors": metadata_errors,
        "errors": errors,
        "ok": not errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit single-source version and generated metadata consistency.")
    parser.add_argument("--root", type=Path, default=ROOT)
    add_progress_arguments(parser)
    args = parser.parse_args(argv)
    display = TerminalProgress.from_namespace(args, "version-consistency", style="dots")
    try:
        payload = build_audit(args.root, progress=display.callback(symbol="lock"))
    except Exception as exc:
        display.fail(f"Audyt wersji przerwany: {type(exc).__name__}")
        raise
    display.finish(bool(payload.get("ok")), "Audyt spójności wersji zakończony")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not payload["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
