from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from src import eval as ev
from src.eval import Row


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_row(
    firm: str,
    leaf: str,
    view: str,
    *,
    commentary: str = "",
    review_flag: str = "none",
    index: int = 0,
    extra: dict[str, str] | None = None,
) -> Row:
    raw = {
        "Firm": firm,
        "Sub-Asset Class": leaf,
        "View": view,
        "Full Commentary": commentary,
        "review_flag": review_flag,
    }
    if extra:
        raw.update(extra)
    return Row(
        firm=firm,
        firm_key=ev.normalize_firm(firm),
        leaf=leaf.strip(),
        view=view,
        commentary=commentary,
        raw=raw,
        index=index,
    )


class FirmNormalizationTest(unittest.TestCase):
    def test_dots_commas_case_and_spacing_folded(self) -> None:
        self.assertEqual(
            ev.normalize_firm("J.P. Morgan Asset Management"),
            ev.normalize_firm("JP Morgan  Asset Management"),
        )
        self.assertEqual(ev.normalize_firm("PIMCO"), ev.normalize_firm("Pimco"))
        self.assertEqual(ev.normalize_firm(" Schroders "), "schroders")

    def test_distinct_firms_stay_distinct(self) -> None:
        self.assertNotEqual(
            ev.normalize_firm("Aberdeen Investments"),
            ev.normalize_firm("AllianceBernstein"),
        )


class TokenOverlapTest(unittest.TestCase):
    def test_shared_tokens_score_partial(self) -> None:
        # "India Equities" vs "Asia Equities" share "equities" of 3 unique tokens.
        self.assertAlmostEqual(
            ev.token_overlap("India Equities", "Asia Equities"), 1 / 3, places=3
        )

    def test_general_stopword_ignored(self) -> None:
        # "Equities - General" reduces to {equities}; matches "Global Equities"
        # on that token only.
        self.assertGreater(ev.token_overlap("Equities - General", "Global Equities"), 0)

    def test_no_overlap_is_zero(self) -> None:
        self.assertEqual(ev.token_overlap("Oil", "Gold/Precious"), 0.0)


class JoinBucketsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.run_dir = Path("runs/synthetic")
        self.model = [
            make_row("Acme Co.", "US Equities", "O", index=0),
            make_row("Acme Co.", "US Treasuries", "N", index=1),  # disagree with GT U
            make_row("Acme Co.", "Gold/Precious", "O", index=2),  # model_only
            make_row("Beta LLC", "EUR", "U", index=3),
        ]
        self.gt = [
            make_row("ACME CO", "US Equities", "O", index=0),  # case/punct variant
            make_row("Acme Co.", "US Treasuries", "U", index=1),
            make_row("Acme Co.", "China Equities", "N", index=2),  # gt_only
            make_row("Beta LLC", "EUR", "U", index=3),
        ]

    def test_three_buckets(self) -> None:
        result = ev.build_eval(self.model, self.gt, self.run_dir)
        self.assertEqual(len(result.matched), 3)  # US Eq, US Tsy, EUR
        self.assertEqual(len(result.model_only), 1)
        self.assertEqual(len(result.gt_only), 1)
        self.assertEqual(result.model_only[0].leaf, "Gold/Precious")
        self.assertEqual(result.gt_only[0].leaf, "China Equities")

    def test_view_agreement_split(self) -> None:
        result = ev.build_eval(self.model, self.gt, self.run_dir)
        agree = [m for m in result.matched if m.view_agree]
        disagree = [m for m in result.matched if not m.view_agree and not m.abstain]
        self.assertEqual(len(agree), 2)  # US Equities O/O, EUR U/U
        self.assertEqual(len(disagree), 1)  # US Treasuries N vs U
        self.assertEqual(disagree[0].leaf, "US Treasuries")

    def test_firm_normalization_joins_variant_spelling(self) -> None:
        # "Acme Co" (GT) joins "AcmeCo" (model) on US Equities.
        result = ev.build_eval(self.model, self.gt, self.run_dir)
        leaves = {m.leaf for m in result.matched}
        self.assertIn("US Equities", leaves)

    def test_headline_metrics(self) -> None:
        result = ev.build_eval(self.model, self.gt, self.run_dir)
        head = ev.headline_metrics(result)
        self.assertEqual(head["exact_match"], 3)
        self.assertEqual(head["view_agree"], 2)
        self.assertEqual(head["view_disagree"], 1)
        self.assertEqual(head["model_only"], 1)
        self.assertEqual(head["gt_only"], 1)
        self.assertEqual(head["raw_recall"], {"n": 3, "d": 4, "pct": 75.0})
        self.assertEqual(
            head["view_agreement_among_decided"], {"n": 2, "d": 3, "pct": 66.7}
        )

    def test_duplicate_join_key_rejected(self) -> None:
        dupe = [
            make_row("AcmeCo", "US Equities", "O", index=0),
            make_row("AcmeCo", "US Equities", "N", index=1),
        ]
        with self.assertRaises(ev.EvalError):
            ev.build_eval(dupe, self.gt, self.run_dir)


