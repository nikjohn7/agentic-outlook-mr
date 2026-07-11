from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.ingest import Chunk, IngestedSource, SourceRecord
from src.preflight import (
    PreflightRecord,
    load_preflight_sources,
    parse_content_verdicts,
    render_report,
    run_content_check,
    sweep,
    write_outputs,
)


def _source(source_id: str, firm: str, title: str, url: str, source_type: str) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        firm=firm,
        date="",
        source=title,
        url=url,
        resolved_url=url,
        source_type=source_type,
    )


def _fake_ingested(source: SourceRecord, work_dir: Path, *, text: str, date: str) -> IngestedSource:
    out = work_dir / source.source_id
    out.mkdir(parents=True, exist_ok=True)
    snapshot = out / "snapshot.txt"
    snapshot.write_text(text, encoding="utf-8")
    from dataclasses import replace

    return IngestedSource(
        source=replace(source, date=date),
        snapshot_text_path=snapshot,
        native_source_path=snapshot,
        chunks=[Chunk(chunk_id="p1-1", locator="p.1", source_path=snapshot)],
        page_count=3 if source.source_type == "pdf" else None,
    )


class SweepTest(unittest.TestCase):
    def test_ok_and_failed_rows_and_sweep_continues(self) -> None:
        sources = [
            _source("s1", "Aberdeen", "EMD Outlook", "https://x.test/a.pdf", "pdf"),
            _source("s2", "Bad Firm", "Dead Link", "https://x.test/dead", "html"),
            _source("s3", "Columbia", "Macro View", "https://x.test/c", "html"),
        ]

        def snapshotter(source: SourceRecord, work_dir: Path) -> IngestedSource:
            if source.source_id == "s2":
                raise TimeoutError("connection timed out")
            text = "PIMCO body " * 60 if source.source_id == "s1" else "Columbia prose " * 40
            date = "15/06/2026" if source.source_id == "s1" else "03/07/2026"
            return _fake_ingested(source, work_dir, text=text, date=date)

        with tempfile.TemporaryDirectory() as temp_dir:
            records, heads = sweep(sources, Path(temp_dir) / "work", snapshotter=snapshotter)

        # All three recorded; the failure in the middle did not stop the sweep.
        self.assertEqual(["s1", "s2", "s3"], [r.source_id for r in records])
        self.assertEqual(["ok", "FAILED", "ok"], [r.status for r in records])
        failed = records[1]
        self.assertIn("TimeoutError", failed.error)
        self.assertIn("connection timed out", failed.error)
        # OK PDF row carries page count + doc date + provenance.
        self.assertEqual(3, records[0].page_count)
        self.assertEqual("15/06/2026", records[0].date)
        self.assertEqual("pdf_text", records[0].date_from)
        # OK HTML row carries a char count and html-sourced date.
        self.assertIsNotNone(records[2].char_count)
        self.assertEqual("html", records[2].date_from)
        # Heads captured only for the OK sources.
        self.assertEqual({"s1", "s3"}, set(heads))
        self.assertTrue(heads["s1"].startswith("PIMCO body"))


