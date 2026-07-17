from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import os
import tempfile
import zipfile

from latka_jazn.tools.package_export import export_package
from latka_jazn.tools.release_readiness import build_release_readiness_report
from latka_jazn.tools.release_staging import create_release_staging
from latka_jazn.tools.safe_paths import validate_safe_relative_path
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_release_filename() -> str:
    version = PACKAGE_VERSION_FULL.strip().replace("/", "-").replace("\\", "-")
    return f"jazn_latka_{version}.zip"


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    # The top 16 bits store the Unix mode when create_system == 3.
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    return info.create_system == 3 and (unix_mode & 0o170000) == 0o120000


def verify_release_zip_manifest(zip_path: Path | str) -> dict[str, Any]:
    """Verify transport safety, CRC and manifest hashes directly inside a ZIP."""

    zip_path = Path(zip_path).resolve()
    errors: list[dict[str, Any]] = []
    checked = 0
    manifest: dict[str, Any] = {}
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            member_counts: dict[str, int] = {}
            safe_names: set[str] = set()
            for info in infos:
                try:
                    canonical = validate_safe_relative_path(info.filename)
                except Exception as exc:
                    errors.append({
                        "code": "unsafe_zip_member",
                        "path": info.filename,
                        "detail": repr(exc),
                    })
                    continue
                member_counts[canonical] = member_counts.get(canonical, 0) + 1
                safe_names.add(canonical)
                if _zip_member_is_symlink(info):
                    errors.append({"code": "symlink_member_forbidden", "path": canonical})

            for name, count in sorted(member_counts.items()):
                if count > 1:
                    errors.append({"code": "duplicate_zip_member", "path": name, "count": count})

            bad_crc = archive.testzip()
            if bad_crc:
                errors.append({"code": "crc_failure", "path": bad_crc})

            if "PACKAGE_INTEGRITY_MANIFEST.json" not in safe_names:
                errors.append({"code": "manifest_missing"})
            else:
                raw_manifest = archive.read("PACKAGE_INTEGRITY_MANIFEST.json")
                decoded = json.loads(raw_manifest.decode("utf-8-sig"))
                if not isinstance(decoded, dict):
                    errors.append({"code": "manifest_not_object"})
                else:
                    manifest = decoded

            manifest_version = str(manifest.get("runtime_version") or manifest.get("version") or "")
            if manifest and manifest_version != PACKAGE_VERSION_FULL:
                errors.append({
                    "code": "version_mismatch",
                    "manifest_version": manifest_version,
                    "runtime_version": PACKAGE_VERSION_FULL,
                })

            listed_names: set[str] = set()
            for entry in manifest.get("files") or []:
                try:
                    relative = validate_safe_relative_path(str((entry or {}).get("path") or ""))
                except Exception as exc:
                    errors.append({"code": "unsafe_manifest_path", "detail": repr(exc)})
                    continue
                if relative in listed_names:
                    errors.append({"code": "duplicate_manifest_path", "path": relative})
                    continue
                listed_names.add(relative)
                if relative not in safe_names:
                    errors.append({"code": "file_missing", "path": relative})
                    continue
                info = archive.getinfo(relative)
                expected_size = int(entry.get("size_bytes", -1))
                if info.file_size != expected_size:
                    errors.append({
                        "code": "size_mismatch",
                        "path": relative,
                        "expected": expected_size,
                        "actual": info.file_size,
                    })
                digest = hashlib.sha256()
                with archive.open(relative, "r") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                actual_hash = digest.hexdigest()
                expected_hash = str(entry.get("sha256") or "")
                if actual_hash != expected_hash:
                    errors.append({
                        "code": "sha256_mismatch",
                        "path": relative,
                        "expected": expected_hash,
                        "actual": actual_hash,
                    })
                checked += 1

            allowed_names = set(listed_names)
            allowed_names.add("PACKAGE_INTEGRITY_MANIFEST.json")
            unexpected = sorted(safe_names - allowed_names)
            for relative in unexpected:
                errors.append({"code": "unexpected_zip_member", "path": relative})
    except Exception as exc:
        errors.append({"code": "zip_verification_exception", "detail": repr(exc)})

    return {
        "schema_version": schema_version("release_zip_manifest_verification"),
        "ok": not errors,
        "zip_path": str(zip_path),
        "zip_sha256": _sha256_file(zip_path) if zip_path.is_file() else None,
        "manifest_runtime_version": manifest.get("runtime_version") or manifest.get("version"),
        "checked_file_count": checked,
        "errors": errors,
    }


def _move_optional(source: Path, destination: Path) -> str | None:
    if not source.is_file():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    return str(destination)


