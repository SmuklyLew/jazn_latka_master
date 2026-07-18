from __future__ import annotations

from io import StringIO

from latka_jazn.tools.chat_export_ui import CursorMenu, ScriptedKeySource, explicit_confirmation


def test_cursor_menu_navigation_and_multi_selection() -> None:
    output = StringIO()
    selected = CursorMenu("Tematy", ["a", "b", "c"], multi=True).choose(
        key_source=ScriptedKeySource(["down", "space", "down", "space", "enter"]),
        output=output,
    )
    assert selected == {1, 2}


def test_cursor_menu_escape_and_ctrl_x() -> None:
    output = StringIO()
    assert CursorMenu("Menu", ["a"]).choose(
        key_source=ScriptedKeySource(["escape"]), output=output,
    ) is None
    try:
        CursorMenu("Menu", ["a"]).choose(
            key_source=ScriptedKeySource(["ctrl_x"]), output=output,
        )
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("Ctrl+X must stop the UI")


def test_write_confirmation_requires_exact_nonempty_token() -> None:
    assert explicit_confirmation(lambda _: "", "", token="IMPORTUJ") is False
    assert explicit_confirmation(lambda _: "tak", "", token="IMPORTUJ") is False
    assert explicit_confirmation(lambda _: "IMPORTUJ", "", token="IMPORTUJ") is True
