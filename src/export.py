"""Client export: turn any output.csv into the client-facing files.

This is the last step before anything is sent, and it is deterministic (no
model calls), so it can be re-run over the July master as easily as over a new
run. It exists so the problems the client reported can never reach them again:

- the four structured commentary columns replace `Full Commentary` (parsed
  from the blob for legacy rows, taken directly from new rows);
- internal diagnostics (`basis`, `checker_strength`, `call_language`,
  `quote_match`) are dropped — they confused readers and one was undocumented;
- `char:` locators (HTML / transcript chunk offsets) are blank, keeping any
  figure name that followed them;
- **one row per firm and asset**: rows that share a (firm, sub-asset class)
  are resolved the way reconcile resolves them — same view → merged
  (citation-preserving), conflicting views → the precedence ladder (newest →
  stated beats inferred → confidence) — with the loser listed in the failures
  file and named on the kept row; only a tie keeps both rows, flagged;
- **review flags mean a judgment**: a confidence cap (inferred basis, degraded
  quote match, forecast delta, thin checker) is carried by `band`, not by the
  flag, so `review_flag = review` is the analyst's real queue;
- the files are written as BOM-prefixed UTF-8 with single-line cells, plus a
  native .xlsx twin (see src/csvio.py).

Usage:
    python -m src.export --output <output.csv> --out-dir <dir>
        [--failures <failures-client.csv>] [--nearleaf-audit <reconcile-nearleaf-audit.csv>]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from src.commentary import append_note, commentary_from_row, row_columns
from src.csvio import read_csv, write_csv_and_xlsx

CLIENT_COLUMNS = (
    "Firm",
    "Date",
    "Source",
    "URL",
    "Sub-Asset Class",
    "Asset Class Category",
    "Canva Groupings",
    "Asset Class",
    "View",
    "Reasoning",
    "Verbatim Evidence",
    "Locator",
    "Reviewer Notes",
    "confidence",
    "band",
    "review_flag",
)

CLIENT_FAILURE_COLUMNS = (
    "Firm",
    "Source",
    "Sub-Asset Class",
    "View (proposed)",
    "What happened",
    "Explanation",
    "Evidence / notes",
)

SHARED_KEY_MARKER = "Also held by this firm:"

# A row is a review item when its notes record a judgment made or dodged.
# Everything else the pipeline appends (caps, evidence-check detail, call
# language) is information the band already carries.
JUDGMENT_MARKERS = (
    "Arbiter:",
    "Sibling consistency:",
    "Implied-call challenge",
    "Checker: unconfirmed",
    "Checker: not run",
    "Shared evidence: this passage equally supports",
    "could not be ranked",
    SHARED_KEY_MARKER,
    "Merged from related assets:",
    "a human should decide",
)


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    """A copy of the row with the structured columns present and current
    (rendered from the row's commentary) and reader-facing locators."""
    out = dict(row)
    out.update(row_columns(commentary_from_row(row)))
    return out


def _leaf_key(label: str) -> str:
    return " ".join((label or "").split()).lower()


def _notes(row: dict[str, str]) -> str:
    return row.get("Reviewer Notes") or row.get("Full Commentary") or ""


def recompute_review_flags(rows: list[dict[str, str]]) -> int:
    """Apply the review-flag policy to rows produced under the old one (every
    Medium row was flagged). Low score → strong_review; UNCERTAIN view or a
    judgment marker in the notes → review; otherwise none. Returns the number
    of rows whose flag changed."""
    changed = 0
    for row in rows:
        try:
            score = int((row.get("confidence") or "").strip())
        except ValueError:
            score = None
        notes = _notes(row)
        if score is not None and score < 50:
            flag = "strong_review"
        elif (row.get("View") or "").strip() == "UNCERTAIN" or any(m in notes for m in JUDGMENT_MARKERS):
            flag = "review"
        else:
            flag = "none"
        if (row.get("review_flag") or "none") != flag:
            row["review_flag"] = flag
            changed += 1
    return changed


def annotate_nearleaf_winners(rows: list[dict[str, str]], audit_csv: str | Path) -> int:
    """Near-leaf merges made before 2026-10 flagged their survivor without a
    note. Read the stage's audit file and write the note those rows now carry
    natively, so the flag is explained (and survives recompute). Returns the
    number of rows annotated."""
    from src.eval import normalize_firm

    _, audit = read_csv(audit_csv)
    folded: dict[tuple[str, str], list[str]] = {}
    for entry in audit:
        action = (entry.get("mapping_action") or "").strip()
        canonical = (entry.get("canonical_leaf") or "").strip()
        if action not in {"merged", "superseded"} or not canonical:
            continue
        key = (normalize_firm(entry.get("Firm", "")), _leaf_key(canonical))
        leaf = (entry.get("original_leaf") or "").strip()
        if leaf and leaf not in folded.setdefault(key, []):
            folded[key].append(leaf)
    annotated = 0
    for row in rows:
        key = (normalize_firm(row.get("Firm", "")), _leaf_key(row.get("Sub-Asset Class", "")))
        leaves = folded.get(key)
        if not leaves or "Merged from related assets:" in _notes(row):
            continue
        append_note(
            row,
            "Merged from related assets: "
            + ", ".join(f"'{leaf}'" for leaf in leaves)
            + f" (read as one call on '{row.get('Sub-Asset Class', '')}'; review recommended).",
        )
        annotated += 1
    return annotated


def resolve_shared_keys(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """One row per (firm, sub-asset class), resolved exactly as reconcile does:
    same view → merged, conflicting → precedence ladder with the loser(s) sent
    to the failures file; no unique winner → every row kept and flagged with a
    note naming the others. Rows already carrying the shared-key note are left
    alone (idempotent). Returns (rows, client failure rows)."""
    from src import reconcile
    from src.assemble import client_failure_label
    from src.eval import normalize_firm

    groups: dict[tuple[str, str], list[int]] = {}
    for index, row in enumerate(rows):
        key = (normalize_firm(row.get("Firm", "")), _leaf_key(row.get("Sub-Asset Class", "")))
        groups.setdefault(key, []).append(index)

    emitted: dict[int, dict[str, str] | None] = {}
    failures: list[dict[str, str]] = []
    for key, indexes in groups.items():
        if len(indexes) < 2 or any(SHARED_KEY_MARKER in _notes(rows[i]) for i in indexes):
            continue
        members = [reconcile.row_from_raw(rows[i], "export", i) for i in indexes]
        views = {m.view for m in members}
        if len(views) == 1:
            merged, winner, losers = reconcile.merge_same_view(members)
            append_note(
                merged,
                "Also held by this firm: the same view in "
                + "; ".join(f"'{r.source_title}'" + (f" ({r.date})" if r.date else "") for r in losers)
                + "; merged to keep one row per asset.",
            )
            emitted[winner.index] = merged
            code = reconcile.REASON_MERGED
            for loser in losers:
                emitted[loser.index] = None
                failures.append(_failure_row(loser, code, (
                    f"same {loser.view} view on '{loser.raw.get('Sub-Asset Class', '')}' merged into the "
                    f"kept row from {winner.source_title}; wording preserved in the merged row"
                )))
            continue
        winner, rule, detail = reconcile.resolve_conflict(members)
        if winner is None:
            for member in members:
                out = dict(member.raw)
                others = "; ".join(
                    f"{m.view} in '{m.source_title}'" + (f" ({m.date})" if m.date else "")
                    for m in members if m is not member
                )
                append_note(out, f"Also held by this firm: {others} on the same asset; no rule picks one, a human should decide.")
                out["review_flag"] = "review"
                emitted[member.index] = out
            continue
        out = dict(winner.raw)
        losers = [m for m in members if m is not winner]
        append_note(
            out,
            "Also held by this firm: "
            + "; ".join(f"{m.view} in '{m.source_title}'" + (f" ({m.date})" if m.date else "") for m in losers)
            + f" on the same asset; this row kept by {rule} ({detail}), the other view is listed in the failures file.",
        )
        out["review_flag"] = "review"
        emitted[winner.index] = out
        for loser in losers:
            emitted[loser.index] = None
            failures.append(_failure_row(loser, reconcile.REASON_SUPERSEDED, (
                f"{loser.view} superseded by {winner.source_title}'s {winner.view} ({rule}: {detail})"
            )))

    resolved: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        if index in emitted:
            if emitted[index] is not None:
                resolved.append(emitted[index])  # type: ignore[arg-type]
        else:
            resolved.append(row)
    return resolved, failures


def _failure_row(member, code: str, notes: str) -> dict[str, str]:
    from src.assemble import client_failure_label

    what_happened, explanation = client_failure_label(code)
    return {
        "Firm": member.firm,
        "Source": member.source_title,
        "Sub-Asset Class": member.raw.get("Sub-Asset Class", ""),
        "View (proposed)": member.view,
        "What happened": what_happened,
        "Explanation": explanation,
        "Evidence / notes": notes,
    }


def _label_rank() -> dict[str, int]:
    from src.assemble import CLIENT_FAILURE_LABELS

    ranks: dict[str, int] = {}
    for rank, (label, _) in enumerate(CLIENT_FAILURE_LABELS.values()):
        ranks.setdefault(label, rank)
    return ranks


def merge_failure_rows(existing: list[dict[str, str]], added: list[dict[str, str]]) -> list[dict[str, str]]:
    """Append new client failure rows in label-block order (stable within a
    label), matching the pipeline's importance sort."""
    ranks = _label_rank()
    rows = [*existing, *added]
    return sorted(rows, key=lambda r: ranks.get(r.get("What happened", ""), -1))


def client_rows(
    rows: list[dict[str, str]], *, nearleaf_audit: str | Path | None = None
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, int]]:
    """Normalize, annotate, resolve shared keys, recompute flags, and project
    onto CLIENT_COLUMNS. Returns (client rows, new failure rows, stats)."""
    normalized = [normalize_row(row) for row in rows]
    annotated = annotate_nearleaf_winners(normalized, nearleaf_audit) if nearleaf_audit else 0
    resolved, failures = resolve_shared_keys(normalized)
    resolved = [normalize_row(row) for row in resolved]  # re-render merged rows' columns
    reflagged = recompute_review_flags(resolved)
    stats = Counter(
        rows_in=len(normalized),
        rows_out=len(resolved),
        shared_key_rows_removed=len(normalized) - len(resolved),
        shared_key_failure_rows=len(failures),
        nearleaf_rows_annotated=annotated,
        review_flags_changed=reflagged,
        review_rows=sum(1 for r in resolved if r.get("review_flag") in {"review", "strong_review"}),
        blank_locators=sum(1 for r in resolved if not r.get("Locator")),
        merged_rows=sum(1 for r in resolved if "||||" in (r.get("Verbatim Evidence") or "")),
    )
    projected = [{column: row.get(column, "") for column in CLIENT_COLUMNS} for row in resolved]
    return projected, failures, dict(stats)


def write_client_files(
    out_dir: str | Path,
    output_rows: list[dict[str, str]],
    failure_rows: list[dict[str, str]] | None = None,
    *,
    stem: str = "output-client",
    nearleaf_audit: str | Path | None = None,
) -> dict[str, object]:
    """Write the client files from in-memory rows (used by every stage that
    writes an output.csv, so the client twin always exists next to it). New
    failure rows produced by resolving shared keys are folded into the
    failures file when one is written."""
    projected, new_failures, stats = client_rows(output_rows, nearleaf_audit=nearleaf_audit)
    out = Path(out_dir)
    csv_path, xlsx_path = write_csv_and_xlsx(out / f"{stem}.csv", CLIENT_COLUMNS, projected)
    report: dict[str, object] = {**stats, "output_csv": str(csv_path), "output_xlsx": str(xlsx_path)}
    if failure_rows is not None:
        merged = merge_failure_rows(failure_rows, new_failures)
        f_csv, f_xlsx = write_csv_and_xlsx(out / "failures-client.csv", CLIENT_FAILURE_COLUMNS, merged)
        report.update(failure_rows=len(merged), failures_csv=str(f_csv), failures_xlsx=str(f_xlsx))
    return report


def export(
    output_csv: str | Path,
    out_dir: str | Path,
    *,
    failures_csv: str | Path | None = None,
    nearleaf_audit: str | Path | None = None,
    stem: str = "output-client",
) -> dict[str, object]:
    """Write `<out_dir>/<stem>.csv` + `.xlsx` (and the failures file as CSV +
    XLSX when given). Returns a small report."""
    _, rows = read_csv(output_csv)
    failure_rows = read_csv(failures_csv)[1] if failures_csv is not None else None
    report = write_client_files(out_dir, rows, failure_rows, stem=stem, nearleaf_audit=nearleaf_audit)
    (Path(out_dir) / "export-report.md").write_text(render_report(report), encoding="utf-8")
    return report


def render_report(report: dict[str, object]) -> str:
    lines = ["# Client export report", ""]
    for key, value in report.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.export",
        description="Write the client-facing CSV + XLSX from an output.csv (deterministic).",
    )
    parser.add_argument("--output", required=True, type=Path, help="output.csv (any version)")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--failures", type=Path, help="failures-client.csv to re-write alongside (BOM + xlsx), with shared-key losers folded in")
    parser.add_argument("--nearleaf-audit", type=Path, help="reconcile-nearleaf-audit.csv of a pre-2026-10 reconcile, to annotate its near-leaf survivors")
    parser.add_argument("--stem", default="output-client", help="file stem for the client output (default output-client)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = export(
        args.output, args.out_dir, failures_csv=args.failures, nearleaf_audit=args.nearleaf_audit, stem=args.stem
    )
    print(render_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
