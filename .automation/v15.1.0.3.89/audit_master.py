#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

CURRENT = (15, 1, 0, 3, 88)
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".jsonl", ".yml", ".yaml", ".toml",
    ".ini", ".cfg", ".ps1", ".sh", ".bat", ".cmd", ".xml", ".csv",
}
VERSION_MARKERS = (
    "version", "wersj", "schema", "release", "runtime", "contract", "migration",
    "migrac", "legacy", "compat", "package", "hotfix", "v14", "v15",
)
PATTERNS = (
    ("prefixed_dotted", re.compile(r"(?i)(?<![A-Za-z0-9])v(?P<version>1[45](?:\.\d+){1,7})(?:[-_][A-Za-z0-9][A-Za-z0-9._-]*)?")),
    ("prefixed_slug", re.compile(r"(?i)(?<![A-Za-z0-9])v(?P<version>1[45](?:_\d+){1,7})(?![A-Za-z0-9])")),
    ("plain_dotted", re.compile(r"(?<![A-Za-z0-9])(?P<version>1[45](?:\.\d+){1,7})(?![A-Za-z0-9])")),
)


def run_git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def parse_version(raw: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in raw.replace("_", ".").split("."))
    except ValueError:
        return None


def is_older(raw: str) -> bool:
    parsed = parse_version(raw)
    if not parsed or parsed[0] not in {14, 15}:
        return False
    padded = (parsed + (0, 0, 0, 0, 0))[:5]
    return padded < CURRENT


def looks_like_date(raw: str) -> bool:
    parts = raw.replace("_", ".").split(".")
    if len(parts) != 3:
        return False
    try:
        day, month, year = map(int, parts)
    except ValueError:
        return False
    return 1 <= day <= 31 and 1 <= month <= 12 and 2000 <= year <= 2100


def classify_path(path: str) -> tuple[str, str]:
    folded = path.lower()
    name = Path(path).name.lower()
    if path == "latka_jazn/contracts/embedded_sources.py":
        return "generated_private_source", "remove_from_active_tree_and_retain_only_reviewed_metadata"
    if path in {"PACKAGE_INTEGRITY_MANIFEST.json", "SOURCE_PROVENANCE.json"}:
        return "generated_release_metadata", "regenerate_after_commit"
    if folded.startswith("docs/patches/") or name.endswith((".patch", ".diff")):
        return "historical_patch_material", "archive"
    if folded.startswith("docs/reports/") or "_report" in name or "report." in name:
        return "historical_report", "archive_or_replace_with_current_report"
    if folded.startswith("docs/plans/"):
        return "historical_or_active_plan", "review_plan_state_then_archive_history"
    if folded.startswith("docs/") or name in {"readme.md", "agents.md", "agents.chatgpt.md", "agents.codex.md", "agents.ollama.md"}:
        return "active_documentation", "rewrite_current_contract_and_archive_history"
    if folded.startswith("tests/"):
        return "active_test", "rename_or_rewrite_semantically_if_still_required"
    if folded.startswith("latka_jazn/resources/"):
        return "active_or_legacy_resource", "consolidate_current_resource_and_archive_superseded_versions"
    if folded.startswith("latka_jazn/") or path in {"main.py", "run.py"}:
        return "active_code", "rewrite_to_current_dynamic_contract_or_generic_compatibility"
    if folded.startswith("tools/"):
        return "active_tool", "rewrite_or_archive_if_one_time_migration"
    if folded.startswith(".github/"):
        return "ci_configuration", "rewrite_current_test_paths_and_contracts"
    return "static_project_file", "manual_review"


def detect_hits(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception:
            return []
    except Exception:
        return []
    hits: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        folded = line.casefold()
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(line):
                raw = match.group("version")
                if not is_older(raw) or looks_like_date(raw):
                    continue
                if kind == "plain_dotted" and not any(marker in folded for marker in VERSION_MARKERS):
                    continue
                hits.append({
                    "line": lineno,
                    "match_kind": kind,
                    "version": raw,
                    "token": match.group(0),
                    "line_sha256": hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest(),
                })
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", default=".")
    parser.add_argument("--out", default="audit-v15.1.0.3.89")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    out = root / args.out
    out.mkdir(parents=True, exist_ok=True)
    tracked = [item for item in run_git(root, "ls-files", "-z").split("\0") if item]
    head = run_git(root, "rev-parse", "HEAD").strip()
    branch = run_git(root, "branch", "--show-current").strip()
    files: list[dict[str, Any]] = []
    version_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    hit_count = 0
    for rel in tracked:
        path = root / rel
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        hits = detect_hits(path)
        if not hits:
            continue
        category, action = classify_path(rel)
        for hit in hits:
            version_counts[hit["version"]] += 1
        category_counts[category] += 1
        action_counts[action] += 1
        hit_count += len(hits)
        files.append({
            "path": rel,
            "category": category,
            "recommended_action": action,
            "hit_count": len(hits),
            "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
            "hits": hits,
        })
    files.sort(key=lambda item: item["path"])
    truth = (
        "Audyt wykrywa jawne tokeny wersji starsze niż v15.1.0.3.88 w śledzonych plikach tekstowych. "
        "Nie uznaje samego dopasowania za dowód, że plik jest zbędny; decyzję migracyjną określa kategoria i testy użycia. "
        "Raport nie zapisuje pełnych linii źródłowych, tylko token, numer linii i SHA-256 linii."
    )
    payload = {
        "schema_version": "jazn_old_version_audit/v15.1.0.3.89",
        "source_commit": head,
        "source_branch": branch,
        "target_version": "v15.1.0.3.89",
        "comparison_floor": "v15.1.0.3.88",
        "tracked_file_count": len(tracked),
        "files_with_old_references": len(files),
        "old_reference_count": hit_count,
        "version_counts": dict(version_counts.most_common()),
        "category_counts": dict(category_counts),
        "recommended_action_counts": dict(action_counts),
        "files": files,
        "truth_boundary": truth,
    }
    (out / "old-version-audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = [
        "# Audyt odwołań do wersji starszych niż v15.1.0.3.88", "",
        f"- source commit: `{head}`", f"- source branch: `{branch}`",
        f"- śledzone pliki: **{len(tracked)}**", f"- pliki z trafieniami: **{len(files)}**",
        f"- trafienia: **{hit_count}**", "", "## Kategorie", "",
    ]
    for key, value in sorted(category_counts.items()):
        md.append(f"- `{key}`: {value}")
    md.extend(["", "## Pliki", ""])
    for item in files:
        versions = Counter(hit["version"] for hit in item["hits"])
        shown = ", ".join(f"{key}×{value}" for key, value in versions.most_common(8))
        md.append(f"- `{item['path']}` — {item['category']} — {item['recommended_action']} — {item['hit_count']} trafień ({shown})")
    md.extend(["", "## Granica prawdy", "", truth, ""])
    (out / "old-version-audit.md").write_text("\n".join(md), encoding="utf-8")
    (out / "old-version-files.txt").write_text("\n".join(item["path"] for item in files) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "source_commit": head, "tracked_file_count": len(tracked), "files_with_old_references": len(files), "old_reference_count": hit_count, "output": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
