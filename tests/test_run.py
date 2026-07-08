from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from types import SimpleNamespace

from src.ingest import Chunk, IngestedSource, SourceRecord
from src.run import (
    analyze_source,
    load_sources,
    resolve_engine_settings,
    run_pipeline,
    _check_candidates,
    _chunk_content,
    _html_chunk_text,
    _make_arbiter,
    _make_quote_visual_verifier,
    _resolve_groups,
)
from src.schemas import CandidateCall, SourceInfo


FIXTURE_PRINTED_PDF = Path(__file__).parent / "fixtures" / "printed_page.pdf"


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

    def test_initial_memory_seeds_the_first_chunk_prompt(self) -> None:
        prompts: list[str] = []

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            prompts.append(prompt)
            return subprocess.CompletedProcess(command, 0, stdout=_candidate_json("p1-5"), stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            ingested = _ingested_pdf(work_dir)
            analyze_source(
                ingested,
                work_dir,
                taxonomy_block="- Emerging Markets Equities",
                brain_text="none",
                engine="claude",
                runner=runner,
                initial_memory="\n## Companion document already analyzed\nEM=O[p.2]\n",
            )

        self.assertIn("Companion document already analyzed", prompts[0])
        self.assertIn("EM=O[p.2]", prompts[0])

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


class LoadSourcesTest(unittest.TestCase):
    def test_pilot_and_target_keywords_route_to_the_builtin_loaders(self) -> None:
        self.assertEqual(7, len(load_sources("pilot")))
        self.assertTrue(load_sources("target"))  # non-empty target batch

    def test_arbitrary_path_loads_a_pilot_format_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "second-set.csv"
            path.write_text(
                "Firm,Date,Source,MR Link,local_file\n"
                "Test Firm,4/2/2026,A Doc,https://example.test/a.html,\n",
                encoding="utf-8",
            )
            sources = load_sources(str(path))

        self.assertEqual(1, len(sources))
        self.assertEqual("html", sources[0].source_type)


class ResolveEngineSettingsTest(unittest.TestCase):
    def test_claude_requires_an_explicit_model(self) -> None:
        with self.assertRaises(ValueError):
            resolve_engine_settings("claude", None, "high")

    def test_claude_passes_model_and_effort_through(self) -> None:
        self.assertEqual(("fable", "max"), resolve_engine_settings("claude", "fable", "max"))

    def test_codex_pins_model_and_rejects_overrides(self) -> None:
        self.assertEqual(("gpt-5.5", "high"), resolve_engine_settings("codex", None, "high"))
        self.assertEqual(("gpt-5.5", "low"), resolve_engine_settings("codex", "gpt-5.5", "low"))
        with self.assertRaises(ValueError):
            resolve_engine_settings("codex", "o3", "high")

    def test_effort_is_required_and_validated_per_engine(self) -> None:
        with self.assertRaises(ValueError):
            resolve_engine_settings("claude", "fable", None)
        with self.assertRaises(ValueError):
            resolve_engine_settings("claude", "fable", "minimal")
        with self.assertRaises(ValueError):
            resolve_engine_settings("codex", None, "max")

    def test_model_and_effort_reach_the_engine_command(self) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=_candidate_json("p1-5"), stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            ingested = _ingested_pdf(work_dir)
            analyze_source(
                ingested,
                work_dir,
                taxonomy_block="- Emerging Markets Equities",
                brain_text="none",
                engine="claude",
                model="fable",
                effort="high",
                runner=runner,
            )

        self.assertEqual(["--model", "fable"], commands[0][2:4])
        self.assertEqual(["--effort", "high"], commands[0][4:6])


class CheckerAndArbiterStepTest(unittest.TestCase):
    def test_check_candidates_maps_verdicts_and_uses_engine_settings(self) -> None:
        commands: list[list[str]] = []
        prompts: list[str] = []
        verdicts_json = json.dumps(
            {
                "verdicts": [
                    {
                        "index": 0,
                        "supports_view": "pass",
                        "forward_looking": "unclear",
                        "asset_match": "pass",
                        "evidence_strength": "thin",
                        "note": "thin stance",
                    },
                    # Out-of-range index must be ignored, not crash the run.
                    {
                        "index": 7,
                        "supports_view": "pass",
                        "forward_looking": "pass",
                        "asset_match": "pass",
                        "evidence_strength": "decisive",
                        "note": "",
                    },
                ]
            }
        )

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            prompts.append(prompt)
            return subprocess.CompletedProcess(command, 0, stdout=verdicts_json, stderr="")

        verdict_map, failure = _check_candidates(
            _pdf_source(),
            [_call_candidate()],
            conventions="A two-sided path nets to N.",
            engine="codex",
            model=None,
            effort="high",
            runner=runner,
        )

        self.assertIsNone(failure)
        self.assertEqual([0], list(verdict_map))
        self.assertEqual("thin stance", verdict_map[0].note)
        self.assertEqual("thin", verdict_map[0].evidence_strength)
        self.assertIn('model_reasoning_effort="high"', commands[0])
        # The house conventions were injected into the checker prompt.
        self.assertIn("A two-sided path nets to N.", prompts[0])
        self.assertNotIn("{{conventions}}", prompts[0])

    def test_check_candidates_injects_rolling_memory(self) -> None:
        prompts: list[str] = []
        verdicts_json = json.dumps(
            {
                "verdicts": [
                    {
                        "index": 0,
                        "supports_view": "pass",
                        "forward_looking": "pass",
                        "asset_match": "pass",
                        "evidence_strength": "decisive",
                        "note": "",
                    }
                ]
            }
        )

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            prompts.append(prompt)
            return subprocess.CompletedProcess(command, 0, stdout=verdicts_json, stderr="")

        verdict_map, failure = _check_candidates(
            _pdf_source(),
            [_call_candidate()],
            memory_text="## Chunk p1-5\nSummary: MEMORY-LEDGER-MARKER covered EM positioning.\n",
            conventions="A two-sided path nets to N.",
            engine="codex",
            model=None,
            effort="high",
            runner=runner,
        )

        self.assertIsNone(failure)
        # The source's whole-file rolling memory reached the checker prompt, and
        # the placeholder was substituted (no literal token leaks through).
        self.assertIn("MEMORY-LEDGER-MARKER", prompts[0])
        self.assertNotIn("{{memory}}", prompts[0])

    def test_check_candidates_engine_error_degrades_to_failure_record(self) -> None:
        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="codex blew up")

        verdict_map, failure = _check_candidates(
            _pdf_source(),
            [_call_candidate()],
            engine="codex",
            model=None,
            effort="high",
            runner=runner,
        )

        self.assertEqual({}, verdict_map)
        self.assertEqual("checker_error", failure.reason_code)
        self.assertEqual("checker", failure.chunk_id)

    def test_arbiter_closure_returns_decision_and_swallows_engine_errors(self) -> None:
        group = [
            SimpleNamespace(candidate=_call_candidate()),
            SimpleNamespace(candidate=_call_candidate()),
        ]

        def good_runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command, 0, stdout='{"winning_index": 1, "reasoning": "dial wins"}', stderr=""
            )

        arbiter = _make_arbiter(
            "brain text", engine="codex", model=None, effort="medium", runner=good_runner
        )
        self.assertEqual((1, "dial wins"), arbiter(group))

        def bad_runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

        arbiter = _make_arbiter(
            "brain text", engine="codex", model=None, effort="medium", runner=bad_runner
        )
        winner, reasoning = arbiter(group)
        self.assertIsNone(winner)
        self.assertIn("arbiter error", reasoning)


