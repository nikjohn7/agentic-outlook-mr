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
    _pdf_chunk_text,
    _pdf_page_spans,
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


# The split 1 defect: the model echoed a Neuberger source id with a repeated
# fragment, so the evidence lookup keyed on a source that does not exist.
ECHOED_BAD_SOURCE_ID = "nb-aao-repricing-risk-repricing-risk"
MD_CHUNK_ID = "char:0-2000"
PDF_CHUNK_ID = "pdf:p2-2"


def _md_source(source_id: str, firm: str, title: str) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        firm=firm,
        date="",
        source=title,
        url="",
        resolved_url="",
        source_type="md",
    )


def _ingested_md(
    source: SourceRecord, work_dir: Path, markdown_text: str, pdf_text: str | None = None
) -> IngestedSource:
    """A Markdown source with one `char:` chunk and, when pdf_text is given, a
    paired PDF read as one `pdf:` chunk (page 2 of 3)."""
    source_dir = Path(work_dir) / source.source_id
    source_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = source_dir / "snapshot.txt"
    snapshot_path.write_text(markdown_text + " " + "Broader market context. " * 80, encoding="utf-8")
    native_path = source_dir / "native.md"
    native_path.write_text(markdown_text, encoding="utf-8")
    chunks = [Chunk(chunk_id=MD_CHUNK_ID, locator=MD_CHUNK_ID, source_path=snapshot_path)]
    if pdf_text is None:
        return IngestedSource(
            source=source,
            snapshot_text_path=snapshot_path,
            native_source_path=native_path,
            chunks=chunks,
        )
    pdf_path = source_dir / "native.pdf"
    pdf_path.write_bytes(b"%PDF fixture")
    pdf_snapshot_path = source_dir / "pdf-snapshot.txt"
    pdf_snapshot_path.write_text(pdf_text + " " + "Grid legend and notes. " * 80, encoding="utf-8")
    chunks.append(Chunk(chunk_id=PDF_CHUNK_ID, locator="p.2-2", source_path=pdf_path))
    return IngestedSource(
        source=source,
        snapshot_text_path=snapshot_path,
        native_source_path=native_path,
        chunks=chunks,
        pdf_native_path=pdf_path,
        pdf_snapshot_text_path=pdf_snapshot_path,
        pdf_page_count=3,
        flagged_pages=(2,),
    )


def _echoed_candidate(
    source_id: str, chunk_id: str, leaf: str, quote: str, locator: str, **fields
) -> dict:
    """One analyzer candidate whose source_id / chunk_id are whatever the model
    echoed; the semantic fields are what downstream must keep."""
    return {
        "source_id": source_id,
        "chunk_id": chunk_id,
        "sub_asset_raw": leaf.lower(),
        "sub_asset_class": leaf,
        "taxonomy_match": "exact",
        "view": "O",
        "call_language": "explicit_stance",
        "evidence_kind": "prose",
        "evidence_quote": quote,
        "locator": locator,
        "reasoning": f"The manager states a view on {leaf}.",
        "conflict": False,
        **fields,
    }


