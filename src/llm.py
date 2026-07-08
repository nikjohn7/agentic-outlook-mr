"""Swappable headless LLM subprocess adapter."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.schemas import CandidateCall, CheckVerdict, SchemaError


Runner = Callable[[list[str], str], subprocess.CompletedProcess[str]]


# Codex runs are pinned to one model; only reasoning effort varies per task.
CODEX_MODEL = "gpt-5.5"


@dataclass(frozen=True, slots=True)
class EngineConfig:
    name: str
    command_prefix: tuple[str, ...]
    efforts: tuple[str, ...]

    def command(
        self,
        prompt: str,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> list[str]:
        args = list(self.command_prefix)
        if self.name == "codex":
            args += ["-m", CODEX_MODEL]
            if effort is not None:
                args += ["-c", f'model_reasoning_effort="{effort}"']
        else:
            if model is not None:
                args += ["--model", model]
            if effort is not None:
                args += ["--effort", effort]
        args.append(prompt)
        return args


ENGINE_CONFIGS = {
    "claude": EngineConfig("claude", ("claude", "-p"), ("low", "medium", "high", "xhigh", "max")),
    "codex": EngineConfig("codex", ("codex", "exec"), ("minimal", "low", "medium", "high", "xhigh")),
}


@dataclass(frozen=True, slots=True)
class LLMCallResult:
    candidates: list[CandidateCall]
    summary: str
    raw_response: str
    engine: str
    attempts: int


@dataclass(frozen=True, slots=True)
class ParsedCallResult:
    """Result of a call parsed by a step-specific parser (checker, arbiter)."""

    payload: Any
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
    model: str | None = None,
    effort: str | None = None,
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

    model/effort select the underlying model and its reasoning effort. Codex is
    pinned to CODEX_MODEL (passing anything else raises); claude accepts an
    alias or full model name. When omitted, the engine CLI's own default
    applies (the run CLI never omits them; tests may).
    """
    result = call_parsed(
        prompt_file,
        inputs,
        engine=engine,
        model=model,
        effort=effort,
        max_repair_attempts=max_repair_attempts,
        runner=runner,
        template_vars=template_vars,
        parser=parse_response,
    )
    candidates, summary = result.payload
    return LLMCallResult(candidates, summary, result.raw_response, engine, result.attempts)


def call_parsed(
    prompt_file: str | Path,
    inputs: dict[str, Any],
    *,
    engine: str,
    model: str | None = None,
    effort: str | None = None,
    max_repair_attempts: int = 2,
    runner: Runner | None = None,
    template_vars: dict[str, Any] | None = None,
    parser: Callable[[str], Any],
) -> ParsedCallResult:
    """Like ``call`` but with a step-specific response parser (checker/arbiter).

    The parser receives the raw stdout and must raise on contract violations;
    the same JSON repair-retry loop applies.
    """
    config = ENGINE_CONFIGS.get(engine)
    if config is None:
        valid = ", ".join(sorted(ENGINE_CONFIGS))
        raise ValueError(f"unknown LLM engine {engine!r}; expected one of {valid}")
    if engine == "codex" and model not in (None, CODEX_MODEL):
        raise ValueError(f"codex runs are pinned to {CODEX_MODEL}; got {model!r}")
    if effort is not None and effort not in config.efforts:
        valid = ", ".join(config.efforts)
        raise ValueError(f"unknown {engine} effort {effort!r}; expected one of {valid}")

    prompt_path = Path(prompt_file)
    base_prompt = _fill_template(prompt_path.read_text(encoding="utf-8"), template_vars)
    runner = runner or _default_runner
    prompt = _compose_prompt(base_prompt, inputs)
    last_error = ""
    raw_response = ""

    for attempt in range(1, max_repair_attempts + 2):
        completed = runner(config.command(prompt, model=model, effort=effort), prompt)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"{engine} exited with non-zero status")
        raw_response = completed.stdout
        try:
            payload = parser(raw_response)
            return ParsedCallResult(payload, raw_response, engine, attempt)
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


def parse_verdicts(raw_response: str) -> list[CheckVerdict]:
    """Parse the checker's response: {"verdicts": [{index, three verdicts, note}]}."""
    payload = json.loads(_extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("checker response must be a JSON object")
    verdicts_raw = payload.get("verdicts")
    if not isinstance(verdicts_raw, list):
        raise ValueError("checker response must include a verdicts list")
    return [CheckVerdict.from_mapping(item) for item in verdicts_raw]


def parse_groups(raw_response: str) -> tuple[list[tuple[list[str], str]], list[str]]:
    """Parse the group resolver's response: {"groups": [...], "unmatched_notes": [...]}."""
    payload = json.loads(_extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("group resolver response must be a JSON object")
    groups_raw = payload.get("groups")
    if not isinstance(groups_raw, list):
        raise ValueError("group resolver response must include a groups list")
    groups: list[tuple[list[str], str]] = []
    for item in groups_raw:
        if not isinstance(item, dict):
            raise ValueError("each group must be a JSON object")
        source_ids = item.get("source_ids")
        if (
            not isinstance(source_ids, list)
            or len(source_ids) < 2
            or not all(isinstance(sid, str) and sid.strip() for sid in source_ids)
        ):
            raise ValueError("each group needs source_ids: at least two source-id strings")
        note = item.get("note", "")
        if not isinstance(note, str):
            raise ValueError("group note must be a string")
        groups.append((list(source_ids), note))
    unmatched = payload.get("unmatched_notes", [])
    if not isinstance(unmatched, list) or not all(isinstance(line, str) for line in unmatched):
        raise ValueError("unmatched_notes must be a list of strings")
    return groups, unmatched


def parse_arbitration(raw_response: str) -> tuple[int | None, str]:
    """Parse the arbiter's response: {"winning_index": int|null, "reasoning": str}."""
    payload = json.loads(_extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("arbiter response must be a JSON object")
    winning_index = payload.get("winning_index")
    if winning_index is not None and (
        not isinstance(winning_index, int) or isinstance(winning_index, bool) or winning_index < 0
    ):
        raise ValueError("winning_index must be a non-negative integer or null")
    reasoning = payload.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError("arbiter reasoning must be a non-empty string")
    return winning_index, reasoning


def parse_quote_visual_verification(raw_response: str) -> str:
    """Parse quote visual verifier response: {"judgment": "..."}."""
    payload = json.loads(_extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("quote visual verifier response must be a JSON object")
    judgment = payload.get("judgment")
    valid = {"present_verbatim", "present_paraphrase", "absent"}
    if judgment not in valid:
        raise ValueError(
            "quote visual verifier judgment must be one of "
            + ", ".join(sorted(valid))
        )
    return judgment


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
