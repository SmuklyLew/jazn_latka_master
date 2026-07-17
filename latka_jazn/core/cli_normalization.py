from __future__ import annotations

import argparse
from collections.abc import Sequence


_OPTION_ALIASES = {
    "--chatgpt": "--chat-gpt",
    "--chat_gpt": "--chat-gpt",
}


def normalize_cli_argv(argv: Sequence[str], parser: argparse.ArgumentParser) -> list[str]:
    """Normalize known option names without modifying values or user text.

    Only tokens before the standalone ``--`` separator are considered. Known
    option names are matched case-insensitively. Values, paths, session IDs,
    model names and the message after ``--`` are preserved exactly.
    """

    canonical: dict[str, str] = {}
    for action in parser._actions:
        for option in action.option_strings:
            canonical[option.casefold()] = option
    canonical.update(_OPTION_ALIASES)

    normalized: list[str] = []
    after_separator = False
    for token in argv:
        if after_separator:
            normalized.append(token)
            continue
        if token == "--":
            normalized.append(token)
            after_separator = True
            continue
        if not token.startswith("-"):
            normalized.append(token)
            continue

        option, separator, value = token.partition("=")
        replacement = canonical.get(option.casefold())
        if replacement is None:
            normalized.append(token)
            continue
        normalized.append(replacement + (separator + value if separator else ""))
    return normalized
