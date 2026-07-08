from __future__ import annotations

import csv
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.assemble import (
    ALL_REASON_CODES,
    CLIENT_FAILURE_COLUMNS,
    CLIENT_FAILURE_LABELS,
    FAILURE_COLUMNS,
    OUTPUT_COLUMNS,
    FailureRecord,
    assemble_candidates,
    client_failure_label,
    write_run_outputs,
)
from src.schemas import CandidateCall, CheckVerdict, SourceInfo
from src.taxonomy import Taxonomy


class AssembleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Aberdeen Investments",
                date="3/10/2026",
                source="Emerging Markets Q2 2026 Outlook: Shifting Sands",
                url="https://example.test/source",
            )
        }

    def test_output_columns_are_target_shape_plus_review_fields(self) -> None:
        self.assertEqual(
            (
                "Firm",
                "Date",
                "Source",
                "URL",
                "Sub-Asset Class",
                "Asset Class Category",
                "Canva Groupings",
                "Asset Class",
                "View",
                "Full Commentary",
                "confidence",
                "band",
                "review_flag",
                "basis",
                "checker_strength",
                "call_language",
                "quote_match",
            ),
            OUTPUT_COLUMNS,
        )

    def test_assemble_writes_output_failure_and_manifest(self) -> None:
        candidates = [_candidate(), _candidate(sub_asset_class="Not A Leaf")]
        snapshots = {
            ("source-1", "p1-5"): "EM equities are favored in the outlook. " + "Context. " * 30,
        }

        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=snapshots,
            page_counts={"source-1": 1},
        )

        self.assertEqual(1, len(result.output_rows))
        self.assertEqual(1, len(result.failures))
        with tempfile.TemporaryDirectory() as temp_dir:
            write_run_outputs(result, temp_dir)
            output_rows = _read_csv(Path(temp_dir) / "output.csv")
            failure_rows = _read_csv(Path(temp_dir) / "failures.csv")
            manifest = (Path(temp_dir) / "manifest.md").read_text(encoding="utf-8")

        self.assertEqual("96", output_rows[0]["confidence"])
        self.assertEqual("High", output_rows[0]["band"])
        self.assertEqual("none", output_rows[0]["review_flag"])
        self.assertEqual("", output_rows[0]["checker_strength"])
        self.assertEqual("exact", output_rows[0]["quote_match"])
        self.assertEqual("taxonomy_no_match", failure_rows[0]["reason_code"])
        self.assertIn("count check: pass", manifest)
        self.assertIn("## Quote match tier (kept rows)", manifest)

    def test_failure_columns_place_evidence_quote_after_evidence_kind(self) -> None:
        index = FAILURE_COLUMNS.index("evidence_kind")
        self.assertEqual("evidence_quote", FAILURE_COLUMNS[index + 1])

    def test_failures_csv_records_candidate_evidence_quote(self) -> None:
        # A candidate-level failure keeps the quote the model submitted (needed
        # to diagnose quote_not_found); a chunk-level failure leaves it empty.
        candidates = [_candidate(sub_asset_class="Not A Leaf", evidence_quote="a submitted quote")]
        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={},
        )
        chunk_failure = FailureRecord.from_chunk(
            "json_parse_error", "bad json", "source-1", "p6-10"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            write_run_outputs(result, temp_dir, chunk_failures=[chunk_failure])
            failure_rows = _read_csv(Path(temp_dir) / "failures.csv")

        self.assertIn("evidence_quote", failure_rows[0])
        candidate_row = next(r for r in failure_rows if r["reason_code"] == "taxonomy_no_match")
        chunk_row = next(r for r in failure_rows if r["reason_code"] == "json_parse_error")
        self.assertEqual("a submitted quote", candidate_row["evidence_quote"])
        self.assertEqual("", chunk_row["evidence_quote"])

    def test_conflicting_duplicate_views_route_to_failures(self) -> None:
        candidates = [_candidate(view="O"), _candidate(view="U")]
        snapshots = {
            ("source-1", "p1-5"): "EM equities are favored in the outlook.",
        }

        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=snapshots,
        )

        self.assertEqual([], result.output_rows)
        self.assertEqual(2, len(result.failures))
        self.assertEqual({"unresolved_conflict"}, {failure.reason_code for failure in result.failures})


