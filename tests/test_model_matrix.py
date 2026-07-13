"""Regression net for the model matrix (revamp 2026-07-10).

Every LLM call site's no-flags default must resolve to exactly this matrix. This
is the single place a future revamp updates; if any tool's default drifts, the
matching assertion here fails. The matrix (see the build instructions / STATE.md):

    analyze              codex / gpt-5.6-sol  / high
    checker              claude / opus        / medium
    arbiter (conflict)   codex / gpt-5.6-luna / high
    grouper              claude / haiku       / high
    quote visual verify  codex / gpt-5.6-luna / high     (A/B winner: 17/18 vs sonnet 9/18)
    scout                codex / gpt-5.6-luna / medium
    preflight            codex / gpt-5.6-luna / high
    crosscheck conflict  claude / sonnet      / medium
    reconcile scope gate claude / opus        / medium
    datefill primary     codex / gpt-5.6-luna / high
    datefill cascade     claude / sonnet      / medium
    summarize digest     claude / claude-sonnet-5   / high  (pinned id)
    summarize firmpages  claude / claude-sonnet-4-6 / high  (pinned id)
"""

from __future__ import annotations

import unittest

from src import crosscheck, datefill, preflight, reconcile, scout, summarize
from src.run import build_parser


class RunCliMatrixTest(unittest.TestCase):
    def test_bare_run_resolves_to_the_matrix(self) -> None:
        args = build_parser().parse_args(["--run-id", "x"])
        self.assertEqual(("codex", "gpt-5.6-sol", "high"), (args.engine, args.model, args.effort))
        self.assertEqual(
            ("claude", "opus", "medium"),
            (args.checker_engine, args.checker_model, args.checker_effort),
        )
        self.assertEqual(
            ("codex", "gpt-5.6-luna", "high"),
            (args.arbiter_engine, args.arbiter_model, args.arbiter_effort),
        )
        self.assertEqual(
            ("claude", "haiku", "high"),
            (args.grouper_engine, args.grouper_model, args.grouper_effort),
        )
        self.assertEqual(
            ("codex", "gpt-5.6-luna", "high"),
            (args.quote_visual_engine, args.quote_visual_model, args.quote_visual_effort),
        )


class ScoutMatrixTest(unittest.TestCase):
    def test_defaults(self) -> None:
        self.assertEqual(
            ("codex", "gpt-5.6-luna", "medium"),
            (scout.DEFAULT_ENGINE, scout.DEFAULT_MODEL, scout.DEFAULT_EFFORT),
        )


class PreflightMatrixTest(unittest.TestCase):
    def test_defaults(self) -> None:
        self.assertEqual(
            ("codex", "gpt-5.6-luna", "high"),
            (preflight.DEFAULT_ENGINE, preflight.DEFAULT_MODEL, preflight.DEFAULT_EFFORT),
        )


class CrosscheckMatrixTest(unittest.TestCase):
    def test_bare_conflict_pass_resolves_to_claude_sonnet_medium(self) -> None:
        args = crosscheck.build_parser().parse_args(["--outputs", "a.csv", "--out-dir", "d"])
        model = crosscheck._resolve_model(args.engine, args.model)
        self.assertEqual(("claude", "sonnet", "medium"), (args.engine, model, args.effort))


class ReconcileMatrixTest(unittest.TestCase):
    def test_bare_scope_gate_resolves_to_claude_opus_medium(self) -> None:
        args = reconcile.build_parser().parse_args(["--outputs", "a.csv", "--out-dir", "d"])
        model = reconcile._resolve_model(args.engine, args.model)
        self.assertEqual(("claude", "opus", "medium"), (args.engine, model, args.effort))

    def test_run_reconcile_signature_defaults_match(self) -> None:
        kw = reconcile.run_reconcile.__kwdefaults__
        self.assertEqual("opus", kw["model"])
        self.assertEqual("medium", kw["effort"])

    def test_near_leaf_flag_is_opt_in_and_inherits_the_scope_model(self) -> None:
        # The Phase 3 near-leaf pass adds no independent model default: it is off
        # by default and, when on, reuses run_reconcile's engine/model/effort.
        args = reconcile.build_parser().parse_args(["--outputs", "a.csv", "--out-dir", "d"])
        self.assertFalse(args.near_leaf)
        self.assertFalse(reconcile.run_reconcile.__kwdefaults__["near_leaf"])


class DatefillMatrixTest(unittest.TestCase):
    def test_primary_and_cascade_defaults(self) -> None:
        self.assertEqual(
            ("codex", "gpt-5.6-luna", "high"),
            (
                datefill.DEFAULT_PRIMARY_ENGINE,
                datefill.DEFAULT_PRIMARY_MODEL,
                datefill.DEFAULT_PRIMARY_EFFORT,
            ),
        )
        self.assertEqual(
            ("claude", "sonnet", "medium"),
            (
                datefill.DEFAULT_CASCADE_ENGINE,
                datefill.DEFAULT_CASCADE_MODEL,
                datefill.DEFAULT_CASCADE_EFFORT,
            ),
        )

    def test_primary_codex_model_passes_through_resolution(self) -> None:
        # A bare invocation (engine codex, --model default) keeps the 5.6 model.
        resolved = datefill._resolve_model(
            datefill.DEFAULT_PRIMARY_ENGINE,
            datefill.DEFAULT_PRIMARY_MODEL,
            claude_default=datefill.DEFAULT_CASCADE_MODEL,
        )
        self.assertEqual("gpt-5.6-luna", resolved)


class SummarizeMatrixTest(unittest.TestCase):
    def test_digest_and_firmpages_have_independent_pinned_defaults(self) -> None:
        self.assertEqual("claude-sonnet-5", summarize.PINNED_DIGEST_SONNET_ID)
        self.assertEqual("claude-sonnet-4-6", summarize.PINNED_FIRMPAGE_SONNET_ID)
        self.assertEqual(
            ("claude", "claude-sonnet-5", "high"),
            (summarize.DIGEST_ENGINE, summarize.DIGEST_MODEL, summarize.DIGEST_EFFORT),
        )
        self.assertEqual(
            ("claude", "claude-sonnet-4-6", "high"),
            (summarize.FIRMPAGE_ENGINE, summarize.FIRMPAGE_MODEL, summarize.FIRMPAGE_EFFORT),
        )


if __name__ == "__main__":
    unittest.main()
