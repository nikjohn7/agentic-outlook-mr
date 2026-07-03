"""Swappable headless LLM subprocess adapter."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.schemas import CandidateCall, SchemaError


Runner = Callable[[list[str], str], subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class EngineConfig:
    name: str
    command_prefix: tuple[str, ...]

    def command(self, prompt: str) -> list[str]:
        return [*self.command_prefix, prompt]


ENGINE_CONFIGS = {
    "claude": EngineConfig("claude", ("claude", "-p")),
    "codex": EngineConfig("codex", ("codex", "exec")),
}


@dataclass(frozen=True, slots=True)
class LLMCallResult:
    candidates: list[CandidateCall]
    summary: str
    raw_response: str
    engine: str
    attempts: int


class LLMParseError(ValueError):
    """Raised when the LLM response remains invalid after repair attempts."""


def call(
    prompt_file: str | Path,
    inputs: dict[str, Any],
    *,
    engine: str,
    max_repair_attempts: int = 2,
    runner: Runner | None = None,
    template_vars: dict[str, Any] | None = None,
) -> LLMCallResult:
    """Call one engine with fresh subprocess context and parse candidate JSON.

    template_vars fill ``{{name}}`` placeholders in the prompt body before the
    machine-readable inputs are appended — the seam for injecting large context
    (locked taxonomy, few-shot brain, rolling memory, the chunk itself) that
    should read as prose rather than escaped JSON. The API port fills the same
    placeholders in the same prompt file.
    """
    config = ENGINE_CONFIGS.get(engine)
    if config is None:
        valid = ", ".join(sorted(ENGINE_CONFIGS))
        raise ValueError(f"unknown LLM engine {engine!r}; expected one of {valid}")

    prompt_path = Path(prompt_file)
    base_prompt = _fill_template(prompt_path.read_text(encoding="utf-8"), template_vars)
    runner = runner or _default_runner
    prompt = _compose_prompt(base_prompt, inputs)
    last_error = ""
    raw_response = ""

    for attempt in range(1, max_repair_attempts + 2):
        completed = runner(config.command(prompt), prompt)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"{engine} exited with non-zero status")
        raw_response = completed.stdout
        try:
            candidates, summary = parse_response(raw_response)
            return LLMCallResult(candidates, summary, raw_response, engine, attempt)
        except (json.JSONDecodeError, SchemaError, TypeError, ValueError) as exc:
            last_error = str(exc)
            prompt = _repair_prompt(base_prompt, inputs, raw_response, last_error)

    raise LLMParseError(f"invalid LLM JSON after repair attempts: {last_error}")


def parse_response(raw_response: str) -> tuple[list[CandidateCall], str]:
    payload = json.loads(_extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    candidates_raw = payload.get("candidates")
    if not isinstance(candidates_raw, list):
        raise ValueError("LLM response must include a candidates list")
    summary = payload.get("summary", "")
    if not isinstance(summary, str):
        raise ValueError("LLM summary must be a string")
    return [CandidateCall.from_mapping(item) for item in candidates_raw], summary


def _default_runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
    # stdin must be closed: `codex exec` (and `claude -p`) treat piped stdin as
    # extra prompt input and block waiting for EOF when the parent's stdin
    # stays open (e.g. under an orchestrator).
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _fill_template(base_prompt: str, template_vars: dict[str, Any] | None) -> str:
    if not template_vars:
        return base_prompt
    for key, value in template_vars.items():
        base_prompt = base_prompt.replace("{{" + key + "}}", str(value))
    return base_prompt


def _compose_prompt(base_prompt: str, inputs: dict[str, Any]) -> str:
    return (
        f"{base_prompt.rstrip()}\n\n"
        "## Machine-readable inputs\n"
        f"{json.dumps(inputs, ensure_ascii=False, indent=2, sort_keys=True)}\n"
    )


def _repair_prompt(
    base_prompt: str,
    inputs: dict[str, Any],
    raw_response: str,
    error: str,
) -> str:
    repair_inputs = {
        "original_inputs": inputs,
        "validation_error": error,
        "previous_response": raw_response,
    }
    return (
        f"{base_prompt.rstrip()}\n\n"
        "The previous response did not satisfy the JSON contract. "
        "Return only corrected JSON.\n\n"
        f"{json.dumps(repair_inputs, ensure_ascii=False, indent=2, sort_keys=True)}\n"
    )


def _extract_json(raw_response: str) -> str:
    stripped = raw_response.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped
