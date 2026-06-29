#!/usr/bin/env python3
"""Require project .venv for Python-related shell commands."""

from __future__ import annotations

import json
import re
import shlex
import sys


PYTHON_RELATED = {
    "python",
    "python3",
    "pip",
    "pip3",
    "pytest",
    "py.test",
    "alembic",
    "uvicorn",
    "fastapi",
}

CONTROL_WORDS = {
    "&&",
    "||",
    ";",
    "|",
    "(",
    ")",
}


def read_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}


def command_from_payload(payload: dict) -> str:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command") or tool_input.get("cmd")
        if isinstance(command, str):
            return command

    command = payload.get("command")
    return command if isinstance(command, str) else ""


def is_python_related_token(token: str) -> bool:
    base = token.rsplit("/", 1)[-1]
    if base in PYTHON_RELATED:
        return True
    return bool(re.fullmatch(r"python3?(\.\d+)?", base))


def has_venv_context(command: str, tokens: list[str]) -> bool:
    if ".venv/bin/activate" in command:
        return True
    if "VIRTUAL_ENV=.venv" in command or "VIRTUAL_ENV=$PWD/.venv" in command:
        return True
    return any(token.startswith(".venv/bin/") or "/.venv/bin/" in token for token in tokens)


def command_needs_venv(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token in CONTROL_WORDS or ("=" in token and not token.startswith(("/", "."))):
            continue

        previous = tokens[index - 1] if index else ""
        if previous in {"source", "."}:
            continue

        if is_python_related_token(token):
            return True

    return False


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def main() -> int:
    command = command_from_payload(read_payload())
    if not command:
        return 0

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    if command_needs_venv(tokens) and not has_venv_context(command, tokens):
        deny(
            "Python-related commands in this project must use the local venv. "
            "Run with `source .venv/bin/activate && ...` or use `.venv/bin/<tool>`."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