def _passing_verdicts(count: int) -> str:
    return json.dumps(
        {
            "verdicts": [
                {
                    "index": index,
                    "supports_view": "pass",
                    "forward_looking": "pass",
                    "asset_match": "pass",
                    "evidence_strength": "decisive",
                    "note": "",
                }
                for index in range(count)
            ]
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

    def test_echoed_ids_are_replaced_by_the_source_and_chunk_actually_read(self) -> None:
        source = _md_source("nb-aao", "Neuberger Berman", "Asset Allocation Outlook")
        # Markdown chunk: a repeated-fragment source id echoing the PDF chunk's
        # id, plus a correctly echoed call. PDF chunk: another (valid) source's
        # id with the Markdown chunk's id.
        markdown_calls = [
            _echoed_candidate(
                ECHOED_BAD_SOURCE_ID, PDF_CHUNK_ID, "US Equities",
                ["We favor US equities.", "Repricing risk is contained."], MD_CHUNK_ID,
                basis="forecast_delta", delta_value=4.5, delta_unit="pct", conflict=True,
            ),
            _echoed_candidate("nb-aao", MD_CHUNK_ID, "Japan Equities",
                              "Japan stays attractive.", MD_CHUNK_ID, view="N"),
        ]
        pdf_calls = [
            _echoed_candidate("other-doc", MD_CHUNK_ID, "Emerging Markets Equities",
                              "EM equities: overweight.", "p.2 - 'Positioning grid'",
                              view="U", basis="inferred", taxonomy_match="semantic"),
        ]
        prompts: list[str] = []

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            prompts.append(prompt)
            calls = pdf_calls if "pages 2-2 of the PDF" in prompt else markdown_calls
            out = json.dumps({"summary": f"chunk {len(prompts)}", "candidates": calls})
            return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            ingested = _ingested_md(source, work_dir, "markdown body", pdf_text="pdf page")
            candidates, failures = analyze_source(
                ingested,
                work_dir,
                taxonomy_block="- US Equities",
                brain_text="none",
                engine="claude",
                runner=runner,
            )
            memory = (work_dir / "nb-aao" / "memory.md").read_text(encoding="utf-8")

        self.assertEqual([], failures)
        self.assertEqual(
            [("nb-aao", MD_CHUNK_ID), ("nb-aao", MD_CHUNK_ID), ("nb-aao", PDF_CHUNK_ID)],
            [(c.source_id, c.chunk_id) for c in candidates],
        )
        # Every other field is exactly what the model returned.
        for candidate, raw in zip(candidates, markdown_calls + pdf_calls):
            expected = CandidateCall.from_mapping(
                {**raw, "source_id": "nb-aao", "chunk_id": candidate.chunk_id}
            )
            self.assertEqual(expected, candidate)
        self.assertEqual(
            ("We favor US equities.", "Repricing risk is contained."),
            candidates[0].evidence_spans,
        )
        self.assertEqual(("forecast_delta", 4.5, "pct", True),
                         (candidates[0].basis, candidates[0].delta_value,
                          candidates[0].delta_unit, candidates[0].conflict))
        self.assertEqual(("U", "inferred", "p.2 - 'Positioning grid'"),
                         (candidates[2].view, candidates[2].basis, candidates[2].locator))
        # Rolling memory (and the PDF chunk's prompt built from it) names the
        # source and chunks read, never an echoed id.
        self.assertIn("(nb-aao)", memory)
        self.assertIn(f"## Chunk {MD_CHUNK_ID}", memory)
        self.assertIn(f"## Chunk {PDF_CHUNK_ID}", memory)
        self.assertIn("US Equities=O[char:0-2000]", memory)
        self.assertIn("Emerging Markets Equities=U[p.2 - 'Positioning grid']", memory)
        for text in (memory, prompts[1]):
            self.assertNotIn(ECHOED_BAD_SOURCE_ID, text)
            self.assertNotIn("other-doc", text)


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

    def test_codex_defaults_model_and_accepts_the_allowlist(self) -> None:
        self.assertEqual(("gpt-5.5", "high"), resolve_engine_settings("codex", None, "high"))
        self.assertEqual(("gpt-5.5", "low"), resolve_engine_settings("codex", "gpt-5.5", "low"))
        self.assertEqual(
            ("gpt-5.6-sol", "high"), resolve_engine_settings("codex", "gpt-5.6-sol", "high")
        )
        with self.assertRaises(ValueError):
            resolve_engine_settings("codex", "o3", "high")

    def test_effort_is_required_and_validated_per_engine(self) -> None:
        with self.assertRaises(ValueError):
            resolve_engine_settings("claude", "fable", None)
        with self.assertRaises(ValueError):
            resolve_engine_settings("claude", "fable", "minimal")
        with self.assertRaises(ValueError):
            resolve_engine_settings("codex", None, "max")

    def test_codex_rejects_minimal_for_every_model(self) -> None:
        # The codex floor is `low`; minimal is never accepted, not even on gpt-5.5.
        for model in (None, "gpt-5.5", "gpt-5.6-luna"):
            with self.assertRaises(ValueError):
                resolve_engine_settings("codex", model, "minimal")
        self.assertEqual(("gpt-5.5", "low"), resolve_engine_settings("codex", None, "low"))
        self.assertEqual(
            ("gpt-5.6-luna", "xhigh"), resolve_engine_settings("codex", "gpt-5.6-luna", "xhigh")
        )

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

    def _verdict_runner(self, prompts: list[str]):
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

        return runner

    def test_checker_context_off_attaches_no_context(self) -> None:
        prompts: list[str] = []
        _check_candidates(
            _pdf_source(),
            [_call_candidate()],
            engine="codex",
            model=None,
            effort="high",
            runner=self._verdict_runner(prompts),
            snapshot_text="Before.\n\nWe are overweight the segment strongly.\n\nAfter.",
        )
        self.assertNotIn('"evidence_context"', prompts[0])
        self.assertNotIn('"context_unreliable"', prompts[0])

    def test_checker_context_clean_attaches_text_window(self) -> None:
        prompts: list[str] = []
        _check_candidates(
            _pdf_source(),
            [_call_candidate()],
            engine="codex",
            model=None,
            effort="high",
            runner=self._verdict_runner(prompts),
            checker_context=True,
            snapshot_text=(
                "Macro framing paragraph here.\n\n"
                "We are overweight the segment. Fundamentals stay supportive.\n\n"
                "A closing paragraph on rates."
            ),
        )
        self.assertIn('"evidence_context"', prompts[0])
        self.assertIn("Macro framing paragraph", prompts[0])
        self.assertNotIn('"context_unreliable"', prompts[0])

    def test_checker_context_scrambled_page_attaches_visual_flag(self) -> None:
        prompts: list[str] = []
        _check_candidates(
            _pdf_source(),
            [_call_candidate()],
            engine="codex",
            model=None,
            effort="high",
            runner=self._verdict_runner(prompts),
            native_source_path=Path("/tmp/doc.pdf"),
            checker_context=True,
            snapshot_text="Before.\n\nWe are overweight the segment strongly.\n\nAfter.",
            scrambled_pages=frozenset({3}),
        )
        self.assertIn('"context_unreliable"', prompts[0])
        # No trustworthy text is shipped for a degraded page.
        self.assertNotIn('"evidence_context"', prompts[0])

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
            with (run_dir / "output.csv").open(newline="", encoding="utf-8-sig") as handle:
                output_rows = list(csv.DictReader(handle))
            with (run_dir / "failures.csv").open(newline="", encoding="utf-8-sig") as handle:
                failure_rows = list(csv.DictReader(handle))
            client_text = (run_dir / "failures-client.csv").read_text(encoding="utf-8-sig")
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


class RunPipelineEchoedIdentityTest(unittest.TestCase):
    """Evidence lookup and validation routing follow the source and chunk the
    model read, whatever ids it echoed (split 1: a Neuberger call echoing a
    repeated-fragment source id failed as `snapshot text is empty`)."""

    def test_echoed_ids_cannot_redirect_evidence_lookup_or_validation(self) -> None:
        neuberger = _md_source("nb-aao", "Neuberger Berman", "Asset Allocation Outlook")
        other = _md_source("other-doc", "Other Firm", "Other Outlook")
        # Each quote exists in exactly one snapshot: Neuberger's Markdown,
        # Neuberger's paired PDF, or the other source's Markdown. Both Markdown
        # sources have a chunk with the same id, so an echoed source id alone
        # would land on the other source's text.
        texts = {
            "nb-aao": (
                "We favor US equities. Japan stays attractive.",
                "Positioning grid: EM equities overweight.",
            ),
            "other-doc": ("Gold remains a core hedge.", None),
        }
        calls = {
            ("nb-aao", MD_CHUNK_ID): [
                # The split 1 defect: a repeated-fragment source id, and the
                # PDF chunk's id echoed for a Markdown read.
                _echoed_candidate(ECHOED_BAD_SOURCE_ID, PDF_CHUNK_ID, "US Equities",
                                  "We favor US equities.", MD_CHUNK_ID),
                # Another valid source's id.
                _echoed_candidate("other-doc", MD_CHUNK_ID, "Japan Equities",
                                  "Japan stays attractive.", MD_CHUNK_ID, view="N"),
            ],
            ("nb-aao", PDF_CHUNK_ID): [
                # Another source's id and the Markdown chunk's id for a PDF read.
                _echoed_candidate("other-doc", MD_CHUNK_ID, "Emerging Markets Equities",
                                  "Positioning grid: EM equities overweight.",
                                  "p.2 - 'Positioning grid'"),
            ],
            ("other-doc", MD_CHUNK_ID): [
                # Correctly echoed.
                _echoed_candidate("other-doc", MD_CHUNK_ID, "Gold/Precious",
                                  "Gold remains a core hedge.", MD_CHUNK_ID),
            ],
        }

        def fake_snapshot(source: SourceRecord, work_dir: Path) -> IngestedSource:
            markdown_text, pdf_text = texts[source.source_id]
            return _ingested_md(source, work_dir, markdown_text, pdf_text=pdf_text)

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            if "native_source_path" in prompt:  # the checker
                return subprocess.CompletedProcess(command, 0, stdout=_passing_verdicts(3), stderr="")
            source_id = "nb-aao" if '"source_id": "nb-aao"' in prompt else "other-doc"
            chunk_id = PDF_CHUNK_ID if "pages 2-2 of the PDF" in prompt else MD_CHUNK_ID
            out = json.dumps({"summary": "views", "candidates": calls[(source_id, chunk_id)]})
            return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.run.create_snapshot", side_effect=fake_snapshot):
                result, chunk_failures, run_dir = run_pipeline(
                    sources=[neuberger, other],
                    run_id="echoed-ids",
                    engine="claude",
                    model="fable",
                    effort="high",
                    evidence_gate=False,
                    runner=runner,
                    out_root=Path(temp_dir) / "out",
                )
            with (run_dir / "output.csv").open(newline="", encoding="utf-8-sig") as handle:
                written = {row["Sub-Asset Class"]: row for row in csv.DictReader(handle)}

        self.assertEqual([], chunk_failures)
        self.assertEqual([], [(f.reason_code, f.message) for f in result.failures])
        rows = {row["Sub-Asset Class"]: row for row in result.output_rows}
        self.assertEqual(
            {
                "US Equities": ("Neuberger Berman", "Asset Allocation Outlook", "O"),
                "Japan Equities": ("Neuberger Berman", "Asset Allocation Outlook", "N"),
                "Emerging Markets Equities": ("Neuberger Berman", "Asset Allocation Outlook", "O"),
                "Gold/Precious": ("Other Firm", "Other Outlook", "O"),
            },
            {leaf: (row["Firm"], row["Source"], row["View"]) for leaf, row in rows.items()},
        )
        # Validation routing follows the chunk read: the Markdown calls
        # validated on the Markdown, the PDF call on the PDF (and is marked).
        self.assertEqual(
            {
                "US Equities": "markdown",
                "Japan Equities": "markdown",
                "Emerging Markets Equities": "pdf",
                "Gold/Precious": "markdown",
            },
            {leaf: row["evidence_origin"] for leaf, row in rows.items()},
        )
        self.assertEqual(
            "p.2 - 'Positioning grid' (read from PDF)",
            rows["Emerging Markets Equities"]["Locator"],
        )
        self.assertIn("We favor US equities.", rows["US Equities"]["Verbatim Evidence"])
        self.assertEqual(set(rows), set(written))
        self.assertEqual("Neuberger Berman", written["US Equities"]["Firm"])


class PairedPdfChunkTextTest(unittest.TestCase):
    """A `pdf:` chunk validates against the text of its own pages only - the
    pages the model was given - not the whole PDF."""

    PAGES = [
        "Page one: macro backdrop.",
        "Positioning grid: EM equities overweight.",
        "Japan equities are our top underweight.",
    ]

    def test_page_spans_recover_each_page_of_the_joined_snapshot(self) -> None:
        pdf_text = "\n\n".join(self.PAGES)
        spans = _pdf_page_spans(pdf_text, self.PAGES, frozenset())

        self.assertEqual(self.PAGES, [pdf_text[a:b] for a, b in (spans[1], spans[2], spans[3])])
        self.assertEqual(self.PAGES[1], _pdf_chunk_text(pdf_text, spans, (2,)))
        self.assertEqual("\n\n".join(self.PAGES[1:]), _pdf_chunk_text(pdf_text, spans, (2, 3)))

    def test_ocr_page_text_is_bounded_by_its_neighbours(self) -> None:
        # Page 2 was OCR'd: the snapshot holds OCR text, not the text layer.
        pdf_text = "\n\n".join([self.PAGES[0], "OCR: EM equities overweight.", self.PAGES[2]])
        spans = _pdf_page_spans(pdf_text, [self.PAGES[0], "", self.PAGES[2]], frozenset({2}))

        self.assertEqual("OCR: EM equities overweight.", _pdf_chunk_text(pdf_text, spans, (2,)))

    def test_misaligned_snapshot_falls_back_to_the_whole_text(self) -> None:
        pdf_text = "\n\n".join(self.PAGES)
        self.assertIsNone(_pdf_page_spans(pdf_text, ["something else", *self.PAGES[1:]], frozenset()))
        self.assertIsNone(_pdf_page_spans(pdf_text, None, frozenset()))
        self.assertEqual(pdf_text, _pdf_chunk_text(pdf_text, None, (2,)))

    def test_quote_only_on_an_unsupplied_page_fails_validation(self) -> None:
        source = _md_source("nb-aao", "Neuberger Berman", "Asset Allocation Outlook")
        pdf_text = "\n\n".join(self.PAGES)
        filler = " " + "Grid legend and notes. " * 80  # _ingested_md appends this
        page_texts = [self.PAGES[0], self.PAGES[1], self.PAGES[2] + filler]
        calls = {
            MD_CHUNK_ID: [],
            PDF_CHUNK_ID: [
                _echoed_candidate("nb-aao", PDF_CHUNK_ID, "Emerging Markets Equities",
                                  self.PAGES[1], "p.2 - 'Positioning grid'"),
                # Page 3's sentence, cited as page 2 of the chunk p2-2.
                _echoed_candidate("nb-aao", PDF_CHUNK_ID, "Japan Equities",
                                  self.PAGES[2], "p.2", view="U"),
            ],
        }
        verified: list[str] = []

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            if "Verify quote visually" in prompt:
                verified.append(prompt)
                return subprocess.CompletedProcess(
                    command, 0, stdout='{"judgment": "absent"}', stderr=""
                )
            if "native_source_path" in prompt:  # the checker
                return subprocess.CompletedProcess(command, 0, stdout=_passing_verdicts(2), stderr="")
            chunk_id = PDF_CHUNK_ID if "pages 2-2 of the PDF" in prompt else MD_CHUNK_ID
            out = json.dumps({"summary": "views", "candidates": calls[chunk_id]})
            return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "src.run.create_snapshot",
                side_effect=lambda src, work: _ingested_md(src, work, "markdown body", pdf_text),
            ), patch("src.run._pdf_page_texts", return_value=page_texts):
                result, chunk_failures, _ = run_pipeline(
                    sources=[source],
                    run_id="pdf-chunk-pages",
                    engine="claude",
                    model="fable",
                    effort="high",
                    evidence_gate=False,
                    runner=runner,
                    out_root=Path(temp_dir) / "out",
                )

        self.assertEqual([], chunk_failures)
        self.assertEqual(
            ["Emerging Markets Equities"], [row["Sub-Asset Class"] for row in result.output_rows]
        )
        self.assertEqual(
            [("Japan Equities", "quote_not_found_visual")],
            [(f.sub_asset_class, f.reason_code) for f in result.failures],
        )
        self.assertEqual(1, len(verified))  # the page image is the only rescue

    def test_pdf_pages_are_not_reread_without_a_pdf_chunk(self) -> None:
        # A paired PDF with no flagged pages has no `pdf:` chunk to slice for.
        source = _md_source("nb-aao", "Neuberger Berman", "Asset Allocation Outlook")

        def ingested(src, work):
            item = _ingested_md(src, work, "markdown body", "pdf body")
            item.chunks[:] = [c for c in item.chunks if c.chunk_id == MD_CHUNK_ID]
            return item

        def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
            out = json.dumps({"summary": "no views", "candidates": []})
            return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.run.create_snapshot", side_effect=ingested), patch(
                "src.run._pdf_page_texts"
            ) as page_texts:
                run_pipeline(
                    sources=[source],
                    run_id="no-pdf-chunk",
                    engine="claude",
                    model="fable",
                    effort="high",
                    evidence_gate=False,
                    runner=runner,
                    out_root=Path(temp_dir) / "out",
                )
        page_texts.assert_not_called()


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

    def test_markdown_chunk_warns_that_graphics_may_be_lost(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = Path(temp_dir) / "snapshot.txt"
            snapshot.write_text("We are overweight EM equities.", encoding="utf-8")
            source = SourceRecord(
                source_id="nb-aao", firm="Neuberger Berman", date="", source="AAO 4Q 2026",
                url="", resolved_url="", source_type="md",
            )
            ingested = IngestedSource(
                source=source,
                snapshot_text_path=snapshot,
                native_source_path=Path(temp_dir) / "AAO_4Q2026.md",
                chunks=[Chunk(chunk_id="char:0-30", locator="char:0-30", source_path=snapshot)],
            )
            content = _chunk_content(ingested, ingested.chunks[0])
        self.assertIn("from AAO_4Q2026.md", content)  # no URL: name the file
        self.assertIn("Markdown conversion", content)
        self.assertIn("Never infer a view from a visual", content)
        self.assertIn("We are overweight EM equities.", content)


if __name__ == "__main__":
    unittest.main()
