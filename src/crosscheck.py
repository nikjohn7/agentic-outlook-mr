"""Bare-bones post-run firm cross-check across one or more run outputs.

`crosscheck.py` is a standalone REPORT generator, run AFTER runs are frozen. It
is layer 3 of the grouping design (scout companions before a run -> in-run
assembly/arbitration resolves grouped overlap -> post-run cross-check catches
everything left: ungrouped same-firm sources, and same-firm sources split
across runs by the 20-items-per-run cap). It NEVER modifies any run output.

    .venv/bin/python -m src.crosscheck \\
        --outputs runs/a/output.csv runs/b/output.csv \\
        --out-dir tmp/crosscheck [--engine claude --model sonnet --effort medium]

What it does:

1. Load every row from every given `output.csv`, tagging each with its source
   file. The join key is `src.eval`'s normalized firm + normalized leaf — the
   SAME normalization the ground-truth harness and `runs/test2-01-rescored`
   use, imported (never reimplemented) so "same call" means one thing
   everywhere.
2. Bucket each (firm, leaf) key: a single row is not reported; two-or-more rows
   with the same `View` are `duplicate_same_view` (auto-resolved, no LLM); two-
   or-more rows with differing `View`s are `conflicting_views`.
3. `conflicting_views` groups get ONE batched categorical review pass (default a
   light Claude tier). Same-view duplicates never touch the LLM. A failed review
   pass degrades gracefully — every conflict group falls back to `needs_human`.
4. Write `crosscheck.csv` (one row per reported key, full provenance) and
   `crosscheck-summary.md` (counts + needs-human list + scope disclaimer).

Exact firm+leaf matches only. Near-leaf / fuzzy matching and the full
dual-confidence firm-reconcile stage are deliberately out of scope — v1.2
backlog items 6 and 1 in `ROADMAP.md`.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src import llm
# Import the join normalization from src.eval rather than reimplementing it, so
# a "duplicate key" here means exactly what it means to src.eval and to the
# runs/test2-01-rescored frozen-wins dedupe (precedent: rescore.py). Do not
# invent a new normalization.
from src.eval import _leaf_key, normalize_firm

CROSSCHECK_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "crosscheck_conflicts.md"

# Buckets, reusing the assemble.py failure-reason word where the meaning matches
# (a set of same-view rows on one key is a duplicate_same_view, exactly as the
# in-run assembler names its cross-doc same-view dedups).
BUCKET_SAME_VIEW = "duplicate_same_view"
BUCKET_CONFLICT = "conflicting_views"

# The three categorical verdicts the review pass may return (no scores, ever).
VERDICT_SAME_CALL = "same_call"
VERDICT_SUPERSEDED = "superseded"
VERDICT_NEEDS_HUMAN = "needs_human"
VALID_VERDICTS = frozenset({VERDICT_SAME_CALL, VERDICT_SUPERSEDED, VERDICT_NEEDS_HUMAN})

# Verdicts that require a human even after the review pass. A failed/skipped pass
# also resolves here (see conflict_verdicts).
_NEEDS_HUMAN_VERDICTS = frozenset({VERDICT_NEEDS_HUMAN})

Runner = Callable[[list[str], str], object]


class CrossCheckError(RuntimeError):
    """A fatal problem loading the run outputs."""


# --------------------------------------------------------------------------- #
# Row loading
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Row:
    """One output.csv row, tagged with the file it came from."""

    source_file: str
    firm: str
    firm_key: str
    leaf: str
    view: str
    date: str
    source_title: str
    confidence: str
    band: str
    commentary: str
    index: int  # 0-based data-row index within its source file


_REQUIRED_COLUMNS = frozenset({"Firm", "Sub-Asset Class", "View"})


def load_rows(paths: list[Path]) -> list[Row]:
    """Load every row from every output.csv, tagging each with its source file.

    Firm and leaf are folded through the imported `src.eval` normalization so the
    join key is identical to the eval harness's."""
    rows: list[Row] = []
    for path in paths:
        rows.extend(_read_rows(path))
    return rows


