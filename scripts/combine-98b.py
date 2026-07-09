"""Combine the ten 98-batch split runs into the single client deliverable.

Reads each split's output.csv / failures-client.csv / failures.csv (split 1
via its rescored deliverable dir; split 8's files already include the HSBC
rerun merge) and writes 98b-combined/ with:

- output.csv           — all kept calls, splits concatenated in order
- failures-client.csv  — all client failure rows, grouped by "What happened"
                         label and sorted most-important-first (the same
                         importance order src.assemble now writes per-run
                         files in); stable within a label, so split/source
                         order is preserved inside each group
- failures.csv         — internal, concatenated in split order, unsorted
- manifest.md          — per-split row counts and provenance

Run from the repo root: .venv/bin/python scripts/combine-98b.py
"""
from __future__ import annotations

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
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
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

    client_rows.sort(key=lambda row: LABEL_RANKS.get(row["What happened"], -1))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_rows(OUT_DIR / "output.csv", OUTPUT_COLUMNS, output_rows)
    write_rows(OUT_DIR / "failures-client.csv", CLIENT_FAILURE_COLUMNS, client_rows)
    write_rows(OUT_DIR / "failures.csv", FAILURE_COLUMNS, failure_rows)

    label_counts: dict[str, int] = {}
    for row in client_rows:
        label_counts[row["What happened"]] = label_counts.get(row["What happened"], 0) + 1

    lines = [
        "# 98-batch combined deliverable",
        "",
        "Combined from the ten split runs (split 1 via `98b-split1-rescored`;",
        "split 8 files already include the HSBC rerun merge). `output.csv` is",
        "concatenated in split order. `failures-client.csv` is grouped by the",
        "\"What happened\" label and sorted most-important-first (the canonical",
        "order in `src.assemble.CLIENT_FAILURE_LABELS`). `failures.csv` is the",
        "internal file, concatenated unsorted.",
        "",
        "| Split | Kept calls | Failure rows |",
        "|---|---|---|",
    ]
    for name, kept, failed in counts:
        lines.append(f"| {name} | {kept} | {failed} |")
    lines.append(f"| **Total** | **{len(output_rows)}** | **{len(client_rows)}** |")
    lines += ["", "## failures-client.csv label counts (in file order)", ""]
    for label, count in sorted(
        label_counts.items(), key=lambda item: LABEL_RANKS.get(item[0], -1)
    ):
        lines.append(f"- {label}: {count}")
    (OUT_DIR / "manifest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"wrote {OUT_DIR}: {len(output_rows)} output rows, {len(client_rows)} failure rows")
    for name, kept, failed in counts:
        print(f"  {name}: {kept} kept / {failed} failed")


if __name__ == "__main__":
    main()