def build_release_bundle(
    root: Path | str,
    output_zip: Path | str | None = None,
) -> dict[str, Any]:
    """Build one verified release ZIP atomically from the clean current Git commit.

    Fresh provenance and the canonical package manifest are generated only in a
    temporary release staging tree. The source checkout is not rewritten, which
    avoids committing metadata that would immediately become stale after commit.
    The final ZIP replaces an older output only after all checks pass.
    """

    root = Path(root).resolve()
    if output_zip is None:
        output = root / "exports" / _default_release_filename()
    else:
        output = Path(output_zip)
        if not output.is_absolute():
            output = root / output
        output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Build provenance and the canonical manifest before creating any temporary
        # output inside the checkout. This preserves the clean-HEAD proof even when
        # the requested ZIP path is located below the repository root.
        with tempfile.TemporaryDirectory(prefix="jazn-release-staging-") as staging_dir_name:
            staging = Path(staging_dir_name) / "staging"
            staging_report = create_release_staging(root, staging)
            readiness = build_release_readiness_report(staging, profile="export-without-git")
            if not readiness.get("ok"):
                return {
                    "schema_version": schema_version("release_bundle"),
                    "ok": False,
                    "exit_code": int(readiness.get("exit_code", 1)),
                    "output_zip": str(output),
                    "staging": staging_report,
                    "readiness": readiness,
                    "error": "release staging readiness failed",
                }

            # Candidate and final output share a filesystem so the final ZIP can be
            # promoted with os.replace only after all transport checks pass.
            with tempfile.TemporaryDirectory(
                prefix=".jazn-release-output-",
                dir=str(output.parent),
            ) as build_dir_name:
                build_dir = Path(build_dir_name)
                candidate = build_dir / output.name
                export_report = export_package(staging, "system", candidate).to_dict()
                zip_verification = verify_release_zip_manifest(candidate)
                candidate_digest = _sha256_file(candidate)
                candidate_ok = bool(
                    zip_verification.get("ok")
                    and export_report.get("crc_ok")
                    and export_report.get("extract_smoke_ok")
                    and export_report.get("sha256") == candidate_digest
                )
                if not candidate_ok:
                    return {
                        "schema_version": schema_version("release_bundle"),
                        "ok": False,
                        "exit_code": 1,
                        "runtime_version": PACKAGE_VERSION_FULL,
                        "output_zip": str(output),
                        "staging": staging_report,
                        "readiness": readiness,
                        "export": export_report,
                        "zip_manifest_verification": zip_verification,
                        "error": "candidate release ZIP verification failed",
                    }

                candidate_package_manifest = candidate.with_name(candidate.name + ".package_manifest.json")
                candidate_packing_audit = candidate.with_name(candidate.name + ".PACKING_AUDIT.json")
                candidate_report = candidate.with_suffix(".report.json")

                package_manifest_path = _move_optional(
                    candidate_package_manifest,
                    output.with_name(output.name + ".package_manifest.json"),
                )
                packing_audit_path = _move_optional(
                    candidate_packing_audit,
                    output.with_name(output.name + ".PACKING_AUDIT.json"),
                )
                report_path = _move_optional(
                    candidate_report,
                    output.with_suffix(".report.json"),
                )
                # Promote the ZIP last. Until this point an existing release ZIP is
                # untouched even if staging, export, CRC or manifest checks fail.
                os.replace(candidate, output)

                if report_path:
                    persisted_report = {
                        **export_report,
                        "output_zip": str(output),
                        "package_manifest_path": package_manifest_path or "",
                        "packing_audit_path": packing_audit_path or "",
                    }
                    Path(report_path).write_text(
                        json.dumps(persisted_report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

        digest = _sha256_file(output)
        sha_path = output.with_name(output.name + ".sha256")
        sha_temp = sha_path.with_name(sha_path.name + ".tmp")
        sha_temp.write_text(f"{digest}  {output.name}\n", encoding="ascii")
        os.replace(sha_temp, sha_path)

        return {
            "schema_version": schema_version("release_bundle"),
            "ok": True,
            "exit_code": 0,
            "runtime_version": PACKAGE_VERSION_FULL,
            "output_zip": str(output),
            "sha256": digest,
            "sha256_path": str(sha_path),
            "package_manifest_path": package_manifest_path,
            "packing_audit_path": packing_audit_path,
            "report_path": report_path,
            "staging": staging_report,
            "readiness": readiness,
            "export": export_report,
            "zip_manifest_verification": {
                **zip_verification,
                "zip_path": str(output),
                "zip_sha256": digest,
            },
            "truth_boundary": (
                "The release ZIP was built from one clean Git commit. Fresh provenance and "
                "PACKAGE_INTEGRITY_MANIFEST.json exist only in staging/the ZIP and were verified "
                "after transport. The source checkout metadata was not rewritten or promoted."
            ),
        }
    except Exception as exc:
        return {
            "schema_version": schema_version("release_bundle"),
            "ok": False,
            "exit_code": 2,
            "runtime_version": PACKAGE_VERSION_FULL,
            "output_zip": str(output),
            "error": repr(exc),
        }