class UncertainAbstainTest(unittest.TestCase):
    def test_uncertain_is_abstain_not_disagree(self) -> None:
        model = [make_row("AcmeCo", "US Equities", "UNCERTAIN", index=0)]
        gt = [make_row("AcmeCo", "US Equities", "O", index=0)]
        result = ev.build_eval(model, gt, Path("runs/synthetic"))
        pair = result.matched[0]
        self.assertTrue(pair.abstain)
        self.assertFalse(pair.view_agree)
        head = ev.headline_metrics(result)
        self.assertEqual(head["abstain_uncertain"], 1)
        # Abstain excluded from the agreement denominator (0 decided → 0.0).
        self.assertEqual(head["view_agreement_among_decided"], {"n": 0, "d": 0, "pct": 0.0})


class NearLeafTest(unittest.TestCase):
    def test_agreeing_view_different_leaf_with_overlap_suggested(self) -> None:
        model = [make_row("AcmeCo", "Asia Equities", "O", index=0)]  # model_only
        gt = [make_row("AcmeCo", "India Equities", "O", index=0)]  # gt_only
        result = ev.build_eval(model, gt, Path("runs/synthetic"))
        key = ("acmeco", "India Equities")
        self.assertIn(key, result.near_leaf)
        candidate = result.near_leaf[key][0]
        self.assertEqual(candidate.model_leaf, "Asia Equities")
        self.assertGreater(candidate.similarity, 0)

    def test_disagreeing_view_not_suggested(self) -> None:
        model = [make_row("AcmeCo", "Asia Equities", "U", index=0)]
        gt = [make_row("AcmeCo", "India Equities", "O", index=0)]
        result = ev.build_eval(model, gt, Path("runs/synthetic"))
        self.assertNotIn(("acmeco", "India Equities"), result.near_leaf)

    def test_zero_overlap_not_suggested(self) -> None:
        # Same firm, agreeing view, but no shared token → too weak to suggest.
        model = [make_row("AcmeCo", "Oil", "O", index=0)]
        gt = [make_row("AcmeCo", "Gold/Precious", "O", index=0)]
        result = ev.build_eval(model, gt, Path("runs/synthetic"))
        self.assertNotIn(("acmeco", "Gold/Precious"), result.near_leaf)


