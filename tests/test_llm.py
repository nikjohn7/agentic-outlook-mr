from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from src.llm import (
    CODEX_MODEL,
    ENGINE_CONFIGS,
    call,
    call_parsed,
    parse_arbitration,
    parse_groups,
    parse_response,
    parse_verdicts,
)
from src.schemas import SchemaError


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


class StepParserTest(unittest.TestCase):
    def test_parse_verdicts_returns_typed_verdicts(self) -> None:
        raw = (
            '{"verdicts": [{"index": 1, "supports_view": "pass", '
            '"forward_looking": "unclear", "asset_match": "pass", '
            '"evidence_strength": "thin", "note": "thin stance"}]}'
        )

        verdicts = parse_verdicts(raw)

        self.assertEqual(1, verdicts[0].index)
        self.assertEqual("thin", verdicts[0].evidence_strength)
        self.assertFalse(verdicts[0].all_pass)
        self.assertEqual([], verdicts[0].failed_questions())

    def test_parse_verdicts_accepts_legacy_missing_evidence_strength(self) -> None:
        raw = (
            '{"verdicts": [{"index": 0, "supports_view": "pass", '
            '"forward_looking": "pass", "asset_match": "pass"}]}'
        )

        verdicts = parse_verdicts(raw)

        self.assertEqual("", verdicts[0].evidence_strength)

    def test_parse_verdicts_rejects_unknown_verdict_value(self) -> None:
        raw = (
            '{"verdicts": [{"index": 0, "supports_view": "maybe", '
            '"forward_looking": "pass", "asset_match": "pass"}]}'
        )

        with self.assertRaises(SchemaError):
            parse_verdicts(raw)

    def test_parse_verdicts_rejects_unknown_evidence_strength(self) -> None:
        raw = (
            '{"verdicts": [{"index": 0, "supports_view": "pass", '
            '"forward_looking": "pass", "asset_match": "pass", '
            '"evidence_strength": "strong"}]}'
        )

        with self.assertRaises(SchemaError):
            parse_verdicts(raw)

    def test_parse_arbitration_accepts_winner_and_null(self) -> None:
        self.assertEqual(
            (1, "published dial wins"),
            parse_arbitration('{"winning_index": 1, "reasoning": "published dial wins"}'),
        )
        self.assertEqual(
            (None, "two horizons"),
            parse_arbitration('{"winning_index": null, "reasoning": "two horizons"}'),
        )

    def test_parse_arbitration_requires_reasoning(self) -> None:
        with self.assertRaises(ValueError):
            parse_arbitration('{"winning_index": 0, "reasoning": ""}')

    def test_parse_groups_returns_groups_and_unmatched_notes(self) -> None:
        raw = (
            '{"groups": [{"source_ids": ["schroders-review", "schroders-outlook"], '
            '"note": "combine the Schroders pair"}], '
            '"unmatched_notes": ["also merge the Fidelity docs"]}'
        )

        groups, unmatched = parse_groups(raw)

        self.assertEqual(
            [(["schroders-review", "schroders-outlook"], "combine the Schroders pair")], groups
        )
        self.assertEqual(["also merge the Fidelity docs"], unmatched)

    def test_parse_groups_rejects_single_member_groups(self) -> None:
        with self.assertRaises(ValueError):
            parse_groups('{"groups": [{"source_ids": ["only-one"], "note": "n"}]}')

    def test_call_parsed_uses_supplied_parser_and_engine_settings(self) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"winning_index": 0, "reasoning": "specific beats general"}',
                stderr="",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            prompt_path = Path(temp_dir) / "prompt.md"
            prompt_path.write_text("Arbitrate.", encoding="utf-8")
            result = call_parsed(
                prompt_path,
                {"sub_asset_class": "Cash"},
                engine="codex",
                effort="medium",
                runner=runner,
                parser=parse_arbitration,
            )

        self.assertEqual((0, "specific beats general"), result.payload)
        self.assertIn('model_reasoning_effort="medium"', commands[0])


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
