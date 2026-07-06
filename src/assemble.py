"""Assemble validated candidates into run-level output files."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from src.confidence import ConfidenceResult, score_candidate
from src.schemas import CandidateCall, SourceInfo
from src.taxonomy import Taxonomy


TARGET_OUTPUT_COLUMNS = (
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
)

OUTPUT_COLUMNS = TARGET_OUTPUT_COLUMNS + ("confidence", "band", "review_flag")

FAILURE_COLUMNS = (
    "reason_code",
    "message",
    "source_id",
    "chunk_id",
    "sub_asset_raw",
    "sub_asset_class",
    "view",
    "taxonomy_match",
    "evidence_kind",
    "locator",
    "reasoning",
)


@dataclass(frozen=True, slots=True)
class FailureRecord:
    reason_code: str
    message: str
    candidate: CandidateCall

    def to_row(self) -> dict[str, str]:
        return {
            "reason_code": self.reason_code,
            "message": self.message,
            "source_id": self.candidate.source_id,
            "chunk_id": self.candidate.chunk_id,
            "sub_asset_raw": self.candidate.sub_asset_raw,
            "sub_asset_class": self.candidate.sub_asset_class,
            "view": self.candidate.view,
            "taxonomy_match": self.candidate.taxonomy_match,
            "evidence_kind": self.candidate.evidence_kind,
            "locator": self.candidate.locator,
            "reasoning": self.candidate.reasoning,
        }


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    output_rows: list[dict[str, str]]
    failures: list[FailureRecord]
    candidate_count: int


def assemble_candidates(
    candidates: list[CandidateCall],
    *,
    sources: dict[str, SourceInfo],
    taxonomy: Taxonomy,
    snapshots: dict[tuple[str, str], str],
    page_counts: dict[str, int] | None = None,
) -> AssemblyResult:
    """page_counts maps source_id -> PDF page count (absent for HTML sources)."""
    scored: list[ConfidenceResult] = []
    failures: list[FailureRecord] = []

    for candidate in candidates:
        snapshot_text = snapshots.get((candidate.source_id, candidate.chunk_id), "")
        page_count = (page_counts or {}).get(candidate.source_id)
        try:
            scored.append(
                score_candidate(
                    candidate,
                    taxonomy=taxonomy,
                    snapshot_text=snapshot_text,
                    page_count=page_count,
                )
            )
        except ValueError as exc:
            failures.append(FailureRecord(str(exc), str(exc), candidate))

    output_rows: list[dict[str, str]] = []
    for group in _group_scored(scored).values():
        views = {item.candidate.view for item in group}
        if len(views) > 1:
            failures.extend(
                FailureRecord(
                    "unresolved_conflict",
                    "multiple views survived validation for the same source and leaf",
                    item.candidate,
                )
                for item in group
            )
            continue

        selected = max(group, key=lambda item: item.confidence)
        source = sources.get(selected.candidate.source_id)
        if source is None:
            failures.append(
                FailureRecord(
                    "source_metadata_missing",
                    "source metadata was not available for this candidate",
                    selected.candidate,
                )
            )
            continue
        output_rows.append(_output_row(selected, source, taxonomy))

    return AssemblyResult(
        output_rows=output_rows,
        failures=failures,
        candidate_count=len(candidates),
    )


def write_run_outputs(result: AssemblyResult, output_dir: str | Path) -> None:
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(run_dir / "output.csv", OUTPUT_COLUMNS, result.output_rows)
    _write_csv(
        run_dir / "failures.csv",
        FAILURE_COLUMNS,
        [failure.to_row() for failure in result.failures],
    )
    (run_dir / "manifest.md").write_text(_manifest_text(result), encoding="utf-8")


def _output_row(
    scored: ConfidenceResult,
    source: SourceInfo,
    taxonomy: Taxonomy,
) -> dict[str, str]:
    candidate = scored.candidate
    lookup = taxonomy.output_fields_for(candidate.sub_asset_class)
    return {
        "Firm": source.firm,
        "Date": source.date,
        "Source": source.source,
        "URL": source.url,
        "Sub-Asset Class": candidate.sub_asset_class,
        "Asset Class Category": lookup["Asset Class Category"],
        "Canva Groupings": lookup["Canva Groupings"],
        "Asset Class": lookup["Asset Class"],
        "View": candidate.view,
        "Full Commentary": _commentary(candidate),
        "confidence": str(scored.confidence),
        "band": scored.band,
        "review_flag": scored.review_flag,
    }


def _commentary(candidate: CandidateCall) -> str:
    return (
        f"{candidate.reasoning} Evidence: {candidate.evidence_quote}. "
        f"Locator: {candidate.locator}."
    )


def _group_scored(
    scored: list[ConfidenceResult],
) -> dict[tuple[str, str], list[ConfidenceResult]]:
    groups: dict[tuple[str, str], list[ConfidenceResult]] = {}
    for item in scored:
        key = (item.candidate.source_id, item.candidate.sub_asset_class)
        groups.setdefault(key, []).append(item)
    return groups


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _manifest_text(result: AssemblyResult) -> str:
    kept = len(result.output_rows)
    failed = len(result.failures)
    return "\n".join(
        [
            "# Run Manifest",
            "",
            f"- candidates: {result.candidate_count}",
            f"- kept: {kept}",
            f"- failed: {failed}",
            f"- count check: {'pass' if result.candidate_count == kept + failed else 'review'}",
            "",
        ]
    )
