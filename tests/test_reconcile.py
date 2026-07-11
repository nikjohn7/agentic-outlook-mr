from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import reconcile
from src.reconcile import (
    ACTION_KEPT_DISTINCT,
    ACTION_MERGED,
    ACTION_NEEDS_HUMAN,
    ACTION_SUPERSEDED,
    ACTION_WINNER,
    REASON_MERGED,
    REASON_SUPERSEDED,
    VERDICT_DISTINCT,
    VERDICT_NEEDS_HUMAN,
    VERDICT_SAME_CLAIM,
    Row,
    Verdict,
    merge_same_view,
    parse_reconcile_scope,
    resolve_conflict,
    run_reconcile,
    scope_verdicts,
    write_outputs,
)


def _row(
    *,
    firm="Aberdeen Investments",
    leaf="Emerging Markets Equities",
    view="O",
    date="",
    source="Doc A",
    url="http://x/a",
    commentary="The manager favors it. Evidence: EM favored. Locator: p.3.",
    confidence=80,
    band="High",
    review_flag="none",
    basis="stated",
    checker_strength="decisive",
    call_language="explicit_stance",
    quote_match="exact",
    source_file="run/output.csv",
    index=0,
) -> Row:
    raw = {
        "Firm": firm,
        "Date": date,
        "Source": source,
        "URL": url,
        "Sub-Asset Class": leaf,
        "Asset Class Category": "Equities",
        "Canva Groupings": "EM",
        "Asset Class": "Emerging Markets Equities",
        "View": view,
        "Full Commentary": commentary,
        "confidence": "" if confidence is None else str(confidence),
        "band": band,
        "review_flag": review_flag,
        "basis": basis,
        "checker_strength": checker_strength,
        "call_language": call_language,
        "quote_match": quote_match,
    }
    return Row(
        raw=raw, firm=firm, firm_key=reconcile.normalize_firm(firm),
        leaf=reconcile._leaf_key(leaf), view=view, date=date, source_title=source,
        url=url, commentary=commentary, confidence=confidence, band=band,
        review_flag=review_flag, basis=basis, checker_strength=checker_strength,
        call_language=call_language, quote_match=quote_match, source_file=source_file,
        index=index,
    )


# --------------------------------------------------------------------------- #
# Scope-gate parsing + degrade-to-needs_human
# --------------------------------------------------------------------------- #


class ScopeParseTests(unittest.TestCase):
    def test_valid_verdicts_parse(self) -> None:
        raw = json.dumps(
            {"groups": [
                {"group_id": 0, "verdict": "same_claim", "reason": "same dial"},
                {"group_id": 1, "verdict": "distinct_claims", "reason": "different horizon"},
            ]}
        )
        parsed = parse_reconcile_scope(raw)
        self.assertEqual(VERDICT_SAME_CLAIM, parsed[0].verdict)
        self.assertEqual(VERDICT_DISTINCT, parsed[1].verdict)

    def test_bad_verdict_rejected(self) -> None:
        raw = json.dumps({"groups": [{"group_id": 0, "verdict": "superseded", "reason": "x"}]})
        with self.assertRaises(ValueError):
            parse_reconcile_scope(raw)

    def test_needs_human_verdict_rejected_from_model(self) -> None:
        # needs_human is a code-only sentinel; the model may not emit it.
        raw = json.dumps({"groups": [{"group_id": 0, "verdict": "needs_human", "reason": "x"}]})
        with self.assertRaises(ValueError):
            parse_reconcile_scope(raw)

    def test_no_llm_degrades_all_to_needs_human(self) -> None:
        groups = [[_row(view="O"), _row(view="U")]]
        verdicts = scope_verdicts(groups, engine="claude", model="sonnet", effort="medium", use_llm=False)
        self.assertEqual(VERDICT_NEEDS_HUMAN, verdicts[0].verdict)

    def test_engine_failure_degrades_all_to_needs_human(self) -> None:
        def runner(command, prompt):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

        groups = [[_row(view="O"), _row(view="U")]]
        verdicts = scope_verdicts(groups, engine="claude", model="sonnet", effort="medium", runner=runner)
        self.assertEqual(VERDICT_NEEDS_HUMAN, verdicts[0].verdict)

    def test_missing_group_verdict_degrades_that_key(self) -> None:
        def runner(command, prompt):
            # Only group 0 is answered; group 1 must degrade to needs_human.
            return subprocess.CompletedProcess(
                command, 0,
                stdout=json.dumps({"groups": [{"group_id": 0, "verdict": "same_claim", "reason": "same"}]}),
                stderr="",
            )

        groups = [[_row(view="O"), _row(view="O")], [_row(view="O"), _row(view="U")]]
        verdicts = scope_verdicts(groups, engine="claude", model="sonnet", effort="medium", runner=runner)
        self.assertEqual(VERDICT_SAME_CLAIM, verdicts[0].verdict)
        self.assertEqual(VERDICT_NEEDS_HUMAN, verdicts[1].verdict)


