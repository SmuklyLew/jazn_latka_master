from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASE = "2e244d4a245440447102cca2ed3c7f947c8fd5c2"
EXPECTED_PATCH_SHA256 = "cb6a29630759492de197a53c7072e4d6a61ac96c2a6aba76630b5335b3e2e804"
ARCHIVE_REL = Path(".archives/pre_v15_1_0_3_89/ARCHIVE_MANIFEST.json")


def run(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    payload = Path(args.payload).resolve()

    head = run(root, "rev-parse", "HEAD").stdout.strip()
    if head != EXPECTED_BASE:
        raise SystemExit(f"refusing migration: expected {EXPECTED_BASE}, got {head}")
    if run(root, "status", "--porcelain").stdout.strip():
        raise SystemExit("refusing migration: worktree is not clean")

    parts = sorted((payload / "plain-patch").glob("part-*.patch"))
    if not parts:
        raise SystemExit("no patch parts")
    patch_path = payload / "migration.patch"
    with patch_path.open("wb") as target:
        for part in parts:
            target.write(part.read_bytes())
    actual_patch_sha = sha256(patch_path)
    if actual_patch_sha != EXPECTED_PATCH_SHA256:
        raise SystemExit(f"patch sha mismatch: {actual_patch_sha}")

    manifest_source = payload / "ARCHIVE_MANIFEST.json"
    manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
    if manifest.get("source_commit") != EXPECTED_BASE:
        raise SystemExit("archive manifest source commit mismatch")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) != 204:
        raise SystemExit(f"unexpected archive entries: {len(entries) if isinstance(entries, list) else 'invalid'}")

    copied = 0
    private_metadata = 0
    for entry in entries:
        original = root / entry["original_path"]
        if not original.is_file():
            raise SystemExit(f"missing archive source: {entry['original_path']}")
        if original.stat().st_size != int(entry["size_bytes"]):
            raise SystemExit(f"archive source size mismatch: {entry['original_path']}")
        if sha256(original) != entry["sha256"]:
            raise SystemExit(f"archive source sha mismatch: {entry['original_path']}")
        retention = entry["retention"]
        if retention == "metadata_only_private_source":
            if entry.get("archive_path") is not None:
                raise SystemExit("private source must not have archive path")
            private_metadata += 1
            continue
        if retention != "exact_copy":
            raise SystemExit(f"unsupported retention: {retention}")
        destination = root / entry["archive_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        if sha256(destination) != entry["sha256"]:
            raise SystemExit(f"archive copy sha mismatch: {entry['archive_path']}")
        copied += 1

    archive_manifest = root / ARCHIVE_REL
    archive_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest_source, archive_manifest)

    check = run(root, "apply", "--check", "--binary", str(patch_path), check=False)
    if check.returncode != 0:
        raise SystemExit(f"git apply --check failed\n{check.stdout}\n{check.stderr}")
    run(root, "apply", "--binary", str(patch_path))

    forbidden = [root / ".automation", root / ".transport"]
    for path in forbidden:
        if path.exists():
            raise SystemExit(f"forbidden technical path in target: {path.name}")

    run(root, "add", "-A")
    check_diff = run(root, "diff", "--cached", "--check", check=False)
    if check_diff.returncode != 0:
        raise SystemExit(check_diff.stdout + check_diff.stderr)
    print(json.dumps({
        "ok": True,
        "base": head,
        "patch_sha256": actual_patch_sha,
        "archive_exact_copies": copied,
        "archive_private_metadata_only": private_metadata,
        "staged_paths": len([line for line in run(root, "diff", "--cached", "--name-only").stdout.splitlines() if line]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
