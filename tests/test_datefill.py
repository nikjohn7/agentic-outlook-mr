from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import datefill
from src.datefill import (
    GRAN_FULL,
    GRAN_MONTH_YEAR,
    GRAN_QUARTER,
    WHERE_LANDING,
    WHERE_STATED,
    Candidate,
    Fill,
    ParsedDate,
    VerifiedCandidate,
    _agent_command,
    apply_patch,
    choose_fill,
    collect_undated_sources,
    find_date_for_source,
    parse_find_date_response,
    parse_verbatim_date,
    pdf_metadata_date_from_meta,
    rebuild_date_cell,
    verify_candidates,
)
from src.ingest import SourceRecord


def _source(firm: str, title: str, *, url: str = "http://example.com/x", source_type: str = "html", local=None) -> SourceRecord:
    return SourceRecord(
        source_id="s", firm=firm, date="", source=title, url=url,
        resolved_url=url, source_type=source_type, local_path=local,
    )


def _candidate_json(**over) -> dict:
    base = {
        "where": WHERE_STATED,
        "date_verbatim": "17 June 2026",
        "locator": "1",
        "evidence_quote": "Outlook, 17 June 2026",
        "granularity": GRAN_FULL,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# Response parsing
# --------------------------------------------------------------------------- #


class ParseResponseTests(unittest.TestCase):
    def test_valid_candidate_parses(self) -> None:
        raw = json.dumps({"candidates": [_candidate_json()]})
        [candidate] = parse_find_date_response(raw)
        self.assertEqual(WHERE_STATED, candidate.where)
        self.assertEqual("17 June 2026", candidate.date_verbatim)
        self.assertEqual("1", candidate.locator)

    def test_empty_candidates_list(self) -> None:
        self.assertEqual([], parse_find_date_response('{"candidates": []}'))

    def test_integer_locator_is_coerced(self) -> None:
        raw = json.dumps({"candidates": [_candidate_json(locator=3)]})
        [candidate] = parse_find_date_response(raw)
        self.assertEqual("3", candidate.locator)

    def test_bad_where_rejected(self) -> None:
        raw = json.dumps({"candidates": [_candidate_json(where="somewhere")]})
        with self.assertRaises(ValueError):
            parse_find_date_response(raw)

    def test_bad_granularity_rejected(self) -> None:
        raw = json.dumps({"candidates": [_candidate_json(granularity="daily")]})
        with self.assertRaises(ValueError):
            parse_find_date_response(raw)

    def test_missing_field_rejected(self) -> None:
        item = _candidate_json()
        del item["evidence_quote"]
        with self.assertRaises(ValueError):
            parse_find_date_response(json.dumps({"candidates": [item]}))

    def test_json_extracted_from_narrated_output(self) -> None:
        """codex --search can precede the JSON with narration; the last balanced
        object carrying `candidates` is still recovered."""
        narrated = (
            "I searched the web and read the PDF.\n"
            "Here is my answer:\n"
            + json.dumps({"candidates": [_candidate_json()]})
            + "\nDone."
        )
        [candidate] = parse_find_date_response(narrated)
        self.assertEqual("17 June 2026", candidate.date_verbatim)


# --------------------------------------------------------------------------- #
# Verbatim date parsing
# --------------------------------------------------------------------------- #


class ParseVerbatimDateTests(unittest.TestCase):
    def test_day_first_worded(self) -> None:
        self.assertEqual(ParsedDate(17, 6, 2026), parse_verbatim_date("17 June 2026", GRAN_FULL))

    def test_day_first_ordinal_and_abbrev(self) -> None:
        self.assertEqual(ParsedDate(3, 7, 2026), parse_verbatim_date("3rd Jul 2026", GRAN_FULL))

    def test_month_first_worded(self) -> None:
        self.assertEqual(ParsedDate(17, 6, 2026), parse_verbatim_date("June 17, 2026", GRAN_FULL))

    def test_iso(self) -> None:
        self.assertEqual(ParsedDate(15, 6, 2026), parse_verbatim_date("2026-06-15", GRAN_FULL))

    def test_numeric_disambiguated_by_day(self) -> None:
        self.assertEqual(ParsedDate(15, 6, 2026), parse_verbatim_date("15/06/2026", GRAN_FULL))

    def test_numeric_ambiguous_rejected(self) -> None:
        self.assertIsNone(parse_verbatim_date("05/06/2026", GRAN_FULL))

    def test_month_year_partial(self) -> None:
        self.assertEqual(ParsedDate(None, 6, 2026), parse_verbatim_date("June 2026", GRAN_MONTH_YEAR))

    def test_quarter_never_parses(self) -> None:
        self.assertIsNone(parse_verbatim_date("Q3 2026", GRAN_QUARTER))
        self.assertIsNone(parse_verbatim_date("Midyear 2026", GRAN_QUARTER))

    def test_year_outside_window_rejected(self) -> None:
        self.assertIsNone(parse_verbatim_date("17 June 2024", GRAN_FULL))
        self.assertIsNone(parse_verbatim_date("17 June 2030", GRAN_FULL))

    def test_month_year_render_uses_synthetic_day(self) -> None:
        parsed = parse_verbatim_date("June 2026", GRAN_MONTH_YEAR)
        self.assertEqual("01/06/2026", parsed.render())


# --------------------------------------------------------------------------- #
# PDF metadata (print-capture exclusion)
# --------------------------------------------------------------------------- #


class PdfMetadataTests(unittest.TestCase):
    def test_publisher_pdf_uses_creationdate(self) -> None:
        meta = {"Producer": "Adobe PDF Library 18.0", "Creator": "Adobe InDesign 21.3", "CreationDate": "D:20260615120000Z"}
        self.assertEqual(ParsedDate(15, 6, 2026), pdf_metadata_date_from_meta(meta))

    def test_chromium_skia_print_capture_excluded(self) -> None:
        meta = {"Producer": "Skia/PDF m149", "Creator": "Mozilla/5.0", "CreationDate": "D:20260708120000Z"}
        self.assertIsNone(pdf_metadata_date_from_meta(meta))

    def test_macos_quartz_firefox_save_excluded(self) -> None:
        meta = {"Producer": "macOS Version 15.7.2 (Build 24G325) Quartz PDFContext", "Creator": "Firefox", "CreationDate": "D:20260707120000Z"}
        self.assertIsNone(pdf_metadata_date_from_meta(meta))

    def test_epoch_year_rejected_by_window(self) -> None:
        meta = {"Producer": "FPDF 1.85", "Creator": "", "CreationDate": "D:19700101000000"}
        self.assertIsNone(pdf_metadata_date_from_meta(meta))

    def test_missing_creationdate(self) -> None:
        self.assertIsNone(pdf_metadata_date_from_meta({"Producer": "Adobe PDF Library"}))


# --------------------------------------------------------------------------- #
# Verification (fail-closed)
# --------------------------------------------------------------------------- #


class VerifyTests(unittest.TestCase):
    def _no_fetch(self, url: str):
        raise AssertionError("stated candidate must not fetch a page")

    def test_stated_quote_found_verifies(self) -> None:
        source = _source("Firmco", "Outlook")
        text = "Firmco Fixed Income Outlook, 17 June 2026. Markets ..."
        verified, discards = verify_candidates([Candidate(WHERE_STATED, "17 June 2026", "1", "Outlook, 17 June 2026", GRAN_FULL)], source, text, self._no_fetch)
        self.assertEqual(1, len(verified))
        self.assertEqual([], discards)
        self.assertEqual("17/06/2026", verified[0].date_str)

    def test_stated_quote_absent_discarded(self) -> None:
        source = _source("Firmco", "Outlook")
        text = "A document with no such line."
        verified, discards = verify_candidates([Candidate(WHERE_STATED, "17 June 2026", "1", "Outlook, 17 June 2026", GRAN_FULL)], source, text, self._no_fetch)
        self.assertEqual([], verified)
        self.assertEqual(1, len(discards))

    def test_normalized_quote_match(self) -> None:
        source = _source("Firmco", "Outlook")
        text = "Firmco Outlook — 17 June 2026"  # em dash in source
        cand = Candidate(WHERE_STATED, "17 June 2026", "1", "Outlook - 17 June 2026", GRAN_FULL)
        verified, _ = verify_candidates([cand], source, text, self._no_fetch)
        self.assertEqual(1, len(verified))

    def test_landing_page_referencing_document_verifies(self) -> None:
        source = _source("Firmco", "Mid Year Outlook", url="https://firmco.com/insights/mid-year-outlook")
        page = "<html><body><a href='https://firmco.com/insights/mid-year-outlook'>read</a> Published 12 June 2026</body></html>"
        verified, discards = verify_candidates([Candidate(WHERE_LANDING, "12 June 2026", "https://firmco.com/insights", "Published 12 June 2026", GRAN_FULL)], source, "", lambda url: page)
        self.assertEqual(1, len(verified))
        self.assertEqual("12/06/2026", verified[0].date_str)

    def test_landing_page_without_document_reference_discarded(self) -> None:
        source = _source("Firmco", "Mid Year Outlook", url="https://firmco.com/insights/mid-year-outlook")
        # date present but the page never links/names THIS document -> sibling page.
        page = "<html><body>Some other article. Published 12 June 2026</body></html>"
        verified, discards = verify_candidates([Candidate(WHERE_LANDING, "12 June 2026", "https://firmco.com/other", "Published 12 June 2026", GRAN_FULL)], source, "", lambda url: page)
        self.assertEqual([], verified)
        self.assertEqual(1, len(discards))

    def test_quarter_partial_discarded(self) -> None:
        source = _source("Firmco", "Outlook")
        verified, discards = verify_candidates([Candidate(WHERE_STATED, "Q3 2026", "1", "Q3 2026 Outlook", GRAN_QUARTER)], source, "Q3 2026 Outlook", self._no_fetch)
        self.assertEqual([], verified)
        self.assertEqual(1, len(discards))


# --------------------------------------------------------------------------- #
# Precedence
# --------------------------------------------------------------------------- #


def _verified(where: str, granularity: str, date: str) -> VerifiedCandidate:
    parsed = parse_verbatim_date(date, granularity)
    return VerifiedCandidate(Candidate(where, date, "loc", "quote", granularity), parsed)


class ChooseFillTests(unittest.TestCase):
    def test_stated_full_beats_metadata(self) -> None:
        fill = choose_fill([_verified(WHERE_STATED, GRAN_FULL, "17 June 2026")], ParsedDate(1, 6, 2026), "pdf_metadata")
        self.assertEqual("17/06/2026", fill.date)
        self.assertEqual("stated_document", fill.date_from)

    def test_metadata_beats_landing(self) -> None:
        fill = choose_fill([_verified(WHERE_LANDING, GRAN_FULL, "10 June 2026")], ParsedDate(15, 6, 2026), "pdf_metadata")
        self.assertEqual("15/06/2026", fill.date)
        self.assertEqual("pdf_metadata", fill.date_from)

    def test_landing_beats_partial(self) -> None:
        fill = choose_fill([_verified(WHERE_LANDING, GRAN_FULL, "10 June 2026"), _verified(WHERE_STATED, GRAN_MONTH_YEAR, "June 2026")], None, "")
        self.assertEqual("10/06/2026", fill.date)
        self.assertEqual("landing_page", fill.date_from)

    def test_partial_last_resort_is_synthetic(self) -> None:
        fill = choose_fill([_verified(WHERE_STATED, GRAN_MONTH_YEAR, "June 2026")], None, "")
        self.assertEqual("01/06/2026", fill.date)
        self.assertEqual("partial_month", fill.date_from)
        self.assertTrue(fill.synthetic_day)

    def test_nothing_verified_is_blank(self) -> None:
        self.assertEqual(Fill(), choose_fill([], None, ""))


# --------------------------------------------------------------------------- #
# Agent command shape + mock-runner call
# --------------------------------------------------------------------------- #


class AgentCommandTests(unittest.TestCase):
    def test_codex_command_has_search_and_prompt_in_argv(self) -> None:
        command, stdin_text = _agent_command("codex", None, "low", "PROMPT")
        self.assertIn("tools.web_search=true", command)
        self.assertIn("gpt-5.5", command)  # None → default codex model
        self.assertEqual("PROMPT", command[-1])
        self.assertIsNone(stdin_text)

    def test_codex_command_threads_an_allowlisted_model(self) -> None:
        command, _ = _agent_command("codex", "gpt-5.6-terra", "low", "PROMPT")
        self.assertIn("gpt-5.6-terra", command)

    def test_codex_command_rejects_offlist_model(self) -> None:
        with self.assertRaises(ValueError):
            _agent_command("codex", "o3", "low", "PROMPT")

    def test_claude_command_passes_prompt_via_stdin(self) -> None:
        command, stdin_text = _agent_command("claude", "sonnet", "low", "PROMPT")
        self.assertIn("--allowed-tools", command)
        self.assertNotIn("PROMPT", command)  # variadic --allowed-tools would swallow it
        self.assertEqual("PROMPT", stdin_text)

    def test_find_date_parses_mock_runner_output(self) -> None:
        captured: dict = {}

        def runner(command, stdin_text):
            captured["command"] = command
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"candidates": [_candidate_json()]}), stderr="")

        candidates, error = find_date_for_source(_source("Firmco", "Outlook"), "text", engine="codex", model=None, effort="low", runner=runner)
        self.assertEqual("", error)
        self.assertEqual(1, len(candidates))
        self.assertIn("codex", captured["command"][0])

    def test_engine_failure_returns_error_not_raise(self) -> None:
        def runner(command, stdin_text):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

        candidates, error = find_date_for_source(_source("Firmco", "Outlook"), "text", engine="codex", model=None, effort="low", runner=runner)
        self.assertEqual([], candidates)
        self.assertIn("boom", error)

    def test_bad_json_returns_error_not_raise(self) -> None:
        def runner(command, stdin_text):
            return subprocess.CompletedProcess(command, 0, stdout="not json at all", stderr="")

        candidates, error = find_date_for_source(_source("Firmco", "Outlook"), "text", engine="codex", model=None, effort="low", runner=runner)
        self.assertEqual([], candidates)
        self.assertIn("unparseable", error)