# --------------------------------------------------------------------------- #
# Same claim, same view → merge
# --------------------------------------------------------------------------- #


class MergeSameViewTests(unittest.TestCase):
    def test_merge_joins_sources_and_labels_commentary(self) -> None:
        members = [
            _row(source="Review Q1", url="http://x/1", date="10/06/2026", confidence=70,
                 commentary="Reasoning one. Evidence: e1. Locator: p.3.", band="Medium", review_flag="review"),
            _row(source="Outlook Q2", url="http://x/2", date="15/06/2026", confidence=85,
                 commentary="Reasoning two. Evidence: e2. Locator: p.11.", band="High", review_flag="none"),
        ]
        merged, winner, losers = merge_same_view(members)
        self.assertEqual("Outlook Q2", winner.source_title)  # max confidence wins
        self.assertEqual([members[0]], losers)
        self.assertEqual("Review Q1 | Outlook Q2", merged["Source"])
        self.assertEqual("http://x/1 | http://x/2", merged["URL"])
        self.assertEqual("10/06/2026 | 15/06/2026", merged["Date"])
        self.assertEqual("85", merged["confidence"])
        self.assertEqual("High", merged["band"])
        self.assertEqual("review", merged["review_flag"])  # OR of members
        commentary = merged["Full Commentary"]
        self.assertIn("  ||||  ", commentary)
        self.assertIn("Review Q1 (p.3): ", commentary)
        self.assertIn("Outlook Q2 (p.11): ", commentary)

    def test_merge_end_to_end_produces_one_row_and_a_merged_failure(self) -> None:
        rows = [_row(source="A", confidence=60), _row(source="B", confidence=90)]
        result = _reconcile_rows(rows, verdict=VERDICT_SAME_CLAIM)
        self.assertEqual(1, len(result.output_rows))
        self.assertEqual([REASON_MERGED], [f.reason_code for f in result.failures])
        actions = {a for d in result.decisions for a in d.per_row_action.values()}
        self.assertEqual({ACTION_WINNER, ACTION_MERGED}, actions)


# --------------------------------------------------------------------------- #
# Same claim, conflicting views → precedence ladder
# --------------------------------------------------------------------------- #


