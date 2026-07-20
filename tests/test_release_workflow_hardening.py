from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
UPLOAD_ARTIFACT_V701_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"


def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_release_workflows_do_not_hardcode_previous_release_version() -> None:
    for name in ("release-hardening.yml", "release-metadata-sync.yml"):
        text = _read(name)
        assert "v15.1.0.1" not in text
        assert "update/v15.1.0.1" not in text


def test_release_workflows_use_dynamic_branch_families() -> None:
    for name in ("release-hardening.yml", "release-metadata-sync.yml"):
        text = _read(name)
        assert "update/*|tools/upgrade-*|hotfix/*|upgrade/*|fix/*" in text
        assert "PACKAGE_VERSION_FULL" in text


def test_release_workflows_minimize_default_permissions() -> None:
    for name in ("release-hardening.yml", "release-metadata-sync.yml"):
        text = _read(name)
        assert "permissions:\n  contents: read" in text
        assert "permissions:\n      contents: write" in text


def test_release_workflows_serialize_metadata_writers_without_cancellation() -> None:
    expected_group = (
        "group: jazn-release-metadata-${{ github.event_name }}-"
        "${{ github.head_ref || github.ref_name }}"
    )
    for name in ("release-hardening.yml", "release-metadata-sync.yml"):
        text = _read(name)
        assert expected_group in text
        assert "cancel-in-progress: false" in text


def test_upload_artifact_is_pinned_to_verified_full_sha() -> None:
    text = _read("release-hardening.yml")
    refs = re.findall(r"uses:\s*actions/upload-artifact@([^\s#]+)", text)
    assert refs
    assert refs == [UPLOAD_ARTIFACT_V701_SHA, UPLOAD_ARTIFACT_V701_SHA]
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in refs)


def test_release_finalization_paths_are_version_dynamic() -> None:
    text = _read("release-hardening.yml")
    assert "JAZN_RELEASE_VERSION: ${{ needs.manifest_sync.outputs.runtime_version }}" in text
    assert 'f"jazn-{os.environ[\'JAZN_RELEASE_VERSION\']}-release"' in text
    assert 'staging="$RUNNER_TEMP/jazn-${JAZN_RELEASE_VERSION}-release"' in text
    assert "activation_prerequisites_ready" in text
