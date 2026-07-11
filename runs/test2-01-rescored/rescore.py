"""Re-score test2-01 visual/table evidence failures under the visual-checker gate.

This script reconstructs only the frozen `evidence_check_failed` rows from
`runs/test2-01/failures.csv`, re-verifies them under the visual-checker route,
and writes a new review artifact in this directory. It never modifies
`runs/test2-01/`.

Verdicts: the default mode REPLAYS the 23 saved `claude/opus/medium` verdicts
from `checker-verdicts.json` (zero LLM calls). The live checker path is kept
behind `--live` for provenance but is not exercised by the committed artifact.

Collision policy (frozen-wins): the rescued dial/graphic candidates re-emit
leaves the frozen run already kept from each firm's paired document. A rescued
row whose (firm, leaf) join key — built exactly the way `src/eval.py` joins —
matches a frozen kept row is NOT appended to `output.csv`; it is recorded in
`failures.csv` as a duplicate (the frozen clean-text row wins, the same outcome
a single assembly pass with group-level dedup would have produced). Only rescued
rows on genuinely new leaves are appended. This keeps `output.csv` free of
duplicate join keys so `src.eval` consumes it directly.
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

from src.assemble import FAILURE_COLUMNS, OUTPUT_COLUMNS, FailureRecord, assemble_candidates
from src.eval import normalize_firm
from src.run import _check_candidates, load_sources
from src.schemas import CandidateCall, CheckVerdict, SourceInfo
from src.taxonomy import Taxonomy


FROZEN_RUN = PROJECT_ROOT / "runs" / "test2-01"
FROZEN_WORK = PROJECT_ROOT / "work" / "test2-01"
OUT_DIR = Path(__file__).resolve().parent
VERDICTS_PATH = OUT_DIR / "checker-verdicts.json"

# The saved verdicts must all come from the real opus checker pass. Replay mode
# asserts this and aborts loudly on anything else, so a fallback/local dump can
# never masquerade as the committed opus verdicts.
REQUIRED_VERDICT_SOURCE = "claude/opus/medium"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live",
        action="store_true",
        help="Call the real claude/opus/medium checker instead of replaying the "
        "saved verdicts. Kept for provenance; the committed artifact uses replay.",
    )
    mode.add_argument(
        "--fallback-local-visual-review",
        action="store_true",
        help="Use the recorded local visual review fallback when Claude is not logged in.",
    )
    args = parser.parse_args(argv)

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

    live = args.live or args.fallback_local_visual_review
    if live:
        verdicts, verdict_dump, used_fallback = _live_verdicts(
            args, candidates, source_records, native_paths
        )
    else:
        verdicts = _replay_verdicts(candidates)
        verdict_dump = None
        used_fallback = False

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
    output_rows, collision_failures = _apply_frozen_wins(
        frozen_rows, result.output_rows, candidates, source_infos
    )

    # failures.csv = the rescued rows the frozen run already owns (frozen-wins) +
    # the genuine assembly failures produced by this pass (the T. Rowe Price GBP
    # duplicate_same_view). The assembly failures are kept verbatim so the
    # existing genuine failure is never lost.
    all_failures = collision_failures + list(result.failures)

    _write_csv(OUT_DIR / "output.csv", OUTPUT_COLUMNS, output_rows)
    _write_csv(OUT_DIR / "failures.csv", FAILURE_COLUMNS, [f.to_row() for f in all_failures])
    # In replay mode the verdicts file is the INPUT provenance record — leave it
    # byte-for-byte untouched. Only a live/fallback pass rewrites it.
    if verdict_dump is not None:
        VERDICTS_PATH.write_text(json.dumps(verdict_dump, indent=2), encoding="utf-8")
    _write_readme(
        frozen_rows,
        output_rows,
        result,
        collision_failures,
        replayed=not live,
        used_fallback=used_fallback,
    )
    return 0


def _replay_verdicts(candidates: list[CandidateCall]) -> dict[int, CheckVerdict]:
    """Load the saved opus verdicts and map them back to the candidate indices by
    the same `global_index` scheme the file records — reproducing exactly the
    verdicts dict the original live pass fed to `assemble_candidates`. Asserts
    every entry is the real claude/opus/medium pass and aborts otherwise. Makes
    zero LLM calls."""
    if not VERDICTS_PATH.exists():
        raise RuntimeError(f"replay mode requires saved verdicts at {VERDICTS_PATH}")
    entries = json.loads(VERDICTS_PATH.read_text(encoding="utf-8"))

    verdicts: dict[int, CheckVerdict] = {}
    for entry in entries:
        source = entry.get("verdict_source")
        if source != REQUIRED_VERDICT_SOURCE:
            raise RuntimeError(
                "replay aborted: checker-verdicts.json entry with global_index "
                f"{entry.get('global_index')!r} has verdict_source {source!r}, "
                f"expected {REQUIRED_VERDICT_SOURCE!r}. Refusing to ship a non-opus "
                "verdict as the committed pass."
            )
        global_index = entry.get("global_index")
        if not isinstance(global_index, int) or isinstance(global_index, bool):
            raise RuntimeError(
                f"replay aborted: entry has non-integer global_index {global_index!r}"
            )
        if global_index in verdicts:
            raise RuntimeError(
                f"replay aborted: duplicate global_index {global_index} in verdicts file"
            )
        verdicts[global_index] = CheckVerdict.from_mapping(entry)

    expected = set(range(len(candidates)))
    if set(verdicts) != expected:
        raise RuntimeError(
            "replay aborted: verdict indices "
            f"{sorted(verdicts)} do not cover the {len(candidates)} candidates "
            f"{sorted(expected)}"
        )
    return verdicts


def _live_verdicts(
    args: argparse.Namespace,
    candidates: list[CandidateCall],
    source_records: dict,
    native_paths: dict,
) -> tuple[dict[int, CheckVerdict], list[dict], bool]:
    """Provenance path: run the real checker (or the recorded local fallback) and
    build both the verdicts dict and the JSON dump. Not exercised by the
    committed artifact — the default replay path is used instead."""
    verdicts: dict[int, CheckVerdict] = {}
    verdict_dump: list[dict] = []
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
                        else REQUIRED_VERDICT_SOURCE
                    ),
                    **asdict(verdict),
                }
            )
        offset += len(source_candidates)
    return verdicts, verdict_dump, used_fallback


def _collision_key(row: dict[str, str]) -> tuple[str, str]:
    """The (firm, leaf) join key, built exactly the way `src/eval.py` joins rows:
    firm folded through `normalize_firm`, leaf stripped. Using eval's own
    `normalize_firm` (imported, not reimplemented) guarantees "no duplicate keys"
    means the same thing to the harness that consumes this artifact."""
    return (normalize_firm(row.get("Firm", "")), (row.get("Sub-Asset Class") or "").strip())


def _candidate_lookup(
    candidates: list[CandidateCall], source_infos: dict[str, SourceInfo]
) -> dict[tuple[str, str, str], CandidateCall]:
    """Map (firm_key, leaf, view) -> the candidate that produced it, so a
    frozen-wins failure row can carry the full source story (source_id, evidence
    quote, locator, reasoning) rather than just firm/leaf/view."""
    lookup: dict[tuple[str, str, str], CandidateCall] = {}
    for candidate in candidates:
        source = source_infos.get(candidate.source_id)
        firm = source.firm if source else candidate.source_id
        key = (normalize_firm(firm), candidate.sub_asset_class.strip(), candidate.view)
        lookup.setdefault(key, candidate)
    return lookup


def _apply_frozen_wins(
    frozen_rows: list[dict[str, str]],
    rescued_rows: list[dict[str, str]],
    candidates: list[CandidateCall],
    source_infos: dict[str, SourceInfo],
) -> tuple[list[dict[str, str]], list[FailureRecord]]:
    """Frozen-wins collision dedupe.

    A rescued row whose (firm, leaf) key matches a frozen kept row is dropped
    from output and recorded as a duplicate failure (frozen row wins). A rescued
    row on a new leaf is appended. Returns (output_rows, collision_failures)."""
    frozen_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in frozen_rows:
        frozen_by_key[_collision_key(row)] = row

    lookup = _candidate_lookup(candidates, source_infos)

    kept_new: list[dict[str, str]] = []
    collision_failures: list[FailureRecord] = []
    for row in rescued_rows:
        key = _collision_key(row)
        frozen_row = frozen_by_key.get(key)
        if frozen_row is None:
            kept_new.append(row)
            continue
        candidate = lookup.get((key[0], key[1], row.get("View", "")))
        collision_failures.append(_collision_failure(row, frozen_row, candidate))

    output_rows = list(frozen_rows) + kept_new
    return output_rows, collision_failures


def _collision_failure(
    row: dict[str, str],
    frozen_row: dict[str, str],
    candidate: CandidateCall | None,
) -> FailureRecord:
    """Build a diagnosable frozen-wins failure record that preserves the story.

    Same-view collisions use the pipeline's `duplicate_same_view` code. The
    conflicting-view collisions (frozen O, rescued reduced to N) get their own
    `duplicate_conflicting_view` code and a message naming both calls, so an
    analyst can find the known reduce/neutralize dial-read defect directly."""
    frozen_view = (frozen_row.get("View") or "").strip()
    rescued_view = (row.get("View") or "").strip()
    leaf = (row.get("Sub-Asset Class") or "").strip()
    same_view = frozen_view == rescued_view
    if same_view:
        reason_code = "duplicate_same_view"
        message = (
            f"visual checker verified (decisive), but the frozen run already kept "
            f"{frozen_view} on '{leaf}' from the paired document — frozen row wins"
        )
    else:
        reason_code = "duplicate_conflicting_view"
        message = (
            f"visual checker verified {rescued_view}, but the frozen run already kept "
            f"the opposite call {frozen_view} on '{leaf}' from the paired document — "
            f"frozen row wins (known reduce/neutralize dial-read defect; the rescued "
            f"{rescued_view} view is not shipped)"
        )
    if candidate is not None:
        return FailureRecord(
            reason_code=reason_code,
            message=message,
            source_id=candidate.source_id,
            chunk_id=candidate.chunk_id,
            sub_asset_raw=candidate.sub_asset_raw,
            sub_asset_class=candidate.sub_asset_class,
            view=candidate.view,
            taxonomy_match=candidate.taxonomy_match,
            evidence_kind=candidate.evidence_kind,
            evidence_quote=candidate.evidence_quote,
            locator=candidate.locator,
            reasoning=candidate.reasoning,
            basis=row.get("basis", "") or candidate.basis,
            checker_strength=row.get("checker_strength", ""),
            call_language=row.get("call_language", ""),
        )
    # Fallback: no candidate mapped (should not happen for the 23 rescued rows);
    # still record a faithful failure from the rendered row.
    return FailureRecord(
        reason_code=reason_code,
        message=message,
        source_id="",
        chunk_id="",
        sub_asset_class=leaf,
        view=rescued_view,
        taxonomy_match="exact",
        basis=row.get("basis", ""),
        checker_strength=row.get("checker_strength", ""),
        call_language=row.get("call_language", ""),
    )


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


def _write_readme(
    frozen_rows: list[dict[str, str]],
    output_rows: list[dict[str, str]],
    result,
    collision_failures: list[FailureRecord],
    *,
    replayed: bool,
    used_fallback: bool,
) -> None:
    rechecked = result.candidate_count
    verified = len(result.output_rows)
    assembly_failed = len(result.failures)
    net_new = len(output_rows) - len(frozen_rows)
    same_view = sum(1 for f in collision_failures if f.reason_code == "duplicate_same_view")
    conflicting = sum(
        1 for f in collision_failures if f.reason_code == "duplicate_conflicting_view"
    )
    total_failures = len(collision_failures) + assembly_failed
    kept_leaves = {row["Sub-Asset Class"]: row["View"] for row in output_rows}
    new_leaves = {
        row["Sub-Asset Class"]: row["View"] for row in output_rows[len(frozen_rows):]
    }
    if replayed:
        checker = f"{REQUIRED_VERDICT_SOURCE} (verdicts REPLAYED from checker-verdicts.json, not re-run)"
    elif used_fallback:
        checker = "local visual review fallback (Claude CLI was not logged in)"
    else:
        checker = REQUIRED_VERDICT_SOURCE

    readme = f"""# test2-01-rescored — visual-checker artifact

