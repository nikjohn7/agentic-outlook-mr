"""Assemble validated candidates into run-level output files."""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.confidence import (
    CHECKER_FAIL_REASONS,
    ConfidenceResult,
    effective_call_language,
    normalize_quote_text,
    score_candidate,
)
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

# `basis`, `checker_strength`, and `call_language` are exposed after the
# internal-review columns so an analyst can filter stated vs.
# forecast_delta/inferred calls, High-but-adequate checker confirmations, and
# the effective call-language grade at a glance. `call_language` is the value
# actually scored (after the explicit_dial-on-prose downgrade).
OUTPUT_COLUMNS = TARGET_OUTPUT_COLUMNS + (
    "confidence",
    "band",
    "review_flag",
    "basis",
    "checker_strength",
    "call_language",
)

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
    "basis",
    "checker_strength",
    "call_language",
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
    basis: str = ""
    checker_strength: str = ""
    # The effective call-language bucket (after the explicit_dial-on-prose
    # downgrade), so a failed row carries the same grade a kept row would.
    call_language: str = ""

    @classmethod
    def from_candidate(
        cls,
        reason_code: str,
        message: str,
        candidate: CandidateCall,
        checker_strength: str = "",
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
            basis=candidate.basis,
            checker_strength=checker_strength,
            call_language=effective_call_language(candidate)[0],
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
            "basis": self.basis,
            "checker_strength": self.checker_strength,
            "call_language": self.call_language,
        }


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    output_rows: list[dict[str, str]]
    failures: list[FailureRecord]
    candidate_count: int


@dataclass(frozen=True, slots=True)
class _Selected:
    """One (source/group, leaf) winner that survived scoring, same-view dedup,
    and conflict arbitration — carried to the cross-leaf dedup pass and then
    rendered. Bundles the display context computed during grouping."""

    scored: ConfidenceResult
    source: SourceInfo
    display_source: SourceInfo
    member_count: int
    arbiter_note: str = ""
    corroboration: str = ""


