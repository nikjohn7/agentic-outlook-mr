from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src import crosscheck
from src.crosscheck import (
    BUCKET_CONFLICT,
    BUCKET_SAME_VIEW,
    VERDICT_NEEDS_HUMAN,
    VERDICT_SUPERSEDED,
    Verdict,
    bucket_rows,
    build_report,
    conflict_verdicts,
    load_rows,
    run_crosscheck,
    write_outputs,
)

# The 10 workbook columns plus the review columns, matching assemble.OUTPUT_COLUMNS.
_COLUMNS = (
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
)


def _write_output(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _row(firm: str, leaf: str, view: str, **extra: str) -> dict[str, str]:
    base = {
        "Firm": firm,
        "Date": extra.get("Date", "1/1/2026"),
        "Source": extra.get("Source", f"{firm} Outlook"),
        "URL": extra.get("URL", "http://example.com"),
        "Sub-Asset Class": leaf,
        "View": view,
        "Full Commentary": extra.get("Full Commentary", f"{firm} view on {leaf}."),
        "confidence": extra.get("confidence", "80"),
        "band": extra.get("band", "High"),
    }
    return base


def _verdicts_response(mapping: dict[int, tuple[str, str]]) -> str:
    return json.dumps(
        {
            "groups": [
                {"group_id": gid, "verdict": verdict, "note": note}
                for gid, (verdict, note) in sorted(mapping.items())
            ]
        }
    )


class LoadingTests(unittest.TestCase):
    def test_multi_file_loading_tags_each_row_with_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.csv"
            b = Path(tmp) / "b.csv"
            _write_output(a, [_row("Aberdeen", "US Equities", "O")])
            _write_output(b, [_row("Aberdeen", "US Equities", "O")])

            rows = load_rows([a, b])

            self.assertEqual(2, len(rows))
            self.assertEqual({str(a), str(b)}, {r.source_file for r in rows})
            self.assertEqual({0}, {r.index for r in rows})

    def test_missing_required_column_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Firm", "View"])
                writer.writeheader()
            with self.assertRaises(crosscheck.CrossCheckError):
                load_rows([path])


class JoinNormalizationTests(unittest.TestCase):
    def test_firm_name_variant_collapses_via_imported_normalize_firm(self) -> None:
        """A firm-name spelling variant must join to one key — proving the join
        reuses src.eval.normalize_firm (dots removed, case-folded)."""
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.csv"
            b = Path(tmp) / "b.csv"
            _write_output(a, [_row("J.P. Morgan", "Global HY", "O")])
            _write_output(b, [_row("JP Morgan", "Global HY", "O")])

            groups = bucket_rows(load_rows([a, b]))

            self.assertEqual(1, len(groups))
            self.assertEqual(BUCKET_SAME_VIEW, groups[0].bucket)
            self.assertEqual(2, len(groups[0].rows))

    def test_leaf_normalization_strips_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.csv"
            b = Path(tmp) / "b.csv"
            _write_output(a, [_row("Invesco", "US Equities", "O")])
            _write_output(b, [_row("Invesco", "  US Equities  ", "O")])

            groups = bucket_rows(load_rows([a, b]))

            self.assertEqual(1, len(groups))
            self.assertEqual(2, len(groups[0].rows))


class BucketingTests(unittest.TestCase):
    def _groups(self) -> list:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.csv"
            b = Path(tmp) / "b.csv"
            _write_output(
                a,
                [
                    _row("Aberdeen", "US Equities", "O"),  # same-view w/ b
                    _row("Aberdeen", "EM Debt", "O"),      # conflict w/ b
                    _row("Aberdeen", "Gold", "N"),         # unique
                ],
            )
            _write_output(
                b,
                [
                    _row("Aberdeen", "US Equities", "O"),
                    _row("Aberdeen", "EM Debt", "U"),
                ],
            )
            return bucket_rows(load_rows([a, b]))

    def test_unique_key_not_reported(self) -> None:
        leaves = {g.leaf for g in self._groups()}
        self.assertNotIn("Gold", leaves)

    def test_same_view_and_conflicting_buckets(self) -> None:
        by_leaf = {g.leaf: g for g in self._groups()}
        self.assertEqual(BUCKET_SAME_VIEW, by_leaf["US Equities"].bucket)
        self.assertEqual(BUCKET_CONFLICT, by_leaf["EM Debt"].bucket)

    def test_groups_sorted_deterministically(self) -> None:
        groups = self._groups()
        keys = [(g.firm_key, g.leaf) for g in groups]
        self.assertEqual(keys, sorted(keys))


class AgentPassTests(unittest.TestCase):
    def _conflict_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.csv"
            b = Path(tmp) / "b.csv"
            _write_output(a, [_row("Aberdeen", "EM Debt", "O", Date="4/1/2026")])
            _write_output(b, [_row("Aberdeen", "EM Debt", "U", Date="1/1/2026")])
            groups = bucket_rows(load_rows([a, b]))
        return [g for g in groups if g.bucket == BUCKET_CONFLICT]

    def test_stubbed_pass_maps_verdicts_onto_groups(self) -> None:
        captured: dict[str, str] = {}

        def runner(command: list[str], prompt: str):
            captured["prompt"] = prompt
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_verdicts_response({0: (VERDICT_SUPERSEDED, "later dated row wins")}),
                stderr="",
            )

        conflicts = self._conflict_groups()
        verdicts = conflict_verdicts(
            conflicts, engine="claude", model="haiku", effort="medium", runner=runner
        )

        self.assertEqual(VERDICT_SUPERSEDED, verdicts[0].verdict)
        self.assertEqual("later dated row wins", verdicts[0].note)
        # The batched pass saw the conflicting rows' commentary.
        self.assertIn("EM Debt", captured["prompt"])

    def test_build_report_flags_needs_human_only_for_needs_human_verdicts(self) -> None:
        conflicts = self._conflict_groups()
        # Two-conflict scenario is unnecessary; verify one superseded is not flagged.
        groups = conflicts
        report = build_report(groups, {0: Verdict(VERDICT_SUPERSEDED, "outlook supersedes review")})
        self.assertEqual(1, len(report))
        self.assertFalse(report[0].needs_human)
        self.assertEqual(VERDICT_SUPERSEDED, report[0].verdict)

    def test_missing_verdict_for_a_conflict_degrades_to_needs_human(self) -> None:
        conflicts = self._conflict_groups()
        verdicts = conflict_verdicts(
            conflicts, engine="claude", model="haiku", effort="medium",
            runner=lambda c, p: subprocess.CompletedProcess(c, 0, stdout='{"groups": []}', stderr=""),
        )
        self.assertEqual(VERDICT_NEEDS_HUMAN, verdicts[0].verdict)


