from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import sharedevidence
from src.sharedevidence import (
    Entry,
    EvidenceGroup,
    GroupVerdict,
    Member,
    apply_verdict,
    build_groups,
    cluster_by_passage,
    gate_entries,
    gate_rows,
    judge_groups,
    parse_select_evidence,
    same_passage,
)


def _entry(entry_id: int, leaf: str, evidence: str, *, source="doc-1", view="O", reasoning="because") -> Entry:
    return Entry(
        entry_id=entry_id,
        source_key=source,
        firm="Firm",
        source_title="Doc",
        evidence=evidence,
        leaf=leaf,
        view=view,
        reasoning=reasoning,
        basis="stated",
        asset_class="Equities",
        category="Equities - Region",
    )


LONG = "Smaller companies are often at the forefront of innovation and have three structural shifts in their favour"


class GroupingTest(unittest.TestCase):
    def test_same_passage_equal_or_bounded_containment(self) -> None:
        self.assertTrue(same_passage("we favour em", "we favour em"))
        self.assertFalse(same_passage("we favour em", "we favour em equities"))  # too short to contain
        long = LONG.lower()
        self.assertTrue(same_passage(long, long + " this year"))
        self.assertFalse(same_passage("", long))

    def test_cluster_by_passage_returns_only_multi_member_clusters(self) -> None:
        items = [(1, "a" * 70), (2, "a" * 70 + " tail"), (3, "unrelated text"), (4, "a" * 70)]
        self.assertEqual([[1, 2, 4]], cluster_by_passage(items))

    def test_build_groups_needs_two_leaves_in_one_document(self) -> None:
        entries = [
            _entry(1, "Small/Mid-Cap", LONG),
            _entry(2, "Supply Chain Reshoring", LONG),
            _entry(3, "Small/Mid-Cap", LONG, source="doc-2"),  # other document: never grouped
            _entry(4, "Oil", "Oil prices peak in Q2"),
            _entry(5, "Oil", "Oil prices peak in Q2", view="U"),  # same leaf twice: not a shared-evidence question
        ]
        groups = build_groups(entries)
        self.assertEqual(1, len(groups))
        self.assertEqual((1, 2), tuple(m.row_id for m in groups[0].members))
        self.assertEqual(LONG, groups[0].evidence)


class ParseAndJudgeTest(unittest.TestCase):
    def test_parse_contract(self) -> None:
        verdicts = parse_select_evidence('{"groups": [{"group_id": 0, "keep": [2, 2], "reason": "names it"}]}')
        self.assertEqual(GroupVerdict((2,), "names it"), verdicts[0])
        for bad in (
            '{"groups": [{"group_id": 0, "keep": [], "reason": ""}]}',
            '{"groups": [{"group_id": "0", "keep": [1], "reason": ""}]}',
            '{"groups": [{"group_id": 0, "keep": "1", "reason": ""}]}',
            '[]',
        ):
            with self.assertRaises(ValueError):
                parse_select_evidence(bad)

    def test_judge_batches_and_fails_closed_per_batch(self) -> None:
        groups = [
            EvidenceGroup(i, "Firm", "Doc", f"evidence {i}", (Member(0, "A", "O", "r", "stated", "Equities", "c"), Member(1, "B", "O", "r", "stated", "Equities", "c")))
            for i in range(sharedevidence.BATCH_MAX_GROUPS + 1)
        ]
        calls: list[int] = []

        def runner(command, prompt):
            marker = "## Machine-readable inputs\n"
            payload = json.loads(prompt[prompt.rindex(marker) + len(marker):])
            ids = [g["group_id"] for g in payload.get("groups", [])]
            calls.append(len(ids))
            if len(calls) == 2:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")
            body = {"groups": [{"group_id": i, "keep": [0], "reason": "first"} for i in ids]}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(body), stderr="")

        verdicts = judge_groups(groups, engine="claude", model="opus", effort="medium", runner=runner)
        self.assertEqual([sharedevidence.BATCH_MAX_GROUPS, 1], calls)
        self.assertEqual((0,), verdicts[0].keep)
        self.assertTrue(verdicts[sharedevidence.BATCH_MAX_GROUPS].failed)

    def test_no_llm_degrades_every_group(self) -> None:
        group = EvidenceGroup(0, "F", "D", "e", (Member(0, "A", "O", "r", "stated", "", ""),))
        self.assertTrue(judge_groups([group], use_llm=False)[0].failed)


class ApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.group = EvidenceGroup(
            0, "Firm", "Doc", LONG,
            (
                Member(1, "Small/Mid-Cap", "O", "named", "stated", "Equities", "c"),
                Member(2, "Supply Chain Reshoring", "O", "theme", "inferred", "Equities", "c"),
            ),
        )

    def test_keep_one_drops_the_other(self) -> None:
        outcome = apply_verdict(self.group, GroupVerdict((1,), "names smaller companies"))
        self.assertEqual(((1,), (2,), False), (outcome.kept, outcome.dropped, outcome.failed))
        self.assertFalse(outcome.review)
        self.assertIn("'Small/Mid-Cap'", sharedevidence.dropped_message(outcome, 2))
        note = sharedevidence.kept_note(outcome, 1)
        self.assertIn("also read as 'Supply Chain Reshoring'", note)
        self.assertIn("most compelling fit", note)

    def test_keep_both_is_noted_not_flagged(self) -> None:
        outcome = apply_verdict(self.group, GroupVerdict((1, 2), "names both"))
        self.assertEqual((), outcome.dropped)
        self.assertFalse(outcome.review)
        note = sharedevidence.kept_note(outcome, 1)
        self.assertIn("equally supports 'Supply Chain Reshoring'", note)
        self.assertNotIn("review", note)

    def test_ids_outside_the_group_fail_closed(self) -> None:
        outcome = apply_verdict(self.group, GroupVerdict((1, 9), "?"))
        self.assertTrue(outcome.failed)
        self.assertEqual((1, 2), outcome.kept)
        self.assertIn("could not be ranked", sharedevidence.kept_note(outcome, 2))

    def test_failed_or_missing_verdict_keeps_all(self) -> None:
        self.assertEqual((1, 2), apply_verdict(self.group, None).kept)
        self.assertEqual((1, 2), apply_verdict(self.group, GroupVerdict((), "x", failed=True)).kept)

    def test_gate_entries_end_to_end(self) -> None:
        entries = [_entry(1, "Small/Mid-Cap", LONG), _entry(2, "Supply Chain Reshoring", LONG), _entry(3, "Oil", "oil")]
        result = gate_entries(entries, lambda groups: {0: GroupVerdict((1,), "named")})
        self.assertEqual({2}, set(result.dropped))
        self.assertEqual({1}, set(result.kept_notes))
        self.assertEqual(1, result.group_count)


