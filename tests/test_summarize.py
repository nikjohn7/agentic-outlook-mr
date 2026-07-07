from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document

from src import summarize
from src.summarize import (
    RES_SAME_CALL,
    RES_SAME_VIEW,
    RES_SINGLE,
    RES_SUPERSEDED,
    RES_UNRESOLVED,
    RunSource,
    SummarizeError,
    bind_pages,
    digest_source,
    load_run_sources,
    parse_firm_page,
    reconcile_firm_calls,
    run_firmpages,
)

_OUTPUT_COLUMNS = (
    "Firm", "Date", "Source", "URL", "Sub-Asset Class", "Asset Class Category",
    "Canva Groupings", "Asset Class", "View", "Full Commentary", "confidence",
    "band", "review_flag", "basis", "checker_strength", "call_language",
)
_CC_COLUMNS = (
    "Firm", "Sub-Asset Class", "views", "run_files", "source_titles", "dates",
    "confidence_bands", "bucket", "agent_verdict", "note", "needs_human",
)


def _orow(firm: str, leaf: str, view: str, **extra: str) -> dict[str, str]:
    base = {
        "Firm": firm,
        "Date": extra.get("Date", "1/1/2026"),
        "Source": extra.get("Source", f"{firm} Outlook"),
        "URL": extra.get("URL", "http://example.com"),
        "Sub-Asset Class": leaf,
        "View": view,
        "Full Commentary": extra.get("Full Commentary", f"{firm} on {leaf}."),
        "confidence": extra.get("confidence", "80"),
        "band": extra.get("band", "High"),
    }
    return base


