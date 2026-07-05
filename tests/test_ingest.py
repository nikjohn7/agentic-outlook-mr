from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ingest import (
    SourceRecord,
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

        enforce_source_limit(sources, limit=6)
        with self.assertRaises(ValueError):
            enforce_source_limit(sources, limit=5)

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


if __name__ == "__main__":
    unittest.main()