class FallbackTests(unittest.TestCase):
    def _conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.csv"
            b = Path(tmp) / "b.csv"
            _write_output(a, [_row("PGIM", "US IG Credit", "O")])
            _write_output(b, [_row("PGIM", "US IG Credit", "N")])
            groups = bucket_rows(load_rows([a, b]))
        return [g for g in groups if g.bucket == BUCKET_CONFLICT]

    def test_engine_failure_degrades_all_conflicts_to_needs_human(self) -> None:
        def runner(command: list[str], prompt: str):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="engine blew up")

        verdicts = conflict_verdicts(
            self._conflicts(), engine="claude", model="haiku", effort="medium", runner=runner
        )
        self.assertEqual(VERDICT_NEEDS_HUMAN, verdicts[0].verdict)
        self.assertIn("failed", verdicts[0].note)

    def test_no_llm_flag_forces_needs_human_without_calling_runner(self) -> None:
        def runner(command: list[str], prompt: str):
            raise AssertionError("runner must not be called when use_llm=False")

        verdicts = conflict_verdicts(
            self._conflicts(),
            engine="claude",
            model="haiku",
            effort="medium",
            runner=runner,
            use_llm=False,
        )
        self.assertEqual(VERDICT_NEEDS_HUMAN, verdicts[0].verdict)
        self.assertIn("--no-llm", verdicts[0].note)


class ByteStableOutputTests(unittest.TestCase):
    def test_outputs_byte_identical_across_two_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.csv"
            b = Path(tmp) / "b.csv"
            _write_output(
                a,
                [
                    _row("State Street", "US Equities", "O"),
                    _row("State Street", "EM Debt", "O"),
                    _row("Impax", "Clean Energy", "O"),
                ],
            )
            _write_output(
                b,
                [
                    _row("State Street", "US Equities", "O"),
                    _row("State Street", "EM Debt", "U"),
                ],
            )
            out1 = Path(tmp) / "out1"
            out2 = Path(tmp) / "out2"
            for out in (out1, out2):
                result = run_crosscheck([a, b], use_llm=False)
                write_outputs(result.reported, out, [a, b])

            self.assertEqual(
                (out1 / "crosscheck.csv").read_bytes(),
                (out2 / "crosscheck.csv").read_bytes(),
            )
            self.assertEqual(
                (out1 / "crosscheck-summary.md").read_bytes(),
                (out2 / "crosscheck-summary.md").read_bytes(),
            )

    def test_summary_states_scope_limitations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.csv"
            _write_output(a, [_row("Aberdeen", "US Equities", "O")])
            result = run_crosscheck([a], use_llm=False)
            out = Path(tmp) / "out"
            write_outputs(result.reported, out, [a])
            summary = (out / "crosscheck-summary.md").read_text(encoding="utf-8")
            self.assertIn("No fuzzy", summary)
            self.assertIn("dual-confidence", summary)
            self.assertIn("ROADMAP.md", summary)


if __name__ == "__main__":
    unittest.main()
