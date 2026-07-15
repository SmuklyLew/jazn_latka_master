from __future__ import annotations


def legacy_args(command: str, remainder: list[str]) -> list[str]:
    mapping = {
        "start": ["--daemon-start"],
        "stop": ["--daemon-stop"],
        "chat": ["--chat"],
        "chat-gpt": ["--chat-gpt"],
    }
    return mapping[command] + list(remainder)
