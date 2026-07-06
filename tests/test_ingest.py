from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ingest import (
    SourceRecord,
    _download_pdf,
    _pdf_filename_from_url,
    count_visual_markup,
    create_snapshot,
    detect_scrambled_page,
    detect_source_type,
    enforce_source_limit,
    is_visual_heavy,
    load_pilot_sources,
    resolve_url,
    strip_tracking_params,
)


FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_PRINTED_PDF = FIXTURES / "printed_page.pdf"


def _html_source() -> SourceRecord:
    return SourceRecord(
        source_id="aberdeen-outlook",
        firm="Aberdeen Investments",
        date="6/1/2026",
        source="EM Outlook",
        url="https://example.test/outlook",
        resolved_url="https://example.test/outlook",
        source_type="html",
    )


class IngestTest(unittest.TestCase):
    def test_read_url_unwraps_to_target_url(self) -> None:
        raw = "read://https_www.example.com/?url=https%3A%2F%2Fexample.com%2Foutlook.html"

        self.assertEqual("https://example.com/outlook.html", resolve_url(raw))

    def test_tracking_params_are_removed(self) -> None:
        url = "https://example.com/a?utm_source=x&keep=1&gclid=abc"

        self.assertEqual("https://example.com/a?keep=1", strip_tracking_params(url))

    def test_seismic_encoded_markers_are_decoded(self) -> None:
        raw = "https://eng4e.seismic.com/i/abcPLUSSIGNdef___ghi"

        self.assertEqual("https://eng4e.seismic.com/i/abc+def/ghi", resolve_url(raw))

    def test_pilot_sources_map_known_local_pdfs(self) -> None:
        sources = {source.firm: source for source in load_pilot_sources()}

        self.assertEqual("html", sources["Aberdeen Investments"].source_type)
        self.assertEqual("pdf", sources["AllianceBernstein"].source_type)
        self.assertIsNotNone(sources["AllianceBernstein"].local_path)

    def test_source_limit_is_enforced(self) -> None:
        sources = load_pilot_sources()

        enforce_source_limit(sources, limit=7)
        with self.assertRaises(ValueError):
            enforce_source_limit(sources, limit=6)

    def test_pilot_resolution_regression(self) -> None:
        # Golden (source_id -> local PDF name or None) captured from the
        # firm/title mapping before it was replaced by the local_file column.
        # The migrated pilot.csv must resolve byte-identically.
        expected = {
            "aberdeen-investments-emerging-markets-q2-2026-outlook-shifting-sands": None,
            "alliancebernstein-global-macro-outlook-second-quarter-2026": "alliance-bernstein.pdf",
            "schroders-quarterly-markets-review-q1-2026": "Quarterly markets review - Q1 2026.pdf",
            "j-p-morgan-asset-management-global-fixed-income-views-2q-2026": "jp-morgan.pdf",
            "pimco-layered-uncertainty-conflict-credit-stress-and-ai": "PIMCO.pdf",
            "schroders-our-multi-asset-investment-views-march-2026": (
                "Our multi-asset investment views – March 2026.pdf"
            ),
            "j-p-morgan-asset-management-global-asset-allocation-views-2q-2026": (
                "Global Asset Allocation Views 2Q 2026 _ J.P. Morgan Asset Management.pdf"
            ),
        }
        resolved = {
            source.source_id: (source.local_path.name if source.local_path else None)
            for source in load_pilot_sources()
        }
        self.assertEqual(expected, resolved)


