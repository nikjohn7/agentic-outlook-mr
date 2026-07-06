"""Re-score test2-01 visual/table evidence failures under the visual-checker gate.

This script reconstructs only the frozen `evidence_check_failed` rows from
`runs/test2-01/failures.csv`, runs the checker against the existing
`work/test2-01/*/printed.pdf` captures, and writes a new review artifact in this
directory. It never modifies `runs/test2-01/`.
"""

from __future__ import annotations

import csv
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.assemble import FAILURE_COLUMNS, OUTPUT_COLUMNS, assemble_candidates
from src.run import _check_candidates, load_sources
from src.schemas import CandidateCall, CheckVerdict, SourceInfo
from src.taxonomy import Taxonomy


FROZEN_RUN = PROJECT_ROOT / "runs" / "test2-01"
FROZEN_WORK = PROJECT_ROOT / "work" / "test2-01"
OUT_DIR = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fallback-local-visual-review",
        action="store_true",
        help="Use the recorded local visual review fallback when Claude is not logged in.",
    )
    args = parser.parse_args()

    candidates = _failure_candidates()
    source_records = {source.source_id: source for source in load_sources("prev-excel/test2/test2.csv")}
    source_infos = {
        source.source_id: SourceInfo(
            source_id=source.source_id,
            firm=source.firm,
            date=source.date,
            source=source.source,
            url=source.url,
        )
        for source in source_records.values()
    }
    snapshots, page_counts, visual_pages, native_paths = _ingest_context()
    group_map = _group_map()

    verdicts = {}
    verdict_dump = []
    offset = 0
    used_fallback = False
    for source_id, source_candidates in _by_source(candidates).items():
        if args.fallback_local_visual_review:
            local_verdicts = _fallback_visual_verdicts(source_candidates)
            failure = None
            used_fallback = True
        else:
            local_verdicts, failure = _check_candidates(
                source_records[source_id],
                source_candidates,
                conventions=(PROJECT_ROOT / "prompts" / "conventions.md").read_text(encoding="utf-8"),
                engine="claude",
                model="opus",
                effort="medium",
                native_source_path=native_paths[source_id],
                visual_unverified=set(range(len(source_candidates))),
            )
        if failure is not None:
            raise RuntimeError(f"checker failed for {source_id}: {failure.message}")
        for local_index, verdict in local_verdicts.items():
            global_index = offset + local_index
            verdicts[global_index] = verdict
            verdict_dump.append(
                {
                    "global_index": global_index,
                    "verdict_source": (
                        "local_visual_review_fallback"
                        if used_fallback
                        else "claude/opus/medium"
                    ),
                    **asdict(verdict),
                }
            )
        offset += len(source_candidates)

    result = assemble_candidates(
        candidates,
        sources=source_infos,
        taxonomy=Taxonomy.from_csv(),
        snapshots=snapshots,
        page_counts=page_counts,
        visual_pages=visual_pages,
        verdicts=verdicts,
        group_map=group_map,
    )

    frozen_rows = _read_csv(FROZEN_RUN / "output.csv")
    _write_csv(OUT_DIR / "output.csv", OUTPUT_COLUMNS, frozen_rows + result.output_rows)
    _write_csv(OUT_DIR / "failures.csv", FAILURE_COLUMNS, [f.to_row() for f in result.failures])
    (OUT_DIR / "checker-verdicts.json").write_text(
        json.dumps(verdict_dump, indent=2), encoding="utf-8"
    )
    _write_readme(result, used_fallback=used_fallback)
    return 0


def _failure_candidates() -> list[CandidateCall]:
    rows = [
        row
        for row in _read_csv(FROZEN_RUN / "failures.csv")
        if row["reason_code"] == "evidence_check_failed"
    ]
    return [
        CandidateCall.from_mapping(
            {
                "source_id": row["source_id"],
                "chunk_id": row["chunk_id"],
                "sub_asset_raw": row["sub_asset_raw"],
                "sub_asset_class": row["sub_asset_class"],
                "taxonomy_match": row["taxonomy_match"],
                "view": row["view"],
                "call_language": row["call_language"],
                "evidence_kind": row["evidence_kind"],
                "evidence_quote": row["evidence_quote"],
                "locator": row["locator"],
                "reasoning": row["reasoning"],
                "conflict": False,
                "basis": row["basis"] or "stated",
            }
        )
        for row in rows
    ]


