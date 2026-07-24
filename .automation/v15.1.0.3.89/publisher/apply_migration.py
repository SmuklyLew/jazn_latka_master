from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_SOURCE_COMMIT = "2e244d4a245440447102cca2ed3c7f947c8fd5c2"
EXPECTED_HEAD = EXPECTED_SOURCE_COMMIT
EXPECTED_TEXT_PLAN_SHA256 = "a114e7ee6f3c27f43e915b1ef7dce1354e3849acb69374ef762bcc1c725fc882"
EXPECTED_ARCHIVE_PLAN_SHA256 = "fcfa5711de9e22e58d63b28dae8a1ec829d7fb0b6c8539b52a10b069718b082d"
ARCHIVE_REL = Path(".archives/pre_v15_1_0_3_89/ARCHIVE_MANIFEST.json")


def run(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if check and proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def join_parts(directory: Path, output: Path, expected_sha: str) -> Path:
    parts = sorted(directory.glob("part-*"))
    if not parts:
        raise SystemExit(f"no plan parts in {directory}")
    with output.open("wb") as target:
        for part in parts:
            target.write(part.read_bytes())
    actual = sha256(output)
    if actual != expected_sha:
        raise SystemExit(f"plan sha mismatch for {output.name}: {actual}")
    return output


def apply_text_plan(root: Path, plan: dict) -> int:
    operations = plan.get("o")
    if not isinstance(operations, list) or len(operations) != 251:
        raise SystemExit("unexpected text migration operation count")
    for operation in operations:
        kind = operation["k"]
        path = root / operation["p"]
        if kind == "a":
            if path.exists():
                raise SystemExit(f"add target already exists: {operation['p']}")
            data = operation["c"].encode("utf-8")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        elif kind == "d":
            if not path.is_file() or sha256(path) != operation["h"]:
                raise SystemExit(f"delete source mismatch: {operation['p']}")
            path.unlink()
            continue
        else:
            source = root / operation.get("q", operation["p"])
            if not source.is_file() or sha256(source) != operation["h"]:
                raise SystemExit(f"source mismatch: {source.relative_to(root)}")
            lines = source.read_bytes().decode("utf-8").splitlines(keepends=True)
            for i1, i2, new in reversed(operation.get("e", [])):
                lines[int(i1):int(i2)] = [new]
            data = "".join(lines).encode("utf-8")
            if kind == "r":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                source.unlink()
            elif kind == "m":
                path.write_bytes(data)
            else:
                raise SystemExit(f"unsupported operation kind: {kind}")
        if sha256_bytes(data) != operation["n"]:
            raise SystemExit(f"result sha mismatch: {operation['p']}")
    return len(operations)


def classify_archive_path(path: str) -> tuple[str, str, str]:
    folded = path.lower()
    name = Path(path).name.lower()
    if path == "latka_jazn/contracts/embedded_sources.py":
        return "generated_private_source", "remove_active_retain_metadata_only", "metadata_only_private_source"
    if path in {
        "JAZN_v15.0.3.3_SELF_CONFIG_COMPATIBILITY_REPORT.txt",
        "tools/jazn_version_rebuild_v0.1_callback_fix.diff",
    } or "_v14_" in name or "_v15_" in name or "v15." in name:
        return "versioned_path_or_historical_artifact", "archive_and_migrate_if_active", "exact_copy"
    if folded.startswith("docs/patches/") or name.endswith((".patch", ".diff")):
        return "historical_patch_material", "archive", "exact_copy"
    if folded.startswith("docs/reports/") or "_report" in name or "report." in name:
        return "historical_report", "archive_or_replace", "exact_copy"
    if folded.startswith("docs/plans/"):
        return "historical_or_active_plan", "review_then_archive_history", "exact_copy"
    if folded.startswith("docs/") or name == "readme.md":
        return "active_documentation", "rewrite_current_archive_history", "exact_copy"
    if folded.startswith("tests/"):
        return "active_test", "rewrite_semantic_or_archive_legacy_fixture", "exact_copy"
    if folded.startswith("latka_jazn/resources/"):
        return "active_or_legacy_resource", "consolidate_current_archive_superseded", "exact_copy"
    if folded.startswith("latka_jazn/") or path in {"main.py", "run.py"}:
        return "active_code", "rewrite_dynamic_or_generic_compatibility", "exact_copy"
    if folded.startswith("tools/"):
        return "active_tool", "rewrite_or_archive_one_time", "exact_copy"
    return "versioned_path_or_historical_artifact", "archive_and_migrate_if_active", "exact_copy"


def create_archive(root: Path, plan: dict) -> tuple[int, int]:
    paths = plan.get("p")
    if not isinstance(paths, list) or len(paths) != 204:
        raise SystemExit("unexpected archive plan entry count")
    copied = 0
    private_metadata = 0
    manifest_entries = []
    for original_path in paths:
        original = root / original_path
        if not original.is_file():
            raise SystemExit(f"missing archive source: {original_path}")
        actual_sha = sha256(original)
        actual_size = original.stat().st_size
        category, reason, retention = classify_archive_path(original_path)
        archive_path = None
        if retention == "metadata_only_private_source":
            private_metadata += 1
        else:
            archive_path = f".archives/pre_v15_1_0_3_89/tree/{original_path}"
            destination = root / archive_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
            if sha256(destination) != actual_sha:
                raise SystemExit(f"archive copy sha mismatch: {archive_path}")
            copied += 1
        manifest_entries.append({
            "archive_path": archive_path,
            "category": category,
            "original_path": original_path,
            "reason": reason,
            "retention": retention,
            "sha256": actual_sha,
            "size_bytes": actual_size,
        })
    archive_manifest = root / ARCHIVE_REL
    archive_manifest.parent.mkdir(parents=True, exist_ok=True)
    archive_payload = {
        "exact_copy_count": copied,
        "file_count": len(manifest_entries),
        "files": manifest_entries,
        "metadata_only_private_count": private_metadata,
        "schema_version": "jazn_source_archive/v15.1.0.3.89",
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "source_version": "v15.1.0.3.88-Night of Hotfix",
        "target_version": "v15.1.0.3.89-Night of Hotfix",
        "truth_boundary": "Archive preserves reviewed historical project sources. Private generated embedded_sources.py is represented only by path, size and SHA-256; its content is not duplicated.",
    }
    archive_manifest.write_text(json.dumps(archive_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / ".archives/pre_v15_1_0_3_89/README.md").write_text(
        "# Archiwum źródeł sprzed v15.1.0.3.89\n\n"
        "Źródło: `2e244d4a245440447102cca2ed3c7f947c8fd5c2` (`v15.1.0.3.88-Night of Hotfix`).\n\n"
        "Archiwum przechowuje dokładne kopie historycznych plików wykrytych podczas migracji aktywnej linii. Nie jest częścią paczki systemowej ani aktywnego runtime. Plik `latka_jazn/contracts/embedded_sources.py` nie został skopiowany z powodu granicy prywatności; manifest przechowuje wyłącznie jego metadane integralności.\n\n"
        "Aktywna logika potrzebna przez najnowszą linię została przeniesiona do nazw semantycznych i bieżących kontraktów w głównym drzewie źródeł.\n",
        encoding="utf-8",
    )
    return copied, private_metadata


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    payload = Path(args.payload).resolve()
    head = run(root, "rev-parse", "HEAD").stdout.strip()
    if head != EXPECTED_HEAD:
        raise SystemExit(f"refusing migration: expected {EXPECTED_HEAD}, got {head}")
    if run(root, "status", "--porcelain").stdout.strip():
        raise SystemExit("refusing migration: worktree is not clean")
    text_plan = json.loads(join_parts(payload / "text-plan", payload / "TEXT_PLAN.json", EXPECTED_TEXT_PLAN_SHA256).read_text(encoding="utf-8"))
    archive_plan = json.loads(join_parts(payload / "archive-plan", payload / "ARCHIVE_PLAN.json", EXPECTED_ARCHIVE_PLAN_SHA256).read_text(encoding="utf-8"))
    copied, private_metadata = create_archive(root, archive_plan)
    operation_count = apply_text_plan(root, text_plan)
    for forbidden in (root / ".automation", root / ".transport"):
        if forbidden.exists():
            raise SystemExit(f"forbidden technical path in target: {forbidden.name}")
    run(root, "add", "-A")
    check_diff = run(root, "diff", "--cached", "--check", check=False)
    if check_diff.returncode != 0:
        raise SystemExit(check_diff.stdout + check_diff.stderr)
    print(json.dumps({
        "ok": True, "base": head, "operations": operation_count,
        "archive_exact_copies": copied, "archive_private_metadata_only": private_metadata,
        "staged_paths": len(run(root, "diff", "--cached", "--name-only").stdout.splitlines()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