class GroupResolutionTest(unittest.TestCase):
    def _sources(self) -> list[SourceRecord]:
        def record(source_id: str, title: str) -> SourceRecord:
            return SourceRecord(
                source_id=source_id,
                firm="Schroders",
                date="4/1/2026",
                source=title,
                url="https://example.test/x",
                resolved_url="https://example.test/x",
                source_type="html",
            )

        return [
            record("schroders-review", "Quarterly Markets Review Q1"),
            record("schroders-outlook", "Global Investment Outlook Q2"),
        ]

    def test_resolves_notes_and_drops_unknown_ids_with_warnings(self) -> None:
        response = json.dumps(
            {
                "groups": [
                    {
                        "source_ids": ["schroders-review", "schroders-outlook", "ghost-doc"],
                        "note": "combine the Schroders pair",
                    }
                ],
                "unmatched_notes": ["also merge the Fidelity docs"],
            }
        )

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, stdout=response, stderr="")

        groups, warnings = _resolve_groups(
            self._sources(), "notes text", engine="codex", model=None, effort="low", runner=runner
        )

        self.assertEqual(1, len(groups))
        self.assertEqual(["schroders-review", "schroders-outlook"], groups[0]["source_ids"])
        self.assertEqual("group-1", groups[0]["group_id"])
        self.assertTrue(any("ghost-doc" in warning for warning in warnings))
        self.assertTrue(any("unmatched note" in warning for warning in warnings))

    def test_note_resolving_to_one_source_is_ignored_with_warning(self) -> None:
        response = json.dumps(
            {
                "groups": [{"source_ids": ["schroders-review", "ghost-doc"], "note": "pair up"}],
                "unmatched_notes": [],
            }
        )

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, stdout=response, stderr="")

        groups, warnings = _resolve_groups(
            self._sources(), "notes text", engine="codex", model=None, effort="low", runner=runner
        )

        self.assertEqual([], groups)
        self.assertTrue(any("did not resolve to two run sources" in w for w in warnings))

    def test_resolver_engine_error_degrades_to_ungrouped_run(self) -> None:
        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

        groups, warnings = _resolve_groups(
            self._sources(), "notes text", engine="codex", model=None, effort="low", runner=runner
        )

        self.assertEqual([], groups)
        self.assertIn("proceeds ungrouped", warnings[0])