class LocalFileLoaderTest(unittest.TestCase):
    """The optional `local_file` column's three-way contract."""

    HEADER = "Firm,Date,Source,MR Link,local_file\n"

    def _write_csv(self, temp_dir: str, body: str) -> Path:
        path = Path(temp_dir) / "sources.csv"
        path.write_text(self.HEADER + body, encoding="utf-8")
        return path

    def test_present_and_existing_local_file_is_ingested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = self._write_csv(
                temp_dir,
                "Test Firm,4/2/2026,A Doc,https://example.test/a,prev-excel/PIMCO.pdf\n",
            )
            [source] = load_pilot_sources(csv_path)

        self.assertEqual("pdf", source.source_type)
        self.assertEqual("PIMCO.pdf", source.local_path.name)
        self.assertEqual("https://example.test/a", source.url)  # URL kept as metadata

    def test_missing_local_file_hard_errors_naming_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = self._write_csv(
                temp_dir,
                "Test Firm,4/2/2026,Ghost Doc,https://example.test/a,prev-excel/does-not-exist.pdf\n",
            )
            with self.assertRaises(FileNotFoundError) as ctx:
                load_pilot_sources(csv_path)

        self.assertIn("Ghost Doc", str(ctx.exception))
        self.assertIn("does-not-exist.pdf", str(ctx.exception))

    def test_empty_local_file_fetches_the_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = self._write_csv(
                temp_dir, "Test Firm,4/2/2026,A Doc,https://example.test/a.html,\n"
            )
            [source] = load_pilot_sources(csv_path)

        self.assertIsNone(source.local_path)
        self.assertEqual("html", source.source_type)

    def test_absent_local_file_column_behaves_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sources.csv"
            path.write_text(
                "Firm,Date,Source,MR Link\nTest Firm,4/2/2026,A Doc,https://example.test/a.html\n",
                encoding="utf-8",
            )
            [source] = load_pilot_sources(path)

        self.assertIsNone(source.local_path)
        self.assertEqual("html", source.source_type)

    def test_arbitrary_pilot_format_csv_loads_and_respects_the_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            body = "".join(
                f"Firm {n},4/2/2026,Doc {n},https://example.test/{n}.html,\n" for n in range(3)
            )
            csv_path = self._write_csv(temp_dir, body)
            sources = load_pilot_sources(csv_path)

        self.assertEqual(3, len(sources))
        enforce_source_limit(sources, limit=3)
        with self.assertRaises(ValueError):
            enforce_source_limit(sources, limit=2)

    def test_pilot_schroders_pair_maps_to_distinct_local_pdfs(self) -> None:
        schroders = [source for source in load_pilot_sources() if source.firm == "Schroders"]

        self.assertEqual(2, len(schroders))
        self.assertEqual(
            {
                "Quarterly markets review - Q1 2026.pdf",
                "Our multi-asset investment views – March 2026.pdf",
            },
            {source.local_path.name for source in schroders},
        )
        self.assertEqual({"pdf"}, {source.source_type for source in schroders})

    def test_pilot_jpm_pair_maps_to_distinct_local_pdfs(self) -> None:
        jpm = [
            source
            for source in load_pilot_sources()
            if source.firm == "J.P. Morgan Asset Management"
        ]

        self.assertEqual(2, len(jpm))
        self.assertEqual(
            {
                "jp-morgan.pdf",
                "Global Asset Allocation Views 2Q 2026 _ J.P. Morgan Asset Management.pdf",
            },
            {source.local_path.name for source in jpm},
        )
        self.assertEqual({"pdf"}, {source.source_type for source in jpm})

    def test_detect_pdf_from_path_or_url(self) -> None:
        self.assertEqual("pdf", detect_source_type("https://example.com/a.pdf"))
        self.assertEqual("html", detect_source_type("https://example.com/a"))

    def test_visual_markup_counts_content_graphics(self) -> None:
        html = "<html>" + '<img src="c.png">' * 4 + "<figure></figure>" + "<svg></svg>" * 30 + "</html>"

        counts = count_visual_markup(html)

        self.assertEqual(4, counts["img"])
        self.assertEqual(1, counts["figure"])
        self.assertEqual(30, counts["svg"])

    def test_visual_heavy_flag_ignores_svg_icons(self) -> None:
        self.assertTrue(is_visual_heavy({"img": 4, "svg": 0, "canvas": 0, "figure": 1}))
        self.assertFalse(is_visual_heavy({"img": 3, "svg": 40, "canvas": 0, "figure": 0}))


def _load_word_fixture(name: str) -> tuple[list[dict[str, object]], float, float]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data["words"], data["page_width"], data["page_height"]


