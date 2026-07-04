"""Assemble validated candidates into run-level output files."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.confidence import CHECKER_FAIL_REASONS, ConfidenceResult, score_candidate
from src.schemas import CandidateCall, CheckVerdict, SourceInfo
from src.taxonomy import Taxonomy


# Given the conflicting scored candidates for one (source, leaf), returns the
# winning index within the group (None = unresolved) and the reasoning.
Arbiter = Callable[[list[ConfidenceResult]], tuple[int | None, str]]


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
    "evidence_quote",
    "locator",
    "reasoning",
)


@dataclass(frozen=True, slots=True)
class FailureRecord:
    reason_code: str
    message: str
    source_id: str
    chunk_id: str
    sub_asset_raw: str = ""
    sub_asset_class: str = ""
    view: str = ""
    taxonomy_match: str = ""
    evidence_kind: str = ""
    # The quote the model actually submitted: the single field needed to
    # diagnose quote_not_found. Chunk-level failures (from_chunk) have no
    # candidate, so it stays "".
    evidence_quote: str = ""
    locator: str = ""
    reasoning: str = ""

    @classmethod
    def from_candidate(
        cls, reason_code: str, message: str, candidate: CandidateCall
    ) -> "FailureRecord":
        return cls(
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
        )

    @classmethod
    def from_chunk(
        cls, reason_code: str, message: str, source_id: str, chunk_id: str
    ) -> "FailureRecord":
        """A whole-chunk failure (no candidate survived to inspect)."""
        return cls(reason_code=reason_code, message=message, source_id=source_id, chunk_id=chunk_id)

    def to_row(self) -> dict[str, str]:
        return {
            "reason_code": self.reason_code,
            "message": self.message,
            "source_id": self.source_id,
            "chunk_id": self.chunk_id,
            "sub_asset_raw": self.sub_asset_raw,
            "sub_asset_class": self.sub_asset_class,
            "view": self.view,
            "taxonomy_match": self.taxonomy_match,
            "evidence_kind": self.evidence_kind,
            "evidence_quote": self.evidence_quote,
            "locator": self.locator,
            "reasoning": self.reasoning,
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
    verdicts: dict[int, CheckVerdict] | None = None,
    arbiter: Arbiter | None = None,
    group_map: dict[str, str] | None = None,
) -> AssemblyResult:
    """page_counts maps source_id -> PDF page count (absent for HTML sources).

    verdicts maps candidate list index -> checker verdict; None means the
    checker step is not in play (no caps), while a dict with a missing index
    means the checker ran but produced no verdict for that candidate (capped).
    arbiter resolves surviving view conflicts; without one they route to
    failures as before.
    group_map maps source_id -> group_id for analyst-grouped source sets:
    dedup/conflict then keys on the group, and grouped rows render the set as
    one pipe-joined source entity (the analysts' own output convention).
    """
    checker_enabled = verdicts is not None
    scored: list[ConfidenceResult] = []
    failures: list[FailureRecord] = []

    for index, candidate in enumerate(candidates):
        snapshot_text = snapshots.get((candidate.source_id, candidate.chunk_id), "")
        page_count = (page_counts or {}).get(candidate.source_id)
        verdict = (verdicts or {}).get(index)
        try:
            scored.append(
                score_candidate(
                    candidate,
                    taxonomy=taxonomy,
                    snapshot_text=snapshot_text,
                    page_count=page_count,
                    verdict=verdict,
                    checker_enabled=checker_enabled,
                )
            )
        except ValueError as exc:
            reason = str(exc)
            message = reason
            if verdict is not None and reason in CHECKER_FAIL_REASONS.values() and verdict.note:
                message = verdict.note
            failures.append(FailureRecord.from_candidate(reason, message, candidate))

    output_rows: list[dict[str, str]] = []
    for group in _group_scored(scored, group_map).values():
        views = {item.candidate.view for item in group}
        arbiter_note = ""
        corroboration = ""
        if len(views) > 1:
            winner, reasoning = _arbitrate(group, arbiter)
            if winner is None:
                message = "multiple views survived validation for the same source/group and leaf"
                if reasoning:
                    message += f"; arbiter: {reasoning}"
                failures.extend(
                    FailureRecord.from_candidate("unresolved_conflict", message, item.candidate)
                    for item in group
                )
                continue
            failures.extend(
                FailureRecord.from_candidate("arbitrated_out", reasoning, item.candidate)
                for item in group
                if item is not winner
            )
            selected = winner
            arbiter_note = reasoning
        else:
            selected = max(group, key=lambda item: item.confidence)
            corroborators: list[str] = []
            for item in group:
                if item is selected:
                    continue
                failures.append(
                    FailureRecord.from_candidate(
                        "duplicate_same_view",
                        f"same view already kept from {selected.candidate.locator}",
                        item.candidate,
                    )
                )
                if item.candidate.source_id != selected.candidate.source_id:
                    dup_source = sources.get(item.candidate.source_id)
                    title = dup_source.source if dup_source else item.candidate.source_id
                    corroborators.append(f"{title} ({item.candidate.locator})")
            if corroborators:
                corroboration = (
                    f"Corroborated by companion source: {'; '.join(corroborators)}."
                )

        source = sources.get(selected.candidate.source_id)
        if source is None:
            failures.append(
                FailureRecord.from_candidate(
                    "source_metadata_missing",
                    "source metadata was not available for this candidate",
                    selected.candidate,
                )
            )
            continue
        display_source, member_count = _display_source(
            selected.candidate.source_id, sources, group_map
        )
        output_rows.append(
            _output_row(
                selected,
                display_source,
                taxonomy,
                arbiter_note=arbiter_note,
                locator_source=source.source if member_count > 1 else "",
                corroboration=corroboration,
            )
        )

    return AssemblyResult(
        output_rows=output_rows,
        failures=failures,
        candidate_count=len(candidates),
    )


def _arbitrate(
    group: list[ConfidenceResult],
    arbiter: Arbiter | None,
) -> tuple[ConfidenceResult | None, str]:
    if arbiter is None:
        return None, ""
    winning_index, reasoning = arbiter(group)
    if winning_index is None or not 0 <= winning_index < len(group):
        return None, reasoning
    return group[winning_index], reasoning


def write_run_outputs(
    result: AssemblyResult,
    output_dir: str | Path,
    *,
    source_summaries: list[dict[str, object]] | None = None,
    chunk_failures: list[FailureRecord] | None = None,
    run_config: dict[str, object] | None = None,
    grouping: dict[str, object] | None = None,
) -> None:
    """Write the run's three review files.

    chunk_failures are whole-chunk failures (e.g. unparseable model output) that
    produced no candidate; they are recorded in failures.csv and counted
    separately in the manifest so the candidate reconciliation stays exact.
    run_config (engine/model/effort) is recorded in the manifest so a frozen
    run states exactly what produced it. grouping is the resolved group-notes
    plan (groups + warnings) so the manifest shows exactly what was combined.
    """
    chunk_failures = chunk_failures or []
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(run_dir / "output.csv", OUTPUT_COLUMNS, result.output_rows)
    _write_csv(
        run_dir / "failures.csv",
        FAILURE_COLUMNS,
        [failure.to_row() for failure in [*result.failures, *chunk_failures]],
    )
    manifest = _manifest_text(result, source_summaries or [], chunk_failures, run_config, grouping)
    (run_dir / "manifest.md").write_text(manifest, encoding="utf-8")


def _display_source(
    source_id: str,
    sources: dict[str, SourceInfo],
    group_map: dict[str, str] | None,
) -> tuple[SourceInfo, int]:
    """Workbook display identity for a row: a grouped set renders as ONE
    pipe-joined source entity (the analysts' own convention for combined
    review+outlook pairs). Returns the display info and the member count."""
    group_id = (group_map or {}).get(source_id)
    if group_id is None:
        return sources[source_id], 1
    members = [
        info for sid, info in sources.items() if (group_map or {}).get(sid) == group_id
    ]
    if len(members) <= 1:
        return sources[source_id], 1
    return (
        SourceInfo(
            source_id=group_id,
            firm=members[0].firm,
            date=" | ".join(member.date for member in members),
            source=" | ".join(member.source for member in members),
            url=" | ".join(member.url for member in members),
        ),
        len(members),
    )


def _output_row(
    scored: ConfidenceResult,
    source: SourceInfo,
    taxonomy: Taxonomy,
    *,
    arbiter_note: str = "",
    locator_source: str = "",
    corroboration: str = "",
) -> dict[str, str]:
    candidate = scored.candidate
    lookup = taxonomy.output_fields_for(candidate.sub_asset_class)
    commentary = _commentary(candidate, locator_source=locator_source)
    if corroboration:
        commentary += f" {corroboration}"
    if scored.checker_status == "unclear":
        note = f" ({scored.checker_note})" if scored.checker_note else ""
        commentary += f" Checker: unconfirmed{note}."
    elif scored.checker_status == "missing":
        commentary += " Checker: not run."
    review_flag = scored.review_flag
    if arbiter_note:
        commentary += f" Arbiter: {arbiter_note}"
        if review_flag == "none":
            review_flag = "review"
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
        "Full Commentary": commentary,
        "confidence": str(scored.confidence),
        "band": scored.band,
        "review_flag": review_flag,
    }


def _commentary(candidate: CandidateCall, *, locator_source: str = "") -> str:
    where = f"{candidate.locator} ({locator_source})" if locator_source else candidate.locator
    return (
        f"{candidate.reasoning} Evidence: {candidate.evidence_quote}. "
        f"Locator: {where}."
    )


def _group_scored(
    scored: list[ConfidenceResult],
    group_map: dict[str, str] | None = None,
) -> dict[tuple[str, str], list[ConfidenceResult]]:
    groups: dict[tuple[str, str], list[ConfidenceResult]] = {}
    for item in scored:
        source_id = item.candidate.source_id
        key = (
            (group_map or {}).get(source_id, source_id),
            item.candidate.sub_asset_class,
        )
        groups.setdefault(key, []).append(item)
    return groups


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _manifest_text(
    result: AssemblyResult,
    source_summaries: list[dict[str, object]],
    chunk_failures: list[FailureRecord],
    run_config: dict[str, object] | None = None,
    grouping: dict[str, object] | None = None,
) -> str:
    kept = len(result.output_rows)
    failed = len(result.failures)
    lines = ["# Run Manifest", ""]
    if run_config:
        lines.append("## Run configuration")
        lines.extend(f"- {key}: {value}" for key, value in run_config.items() if value is not None)
        lines.append("")
    if grouping:
        lines.append("## Grouping")
        lines.append(f"- group notes: {grouping.get('notes_path')}")
        for group in grouping.get("groups", []):
            note = f" — note: {group['note']}" if group.get("note") else ""
            lines.append(f"- {group['group_id']}: {', '.join(group['source_ids'])}{note}")
        for warning in grouping.get("warnings", []):
            lines.append(f"- warning: {warning}")
        lines.append("")
    lines += [
        "## Candidate reconciliation",
        f"- candidates: {result.candidate_count}",
        f"- kept: {kept}",
        f"- failed: {failed}",
        f"- count check: {'pass' if result.candidate_count == kept + failed else 'review'}",
        f"- chunk failures (no candidate): {len(chunk_failures)}",
        "",
    ]
    reason_counts = Counter(
        failure.reason_code for failure in [*result.failures, *chunk_failures]
    )
    if reason_counts:
        lines.append("## Failure reasons")
        lines.extend(
            f"- {reason}: {count}"
            for reason, count in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        lines.append("")
    if source_summaries:
        lines.append("## Sources processed")
        for summary in source_summaries:
            flag_text = " [visual_heavy]" if summary.get("visual_heavy") else ""
            if summary.get("printed_pdf"):
                flag_text += " [printed-to-pdf]"
            pages = summary.get("page_count")
            chunk_count = summary.get("chunk_count", 0)
            size = f"{pages}p / {chunk_count} chunks" if pages else f"{chunk_count} chunks"
            lines.append(
                f"- {summary.get('source_id')} ({summary.get('source_type')}, {size}): "
                f"{summary.get('candidates', 0)} candidates emitted{flag_text}"
            )
        lines.append("")
    return "\n".join(lines)