# --------------------------------------------------------------------------- #
# Undated-source collection
# --------------------------------------------------------------------------- #


class CollectTests(unittest.TestCase):
    def test_collects_undated_and_logs_unmatched(self) -> None:
        rows = [
            {"Firm": "Firmco", "Source": "Outlook", "Date": ""},
            {"Firm": "Firmco", "Source": "Outlook", "Date": ""},  # duplicate source, one call
            {"Firm": "Datedco", "Source": "Dated Doc", "Date": "01/06/2026"},  # skipped
            {"Firm": "Aon", "Source": "Reisnsurance Report", "Date": ""},  # firm variant, unmatched
        ]
        master = [
            _source("Firmco", "Outlook"),
            _source("Datedco", "Dated Doc"),
            _source("Aon's", "Reisnsurance Report"),  # firm differs -> no join
        ]
        sources, unmatched = collect_undated_sources(rows, master)
        self.assertEqual(["Outlook"], [s.source for s in sources])
        self.assertEqual([("Aon", "Reisnsurance Report")], unmatched)

    def test_grouped_titles_split_into_members(self) -> None:
        rows = [{"Firm": "RBC", "Source": "Europe | United States", "Date": ""}]
        master = [_source("RBC", "Europe"), _source("RBC", "United States")]
        sources, unmatched = collect_undated_sources(rows, master)
        self.assertEqual({"Europe", "United States"}, {s.source for s in sources})
        self.assertEqual([], unmatched)