Purpose: targeted re-score of the {rechecked} frozen `evidence_check_failed`
candidates from `runs/test2-01/failures.csv` after adding the visual-checker
route for print-captured / visual-heavy pages. `runs/test2-01/` remains frozen
and untouched.

## What was reconstructed

- Source rows: the {rechecked} `evidence_check_failed` candidates only.
- Source files: existing snapshots and printed PDFs in `work/test2-01/`.
- Checker/review source: {checker}.
- Assembly/scoring: current `assemble_candidates` and `score_candidate` with
  `visual_pages` set from frozen `ingest_meta.json` (`printed_pdf` /
  `visual_heavy`).

## Collision policy (frozen-wins)

The rescued dial/graphic candidates re-emit leaves the frozen run already kept
from each firm's paired document. Every rescued row is joined against the frozen
kept rows on the SAME key `src/eval.py` uses — `normalize_firm(Firm)` + stripped
`Sub-Asset Class` leaf. On a collision the frozen clean-text row wins (the
outcome a single assembly pass with group-level dedup would have produced), and
the rescued row is recorded in `failures.csv` instead of being appended:

- `duplicate_same_view` — the rescued view agrees with the frozen row.
- `duplicate_conflicting_view` — the rescued view disagrees (frozen O, rescued
  reduced to N): the known reduce/neutralize dial-read defect, kept out of
  `output.csv` and flagged distinctly so an analyst can find all such pairs.

