from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

VERSION_MODULE_RELATIVE_PATH = Path("latka_jazn") / "version.py"
VERSION_CHECKPOINT_FILE_NAME = "VERSION.txt"


def version_module_path(root: Path) -> Path:
    return Path(root).expanduser().resolve() / VERSION_MODULE_RELATIVE_PATH


def version_checkpoint_path(root: Path) -> Path:
    return Path(root).expanduser().resolve() / VERSION_CHECKPOINT_FILE_NAME


def _literal_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip()
    return None


def read_version_py_assignments(version_file: Path) -> dict[str, str]:
    """Read literal version metadata from latka_jazn/version.py without importing it."""
    version_file = Path(version_file).expanduser().resolve()
    tree = ast.parse(version_file.read_text(encoding="utf-8-sig"), filename=str(version_file))
    values: dict[str, str] = {}
    for node in tree.body:
        targets: list[str] = []
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value_node = node.value
        value = _literal_str(value_node)
        if value is None:
            continue
        for target in targets:
            values[target] = value
    return values



@dataclass(frozen=True, slots=True)
class VersionMetadata:
    distribution_version: str
    package_version: str
    release_name: str
    package_version_full: str


def read_version_metadata_from_version_py(root: Path) -> VersionMetadata:
    """Read canonical release metadata from ``latka_jazn/version.py``.

    This function never consults VERSION.txt.  VERSION.txt is a human-visible
    checkpoint that must be compared with ``package_version_full`` separately.
    """
    path = version_module_path(root)
    if not path.is_file():
        raise FileNotFoundError(f"Missing canonical version module: {path}")
    values = read_version_py_assignments(path)
    distribution = (values.get("DISTRIBUTION_VERSION") or "").strip()
    package = (values.get("PACKAGE_VERSION") or "").strip()
    release = (values.get("PACKAGE_RELEASE_NAME") or "").strip()
    if not package and distribution:
        package = f"v{distribution}"
    if not distribution and package:
        distribution = package.lstrip("v").split("-", 1)[0]
    if not distribution or not package:
        raise ValueError(
            "latka_jazn/version.py must define literal DISTRIBUTION_VERSION and PACKAGE_VERSION"
        )
    full = f"{package}-{release}" if release else package
    return VersionMetadata(
        distribution_version=distribution,
        package_version=package,
        release_name=release,
        package_version_full=full,
    )


def version_checkpoint_matches(root: Path) -> bool:
    try:
        metadata = read_version_metadata_from_version_py(root)
    except (FileNotFoundError, OSError, SyntaxError, ValueError):
        return False
    return read_version_checkpoint(root) == metadata.package_version_full

def read_runtime_version_from_version_py(root: Path, *, fallback: str | None = None) -> str | None:
    """Return the active runtime/package version from latka_jazn/version.py.

    VERSION.txt is intentionally not used here. That file is a checkpoint/visible
    release marker only; latka_jazn/version.py is the single source of truth for
    runtime code and tools.
    """
    try:
        return read_version_metadata_from_version_py(root).package_version_full
    except (FileNotFoundError, OSError, SyntaxError, ValueError):
        return fallback


def read_version_checkpoint(root: Path) -> str | None:
    path = version_checkpoint_path(root)
    try:
        value = path.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff").strip()
    except FileNotFoundError:
        return None
    return value or None


def version_number(value: str | None) -> str:
    text = str(value or "").strip().lstrip("\ufeff").strip()
    if text.startswith("v"):
        text = text[1:]
    return text.split("-", 1)[0]
