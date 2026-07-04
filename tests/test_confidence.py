from __future__ import annotations

import unittest

from src.confidence import (
    CHECKER_UNCONFIRMED_CAP,
    MIN_HTML_SNAPSHOT_CHARS,
    MIN_PDF_CHARS_PER_PAGE,
    evidence_passes,
    normalize_quote_text,
    score_candidate,
    snapshot_read_quality,
)
from src.schemas import CandidateCall, CheckVerdict
from src.taxonomy import Taxonomy


class ConfidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def test_quote_normalization_handles_hyphenation_and_quotes(self) -> None:
        source = "The manager said \u201cEmerging mar-\n kets equities look attractive.\u201d"
        quote = '"Emerging markets equities look attractive."'

        self.assertIn(normalize_quote_text(quote), normalize_quote_text(source))

    def test_prose_quote_failure_is_hard_failure(self) -> None:
        candidate = _candidate(evidence_quote="fabricated quote")

        check = evidence_passes(candidate, "real source text")

        self.assertFalse(check.passed)
        self.assertEqual("quote_not_found", check.reason_code)

    def test_semantic_implied_call_scores_high_at_threshold(self) -> None:
        candidate = _candidate(taxonomy_match="semantic", call_language="implied")

        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot("EM equities are favored in the outlook."),
        )

        self.assertEqual(75, result.confidence)
        self.assertEqual("High", result.band)
        self.assertEqual("none", result.review_flag)

    def test_thin_snapshot_drops_read_quality_points(self) -> None:
        candidate = _candidate(taxonomy_match="semantic", call_language="implied")

        result = score_candidate(
            candidate,
            taxonomy=self.taxonomy,
            snapshot_text="EM equities are favored in the outlook.",
        )

        self.assertEqual(65, result.confidence)
        self.assertEqual("Medium", result.band)
        self.assertEqual("review", result.review_flag)

    def test_snapshot_read_quality_pdf_per_page_floor(self) -> None:
        thin_scanned = "a" * (MIN_PDF_CHARS_PER_PAGE * 2)  # 10 pages' worth missing

        self.assertFalse(snapshot_read_quality(thin_scanned, page_count=10))
        self.assertTrue(snapshot_read_quality("a" * (MIN_PDF_CHARS_PER_PAGE * 10), page_count=10))
        self.assertFalse(snapshot_read_quality("anything", page_count=0))

    def test_snapshot_read_quality_html_total_floor(self) -> None:
        self.assertFalse(snapshot_read_quality("a" * (MIN_HTML_SNAPSHOT_CHARS - 1)))
        self.assertTrue(snapshot_read_quality("a" * MIN_HTML_SNAPSHOT_CHARS))

    def test_table_visual_requires_specific_locator(self) -> None:
        candidate = _candidate(
            evidence_kind="table",
            evidence_quote="Taiwan overweight",
            locator="p.5",
        )

        check = evidence_passes(candidate, "Taiwan overweight table")

        self.assertFalse(check.passed)
        self.assertEqual("visual_locator_missing", check.reason_code)

    def test_table_visual_accepts_compact_specific_reference(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Taiwan overweight",
            locator="p.5 - Regional views grid",
        )

        check = evidence_passes(candidate, "Regional views grid Taiwan overweight")

        self.assertTrue(check.passed)

    def test_table_visual_accepts_em_dash_artifact_reference(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Taiwan overweight",
            locator="p.5 \u2014 Regional views grid",
        )

        check = evidence_passes(candidate, "Regional views grid Taiwan overweight")

        self.assertTrue(check.passed)


class CheckerScoringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()

    def _score(self, verdict: CheckVerdict | None, *, checker_enabled: bool = True):
        return score_candidate(
            _candidate(),
            taxonomy=self.taxonomy,
            snapshot_text=_healthy_snapshot("EM equities are favored in the outlook."),
            verdict=verdict,
            checker_enabled=checker_enabled,
        )

    def test_fail_verdict_is_a_hard_failure_with_specific_reason(self) -> None:
        verdict = _verdict(supports_view="fail")

        with self.assertRaises(ValueError) as caught:
            self._score(verdict)

        self.assertEqual("checker_sign_mismatch", str(caught.exception))

    def test_confirmed_verdict_leaves_score_uncapped(self) -> None:
        result = self._score(_verdict())

        self.assertEqual(100, result.confidence)
        self.assertEqual("confirmed", result.checker_status)
        self.assertEqual("none", result.review_flag)

    def test_unclear_verdict_caps_below_high_and_flags_review(self) -> None:
        result = self._score(_verdict(forward_looking="unclear", note="mixed recap"))

        self.assertEqual(CHECKER_UNCONFIRMED_CAP, result.confidence)
        self.assertEqual("Medium", result.band)
        self.assertEqual("review", result.review_flag)
        self.assertEqual("unclear", result.checker_status)
        self.assertEqual("mixed recap", result.checker_note)

    def test_missing_verdict_caps_when_checker_enabled(self) -> None:
        result = self._score(None)

        self.assertEqual(CHECKER_UNCONFIRMED_CAP, result.confidence)
        self.assertEqual("missing", result.checker_status)
        self.assertEqual("review", result.review_flag)

    def test_checker_off_keeps_legacy_scoring(self) -> None:
        result = self._score(None, checker_enabled=False)

        self.assertEqual(100, result.confidence)
        self.assertEqual("off", result.checker_status)


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


def _healthy_snapshot(quote: str) -> str:
    """Snapshot text containing the quote and passing the HTML read-quality floor."""
    filler = "Broader market commentary continues across the outlook document. "
    return quote + " " + filler * (MIN_HTML_SNAPSHOT_CHARS // len(filler) + 1)


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


if __name__ == "__main__":
    unittest.main()