class RunPipelineOutRootTest(unittest.TestCase):
    """--out-root reroots both the run dir and the work dir; absent keeps the
    default runs/<id> + work/<id> paths."""

    def _source(self) -> SourceRecord:
        return SourceRecord(
            source_id="fixture-doc",
            firm="Fixture Firm",
            date="",
            source="Fixture Outlook",
            url="https://example.test/fixture",
            resolved_url="https://example.test/fixture",
            source_type="pdf",
            local_path=FIXTURE_PRINTED_PDF,
        )

    def _runner(self):
        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            # The checker prompt is the only one carrying native_source_path in
            # its appended inputs; everything else is an analyze chunk call.
            if "native_source_path" in prompt:
                out = json.dumps(
                    {
                        "verdicts": [
                            {
                                "index": 0,
                                "supports_view": "pass",
                                "forward_looking": "pass",
                                "asset_match": "pass",
                                "evidence_strength": "decisive",
                                "note": "",
                            }
                        ]
                    }
                )
            else:
                out = _candidate_json("p1-1")
            return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")

        return runner

    def test_out_root_reroots_run_and_work_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_root = Path(temp_dir) / "client-runs" / "batch"
            result, _failures, run_dir = run_pipeline(
                sources=[self._source()],
                run_id="pf-01",
                engine="claude",
                model="fable",
                effort="high",
                runner=self._runner(),
                out_root=out_root,
            )

            self.assertEqual(out_root / "pf-01", run_dir)
            self.assertTrue((out_root / "pf-01" / "output.csv").exists())
            self.assertTrue((out_root / "work" / "pf-01" / "fixture-doc").is_dir())
            # Nothing leaked into the default trees.
            self.assertFalse((Path(temp_dir) / "runs").exists())
            self.assertFalse((Path(temp_dir) / "work").exists())

    def test_default_paths_are_runs_and_work(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                _result, _failures, run_dir = run_pipeline(
                    sources=[self._source()],
                    run_id="pf-02",
                    engine="claude",
                    model="fable",
                    effort="high",
                    runner=self._runner(),
                )
            finally:
                os.chdir(cwd)

            self.assertEqual(Path("runs") / "pf-02", run_dir)
            self.assertTrue((Path(temp_dir) / "runs" / "pf-02" / "output.csv").exists())
            self.assertTrue((Path(temp_dir) / "work" / "pf-02" / "fixture-doc").is_dir())


class RunPipelineIngestFaultToleranceTest(unittest.TestCase):
    def _source(self, source_id: str, title: str) -> SourceRecord:
        return SourceRecord(
            source_id=source_id,
            firm="Fixture Firm",
            date="",
            source=title,
            url=f"https://example.test/{source_id}",
            resolved_url=f"https://example.test/{source_id}",
            source_type="pdf",
            local_path=FIXTURE_PRINTED_PDF,
        )

    def test_one_ingest_failure_does_not_sink_multi_source_run(self) -> None:
        good = self._source("good-doc", "Good Outlook")
        bad = self._source("bad-doc", "Bad Outlook")

        def fake_snapshot(source: SourceRecord, work_dir: Path) -> IngestedSource:
            source_dir = Path(work_dir) / source.source_id
            source_dir.mkdir(parents=True, exist_ok=True)
            if source.source_id == "bad-doc":
                raise RuntimeError("playwright ERR_NAME_NOT_RESOLVED")
            snapshot_path = source_dir / "snapshot.txt"
            snapshot_path.write_text("We are overweight the segment.", encoding="utf-8")
            native_path = source_dir / "native.pdf"
            native_path.write_bytes(b"%PDF fixture")
            return IngestedSource(
                source=source,
                snapshot_text_path=snapshot_path,
                native_source_path=native_path,
                chunks=[Chunk(chunk_id="p1-1", locator="p.1", source_path=native_path)],
                page_count=1,
            )

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            if "native_source_path" in prompt:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "verdicts": [
                                {
                                    "index": 0,
                                    "supports_view": "pass",
                                    "forward_looking": "pass",
                                    "asset_match": "pass",
                                    "evidence_strength": "decisive",
                                    "note": "",
                                }
                            ]
                        }
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "summary": "one supported view",
                        "candidates": [
                            {
                                "source_id": "good-doc",
                                "chunk_id": "p1-1",
                                "sub_asset_raw": "EM equities",
                                "sub_asset_class": "Emerging Markets Equities",
                                "taxonomy_match": "exact",
                                "view": "O",
                                "call_language": "explicit_stance",
                                "evidence_kind": "prose",
                                "evidence_quote": "We are overweight the segment.",
                                "locator": "p.1",
                                "reasoning": "The source states an overweight.",
                                "conflict": False,
                            }
                        ],
                    }
                ),
                stderr="",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            out_root = Path(temp_dir) / "out"
            with patch("src.run.create_snapshot", side_effect=fake_snapshot):
                result, failures, run_dir = run_pipeline(
                    sources=[good, bad],
                    run_id="ingest-fault",
                    engine="claude",
                    model="fable",
                    effort="high",
                    runner=runner,
                    out_root=out_root,
                )
            with (run_dir / "output.csv").open(newline="", encoding="utf-8") as handle:
                output_rows = list(csv.DictReader(handle))
            with (run_dir / "failures.csv").open(newline="", encoding="utf-8") as handle:
                failure_rows = list(csv.DictReader(handle))
            client_text = (run_dir / "failures-client.csv").read_text(encoding="utf-8")
            manifest = (run_dir / "manifest.md").read_text(encoding="utf-8")

        self.assertEqual(1, len(result.output_rows))
        self.assertEqual(1, len(output_rows))
        self.assertEqual("bad-doc", failures[0].source_id)
        ingest_rows = [row for row in failure_rows if row["reason_code"] == "ingest_error"]
        self.assertEqual(1, len(ingest_rows))
        self.assertEqual("bad-doc", ingest_rows[0]["source_id"])
        self.assertIn("ERR_NAME_NOT_RESOLVED", ingest_rows[0]["message"])
        self.assertIn("Document could not be ingested", client_text)
        self.assertIn("bad-doc (pdf, 0 chunks): 0 candidates emitted [ingest-failed:", manifest)
        self.assertIn("good-doc (pdf, 1p / 1 chunks): 1 candidates emitted", manifest)