class PerFirmTest(unittest.TestCase):
    def test_per_firm_math_sums_to_totals(self) -> None:
        model = [
            make_row("AcmeCo", "US Equities", "O", index=0),
            make_row("AcmeCo", "Gold/Precious", "O", index=1),
            make_row("Beta LLC", "EUR", "U", index=2),
        ]
        gt = [
            make_row("AcmeCo", "US Equities", "O", index=0),
            make_row("Beta LLC", "EUR", "U", index=1),
            make_row("Beta LLC", "GBP", "N", index=2),
        ]
        result = ev.build_eval(model, gt, Path("runs/synthetic"))
        table = {row["firm"]: row for row in ev.per_firm_table(result)}
        acme = table["AcmeCo"]
        self.assertEqual(acme["gt_total"], 1)
        self.assertEqual(acme["model_total"], 2)
        self.assertEqual(acme["matched"], 1)
        self.assertEqual(acme["model_only"], 1)
        self.assertEqual(acme["gt_only"], 0)
        self.assertEqual(acme["recall_pct"], 100.0)
        beta = table["Beta LLC"]
        self.assertEqual(beta["gt_total"], 2)
        self.assertEqual(beta["matched"], 1)
        self.assertEqual(beta["gt_only"], 1)
        self.assertEqual(beta["recall_pct"], 50.0)


class ReviewFlagAnalysisTest(unittest.TestCase):
    def test_flagged_disagreement_counted(self) -> None:
        model = [make_row("AcmeCo", "US Treasuries", "N", review_flag="review", index=0)]
        gt = [make_row("AcmeCo", "US Treasuries", "U", index=0)]
        result = ev.build_eval(model, gt, Path("runs/synthetic"))
        analysis = ev.review_flag_analysis(result)
        self.assertEqual(analysis["disagreements_total"], 1)
        self.assertEqual(analysis["disagreements_flagged"], 1)


class ColumnDistributionTest(unittest.TestCase):
    def test_missing_column_reports_none(self) -> None:
        rows = [make_row("AcmeCo", "US Equities", "O", index=0)]
        self.assertIsNone(ev.column_distribution(rows, "basis"))

    def test_present_column_counted(self) -> None:
        rows = [
            make_row("AcmeCo", "US Equities", "O", index=0, extra={"band": "High"}),
            make_row("AcmeCo", "US Treasuries", "N", index=1, extra={"band": "High"}),
            make_row("AcmeCo", "Gold/Precious", "O", index=2, extra={"band": "Medium"}),
        ]
        self.assertEqual(ev.column_distribution(rows, "band"), {"High": 2, "Medium": 1})


class CandidateReconstructionTest(unittest.TestCase):
    def test_prose_commentary_parsed(self) -> None:
        row = make_row(
            "AcmeCo",
            "US Equities",
            "O",
            commentary=(
                "Acme is constructive on US equities. Evidence: We remain "
                "overweight US equities. Locator: p.3 (Outlook)."
            ),
        )
        candidate = ev._reconstruct_candidate(row)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.evidence_kind, "prose")
        self.assertEqual(candidate.evidence_spans, ("We remain overweight US equities",))
        self.assertEqual(candidate.locator, "p.3")

    def test_table_locator_infers_table_kind(self) -> None:
        row = make_row(
            "AcmeCo",
            "CNY",
            "U",
            commentary=(
                "Forecast implies weaker yuan. Evidence: USD/CNY 7.2 -> 7.4. "
                "Locator: p.5 — 'China' forecast table."
            ),
        )
        candidate = ev._reconstruct_candidate(row)
        assert candidate is not None
        self.assertEqual(candidate.evidence_kind, "table")

    def test_unparseable_commentary_returns_none(self) -> None:
        row = make_row("AcmeCo", "US Equities", "O", commentary="no evidence marker here")
        self.assertIsNone(ev._reconstruct_candidate(row))


