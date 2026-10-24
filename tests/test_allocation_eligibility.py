"""Deterministic routing of allocation-eligibility verdicts (prompts v1.9/v1.2).

The prompts (`conventions.md` v1.4, `check_candidates.md` v1.9,
`arbitrate_conflict.md` v1.2) tell the checker to fail an analytical-input
candidate on `forward_looking` with a "Not an allocation stance:" note, and the
arbiter to return null when no candidate in a conflict is a house stance. These
tests pin what the deterministic layer does with those answers. The verdicts
and arbiter answers are synthetic stand-ins: they prove the routing, not that a
model produces them - that is for live validation.
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from src.assemble import assemble_candidates, client_failure_label, write_run_outputs
from src.schemas import CandidateCall, CheckVerdict, SourceInfo
from src.taxonomy import Taxonomy

SOURCE_ID = "kestrel-q3"
CHUNK_ID = "p6-8"
SIGNAL_LOCATOR = "p.6 - Figure 2, 'Momentum signals by market'"
VALUATION_LOCATOR = "p.7 - Figure 3, 'Valuation z-scores'"
ALLOCATION_LOCATOR = "p.8 - Figure 4, 'Final tactical allocation vs neutral'"
NOT_A_STANCE_NOTE = (
    "Not an allocation stance: a cell of a chart captioned as momentum signals; "
    "the source shows its allocation in a separate figure."
)
# Snapshot text that carries the figures' printed words, so table evidence
# passes the deterministic key-token check on its own (no visual route).
SNAPSHOT = (
    "Figure 2 Momentum signals by market. Canada equity index momentum positive. "
    "Figure 3 Valuation z-scores. Canada equity index valuation expensive. "
    "Figure 4 Final tactical allocation vs neutral. Canada equities current 7% "
    "versus neutral 5%. " + "Broader market context. " * 80
)


class AllocationEligibilityRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            SOURCE_ID: SourceInfo(
                source_id=SOURCE_ID,
                firm="Kestrel Multi-Asset",
                date="",
                source="Q3 Multi-Asset Outlook",
                url="",
            )
        }

    def _assemble(self, candidates, verdicts, *, arbiter=None, snapshot=SNAPSHOT, visual_pages=None):
        return assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={(SOURCE_ID, CHUNK_ID): snapshot},
            page_counts={SOURCE_ID: 8},
            visual_pages=visual_pages,
            verdicts=verdicts,
            arbiter=arbiter,
        )

    def test_signal_visual_failing_stance_check_is_excluded_and_audited(self) -> None:
        # Visual route: the snapshot lacks the rendered cell, so only the
        # checker's page-image verdict decides. A stated/explicit_dial reading
        # of a signal chart that fails the stance check is dropped, not kept
        # as a flagged row.
        candidate = _signal(evidence_kind="visual", evidence_quote="Canada index cell is green")
        result = self._assemble(
            [candidate],
            {0: _not_a_stance()},
            snapshot="Text layer without the rendered heatmap. " + "Context. " * 200,
            visual_pages={SOURCE_ID: {6}},
        )

        self.assertEqual([], result.output_rows)
        self.assertEqual(1, len(result.failures))
        failure = result.failures[0]
        self.assertEqual("checker_not_forward_looking", failure.reason_code)
        self.assertIn("checker visual review failed", failure.message)
        self.assertIn(NOT_A_STANCE_NOTE, failure.message)
        self.assertEqual("stated", failure.basis)
        self.assertEqual("explicit_dial", failure.call_language)
        self.assertEqual(SIGNAL_LOCATOR, failure.locator)

        with tempfile.TemporaryDirectory() as temp_dir:
            write_run_outputs(result, temp_dir, sources=self.sources)
            output_rows = _read_csv(Path(temp_dir) / "output.csv")
            internal = _read_csv(Path(temp_dir) / "failures.csv")
            client = _read_csv(Path(temp_dir) / "failures-client.csv")

        self.assertEqual([], output_rows)
        self.assertEqual(["checker_not_forward_looking"], [row["reason_code"] for row in internal])
        self.assertEqual(1, len(client))
        what_happened, _ = client_failure_label("checker_not_forward_looking")
        self.assertEqual(what_happened, client[0]["What happened"])
        self.assertEqual("Canada Equities", client[0]["Sub-Asset Class"])
        self.assertIn("Not an allocation stance:", client[0]["Evidence / notes"])

    def test_text_verified_signal_failure_carries_the_checker_note(self) -> None:
        result = self._assemble([_signal()], {0: _not_a_stance()})

        self.assertEqual([], result.output_rows)
        self.assertEqual("checker_not_forward_looking", result.failures[0].reason_code)
        self.assertEqual(NOT_A_STANCE_NOTE, result.failures[0].message)

    def test_failed_signals_never_reach_arbitration_or_the_kept_row(self) -> None:
        # One eligible allocation-vs-neutral call plus a same-view trend signal
        # and an opposite-view valuation signal on the same leaf. Failing the
        # two signals removes them before conflict grouping: the arbiter is
        # never consulted and the signal evidence is not merged into the row.
        def arbiter(group):
            raise AssertionError("arbiter must not run once the signals fail")

        candidates = [
            _allocation(),
            _signal(view="O"),
            _signal(
                view="U",
                evidence_quote="Canada equity index valuation expensive",
                locator=VALUATION_LOCATOR,
            ),
        ]
        verdicts = {0: _verdict(), 1: _not_a_stance(index=1), 2: _not_a_stance(index=2)}
        result = self._assemble(candidates, verdicts, arbiter=arbiter)

        self.assertEqual(1, len(result.output_rows))
        row = result.output_rows[0]
        self.assertEqual("O", row["View"])
        self.assertEqual("High", row["band"])
        self.assertEqual("none", row["review_flag"])
        self.assertIn("neutral 5%", row["Verbatim Evidence"])
        self.assertNotIn("momentum", row["Full Commentary"].lower())
        self.assertNotIn("valuation", row["Full Commentary"].lower())
        self.assertEqual(
            ["checker_not_forward_looking", "checker_not_forward_looking"],
            [failure.reason_code for failure in result.failures],
        )

    def test_eligible_visual_position_with_passing_verdict_survives(self) -> None:
        candidate = _allocation(
            evidence_kind="visual",
            evidence_quote="Canada equities bar at 7% against the 5% neutral marker",
        )
        result = self._assemble(
            [candidate],
            {0: _verdict()},
            snapshot="Text layer without the rendered bars. " + "Context. " * 200,
            visual_pages={SOURCE_ID: {8}},
        )

        self.assertEqual([], result.failures)
        row = result.output_rows[0]
        self.assertEqual("O", row["View"])
        self.assertEqual("High", row["band"])
        self.assertEqual("none", row["review_flag"])

    def test_genuinely_ambiguous_visual_keeps_review_treatment(self) -> None:
        note = "The caption does not say whether this grid is the house view or a model input."
        result = self._assemble(
            [_signal(locator="p.6 - Figure 2, 'Market scores'")],
            {0: _verdict(forward_looking="unclear", evidence_strength="thin", note=note)},
        )

        self.assertEqual([], result.failures)
        row = result.output_rows[0]
        self.assertLessEqual(int(row["confidence"]), 74)
        self.assertEqual("Medium", row["band"])
        self.assertEqual("review", row["review_flag"])
        self.assertIn(f"Checker: unconfirmed ({note})", row["Full Commentary"])

    def test_arbiter_null_for_no_eligible_stance_keeps_no_position(self) -> None:
        # Two ambiguous signal readings survive the checker as unclear and
        # disagree. The arbiter's rule-0 null keeps neither: no position is
        # manufactured from disagreeing inputs.
        reasoning = (
            "Rule 0: index 0 is a momentum-signal cell and index 1 a valuation "
            "z-score, neither an allocation stance."
        )
        candidates = [
            _signal(view="O"),
            _signal(
                view="U",
                evidence_quote="Canada equity index valuation expensive",
                locator=VALUATION_LOCATOR,
            ),
        ]
        unclear = "Caption leaves open whether this is the house view."
        verdicts = {
            0: _verdict(forward_looking="unclear", note=unclear),
            1: _verdict(index=1, forward_looking="unclear", note=unclear),
        }
        result = self._assemble(candidates, verdicts, arbiter=lambda group: (None, reasoning))

        self.assertEqual([], result.output_rows)
        self.assertEqual(
            ["unresolved_conflict", "unresolved_conflict"],
            [failure.reason_code for failure in result.failures],
        )
        self.assertIn(reasoning, result.failures[0].message)

    def test_arbiter_keeps_the_eligible_stance_and_audits_the_signal(self) -> None:
        reasoning = "Rule 0: index 1 is a momentum-signal cell, not an allocation stance."
        candidates = [
            _allocation(view="U", evidence_quote="Canada equities current 4% versus neutral 5%"),
            _signal(view="O"),
        ]
        verdicts = {
            0: _verdict(),
            1: _verdict(index=1, forward_looking="unclear", note="Caption is ambiguous."),
        }
        result = self._assemble(candidates, verdicts, arbiter=lambda group: (0, reasoning))

        self.assertEqual(1, len(result.output_rows))
        row = result.output_rows[0]
        self.assertEqual("U", row["View"])
        self.assertEqual("review", row["review_flag"])
        self.assertIn(f"Arbiter: {reasoning}", row["Full Commentary"])
        self.assertEqual(["arbitrated_out"], [failure.reason_code for failure in result.failures])
        self.assertEqual(reasoning, result.failures[0].message)
        self.assertEqual(SIGNAL_LOCATOR, result.failures[0].locator)

    def test_inferred_product_claim_failing_stance_check_is_excluded(self) -> None:
        quote = "Our multi-strategy fund has delivered positive returns in nine of the last ten years."
        candidate = _candidate(
            sub_asset_raw="multi-strategy fund",
            sub_asset_class="Multi-Strategy Hedge Funds",
            taxonomy_match="semantic",
            call_language="implied",
            evidence_kind="prose",
            evidence_quote=quote,
            locator="p.7",
            basis="inferred",
            reasoning="Strong product performance implies a positive view on the asset class.",
        )
        note = "Not an allocation stance: a performance claim about the firm's own fund."
        result = self._assemble(
            [candidate],
            {0: _verdict(forward_looking="fail", note=note)},
            snapshot=quote + " " + SNAPSHOT,
        )

        self.assertEqual([], result.output_rows)
        failure = result.failures[0]
        self.assertEqual("checker_not_forward_looking", failure.reason_code)
        self.assertEqual("inferred", failure.basis)
        self.assertEqual(note, failure.message)


def _candidate(**overrides: object) -> CandidateCall:
    values = {
        "source_id": SOURCE_ID,
        "chunk_id": CHUNK_ID,
        "sub_asset_raw": "Canada equity index",
        "sub_asset_class": "Canada Equities",
        "taxonomy_match": "semantic",
        "view": "O",
        "call_language": "explicit_dial",
        "evidence_kind": "table",
        "evidence_quote": "Canada equity index momentum positive",
        "locator": SIGNAL_LOCATOR,
        "reasoning": "The cell is green.",
        "conflict": False,
        "basis": "stated",
    }
    values.update(overrides)
    return CandidateCall.from_mapping(values)


def _signal(**overrides: object) -> CandidateCall:
    return _candidate(**overrides)


def _allocation(**overrides: object) -> CandidateCall:
    values: dict[str, object] = {
        "sub_asset_raw": "Canada equities",
        "taxonomy_match": "exact",
        "evidence_quote": "Canada equities current 7% versus neutral 5%",
        "locator": ALLOCATION_LOCATOR,
        "reasoning": "The final allocation holds Canada equities above the stated neutral weight.",
    }
    values.update(overrides)
    return _candidate(**values)


def _verdict(**overrides: object) -> CheckVerdict:
    values = {
        "index": 0,
        "supports_view": "pass",
        "forward_looking": "pass",
        "asset_match": "pass",
        "evidence_strength": "decisive",
        "note": "",
    }
    values.update(overrides)
    return CheckVerdict.from_mapping(values)


def _not_a_stance(index: int = 0) -> CheckVerdict:
    return _verdict(index=index, forward_looking="fail", note=NOT_A_STANCE_NOTE)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    unittest.main()
