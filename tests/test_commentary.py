from __future__ import annotations

import unittest

from src.commentary import (
    SEGMENT_SEP,
    Commentary,
    Segment,
    append_note,
    commentary_from_row,
    display_locator,
    merge_commentaries,
    parse_full_commentary,
    render_full_commentary,
    row_columns,
    structured_columns,
)


class DisplayLocatorTest(unittest.TestCase):
    def test_page_locators_pass_through(self) -> None:
        self.assertEqual("p.13", display_locator("p.13"))
        self.assertEqual("p.5 - 'Fixed Income Views' grid", display_locator("p.5 - 'Fixed Income Views' grid"))

    def test_char_offsets_render_blank(self) -> None:
        # HTML / transcript chunk offsets mean nothing to a reader (client decision 10).
        self.assertEqual("", display_locator("char:0-8000"))
        self.assertEqual("", display_locator(" char:16000-24000 "))

    def test_char_offset_keeps_a_trailing_figure_name(self) -> None:
        self.assertEqual(
            "Figure 1: Summary of sector market perspectives",
            display_locator("char:0-8000 - Figure 1: Summary of sector market perspectives"),
        )


class RenderTest(unittest.TestCase):
    def test_single_segment_renders_legacy_shape(self) -> None:
        commentary = Commentary(
            (Segment("Doc A", "p.3", "Firm favours EM.", "We favour EM equities"),),
            "Checker: not run.",
        )
        self.assertEqual(
            "Firm favours EM. Evidence: We favour EM equities. Locator: p.3. Checker: not run.",
            render_full_commentary(commentary),
        )
        self.assertEqual(
            {
                "Reasoning": "Firm favours EM.",
                "Verbatim Evidence": "We favour EM equities",
                "Locator": "p.3",
                "Reviewer Notes": "Checker: not run.",
            },
            structured_columns(commentary),
        )

    def test_single_segment_without_locator_omits_the_clause(self) -> None:
        commentary = Commentary((Segment("Doc A", "", "R.", "Q"),))
        self.assertEqual("R. Evidence: Q.", render_full_commentary(commentary))
        self.assertEqual("", structured_columns(commentary)["Locator"])

    def test_merged_segments_are_labeled_in_every_column(self) -> None:
        commentary = Commentary(
            (
                Segment("Doc A", "p.3", "Reason one.", "Quote one"),
                Segment("Doc B", "", "Reason two.", "Quote two"),
            ),
            "Arbiter: kept A.",
        )
        full = render_full_commentary(commentary)
        self.assertEqual(
            "Doc A (p.3): Reason one. Evidence: Quote one." + SEGMENT_SEP
            + "Doc B: Reason two. Evidence: Quote two. Arbiter: kept A.",
            full,
        )
        columns = structured_columns(commentary)
        self.assertEqual("Doc A: Reason one." + SEGMENT_SEP + "Doc B: Reason two.", columns["Reasoning"])
        self.assertEqual("Doc A: Quote one" + SEGMENT_SEP + "Doc B: Quote two", columns["Verbatim Evidence"])
        self.assertEqual("Doc A: p.3", columns["Locator"])
        self.assertEqual("Arbiter: kept A.", columns["Reviewer Notes"])

    def test_merged_locator_omits_pageless_segments_and_round_trips(self) -> None:
        # Same-title segments, the pageless one first: the Locator cell shows
        # only real locators, and re-reading the row keeps each locator on its
        # own segment (aligned through Full Commentary, not by position).
        commentary = Commentary(
            (
                Segment("Doc A", "", "Reason one.", "Quote one"),
                Segment("Doc A", "p.4", "Reason two.", "Quote two"),
                Segment("Doc A", "p.10 (read from PDF)", "Reason three.", "Quote three"),
            ),
        )
        columns = row_columns(commentary)
        self.assertEqual("Doc A: p.4" + SEGMENT_SEP + "Doc A: p.10 (read from PDF)", columns["Locator"])
        row = {"Source": "Doc A", **columns}
        back = commentary_from_row(row)
        self.assertEqual(["", "p.4", "p.10 (read from PDF)"], [s.locator for s in back.segments])
        self.assertEqual(["Quote one", "Quote two", "Quote three"], [s.evidence for s in back.segments])
        self.assertEqual("both", columns["evidence_origin"])

    def test_merged_locator_blank_when_no_segment_has_one(self) -> None:
        commentary = Commentary(
            (Segment("Doc A", "", "R1.", "Q1"), Segment("Doc B", "", "R2.", "Q2")),
        )
        columns = row_columns(commentary)
        self.assertEqual("", columns["Locator"])
        back = commentary_from_row({"Source": "Doc A | Doc B", **columns})
        self.assertEqual(["", ""], [s.locator for s in back.segments])
        self.assertEqual(["Doc A", "Doc B"], [s.title for s in back.segments])

    def test_legacy_dash_placeholder_still_reads_as_blank(self) -> None:
        row = {
            "Source": "Doc A | Doc B",
            "Reasoning": "Doc A: R1." + SEGMENT_SEP + "Doc B: R2.",
            "Verbatim Evidence": "Doc A: Q1" + SEGMENT_SEP + "Doc B: Q2",
            "Locator": "Doc A: p.3" + SEGMENT_SEP + "Doc B: -",
            "Reviewer Notes": "",
        }
        self.assertEqual(["p.3", ""], [s.locator for s in commentary_from_row(row).segments])

    def test_row_columns_carries_full_commentary_and_the_four(self) -> None:
        columns = row_columns(Commentary((Segment("T", "p.1", "R.", "Q"),)))
        self.assertEqual(
            {"Full Commentary", "Reasoning", "Verbatim Evidence", "Locator", "Reviewer Notes",
             "evidence_origin"},
            set(columns),
        )
        self.assertEqual("markdown", columns["evidence_origin"])


