from __future__ import annotations

import unittest
from pathlib import Path

from src.commentary import Commentary, Segment, commentary_from_row, row_columns
from src.pagecheck import (
    ADDED,
    AMBIGUOUS,
    CONFIRMED,
    CORRECTED,
    NO_PDF,
    NOT_FOUND,
    PDF_READ,
    _key,
    check_row,
    check_segment,
    cited_pages,
    found_pages,
    strip_page,
)

PAGES = [
    _key("Key takeaways. We underweight German Bunds, JGBs and cash."),
    _key("Equities. We expect Taiwan, Malaysia and Singapore to outperform regional peers."),
    _key("Appendix. We expect Taiwan, Malaysia and Singapore to outperform regional peers."),
]


def _seg(locator: str, evidence: str) -> Segment:
    return Segment("Doc A", locator, "Reason.", evidence)


class LocatorHelpersTest(unittest.TestCase):
    def test_cited_pages_and_strip(self) -> None:
        self.assertEqual((12,), cited_pages("p.12 - 'Rankings' table"))
        self.assertEqual((4, 5), cited_pages("pp. 4-5"))
        self.assertEqual((), cited_pages("'Rankings' table"))
        self.assertEqual("'Rankings' table", strip_page("p.12 - 'Rankings' table"))
        self.assertEqual("", strip_page("p.3"))

    def test_found_pages_ignores_spacing_hyphens_and_punctuation(self) -> None:
        self.assertEqual((1,), found_pages("underweight German-Bunds,JGBs and cash", PAGES))

    def test_ellipsis_spans_must_share_a_page(self) -> None:
        self.assertEqual((1,), found_pages("Key takeaways ... German Bunds, JGBs and cash", PAGES))
        self.assertEqual((), found_pages("We underweight German Bunds ... Malaysia and Singapore to outperform", PAGES))


class CheckSegmentTest(unittest.TestCase):
    def test_outcomes(self) -> None:
        bunds = "We underweight German Bunds, JGBs and cash"
        asia = "Taiwan, Malaysia and Singapore to outperform regional peers"
        self.assertEqual((CONFIRMED, "p.1"), self._run("p.1", bunds))
        self.assertEqual((CORRECTED, "p.1 - 'Key takeaways' box"), self._run("p.7 - 'Key takeaways' box", bunds))
        self.assertEqual((ADDED, "p.1"), self._run("", bunds))
        self.assertEqual((AMBIGUOUS, ""), self._run("p.2", asia))
        self.assertEqual((NOT_FOUND, "'Rankings' table"), self._run("p.12 - 'Rankings' table", "Long/short equity | Z-Score: 0.3"))
        self.assertEqual(NO_PDF, check_segment(_seg("p.4", bunds), None).outcome)

    def _run(self, locator: str, evidence: str) -> tuple[str, str]:
        check = check_segment(_seg(locator, evidence), PAGES)
        return check.outcome, check.locator


class CheckRowTest(unittest.TestCase):
    def test_row_locators_are_rewritten_with_one_note(self) -> None:
        commentary = Commentary(
            (
                _seg("p.7", "We underweight German Bunds, JGBs and cash"),
                _seg("p.2", "Taiwan, Malaysia and Singapore to outperform regional peers"),
            ),
            "Checker: not run.",
        )
        row = {"Firm": "MetLife IM", "Source": "Doc A", "Sub-Asset Class": "German Bunds", "View": "U",
               "review_flag": "none", **row_columns(commentary)}
        out, audit = check_row(row, {"doc a": Path("doc-a.pdf")}, {Path("doc-a.pdf"): PAGES})
        segments = commentary_from_row(out).segments
        self.assertEqual(["p.1", ""], [s.locator for s in segments])
        self.assertEqual([CORRECTED, AMBIGUOUS], [a["outcome"] for a in audit])
        self.assertIn("Page check: 'Doc A' p.7 corrected to p.1", out["Reviewer Notes"])
        self.assertIn("appears on p.2, p.3 of the PDF", out["Reviewer Notes"])
        self.assertTrue(out["Reviewer Notes"].startswith("Checker: not run."))
        self.assertEqual("none", out["review_flag"])  # page notes never flag

    def test_unchanged_row_gets_no_note(self) -> None:
        row = {"Firm": "F", "Source": "Doc A", **row_columns(Commentary((_seg("p.1", "We underweight German Bunds, JGBs and cash"),)))}
        out, _ = check_row(row, {"doc a": Path("x.pdf")}, {Path("x.pdf"): PAGES})
        self.assertNotIn("Page check", out["Reviewer Notes"])

    def test_mixed_origin_row_checks_markdown_and_leaves_pdf_read_segment(self) -> None:
        pdf_locator = "p.2 - 'Positioning grid' (read from PDF)"
        commentary = Commentary(
            (
                _seg("p.7", "We underweight German Bunds, JGBs and cash"),
                _seg(pdf_locator, "German Bunds: underweight dot"),
            )
        )
        row = {"Firm": "F", "Source": "Doc A", **row_columns(commentary)}
        out, audit = check_row(row, {"doc a": Path("x.pdf")}, {Path("x.pdf"): PAGES})
        segments = commentary_from_row(out).segments
        self.assertEqual(["p.1", pdf_locator], [s.locator for s in segments])
        self.assertEqual([CORRECTED, PDF_READ], [a["outcome"] for a in audit])
        self.assertEqual("both", out["evidence_origin"])


if __name__ == "__main__":
    unittest.main()