def assemble_candidates(
    candidates: list[CandidateCall],
    *,
    sources: dict[str, SourceInfo],
    taxonomy: Taxonomy,
    snapshots: dict[tuple[str, str], str],
    page_counts: dict[str, int] | None = None,
    scrambled_pages: dict[str, set[int]] | None = None,
    visual_pages: dict[str, set[int]] | None = None,
    verdicts: dict[int, CheckVerdict] | None = None,
    arbiter: Arbiter | None = None,
    group_map: dict[str, str] | None = None,
) -> AssemblyResult:
    """page_counts maps source_id -> PDF page count (absent for HTML sources).

    scrambled_pages maps source_id -> the set of column-interleaved page numbers
    (see src/ingest.detect_scrambled_page); a prose call citing one of them uses
    the degraded key-token check, capping confidence and forcing review.
    visual_pages maps source_id -> pages from print-captured / visual-heavy
    sources where table/visual token misses should route to checker visual
    review instead of hard-failing on snapshot text.

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
        source_scrambled = frozenset((scrambled_pages or {}).get(candidate.source_id, ()))
        source_visual_pages = frozenset((visual_pages or {}).get(candidate.source_id, ()))
        verdict = (verdicts or {}).get(index)
        try:
            scored.append(
                score_candidate(
                    candidate,
                    taxonomy=taxonomy,
                    snapshot_text=snapshot_text,
                    page_count=page_count,
                    scrambled_pages=source_scrambled,
                    visual_pages=source_visual_pages,
                    verdict=verdict,
                    checker_enabled=checker_enabled,
                )
            )
        except ValueError as exc:
            reason = str(exc)
            # EvidenceFailure carries the human-readable message (e.g. the
            # scrambled-page degraded-fallback note); other reasons echo the code.
            message = getattr(exc, "message", reason)
            if (
                verdict is not None
                and reason in CHECKER_FAIL_REASONS.values()
                and verdict.note
                and not getattr(exc, "message", "")
            ):
                message = verdict.note
            failures.append(
                FailureRecord.from_candidate(
                    reason,
                    message,
                    candidate,
                    checker_strength=verdict.evidence_strength if verdict is not None else "",
                )
            )

    selected_entries: list[_Selected] = []
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
        selected_entries.append(
            _Selected(
                scored=selected,
                source=source,
                display_source=display_source,
                member_count=member_count,
                arbiter_note=arbiter_note,
                corroboration=corroboration,
            )
        )

    survivors, cross_leaf_failures = _dedup_cross_leaf(selected_entries, taxonomy)
    failures.extend(cross_leaf_failures)
    sibling_notes = _sibling_conflict_notes(survivors, taxonomy)

    output_rows = [
        _output_row(
            entry.scored,
            entry.display_source,
            taxonomy,
            arbiter_note=entry.arbiter_note,
            locator_source=entry.source.source if entry.member_count > 1 else "",
            corroboration=entry.corroboration,
            sibling_note=sibling_notes.get(id(entry), ""),
        )
        for entry in survivors
    ]

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


def _dedup_cross_leaf(
    entries: list[_Selected], taxonomy: Taxonomy
) -> tuple[list[_Selected], list[FailureRecord]]:
    """Deterministic cross-leaf dedup: one source doc emitting the SAME view on
    the SAME evidence under several leaves is fanning one call out.

    Cluster key: (source_id, view, normalized evidence span-set) — same source
    DOC (not group), identical spans, order-insensitive. Within a cluster of
    more than one row:
    - A leaf SURVIVES if its name is grounded in the evidence text (token
      overlap with the evidence — the same signal as the keep rule). When the
      evidence names several distinct leaves (e.g. "long NOK ... and long AUD",
      or "the information technology and communication services sectors"), every
      named leaf is a real, distinct call and all survive.
    - Leaves the evidence does NOT name are the fan-out duplicates and collapse
      to `duplicate_cross_leaf`, referencing the surviving leaf/leaves.
    - If the cluster names NO leaf, keep the single highest-overlap leaf
      (tie-break: locked-taxonomy order) and fail the rest.

    Rows that differ in view or evidence never share a cluster, so they never
    collapse — even on sibling leaves.

    Known limitation: the trigger is IDENTICAL evidence. The AB global-duration
    triple (Duration / Global Govt Bonds/SSAs / DM Sovereigns) cites a DIFFERENT
    forecast-table row per leaf, so it is out of scope for this rule; the Task-1
    materiality gate partially mitigates it.
    """
    clusters: dict[tuple[str, str, frozenset[str]], list[_Selected]] = {}
    for entry in entries:
        candidate = entry.scored.candidate
        span_key = frozenset(
            normalized
            for span in candidate.evidence_spans
            if (normalized := normalize_quote_text(span))
        )
        key = (candidate.source_id, candidate.view, span_key)
        clusters.setdefault(key, []).append(entry)

    dropped: dict[int, str] = {}  # id(entry) -> description of the kept leaf/leaves
    for members in clusters.values():
        if len(members) <= 1:
            continue
        named = [m for m in members if _leaf_named_in_evidence(m.scored.candidate)]
        keepers = named if named else [_highest_overlap_leaf(members, taxonomy)]
        kept_desc = ", ".join(
            f"'{m.scored.candidate.sub_asset_class}'" for m in keepers
        )
        keeper_ids = {id(m) for m in keepers}
        for member in members:
            if id(member) not in keeper_ids:
                dropped[id(member)] = kept_desc

    survivors: list[_Selected] = []
    failures: list[FailureRecord] = []
    for entry in entries:
        if id(entry) in dropped:
            failures.append(
                FailureRecord.from_candidate(
                    "duplicate_cross_leaf",
                    f"same source, view, and evidence as kept leaf {dropped[id(entry)]}; "
                    "this leaf is not named in the shared evidence (cross-leaf fan-out)",
                    entry.scored.candidate,
                )
            )
        else:
            survivors.append(entry)
    return survivors, failures


def _sibling_conflict_notes(entries: list[_Selected], taxonomy: Taxonomy) -> dict[int, str]:
    """Flag O-vs-U sibling rows on byte-identical evidence.

    This is a review tripwire only: same source doc, same cited page, same
    normalized evidence spans, same top-level Asset Class, opposite directional
    views. N-vs-U is deliberately out of scope.
    """
    clusters: dict[tuple[str, int | None, frozenset[str], str], list[_Selected]] = {}
    for entry in entries:
        candidate = entry.scored.candidate
        if candidate.view not in {"O", "U"}:
            continue
        lookup = taxonomy.require_label(candidate.sub_asset_class)
        span_key = frozenset(
            normalized
            for span in candidate.evidence_spans
            if (normalized := normalize_quote_text(span))
        )
        key = (
            candidate.source_id,
            _cited_page(candidate.locator),
            span_key,
            lookup.asset_class,
        )
        clusters.setdefault(key, []).append(entry)

    notes: dict[int, str] = {}
    for members in clusters.values():
        views = {member.scored.candidate.view for member in members}
        if views != {"O", "U"}:
            continue
        labels = sorted(member.scored.candidate.sub_asset_class for member in members)
        note = (
            "Sibling consistency: same source/page/evidence produced opposite "
            f"O/U views on related {taxonomy.require_label(members[0].scored.candidate.sub_asset_class).asset_class} "
            f"leaves ({', '.join(labels)}); review required."
        )
        for member in members:
            notes[id(member)] = note
    return notes


def _leaf_named_in_evidence(candidate: CandidateCall) -> bool:
    return _leaf_evidence_overlap(candidate.sub_asset_class, candidate.evidence_quote) > 0


def _highest_overlap_leaf(members: list[_Selected], taxonomy: Taxonomy) -> _Selected:
    """The keep rule when no leaf is named: most leaf-name/evidence token overlap
    wins; ties break by the leaf's position in the locked taxonomy CSV."""

    def sort_key(member: _Selected) -> tuple[int, int]:
        candidate = member.scored.candidate
        overlap = _leaf_evidence_overlap(candidate.sub_asset_class, candidate.evidence_quote)
        return (-overlap, taxonomy.require_label(candidate.sub_asset_class).number)

    return sorted(members, key=sort_key)[0]