class ClientFailuresFileTest(unittest.TestCase):
    """failures-client.csv: same rows as failures.csv, plain labels, every
    reason code mapped, internal file unchanged."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Aberdeen Investments",
                date="",
                source="Emerging Markets Q2 2026 Outlook: Shifting Sands",
                url="https://example.test/source",
            )
        }

    def test_every_reason_code_is_mapped(self) -> None:
        # A new code added anywhere in the codebase must be registered in
        # ALL_REASON_CODES and given a client label, or this fails.
        unmapped = ALL_REASON_CODES - set(CLIENT_FAILURE_LABELS)
        self.assertEqual(set(), unmapped, f"reason codes with no client label: {unmapped}")
        # No stray labels either — the mapping and the registry match exactly.
        self.assertEqual(set(), set(CLIENT_FAILURE_LABELS) - ALL_REASON_CODES)

    def test_source_from_candidate_reason_literals_are_registered(self) -> None:
        # Guard against a new literal reason code slipping into failures without
        # a registry entry: scan assemble.py/run.py for from_candidate/from_chunk
        # string-literal first args and assert each is in ALL_REASON_CODES.
        import re

        root = Path(__file__).resolve().parents[1] / "src"
        pattern = re.compile(
            r'FailureRecord\.from_(?:candidate|chunk)\(\s*"([a-z_]+)"'
        )
        found: set[str] = set()
        for name in ("assemble.py", "run.py"):
            found |= set(pattern.findall((root / name).read_text(encoding="utf-8")))
        self.assertTrue(found, "scan found no literal reason codes — pattern drifted")
        self.assertEqual(set(), found - ALL_REASON_CODES, f"unregistered: {found - ALL_REASON_CODES}")

    def test_unmapped_code_falls_back_gracefully(self) -> None:
        what, explanation = client_failure_label("some_future_code")
        self.assertEqual("some_future_code", what)
        self.assertTrue(explanation)  # a real sentence, never a crash

    def test_client_file_written_alongside_and_readable(self) -> None:
        candidates = [_candidate(sub_asset_class="Not A Leaf", view="O")]
        result = assemble_candidates(
            candidates, sources=self.sources, taxonomy=self.taxonomy, snapshots={}
        )
        chunk_failure = FailureRecord.from_chunk(
            "json_parse_error", "bad json", "source-1", "p6-10"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            write_run_outputs(
                result, temp_dir, sources=self.sources, chunk_failures=[chunk_failure]
            )
            internal = _read_csv(Path(temp_dir) / "failures.csv")
            client = _read_csv(Path(temp_dir) / "failures-client.csv")

        # Same rows, same order.
        self.assertEqual(len(internal), len(client))
        self.assertEqual(list(CLIENT_FAILURE_COLUMNS), list(client[0].keys()))
        tax_row = client[0]
        self.assertEqual("Aberdeen Investments", tax_row["Firm"])
        self.assertEqual(
            "Emerging Markets Q2 2026 Outlook: Shifting Sands", tax_row["Source"]
        )
        self.assertEqual("O", tax_row["View (proposed)"])
        self.assertEqual("Skipped — asset not on the list", tax_row["What happened"])
        self.assertTrue(tax_row["Explanation"])
        # No internal jargon leaks into the reader columns.
        self.assertNotIn("taxonomy_no_match", tax_row["What happened"])
        self.assertNotIn("taxonomy_no_match", tax_row["Explanation"])

    def test_internal_failures_file_is_unchanged_by_client_file(self) -> None:
        # Writing with and without sources yields a byte-identical failures.csv.
        candidates = [_candidate(sub_asset_class="Not A Leaf")]
        result = assemble_candidates(
            candidates, sources=self.sources, taxonomy=self.taxonomy, snapshots={}
        )
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            write_run_outputs(result, a)
            write_run_outputs(result, b, sources=self.sources)
            self.assertEqual(
                (Path(a) / "failures.csv").read_bytes(),
                (Path(b) / "failures.csv").read_bytes(),
            )


class VisualQuoteFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Janus Henderson Investors",
                date="",
                source="Market GPS Investment Outlook Mid-Year 2026",
                url="https://example.test/jh.pdf",
            )
        }

    def test_present_verbatim_keeps_with_visual_cap_and_review(self) -> None:
        calls: list[str] = []

        def verifier(candidate: CandidateCall, source: SourceInfo, native_path: Path | None) -> str:
            calls.append(candidate.sub_asset_class)
            return "present_verbatim"

        result = assemble_candidates(
            [_candidate(evidence_quote="quote only visible on rendered page")],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={("source-1", "p1-5"): _long_snapshot("snapshot omits it")},
            quote_visual_verifier=verifier,
            native_source_paths={"source-1": Path("/tmp/source.pdf")},
        )

        self.assertEqual(["Emerging Markets Equities"], calls)
        self.assertEqual(1, len(result.output_rows))
        row = result.output_rows[0]
        self.assertEqual("74", row["confidence"])
        self.assertEqual("Medium", row["band"])
        self.assertEqual("review", row["review_flag"])
        self.assertEqual("visual", row["quote_match"])
        self.assertEqual([], result.failures)

    def test_absent_visual_verification_drops_with_distinct_reason(self) -> None:
        result = assemble_candidates(
            [_candidate(evidence_quote="quote only visible on rendered page")],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={("source-1", "p1-5"): _long_snapshot("snapshot omits it")},
            quote_visual_verifier=lambda candidate, source, native_path: "absent",
            native_source_paths={"source-1": Path("/tmp/source.pdf")},
        )

        self.assertEqual([], result.output_rows)
        self.assertEqual(["quote_not_found_visual"], [f.reason_code for f in result.failures])
        self.assertIn("did not find", result.failures[0].message)

    def test_malformed_visual_verification_fails_closed(self) -> None:
        result = assemble_candidates(
            [_candidate(evidence_quote="quote only visible on rendered page")],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={("source-1", "p1-5"): _long_snapshot("snapshot omits it")},
            quote_visual_verifier=lambda candidate, source, native_path: "not_a_judgment",
            native_source_paths={"source-1": Path("/tmp/source.pdf")},
        )

        self.assertEqual([], result.output_rows)
        self.assertEqual(["quote_not_found_visual"], [f.reason_code for f in result.failures])
        self.assertIn("malformed", result.failures[0].message)

    def test_visual_verification_not_invoked_when_deterministic_gate_passes(self) -> None:
        def forbidden(candidate: CandidateCall, source: SourceInfo, native_path: Path | None) -> str:
            raise AssertionError("visual verifier must not run")

        result = assemble_candidates(
            [_candidate(evidence_quote="EM equities are favored in the outlook.")],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={
                ("source-1", "p1-5"): _long_snapshot(
                    "EM equities are favored in the outlook."
                )
            },
            quote_visual_verifier=forbidden,
            native_source_paths={"source-1": Path("/tmp/source.pdf")},
        )

        self.assertEqual(1, len(result.output_rows))
        self.assertEqual("exact", result.output_rows[0]["quote_match"])


class CheckerAndArbiterAssemblyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Aberdeen Investments",
                date="3/10/2026",
                source="Emerging Markets Q2 2026 Outlook: Shifting Sands",
                url="https://example.test/source",
            )
        }
        cls.snapshots = {
            ("source-1", "p1-5"): "EM equities are favored in the outlook. " + "Context. " * 30,
        }

    def test_checker_fail_verdict_routes_to_failures_with_note(self) -> None:
        result = assemble_candidates(
            [_candidate()],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts={"source-1": 1},
            verdicts={0: _verdict(supports_view="fail", note="quote is a market recap")},
        )

        self.assertEqual([], result.output_rows)
        self.assertEqual("checker_sign_mismatch", result.failures[0].reason_code)
        self.assertEqual("quote is a market recap", result.failures[0].message)

    def test_unclear_verdict_caps_row_and_annotates_commentary(self) -> None:
        result = assemble_candidates(
            [_candidate()],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts={"source-1": 1},
            verdicts={0: _verdict(asset_match="unclear", note="broader bucket")},
        )

        row = result.output_rows[0]
        self.assertEqual("74", row["confidence"])
        self.assertEqual("review", row["review_flag"])
        self.assertEqual("decisive", row["checker_strength"])
        self.assertIn("Checker: unconfirmed (broader bucket).", row["Full Commentary"])

    def test_checker_strength_is_exposed_in_output_and_manifest(self) -> None:
        result = assemble_candidates(
            [_candidate()],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts={"source-1": 1},
            verdicts={0: _verdict(evidence_strength="adequate")},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            write_run_outputs(result, temp_dir)
            output_rows = _read_csv(Path(temp_dir) / "output.csv")
            manifest = (Path(temp_dir) / "manifest.md").read_text(encoding="utf-8")

        self.assertEqual("adequate", output_rows[0]["checker_strength"])
        self.assertIn("Checker strength (kept rows)", manifest)
        self.assertIn("- adequate: 1", manifest)

    def test_visual_unverified_route_adds_output_note(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Japan equities dial sits in the Neutral box",
            locator="p.4 - Regional equity dials",
            call_language="explicit_dial",
            view="N",
            sub_asset_class="Japan Equities",
        )
        result = assemble_candidates(
            [candidate],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={("source-1", "p1-5"): "snapshot text omits rendered dial labels"},
            page_counts={"source-1": 5},
            visual_pages={"source-1": {4}},
            verdicts={0: _verdict()},
        )

        row = result.output_rows[0]
        self.assertEqual("High", row["band"])
        self.assertEqual("none", row["review_flag"])
        self.assertIn("checker verified the cited page image", row["Full Commentary"])

    def test_visual_unverified_checker_fail_message_is_distinct_from_token_miss(self) -> None:
        candidate = _candidate(
            evidence_kind="visual",
            evidence_quote="Japan equities dial sits in the Neutral box",
            locator="p.4 - Regional equity dials",
            call_language="explicit_dial",
            view="N",
            sub_asset_class="Japan Equities",
        )
        result = assemble_candidates(
            [candidate],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={("source-1", "p1-5"): "snapshot text omits rendered dial labels"},
            page_counts={"source-1": 5},
            visual_pages={"source-1": {4}},
            verdicts={
                0: _verdict(supports_view="fail", note="dial was not present on the page")
            },
        )

        self.assertEqual([], result.output_rows)
        self.assertEqual("checker_sign_mismatch", result.failures[0].reason_code)
        self.assertIn("checker visual review failed", result.failures[0].message)
        self.assertNotEqual(
            "table/visual evidence tokens were not found in snapshot text",
            result.failures[0].message,
        )

    def test_inferred_candidate_rejected_by_checker_is_auditable(self) -> None:
        # An inferred call the checker rejects lands in failures.csv with its
        # basis, the inference reasoning (what was inferred), and the checker's
        # note (why it failed) — so the rejected inference is never glazed over.
        candidate = _candidate(
            basis="inferred",
            view="U",
            call_language="implied",
            reasoning="Sustained capital flight from the region implies EM equities underweight.",
        )
        result = assemble_candidates(
            [candidate],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts={"source-1": 1},
            verdicts={
                0: _verdict(supports_view="fail", note="the inference is a multi-step leap")
            },
        )

        self.assertEqual([], result.output_rows)
        failure = result.failures[0]
        self.assertEqual("checker_sign_mismatch", failure.reason_code)
        self.assertEqual("inferred", failure.basis)
        self.assertIn("Sustained capital flight", failure.reasoning)
        self.assertEqual("the inference is a multi-step leap", failure.message)

    def test_arbiter_resolves_conflict_and_records_loser(self) -> None:
        candidates = [_candidate(view="O"), _candidate(view="U")]

        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts={"source-1": 1},
            verdicts={0: _verdict(), 1: _verdict()},
            arbiter=lambda group: (0, "published dial wins"),
        )

        self.assertEqual(1, len(result.output_rows))
        row = result.output_rows[0]
        self.assertEqual("O", row["View"])
        self.assertEqual("review", row["review_flag"])
        self.assertIn("Arbiter: published dial wins", row["Full Commentary"])
        self.assertEqual(["arbitrated_out"], [f.reason_code for f in result.failures])
        self.assertEqual("published dial wins", result.failures[0].message)

    def test_arbiter_null_falls_back_to_unresolved_conflict(self) -> None:
        candidates = [_candidate(view="O"), _candidate(view="U")]

        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts={"source-1": 1},
            verdicts={0: _verdict(), 1: _verdict()},
            arbiter=lambda group: (None, "two explicit horizons"),
        )

        self.assertEqual([], result.output_rows)
        self.assertEqual(
            {"unresolved_conflict"}, {failure.reason_code for failure in result.failures}
        )
        self.assertIn("two explicit horizons", result.failures[0].message)


class GroupedAssemblyTest(unittest.TestCase):
    GROUP_MAP = {"source-1": "group-1", "source-2": "group-1"}

    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Schroders",
                date="3/10/2026",
                source="Quarterly Markets Review Q1",
                url="https://example.test/review",
            ),
            "source-2": SourceInfo(
                source_id="source-2",
                firm="Schroders",
                date="4/2/2026",
                source="Global Investment Outlook Q2",
                url="https://example.test/outlook",
            ),
        }
        snapshot = "EM equities are favored in the outlook. " + "Context. " * 30
        cls.snapshots = {
            ("source-1", "p1-5"): snapshot,
            ("source-2", "p1-5"): snapshot,
        }
        cls.page_counts = {"source-1": 1, "source-2": 1}

    def test_same_view_across_grouped_docs_keeps_one_corroborated_row(self) -> None:
        candidates = [_candidate(), _candidate(source_id="source-2", locator="p.7")]

        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts=self.page_counts,
            group_map=self.GROUP_MAP,
        )

        self.assertEqual(1, len(result.output_rows))
        row = result.output_rows[0]
        self.assertEqual(
            "Quarterly Markets Review Q1 | Global Investment Outlook Q2", row["Source"]
        )
        self.assertEqual("3/10/2026 | 4/2/2026", row["Date"])
        self.assertIn("Locator: p.3 (Quarterly Markets Review Q1).", row["Full Commentary"])
        self.assertIn(
            "Corroborated by companion source: Global Investment Outlook Q2 (p.7).",
            row["Full Commentary"],
        )
        self.assertEqual(["duplicate_same_view"], [f.reason_code for f in result.failures])
        # Reconciliation stays exact: every candidate is either kept or recorded.
        self.assertEqual(
            result.candidate_count, len(result.output_rows) + len(result.failures)
        )

    def test_grouped_date_pipe_join_skips_blank_dates(self) -> None:
        # Document-extracted dates are often blank; a grouped row joins only the
        # non-blank ones (no stray " | "), while titles/URLs still join every member.
        sources = {
            "source-1": replace(self.sources["source-1"], date=""),
            "source-2": self.sources["source-2"],
        }
        candidates = [_candidate(), _candidate(source_id="source-2", locator="p.7")]

        result = assemble_candidates(
            candidates,
            sources=sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts=self.page_counts,
            group_map=self.GROUP_MAP,
        )

        row = result.output_rows[0]
        self.assertEqual("4/2/2026", row["Date"])
        self.assertEqual(
            "Quarterly Markets Review Q1 | Global Investment Outlook Q2", row["Source"]
        )

    def test_grouped_date_all_blank_yields_blank_field(self) -> None:
        sources = {
            "source-1": replace(self.sources["source-1"], date=""),
            "source-2": replace(self.sources["source-2"], date=""),
        }
        candidates = [_candidate(), _candidate(source_id="source-2", locator="p.7")]

        result = assemble_candidates(
            candidates,
            sources=sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts=self.page_counts,
            group_map=self.GROUP_MAP,
        )

        self.assertEqual("", result.output_rows[0]["Date"])

    def test_conflicting_views_across_grouped_docs_route_to_the_arbiter(self) -> None:
        candidates = [
            _candidate(view="O"),
            _candidate(source_id="source-2", view="U", locator="p.7"),
        ]

        result = assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts=self.page_counts,
            group_map=self.GROUP_MAP,
            arbiter=lambda group: (1, "outlook document beats review document"),
        )

        self.assertEqual(1, len(result.output_rows))
        row = result.output_rows[0]
        self.assertEqual("U", row["View"])
        self.assertIn(" | ", row["Source"])
        self.assertEqual("review", row["review_flag"])
        self.assertIn("Arbiter: outlook document beats review document", row["Full Commentary"])
        self.assertEqual(["arbitrated_out"], [f.reason_code for f in result.failures])

    def test_ungrouped_sources_are_untouched_by_group_map(self) -> None:
        result = assemble_candidates(
            [_candidate()],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots=self.snapshots,
            page_counts=self.page_counts,
            group_map={"other-source": "group-9"},
        )

        row = result.output_rows[0]
        self.assertEqual("Quarterly Markets Review Q1", row["Source"])
        self.assertNotIn("(Quarterly", row["Full Commentary"])


class CrossLeafDedupTest(unittest.TestCase):
    """One source doc emitting the same view on the same evidence under several
    leaves. Fixtures mirror the four frozen pilot-05 clusters plus the
    no-named-leaf fallback."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Test Firm",
                date="4/2/2026",
                source="Outlook",
                url="https://example.test/source",
            )
        }

    def _assemble(self, candidates: list[CandidateCall], evidence_texts: list[str]):
        snapshot = " ".join(dict.fromkeys(evidence_texts)) + " " + "Context. " * 40
        return assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={("source-1", "p1-5"): snapshot},
            page_counts={"source-1": 1},
        )

    def _cluster(self, evidence: str, leaves: list[str], view: str = "O") -> list[CandidateCall]:
        return [_candidate(sub_asset_class=leaf, view=view, evidence_quote=evidence) for leaf in leaves]

    def test_aberdeen_fanout_collapses_to_the_named_leaf(self) -> None:
        evidence = "We expect AI to remain a tailwind for the region and for EMs overall."
        candidates = self._cluster(evidence, ["AI", "Asia Equities", "Emerging Markets Equities"])

        result = self._assemble(candidates, [evidence])

        kept = {row["Sub-Asset Class"] for row in result.output_rows}
        self.assertEqual({"AI"}, kept)
        dropped = [f for f in result.failures if f.reason_code == "duplicate_cross_leaf"]
        self.assertEqual({"Asia Equities", "Emerging Markets Equities"}, {f.sub_asset_class for f in dropped})
        self.assertIn("'AI'", dropped[0].message)

    def test_jpm_currency_pair_both_named_survive(self) -> None:
        evidence = "Leaving us long NOK versus GBP and long AUD versus USD and GBP."
        candidates = self._cluster(evidence, ["NOK", "AUD"])

        result = self._assemble(candidates, [evidence])

        self.assertEqual({"NOK", "AUD"}, {row["Sub-Asset Class"] for row in result.output_rows})
        self.assertEqual([], [f for f in result.failures if f.reason_code == "duplicate_cross_leaf"])

    def test_jpm_sector_pair_both_named_survive(self) -> None:
        evidence = "We maintain overweights to the information technology and communication services sectors."
        candidates = self._cluster(evidence, ["IT/Tech/Telecomms (inc. AI)", "Communication Services"])

        result = self._assemble(candidates, [evidence])

        self.assertEqual(
            {"IT/Tech/Telecomms (inc. AI)", "Communication Services"},
            {row["Sub-Asset Class"] for row in result.output_rows},
        )
        self.assertEqual([], [f for f in result.failures if f.reason_code == "duplicate_cross_leaf"])

    def test_pimco_box_keeps_only_the_leaves_the_text_names(self) -> None:
        evidence = "Consider structural allocations to real assets and commodities to hedge energy shocks."
        candidates = self._cluster(
            evidence, ["Commodities", "Real Assets", "Inflation-Linked/TIPs"]
        )

        result = self._assemble(candidates, [evidence])

        self.assertEqual(
            {"Commodities", "Real Assets"}, {row["Sub-Asset Class"] for row in result.output_rows}
        )
        dropped = [f for f in result.failures if f.reason_code == "duplicate_cross_leaf"]
        self.assertEqual({"Inflation-Linked/TIPs"}, {f.sub_asset_class for f in dropped})

    def test_no_named_leaf_falls_back_to_highest_overlap_then_taxonomy_order(self) -> None:
        # Neither leaf name appears in the generic evidence: overlap ties at 0,
        # so the tie-break is locked-taxonomy order (Gold/Precious #143 < Oil
        # #146), which keeps Gold/Precious.
        evidence = "The broad backdrop is constructive across the board."
        candidates = self._cluster(evidence, ["Oil", "Gold/Precious"])

        result = self._assemble(candidates, [evidence])

        self.assertEqual({"Gold/Precious"}, {row["Sub-Asset Class"] for row in result.output_rows})
        self.assertEqual(
            {"Oil"},
            {f.sub_asset_class for f in result.failures if f.reason_code == "duplicate_cross_leaf"},
        )

    def test_same_evidence_different_view_never_collapses(self) -> None:
        evidence = "The broad backdrop is constructive across the board."
        candidates = [
            _candidate(sub_asset_class="Oil", view="O", evidence_quote=evidence),
            _candidate(sub_asset_class="Gold/Precious", view="U", evidence_quote=evidence),
        ]

        result = self._assemble(candidates, [evidence])

        self.assertEqual(2, len(result.output_rows))
        self.assertEqual([], [f for f in result.failures if f.reason_code == "duplicate_cross_leaf"])

    def test_different_evidence_same_view_never_collapses(self) -> None:
        candidates = [
            _candidate(sub_asset_class="Oil", view="O", evidence_quote="Oil prices should settle higher."),
            _candidate(
                sub_asset_class="Gold/Precious", view="O", evidence_quote="Gold remains a portfolio ballast."
            ),
        ]

        result = self._assemble(
            candidates, ["Oil prices should settle higher.", "Gold remains a portfolio ballast."]
        )

        self.assertEqual(2, len(result.output_rows))
        self.assertEqual([], [f for f in result.failures if f.reason_code == "duplicate_cross_leaf"])


class SiblingConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Test Firm",
                date="4/2/2026",
                source="Outlook",
                url="https://example.test/source",
            )
        }

    def _assemble(self, candidates: list[CandidateCall]):
        evidence = "Shared dial row shows the pairwise allocation stance."
        return assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={("source-1", "p1-5"): evidence + " " + "Context. " * 40},
            page_counts={"source-1": 1},
        )

    def test_flags_opposite_directional_siblings_on_same_evidence(self) -> None:
        evidence = "Shared dial row shows the pairwise allocation stance."
        candidates = [
            _candidate(
                sub_asset_class="US Equities",
                view="O",
                evidence_quote=evidence,
                locator="p.3",
            ),
            _candidate(
                sub_asset_class="Japan Equities",
                view="U",
                evidence_quote=evidence,
                locator="p.3",
            ),
        ]

        result = self._assemble(candidates)

        self.assertEqual(2, len(result.output_rows))
        self.assertEqual({"review"}, {row["review_flag"] for row in result.output_rows})
        self.assertTrue(
            all("Sibling consistency" in row["Full Commentary"] for row in result.output_rows)
        )

    def test_neutral_versus_underweight_does_not_trigger_sibling_flag(self) -> None:
        evidence = "Shared dial row shows the pairwise allocation stance."
        candidates = [
            _candidate(
                sub_asset_class="US Equities",
                view="N",
                evidence_quote=evidence,
                locator="p.3",
            ),
            _candidate(
                sub_asset_class="Japan Equities",
                view="U",
                evidence_quote=evidence,
                locator="p.3",
            ),
        ]

        result = self._assemble(candidates)

        self.assertEqual(2, len(result.output_rows))
        self.assertTrue(
            all("Sibling consistency" not in row["Full Commentary"] for row in result.output_rows)
        )


class BasisOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Test Firm",
                date="4/2/2026",
                source="Outlook",
                url="https://example.test/source",
            )
        }

    def _assemble(self, candidate: CandidateCall, snapshot: str):
        return assemble_candidates(
            [candidate],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={("source-1", "p1-5"): snapshot + " " + "Context. " * 40},
            page_counts={"source-1": 1},
        )

    def test_forecast_delta_row_exposes_basis_and_cap_reason(self) -> None:
        candidate = _candidate(
            sub_asset_class="Global Govt Bonds/SSAs",
            evidence_kind="table",
            evidence_quote="Global row Long Rates forecast endpoint move",
            locator="p.10 - 'Forecast Table'",
            basis="forecast_delta",
            delta_value=40,
            delta_unit="bp",
            call_language="implied",
        )
        result = self._assemble(candidate, "Global row Long Rates forecast endpoint move")

        row = result.output_rows[0]
        self.assertEqual("forecast_delta", row["basis"])
        self.assertEqual("74", row["confidence"])
        self.assertEqual("review", row["review_flag"])
        self.assertIn("Basis: forecast_delta", row["Full Commentary"])

    def test_inferred_row_exposes_basis_and_cap_reason(self) -> None:
        candidate = _candidate(basis="inferred")
        result = self._assemble(candidate, "EM equities are favored in the outlook.")

        row = result.output_rows[0]
        self.assertEqual("inferred", row["basis"])
        self.assertEqual("review", row["review_flag"])
        self.assertIn("Basis: inferred", row["Full Commentary"])

    def test_stated_row_defaults_basis_column(self) -> None:
        result = self._assemble(_candidate(), "EM equities are favored in the outlook.")
        self.assertEqual("stated", result.output_rows[0]["basis"])

    def test_failure_row_carries_basis(self) -> None:
        # A sub-floor forecast delta hard-fails and records its basis.
        candidate = _candidate(
            sub_asset_class="Asia Fixed Income",
            evidence_kind="table",
            evidence_quote="Asia row Long Rates forecast endpoint move",
            locator="p.10 - 'Forecast Table'",
            basis="forecast_delta",
            delta_value=4,
            delta_unit="bp",
        )
        result = self._assemble(candidate, "Asia row Long Rates forecast endpoint move")

        self.assertEqual([], result.output_rows)
        failure = next(f for f in result.failures if f.reason_code == "delta_below_materiality")
        self.assertEqual("forecast_delta", failure.basis)


class CallLanguageOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Test Firm",
                date="4/2/2026",
                source="Outlook",
                url="https://example.test/source",
            )
        }

    def _assemble(self, candidate: CandidateCall, snapshot: str):
        return assemble_candidates(
            [candidate],
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={("source-1", "p1-5"): snapshot + " " + "Context. " * 40},
            page_counts={"source-1": 1},
        )

    def test_output_row_persists_directional_call_language(self) -> None:
        result = self._assemble(
            _candidate(call_language="directional"), "EM equities are favored in the outlook."
        )
        self.assertEqual("directional", result.output_rows[0]["call_language"])

    def test_explicit_dial_on_prose_persists_downgraded_effective_value(self) -> None:
        # explicit_dial is accepted only for table/visual; on prose it downgrades
        # to explicit_stance, and it is the EFFECTIVE (scored) value that persists.
        result = self._assemble(
            _candidate(call_language="explicit_dial"), "EM equities are favored in the outlook."
        )
        row = result.output_rows[0]
        self.assertEqual("explicit_stance", row["call_language"])
        self.assertIn("scored as explicit_stance", row["Full Commentary"])

    def test_explicit_dial_on_table_evidence_is_kept(self) -> None:
        candidate = _candidate(
            call_language="explicit_dial",
            evidence_kind="table",
            evidence_quote="Emerging Markets Equities Overweight",
            locator="p.5 - 'Regional allocation grid'",
        )
        result = self._assemble(candidate, "Emerging Markets Equities Overweight")
        row = result.output_rows[0]
        self.assertEqual("explicit_dial", row["call_language"])
        self.assertNotIn("scored as explicit_stance", row["Full Commentary"])

    def test_failure_row_carries_effective_call_language(self) -> None:
        # A taxonomy failure still records the effective (downgraded) grade.
        candidate = _candidate(sub_asset_class="Not A Leaf", call_language="explicit_dial")
        result = self._assemble(candidate, "EM equities are favored in the outlook.")

        failure = next(f for f in result.failures if f.reason_code == "taxonomy_no_match")
        self.assertEqual("explicit_stance", failure.call_language)

    def test_manifest_reports_call_language_distribution(self) -> None:
        result = self._assemble(
            _candidate(call_language="directional"), "EM equities are favored in the outlook."
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            write_run_outputs(result, temp_dir)
            manifest = (Path(temp_dir) / "manifest.md").read_text(encoding="utf-8")
        self.assertIn("Call language (kept rows)", manifest)
        self.assertIn("- directional: 1", manifest)


class StatedBeatsImpliedTest(unittest.TestCase):
    """Deterministic stated-beats-implied resolution (client decision 5): a
    same-leaf conflict between a stated call and an implied one is resolved
    without the arbiter — stated wins, the implied side is logged as a flagged
    recommendation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = Taxonomy.from_csv()
        cls.sources = {
            "source-1": SourceInfo(
                source_id="source-1",
                firm="Test Firm",
                date="4/2/2026",
                source="Outlook",
                url="https://example.test/source",
            )
        }

    def _assemble(self, candidates: list[CandidateCall], *, arbiter=None):
        snapshot = (
            "EM equities are favored in the outlook. "
            "Capital is leaving the region at pace. " + "Context. " * 30
        )
        return assemble_candidates(
            candidates,
            sources=self.sources,
            taxonomy=self.taxonomy,
            snapshots={("source-1", "p1-5"): snapshot},
            page_counts={"source-1": 1},
            arbiter=arbiter,
        )

    def _implied(self, view: str, reasoning: str) -> CandidateCall:
        return _candidate(
            view=view,
            basis="inferred",
            call_language="implied",
            evidence_quote="Capital is leaving the region at pace.",
            reasoning=reasoning,
        )

    def test_conflict_resolves_deterministically_without_the_arbiter(self) -> None:
        def _never(group):
            raise AssertionError("arbiter must not run on a stated-vs-implied conflict")

        stated = _candidate(view="O")  # basis defaults to stated
        implied = self._implied("U", "Capital flight implies EM equities underweight.")

        result = self._assemble([stated, implied], arbiter=_never)

        self.assertEqual(1, len(result.output_rows))
        row = result.output_rows[0]
        self.assertEqual("O", row["View"])
        self.assertEqual("stated", row["basis"])
        self.assertEqual("review", row["review_flag"])
        self.assertIn("Implied-call challenge", row["Full Commentary"])

        self.assertEqual(
            [], [f for f in result.failures if f.reason_code == "arbitrated_out"]
        )
        challenge = next(
            f for f in result.failures if f.reason_code == "implied_challenges_stated"
        )
        self.assertEqual("inferred", challenge.basis)
        self.assertIn("reconsider the stated", challenge.message)
        self.assertIn("Capital flight", challenge.message)  # the inference reasoning is recorded

    def test_same_view_implied_is_plain_dedup_not_a_challenge(self) -> None:
        stated = _candidate(view="O")
        implied = self._implied("O", "A thematic read also lands EM equities overweight.")

        result = self._assemble([stated, implied])

        self.assertEqual(1, len(result.output_rows))
        row = result.output_rows[0]
        self.assertEqual("O", row["View"])
        self.assertEqual("stated", row["basis"])
        self.assertNotIn("Implied-call challenge", row["Full Commentary"])
        self.assertEqual({"duplicate_same_view"}, {f.reason_code for f in result.failures})
        self.assertEqual("inferred", result.failures[0].basis)

    def test_both_stated_conflict_still_uses_the_arbiter(self) -> None:
        used: dict[str, bool] = {}

        def arbiter(group):
            used["called"] = True
            return 0, "arbiter picked the first"

        stated_o = _candidate(view="O")
        stated_u = _candidate(view="U", evidence_quote="Capital is leaving the region at pace.")

        result = self._assemble([stated_o, stated_u], arbiter=arbiter)

        self.assertTrue(used.get("called"))
        self.assertEqual(1, len(result.output_rows))
        self.assertEqual(["arbitrated_out"], [f.reason_code for f in result.failures])

    def test_forecast_delta_present_falls_through_to_the_arbiter(self) -> None:
        used: dict[str, bool] = {}

        def arbiter(group):
            used["called"] = True
            return 0, "arbiter resolves"

        stated = _candidate(view="O")
        delta = _candidate(
            view="U",
            basis="forecast_delta",
            delta_value=40,
            delta_unit="bp",
            evidence_kind="table",
            evidence_quote="Capital is leaving the region at pace.",
            call_language="implied",
            locator="p.10 - 'Forecast Table'",
        )

        result = self._assemble([stated, delta], arbiter=arbiter)

        self.assertTrue(used.get("called"))
        self.assertEqual(
            [], [f for f in result.failures if f.reason_code == "implied_challenges_stated"]
        )


def _verdict(**overrides: object) -> CheckVerdict:
    values = {
        "index": 0,
        "supports_view": "pass",
        "forward_looking": "pass",
        "asset_match": "pass",
        "evidence_strength": "decisive",
        "note": "",
    }
    values.update(overrides)
    return CheckVerdict.from_mapping(values)


def _long_snapshot(text: str) -> str:
    return text + " " + ("Broader market context. " * 80)


def _candidate(**overrides: object) -> CandidateCall:
    values = {
        "source_id": "source-1",
        "chunk_id": "p1-5",
        "sub_asset_raw": "EM equities",
        "sub_asset_class": "Emerging Markets Equities",
        "taxonomy_match": "exact",
        "view": "O",
        "call_language": "explicit",
        "evidence_kind": "prose",
        "evidence_quote": "EM equities are favored in the outlook.",
        "locator": "p.3",
        "reasoning": "The manager favors the asset class.",
        "conflict": False,
    }
    values.update(overrides)
    return CandidateCall.from_mapping(values)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    unittest.main()
