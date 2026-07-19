from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "tools" / "jazn_pack_generator.py"

# Deliberately synthetic values: regression tests must never copy the active
# runtime version or release name from latka_jazn/version.py. The repository's
# version-consistency audit correctly rejects such duplicated source-of-truth
# literals, so these fixtures stay independent across future releases.
FIXTURE_VERSION_NUMBER = "91.82.73.64"
FIXTURE_PACKAGE_VERSION = f"v{FIXTURE_VERSION_NUMBER}"
FIXTURE_RELEASE_NAME = "fixture-release"
FIXTURE_VERSION_FULL = f"{FIXTURE_PACKAGE_VERSION}-{FIXTURE_RELEASE_NAME}"


def _load_generator():
    if not GENERATOR_PATH.is_file():
        raise FileNotFoundError(f"canonical package generator is missing: {GENERATOR_PATH}")
    module_name = "jazn_pack_generator_release_version_test"
    spec = importlib.util.spec_from_file_location(module_name, GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_full_version_includes_release_name() -> None:
    generator = _load_generator()
    assert generator.compose_package_version_full(
        FIXTURE_VERSION_NUMBER,
        FIXTURE_RELEASE_NAME,
    ) == f"{FIXTURE_VERSION_NUMBER}-{FIXTURE_RELEASE_NAME}"


def test_full_version_does_not_duplicate_release_name() -> None:
    generator = _load_generator()
    assert generator.compose_package_version_full(
        FIXTURE_VERSION_FULL,
        FIXTURE_RELEASE_NAME,
    ) == f"{FIXTURE_VERSION_NUMBER}-{FIXTURE_RELEASE_NAME}"


def test_manifest_accepts_canonical_full_release_version() -> None:
    generator = _load_generator()
    assert generator.manifest_version_matches(
        FIXTURE_VERSION_FULL,
        FIXTURE_VERSION_NUMBER,
        FIXTURE_RELEASE_NAME,
    )
    assert not generator.manifest_version_matches(
        FIXTURE_VERSION_FULL,
        FIXTURE_VERSION_NUMBER,
        "",
    )
