from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "Invoke-JaznPrivateMemoryValidation.ps1"


def test_empty_search_result_collection_is_accepted() -> None:
    text = SCRIPT.read_text(encoding="utf-8-sig")
    assert (
        "[Parameter(Mandatory)][AllowEmptyCollection()]"
        "[System.Collections.Generic.List[string]]$Target"
    ) in text
    assert (
        "Add-ObjectStrings -Value $searchRun.Payload.results -Target $strings"
        in text
    )
    assert '$strings = New-Object "System.Collections.Generic.List[string]"' in text