class PrecedenceLadderTests(unittest.TestCase):
    def test_recency_wins_when_all_dated_and_one_newest(self) -> None:
        members = [
            _row(view="U", date="10/06/2026", basis="stated", band="High", confidence=90),
            _row(view="O", date="15/06/2026", basis="inferred", band="Low", confidence=40),
        ]
        winner, rule, detail = resolve_conflict(members)
        self.assertEqual("recency", rule)
        self.assertEqual("O", winner.view)  # newer wins despite weaker basis/band
        self.assertIn("15/06/2026", detail)

    def test_undated_row_skips_recency_to_basis(self) -> None:
        members = [
            _row(view="U", date="", basis="inferred", band="High", confidence=90),
            _row(view="O", date="15/06/2026", basis="stated", band="Low", confidence=40),
        ]
        winner, rule, _ = resolve_conflict(members)
        self.assertEqual("basis", rule)  # recency skipped: one row undated
        self.assertEqual("O", winner.view)  # stated beats inferred

    def test_tied_newest_date_falls_through_to_basis(self) -> None:
        members = [
            _row(view="U", date="15/06/2026", basis="inferred", band="High", confidence=90),
            _row(view="O", date="15/06/2026", basis="stated", band="Low", confidence=40),
        ]
        winner, rule, _ = resolve_conflict(members)
        self.assertEqual("basis", rule)
        self.assertEqual("O", winner.view)

    def test_band_then_confidence_when_recency_and_basis_tie(self) -> None:
        members = [
            _row(view="U", date="", basis="stated", band="Medium", confidence=60),
            _row(view="O", date="", basis="stated", band="High", confidence=80),
        ]
        winner, rule, _ = resolve_conflict(members)
        self.assertEqual("confidence", rule)
        self.assertEqual("O", winner.view)

    def test_full_tie_escalates_to_needs_human(self) -> None:
        members = [
            _row(view="U", date="", basis="stated", band="High", confidence=80),
            _row(view="O", date="", basis="stated", band="High", confidence=80),
        ]
        winner, rule, _ = resolve_conflict(members)
        self.assertIsNone(winner)
        self.assertEqual("needs_human", rule)

    def test_superseded_end_to_end_keeps_winner_only(self) -> None:
        rows = [
            _row(view="U", date="10/06/2026", source="Old"),
            _row(view="O", date="15/06/2026", source="New"),
        ]
        result = _reconcile_rows(rows, verdict=VERDICT_SAME_CLAIM)
        self.assertEqual(1, len(result.output_rows))
        self.assertEqual("O", result.output_rows[0]["View"])
        self.assertEqual([REASON_SUPERSEDED], [f.reason_code for f in result.failures])

    def test_needs_human_keeps_all_rows_flagged(self) -> None:
        rows = [
            _row(view="U", date="", basis="stated", band="High", confidence=80, review_flag="none"),
            _row(view="O", date="", basis="stated", band="High", confidence=80, review_flag="none"),
        ]
        result = _reconcile_rows(rows, verdict=VERDICT_SAME_CLAIM)
        self.assertEqual(2, len(result.output_rows))  # both kept
        self.assertEqual({"review"}, {r["review_flag"] for r in result.output_rows})
        self.assertEqual([], result.failures)  # nothing dropped
        self.assertEqual(1, sum(1 for d in result.decisions if d.action_bucket == "needs_human"))


# --------------------------------------------------------------------------- #
# Distinct claims + scope-gate needs_human
# --------------------------------------------------------------------------- #


class ScopeOutcomeTests(unittest.TestCase):
    def test_distinct_claims_pass_all_rows_through(self) -> None:
        rows = [_row(view="O", source="Strategic"), _row(view="O", source="Tactical")]
        result = _reconcile_rows(rows, verdict=VERDICT_DISTINCT)
        self.assertEqual(2, len(result.output_rows))
        self.assertEqual([], result.failures)
        actions = {a for d in result.decisions for a in d.per_row_action.values()}
        self.assertEqual({ACTION_KEPT_DISTINCT}, actions)

    def test_scope_needs_human_keeps_all_and_flags(self) -> None:
        rows = [_row(view="O", source="A"), _row(view="U", source="B")]
        result = _reconcile_rows(rows, verdict=None, use_llm=False)  # degrade
        self.assertEqual(2, len(result.output_rows))
        self.assertEqual({"review"}, {r["review_flag"] for r in result.output_rows})
        self.assertEqual([ACTION_NEEDS_HUMAN, ACTION_NEEDS_HUMAN], [
            a for d in result.decisions for a in d.per_row_action.values()
        ])

    def test_single_row_key_passes_through_untouched(self) -> None:
        rows = [_row(view="O", leaf="Gold", source="Solo")]
        result = _reconcile_rows(rows, verdict=VERDICT_SAME_CLAIM)
        self.assertEqual(1, len(result.output_rows))
        self.assertEqual(0, result.multi_row_key_count)
        self.assertEqual([], result.decisions)


# --------------------------------------------------------------------------- #
# End-to-end file IO + audit
# --------------------------------------------------------------------------- #