class ContentCheckTest(unittest.TestCase):
    def _records(self) -> list[PreflightRecord]:
        return [
            PreflightRecord("s1", "Aberdeen", "EMD Outlook", "u1", "ok"),
            PreflightRecord("s2", "Bad", "Dead", "u2", "FAILED", error="TimeoutError: x"),
            PreflightRecord("s3", "Columbia", "Macro View", "u3", "ok"),
        ]

    def test_verdicts_are_mapped_onto_ok_records(self) -> None:
        response = json.dumps(
            {
                "verdicts": [
                    {"index": 0, "verdict": "looks_right", "reason": ""},
                    {"index": 1, "verdict": "suspect", "reason": "cookie consent wall"},
                ]
            }
        )

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, stdout=response, stderr="")

        records = self._records()
        run_content_check(
            records,
            {"s1": "real body text", "s3": "consent required"},
            engine="codex",
            model=None,
            effort="medium",
            runner=runner,
        )

        # Only the two OK sources were checked, in checkable order (s1, s3).
        self.assertEqual("looks_right", records[0].content_check)
        self.assertEqual("", records[0].content_reason)
        self.assertEqual("suspect", records[2].content_check)
        self.assertEqual("cookie consent wall", records[2].content_reason)
        # The FAILED source is never content-checked.
        self.assertEqual("", records[1].content_check)

    def test_failed_call_degrades_every_ok_source_to_unchecked(self) -> None:
        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="codex blew up")

        records = self._records()
        run_content_check(
            records, {"s1": "a", "s3": "b"}, engine="codex", model=None, effort="medium", runner=runner
        )

        self.assertEqual("unchecked", records[0].content_check)
        self.assertEqual("unchecked", records[2].content_check)
        self.assertIn("unavailable", records[0].content_reason)
        self.assertEqual("", records[1].content_check)  # FAILED untouched

    def test_parser_rejects_bad_verdict_value(self) -> None:
        with self.assertRaises(ValueError):
            parse_content_verdicts(json.dumps({"verdicts": [{"index": 0, "verdict": "maybe"}]}))


class OutputShapeTest(unittest.TestCase):
    def _records(self) -> list[PreflightRecord]:
        return [
            PreflightRecord(
                "s1", "Aberdeen", "EMD Outlook", "u1", "ok",
                source_type="pdf", page_count=12, char_count=9000,
                date="15/06/2026", date_from="pdf_text", content_check="looks_right",
            ),
            PreflightRecord(
                "s2", "Bad Firm", "Dead Link", "u2", "FAILED",
                source_type="html", error="HTTPError: 404 Not Found",
            ),
            PreflightRecord(
                "s3", "Columbia", "Macro View", "u3", "ok",
                source_type="html", char_count=4000, printed_pdf=True, visual_heavy=True,
                date="", date_from="", content_check="suspect",
                content_reason="professional-investor gate",
            ),
        ]

    def test_csv_has_header_and_one_row_per_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path, report_path = write_outputs(temp_dir, self._records())
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(3, len(rows))
            self.assertEqual("ok", rows[0]["status"])
            self.assertEqual("12", rows[0]["page_count"])
            self.assertEqual("FAILED", rows[1]["status"])
            self.assertEqual("suspect", rows[2]["content_check"])
            self.assertTrue(report_path.exists())

    def test_report_leads_with_failed_then_suspects_then_dates(self) -> None:
        report = render_report(self._records())

        self.assertIn("## FAILED links", report)
        self.assertIn("Bad Firm — Dead Link", report)
        self.assertIn("HTTPError: 404 Not Found", report)
        self.assertIn("## Suspect content", report)
        self.assertIn("professional-investor gate", report)
        self.assertIn("## Date extraction", report)
        # FAILED section appears before the suspects and date sections.
        self.assertLess(report.index("## FAILED links"), report.index("## Suspect content"))
        self.assertLess(report.index("## Suspect content"), report.index("## Date extraction"))

    def test_empty_failed_and_suspect_sections_render_placeholders(self) -> None:
        clean = [
            PreflightRecord(
                "s1", "Firm", "Doc", "u1", "ok", source_type="pdf",
                date="15/06/2026", date_from="pdf_text", content_check="looks_right",
            )
        ]
        report = render_report(clean)
        self.assertIn("every link fetched", report)
        self.assertIn("none flagged suspect", report)


class LoadSourcesTest(unittest.TestCase):
    def test_target_workbook_list_loads_via_target_loader(self) -> None:
        sources = load_preflight_sources("excel-file/Target Ingestion List.csv")
        self.assertGreaterEqual(len(sources), 30)
        self.assertTrue(all(s.firm for s in sources))

    def test_pilot_family_csv_falls_back_to_pilot_loader(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sources.csv"
            path.write_text(
                "Entity Name,Title,External link\n"
                "BlackRock,Equity Outlook,https://example.test/a.pdf\n",
                encoding="utf-8",
            )
            sources = load_preflight_sources(path)

        self.assertEqual(1, len(sources))
        self.assertEqual("BlackRock", sources[0].firm)


if __name__ == "__main__":
    unittest.main()
