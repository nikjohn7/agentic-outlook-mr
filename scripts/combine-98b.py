"""Combine the ten 98-batch split runs into the single client deliverable.

The deliverable pipeline is: combine splits -> apply the Phase-1 date patch ->
firm-reconcile -> final 98b-combined/ files. Per-split outputs stay frozen (the
pre-run scout and in-run grouping are the reading strategy; reconcile is the
output strategy layered on top).

Reads each split's output.csv / failures-client.csv / failures.csv (split 1 via
its rescored deliverable dir; split 8's files already include the HSBC rerun
merge) and writes 98b-combined/ with:

- output.csv              — the RECONCILED master (same shape as a run output)
- output.pre-reconcile.csv — the plain concatenation, before date patch/reconcile
- output.dated.csv        — the concatenation with the Phase-1 date patch applied
- reconcile-audit.csv     — per-row reconcile audit (dual-confidence trail)
- reconcile-summary.md    — reconcile per-action counts + needs_human list
- failures-client.csv     — split client rows + reconcile failure rows, grouped
                            by "What happened" label, most-important-first
- failures.csv            — internal, split rows + reconcile rows, unsorted
- manifest.md             — per-split row counts + reconcile provenance

Run from the repo root:
    .venv/bin/python scripts/combine-98b.py [--engine claude --model opus --effort medium] [--no-llm]
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.assemble import (  # noqa: E402
    CLIENT_FAILURE_COLUMNS,
    CLIENT_FAILURE_LABELS,
    FAILURE_COLUMNS,
    OUTPUT_COLUMNS,
)
from src.datefill import apply_patch  # noqa: E402
from src.reconcile import run_reconcile, write_outputs  # noqa: E402

BATCH_DIR = Path("client-runs/runs-07072026-98rows")
SPLIT_DIRS = [
    "98b-split1-rescored",  # split 1 deliverable is the rescored dir
    "98b-split2",
    "98b-split3",
    "98b-split4",
    "98b-split5",
    "98b-split6",
    "98b-split7",
    "98b-split8",  # current files already include the HSBC rerun merge
    "98b-split9",
    "98b-split10",
]
OUT_DIR = BATCH_DIR / "98b-combined"
DATE_PATCH = BATCH_DIR / "datefill" / "datefill.csv"

# "What happened" label -> importance rank (position of the first reason code
# carrying that label in CLIENT_FAILURE_LABELS, whose dict order is the
# canonical most-important-first order). Labels not in the registry (e.g. a raw
# fallback code) sort to the very top, same as src.assemble.client_failure_rank.
LABEL_RANKS: dict[str, int] = {}
for rank, (code, (label, _)) in enumerate(CLIENT_FAILURE_LABELS.items()):
    LABEL_RANKS.setdefault(label, rank)


def read_rows(path: Path, expected_columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != expected_columns:
            raise SystemExit(f"{path}: unexpected columns {reader.fieldnames}")
        return list(reader)


def write_rows(
    path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine + date-patch + reconcile the 98-batch splits.")
    parser.add_argument("--engine", default="claude", help="reconcile scope-gate engine (default: claude)")
    parser.add_argument("--model", default="opus", help="reconcile scope-gate model (default: opus)")
    parser.add_argument("--effort", default="medium", help="reconcile scope-gate effort (default: medium)")
    parser.add_argument("--no-llm", action="store_true", help="skip the scope gate; every multi-row key degrades to needs_human")
    args = parser.parse_args()

    output_rows: list[dict[str, str]] = []
    client_rows: list[dict[str, str]] = []
    failure_rows: list[dict[str, str]] = []
    counts: list[tuple[str, int, int]] = []

    for name in SPLIT_DIRS:
        split_dir = BATCH_DIR / name
        outputs = read_rows(split_dir / "output.csv", OUTPUT_COLUMNS)
        client = read_rows(split_dir / "failures-client.csv", CLIENT_FAILURE_COLUMNS)
        failures = read_rows(split_dir / "failures.csv", FAILURE_COLUMNS)
        output_rows.extend(outputs)
        client_rows.extend(client)
        failure_rows.extend(failures)
        counts.append((name, len(outputs), len(client)))

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: the plain concatenation (frozen splits, in split order).
    pre_reconcile = OUT_DIR / "output.pre-reconcile.csv"
    write_rows(pre_reconcile, OUTPUT_COLUMNS, output_rows)

    # Step 2: apply the Phase-1 date patch (a NEW sibling; the concatenation is
    # left intact). Reconcile's recency rule reads these dates.
    dated = OUT_DIR / "output.dated.csv"
    date_changed = 0
    if DATE_PATCH.is_file():
        date_changed = apply_patch(pre_reconcile, DATE_PATCH, dated)
        reconcile_input = dated
    else:
        print(f"note: no date patch at {DATE_PATCH}; reconciling the undated concatenation")
        reconcile_input = pre_reconcile

    # Step 3: firm-reconcile over the date-patched combined output.
    result = run_reconcile(
        [reconcile_input],
        engine=args.engine,
        model=args.model,
        effort=args.effort,
        use_llm=not args.no_llm,
    )
    # write_outputs writes output.csv (the reconciled master), reconcile-audit.csv,
    # reconcile-summary.md, and reconcile-failures-client.csv into OUT_DIR.
    write_outputs(result, OUT_DIR, [reconcile_input])

    # Step 4: fold the reconcile failure rows into the run's failure files.
    failure_rows.extend(failure.internal_row() for failure in result.failures)
    client_rows.extend(failure.client_row() for failure in result.failures)
    client_rows.sort(key=lambda row: LABEL_RANKS.get(row["What happened"], -1))

    write_rows(OUT_DIR / "failures-client.csv", CLIENT_FAILURE_COLUMNS, client_rows)
    write_rows(OUT_DIR / "failures.csv", FAILURE_COLUMNS, failure_rows)

    label_counts: dict[str, int] = {}
    for row in client_rows:
        label_counts[row["What happened"]] = label_counts.get(row["What happened"], 0) + 1

    lines = [
        "# 98-batch combined deliverable",
        "",
        "Pipeline: combine the ten split runs (split 1 via `98b-split1-rescored`;",
        "split 8 files already include the HSBC rerun merge) -> apply the Phase-1",
        "date patch -> firm-reconcile. `output.csv` is the RECONCILED master;",
        "`output.pre-reconcile.csv` and `output.dated.csv` are the intermediate",
        "concatenation and its date-patched form. `failures-client.csv` is grouped",
        'by the "What happened" label and sorted most-important-first (the canonical',
        "order in `src.assemble.CLIENT_FAILURE_LABELS`), and now also carries the",
        "reconcile failure rows (merged / superseded). `failures.csv` is the internal",
        "file, concatenated unsorted.",
        "",
        f"- Date patch: {'applied, ' + str(date_changed) + ' Date cells changed' if DATE_PATCH.is_file() else 'not applied (no patch found)'}",
        f"- Reconcile: {result.input_row_count} -> {result.output_row_count} rows; "
        f"{result.multi_row_key_count} multi-row keys "
        f"({result.same_view_key_count} same-view, {result.conflicting_key_count} conflicting); "
        f"{len(result.failures)} reconcile failure rows",
        "",
        "| Split | Kept calls | Failure rows |",
        "|---|---|---|",
    ]
    for name, kept, failed in counts:
        lines.append(f"| {name} | {kept} | {failed} |")
    lines.append(
        f"| **Concatenated** | **{len(output_rows)}** | — |"
    )
    lines.append(
        f"| **Reconciled** | **{result.output_row_count}** | **{len(client_rows)}** |"
    )
    lines += ["", "## failures-client.csv label counts (in file order)", ""]
    for label, count in sorted(
        label_counts.items(), key=lambda item: LABEL_RANKS.get(item[0], -1)
    ):
        lines.append(f"- {label}: {count}")
    (OUT_DIR / "manifest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        f"wrote {OUT_DIR}: concatenated {len(output_rows)} -> reconciled "
        f"{result.output_row_count} output rows, {len(client_rows)} failure rows"
    )
    for name, kept, failed in counts:
        print(f"  {name}: {kept} kept / {failed} failed")


if __name__ == "__main__":
    main()
