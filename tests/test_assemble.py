from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from src.assemble import (
    FAILURE_COLUMNS,
    OUTPUT_COLUMNS,
    FailureRecord,
    assemble_candidates,
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
        self.assertEqual("taxonomy_no_match", failure_rows[0]["reason_code"])
        self.assertIn("count check: pass", manifest)

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