class GateRowsTest(unittest.TestCase):
    def _row(self, leaf: str, evidence: str, **extra) -> dict[str, str]:
        base = {
            "Firm": "Firm", "Date": "", "Source": "Doc", "URL": "u", "Sub-Asset Class": leaf,
            "Asset Class Category": "c", "Canva Groupings": "g", "Asset Class": "Equities", "View": "O",
            "Full Commentary": f"R. Evidence: {evidence}. Locator: p.1.",
            "confidence": "80", "band": "High", "review_flag": "none", "basis": "stated",
            "checker_strength": "", "call_language": "", "quote_match": "",
        }
        base.update(extra)
        return base

    def test_rows_are_gated_notes_flags_and_failures(self) -> None:
        rows = [self._row("Small/Mid-Cap", LONG), self._row("Supply Chain Reshoring", LONG), self._row("Oil", "oil")]
        survivors, failures, audit, result = gate_rows(rows, lambda groups: {0: GroupVerdict((0,), "named")})
        self.assertEqual(["Small/Mid-Cap", "Oil"], [r["Sub-Asset Class"] for r in survivors])
        self.assertEqual("none", survivors[0]["review_flag"])  # a clean single keep is noted, not a review item
        self.assertIn("Shared evidence:", survivors[0]["Full Commentary"])
        self.assertNotIn("Reviewer Notes", survivors[0])  # legacy columns are kept as-is; export adds the split
        self.assertEqual("none", survivors[1]["review_flag"])
        self.assertEqual(1, len(failures))
        self.assertEqual("Evidence better supports a related asset", failures[0]["What happened"])
        self.assertEqual("Supply Chain Reshoring", failures[0]["Sub-Asset Class"])
        self.assertEqual({"kept", "dropped"}, {a["action"] for a in audit})
        # Input rows untouched.
        self.assertEqual("none", rows[0]["review_flag"])

    def test_only_a_failed_verdict_flags_review(self) -> None:
        rows = [self._row("South Korea Equities", LONG), self._row("Taiwan Equities", LONG)]
        both, both_failures, _, _ = gate_rows(rows, lambda groups: {0: GroupVerdict((0, 1), "names both")})
        self.assertEqual({"none"}, {r["review_flag"] for r in both})
        self.assertEqual([], both_failures)
        failed, _, audit, _ = gate_rows(rows, lambda groups: {})
        self.assertEqual({"review"}, {r["review_flag"] for r in failed})
        self.assertEqual({"kept_needs_human"}, {a["action"] for a in audit})

    def test_cli_dry_run_and_no_llm(self) -> None:
        from src.csvio import read_csv, write_csv

        rows = [self._row("Small/Mid-Cap", LONG), self._row("Supply Chain Reshoring", LONG)]
        with tempfile.TemporaryDirectory() as temp_dir:
            src = Path(temp_dir) / "output.csv"
            write_csv(src, tuple(rows[0]), rows)
            self.assertEqual(0, sharedevidence.main(["--output", str(src), "--out-dir", temp_dir + "/o", "--dry-run"]))
            self.assertFalse((Path(temp_dir) / "o").exists())  # dry run writes nothing
            self.assertEqual(0, sharedevidence.main(["--output", str(src), "--out-dir", temp_dir + "/o", "--no-llm"]))
            _, out = read_csv(Path(temp_dir) / "o" / "output.csv")
            self.assertEqual(2, len(out))  # fail-closed: both kept ...
            self.assertEqual({"review"}, {r["review_flag"] for r in out})  # ... for review
            self.assertTrue((Path(temp_dir) / "o" / "shared-evidence-audit.csv").is_file())
            self.assertTrue((Path(temp_dir) / "o" / "output-client.xlsx").is_file())

    def test_same_title_different_documents_never_grouped(self) -> None:
        rows = [self._row("Small/Mid-Cap", LONG, URL="u1"), self._row("Supply Chain Reshoring", LONG, URL="u2")]
        survivors, failures, _, result = gate_rows(rows, lambda groups: {0: GroupVerdict((0,), "named")})
        self.assertEqual(2, len(survivors))
        self.assertEqual([], failures)


class MatrixTest(unittest.TestCase):
    def test_defaults(self) -> None:
        self.assertEqual(("claude", "claude-opus-5-5", "medium"), (sharedevidence.DEFAULT_ENGINE, sharedevidence.DEFAULT_MODEL, sharedevidence.DEFAULT_EFFORT))


if __name__ == "__main__":
    unittest.main()
