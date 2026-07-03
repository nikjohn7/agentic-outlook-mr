from __future__ import annotations

import unittest

from src.ingest import (
    count_visual_markup,
    detect_source_type,
    enforce_source_limit,
    is_visual_heavy,
    load_pilot_sources,
    resolve_url,
    strip_tracking_params,
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

        enforce_source_limit(sources, limit=5)
        with self.assertRaises(ValueError):
            enforce_source_limit(sources, limit=4)

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


if __name__ == "__main__":
    unittest.main()
