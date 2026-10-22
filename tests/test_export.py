from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.csvio import read_csv, write_csv
from src.export import (
    CLIENT_COLUMNS,
    annotate_nearleaf_winners,
    client_rows,
    export,
    merge_failure_rows,
    normalize_row,
    recompute_review_flags,
    resolve_shared_keys,
)


def _legacy_row(**extra) -> dict[str, str]:
    base = {
        "Firm": "Wellington Management", "Date": "29/06/2026", "Source": "Equity Market Outlook",
        "URL": "https://x", "Sub-Asset Class": "Asia Equities", "Asset Class Category": "Equities - Region",
        "Canva Groupings": "Equities", "Asset Class": "Equities", "View": "O",
        "Full Commentary": "Wellington favours Asia. Evidence: We favour Asia ex Japan. Locator: char:0-8000. Checker: not run.",
        "confidence": "82", "band": "High", "review_flag": "none", "basis": "stated",
        "checker_strength": "decisive", "call_language": "explicit_stance", "quote_match": "exact",
    }
    base.update(extra)
    return base


class NormalizeTest(unittest.TestCase):
    def test_legacy_row_gains_structured_columns_and_blank_html_locator(self) -> None:
        row = normalize_row(_legacy_row())
        self.assertEqual("Wellington favours Asia.", row["Reasoning"])
        self.assertEqual("We favour Asia ex Japan", row["Verbatim Evidence"])
        self.assertEqual("", row["Locator"])
        self.assertEqual("Checker: not run.", row["Reviewer Notes"])
        # Full Commentary is re-rendered without the char: locator clause.
        self.assertEqual(
            "Wellington favours Asia. Evidence: We favour Asia ex Japan. Checker: not run.",
            row["Full Commentary"],
        )


class SharedKeyTest(unittest.TestCase):
    def test_conflicting_pair_resolves_by_recency_with_a_superseded_failure(self) -> None:
        rows = [
            normalize_row(_legacy_row()),
            normalize_row(_legacy_row(View="N", Source="Asset Allocation Outlook", Date="08/07/2026")),
            normalize_row(_legacy_row(**{"Sub-Asset Class": "Japan Equities"})),
        ]
        resolved, failures = resolve_shared_keys(rows)
        self.assertEqual(["N", "O"], [r["View"] for r in resolved])
        kept = resolved[0]
        self.assertEqual("review", kept["review_flag"])
        self.assertIn("Also held by this firm: O in 'Equity Market Outlook' (29/06/2026) on the same asset", kept["Reviewer Notes"])
        self.assertIn("kept by recency", kept["Reviewer Notes"])
        self.assertEqual(1, len(failures))
        self.assertEqual("Superseded — a newer document from this firm updated this view", failures[0]["What happened"])
        self.assertEqual(("O", "Equity Market Outlook"), (failures[0]["View (proposed)"], failures[0]["Source"]))
        # Idempotent: a second pass sees the note and leaves things alone.
        again, more = resolve_shared_keys(resolved)
        self.assertEqual((2, 0), (len(again), len(more)))

    def test_same_view_pair_merges_citation_preserving(self) -> None:
        rows = [normalize_row(_legacy_row()), normalize_row(_legacy_row(Source="Mid-Year Outlook", confidence="70"))]
        resolved, failures = resolve_shared_keys(rows)
        self.assertEqual(1, len(resolved))
        merged = normalize_row(resolved[0])
        self.assertEqual("Equity Market Outlook | Mid-Year Outlook", merged["Source"])
        self.assertIn("Equity Market Outlook: We favour Asia ex Japan  ||||  Mid-Year Outlook: We favour Asia ex Japan", merged["Verbatim Evidence"])
        self.assertIn("merged to keep one row per asset", merged["Reviewer Notes"])
        self.assertEqual("Merged — same view stated in several documents", failures[0]["What happened"])

    def test_tie_keeps_both_flagged_and_noted(self) -> None:
        rows = [normalize_row(_legacy_row()), normalize_row(_legacy_row(View="U", Source="Other", Date="29/06/2026"))]
        resolved, failures = resolve_shared_keys(rows)
        self.assertEqual(2, len(resolved))
        self.assertEqual([], failures)
        self.assertEqual({"review"}, {r["review_flag"] for r in resolved})
        self.assertIn("a human should decide", resolved[0]["Reviewer Notes"])

    def test_firm_normalization_joins_variants(self) -> None:
        rows = [normalize_row(_legacy_row()), normalize_row(_legacy_row(Firm="Wellington Management ", View="U", Date="30/06/2026"))]
        resolved, failures = resolve_shared_keys(rows)
        self.assertEqual((1, 1), (len(resolved), len(failures)))