def _single_column_words() -> list[dict[str, object]]:
    # 30 lines that each span the page center — no interior gutter.
    return [
        {"x0": 50.0, "x1": 500.0, "top": 40.0 + row * 15, "bottom": 52.0 + row * 15}
        for row in range(30)
    ]


def _two_column_words() -> list[dict[str, object]]:
    # A left and a right column with an empty gutter across the page center.
    words: list[dict[str, object]] = []
    for row in range(30):
        top = 40.0 + row * 15
        words.append({"x0": 50.0, "x1": 250.0, "top": top, "bottom": top + 12})
        words.append({"x0": 330.0, "x1": 530.0, "top": top, "bottom": top + 12})
    return words


class ScrambleDetectorTest(unittest.TestCase):
    def test_flags_column_interleaved_jpm_page(self) -> None:
        # Real word boxes extracted from JPM pilot-04 p.2 (two-column layout that
        # pdfplumber interleaves line-by-line — the quote_not_found failure).
        words, width, height = _load_word_fixture("jpm_scrambled_p2_words.json")

        self.assertTrue(detect_scrambled_page(words, width, height))

    def test_does_not_flag_clean_single_column_ab_page(self) -> None:
        # Real word boxes from AB pilot-04 p.7 — clean single-column prose (its
        # own failure was a stitched quote, which must stay a failure).
        words, width, height = _load_word_fixture("ab_clean_p7_words.json")

        self.assertFalse(detect_scrambled_page(words, width, height))

    def test_flags_synthetic_two_column_layout(self) -> None:
        self.assertTrue(detect_scrambled_page(_two_column_words(), 595.0, 842.0))

    def test_does_not_flag_synthetic_single_column_layout(self) -> None:
        self.assertFalse(detect_scrambled_page(_single_column_words(), 595.0, 842.0))

    def test_too_little_text_is_not_flagged(self) -> None:
        self.assertFalse(detect_scrambled_page(_two_column_words()[:10], 595.0, 842.0))