class WriteOutputsTests(unittest.TestCase):
    def test_audit_has_one_row_per_multi_key_member_and_files_written(self) -> None:
        rows = [_row(source="A", confidence=60), _row(source="B", confidence=90)]
        result = _reconcile_rows(rows, verdict=VERDICT_SAME_CLAIM)
        with tempfile.TemporaryDirectory() as tmp:
            written = write_outputs(result, Path(tmp), [Path("run/output.csv")])
            output = _read_csv(written["output"])
            audit = _read_csv(written["audit"])
            client = _read_csv(written["failures"])
            self.assertTrue(written["summary"].is_file())
        self.assertEqual(1, len(output))
        self.assertEqual(list(reconcile.OUTPUT_COLUMNS), list(output[0].keys()))
        self.assertEqual(2, len(audit))  # both members audited
        self.assertEqual({"winner", "merged"}, {r["action"] for r in audit})
        self.assertEqual(1, len(client))  # one merged_by_reconcile client row
        self.assertEqual("Merged — same view stated in several documents", client[0]["What happened"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _reconcile_rows(rows, *, verdict, use_llm=True):
    """Drive run_reconcile over an in-memory row list by writing them to a temp
    output.csv and forcing a single scope verdict via a mock runner."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "output.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=reconcile.OUTPUT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row.raw)

        if not use_llm:
            return run_reconcile([path], engine="claude", model="sonnet", effort="medium", use_llm=False)

        def runner(command, prompt):
            # Answer every batched group (ids 0..9 covers these small fixtures)
            # with the chosen verdict.
            groups = [{"group_id": i, "verdict": verdict, "reason": "test"} for i in range(10)]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"groups": groups}), stderr="")

        return run_reconcile([path], engine="claude", model="sonnet", effort="medium", runner=runner)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# =========================================================================== #
# Phase 3 — near-leaf reconciliation
# =========================================================================== #

from src.reconcile import (  # noqa: E402
    COVERAGE_COLUMNS,
    NEARLEAF_AUDIT_COLUMNS,
    NL_SAME_CLAIM,
    REASON_NEAR_LEAF_MERGED,
    REASON_NEAR_LEAF_SUPERSEDED,
    NearLeafGroup,
    NearLeafVerdict,
    apply_cluster,
    build_clusters,
    coverage_advisory,
    generate_candidates,
    parse_nearleaf,
    run_near_leaf,
)
from src.taxonomy import load_taxonomy

_TAX = load_taxonomy()


def _nl_row(firm, leaf, view, source, *, confidence=80, date=""):
    """A reconciled Row with intentionally WRONG taxonomy lookup fields, so a
    remap test can prove all four fields are rebuilt from src.taxonomy."""
    raw = {
        "Firm": firm, "Date": date, "Source": source, "URL": "http://x/" + source,
        "Sub-Asset Class": leaf, "Asset Class Category": "WRONG", "Canva Groupings": "WRONG",
        "Asset Class": "WRONG", "View": view,
        "Full Commentary": f"Reasoning {source}. Evidence: e. Locator: p.1.",
        "confidence": "" if confidence is None else str(confidence), "band": "High",
        "review_flag": "none", "basis": "stated", "checker_strength": "decisive",
        "call_language": "explicit_stance", "quote_match": "exact",
    }
    return reconcile.row_from_raw(raw, "reconciled", 0)


def _one_cluster(rows):
    cands = generate_candidates(rows, _TAX)
    clusters = build_clusters(rows, cands, _TAX)
    assert len(clusters) == 1, f"expected 1 cluster, got {len(clusters)}"
    return clusters[0]


class NearLeafCandidateTests(unittest.TestCase):
    def test_structural_and_short_label_lanes(self) -> None:
        cases = [
            ("KKR", "US Treasuries", "Intermediate US Treasuries", "structural"),
            ("RBC Wealth", "US Credit", "US IG Credit", "structural"),
            ("Amundi", "Euro Govt Bonds", "Euro Govt Bonds (Core)", "structural"),
            ("Client", "Europe Equities", "Europe Equities - Financials", "structural"),
            ("Client", "RE - Logistics", "RE - US Logistics", "structural"),
            ("Firmz", "AI", "IT/Tech/Telecomms (inc. AI)", "short_label_containment"),
        ]
        for firm, a, b, lane in cases:
            rows = [_nl_row(firm, a, "O", "A"), _nl_row(firm, b, "O", "B")]
            cands = generate_candidates(rows, _TAX)
            self.assertEqual(1, len(cands), f"{a} <-> {b}")
            self.assertEqual(lane, cands[0].lane, f"{a} <-> {b}")

    def test_cross_firm_pairs_never_generated(self) -> None:
        rows = [_nl_row("KKR", "US Treasuries", "O", "A"),
                _nl_row("PIMCO", "Intermediate US Treasuries", "O", "B")]
        self.assertEqual([], generate_candidates(rows, _TAX))

    def test_different_asset_class_never_pairs(self) -> None:
        # US Treasuries (Fixed Income) vs Gold (a different top-level class).
        rows = [_nl_row("KKR", "US Treasuries", "O", "A"), _nl_row("KKR", "Gold", "O", "B")]
        self.assertEqual([], generate_candidates(rows, _TAX))

    def test_deterministic_and_deduplicated(self) -> None:
        rows = [
            _nl_row("KKR", "US Treasuries", "O", "A"),
            _nl_row("KKR", "Intermediate US Treasuries", "O", "B"),
            _nl_row("KKR", "US Treasuries", "O", "C"),  # duplicate leaf, second doc
        ]
        first = generate_candidates(rows, _TAX)
        self.assertEqual(1, len(first))  # one edge despite two US Treasuries rows
        self.assertEqual(first, generate_candidates(rows, _TAX))  # byte-stable


class NearLeafClusterTests(unittest.TestCase):
    def test_geographic_logistics_chain_into_one_cluster(self) -> None:
        firm = "Client"
        leaves = ["RE - Logistics", "RE - US Logistics", "RE - Europe Logistics"]
        rows = [_nl_row(firm, leaf, "O", f"D{i}") for i, leaf in enumerate(leaves)]
        cluster = _one_cluster(rows)
        self.assertEqual(set(leaves), set(cluster.leaves))
        self.assertEqual(3, len(cluster.rows))

    def test_single_row_component_is_not_a_cluster(self) -> None:
        # Two firms each with one of a near-leaf pair: no same-firm cluster forms.
        rows = [_nl_row("KKR", "US Treasuries", "O", "A"),
                _nl_row("PIMCO", "Intermediate US Treasuries", "O", "B")]
        cands = generate_candidates(rows, _TAX)
        self.assertEqual([], build_clusters(rows, cands, _TAX))


class NearLeafApplyTests(unittest.TestCase):
    def _cluster(self, rows):
        return _one_cluster(rows)

    def test_same_view_merge_rebuilds_all_four_taxonomy_fields(self) -> None:
        rows = [_nl_row("KKR", "US Treasuries", "O", "A", confidence=70),
                _nl_row("KKR", "Intermediate US Treasuries", "O", "B", confidence=90)]
        cluster = self._cluster(rows)
        verdict = NearLeafVerdict((NearLeafGroup((0, 1), NL_SAME_CLAIM,
                                                 "Intermediate US Treasuries", None, "same"),))
        decision = apply_cluster(cluster, verdict, _TAX)
        survivors = [r for r in decision.emit.values() if r is not None]
        self.assertEqual(1, len(survivors))
        row = survivors[0]
        entry = _TAX.output_fields_for("Intermediate US Treasuries")
        for field in ("Sub-Asset Class", "Asset Class Category", "Canva Groupings", "Asset Class"):
            self.assertEqual(entry[field], row[field])  # rebuilt, not "WRONG"
        self.assertEqual("review", row["review_flag"])  # near-leaf survivors are flagged
        self.assertEqual("90", row["confidence"])  # max
        self.assertEqual([REASON_NEAR_LEAF_MERGED], [f.reason_code for f in decision.failures])

    def test_cross_view_collective_pick_supersedes_others(self) -> None:
        rows = [_nl_row("Firmz", "AI", "O", "A"),
                _nl_row("Firmz", "IT/Tech/Telecomms (inc. AI)", "U", "B")]
        cluster = self._cluster(rows)
        # primary = row 1 (the underweight IT/Tech call).
        verdict = NearLeafVerdict((NearLeafGroup((0, 1), NL_SAME_CLAIM,
                                                 "IT/Tech/Telecomms (inc. AI)", 1, "collective"),))
        decision = apply_cluster(cluster, verdict, _TAX)
        survivors = [r for r in decision.emit.values() if r is not None]
        self.assertEqual(1, len(survivors))
        self.assertEqual("U", survivors[0]["View"])
        self.assertEqual("IT/Tech/Telecomms (inc. AI)", survivors[0]["Sub-Asset Class"])
        self.assertEqual([REASON_NEAR_LEAF_SUPERSEDED], [f.reason_code for f in decision.failures])

    def test_distinct_groups_keep_all_rows(self) -> None:
        rows = [_nl_row("KKR", "US Treasuries", "O", "A"),
                _nl_row("KKR", "Intermediate US Treasuries", "U", "B")]
        cluster = self._cluster(rows)
        verdict = NearLeafVerdict((NearLeafGroup((0,), "distinct", None, None, "strategic"),
                                   NearLeafGroup((1,), "distinct", None, None, "tactical")))
        decision = apply_cluster(cluster, verdict, _TAX)
        self.assertEqual(2, len([r for r in decision.emit.values() if r is not None]))
        self.assertEqual((), decision.failures)
        self.assertEqual("kept", decision.action_bucket)

    def test_invalid_canonical_label_fails_closed(self) -> None:
        rows = [_nl_row("KKR", "US Treasuries", "O", "A"),
                _nl_row("KKR", "Intermediate US Treasuries", "O", "B")]
        cluster = self._cluster(rows)
        verdict = NearLeafVerdict((NearLeafGroup((0, 1), NL_SAME_CLAIM, "Gold", None, "bad"),))
        decision = apply_cluster(cluster, verdict, _TAX)
        self.assertEqual("needs_human", decision.action_bucket)
        self.assertEqual(2, len([r for r in decision.emit.values() if r is not None]))
        self.assertEqual({"review"}, {r["review_flag"] for r in decision.emit.values()})

    def test_failed_verdict_fails_closed(self) -> None:
        rows = [_nl_row("KKR", "US Treasuries", "O", "A"),
                _nl_row("KKR", "Intermediate US Treasuries", "O", "B")]
        cluster = self._cluster(rows)
        decision = apply_cluster(cluster, NearLeafVerdict((), failed=True, reason="boom"), _TAX)
        self.assertEqual("needs_human", decision.action_bucket)

    def test_bad_partition_fails_closed(self) -> None:
        rows = [_nl_row("KKR", "US Treasuries", "O", "A"),
                _nl_row("KKR", "Intermediate US Treasuries", "O", "B")]
        cluster = self._cluster(rows)
        # row 1 never assigned -> not a partition.
        verdict = NearLeafVerdict((NearLeafGroup((0,), "distinct", None, None, "x"),))
        decision = apply_cluster(cluster, verdict, _TAX)
        self.assertEqual("needs_human", decision.action_bucket)

    def test_conflicting_view_merge_without_primary_fails_closed(self) -> None:
        rows = [_nl_row("Firmz", "AI", "O", "A"),
                _nl_row("Firmz", "IT/Tech/Telecomms (inc. AI)", "U", "B")]
        cluster = self._cluster(rows)
        verdict = NearLeafVerdict((NearLeafGroup((0, 1), NL_SAME_CLAIM, "AI", None, "no primary"),))
        decision = apply_cluster(cluster, verdict, _TAX)
        self.assertEqual("needs_human", decision.action_bucket)


class NearLeafParseTests(unittest.TestCase):
    def test_valid_partition_parses(self) -> None:
        raw = json.dumps({"clusters": [{"cluster_id": 0, "groups": [
            {"member_row_ids": [0, 1], "relationship": "same_claim",
             "canonical_leaf": "US IG Credit", "primary_row_id": 1, "reason": "same"},
            {"member_row_ids": [2], "relationship": "distinct", "reason": "sep"},
        ]}]})
        parsed = parse_nearleaf(raw)
        self.assertEqual(2, len(parsed[0].groups))
        self.assertEqual("US IG Credit", parsed[0].groups[0].canonical_leaf)

    def test_multi_row_group_without_canonical_rejected(self) -> None:
        raw = json.dumps({"clusters": [{"cluster_id": 0, "groups": [
            {"member_row_ids": [0, 1], "relationship": "same_claim", "reason": "x"}]}]})
        with self.assertRaises(ValueError):
            parse_nearleaf(raw)

    def test_bad_relationship_rejected(self) -> None:
        raw = json.dumps({"clusters": [{"cluster_id": 0, "groups": [
            {"member_row_ids": [0], "relationship": "merged", "reason": "x"}]}]})
        with self.assertRaises(ValueError):
            parse_nearleaf(raw)


class NearLeafCoverageTests(unittest.TestCase):
    def test_broad_specific_volume_is_deterministic_context_only(self) -> None:
        rows = [
            _nl_row("KKR", "US Treasuries", "O", "A"),
            _nl_row("KKR", "Intermediate US Treasuries", "O", "B"),
            _nl_row("Other", "US Treasuries", "N", "C"),  # broad leaf at a 2nd firm
        ]
        cov = coverage_advisory(rows, _TAX)
        self.assertEqual(1, len(cov))
        row = cov[0]
        self.assertEqual("US Treasuries", row["broad_leaf"])
        self.assertEqual("Intermediate US Treasuries", row["specific_leaf"])
        self.assertEqual("2", row["broad_firm_count"])
        self.assertEqual("1", row["specific_firm_count"])
        self.assertEqual("1", row["firms_with_both"])
        self.assertEqual(cov, coverage_advisory(rows, _TAX))  # deterministic


class NearLeafEndToEndTests(unittest.TestCase):
    """run_near_leaf + run_reconcile(near_leaf=True) with a branching mock runner
    that answers both the exact scope gate and the near-leaf judge."""

    @staticmethod
    def _branching_runner(nearleaf_response):
        def runner(command, prompt):
            if "near-leaf judge" in prompt:
                out = json.dumps(nearleaf_response)
            else:  # the exact-leaf scope gate
                out = json.dumps({"groups": [
                    {"group_id": i, "verdict": "same_claim", "reason": "t"} for i in range(10)]})
            return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")
        return runner

    def test_run_near_leaf_merges_and_emits_artifacts(self) -> None:
        rows = [
            {"Firm": "KKR", "Sub-Asset Class": "US Treasuries", "View": "O"},
            {"Firm": "KKR", "Sub-Asset Class": "Intermediate US Treasuries", "View": "O"},
            {"Firm": "Solo", "Sub-Asset Class": "Gold", "View": "O"},
        ]
        full = [dict(_nl_row(r["Firm"], r["Sub-Asset Class"], r["View"], "S").raw) for r in rows]
        resp = {"clusters": [{"cluster_id": 0, "groups": [
            {"member_row_ids": [0, 1], "relationship": "same_claim",
             "canonical_leaf": "Intermediate US Treasuries", "reason": "same"}]}]}
        result = run_near_leaf(full, taxonomy=_TAX, engine="claude", model="opus",
                               effort="medium", runner=self._branching_runner(resp))
        self.assertEqual(2, len(result.output_rows))  # 3 -> 2 (one merged away)
        self.assertEqual(1, result.merged_count)
        self.assertEqual(1, result.cluster_count)

    def test_run_reconcile_near_leaf_off_is_unchanged(self) -> None:
        # near_leaf defaults off: no near-leaf artifacts, identical exact behavior.
        rows = [_row(source="A", confidence=60), _row(source="B", confidence=90)]
        result = _reconcile_rows(rows, verdict=VERDICT_SAME_CLAIM)
        self.assertIsNone(result.near_leaf)

    def test_run_reconcile_near_leaf_on_composes_both_passes(self) -> None:
        rows = [
            _row(firm="KKR", leaf="US Treasuries", view="O", source="A"),
            _row(firm="KKR", leaf="Intermediate US Treasuries", view="O", source="B"),
        ]
        resp = {"clusters": [{"cluster_id": 0, "groups": [
            {"member_row_ids": [0, 1], "relationship": "same_claim",
             "canonical_leaf": "Intermediate US Treasuries", "reason": "same"}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=reconcile.OUTPUT_COLUMNS, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    writer.writerow(row.raw)
            result = run_reconcile(
                [path], engine="claude", model="opus", effort="medium",
                runner=self._branching_runner(resp), near_leaf=True, taxonomy=_TAX,
            )
            self.assertIsNotNone(result.near_leaf)
            self.assertEqual(1, result.near_leaf.merged_count)
            self.assertEqual(1, len(result.output_rows))  # the two near-leaves merged to one
            written = write_outputs(result, Path(tmp) / "out", [path])
            self.assertIn("nearleaf_audit", written)
            self.assertIn("coverage", written)
            audit = _read_csv(written["nearleaf_audit"])
            self.assertEqual(list(NEARLEAF_AUDIT_COLUMNS), list(audit[0].keys()))
            self.assertEqual(list(COVERAGE_COLUMNS), list(_read_csv(written["coverage"])[0].keys()))


if __name__ == "__main__":
    unittest.main()
