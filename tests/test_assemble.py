from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from src.assemble import OUTPUT_COLUMNS, assemble_candidates, write_run_outputs
from src.schemas import CandidateCall, CheckVerdict, SourceInfo
from src.taxonomy import Taxonomy


class AssembleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Aberdeen Investments",
                date="3/10/2026",
                source="Emerging Markets Q2 2026 Outlook: Shifting Sands",
                url="https://example.test/source",
            )
        }

    def test_output_columns_are_target_shape_plus_review_fields(self) -> None:
        self.assertEqual(
            (
                "Firm",
                "Date",
                "Source",
                "URL",
                "Sub-Asset Class",
                "Asset Class Category",
                "Canva Groupings",
                "Asset Class",
                "View",
                "Full Commentary",
                "confidence",
                "band",
                "review_flag",
            ),
            OUTPUT_COLUMNS,
        )

    def test_assemble_writes_output_failure_and_manifest(self) -> None:
        candidates = [_candidate(), _candidate(sub_asset_class="Not A Leaf")]
        snapshots = {
            ("source-1", "p1-5"): "EM equities are favored in the outlook. " + "Context. " * 30,
        }

        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=snapshots,
            page_counts={"source-1": 1},
        )

        self.assertEqual(1, len(result.output_rows))
        self.assertEqual(1, len(result.failures))
        with tempfile.TemporaryDirectory() as temp_dir:
            write_run_outputs(result, temp_dir)
            output_rows = _read_csv(Path(temp_dir) / "output.csv")
            failure_rows = _read_csv(Path(temp_dir) / "failures.csv")
            manifest = (Path(temp_dir) / "manifest.md").read_text(encoding="utf-8")

        self.assertEqual("100", output_rows[0]["confidence"])
        self.assertEqual("High", output_rows[0]["band"])
        self.assertEqual("none", output_rows[0]["review_flag"])
        self.assertEqual("taxonomy_no_match", failure_rows[0]["reason_code"])
        self.assertIn("count check: pass", manifest)

    def test_conflicting_duplicate_views_route_to_failures(self) -> None:
        candidates = [_candidate(view="O"), _candidate(view="U")]
        snapshots = {
            ("source-1", "p1-5"): "EM equities are favored in the outlook.",
        }

        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=snapshots,
        )

        self.assertEqual([], result.output_rows)
        self.assertEqual(2, len(result.failures))
        self.assertEqual({"unresolved_conflict"}, {failure.reason_code for failure in result.failures})


class CheckerAndArbiterAssemblyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Aberdeen Investments",
                date="3/10/2026",
                source="Emerging Markets Q2 2026 Outlook: Shifting Sands",
                url="https://example.test/source",
            )
        }
        cls.snapshots = {
            ("source-1", "p1-5"): "EM equities are favored in the outlook. " + "Context. " * 30,
        }

    def test_checker_fail_verdict_routes_to_failures_with_note(self) -> None:
        result = assemble_candidates(
            [_candidate()],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts={"source-1": 1},
            verdicts={0: _verdict(supports_view="fail", note="quote is a market recap")},
        )

        self.assertEqual([], result.output_rows)
        self.assertEqual("checker_sign_mismatch", result.failures[0].reason_code)
        self.assertEqual("quote is a market recap", result.failures[0].message)

    def test_unclear_verdict_caps_row_and_annotates_commentary(self) -> None:
        result = assemble_candidates(
            [_candidate()],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts={"source-1": 1},
            verdicts={0: _verdict(asset_match="unclear", note="broader bucket")},
        )

        row = result.output_rows[0]
        self.assertEqual("74", row["confidence"])
        self.assertEqual("review", row["review_flag"])
        self.assertIn("Checker: unconfirmed (broader bucket).", row["Full Commentary"])

    def test_arbiter_resolves_conflict_and_records_loser(self) -> None:
        candidates = [_candidate(view="O"), _candidate(view="U")]

        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts={"source-1": 1},
            verdicts={0: _verdict(), 1: _verdict()},
            arbiter=lambda group: (0, "published dial wins"),
        )

        self.assertEqual(1, len(result.output_rows))
        row = result.output_rows[0]
        self.assertEqual("O", row["View"])
        self.assertEqual("review", row["review_flag"])
        self.assertIn("Arbiter: published dial wins", row["Full Commentary"])
        self.assertEqual(["arbitrated_out"], [f.reason_code for f in result.failures])
        self.assertEqual("published dial wins", result.failures[0].message)

    def test_arbiter_null_falls_back_to_unresolved_conflict(self) -> None:
        candidates = [_candidate(view="O"), _candidate(view="U")]

        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts={"source-1": 1},
            verdicts={0: _verdict(), 1: _verdict()},
            arbiter=lambda group: (None, "two explicit horizons"),
        )

        self.assertEqual([], result.output_rows)
        self.assertEqual(
            {"unresolved_conflict"}, {failure.reason_code for failure in result.failures}
        )
        self.assertIn("two explicit horizons", result.failures[0].message)


def _verdict(**overrides: object) -> CheckVerdict:
    values = {
        "index": 0,
        "supports_view": "pass",
        "forward_looking": "pass",
        "asset_match": "pass",
        "note": "",
    }
    values.update(overrides)
    return CheckVerdict.from_mapping(values)


def _candidate(**overrides: object) -> CandidateCall:
    values = {
        "source_id": "source-1",
        "chunk_id": "p1-5",
        "sub_asset_raw": "EM equities",
        "sub_asset_class": "Emerging Markets Equities",
        "taxonomy_match": "exact",
        "view": "O",
        "call_language": "explicit",
        "evidence_kind": "prose",
        "evidence_quote": "EM equities are favored in the outlook.",
        "locator": "p.3",
        "reasoning": "The manager favors the asset class.",
        "conflict": False,
    }
    values.update(overrides)
    return CandidateCall.from_mapping(values)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    unittest.main()