class PrintToPdfIngestTest(unittest.TestCase):
    def test_visual_heavy_html_is_printed_and_flows_through_the_pdf_path(self) -> None:
        html = "<html>" + '<img src="chart.png">' * 6 + "<p>views live in charts</p></html>"
        printed_urls: list[str] = []

        def fake_printer(url: str, output_path: Path) -> None:
            printed_urls.append(url)
            shutil.copy2(FIXTURE_PRINTED_PDF, output_path)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.ingest._fetch_html", return_value=html):
                ingested = create_snapshot(_html_source(), temp_dir, printer=fake_printer)
            snapshot = ingested.snapshot_text_path.read_text(encoding="utf-8")
            meta = json.loads(
                (Path(temp_dir) / "aberdeen-outlook" / "ingest_meta.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(["https://example.test/outlook"], printed_urls)
        self.assertTrue(ingested.printed_pdf)
        self.assertEqual(1, ingested.page_count)
        self.assertEqual("printed.pdf", ingested.native_source_path.name)
        # Page chunks, not char chunks: the source is analyzed as a PDF.
        self.assertEqual(["p1-1"], [chunk.chunk_id for chunk in ingested.chunks])
        self.assertIn("Quarterly Outlook Fixture", snapshot)
        self.assertTrue(meta["printed_pdf"])
        self.assertTrue(meta["visual_heavy"])

    def test_light_html_keeps_the_text_path_and_never_prints(self) -> None:
        html = '<html><img src="logo.png"><p>' + "prose " * 50 + "</p></html>"

        def forbidden_printer(url: str, output_path: Path) -> None:
            raise AssertionError("printer must not run for a non-visual-heavy source")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.ingest._fetch_html", return_value=html):
                ingested = create_snapshot(_html_source(), temp_dir, printer=forbidden_printer)

        self.assertFalse(ingested.printed_pdf)
        self.assertIsNone(ingested.page_count)
        self.assertTrue(all(chunk.chunk_id.startswith("char:") for chunk in ingested.chunks))


class HeaderAliasTest(unittest.TestCase):
    """A pilot-family CSV using aliased headers (Entity Name / Title / External
    link) loads with no editing."""

    def _load(self, temp_dir: str, text: str) -> list:
        path = Path(temp_dir) / "sources.csv"
        path.write_text(text, encoding="utf-8")
        return load_pilot_sources(path)

    def test_aliased_headers_map_to_canonical_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sources = self._load(
                temp_dir,
                "Entity Name,Title,Date,External link\n"
                "BlackRock,Equity Outlook,3/25/2026,https://example.test/a.pdf\n"
                "T. Rowe Price,Monthly Update,4/8/2026,https://example.test/b.html\n",
            )

        self.assertEqual(["BlackRock", "T. Rowe Price"], [s.firm for s in sources])
        self.assertEqual(["Equity Outlook", "Monthly Update"], [s.source for s in sources])
        self.assertEqual(["3/25/2026", "4/8/2026"], [s.date for s in sources])
        # .pdf URL -> pdf route (remote), non-pdf -> html route.
        self.assertEqual(["pdf", "html"], [s.source_type for s in sources])
        self.assertEqual([None, None], [s.local_path for s in sources])

    def test_missing_required_column_raises_naming_what_was_seen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError) as ctx:
                # No firm-like column.
                self._load(temp_dir, "Title,Date,External link\nDoc,4/8/2026,https://x.test/a\n")

        self.assertIn("firm", str(ctx.exception))
        self.assertIn("Title", str(ctx.exception))

    def test_local_file_alias_is_honoured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            [source] = self._load(
                temp_dir,
                "Entity Name,Title,Date,External link,Local File\n"
                "PIMCO,A Doc,4/8/2026,https://example.test/a,prev-excel/PIMCO.pdf\n",
            )

        self.assertEqual("PIMCO.pdf", source.local_path.name)
        self.assertEqual("pdf", source.source_type)


class RemotePdfTest(unittest.TestCase):
    def _remote_pdf_source(self) -> SourceRecord:
        return SourceRecord(
            source_id="blackrock-outlook",
            firm="BlackRock",
            date="3/25/2026",
            source="Equity Outlook",
            url="https://example.test/docs/outlook.pdf?view=true",
            resolved_url="https://example.test/docs/outlook.pdf?view=true",
            source_type="pdf",
            local_path=None,
        )

    def test_remote_pdf_is_downloaded_and_flows_through_the_pdf_path(self) -> None:
        fetched: list[str] = []

        def fake_downloader(url: str, output_dir: Path) -> Path:
            fetched.append(url)
            target = output_dir / "outlook.pdf"
            shutil.copy2(FIXTURE_PRINTED_PDF, target)
            return target

        with tempfile.TemporaryDirectory() as temp_dir:
            ingested = create_snapshot(
                self._remote_pdf_source(), temp_dir, downloader=fake_downloader
            )

        self.assertEqual(["https://example.test/docs/outlook.pdf?view=true"], fetched)
        self.assertEqual(1, ingested.page_count)
        self.assertEqual(["p1-1"], [chunk.chunk_id for chunk in ingested.chunks])

    def test_filename_is_derived_from_the_url_path(self) -> None:
        self.assertEqual(
            "outlook.pdf", _pdf_filename_from_url("https://x.test/docs/outlook.pdf?view=true")
        )
        # A path without a .pdf suffix still lands on a .pdf file on disk.
        self.assertEqual("report.pdf", _pdf_filename_from_url("https://x.test/report"))

    def test_download_rejects_a_non_pdf_body(self) -> None:
        # A .pdf URL that actually returns an HTML consent/error page must fail
        # loudly, not feed junk to pdfplumber.
        class FakeResponse:
            content = b"<!doctype html><html>Access denied</html>"
            headers = {"Content-Type": "text/html"}

            def raise_for_status(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.ingest.requests.get", return_value=FakeResponse()):
                with self.assertRaises(ValueError) as ctx:
                    _download_pdf("https://x.test/a.pdf", Path(temp_dir))

        self.assertIn("did not return a PDF", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