Only rescued rows on genuinely new leaves are appended. This leaves `output.csv`
free of duplicate join keys, so `src.eval` consumes the artifact directly.

## Result

- candidates rechecked: {rechecked}
- verified by checker + assembly: {verified}
- net-new leaves kept (appended to the frozen output): {net_new}
- recorded as duplicates (frozen-wins): {len(collision_failures)}
  ({same_view} same-view + {conflicting} conflicting-view)
- assembly failures preserved (pre-existing): {assembly_failed}
- `failures.csv` rows: {total_failures}
- `output.csv` rows: {len(output_rows)} (= {len(frozen_rows)} frozen + {net_new} net-new)

Net-new leaves appended: {', '.join(f"{leaf} {view}" for leaf, view in new_leaves.items()) or 'none'}.

- Wellington Japan Equities N kept as net-new: {'yes' if new_leaves.get('Japan Equities') == 'N' else 'no'}
- Wellington UK Duration N kept as net-new: {'yes' if new_leaves.get('UK Duration') == 'N' else 'no'}
- T. Rowe Price UK Gilts U: frozen row wins (rescued duplicate, same view): {'yes' if kept_leaves.get('UK Gilts') == 'U' else 'no'}

`output.csv` preserves all frozen `runs/test2-01/output.csv` rows verbatim and
appends only the net-new rescued leaves. `failures.csv` records the frozen-wins
duplicates (with the full story per row) plus the genuine assembly failures.

## Files

- `rescore.py` — provenance script (default replay mode; `--live` re-runs the checker).
- `checker-verdicts.json` — the {rechecked} saved opus verdicts, replayed here.
- `output.csv` — frozen output rows plus net-new rescued leaves ({len(output_rows)} rows).
- `failures.csv` — frozen-wins duplicates plus preserved assembly failures.
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