def _write_output(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_crosscheck(path: Path, entries: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CC_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(entries)


def _cc(firm: str, leaf: str, verdict: str, *, needs_human: str = "false", note: str = "") -> dict[str, str]:
    return {
        "Firm": firm,
        "Sub-Asset Class": leaf,
        "bucket": "conflicting_views",
        "agent_verdict": verdict,
        "note": note,
        "needs_human": needs_human,
    }


class LoadRunSourcesTests(unittest.TestCase):
    """Map a run's own artifacts (work dir + output.csv) back to per-source inputs."""

    def _build_run(self, tmp: Path, sources: list[dict], output_rows: list[dict[str, str]]) -> Path:
        run_dir = tmp / "runs" / "demo"
        work_dir = tmp / "work" / "demo"
        run_dir.mkdir(parents=True)
        for spec in sources:
            sdir = work_dir / spec["sid"]
            sdir.mkdir(parents=True)
            (sdir / "memory.md").write_text(
                f"# {spec['firm']} — {spec['title']}  ({spec['sid']})\n\n## Chunk\nSummary: s\n",
                encoding="utf-8",
            )
            native = sdir / spec["native"]
            native.write_bytes(b"%PDF-1.4 stub")
            (sdir / "chunks.json").write_text(
                json.dumps([{"chunk_id": "p1", "locator": "p.1", "source_path": f"work/demo/{spec['sid']}/{spec['native']}"}]),
                encoding="utf-8",
            )
        _write_output(run_dir / "output.csv", output_rows)
        return run_dir

    def test_maps_header_native_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            run_dir = self._build_run(
                tmp,
                [{"sid": "acme-outlook", "firm": "Acme", "title": "Acme Outlook 2026", "native": "acme.pdf"}],
                [_orow("Acme", "US Equities", "O", Source="Acme Outlook 2026", URL="http://acme/doc", Date="2/2/2026")],
            )
            self.addCleanup(_restore_root, summarize.PROJECT_ROOT)
            summarize.PROJECT_ROOT = tmp

            sources = load_run_sources(run_dir)

            self.assertEqual(1, len(sources))
            src = sources[0]
            self.assertEqual("Acme", src.firm)
            self.assertEqual("Acme Outlook 2026", src.title)
            self.assertEqual("acme.pdf", src.native_doc.name)
            self.assertTrue(src.native_doc.is_file())
            self.assertEqual("http://acme/doc", src.url)
            self.assertEqual("2/2/2026", src.date)
            self.assertEqual(1, len(src.kept_rows))

    def test_grouped_row_attributes_url_and_date_per_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            combined = _orow(
                "TRP", "US Equities", "O",
                Source="Monthly Update | The UK View",
                URL="http://trp/monthly | http://trp/uk",
                Date="4/8/2026 | 3/18/2026",
            )
            run_dir = self._build_run(
                tmp,
                [
                    {"sid": "trp-monthly", "firm": "TRP", "title": "Monthly Update", "native": "m.pdf"},
                    {"sid": "trp-uk", "firm": "TRP", "title": "The UK View", "native": "u.pdf"},
                ],
                [combined],
            )
            self.addCleanup(_restore_root, summarize.PROJECT_ROOT)
            summarize.PROJECT_ROOT = tmp

            sources = {s.source_id: s for s in load_run_sources(run_dir)}

            self.assertEqual("http://trp/monthly", sources["trp-monthly"].url)
            self.assertEqual("4/8/2026", sources["trp-monthly"].date)
            self.assertEqual("http://trp/uk", sources["trp-uk"].url)
            self.assertEqual("3/18/2026", sources["trp-uk"].date)
            # Both documents are attributed the shared combined row.
            self.assertEqual(1, len(sources["trp-monthly"].kept_rows))
            self.assertEqual(1, len(sources["trp-uk"].kept_rows))

    def test_only_filter_and_unknown_id_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            run_dir = self._build_run(
                tmp,
                [
                    {"sid": "a", "firm": "A", "title": "A Doc", "native": "a.pdf"},
                    {"sid": "b", "firm": "B", "title": "B Doc", "native": "b.pdf"},
                ],
                [_orow("A", "US Equities", "O", Source="A Doc")],
            )
            self.addCleanup(_restore_root, summarize.PROJECT_ROOT)
            summarize.PROJECT_ROOT = tmp

            self.assertEqual(["a"], [s.source_id for s in load_run_sources(run_dir, only={"a"})])
            with self.assertRaises(SummarizeError):
                load_run_sources(run_dir, only={"nope"})


def _restore_root(value: Path) -> None:
    summarize.PROJECT_ROOT = value


class DigestPlumbingTests(unittest.TestCase):
    """The native document, the kept calls, and the rolling memory reach the prompt."""

    def test_native_calls_and_memory_reach_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            native = Path(tmp) / "doc.pdf"
            native.write_bytes(b"%PDF stub")
            source = RunSource(
                source_id="acme",
                firm="Acme",
                title="Acme Outlook",
                url="http://acme",
                date="1/1/2026",
                native_doc=native,
                memory_text="MEMORY_MARKER: Acme is bullish EM.",
                kept_rows=(
                    {"Sub-Asset Class": "EM Equities", "View": "O", "basis": "stated",
                     "Full Commentary": "COMMENTARY_MARKER supports EM."},
                ),
            )
            captured: dict[str, str] = {}

            def runner(command: list[str], prompt: str):
                captured["prompt"] = prompt
                payload = {
                    "firm": "Acme", "document_title": "Acme Outlook", "url": "http://acme",
                    "date": "1/1/2026",
                    "themes": [{"label": "EM", "summary": "bullish", "points": ["EM"]}],
                    "stances": [{"asset_class": "EM Equities", "stance": "overweight", "detail": "d"}],
                }
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

            result = digest_source(source, engine="codex", model=None, effort="medium", runner=runner)

            self.assertEqual("Acme", result["firm"])
            self.assertIn(str(native.resolve()), captured["prompt"])
            self.assertIn("MEMORY_MARKER", captured["prompt"])
            self.assertIn("EM Equities", captured["prompt"])
            self.assertIn("COMMENTARY_MARKER", captured["prompt"])


class ReconcileTests(unittest.TestCase):
    """One test per Task 2 bucket, including unresolved-keeps-both."""

    def _finals(self, tmp: Path, files: list[list[dict[str, str]]], cc: list[dict[str, str]] | None):
        paths = []
        for i, rows in enumerate(files):
            p = tmp / f"out{i}.csv"
            _write_output(p, rows)
            paths.append(p)
        cc_path = None
        if cc is not None:
            cc_path = tmp / "crosscheck.csv"
            _write_crosscheck(cc_path, cc)
        return {(f.firm_key, f.leaf): f for f in reconcile_firm_calls(paths, cc_path)}

    def test_single_key_is_final(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            finals = self._finals(Path(tmp), [[_orow("Acme", "US Equities", "O")]], None)
            call = finals[("acme", "US Equities")]
            self.assertEqual(RES_SINGLE, call.resolution)
            self.assertFalse(call.unresolved)
            self.assertEqual("O", call.view)

    def test_duplicate_same_view_keeps_highest_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            finals = self._finals(
                Path(tmp),
                [
                    [_orow("Acme", "US Equities", "O", confidence="80", **{"Full Commentary": "LOW"})],
                    [_orow("Acme", "US Equities", "O", confidence="90", **{"Full Commentary": "HIGH"})],
                ],
                None,
            )
            call = finals[("acme", "US Equities")]
            self.assertEqual(RES_SAME_VIEW, call.resolution)
            self.assertFalse(call.unresolved)
            self.assertEqual("HIGH", call.commentary)
            self.assertEqual(2, len(call.provenance))

    def test_conflicting_superseded_keeps_most_recent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            finals = self._finals(
                Path(tmp),
                [
                    [_orow("Acme", "US Equities", "O", Date="1/1/2026")],
                    [_orow("Acme", "US Equities", "U", Date="6/1/2026")],
                ],
                [_cc("Acme", "US Equities", "superseded", note="June reading supersedes January")],
            )
            call = finals[("acme", "US Equities")]
            self.assertEqual(RES_SUPERSEDED, call.resolution)
            self.assertFalse(call.unresolved)
            self.assertEqual("U", call.view)  # the June (more recent) row wins

    def test_conflicting_same_call_keeps_one_highest_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            finals = self._finals(
                Path(tmp),
                [
                    [_orow("Acme", "US Equities", "O", confidence="70")],
                    [_orow("Acme", "US Equities", "N", confidence="85")],
                ],
                [_cc("Acme", "US Equities", "same_call", note="same substance")],
            )
            call = finals[("acme", "US Equities")]
            self.assertEqual(RES_SAME_CALL, call.resolution)
            self.assertFalse(call.unresolved)
            self.assertEqual("N", call.view)  # highest-confidence row kept

    def test_conflicting_needs_human_keeps_both_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            finals = self._finals(
                Path(tmp),
                [
                    [_orow("Acme", "US Equities", "O")],
                    [_orow("Acme", "US Equities", "U")],
                ],
                [_cc("Acme", "US Equities", "needs_human", needs_human="true")],
            )
            call = finals[("acme", "US Equities")]
            self.assertEqual(RES_UNRESOLVED, call.resolution)
            self.assertTrue(call.unresolved)
            self.assertEqual(("O", "U"), call.views)
            self.assertEqual("", call.view)
            self.assertEqual(2, len(call.provenance))

    def test_conflicting_without_crosscheck_is_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            finals = self._finals(
                Path(tmp),
                [
                    [_orow("Acme", "US Equities", "O")],
                    [_orow("Acme", "US Equities", "U")],
                ],
                None,
            )
            call = finals[("acme", "US Equities")]
            self.assertEqual(RES_UNRESOLVED, call.resolution)
            self.assertTrue(call.unresolved)
            self.assertEqual(("O", "U"), call.views)


class FirmPagesTests(unittest.TestCase):
    def _digest(self, dir_: Path, sid: str, firm: str, title: str) -> None:
        (dir_ / f"{sid}.json").write_text(
            json.dumps({
                "firm": firm, "document_title": title, "url": f"http://{sid}", "date": "1/1/2026",
                "themes": [{"label": "Macro", "summary": "s", "points": []}],
                "stances": [{"asset_class": "US Equities", "stance": "overweight", "detail": "d"}],
            }),
            encoding="utf-8",
        )

    def test_multi_source_firm_without_crosscheck_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            digests = tmp / "digests"
            digests.mkdir()
            self._digest(digests, "acme-a", "Acme", "Acme A")
            self._digest(digests, "acme-b", "Acme", "Acme B")
            out = tmp / "out.csv"
            _write_output(out, [_orow("Acme", "US Equities", "O")])

            def runner(command, prompt):
                raise AssertionError("must not call the LLM when it should fail loudly")

            with self.assertRaises(SummarizeError):
                run_firmpages(digests, [out], tmp / "pages", crosscheck_path=None, runner=runner)

    def test_single_source_passthrough_writes_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            digests = tmp / "digests"
            digests.mkdir()
            self._digest(digests, "acme-a", "Acme", "Acme A")
            out = tmp / "out.csv"
            _write_output(out, [_orow("Acme", "US Equities", "O")])

            page = "# Acme\n\nFraming.\n\n## Macro\n- point\n\n## Sources\n- [Acme A](http://acme-a)\n"

            def runner(command, prompt):
                return subprocess.CompletedProcess(command, 0, stdout=page, stderr="")

            written = run_firmpages(digests, [out], tmp / "pages", crosscheck_path=None, runner=runner)

            self.assertEqual(1, len(written))
            self.assertTrue(written[0].read_text(encoding="utf-8").startswith("# Acme"))

    def test_firm_page_parser_requires_heading_and_sources(self) -> None:
        with self.assertRaises(ValueError):
            parse_firm_page("no heading and no sources")
        with self.assertRaises(ValueError):
            parse_firm_page("# Acme\n\nbody but no sources section")
        ok = parse_firm_page("# Acme\n\nbody\n\n## Sources\n- [X](http://x)\n")
        self.assertTrue(ok.startswith("# Acme"))


class BinderTests(unittest.TestCase):
    def _pages(self, tmp: Path) -> Path:
        pages = tmp / "pages"
        pages.mkdir()
        (pages / "acme.md").write_text(
            "# Acme\n\nFraming paragraph.\n\n## Macro: shift\n- named specific\n\n"
            "## Sources\n- [Acme A](http://a)\n- [Acme B](http://b)\n",
            encoding="utf-8",
        )
        (pages / "beta.md").write_text(
            "# Beta\n\nBeta framing.\n\n## Equities\nProse line.\n\n## Sources\n- [Beta X](http://x)\n",
            encoding="utf-8",
        )
        return pages

    def _document_xml(self, path: Path) -> bytes:
        with zipfile.ZipFile(path) as archive:
            return archive.read("word/document.xml")

    def test_docx_has_expected_headings_and_pagebreaks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            out = bind_pages(self._pages(tmp), tmp / "sample.docx", title="2026 Summaries")
            document = Document(str(out))
            h1 = [p.text for p in document.paragraphs if p.style.name == "Heading 1"]
            self.assertEqual(["Acme", "Beta"], h1)
            # A page break before each firm (title page + two firms) -> 3 breaks.
            self.assertEqual(3, self._document_xml(out).count(b"w:type=\"page\""))

    def test_content_stable_across_two_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pages = self._pages(tmp)
            a = bind_pages(pages, tmp / "a.docx")
            b = bind_pages(pages, tmp / "b.docx")
            self.assertEqual(self._document_xml(a), self._document_xml(b))


if __name__ == "__main__":
    unittest.main()