class OutputWritingTest(unittest.TestCase):
    def test_writes_three_files_and_worksheet_kinds(self) -> None:
        model = [
            make_row("AcmeCo", "US Equities", "O", index=0),
            make_row("AcmeCo", "US Treasuries", "N", index=1),  # disagree
            make_row("AcmeCo", "Gold/Precious", "O", index=2),  # model_only
        ]
        gt = [
            make_row("AcmeCo", "US Equities", "O", index=0),
            make_row("AcmeCo", "US Treasuries", "U", index=1),
            make_row("AcmeCo", "China Equities", "N", index=2),  # gt_only
        ]
        result = ev.build_eval(model, gt, Path("runs/synthetic"))
        result.spot_check = {"ran": False, "note": "synthetic"}
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "eval"
            written = ev.write_eval_outputs(result, out_dir)
            self.assertTrue(written["report"].is_file())
            self.assertTrue(written["buckets"].is_file())
            self.assertTrue(written["worksheet"].is_file())

            buckets = json.loads(written["buckets"].read_text())
            self.assertEqual(len(buckets["exact_match"]), 2)
            self.assertEqual(len(buckets["model_only"]), 1)
            self.assertEqual(len(buckets["gt_only"]), 1)

            with written["worksheet"].open(newline="") as handle:
                worksheet = list(csv.DictReader(handle))
            kinds = sorted(r["kind"] for r in worksheet)
            # 1 gt_only + 1 model_only + 1 view_disagreement
            self.assertEqual(kinds, ["gt_only", "model_only", "view_disagreement"])


class Pilot05RegressionTest(unittest.TestCase):
    """Pinned: eval.py against the frozen pilot-05 run + ground truth must
    reproduce the pilot-05 phase-1 join counts exactly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.run_dir = PROJECT_ROOT / "runs" / "pilot-05"
        cls.gt = PROJECT_ROOT / "ground-truth" / "pilot-ground-truth.csv"
        if not (cls.run_dir / "output.csv").is_file() or not cls.gt.is_file():
            raise unittest.SkipTest("frozen pilot-05 inputs not present")
        cls.result = ev.run_eval(cls.run_dir, cls.gt)
        cls.head = ev.headline_metrics(cls.result)

    def test_phase1_totals(self) -> None:
        self.assertEqual(self.head["gt_total"], 82)
        self.assertEqual(self.head["model_total"], 119)

    def test_phase1_buckets(self) -> None:
        self.assertEqual(self.head["exact_match"], 44)
        self.assertEqual(self.head["view_agree"], 40)
        self.assertEqual(self.head["view_disagree"], 4)
        self.assertEqual(self.head["model_only"], 75)
        self.assertEqual(self.head["gt_only"], 38)

    def test_reconciles_against_phase1_jsons(self) -> None:
        # Sum the frozen phase-1 join JSONs and confirm our buckets match them.
        judgments = self.run_dir / "gt-judgments"
        exact = model_only = gt_only = 0
        for path in sorted(judgments.glob("*.phase1.json")):
            data = json.loads(path.read_text())
            exact += len(data["exact_match"])
            model_only += len(data["model_only"])
            gt_only += len(data["gt_only"])
        self.assertEqual(exact, self.head["exact_match"])
        self.assertEqual(model_only, self.head["model_only"])
        self.assertEqual(gt_only, self.head["gt_only"])

    def test_per_firm_matches_frozen_jsons(self) -> None:
        judgments = self.run_dir / "gt-judgments"
        frozen = {}
        for path in sorted(judgments.glob("*.phase1.json")):
            data = json.loads(path.read_text())
            frozen[ev.normalize_firm(data["firm"])] = (
                len(data["exact_match"]),
                len(data["model_only"]),
                len(data["gt_only"]),
            )
        for row in ev.per_firm_table(self.result):
            key = ev.normalize_firm(row["firm"])
            self.assertEqual(
                (row["matched"], row["model_only"], row["gt_only"]),
                frozen[key],
                msg=f"firm {row['firm']} mismatch",
            )

    def test_spot_check_all_pass(self) -> None:
        # Every frozen output row already passed evidence at run time, so the
        # best-effort re-verification should report zero failures.
        spot = self.result.spot_check
        self.assertTrue(spot["ran"])
        self.assertEqual(spot["counts"]["failed"], 0)
        self.assertEqual(spot["counts"]["unparseable"], 0)


if __name__ == "__main__":
    unittest.main()