def _read_rows(path: Path) -> list[Row]:
    if not path.is_file():
        raise CrossCheckError(f"output not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise CrossCheckError(f"CSV is empty: {path}")
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise CrossCheckError(
                f"{path} missing required columns: {', '.join(sorted(missing))}"
            )
        tag = str(path)
        rows: list[Row] = []
        for index, raw in enumerate(reader):
            firm = (raw.get("Firm") or "").strip()
            rows.append(
                Row(
                    source_file=tag,
                    firm=firm,
                    firm_key=normalize_firm(firm),
                    leaf=_leaf_key(raw.get("Sub-Asset Class") or ""),
                    view=(raw.get("View") or "").strip(),
                    date=(raw.get("Date") or "").strip(),
                    source_title=(raw.get("Source") or "").strip(),
                    confidence=(raw.get("confidence") or "").strip(),
                    band=(raw.get("band") or "").strip(),
                    commentary=(raw.get("Full Commentary") or "").strip(),
                    index=index,
                )
            )
        return rows


# --------------------------------------------------------------------------- #
# Bucketing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Group:
    """A reported (firm, leaf) key with its overlapping rows and bucket."""

    firm_key: str
    firm: str
    leaf: str
    bucket: str
    rows: tuple[Row, ...]


def bucket_rows(rows: list[Row]) -> list[Group]:
    """Group rows on (firm_key, leaf); report only keys with >= 2 rows.

    Groups and their member rows are sorted deterministically (never dict order)
    so the outputs are byte-stable across invocations on the same inputs."""
    by_key: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        by_key.setdefault((row.firm_key, row.leaf), []).append(row)

    groups: list[Group] = []
    for (firm_key, leaf), members in by_key.items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=_row_sort_key)
        views = {row.view for row in ordered}
        bucket = BUCKET_SAME_VIEW if len(views) == 1 else BUCKET_CONFLICT
        groups.append(
            Group(
                firm_key=firm_key,
                firm=ordered[0].firm,
                leaf=leaf,
                bucket=bucket,
                rows=tuple(ordered),
            )
        )
    groups.sort(key=lambda g: (g.firm_key, g.leaf))
    return groups


def _row_sort_key(row: Row) -> tuple[str, int]:
    return (row.source_file, row.index)


# --------------------------------------------------------------------------- #
# Task 2 — one batched categorical review pass over conflicting groups
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: str
    note: str


_REVIEW_FAILED_NOTE = "review pass failed; escalated to human"
_NO_LLM_NOTE = "LLM review skipped (--no-llm); escalated to human"


def parse_crosscheck(raw_response: str) -> dict[int, Verdict]:
    """Parse the review pass response: {"groups": [{group_id, verdict, note}]}.

    Raises on any contract violation so `llm.call_parsed`'s repair-retry loop can
    re-prompt. Returns {group_id: Verdict}."""
    payload = json.loads(llm._extract_json(raw_response))
    if not isinstance(payload, dict):
        raise ValueError("crosscheck response must be a JSON object")
    groups_raw = payload.get("groups")
    if not isinstance(groups_raw, list):
        raise ValueError("crosscheck response must include a groups list")
    verdicts: dict[int, Verdict] = {}
    for item in groups_raw:
        if not isinstance(item, dict):
            raise ValueError("each group must be a JSON object")
        group_id = item.get("group_id")
        if not isinstance(group_id, int) or isinstance(group_id, bool):
            raise ValueError("each group needs an integer group_id")
        verdict = item.get("verdict")
        if verdict not in VALID_VERDICTS:
            raise ValueError(
                f"verdict must be one of {', '.join(sorted(VALID_VERDICTS))}; got {verdict!r}"
            )
        note = item.get("note", "")
        if not isinstance(note, str):
            raise ValueError("group note must be a string")
        verdicts[group_id] = Verdict(verdict=verdict, note=note.strip())
    return verdicts


def conflict_verdicts(
    conflicts: list[Group],
    *,
    engine: str,
    model: str | None,
    effort: str | None,
    runner: Runner | None = None,
    use_llm: bool = True,
) -> dict[int, Verdict]:
    """One batched review pass over the conflicting groups.

    Returns {index-into-conflicts: Verdict}. With `use_llm=False`, or when the
    single batched call fails for any reason, every conflict group degrades to a
    `needs_human` verdict — never a crash. The group_id sent to the model is the
    conflict's position in this list, so the mapping back is exact."""
    if not conflicts:
        return {}
    if not use_llm:
        return {i: Verdict(VERDICT_NEEDS_HUMAN, _NO_LLM_NOTE) for i in range(len(conflicts))}

    inputs = {
        "groups": [
            {
                "group_id": i,
                "firm": group.firm,
                "sub_asset_leaf": group.leaf,
                "rows": [
                    {
                        "view": row.view,
                        "source_title": row.source_title,
                        "date": row.date,
                        "full_commentary": row.commentary,
                    }
                    for row in group.rows
                ],
            }
            for i, group in enumerate(conflicts)
        ]
    }
    try:
        result = llm.call_parsed(
            CROSSCHECK_PROMPT,
            inputs,
            engine=engine,
            model=model,
            effort=effort,
            runner=runner,
            parser=parse_crosscheck,
        )
    except Exception:  # noqa: BLE001 — any failure degrades to needs_human, never a crash
        return {i: Verdict(VERDICT_NEEDS_HUMAN, _REVIEW_FAILED_NOTE) for i in range(len(conflicts))}

    payload: dict[int, Verdict] = result.payload
    # A verdict missing for any conflict group also degrades to needs_human, so
    # every conflict is always accounted for.
    return {
        i: payload.get(i, Verdict(VERDICT_NEEDS_HUMAN, _REVIEW_FAILED_NOTE))
        for i in range(len(conflicts))
    }