class MergeAndNoteTest(unittest.TestCase):
    def test_merge_keeps_order_and_dedupes_notes(self) -> None:
        a = Commentary((Segment("A", "p.1", "ra", "qa"),), "Checker: x.")
        b = Commentary((Segment("B", "", "rb", "qb"),), "Checker: x.")
        merged = merge_commentaries([a, b])
        self.assertEqual(("A", "B"), tuple(s.title for s in merged.segments))
        self.assertEqual("Checker: x.", merged.notes)

    def test_append_note_updates_both_notes_and_full_commentary(self) -> None:
        row = row_columns(Commentary((Segment("T", "p.1", "R.", "Q"),)))
        append_note(row, "Also held by this firm: U in 'Doc B'.")
        self.assertEqual("Also held by this firm: U in 'Doc B'.", row["Reviewer Notes"])
        self.assertTrue(row["Full Commentary"].endswith(" Also held by this firm: U in 'Doc B'."))
        append_note(row, "")
        self.assertEqual("Also held by this firm: U in 'Doc B'.", row["Reviewer Notes"])


class ParseLegacyTest(unittest.TestCase):
    def test_single_row_round_trips(self) -> None:
        text = (
            "M&G favours asset-based financings. Evidence: Inflationary pressures may "
            "favour floating rate private credit asset classes.. Locator: p.20. "
            "Basis: inferred - single-step analyst inference; review required. "
            "Checker: unconfirmed (conditional)."
        )
        parsed = parse_full_commentary(text, "Doc A")
        self.assertEqual(1, len(parsed.segments))
        seg = parsed.segments[0]
        self.assertEqual("M&G favours asset-based financings.", seg.reasoning)
        self.assertEqual(
            "Inflationary pressures may favour floating rate private credit asset classes.",
            seg.evidence,
        )
        self.assertEqual("p.20", seg.locator)
        self.assertEqual("Doc A", seg.title)
        self.assertEqual(
            "Basis: inferred - single-step analyst inference; review required. "
            "Checker: unconfirmed (conditional).",
            parsed.notes,
        )
        self.assertEqual(text, render_full_commentary(parsed))

    def test_locator_with_period_inside_is_not_truncated(self) -> None:
        text = "R. Evidence: Q. Locator: p.5 - 'T. Rowe Price views' grid. Evidence check: degraded."
        seg = parse_full_commentary(text, "Doc").segments[0]
        self.assertEqual("p.5 - 'T. Rowe Price views' grid", seg.locator)
        self.assertEqual("Evidence check: degraded.", parse_full_commentary(text, "Doc").notes)

    def test_char_locator_is_blanked_on_parse(self) -> None:
        seg = parse_full_commentary("R. Evidence: Q. Locator: char:0-8000.", "Doc").segments[0]
        self.assertEqual("", seg.locator)
        self.assertEqual("Q", seg.evidence)

    def test_grouped_row_title_suffix_is_stripped_from_locator(self) -> None:
        text = "R. Evidence: Q. Locator: p.3 (Review Q1)."
        seg = parse_full_commentary(text, "Review Q1 | Outlook Q2").segments[0]
        self.assertEqual("p.3", seg.locator)

    def test_merged_segments_split_on_the_rows_own_titles(self) -> None:
        # Titles containing ": " must not confuse the label split.
        titles = "Market Signals: Midyear | Sector Views"
        text = (
            "Market Signals: Midyear (p.3): Reason one. Evidence: Quote one."
            + SEGMENT_SEP
            + "Sector Views: Reason two. Evidence: Quote two. Locator: p.9. Checker: not run."
            + " Arbiter: kept the first."
        )
        parsed = parse_full_commentary(text, titles)
        self.assertEqual(2, len(parsed.segments))
        first, second = parsed.segments
        self.assertEqual(("Market Signals: Midyear", "p.3", "Reason one.", "Quote one"), (first.title, first.locator, first.reasoning, first.evidence))
        self.assertEqual(("Sector Views", "p.9", "Reason two.", "Quote two"), (second.title, second.locator, second.reasoning, second.evidence))
        self.assertEqual("Checker: not run. Arbiter: kept the first.", parsed.notes)

    def test_commentary_from_row_prefers_structured_columns(self) -> None:
        row = {
            "Source": "Doc A",
            "Full Commentary": "stale blob",
            "Reasoning": "R.",
            "Verbatim Evidence": "Q",
            "Locator": "p.2",
            "Reviewer Notes": "N.",
        }
        parsed = commentary_from_row(row)
        self.assertEqual(("R.", "Q", "p.2"), (parsed.segments[0].reasoning, parsed.segments[0].evidence, parsed.segments[0].locator))
        self.assertEqual("N.", parsed.notes)

    def test_commentary_from_row_rebuilds_merged_structured_columns(self) -> None:
        original = Commentary(
            (Segment("Doc A", "p.3", "Reason one.", "Quote one"), Segment("Doc B", "", "Reason two.", "Quote two")),
            "Arbiter: kept A.",
        )
        row = {"Source": "Doc A | Doc B", **row_columns(original)}
        rebuilt = commentary_from_row(row)
        self.assertEqual(original, rebuilt)

    def test_merged_row_from_one_document_round_trips_through_the_csv_writer(self) -> None:
        # The CSV writer collapses space runs, so "  ||||  " is " |||| " on disk;
        # a same-document merge (one title) must still split into its segments.
        import tempfile
        from pathlib import Path

        from src.csvio import read_csv, write_csv

        original = Commentary(
            (Segment("Doc A", "p.3", "Reason one.", "Quote one"), Segment("Doc A", "p.9", "Reason two.", "Quote two")),
            "Checker: not run.",
        )
        row = {"Source": "Doc A", **row_columns(original)}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "out.csv"
            write_csv(path, tuple(row), [row])
            _, [read_back] = read_csv(path)
        self.assertIn(" |||| ", read_back["Verbatim Evidence"])
        self.assertEqual(original, commentary_from_row(read_back))

    def test_commentary_from_row_falls_back_to_full_commentary(self) -> None:
        row = {"Source": "Doc A", "Full Commentary": "R. Evidence: Q. Locator: p.4."}
        parsed = commentary_from_row(row)
        self.assertEqual("Q", parsed.segments[0].evidence)
        self.assertEqual("p.4", parsed.segments[0].locator)


if __name__ == "__main__":
    unittest.main()
