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
    detect_source_type,
    enforce_source_limit,
    is_visual_heavy,
    load_pilot_sources,
    resolve_url,
    strip_tracking_params,
)


FIXTURE_PRINTED_PDF = Path(__file__).parent / "fixtures" / "printed_page.pdf"


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