# --------------------------------------------------------------------------- #
# Task 3 — reported records + output rendering
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ReportedKey:
    """One reported (firm, leaf) key, with its bucket, verdict, and flag."""

    firm: str
    leaf: str
    bucket: str
    rows: tuple[Row, ...]
    verdict: str = ""  # empty for same-view rows
    note: str = ""
    needs_human: bool = False


def build_report(groups: list[Group], verdicts: dict[int, Verdict]) -> list[ReportedKey]:
    """Attach each conflicting group's verdict (by its position among conflicts);
    same-view groups carry no verdict. Ordering follows `bucket_rows` (already
    sorted), so the report is byte-stable."""
    conflicts = [g for g in groups if g.bucket == BUCKET_CONFLICT]
    verdict_by_group_id: dict[int, Verdict] = {
        id(conflicts[i]): verdict for i, verdict in verdicts.items() if i < len(conflicts)
    }

    reported: list[ReportedKey] = []
    for group in groups:
        if group.bucket == BUCKET_CONFLICT:
            verdict = verdict_by_group_id.get(
                id(group), Verdict(VERDICT_NEEDS_HUMAN, _REVIEW_FAILED_NOTE)
            )
            reported.append(
                ReportedKey(
                    firm=group.firm,
                    leaf=group.leaf,
                    bucket=group.bucket,
                    rows=group.rows,
                    verdict=verdict.verdict,
                    note=verdict.note,
                    needs_human=verdict.verdict in _NEEDS_HUMAN_VERDICTS,
                )
            )
        else:
            reported.append(
                ReportedKey(
                    firm=group.firm,
                    leaf=group.leaf,
                    bucket=group.bucket,
                    rows=group.rows,
                    verdict="",
                    note="",
                    needs_human=False,
                )
            )
    return reported


CROSSCHECK_COLUMNS = (
    "Firm",
    "Sub-Asset Class",
    "views",
    "run_files",
    "source_titles",
    "dates",
    "confidence_bands",
    "bucket",
    "agent_verdict",
    "note",
    "needs_human",
)


def _pipe(values: list[str]) -> str:
    return " | ".join(values)


def _crosscheck_row(key: ReportedKey) -> dict[str, str]:
    return {
        "Firm": key.firm,
        "Sub-Asset Class": key.leaf,
        "views": _pipe([row.view for row in key.rows]),
        "run_files": _pipe([row.source_file for row in key.rows]),
        "source_titles": _pipe([row.source_title for row in key.rows]),
        "dates": _pipe([row.date for row in key.rows]),
        "confidence_bands": _pipe(
            [f"{row.confidence or '—'}/{row.band or '—'}" for row in key.rows]
        ),
        "bucket": key.bucket,
        "agent_verdict": key.verdict,
        "note": key.note,
        "needs_human": "true" if key.needs_human else "false",
    }


def render_summary(reported: list[ReportedKey], paths: list[Path]) -> str:
    bucket_counts = Counter(key.bucket for key in reported)
    firm_counts = Counter(key.firm for key in reported)
    needs_human = [key for key in reported if key.needs_human]

    lines: list[str] = []
    lines.append("# Firm cross-check summary")
    lines.append("")
    lines.append(
        "Post-run same-firm overlap report (layer 3 of the grouping design). "
        "Deterministic join on `src.eval`-normalized firm + sub-asset leaf across "
        "the given run outputs; same-view overlaps auto-resolved, conflicting "
        "views reviewed by one categorical agent pass. Run outputs are read-only "
        "and were not modified."
    )
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    for path in paths:
        lines.append(f"- {path}")
    lines.append("")
    lines.append("## Reported keys per bucket")
    lines.append("")
    lines.append(f"- `{BUCKET_SAME_VIEW}`: {bucket_counts.get(BUCKET_SAME_VIEW, 0)}")
    lines.append(f"- `{BUCKET_CONFLICT}`: {bucket_counts.get(BUCKET_CONFLICT, 0)}")
    lines.append(f"- total reported: {len(reported)}")
    lines.append("")
    lines.append("## Reported keys per firm")
    lines.append("")
    if firm_counts:
        for firm in sorted(firm_counts):
            lines.append(f"- {firm}: {firm_counts[firm]}")
    else:
        lines.append("_None — no same-firm same-leaf overlap found._")
    lines.append("")
    lines.append("## Needs-human groups")
    lines.append("")
    if needs_human:
        for key in needs_human:
            note = f" — {key.note}" if key.note else ""
            lines.append(f"- {key.firm} / {key.leaf} (views: {_pipe([r.view for r in key.rows])}){note}")
    else:
        lines.append("_None._")
    lines.append("")
    lines.append("## Scope — what this tool does NOT do")
    lines.append("")
    lines.append(
        "- **No fuzzy / near-leaf matching.** Joins on the exact normalized "
        "firm+leaf only; adjacent leaves (e.g. \"US Duration\" vs \"US "
        "Treasuries\") are NOT merged. That is v1.2 backlog item 6 in `ROADMAP.md`."
    )
    lines.append(
        "- **No dual-confidence reconcile.** It never re-scores, never picks a "
        "surviving row, and never writes a reconciled master file. The full "
        "firm-reconcile stage with a dual-confidence audit trail is v1.2 backlog "
        "item 1 in `ROADMAP.md`, which supersedes this bare-bones cross-check."
    )
    lines.append(
        "- **It never modifies run outputs.** This is a purely additive report."
    )
    lines.append("")
    return "\n".join(lines)


