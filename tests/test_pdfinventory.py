from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.pdfinventory import flagged_pages, page_reports


class PdfInventoryTest(unittest.TestCase):
    def test_graphics_heavy_page_survives_full_markdown_coverage(self) -> None:
        # A synthetic two-page document: identical words, one rectangle grid.
        text = "We prefer quality credit and remain neutral equities."
        rects = [
            {"x0": 50 + x * 20, "x1": 60 + x * 20,
             "top": 100 + y * 20, "bottom": 110 + y * 20}
            for x in range(8) for y in range(10)
        ]
        pages = [
            SimpleNamespace(
                page_number=number, width=600, height=800, curves=[],
                images=[], lines=[], rects=graphics, extract_text=lambda: text,
                close=lambda: None,
            )
            for number, graphics in ((1, []), (2, rects))
        ]
        pdf = MagicMock()
        pdf.__enter__.return_value.pages = pages
        with tempfile.TemporaryDirectory() as temp_dir:
            md_path = Path(temp_dir) / "source.md"
            md_path.write_text("# Outlook\n" + text, encoding="utf-8")
            with patch("src.pdfinventory.pdfplumber.open", return_value=pdf):
                reports = page_reports(md_path, Path(temp_dir) / "source.pdf")
                self.assertEqual(1.0, reports[1].text_coverage)
                self.assertFalse(reports[1].low_coverage)
                self.assertTrue(reports[1].graphics_heavy)
                self.assertFalse(reports[0].flagged)
                self.assertEqual((2,), flagged_pages(md_path, Path(temp_dir) / "source.pdf"))
