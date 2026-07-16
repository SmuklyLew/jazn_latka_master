from __future__ import annotations

from pathlib import Path
import ast
import inspect
import json

import pytest

from latka_jazn.core import self_knowledge_contract, startup_contract
from latka_jazn.core.memory_search_planner import MemorySearchPlanner
from latka_jazn.packaging.package_profiles import PackageProfileError, load_package_profiles


ROOT = Path(__file__).resolve().parents[1]

ACTIVE_GENERATORS = (
    ROOT / "latka_jazn" / "memory" / "auto_memory_update.py",
    ROOT / "latka_jazn" / "tools" / "dedup_manifest.py",
    ROOT / "latka_jazn" / "integrations" / "github_repository_plan.py",
)


def _legacy_write_calls(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        rendered = ast.unparse(node)
        if "VERSION.txt" not in rendered and "MANIFEST_CURRENT.json" not in rendered:
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else function.id if isinstance(function, ast.Name) else ""
        if name in {"write_text", "touch"}:
            findings.append(rendered)
        elif name == "open":
            mode_index = 0 if isinstance(function, ast.Attribute) else 1
            mode = (
                ast.literal_eval(node.args[mode_index])
                if len(node.args) > mode_index and isinstance(node.args[mode_index], ast.Constant)
                else ""
            )
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = keyword.value.value
            if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
                findings.append(rendered)
    return findings


def test_active_bootstrap_contract_has_only_canonical_release_sources_and_commands() -> None:
    path = ROOT / "latka_jazn" / "resources" / "canon" / "LATKA_SELF_KNOWLEDGE_CONTRACT.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    checklist = payload["post_update_bootstrap"]
    assert checklist == [
        "read latka_jazn/version.py",
        "read PACKAGE_INTEGRITY_MANIFEST.json",
        "read workspace_runtime/JAZN_ACTIVE_RUNTIME.json when present and verify it",
        "run python -X utf8 run.py status --json",
        "run python -X utf8 run.py doctor --json",
        'run python -X utf8 run.py chat-gpt -- "<wiadomość>"',
    ]
    active_text = path.read_text(encoding="utf-8")
    assert "VERSION.txt" not in active_text
    assert "MANIFEST_CURRENT.json" not in active_text
    assert "--chat-gpt-final-only" not in active_text


def test_self_check_uses_canonical_fields_and_legacy_alias_is_diagnostic_only() -> None:
    source = inspect.getsource(startup_contract.build_self_check)
    assert "'version_py_present'" in source
    assert "'package_integrity_manifest_present'" in source
    assert "'legacy_manifest_current_present'" in source
    assert "'manifest_current_present':" not in source


def test_active_code_does_not_recommend_legacy_release_sources() -> None:
    active_paths = [
        ROOT / "latka_jazn" / "core" / "self_knowledge_contract.py",
        ROOT / "latka_jazn" / "core" / "runtime_activation_cascade.py",
        ROOT / "latka_jazn" / "core" / "runtime_response_synthesizer.py",
        ROOT / "latka_jazn" / "resources" / "startup_contract_v14_8_2_4.json",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in active_paths)
    assert "VERSION.txt" not in combined
    assert "MANIFEST_CURRENT.json" not in combined
    assert "--chat-gpt-final-only" not in combined


def test_historical_update_docs_are_not_loaded_as_active_contract_content() -> None:
    loader_source = inspect.getsource(self_knowledge_contract.load_self_knowledge_contract)
    assert "docs/update_history" not in loader_source
    assert "SELF_KNOWLEDGE_RESOURCE" in loader_source


def test_active_generators_neither_write_nor_plan_legacy_release_files() -> None:
    for path in ACTIVE_GENERATORS:
        source = path.read_text(encoding="utf-8")
        assert _legacy_write_calls(path) == [], path
        assert "VERSION.txt" not in source, path
        assert "MANIFEST_CURRENT.json" not in source, path


def test_config_has_no_canonical_manifest_current_alias() -> None:
    path = ROOT / "latka_jazn" / "config.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    properties = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "package_integrity_manifest_path" in properties
    assert "legacy_manifest_current_path" in properties
    assert "manifest_current_path" not in properties


def test_active_update_checkpoints_use_only_canonical_release_sources() -> None:
    auto_update = (ROOT / "latka_jazn" / "memory" / "auto_memory_update.py").read_text(encoding="utf-8")
    repository_plan = (ROOT / "latka_jazn" / "integrations" / "github_repository_plan.py").read_text(encoding="utf-8")
    assert '"latka_jazn/version.py"' in auto_update
    assert '"latka_jazn/version.py"' in repository_plan
    assert '"PACKAGE_INTEGRITY_MANIFEST.json"' in repository_plan
    assert "MANIFEST_*.json" not in repository_plan


def test_active_package_profiles_use_only_canonical_release_sources() -> None:
    profiles = load_package_profiles(ROOT)
    assert {"system", "memory", "nlp", "full", "github_source_safe"}.issubset(profiles)
    includes_rendered = json.dumps(
        {name: profile.includes for name, profile in profiles.items()},
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "VERSION.txt" not in includes_rendered
    assert "MANIFEST_CURRENT.json" not in includes_rendered
    assert "PACKAGE_INTEGRITY_MANIFEST.json" in includes_rendered
    assert "VERSION.txt" in profiles["full"].excludes
    assert "MANIFEST_CURRENT.json" in profiles["full"].excludes
    assert "latka_jazn/**" in profiles["system"].includes
    assert "workspace_runtime/**" in profiles["system"].excludes
    assert "memory/**" in profiles["system"].excludes


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "zip_package_profiles/legacy"),
        ("version_source", "VERSION.txt"),
        ("manifest_source", "MANIFEST_CURRENT.json"),
    ],
)
def test_package_profiles_reject_noncanonical_contract_metadata(
    tmp_path: Path, field: str, value: str
) -> None:
    resource = tmp_path / "latka_jazn" / "resources" / "zip_package_profiles.json"
    resource.parent.mkdir(parents=True)
    payload = {
        "schema_version": "zip_package_profiles/v1",
        "version_source": "latka_jazn/version.py",
        "manifest_source": "PACKAGE_INTEGRITY_MANIFEST.json",
        "profiles": [
            {
                "name": "safe",
                "includes": ["run.py"],
                "excludes": [],
                "purpose": "test",
            }
        ],
    }
    payload[field] = value
    resource.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PackageProfileError):
        load_package_profiles(tmp_path)


