from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.ingest import SourceRecord, load_pilot_sources
from src.scout import (
    _apply_guards,
    multi_source_firms,
    parse_scout_groups,
    run_scout,
)


def _record(source_id: str, firm: str, title: str, date: str = "") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        firm=firm,
        date=date,
        source=title,
        url="https://example.test/x",
        resolved_url="https://example.test/x",
        source_type="html",
    )


def _runner_returning(response: str):
    def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=response, stderr="")

    return runner


def _failing_runner():
    def runner(command: list[str], prompt: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    return runner


class ParseScoutGroupsTest(unittest.TestCase):
    def test_parses_groups_and_ungrouped(self) -> None:
        response = json.dumps(
            {
                "groups": [
                    {"firm": "Acme", "source_ids": ["a", "b"], "reason": "Q2 review + outlook"}
                ],
                "ungrouped_firms": [{"firm": "Beta", "reason": "separate desks"}],
            }
        )
        groups, ungrouped = parse_scout_groups(response)
        self.assertEqual(1, len(groups))
        self.assertEqual(["a", "b"], groups[0]["source_ids"])
        self.assertEqual("Acme", groups[0]["firm"])
        self.assertEqual([{"firm": "Beta", "reason": "separate desks"}], ungrouped)

    def test_rejects_group_with_one_member(self) -> None:
        response = json.dumps({"groups": [{"firm": "Acme", "source_ids": ["a"]}]})
        with self.assertRaises(ValueError):
            parse_scout_groups(response)

    def test_defaults_ungrouped_to_empty(self) -> None:
        groups, ungrouped = parse_scout_groups(json.dumps({"groups": []}))
        self.assertEqual([], groups)
        self.assertEqual([], ungrouped)


class MultiSourceFirmsTest(unittest.TestCase):
    def test_filters_single_source_firms(self) -> None:
        sources = [
            _record("a", "Acme", "Review"),
            _record("b", "Acme", "Outlook"),
            _record("c", "Solo", "Lonely Outlook"),
        ]
        firms = multi_source_firms(sources)
        self.assertEqual(["Acme"], [firm for firm, _ in firms])
        self.assertEqual(2, len(firms[0][1]))


class ApplyGuardsTest(unittest.TestCase):
    def _firm_by_id(self) -> dict[str, str]:
        return {"a": "Acme", "b": "Acme", "c": "Acme", "x": "Other"}

    def test_drops_unknown_ids_with_warning(self) -> None:
        raw = [{"firm": "Acme", "source_ids": ["a", "b", "ghost"], "reason": "pair"}]
        accepted, warnings = _apply_guards(raw, {"a", "b", "c"}, self._firm_by_id())
        self.assertEqual(1, len(accepted))
        self.assertEqual(["a", "b"], accepted[0]["source_ids"])
        self.assertTrue(any("ghost" in w for w in warnings))

    def test_drops_overlapping_membership(self) -> None:
        raw = [
            {"firm": "Acme", "source_ids": ["a", "b"], "reason": "first"},
            {"firm": "Acme", "source_ids": ["b", "c"], "reason": "second"},
        ]
        accepted, warnings = _apply_guards(raw, {"a", "b", "c"}, self._firm_by_id())
        # b is consumed by the first group; the second collapses to a single new id.
        self.assertEqual(1, len(accepted))
        self.assertTrue(any("already grouped" in w for w in warnings))
        self.assertTrue(any("did not resolve to two" in w for w in warnings))

    def test_group_of_one_ignored_with_warning(self) -> None:
        raw = [{"firm": "Acme", "source_ids": ["a", "ghost"], "reason": "solo"}]
        accepted, warnings = _apply_guards(raw, {"a", "b", "c"}, self._firm_by_id())
        self.assertEqual([], accepted)
        self.assertTrue(any("did not resolve to two" in w for w in warnings))

    def test_cross_firm_group_dropped(self) -> None:
        raw = [{"firm": "Acme", "source_ids": ["a", "x"], "reason": "cross-firm"}]
        firm_by_id = {"a": "Acme", "x": "Other"}
        accepted, warnings = _apply_guards(raw, {"a", "x"}, firm_by_id)
        self.assertEqual([], accepted)
        self.assertTrue(any("multiple firms" in w for w in warnings))


class RunScoutTest(unittest.TestCase):
    def _sources(self) -> list[SourceRecord]:
        return [
            _record("acme-review", "Acme", "Q2 Markets Review", "01/04/2026"),
            _record("acme-outlook", "Acme", "Q2 Markets Outlook", "01/04/2026"),
            _record("beta-eq", "Beta", "Equity Outlook", "02/04/2026"),
            _record("beta-fi", "Beta", "Fixed Income Outlook", "02/04/2026"),
            _record("solo-macro", "Solo", "Macro View", "03/04/2026"),
        ]

    def test_no_multi_source_firms_early_exits_without_llm(self) -> None:
        called = {"n": 0}

        def runner(command, prompt):
            called["n"] += 1
            return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

        sources = [_record("a", "Acme", "Only Doc"), _record("b", "Beta", "Only Doc")]
        outcome = run_scout(sources, runner=runner)
        self.assertFalse(outcome.llm_invoked)
        self.assertEqual(0, called["n"])
        self.assertEqual(0, outcome.multi_source_firm_count)
        self.assertIn("<!-- scout:", outcome.notes_text)
        self.assertEqual([], outcome.accepted_groups)

    def test_groups_pair_and_leaves_other_firm_independent(self) -> None:
        response = json.dumps(
            {
                "groups": [
                    {
                        "firm": "Acme",
                        "source_ids": ["acme-review", "acme-outlook"],
                        "reason": "Q2 review paired with Q2 outlook",
                    }
                ],
                "ungrouped_firms": [
                    {"firm": "Beta", "reason": "equity vs fixed income — separate desks"}
                ],
            }
        )
        outcome = run_scout(self._sources(), runner=_runner_returning(response))
        self.assertEqual(1, len(outcome.accepted_groups))
        # Notes file: one line naming the firm and both exact titles, quoted.
        note_lines = [ln for ln in outcome.notes_text.splitlines() if ln.strip()]
        self.assertEqual(1, len(note_lines))
        line = note_lines[0]
        self.assertIn("Read the Acme", line)
        self.assertIn('"Q2 Markets Review"', line)
        self.assertIn('"Q2 Markets Outlook"', line)
        self.assertIn("one combined source", line)
        # Report explains both grouped and independent firms.
        self.assertIn("Beta", outcome.report_text)
        self.assertIn("separate desks", outcome.report_text)

    def test_llm_failure_degrades_to_empty_notes_and_warning(self) -> None:
        outcome = run_scout(self._sources(), runner=_failing_runner())
        self.assertTrue(outcome.llm_invoked)
        self.assertEqual([], outcome.accepted_groups)
        self.assertIn("<!-- scout:", outcome.notes_text)
        self.assertTrue(any("failed" in w for w in outcome.warnings))
        self.assertIn("Guard warnings", outcome.report_text)

    def test_unknown_id_from_llm_is_guarded(self) -> None:
        response = json.dumps(
            {
                "groups": [
                    {
                        "firm": "Acme",
                        "source_ids": ["acme-review", "acme-outlook", "phantom"],
                        "reason": "pair plus a hallucinated id",
                    }
                ],
                "ungrouped_firms": [],
            }
        )
        outcome = run_scout(self._sources(), runner=_runner_returning(response))
        self.assertEqual(1, len(outcome.accepted_groups))
        self.assertEqual(
            ["acme-review", "acme-outlook"], outcome.accepted_groups[0]["source_ids"]
        )
        self.assertTrue(any("phantom" in w for w in outcome.warnings))

    def test_empty_notes_when_llm_proposes_no_groups(self) -> None:
        response = json.dumps(
            {
                "groups": [],
                "ungrouped_firms": [
                    {"firm": "Acme", "reason": "no companion signal"},
                    {"firm": "Beta", "reason": "separate desks"},
                ],
            }
        )
        outcome = run_scout(self._sources(), runner=_runner_returning(response))
        self.assertEqual([], outcome.accepted_groups)
        self.assertIn("<!-- scout:", outcome.notes_text)
        self.assertTrue(outcome.llm_invoked)


class AliasCsvLoadingTest(unittest.TestCase):
    def test_scout_loads_alias_header_csv_and_groups_multi_source_firm(self) -> None:
        csv_text = (
            "Entity Name,Title,Published At,External link\n"
            "Acme,Q2 Markets Review,01/04/2026,https://example.test/a\n"
            "Acme,Q2 Markets Outlook,01/04/2026,https://example.test/b\n"
            "Solo,Macro View,03/04/2026,https://example.test/c\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sources.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            sources = load_pilot_sources(csv_path)

        firms = multi_source_firms(sources)
        self.assertEqual(["Acme"], [firm for firm, _ in firms])

        acme_ids = [r.source_id for _, records in firms for r in records]
        response = json.dumps(
            {
                "groups": [
                    {"firm": "Acme", "source_ids": acme_ids, "reason": "review + outlook"}
                ],
                "ungrouped_firms": [],
            }
        )
        outcome = run_scout(sources, runner=_runner_returning(response))
        self.assertEqual(1, len(outcome.accepted_groups))
        self.assertIn('"Q2 Markets Review"', outcome.notes_text)
        self.assertIn('"Q2 Markets Outlook"', outcome.notes_text)


if __name__ == "__main__":
    unittest.main()
