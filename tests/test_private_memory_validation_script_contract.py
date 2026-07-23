from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "Invoke-JaznPrivateMemoryValidation.ps1"
DOC = ROOT / "docs" / "tools" / "PRIVATE_MEMORY_VALIDATION.md"


def test_private_memory_validation_script_has_fail_closed_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8-sig")
    lowered = text.lower()
    assert "109b6823ac23eefa7174b570b851bae106c04d5f" in text
    assert "merge-base" in text
    assert '"--full"' in text
    assert '"--include-all-sqlite"' in text
    assert '"--table-counts"' in text
    assert '"--hash-files"' in text
    assert "from latka_jazn.tools.memory_rebuild import main" in text
    assert '(Join-Path $Root "tools\\memory_rebuild.py")' not in text
    assert "raw_results_persisted = $false" in text
    assert "private_query_text_persisted = $false" in text
    assert "l3_apply_attempted = $false" in text
    assert "approve-l3-manifest-sha" not in lowered
    assert "issue_59_ready_to_close = $false" in text


def test_private_memory_validation_docs_preserve_truth_boundary() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "nie kopiuje pytań" in text
    assert "nie wywołuje `approve-l3-manifest-sha`" in text
    assert "Issue #59 wolno zamknąć dopiero" in text
    assert "workspace_runtime/private_memory_validation" in text