class QuoteVisualVerifierTest(unittest.TestCase):
    def test_malformed_llm_response_fails_closed_and_updates_stats(self) -> None:
        calls = 0

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(command, 0, stdout='{"judgment": "maybe"}', stderr="")

        stats = {
            "attempted": 0,
            "present_verbatim": 0,
            "present_paraphrase": 0,
            "absent": 0,
            "malformed": 0,
        }
        verifier = _make_quote_visual_verifier(
            engine="claude",
            model="sonnet",
            effort="medium",
            runner=runner,
            stats=stats,
        )
        judgment = verifier(
            _call_candidate(),
            SourceInfo(
                source_id="pimco-outlook",
                firm="PIMCO",
                date="",
                source="Layered Uncertainty",
                url="https://example.test/pimco",
            ),
            Path("/tmp/source.pdf"),
        )

        self.assertEqual("malformed", judgment)
        self.assertEqual(3, calls)  # initial call + two repair attempts
        self.assertEqual(1, stats["attempted"])
        self.assertEqual(1, stats["malformed"])


def _call_candidate() -> CandidateCall:
    return CandidateCall.from_mapping(json.loads(_candidate_json("p1-5"))["candidates"][0])


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

    def test_printed_html_chunk_reads_like_a_pdf_with_capture_note(self) -> None:
        source = SourceRecord(
            source_id="aberdeen-outlook",
            firm="Aberdeen Investments",
            date="6/1/2026",
            source="EM Outlook",
            url="https://example.test/outlook",
            resolved_url="https://example.test/outlook",
            source_type="html",
        )
        printed_path = Path("/tmp/printed.pdf")
        ingested = IngestedSource(
            source=source,
            snapshot_text_path=Path("/tmp/snapshot.txt"),
            native_source_path=printed_path,
            chunks=[Chunk(chunk_id="p1-3", locator="p.1-3", source_path=printed_path)],
            page_count=3,
            printed_pdf=True,
        )
        content = _chunk_content(ingested, ingested.chunks[0])
        self.assertIn("pages 1-3", content)
        self.assertIn("print-to-PDF capture", content)
        self.assertIn("https://example.test/outlook", content)
        self.assertIn("rendered pages", content)


if __name__ == "__main__":
    unittest.main()