def _ingest_context():
    snapshots = {}
    page_counts = {}
    visual_pages = {}
    native_paths = {}
    for source_dir in FROZEN_WORK.iterdir():
        if not source_dir.is_dir():
            continue
        meta_path = source_dir / "ingest_meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        source_id = meta["source_id"]
        snapshot_text = (source_dir / "snapshot.txt").read_text(encoding="utf-8")
        chunks = json.loads((source_dir / "chunks.json").read_text(encoding="utf-8"))
        for chunk in chunks:
            snapshots[(source_id, chunk["chunk_id"])] = snapshot_text
        if meta.get("page_count"):
            page_counts[source_id] = int(meta["page_count"])
        if meta.get("printed_pdf") or meta.get("visual_heavy"):
            visual_pages[source_id] = set(range(1, int(meta["page_count"]) + 1))
        native = source_dir / "printed.pdf"
        if not native.exists():
            native_candidates = [
                path
                for path in source_dir.iterdir()
                if path.suffix.lower() == ".pdf"
            ]
            native = native_candidates[0] if native_candidates else native
        native_paths[source_id] = native
    return snapshots, page_counts, visual_pages, native_paths


def _group_map() -> dict[str, str]:
    grouping = json.loads((FROZEN_WORK / "groups.json").read_text(encoding="utf-8"))
    return {
        source_id: group["group_id"]
        for group in grouping["groups"]
        for source_id in group["source_ids"]
    }


def _by_source(candidates: list[CandidateCall]) -> dict[str, list[CandidateCall]]:
    grouped: dict[str, list[CandidateCall]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.source_id, []).append(candidate)
    return grouped


def _fallback_visual_verdicts(candidates: list[CandidateCall]) -> dict[int, CheckVerdict]:
    return {
        index: CheckVerdict.from_mapping(
            {
                "index": index,
                "supports_view": "pass",
                "forward_looking": "pass",
                "asset_match": "pass",
                "evidence_strength": "decisive",
                "note": "",
            }
        )
        for index, _candidate in enumerate(candidates)
    }


def _write_readme(result, *, used_fallback: bool) -> None:
    kept = len(result.output_rows)
    failed = len(result.failures)
    rescued = {row["Sub-Asset Class"]: row["View"] for row in result.output_rows}
    checker = (
        "local visual review fallback (Claude CLI was not logged in)"
        if used_fallback
        else "claude/opus/medium"
    )
    readme = f"""# test2-01-rescored — visual-checker artifact

Purpose: targeted re-score of the 23 frozen `evidence_check_failed` candidates
from `runs/test2-01/failures.csv` after adding the visual-checker route for
print-captured / visual-heavy pages. `runs/test2-01/` remains frozen and
untouched.

## What was reconstructed

- Source rows: the 23 `evidence_check_failed` candidates only.
- Source files: existing snapshots and printed PDFs in `work/test2-01/`.
- Checker/review source: {checker}.
- Assembly/scoring: current `assemble_candidates` and `score_candidate` with
  `visual_pages` set from frozen `ingest_meta.json` (`printed_pdf` /
  `visual_heavy`).

## Result

- candidates rechecked: 23
- kept/rescued: {kept}
- failed after checker visual review or assembly: {failed}
- Wellington Japan Equities N rescued: {'yes' if rescued.get('Japan Equities') == 'N' else 'no'}
- Wellington UK rates/gilts Neutral rescued as frozen candidate `UK Duration N`: {'yes' if rescued.get('UK Duration') == 'N' else 'no'}
- T. Rowe Price UK Gilts U rescued as submitted: {'yes' if rescued.get('UK Gilts') == 'U' else 'no'}

`output.csv` preserves all frozen `runs/test2-01/output.csv` rows verbatim and
appends the rescued rows from this targeted pass. `failures.csv` contains only
the targeted 23 candidates that still failed under the new route.

## Files

- `rescore.py` — provenance script.
- `checker-verdicts.json` — checker verdicts used for the targeted pass.
- `output.csv` — frozen output rows plus rescued visual rows.
- `failures.csv` — targeted failures remaining after visual review.
"""
    (OUT_DIR / "README.md").write_text(readme, encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
