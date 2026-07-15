from __future__ import annotations

import hashlib
import json
from pathlib import Path

from latka_jazn.bootstrap.chatgpt_recovery import runtime_preflight
from latka_jazn.cli_commands.diagnostics import doctor_payload
from latka_jazn.config import JaznConfig
from latka_jazn.core.package_integrity_manifest import (
    PACKAGE_INTEGRITY_MANIFEST_NAME,
    package_integrity_manifest_status,
    resolve_package_integrity_manifest,
)
from latka_jazn.core.version_source import (
    read_runtime_version_from_version_py,
    read_version_metadata_from_version_py,
    version_checkpoint_matches,
)
from latka_jazn.tools.active_extraction_cache import (
    build_active_runtime_status,
    write_active_runtime_marker,
)
from latka_jazn.tools.runtime_contract_version_normalizer import normalize_runtime_contract_versions
from latka_jazn.tools.version_consistency_audit import SOURCE_OF_TRUTH_FILES, build_audit
from latka_jazn.version import DISTRIBUTION_VERSION, PACKAGE_VERSION, PACKAGE_VERSION_FULL


def _runtime(tmp_path: Path, *, legacy_manifest: bool = False, legacy_version: bool = False) -> Path:
    root = tmp_path / "runtime"
    (root / "latka_jazn").mkdir(parents=True)
    (root / "workspace_runtime").mkdir()
    (root / "latka_jazn/version.py").write_text(
        f'DISTRIBUTION_VERSION = {DISTRIBUTION_VERSION!r}\nPACKAGE_VERSION = {PACKAGE_VERSION!r}\nPACKAGE_RELEASE_NAME = ""\n',
        encoding="utf-8",
    )
    (root / "run.py").write_text("print('ok')\n", encoding="utf-8")
    manifest = {
        "schema_version": f"package_integrity_manifest/{PACKAGE_VERSION}",
        "version": PACKAGE_VERSION_FULL,
        "runtime_version": PACKAGE_VERSION_FULL,
        "package_version": PACKAGE_VERSION_FULL,
        "start_file": "run.py",
        "files": [],
    }
    (root / PACKAGE_INTEGRITY_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    if legacy_manifest:
        (root / "MANIFEST_CURRENT.json").write_text(json.dumps(manifest), encoding="utf-8")
    if legacy_version:
        (root / "VERSION.txt").write_text(PACKAGE_VERSION_FULL + "\n", encoding="utf-8")
    return root


def _manifest_sha(root: Path) -> str:
    return hashlib.sha256((root / PACKAGE_INTEGRITY_MANIFEST_NAME).read_bytes()).hexdigest()


def _write_marker(root: Path, **extra: object) -> Path:
    marker = root / "workspace_runtime/JAZN_ACTIVE_RUNTIME.json"
    payload = {"active_root": str(root.resolve()), "version": PACKAGE_VERSION_FULL, **extra}
    marker.write_text(json.dumps(payload), encoding="utf-8")
    return marker


def test_01_version_py_is_the_only_declared_source() -> None:
    assert SOURCE_OF_TRUTH_FILES == ("latka_jazn/version.py",)


def test_02_version_resolves_without_version_txt(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    assert read_runtime_version_from_version_py(root) == PACKAGE_VERSION_FULL


def test_03_metadata_resolves_release_from_version_py(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    metadata = read_version_metadata_from_version_py(root)
    assert metadata.distribution_version == DISTRIBUTION_VERSION
    assert metadata.package_version_full == PACKAGE_VERSION_FULL


def test_04_missing_legacy_checkpoint_is_compatible(tmp_path: Path) -> None:
    assert version_checkpoint_matches(_runtime(tmp_path)) is True


def test_05_config_compat_property_points_to_primary(tmp_path: Path) -> None:
    cfg = JaznConfig(root=_runtime(tmp_path))
    assert cfg.manifest_current_path.name == PACKAGE_INTEGRITY_MANIFEST_NAME


def test_06_resolver_never_falls_back_to_legacy(tmp_path: Path) -> None:
    root = _runtime(tmp_path, legacy_manifest=True)
    (root / PACKAGE_INTEGRITY_MANIFEST_NAME).unlink()
    assert resolve_package_integrity_manifest(root) is None


def test_07_status_reports_legacy_without_selecting_it(tmp_path: Path) -> None:
    root = _runtime(tmp_path, legacy_manifest=True)
    status = package_integrity_manifest_status(root)
    assert status.source_name == PACKAGE_INTEGRITY_MANIFEST_NAME
    assert status.legacy_present is True


def test_08_preflight_accepts_primary_contract_without_legacy_files(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    marker = _write_marker(root, package_integrity_manifest_sha256=_manifest_sha(root))
    report = runtime_preflight(root, marker_path=marker)
    assert report.ok is True


def test_09_preflight_rejects_missing_primary_manifest(tmp_path: Path) -> None:
    root = _runtime(tmp_path, legacy_manifest=True)
    (root / PACKAGE_INTEGRITY_MANIFEST_NAME).unlink()
    report = runtime_preflight(root)
    assert report.manifest_ok is False


def test_10_preflight_rejects_marker_without_primary_sha(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    marker = _write_marker(root)
    report = runtime_preflight(root, marker_path=marker)
    assert report.marker_ok is False


def test_11_preflight_marks_legacy_marker_sha_for_refresh(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    marker = _write_marker(root, manifest_current_sha256=_manifest_sha(root))
    report = runtime_preflight(root, marker_path=marker)
    assert "marker_legacy_manifest_sha256_requires_refresh" in report.warnings


def test_12_cache_requires_primary_manifest(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    (root / PACKAGE_INTEGRITY_MANIFEST_NAME).unlink()
    status = build_active_runtime_status(root)
    assert status["runtime_root_valid"] is False


def test_13_cache_does_not_emit_legacy_marker_sha(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    status = build_active_runtime_status(root)
    assert "manifest_current_sha256" not in status


def test_14_marker_writer_emits_only_primary_sha(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    marker = write_active_runtime_marker(root)
    assert marker["package_integrity_manifest_sha256"] == _manifest_sha(root)
    assert "manifest_current_sha256" not in marker


def test_15_normalizer_removes_legacy_marker_alias(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    marker = _write_marker(root, manifest_current_sha256=_manifest_sha(root))
    normalize_runtime_contract_versions(root, apply=True)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["package_integrity_manifest_sha256"] == _manifest_sha(root)
    assert "manifest_current_sha256" not in payload


def test_16_audit_rejects_legacy_files(tmp_path: Path) -> None:
    root = _runtime(tmp_path, legacy_manifest=True, legacy_version=True)
    # Minimal metadata files expected by the audit.
    (root / "pyproject.toml").write_text('[project]\ndynamic = ["version"]\n[tool.setuptools.dynamic]\nversion = {attr = "latka_jazn.version.DISTRIBUTION_VERSION"}\n', encoding="utf-8")
    report = build_audit(root)
    kinds = {item["kind"] for item in report["errors"]}
    assert "forbidden_legacy_version_checkpoint_present" in kinds
    assert "forbidden_legacy_manifest_alias_present" in kinds


def test_17_audit_accepts_canonical_files_only(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    (root / "pyproject.toml").write_text('[project]\ndynamic = ["version"]\n[tool.setuptools.dynamic]\nversion = {attr = "latka_jazn.version.DISTRIBUTION_VERSION"}\n', encoding="utf-8")
    report = build_audit(root)
    forbidden = {"forbidden_legacy_version_checkpoint_present", "forbidden_legacy_manifest_alias_present", "package_integrity_manifest_missing"}
    assert not (forbidden & {item["kind"] for item in report["errors"]})


def test_18_doctor_requires_version_py_and_primary_manifest(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    payload = doctor_payload(root)
    assert payload["checks"]["version_py_exists"] is True
    assert payload["package_integrity_checks"]["canonical_source_name"] is True
    assert payload["package_integrity_checks"]["legacy_alias_absent"] is True