# Leaf-label tokens are compared against the evidence with prefix tolerance so
# an extraction seam or a label/prose spelling difference ("tech" vs.
# "technology") does not read as "not named"; short tokens (2-3 chars, e.g. AI,
# NOK, AUD) must match a whole evidence word exactly.
_LEAF_NAME_STOPWORDS = frozenset({"of", "and", "the", "inc", "for", "vs"})


def _leaf_name_tokens(label: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", label.lower())
    return [token for token in tokens if len(token) >= 2 and token not in _LEAF_NAME_STOPWORDS]


def _leaf_evidence_overlap(leaf_label: str, evidence_text: str) -> int:
    """Count leaf-name tokens grounded in the evidence text."""
    evidence_words = set(re.findall(r"[a-z0-9]+", normalize_quote_text(evidence_text).lower()))
    return sum(
        1 for token in _leaf_name_tokens(leaf_label) if _token_matches(token, evidence_words)
    )


def _token_matches(token: str, evidence_words: set[str]) -> bool:
    for word in evidence_words:
        if token == word:
            return True
        if len(token) >= 4 and len(word) >= 4 and (word.startswith(token) or token.startswith(word)):
            return True
    return False


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
    sibling_note: str = "",
) -> dict[str, str]:
    candidate = scored.candidate
    lookup = taxonomy.output_fields_for(candidate.sub_asset_class)
    commentary = _commentary(candidate, locator_source=locator_source)
    if corroboration:
        commentary += f" {corroboration}"
    if scored.evidence_check.degraded:
        commentary += (
            " Evidence check: cited page detected as scrambled two-column text; "
            "verbatim quote match degraded to key-token overlap "
            "(confidence capped, review required)."
        )
    if scored.evidence_check.visual_unverified_by_text:
        commentary += (
            " Evidence check: snapshot text did not contain the table/visual tokens; "
            "checker verified the cited page image instead."
        )
    if scored.cap_reason:
        commentary += f" {scored.cap_reason}"
    if scored.call_language_note:
        commentary += f" {scored.call_language_note}"
    if scored.checker_status == "unclear":
        note = f" ({scored.checker_note})" if scored.checker_note else ""
        commentary += f" Checker: unconfirmed{note}."
    elif scored.checker_status == "missing":
        commentary += " Checker: not run."
    elif scored.checker_status == "confirmed" and scored.checker_note:
        commentary += f" Checker: {scored.checker_note}."
    review_flag = scored.review_flag
    if arbiter_note:
        commentary += f" Arbiter: {arbiter_note}"
        if review_flag == "none":
            review_flag = "review"
    if sibling_note:
        commentary += f" {sibling_note}"
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
        "basis": candidate.basis,
        "checker_strength": scored.checker_strength,
        "call_language": scored.call_language,
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


def _cited_page(locator: str) -> int | None:
    match = re.search(r"p\.?\s*(\d+)", locator, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


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
    basis_counts = Counter(row.get("basis") or "stated" for row in result.output_rows)
    if basis_counts:
        lines.append("## Call basis (kept rows)")
        lines.extend(
            f"- {basis}: {count}" for basis, count in sorted(basis_counts.items())
        )
        lines.append("")
    strength_counts = Counter(
        row.get("checker_strength") or "(none)" for row in result.output_rows
    )
    if strength_counts:
        lines.append("## Checker strength (kept rows)")
        lines.extend(
            f"- {strength}: {count}" for strength, count in sorted(strength_counts.items())
        )
        lines.append("")
    call_language_counts = Counter(
        row.get("call_language") or "(none)" for row in result.output_rows
    )
    if call_language_counts:
        lines.append("## Call language (kept rows)")
        lines.extend(
            f"- {language}: {count}"
            for language, count in sorted(call_language_counts.items())
        )
        lines.append("")
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
            scrambled = summary.get("scrambled_pages") or []
            if scrambled:
                flag_text += f" [scrambled pages: {', '.join(f'p.{n}' for n in scrambled)}]"
            pages = summary.get("page_count")
            chunk_count = summary.get("chunk_count", 0)
            size = f"{pages}p / {chunk_count} chunks" if pages else f"{chunk_count} chunks"
            lines.append(
                f"- {summary.get('source_id')} ({summary.get('source_type')}, {size}): "
                f"{summary.get('candidates', 0)} candidates emitted{flag_text}"
            )
        lines.append("")
    return "\n".join(lines)