# --------------------------------------------------------------------------- #
# Apply — grouped Date rebuild + dedup
# --------------------------------------------------------------------------- #


class RebuildDateTests(unittest.TestCase):
    def test_single_undated_row_filled(self) -> None:
        fills = {(datefill.normalize_firm("Firmco"), datefill.normalize_title("Outlook")): "17/06/2026"}
        row = {"Firm": "Firmco", "Source": "Outlook", "Date": ""}
        self.assertEqual("17/06/2026", rebuild_date_cell(row, fills))

    def test_single_dated_row_unchanged(self) -> None:
        row = {"Firm": "Firmco", "Source": "Outlook", "Date": "01/06/2026"}
        self.assertEqual("01/06/2026", rebuild_date_cell(row, {}))

    def test_grouped_identical_dates_collapse(self) -> None:
        row = {
            "Firm": "RBC Wealth",
            "Source": "Europe | United States | Canada | Asia-Pacific",
            "Date": "15/06/2026 | 15/06/2026 | 15/06/2026 | 15/06/2026",
        }
        self.assertEqual("15/06/2026", rebuild_date_cell(row, {}))

    def test_grouped_distinct_dates_preserved_in_order(self) -> None:
        row = {
            "Firm": "Wellington Management",
            "Source": "Doc A | Doc B",
            "Date": "11/06/2026 | 10/06/2026",
        }
        self.assertEqual("11/06/2026 | 10/06/2026", rebuild_date_cell(row, {}))

    def test_apply_patch_writes_new_file_and_counts_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output.csv"
            with output.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Firm", "Date", "Source", "URL", "View"])
                writer.writeheader()
                writer.writerow({"Firm": "Firmco", "Date": "", "Source": "Outlook", "URL": "u", "View": "O"})
                writer.writerow({"Firm": "RBC Wealth", "Date": "15/06/2026 | 15/06/2026", "Source": "Europe | US", "URL": "u", "View": "N"})
            patch = Path(tmp) / "datefill.csv"
            with patch.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=datefill.DATEFILL_COLUMNS)
                writer.writeheader()
                writer.writerow({"Firm": "Firmco", "Title": "Outlook", "URL": "u", "Date": "17/06/2026", "date_from": "stated_document", "synthetic_day": "false", "locator": "1", "evidence_quote": "q", "discarded": "", "engine": "codex", "agent_error": ""})
            write_path = Path(tmp) / "new.csv"
            changed = apply_patch(output, patch, write_path)

            with write_path.open(newline="", encoding="utf-8") as handle:
                new_rows = list(csv.DictReader(handle))
            self.assertEqual(2, changed)  # Firmco filled + RBC deduped
            self.assertEqual("17/06/2026", new_rows[0]["Date"])
            self.assertEqual("15/06/2026", new_rows[1]["Date"])
            # column set unchanged
            self.assertEqual(["Firm", "Date", "Source", "URL", "View"], list(new_rows[0].keys()))


if __name__ == "__main__":
    unittest.main()