class ReviewFlagPolicyTest(unittest.TestCase):
    def test_caps_are_cleared_judgments_and_uncertain_stay(self) -> None:
        rows = [
            normalize_row(_legacy_row(review_flag="review", confidence="74", band="Medium", basis="inferred",
                                      **{"Full Commentary": "R. Evidence: Q. Locator: p.1. Basis: inferred — single-step analyst inference."})),
            normalize_row(_legacy_row(review_flag="review", **{"Full Commentary": "R. Evidence: Q. Locator: p.1. Arbiter: kept this one."})),
            normalize_row(_legacy_row(review_flag="none", View="UNCERTAIN")),
            normalize_row(_legacy_row(review_flag="review", confidence="40", band="Low")),
            normalize_row(_legacy_row(review_flag="review", **{"Full Commentary": "R. Evidence: Q. Locator: p.1."})),
        ]
        changed = recompute_review_flags(rows)
        self.assertEqual(["none", "review", "review", "strong_review", "none"], [r["review_flag"] for r in rows])
        self.assertEqual(4, changed)


class NearLeafAnnotationTest(unittest.TestCase):
    def test_winners_get_the_merge_note_from_the_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audit = Path(temp_dir) / "audit.csv"
            write_csv(audit, ("Firm", "original_leaf", "canonical_leaf", "mapping_action"), [
                {"Firm": "Wellington Management", "original_leaf": "Asia Equities", "canonical_leaf": "Asia Equities", "mapping_action": "winner"},
                {"Firm": "Wellington Management", "original_leaf": "Asia ex-Japan Equities", "canonical_leaf": "Asia Equities", "mapping_action": "merged"},
                {"Firm": "Wellington Management", "original_leaf": "Japan Equities", "canonical_leaf": "", "mapping_action": "kept"},
            ])
            rows = [normalize_row(_legacy_row(review_flag="review")), normalize_row(_legacy_row(**{"Sub-Asset Class": "Japan Equities"}))]
            self.assertEqual(1, annotate_nearleaf_winners(rows, audit))
            self.assertIn("Merged from related assets: 'Asia ex-Japan Equities'", rows[0]["Reviewer Notes"])
            self.assertNotIn("Merged from", rows[1]["Reviewer Notes"])
            recompute_review_flags(rows)
            self.assertEqual("review", rows[0]["review_flag"])  # the note keeps the flag explained


class ClientRowsTest(unittest.TestCase):
    def test_projection_drops_diagnostics_and_blob(self) -> None:
        projected, failures, stats = client_rows([_legacy_row()])
        self.assertEqual(list(CLIENT_COLUMNS), list(projected[0]))
        for column in ("Full Commentary", "basis", "checker_strength", "call_language", "quote_match"):
            self.assertNotIn(column, projected[0])
        self.assertEqual((1, 1, 1), (stats["rows_in"], stats["rows_out"], stats["blank_locators"]))
        self.assertEqual([], failures)
        self.assertEqual("review", projected[0]["review_flag"])  # "Checker: not run" is a judgment gap

    def test_merge_failure_rows_keeps_label_block_order(self) -> None:
        existing = [{"What happened": "Duplicate — already covered"}, {"What happened": "Document could not be ingested"}]
        added = [{"What happened": "Superseded — a newer document from this firm updated this view"}]
        merged = merge_failure_rows(existing, added)
        self.assertEqual(
            ["Document could not be ingested", "Superseded — a newer document from this firm updated this view", "Duplicate — already covered"],
            [r["What happened"] for r in merged],
        )

    def test_export_writes_csv_xlsx_and_report_and_folds_in_losers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            src = Path(temp_dir) / "output.csv"
            write_csv(src, tuple(_legacy_row()), [_legacy_row(), _legacy_row(View="U", Date="30/06/2026", Source="Later")])
            fail = Path(temp_dir) / "failures-client.csv"
            write_csv(fail, ("Firm", "Source", "Sub-Asset Class", "View (proposed)", "What happened", "Explanation", "Evidence / notes"),
                      [{"Firm": "F", "Source": "S", "Sub-Asset Class": "X", "View (proposed)": "O", "What happened": "Duplicate — already covered", "Explanation": "e", "Evidence / notes": "n\nm"}])
            out = Path(temp_dir) / "client"
            report = export(src, out, failures_csv=fail)
            self.assertEqual((2, 1, 1), (report["rows_in"], report["rows_out"], report["shared_key_failure_rows"]))
            for name in ("output-client.csv", "output-client.xlsx", "failures-client.csv", "failures-client.xlsx", "export-report.md"):
                self.assertTrue((out / name).is_file(), name)
            columns, rows = read_csv(out / "output-client.csv")
            self.assertEqual(list(CLIENT_COLUMNS), columns)
            self.assertEqual([("U", "review")], [(r["View"], r["review_flag"]) for r in rows])
            self.assertTrue((out / "output-client.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
            _, failure_rows = read_csv(out / "failures-client.csv")
            self.assertEqual(2, len(failure_rows))
            self.assertEqual("Superseded — a newer document from this firm updated this view", failure_rows[0]["What happened"])
            self.assertEqual("n m", failure_rows[1]["Evidence / notes"])


if __name__ == "__main__":
    unittest.main()