def test_package_profiles_fail_closed_on_legacy_and_traversal(tmp_path: Path) -> None:
    resource = tmp_path / "latka_jazn" / "resources" / "zip_package_profiles.json"
    resource.parent.mkdir(parents=True)
    resource.write_text(
        json.dumps(
            {
                "schema_version": "zip_package_profiles/v1",
                "version_source": "latka_jazn/version.py",
                "manifest_source": "PACKAGE_INTEGRITY_MANIFEST.json",
                "profiles": [
                    {
                        "name": "unsafe",
                        "includes": ["../secret", "VERSION.txt"],
                        "excludes": [],
                        "purpose": "test",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PackageProfileError):
        load_package_profiles(tmp_path)


@pytest.mark.parametrize(
    "unsafe_pattern",
    [
        "C:/secret",
        "//server/share",
        "safe//ambiguous",
        "file.txt:stream",
    ],
)
def test_package_profiles_reject_windows_and_ambiguous_paths(
    tmp_path: Path, unsafe_pattern: str
) -> None:
    resource = tmp_path / "latka_jazn" / "resources" / "zip_package_profiles.json"
    resource.parent.mkdir(parents=True)
    resource.write_text(
        json.dumps(
            {
                "schema_version": "zip_package_profiles/v1",
                "version_source": "latka_jazn/version.py",
                "manifest_source": "PACKAGE_INTEGRITY_MANIFEST.json",
                "profiles": [
                    {
                        "name": "unsafe",
                        "includes": [unsafe_pattern],
                        "excludes": [],
                        "purpose": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PackageProfileError):
        load_package_profiles(tmp_path)


def test_broad_package_profile_must_explicitly_exclude_legacy_files(tmp_path: Path) -> None:
    resource = tmp_path / "latka_jazn" / "resources" / "zip_package_profiles.json"
    resource.parent.mkdir(parents=True)
    resource.write_text(
        json.dumps(
            {
                "schema_version": "zip_package_profiles/v1",
                "version_source": "latka_jazn/version.py",
                "manifest_source": "PACKAGE_INTEGRITY_MANIFEST.json",
                "profiles": [
                    {
                        "name": "unsafe_full",
                        "includes": ["**"],
                        "excludes": ["VERSION.txt"],
                        "purpose": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PackageProfileError, match="MANIFEST_CURRENT.json"):
        load_package_profiles(tmp_path)


def test_active_memory_search_topic_resource_is_loaded() -> None:
    planner = MemorySearchPlanner(ROOT)
    assert planner.resource_status["status"] == "loaded"
    assert planner.resource_status["topic_count"] == len(planner.topics)
    assert "latka_identity_runtime" in planner.topics
    plan = planner.plan("Przypomnij sobie wszystko o domu i piosenkach")
    assert plan.routing_notes[0] == "topic_resource=loaded"


def test_invalid_memory_search_topic_resource_falls_back_with_diagnostic(tmp_path: Path) -> None:
    resource = tmp_path / "latka_jazn" / "resources" / "memory_search_topics_v14_6_10.json"
    resource.parent.mkdir(parents=True)
    resource.write_text(
        '{"schema_version":"memory_search_topics/v14.6.10","topics":["not-an-object"]}',
        encoding="utf-8",
    )
    planner = MemorySearchPlanner(tmp_path)
    assert planner.resource_status["status"] == "fallback_invalid_resource"
    assert "ValueError" in planner.resource_status["error"]
    assert planner.topics
    assert planner.plan("Pamiętasz dom?").routing_notes[0] == "topic_resource=fallback_invalid_resource"


def test_memory_search_topic_schema_mismatch_falls_back_with_diagnostic(tmp_path: Path) -> None:
    resource = tmp_path / "latka_jazn" / "resources" / "memory_search_topics_v14_6_10.json"
    resource.parent.mkdir(parents=True)
    resource.write_text(
        '{"schema_version":"memory_search_topics/legacy","topics":[]}',
        encoding="utf-8",
    )
    planner = MemorySearchPlanner(tmp_path)
    assert planner.resource_status["status"] == "fallback_invalid_resource"
    assert "schema" in planner.resource_status["error"]
    assert planner.topics
