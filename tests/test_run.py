from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.ingest import Chunk, IngestedSource, SourceRecord
from src.run import analyze_source, _chunk_content, _html_chunk_text


def _pdf_source() -> SourceRecord:
    return SourceRecord(
        source_id="pimco-outlook",
        firm="PIMCO",
        date="6/1/2026",
        source="Layered Uncertainty",
        url="https://example.test/pimco",
        resolved_url="https://example.test/pimco",
        source_type="pdf",
        local_path=Path("/tmp/does-not-need-to-exist.pdf"),
    )


def _ingested_pdf(work_dir: Path) -> IngestedSource:
    source = _pdf_source()
    (work_dir / source.source_id).mkdir(parents=True, exist_ok=True)
    pdf_path = Path("/tmp/does-not-need-to-exist.pdf")
    chunks = [
        Chunk(chunk_id="p1-5", locator="p.1-5", source_path=pdf_path),
        Chunk(chunk_id="p6-10", locator="p.6-10", source_path=pdf_path),
    ]
    return IngestedSource(
        source=source,
        snapshot_text_path=work_dir / source.source_id / "snapshot.txt",
        native_source_path=pdf_path,
        chunks=chunks,
        page_count=10,
    )


def _candidate_json(chunk_id: str) -> str:
    return json.dumps(
        {
            "summary": f"chunk {chunk_id} covered fixed income positioning",
            "candidates": [
                {
                    "source_id": "pimco-outlook",
                    "chunk_id": chunk_id,
                    "sub_asset_raw": "IG credit",
                    "sub_asset_class": "Emerging Markets Equities",
                    "taxonomy_match": "semantic",
                    "view": "O",
                    "call_language": "explicit",
                    "evidence_kind": "prose",
                    "evidence_quote": "We are overweight the segment.",
                    "locator": "p.3",
                    "reasoning": "The manager states an overweight.",
                    "conflict": False,
                }
            ],
        }
    )


class AnalyzeSourceTest(unittest.TestCase):
    def test_collects_candidates_fills_template_and_rolls_memory(self) -> None:
        prompts: list[str] = []

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            prompts.append(prompt)
            chunk_id = "p6-10" if "p6-10" in prompt else "p1-5"
            return subprocess.CompletedProcess(command, 0, stdout=_candidate_json(chunk_id), stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            ingested = _ingested_pdf(work_dir)
            candidates, failures = analyze_source(
                ingested,
                work_dir,
                taxonomy_block="- Emerging Markets Equities",
                brain_text="no examples",
                engine="claude",
                runner=runner,
            )
            memory = (work_dir / "pimco-outlook" / "memory.md").read_text(encoding="utf-8")

        self.assertEqual(2, len(candidates))
        self.assertEqual([], failures)
        # Placeholders were substituted (no literal template tokens leak through).
        self.assertNotIn("{{", prompts[0])
        self.assertIn("Emerging Markets Equities", prompts[0])
        self.assertIn("pages 1-5", prompts[0])
        # Rolling memory carried the first chunk's ledger into the second call.
        self.assertIn("p6-10", prompts[1])
        self.assertIn("## Chunk p1-5", prompts[1])
        self.assertIn("Emerging Markets Equities=O[p.3]", memory)

    def test_unparseable_chunk_becomes_a_chunk_failure(self) -> None:
        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, stdout="not json at all", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            ingested = _ingested_pdf(work_dir)
            candidates, failures = analyze_source(
                ingested,
                work_dir,
                taxonomy_block="- Emerging Markets Equities",
                brain_text="none",
                engine="claude",
                runner=runner,
            )
            memory = (work_dir / "pimco-outlook" / "memory.md").read_text(encoding="utf-8")

        self.assertEqual([], candidates)
        self.assertEqual(2, len(failures))
        self.assertEqual({"json_parse_error"}, {f.reason_code for f in failures})
        self.assertIn("json_parse_error; chunk skipped", memory)


class ChunkContentTest(unittest.TestCase):
    def test_pdf_chunk_points_at_native_pages(self) -> None:
        ingested = _ingested_pdf(Path("/tmp"))
        content = _chunk_content(ingested, ingested.chunks[0])
        self.assertIn("pages 1-5", content)
        self.assertIn("rendered pages", content)

    def test_html_chunk_returns_char_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = Path(temp_dir) / "snapshot.txt"
            snapshot.write_text("ABCDEFGHIJ", encoding="utf-8")
            chunk = Chunk(chunk_id="char:2-5", locator="char:2-5", source_path=snapshot)
            self.assertEqual("CDE", _html_chunk_text(chunk))


if __name__ == "__main__":
    unittest.main()