def write_outputs(reported: list[ReportedKey], out_dir: Path, paths: list[Path]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "crosscheck.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CROSSCHECK_COLUMNS)
        writer.writeheader()
        writer.writerows(_crosscheck_row(key) for key in reported)

    summary_path = out_dir / "crosscheck-summary.md"
    summary_path.write_text(render_summary(reported, paths), encoding="utf-8")
    return {"crosscheck": csv_path, "summary": summary_path}


# --------------------------------------------------------------------------- #
# Orchestration + CLI
# --------------------------------------------------------------------------- #


@dataclass
class CrossCheckResult:
    reported: list[ReportedKey]
    groups: list[Group]


def run_crosscheck(
    paths: list[Path],
    *,
    engine: str = "claude",
    model: str | None = None,
    effort: str | None = "medium",
    runner: Runner | None = None,
    use_llm: bool = True,
) -> CrossCheckResult:
    """Load -> bucket -> review conflicts -> build report. Writes nothing."""
    rows = load_rows(paths)
    groups = bucket_rows(rows)
    conflicts = [g for g in groups if g.bucket == BUCKET_CONFLICT]
    verdicts = conflict_verdicts(
        conflicts,
        engine=engine,
        model=model,
        effort=effort,
        runner=runner,
        use_llm=use_llm,
    )
    reported = build_report(groups, verdicts)
    return CrossCheckResult(reported=reported, groups=groups)


def _resolve_model(engine: str, model: str | None) -> str | None:
    """claude requires an explicit model; supply the default when omitted.
    codex passes the model through unchanged — None → the adapter's default codex
    model, a named codex model validated downstream (never silently dropped)."""
    if engine == "claude" and model is None:
        return "sonnet"
    return model


def build_parser() -> argparse.ArgumentParser:
    """The crosscheck CLI parser. Extracted so the no-flags conflict-pass defaults
    (the model matrix) are testable."""
    parser = argparse.ArgumentParser(
        prog="python -m src.crosscheck",
        description="Post-run same-firm overlap cross-check across run outputs.",
    )
    parser.add_argument(
        "--outputs",
        required=True,
        nargs="+",
        type=Path,
        help="one or more frozen run output.csv files (read-only)",
    )
    parser.add_argument("--out-dir", required=True, type=Path, help="directory for the report files")
    parser.add_argument("--engine", default="claude", help="LLM engine for the conflict pass (default: claude)")
    parser.add_argument(
        "--model",
        default=None,
        help="model for the conflict pass (claude default: sonnet; codex: allowlist member, default gpt-5.5)",
    )
    parser.add_argument("--effort", default="medium", help="reasoning effort (default: medium)")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="skip the review pass; every conflict falls back to needs_human",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    model = _resolve_model(args.engine, args.model)
    result = run_crosscheck(
        args.outputs,
        engine=args.engine,
        model=model,
        effort=args.effort,
        use_llm=not args.no_llm,
    )
    written = write_outputs(result.reported, args.out_dir, args.outputs)

    same_view = sum(1 for k in result.reported if k.bucket == BUCKET_SAME_VIEW)
    conflicts = sum(1 for k in result.reported if k.bucket == BUCKET_CONFLICT)
    needs_human = sum(1 for k in result.reported if k.needs_human)
    print(
        f"cross-check: {len(result.reported)} reported keys "
        f"({same_view} {BUCKET_SAME_VIEW}, {conflicts} {BUCKET_CONFLICT}), "
        f"{needs_human} needs_human"
    )
    for label, path in written.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
