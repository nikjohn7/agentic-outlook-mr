from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from src.llm import CODEX_MODEL, ENGINE_CONFIGS, call, parse_response


class LLMTest(unittest.TestCase):
    def test_engines_are_configured_for_swapping(self) -> None:
        self.assertEqual(("claude", "-p"), ENGINE_CONFIGS["claude"].command_prefix)
        self.assertEqual(("codex", "exec"), ENGINE_CONFIGS["codex"].command_prefix)

    def test_claude_command_carries_model_and_effort(self) -> None:
        command = ENGINE_CONFIGS["claude"].command("go", model="fable", effort="high")
        self.assertEqual(["claude", "-p", "--model", "fable", "--effort", "high", "go"], command)

    def test_codex_command_pins_model_and_sets_reasoning_effort(self) -> None:
        command = ENGINE_CONFIGS["codex"].command("go", effort="high")
        self.assertEqual(
            ["codex", "exec", "-m", CODEX_MODEL, "-c", 'model_reasoning_effort="high"', "go"],
            command,
        )

    def test_call_rejects_codex_model_override(self) -> None:
        with self.assertRaises(ValueError):
            call("unused.md", {}, engine="codex", model="o3", runner=lambda c, p: None)

    def test_call_rejects_unknown_effort(self) -> None:
        with self.assertRaises(ValueError):
            call("unused.md", {}, engine="claude", effort="ultra", runner=lambda c, p: None)

    def test_parse_response_accepts_fenced_json(self) -> None:
        candidates, summary = parse_response(f"```json\n{_valid_response()}\n```")

        self.assertEqual("ok", summary)
        self.assertEqual("Emerging Markets Equities", candidates[0].sub_asset_class)

    def test_call_repairs_once_after_invalid_json(self) -> None:
        responses = iter(["not json", _valid_response()])
        commands: list[list[str]] = []

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=next(responses), stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            prompt_path = Path(temp_dir) / "prompt.md"
            prompt_path.write_text("Analyze.", encoding="utf-8")
            result = call(prompt_path, {"chunk_id": "p1-5"}, engine="codex", runner=runner)

        self.assertEqual("codex", result.engine)
        self.assertEqual(2, result.attempts)
        self.assertEqual("codex", commands[0][0])
        self.assertEqual("exec", commands[0][1])


    def test_template_vars_fill_prompt_body_before_dispatch(self) -> None:
        seen: list[str] = []

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            seen.append(prompt)
            return subprocess.CompletedProcess(command, 0, stdout=_valid_response(), stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            prompt_path = Path(temp_dir) / "prompt.md"
            prompt_path.write_text("Taxonomy:\n{{taxonomy}}\nGo.", encoding="utf-8")
            call(
                prompt_path,
                {"chunk_id": "p1-5"},
                engine="claude",
                runner=runner,
                template_vars={"taxonomy": "- Global Equities"},
            )

        self.assertIn("- Global Equities", seen[0])
        self.assertNotIn("{{taxonomy}}", seen[0])


def _valid_response() -> str:
    return """
{
  "summary": "ok",
  "candidates": [
    {
      "source_id": "source-1",
      "chunk_id": "p1-5",
      "sub_asset_raw": "EM equities",
      "sub_asset_class": "Emerging Markets Equities",
      "taxonomy_match": "semantic",
      "view": "O",
      "call_language": "implied",
      "evidence_kind": "prose",
      "evidence_quote": "EM equities are favored in the outlook.",
      "locator": "p.3",
      "reasoning": "The manager favors the asset class.",
      "conflict": false
    }
  ]
}
"""


if __name__ == "__main__":
    unittest.main()
