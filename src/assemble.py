"""Assemble validated candidates into run-level output files."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.commentary import (
    PDF_READ_MARKER,
    SEGMENT_SEP,
    STRUCTURED_COLUMNS,
    Commentary,
    Segment,
    display_locator,
    row_columns,
)
from src.confidence import (
    CHECKER_FAIL_REASONS,
    EvidenceCheck,
    HARD_FAILURE_EVIDENCE,
    HARD_FAILURE_MATERIALITY,
    HARD_FAILURE_QUOTE,
    HARD_FAILURE_TAXONOMY,
    HARD_FAILURE_VISUAL_LOCATOR,
    ConfidenceResult,
    effective_call_language,
    normalize_quote_text,
    score_candidate,
)
from src.csvio import write_csv
from src.pagecheck import cited_pages
from src.schemas import CandidateCall, CheckVerdict, SourceInfo
from src.sharedevidence import Entry as EvidenceEntry
from src.sharedevidence import Judge as EvidenceJudge
from src.sharedevidence import REASON_WEAKER_FIT, gate_entries
from src.taxonomy import Taxonomy


# Given the conflicting scored candidates for one (source, leaf), returns the
# winning index within the group (None = unresolved) and the reasoning.
Arbiter = Callable[[list[ConfidenceResult]], tuple[int | None, str]]
QuoteVisualVerifier = Callable[[CandidateCall, SourceInfo, Path | None], str]


# The shared commentary-merge convention. Wherever two commentaries fold into one
# row (same-view dedup here, and the firm-reconcile stage in src/reconcile.py),
# each segment is labeled with its source title and joined by this separator,
# kept/primary segment first. Owned by src/commentary.py (re-exported here) so
# the format can never drift between the two stages.
COMMENTARY_MERGE_SEP = SEGMENT_SEP


def _candidate_segment(candidate: CandidateCall, title: str, *, locator_source: str = "") -> Segment:
    """One structured segment for a candidate: reader-facing locator (page for
    PDF; `char:` chunk offsets of HTML/transcript sources render blank), the
    model's reasoning, and the verbatim quote. A grouped-source row suffixes
    the member title to the locator so the reader knows which document p.N is in.
    A candidate read from a paired-PDF chunk (`pdf:`) ends with PDF_READ_MARKER,
    which `evidence_origin` reads back after any merge."""
    locator = display_locator(candidate.locator)
    if locator and locator_source:
        locator = f"{locator} ({locator_source})"
    if candidate.chunk_id.startswith("pdf:"):
        locator = f"{locator} {PDF_READ_MARKER}".strip()
    return Segment(
        title=title,
        locator=locator,
        reasoning=candidate.reasoning,
        evidence=candidate.evidence_quote,
    )


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

# The four structured commentary columns (src/commentary.py) follow the
# workbook shape; `Full Commentary` is rendered from them for internal
# consumers. `basis`, `checker_strength`, `call_language` and `quote_match`
# are internal diagnostics for analysts (stated vs inferred, checker grade,
# quote-verification tier); `evidence_origin` (markdown | pdf | both) is derived
# from the Locator's PDF_READ_MARKER by src.commentary.row_columns. The client
# file (src/export.py) never carries them.
OUTPUT_COLUMNS = TARGET_OUTPUT_COLUMNS + STRUCTURED_COLUMNS + (
    "confidence",
    "band",
    "review_flag",
    "basis",
    "checker_strength",
    "call_language",
    "quote_match",
    "evidence_origin",
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

# Client-readable failures file. Same rows as failures.csv, but with a plain
# label + one-sentence explanation per reason code and no internal jargon, and
# grouped by label in importance order (most attention-worthy first). See
# CLIENT_FAILURE_LABELS.
CLIENT_FAILURE_COLUMNS = (
    "Firm",
    "Source",
    "Sub-Asset Class",
    "View (proposed)",
    "What happened",
    "Explanation",
    "Evidence / notes",
)

# Authoritative registry of every failure `reason_code` that can reach
# failures.csv. Codes raised by deterministic scoring live in `src.confidence`;
# the rest are emitted here in assemble or by `run.py`'s chunk/checker paths.
# CLIENT_FAILURE_LABELS must have an entry for every code in this set (a test
# enforces it), so a new code fails the test instead of shipping unmapped.
_CONFIDENCE_REASON_CODES = frozenset(
    {
        HARD_FAILURE_TAXONOMY,
        HARD_FAILURE_QUOTE,
        HARD_FAILURE_VISUAL_LOCATOR,
        HARD_FAILURE_EVIDENCE,
        HARD_FAILURE_MATERIALITY,
        *CHECKER_FAIL_REASONS.values(),
        "quote_not_found_visual",
    }
)
_ASSEMBLE_REASON_CODES = frozenset(
    {
        "arbitrated_out",
        "unresolved_conflict",
        "duplicate_same_view",
        "duplicate_cross_leaf",
        "implied_challenges_stated",
        "source_metadata_missing",
        REASON_WEAKER_FIT,
    }
)
_RUN_REASON_CODES = frozenset(
    {"json_parse_error", "engine_error", "checker_error", "ingest_error"}
)
# Emitted by the post-run firm-reconcile stage (src/reconcile.py) and folded into
# the run's failure files at the combine step. Registered here so the mapping
# test still enforces a client label for every code that can reach failures.csv.
_RECONCILE_REASON_CODES = frozenset(
    {
        "merged_by_reconcile",
        "superseded_by_reconcile",
        # Phase 3 near-leaf pass (src/reconcile.py --near-leaf).
        "near_leaf_merged",
        "near_leaf_superseded",
    }
)
ALL_REASON_CODES = (
    _CONFIDENCE_REASON_CODES
    | _ASSEMBLE_REASON_CODES
    | _RUN_REASON_CODES
    | _RECONCILE_REASON_CODES
)

# reason_code -> (What happened, Explanation), both written for a non-technical
# reader. `What happened` is a short label; `Explanation` says in one plain
# sentence what it means and what (if anything) the reader should do. This is a
# mapping layer only - the internal reason codes are never renamed.
#
# ORDER MATTERS: dict order is the importance order (most attention-worthy
# first), and failures-client.csv is sorted by it - whole-document and
# needs-a-human-decision entries at the top, no-action housekeeping (duplicates,
# too-small changes) at the bottom. Codes sharing a label sit adjacent so the
# client file groups cleanly. failures.csv (internal) is never re-ordered.
CLIENT_FAILURE_LABELS: dict[str, tuple[str, str]] = {
    "ingest_error": (
        "Document could not be ingested",
        "This source could not be fetched or converted into text, so it was "
        "skipped while the rest of the run continued. Let us know so we can "
        "re-run it.",
    ),
    "unresolved_conflict": (
        "Conflicting views - none kept",
        "Two views for this asset disagreed and could not be reconciled "
        "automatically, so neither was kept. A human should decide which is "
        "correct.",
    ),
    "json_parse_error": (
        "Part of a document couldn't be read",
        "A section of the document couldn't be processed automatically, so any "
        "calls in it were skipped. Let us know so we can re-run it.",
    ),
    "engine_error": (
        "Part of a document couldn't be processed",
        "A section of the document failed during processing, so any calls in it "
        "were skipped. Let us know so we can re-run it.",
    ),
    "checker_error": (
        "Second-reader check couldn't run",
        "The second-reader verification failed to run for this document, so "
        "affected calls weren't confirmed. Let us know so we can re-run it.",
    ),
    "source_metadata_missing": (
        "Skipped - source details missing",
        "The document's identifying details were unavailable, so this call "
        "could not be attached to a source. Usually a processing issue; let us "
        "know if it recurs.",
    ),
    "implied_challenges_stated": (
        "Suggestion - review recommended",
        "An implied reading challenges the firm's stated call; the stated call "
        "was kept and this row records the challenge for review.",
    ),
    "arbitrated_out": (
        "Conflicting views - other call kept",
        "The document(s) in this group took conflicting views on this asset; the call "
        "they hold most clearly was kept. Review if the kept call looks wrong.",
    ),
    "quote_not_found": (
        "Evidence could not be verified",
        "The exact supporting text couldn't be found in the document, so the "
        "call was dropped rather than kept unverified. A human can check the "
        "cited page.",
    ),
    "evidence_check_failed": (
        "Evidence could not be verified",
        "The supporting evidence didn't hold up on a second check, so the call "
        "was dropped rather than kept unverified. A human can check the cited "
        "page.",
    ),
    "quote_not_found_visual": (
        "Evidence could not be verified visually",
        "The exact supporting text could not be confirmed even after a visual "
        "page check, so the call was dropped rather than kept unverified. A "
        "human can check the cited page.",
    ),
    "visual_locator_missing": (
        "Evidence location missing",
        "A call from a chart or table didn't record where in the document it "
        "came from, so it couldn't be verified. A human can check the document.",
    ),
    "checker_sign_mismatch": (
        "Evidence points the other way",
        "A second reader found the cited text doesn't support the direction of "
        "this call, so it was dropped. A human can check the cited page.",
    ),
    "checker_asset_mismatch": (
        "Evidence is about a different asset",
        "A second reader found the cited text is about a different asset than "
        "the call, so it was dropped. A human can check the cited page.",
    ),
    "checker_not_forward_looking": (
        "Not a view the firm holds",
        "A second reader found the cited text isn't a view the firm takes "
        "(e.g. it describes the past or an analytical input rather than a position), "
        "so it was dropped. A human can check the "
        "cited page.",
    ),
    "taxonomy_no_match": (
        "Skipped - asset not on the list",
        "The asset named didn't match any item on the approved asset list, so "
        "it was left out. No action needed unless it should be on the list.",
    ),
    "delta_below_materiality": (
        "Change too small to call",
        "The forecast change behind this call was too small to justify a view, "
        "so it was left out. No action needed.",
    ),
    REASON_WEAKER_FIT: (
        "Evidence better supports a related asset",
        "The same passage was used for more than one asset; it supports another "
        "asset more directly, so that call was kept and this one was set aside. "
        "The notes name the kept asset. Review if this asset should also stand.",
    ),
    # Firm-reconcile outcomes (src/reconcile.py). Near the housekeeping end:
    # superseded is a dropped-in-favour-of-a-newer-doc call an analyst may still
    # want to eyeball; merged is pure de-duplication with the evidence visible in
    # the reconciled row.
    "superseded_by_reconcile": (
        "Superseded - a newer document from this firm updated this view",
        "Another document from the same firm took a different view on this asset, "
        "and a more current (or otherwise preferred) call was kept in its place. "
        "Review if the kept call looks wrong.",
    ),
    "near_leaf_superseded": (
        "Merged into a related call - please review",
        "This firm made views on two closely related assets (e.g. a broad category "
        "and a specific part of it) that pointed different ways; they were read as "
        "one collective call and the most relevant view was kept. Review that the "
        "kept view is the right one.",
    ),
    "duplicate_same_view": (
        "Duplicate - already covered",
        "The same view for this asset appears in another of this firm's "
        "documents; it was kept once. No action needed.",
    ),
    "duplicate_cross_leaf": (
        "Duplicate - same call, related asset",
        "This repeats a view already kept for a closely related asset from the "
        "same piece of text; it was kept once to avoid double-counting. No "
        "action needed.",
    ),
    "merged_by_reconcile": (
        "Merged - same view stated in several documents",
        "Several documents from this firm made the same call on this asset; they "
        "were merged into one row and every document's wording is preserved in "
        "that row's commentary. No action needed.",
    ),
    "near_leaf_merged": (
        "Merged - same view on a closely related asset",
        "This firm made the same call on two closely related assets (e.g. a broad "
        "category and a specific part of it); they were merged into one row on the "
        "most precise asset and every document's wording is preserved. No action "
        "needed.",
    ),
}

# Reason codes deliberately kept OUT of failures-client.csv (they stay in the
# internal failures.csv for audit). A same-view duplicate's evidence is now
# merged into the kept row's Full Commentary (Part A: citation-preserving
# merges), so it is visibly in the output and there is nothing for the client to
# review. Implemented as an explicit exclusion, NOT by deleting the label from
# CLIENT_FAILURE_LABELS, so the mapping test still enforces a label for the code.
_CLIENT_FILE_EXCLUDED_CODES = frozenset({"duplicate_same_view"})

# Importance rank per reason code = its position in CLIENT_FAILURE_LABELS.
_CLIENT_FAILURE_RANKS: dict[str, int] = {
    code: rank for rank, code in enumerate(CLIENT_FAILURE_LABELS)
}

# An unmapped code never crashes: it falls back to the raw code plus a generic,
# still plain-language explanation (the test guards against this in practice).
_CLIENT_FAILURE_FALLBACK = (
    "This item was set aside during processing; the reason code is shown for "
    "our reference. Let us know if it needs a closer look."
)


def client_failure_label(reason_code: str) -> tuple[str, str]:
    """(What happened, Explanation) for a reason code, with a graceful fallback
    for any code not in CLIENT_FAILURE_LABELS (raw code + generic sentence)."""
    return CLIENT_FAILURE_LABELS.get(
        reason_code, (reason_code, _CLIENT_FAILURE_FALLBACK)
    )


def client_failure_rank(reason_code: str) -> int:
    """Sort key for failures-client.csv: the code's position in
    CLIENT_FAILURE_LABELS (dict order = most important first). An unmapped code
    sorts to the very top - something we didn't anticipate deserves the most
    attention, not the least."""
    return _CLIENT_FAILURE_RANKS.get(reason_code, -1)


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
    # Same source/leaf view conflicts whose candidates came from both a `pdf:`
    # and a Markdown chunk (counted on the conflict groups themselves).
    mixed_origin_conflicts: int = 0


@dataclass(frozen=True, slots=True)
class _Selected:
    """One (source/group, leaf) winner that survived scoring, same-view dedup,
    and conflict arbitration - carried to the cross-leaf dedup pass and then
    rendered. Bundles the display context computed during grouping."""

    scored: ConfidenceResult
    source: SourceInfo
    display_source: SourceInfo
    member_count: int
    arbiter_note: str = ""
    # Losing same-view candidates' passages, as structured segments to fold into
    # the kept row (citation-preserving dedup).
    merged_segments: tuple[Segment, ...] = ()
    # Set when a same-leaf implied call challenged the kept stated call
    # (deterministic stated-beats-implied resolution). Rendered on the kept row
    # and forces a review flag so a human sees the recommendation.
    challenge_note: str = ""


_PDF_CHUNK_ID = re.compile(r"pdf:p(\d+)-(\d+)")


def pdf_chunk_pages(chunk_id: str) -> tuple[int, ...]:
    """Pages of a Markdown source's paired-PDF chunk (`pdf:p4-5`); () otherwise."""
    match = _PDF_CHUNK_ID.fullmatch(chunk_id)
    if not match:
        return ()
    return tuple(range(int(match.group(1)), int(match.group(2)) + 1))


def validation_key(candidate: CandidateCall) -> str | tuple[str, str]:
    """Key into the per-source validation inputs (page count, scrambled / OCR /
    visual pages, native path). A paired-PDF chunk is keyed per chunk, so a
    Markdown candidate never sees the PDF's pages or path and a PDF candidate
    never sees the Markdown's."""
    if pdf_chunk_pages(candidate.chunk_id):
        return (candidate.source_id, candidate.chunk_id)
    return candidate.source_id


def assemble_candidates(
    candidates: list[CandidateCall],
    *,
    sources: dict[str, SourceInfo],
    taxonomy: Taxonomy,
    snapshots: dict[tuple[str, str], str],
    page_counts: dict[str, int] | None = None,
    scrambled_pages: dict[str, set[int]] | None = None,
    ocr_pages: dict[str, set[int]] | None = None,
    visual_pages: dict[str, set[int]] | None = None,
    verdicts: dict[int, CheckVerdict] | None = None,
    arbiter: Arbiter | None = None,
    group_map: dict[str, str] | None = None,
    quote_visual_verifier: QuoteVisualVerifier | None = None,
    native_source_paths: dict[str, Path] | None = None,
    shared_evidence_judge: EvidenceJudge | None = None,
) -> AssemblyResult:
    """page_counts maps source_id -> PDF page count (absent for HTML sources).

    shared_evidence_judge, when given, runs the shared-evidence gate
    (src/sharedevidence.py) over the survivors of cross-leaf dedup: one
    passage cited for several leaves keeps only the most compelling call(s).
    None skips the gate (tests, --no-evidence-gate).

    scrambled_pages maps source_id -> the set of column-interleaved page numbers
    (see src/ingest.detect_scrambled_page); a prose call citing one of them uses
    the degraded key-token check, capping confidence and forcing review.
    ocr_pages maps source_id -> the set of image-only / low-text page numbers;
    a prose call citing one gets the same degraded cap and review flag, with an
    OCR-specific evidence message.
    visual_pages maps source_id -> pages from print-captured / visual-heavy
    sources where table/visual token misses should route to checker visual
    review instead of hard-failing on snapshot text.
    A Markdown source's paired-PDF chunk keys all of these, and
    native_source_paths, by (source_id, chunk_id) instead (validation_key); a
    `pdf:` candidate citing a page outside its chunk fails.

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
        key = validation_key(candidate)
        page_count = (page_counts or {}).get(key)
        source_scrambled = frozenset((scrambled_pages or {}).get(key, ()))
        source_ocr = frozenset((ocr_pages or {}).get(key, ()))
        source_visual_pages = frozenset((visual_pages or {}).get(key, ()))
        verdict = (verdicts or {}).get(index)
        chunk_pages = pdf_chunk_pages(candidate.chunk_id)
        cited = cited_pages(candidate.locator) if chunk_pages else ()
        if chunk_pages and (not cited or not set(cited) <= set(chunk_pages)):
            # Every cited page (a range `p.3-9` included) must be one the chunk
            # was given. Checked before scoring, so the visual verifier never
            # rescues it.
            failures.append(
                FailureRecord.from_candidate(
                    HARD_FAILURE_QUOTE,
                    f"cited page is outside the PDF pages this chunk was given "
                    f"(p.{chunk_pages[0]}-{chunk_pages[-1]})",
                    candidate,
                )
            )
            continue
        try:
            scored.append(
                score_candidate(
                    candidate,
                    taxonomy=taxonomy,
                    snapshot_text=snapshot_text,
                    page_count=page_count,
                    scrambled_pages=source_scrambled,
                    ocr_pages=source_ocr,
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
            if reason == HARD_FAILURE_QUOTE and quote_visual_verifier is not None:
                source_info = sources.get(candidate.source_id)
                native_path = (native_source_paths or {}).get(key)
                if source_info is not None:
                    visual_check = _visual_quote_check(
                        candidate,
                        source_info,
                        native_path,
                        quote_visual_verifier,
                    )
                    if visual_check.passed:
                        try:
                            scored.append(
                                score_candidate(
                                    candidate,
                                    taxonomy=taxonomy,
                                    snapshot_text=snapshot_text,
                                    page_count=page_count,
                                    scrambled_pages=source_scrambled,
                                    ocr_pages=source_ocr,
                                    visual_pages=source_visual_pages,
                                    verdict=verdict,
                                    checker_enabled=checker_enabled,
                                    evidence_check_override=visual_check,
                                )
                            )
                            continue
                        except ValueError as visual_exc:
                            reason = str(visual_exc)
                            message = getattr(visual_exc, "message", reason)
                    else:
                        failures.append(
                            FailureRecord.from_candidate(
                                visual_check.reason_code,
                                visual_check.message,
                                candidate,
                                checker_strength=(
                                    verdict.evidence_strength if verdict is not None else ""
                                ),
                            )
                        )
                        continue
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
    mixed_origin_conflicts = 0
    for group in _group_scored(scored, group_map).values():
        views = {item.candidate.view for item in group}
        arbiter_note = ""
        merged_segments: list[tuple[str, str, str]] = []
        challenge_note = ""
        if len(views) > 1:
            if len({item.candidate.chunk_id.startswith("pdf:") for item in group}) > 1:
                mixed_origin_conflicts += 1
            stated_resolution = _resolve_stated_vs_implied(group)
            if stated_resolution is not None:
                # A clean stated-vs-implied split on one leaf: stated wins
                # deterministically (client rule), the implied challenge is
                # logged as a flagged recommendation, and the arbiter is not run.
                selected, resolution_losers, challenge_note = stated_resolution
                failures.extend(
                    FailureRecord.from_candidate(reason, message, item.candidate)
                    for item, reason, message in resolution_losers
                )
            else:
                winner, reasoning = _arbitrate(group, arbiter)
                if winner is None:
                    message = (
                        "multiple views survived validation for the same source/group and leaf"
                    )
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
            # Fold every losing same-view candidate's commentary into the kept
            # row (within one source and across grouped companion sources - one
            # convention). The failures.csv entry stays for audit, its note
            # reworded to say the evidence was merged, not discarded.
            for item in group:
                if item is selected:
                    continue
                failures.append(
                    FailureRecord.from_candidate(
                        "duplicate_same_view",
                        f"same view already kept from {selected.candidate.locator}; "
                        "this evidence was merged into the kept row's commentary",
                        item.candidate,
                    )
                )
                dup_source = sources.get(item.candidate.source_id)
                title = dup_source.source if dup_source else item.candidate.source_id
                merged_segments.append(_candidate_segment(item.candidate, title))

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
                merged_segments=tuple(merged_segments),
                challenge_note=challenge_note,
            )
        )

    survivors, cross_leaf_failures = _dedup_cross_leaf(selected_entries, taxonomy)
    failures.extend(cross_leaf_failures)
    shared_notes: dict[int, str] = {}
    shared_review: set[int] = set()
    if shared_evidence_judge is not None:
        survivors, shared_notes, shared_review, shared_failures = _gate_shared_evidence(
            survivors, taxonomy, shared_evidence_judge
        )
        failures.extend(shared_failures)
    sibling_notes = _sibling_conflict_notes(survivors, taxonomy)

    output_rows = [
        _output_row(
            entry.scored,
            entry.display_source,
            taxonomy,
            arbiter_note=entry.arbiter_note,
            locator_source=entry.source.source if entry.member_count > 1 else "",
            merged_segments=entry.merged_segments,
            sibling_note=sibling_notes.get(id(entry), ""),
            challenge_note=entry.challenge_note,
            shared_note=shared_notes.get(id(entry), ""),
            shared_review=id(entry) in shared_review,
        )
        for entry in survivors
    ]

    return AssemblyResult(
        output_rows=output_rows,
        failures=failures,
        candidate_count=len(candidates),
        mixed_origin_conflicts=mixed_origin_conflicts,
    )


def _visual_quote_check(
    candidate: CandidateCall,
    source: SourceInfo,
    native_source_path: Path | None,
    verifier: QuoteVisualVerifier,
) -> EvidenceCheck:
    try:
        judgment = verifier(candidate, source, native_source_path)
    except Exception as exc:
        judgment = "malformed"
        detail = f"visual verifier failed: {type(exc).__name__}: {exc}"
    else:
        detail = ""
    if judgment == "present_verbatim":
        return EvidenceCheck(
            True,
            message=(
                "visual verifier confirmed the quote verbatim on the rendered "
                "source page; confidence capped"
            ),
            degraded=True,
            quote_match="visual",
        )
    if judgment == "present_paraphrase":
        detail = (
            "visual verifier found only a paraphrase, not the submitted "
            "verbatim quote; dropped under the verified-evidence policy"
        )
    elif judgment == "absent":
        detail = "visual verifier did not find the submitted quote on the cited page"
    elif not detail:
        detail = f"visual verifier returned malformed judgment {judgment!r}; treated as absent"
    return EvidenceCheck(
        False,
        "quote_not_found_visual",
        detail,
        quote_match="visual",
    )


def _resolve_stated_vs_implied(
    group: list[ConfidenceResult],
) -> tuple[ConfidenceResult, list[tuple[ConfidenceResult, str, str]], str] | None:
    """Deterministic stated-beats-implied resolution for one conflicting
    (source/group, leaf) - the client's rule (ROADMAP decision 5).

    Fires only on the clean split it covers: the group holds only `stated` and
    `inferred` candidates, at least one of each, and the stated side carries a
    single view. The stated call then wins WITHOUT consulting the arbiter; every
    inferred call whose view differs is a *challenge*, recorded as a flagged
    recommendation (it never replaces the stated row in v1). Inferred calls that
    agree with the stated view are ordinary same-view corroboration.

    Returns (winner, losers, challenge_note) or None to fall through to the
    arbiter. `losers` is a list of (item, reason_code, message). Conflicts where
    both sides are stated, both are inferred, or a third basis (forecast_delta)
    is present return None and keep the existing arbiter path unchanged.

    The `implied_challenges_stated` message carries the implied view and its
    reasoning as a recommendation - the deliberate hook the v1.2 confidence-based
    override path will build on (a high-confidence implied call overriding a
    low-confidence stated view); v1 records the recommendation, nothing more.
    """
    stated = [item for item in group if item.candidate.basis == "stated"]
    inferred = [item for item in group if item.candidate.basis == "inferred"]
    if not stated or not inferred:
        return None
    if len(stated) + len(inferred) != len(group):
        return None  # a third basis (e.g. forecast_delta) is present - use the arbiter
    stated_views = {item.candidate.view for item in stated}
    if len(stated_views) != 1:
        return None  # the stated side disagrees with itself - that is arbiter work
    (stated_view,) = tuple(stated_views)

    winner = max(stated, key=lambda item: item.confidence)
    losers: list[tuple[ConfidenceResult, str, str]] = []
    challenges: list[str] = []
    for item in stated:
        if item is winner:
            continue
        losers.append(
            (
                item,
                "duplicate_same_view",
                f"same stated view already kept from {winner.candidate.locator}",
            )
        )
    for item in inferred:
        candidate = item.candidate
        if candidate.view == stated_view:
            losers.append(
                (
                    item,
                    "duplicate_same_view",
                    f"inferred call corroborates the kept stated {stated_view} view "
                    f"from {winner.candidate.locator}",
                )
            )
            continue
        message = (
            f"implied {candidate.view} challenges the kept stated {stated_view} call "
            f"on '{candidate.sub_asset_class}'; stated wins in v1, so this is recorded "
            f"as a recommendation only. Implied reasoning: {candidate.reasoning} "
            f"Recommendation: reconsider the stated {stated_view} call in light of this "
            "inference."
        )
        losers.append((item, "implied_challenges_stated", message))
        challenges.append(f"implied {candidate.view} ({candidate.reasoning})")

    if not challenges:
        return None  # no inferred call actually challenged the stated view

    challenge_note = (
        "Implied-call challenge (recommendation only; stated call kept): "
        + "; ".join(challenges)
        + " Review recommended."
    )
    return winner, losers, challenge_note


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

    Cluster key: (source_id, view, normalized evidence span-set) - same source
    DOC (not group), identical spans, order-insensitive. Within a cluster of
    more than one row:
    - A leaf SURVIVES if its name is grounded in the evidence text (token
      overlap with the evidence - the same signal as the keep rule). When the
      evidence names several distinct leaves (e.g. "long NOK ... and long AUD",
      or "the information technology and communication services sectors"), every
      named leaf is a real, distinct call and all survive.
    - Leaves the evidence does NOT name are the fan-out duplicates and collapse
      to `duplicate_cross_leaf`, referencing the surviving leaf/leaves.
    - If the cluster names NO leaf, keep the single highest-overlap leaf
      (tie-break: locked-taxonomy order) and fail the rest.

    Rows that differ in view or evidence never share a cluster, so they never
    collapse - even on sibling leaves.

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


def _gate_shared_evidence(
    entries: list[_Selected], taxonomy: Taxonomy, judge: EvidenceJudge
) -> tuple[list[_Selected], dict[int, str], set[int], list[FailureRecord]]:
    """Shared-evidence gate over the survivors: the same passage in one source
    DOCUMENT cited for several leaves keeps only the call(s) it most
    compellingly supports (LLM-categorical, deterministically applied, fails
    closed to keep-all + review). Returns (survivors, id(entry) -> note, the
    ids that need review, failures for the dropped calls)."""
    by_id = {id(entry): entry for entry in entries}
    gate_inputs: list[EvidenceEntry] = []
    for entry in entries:
        candidate = entry.scored.candidate
        lookup = taxonomy.require_label(candidate.sub_asset_class)
        gate_inputs.append(
            EvidenceEntry(
                entry_id=id(entry),
                source_key=candidate.source_id,
                firm=entry.source.firm,
                source_title=entry.source.source,
                evidence=candidate.evidence_quote,
                leaf=candidate.sub_asset_class,
                view=candidate.view,
                reasoning=candidate.reasoning,
                basis=candidate.basis,
                asset_class=lookup.asset_class,
                category=lookup.asset_class_category,
            )
        )
    result = gate_entries(gate_inputs, judge)
    failures = [
        FailureRecord.from_candidate(
            REASON_WEAKER_FIT,
            message,
            by_id[entry_id].scored.candidate,
            checker_strength=by_id[entry_id].scored.checker_strength,
        )
        for entry_id, message in result.dropped.items()
    ]
    survivors = [entry for entry in entries if id(entry) not in result.dropped]
    return survivors, dict(result.kept_notes), set(result.review_ids), failures


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
    overlap = sum(
        1 for token in _leaf_name_tokens(leaf_label) if _token_matches(token, evidence_words)
    )
    # Ordinary currency names count even when the ISO code is absent. Match
    # whole adjacent words so a bare 'dollar' does not name AUD; a bare
    # 'pound' (weight, verb) does not name GBP. 'sterling' stays: in evidence
    # already shared with a GBP call it is the currency.
    aliases = {
        "GBP": (
            "sterling", "british pound", "british pounds", "uk pound", "uk pounds",
            "pound sterling", "pounds sterling",
        ),
        "AUD": ("australian dollar", "australian dollars", "aussie", "aussie dollar", "aussie dollars"),
    }
    evidence_phrase = " " + " ".join(re.findall(r"[a-z0-9]+", normalize_quote_text(evidence_text).lower())) + " "
    if any(f" {name} " in evidence_phrase for name in aliases.get(leaf_label, ())):
        return max(1, overlap)
    return overlap


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
    sources: dict[str, SourceInfo] | None = None,
    source_summaries: list[dict[str, object]] | None = None,
    chunk_failures: list[FailureRecord] | None = None,
    run_config: dict[str, object] | None = None,
    grouping: dict[str, object] | None = None,
    quote_visual_stats: dict[str, int] | None = None,
    provenance: dict[str, object] | None = None,
) -> None:
    """Write the run's review files.

    chunk_failures are whole-chunk failures (e.g. unparseable model output) that
    produced no candidate; they are recorded in failures.csv and counted
    separately in the manifest so the candidate reconciliation stays exact.
    run_config (engine/model/effort) and provenance (code commit, working-tree
    state, content hashes; `src.provenance`) are recorded in the manifest so a
    frozen run states exactly what produced it. grouping is the resolved group-notes
    plan (groups + warnings) so the manifest shows exactly what was combined.
    sources maps source_id -> SourceInfo so the client-readable failures file can
    show each firm/source title (absent -> the source_id is shown instead).

    Alongside `failures.csv` (unchanged, internal) a `failures-client.csv` is
    written with plain labels/explanations, grouped by label and sorted
    most-important-first (CLIENT_FAILURE_LABELS order; stable within a label, so
    per-source order is preserved inside each group). `duplicate_same_view` rows
    are excluded from the client file (their evidence is merged into the kept
    row); they stay in the internal file for audit.
    """
    chunk_failures = chunk_failures or []
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    all_failures = [*result.failures, *chunk_failures]
    # Page check (src/pagecheck.py, no model calls): rows naming a source with a
    # paired PDF get their Markdown quotes' pages checked against the PDF text
    # before anything is written. Other rows are written exactly as assembled.
    from src import pagecheck

    output_rows = list(result.output_rows)
    pdf_for_title = pagecheck.pdf_map_from_infos(sources or {})
    page_audit: list[dict[str, str]] | None = None
    if pdf_for_title:
        indexes = pagecheck.rows_with_pdf(output_rows, pdf_for_title)
        checked, page_audit = pagecheck.page_check([output_rows[i] for i in indexes], pdf_for_title)
        for index, row in zip(indexes, checked):
            output_rows[index] = row
        write_csv(run_dir / "pagecheck-audit.csv", pagecheck.AUDIT_COLUMNS, page_audit)
    write_csv(run_dir / "output.csv", OUTPUT_COLUMNS, output_rows)
    write_csv(
        run_dir / "failures.csv",
        FAILURE_COLUMNS,
        [failure.to_row() for failure in all_failures],
    )
    # Exclude the merged-away same-view duplicates from the client file: their
    # evidence is now folded into the kept row's commentary, so there is nothing
    # for the client to review. They remain in the internal failures.csv above.
    client_failures = sorted(
        (f for f in all_failures if f.reason_code not in _CLIENT_FILE_EXCLUDED_CODES),
        key=lambda failure: client_failure_rank(failure.reason_code),
    )
    client_failure_rows = [_client_failure_row(failure, sources or {}) for failure in client_failures]
    write_csv(run_dir / "failures-client.csv", CLIENT_FAILURE_COLUMNS, client_failure_rows)
    # The client-facing twin of output.csv (structured commentary, no internal
    # diagnostics, BOM + xlsx) is written by every run so it can never be
    # forgotten at hand-off time. Late import: export reads these columns.
    from src.export import write_client_files

    write_client_files(run_dir, output_rows, client_failure_rows)
    manifest = _manifest_text(
        result,
        source_summaries or [],
        chunk_failures,
        run_config,
        grouping,
        quote_visual_stats,
        page_audit,
        provenance,
    )
    (run_dir / "manifest.md").write_text(manifest, encoding="utf-8")


def _client_failure_row(
    failure: FailureRecord, sources: dict[str, SourceInfo]
) -> dict[str, str]:
    """One failures-client.csv row: firm/source titles resolved from `sources`,
    a plain What-happened label and Explanation, and the row's own message (or
    reasoning) as human-readable evidence - no internal codes."""
    what_happened, explanation = client_failure_label(failure.reason_code)
    source_info = sources.get(failure.source_id)
    notes = failure.message or failure.reasoning or failure.evidence_quote
    return {
        "Firm": source_info.firm if source_info else "",
        "Source": source_info.source if source_info else failure.source_id,
        "Sub-Asset Class": failure.sub_asset_class,
        "View (proposed)": failure.view,
        "What happened": what_happened,
        "Explanation": explanation,
        "Evidence / notes": notes,
    }


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
            # Dates are document-extracted and often blank; join only the
            # non-blank ones so a grouped row never shows a stray " | ".
            date=" | ".join(member.date for member in members if member.date),
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
    merged_segments: tuple[Segment, ...] = (),
    sibling_note: str = "",
    challenge_note: str = "",
    shared_note: str = "",
    shared_review: bool = False,
) -> dict[str, str]:
    candidate = scored.candidate
    lookup = taxonomy.output_fields_for(candidate.sub_asset_class)
    # The kept candidate's own passage first, then each folded-in same-view
    # loser (citation-preserving merge). locator_source is the kept source's
    # title for a grouped row, so the reader knows which document p.N is in; a
    # merged row already labels every segment with its title, so the suffix is
    # only needed on a single-segment grouped row.
    primary = _candidate_segment(
        candidate,
        locator_source or source.source,
        locator_source="" if merged_segments else locator_source,
    )
    segments = (primary, *merged_segments)

    # Reviewer notes: everything that is ABOUT the call rather than from the
    # document - evidence-check outcome, caps, checker, arbiter, tripwires.
    notes: list[str] = []
    if scored.evidence_check.degraded:
        detail = scored.evidence_check.message or (
            "cited page used degraded evidence verification "
            "(confidence capped)"
        )
        notes.append(f"Evidence check: {detail}.")
    if scored.evidence_check.visual_unverified_by_text:
        notes.append(
            "Evidence check: snapshot text did not contain the table/visual tokens; "
            "checker verified the cited page image instead."
        )
    if scored.cap_reason:
        notes.append(scored.cap_reason)
    if scored.call_language_note:
        notes.append(scored.call_language_note)
    if scored.checker_status == "unclear":
        note = f" ({scored.checker_note})" if scored.checker_note else ""
        notes.append(f"Checker: unconfirmed{note}.")
    elif scored.checker_status == "missing":
        notes.append("Checker: not run.")
    elif scored.checker_status == "confirmed" and scored.checker_note:
        notes.append(f"Checker: {scored.checker_note}.")
    review_flag = scored.review_flag
    for flagged_note, prefix in (
        (arbiter_note, "Arbiter: "),
        (sibling_note, ""),
        (challenge_note, ""),
    ):
        if flagged_note:
            notes.append(f"{prefix}{flagged_note}")
            if review_flag == "none":
                review_flag = "review"
    if shared_note:
        # A clean single keep is noted only; several calls kept on one passage
        # (or a failed verdict) is a review item.
        notes.append(shared_note)
        if shared_review and review_flag == "none":
            review_flag = "review"
    commentary = Commentary(segments, " ".join(notes))
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
        **row_columns(commentary),
        "confidence": str(scored.confidence),
        "band": scored.band,
        "review_flag": review_flag,
        "basis": candidate.basis,
        "checker_strength": scored.checker_strength,
        "call_language": scored.call_language,
        "quote_match": scored.evidence_check.quote_match,
    }


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


def _manifest_text(
    result: AssemblyResult,
    source_summaries: list[dict[str, object]],
    chunk_failures: list[FailureRecord],
    run_config: dict[str, object] | None = None,
    grouping: dict[str, object] | None = None,
    quote_visual_stats: dict[str, int] | None = None,
    page_audit: list[dict[str, str]] | None = None,
    provenance: dict[str, object] | None = None,
) -> str:
    kept = len(result.output_rows)
    failed = len(result.failures)
    lines = ["# Run Manifest", ""]
    if run_config:
        lines.append("## Run configuration")
        lines.extend(f"- {key}: {value}" for key, value in run_config.items() if value is not None)
        lines.append("")
    if provenance:
        from src.provenance import manifest_lines

        lines.extend(manifest_lines(provenance))
    if grouping:
        lines.append("## Grouping")
        lines.append(f"- group notes: {grouping.get('notes_path')}")
        for group in grouping.get("groups", []):
            note = f" - note: {group['note']}" if group.get("note") else ""
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
    origin_counts = Counter(
        row.get("evidence_origin") or "(none)" for row in result.output_rows
    )
    if "pdf" in origin_counts or "both" in origin_counts or result.mixed_origin_conflicts:
        lines.append("## Evidence origin (kept rows)")
        lines.extend(
            f"- {origin}: {count}" for origin, count in sorted(origin_counts.items())
        )
        lines.append(
            "- view conflicts between a PDF-read and a Markdown-read candidate: "
            f"{result.mixed_origin_conflicts}"
        )
        lines.append("")
    quote_match_counts = Counter(
        row.get("quote_match") or "(none)" for row in result.output_rows
    )
    if quote_match_counts:
        lines.append("## Quote match tier (kept rows)")
        lines.extend(
            f"- {tier}: {count}" for tier, count in sorted(quote_match_counts.items())
        )
        lines.append("")
    if quote_visual_stats:
        lines.append("## Quote visual verification")
        lines.extend(
            f"- {key}: {count}" for key, count in sorted(quote_visual_stats.items())
        )
        lines.append("")
    if page_audit is not None:
        from src.pagecheck import OUTCOMES, verified_page_count

        page_counts = Counter(record["outcome"] for record in page_audit)
        lines.append("## Page check (paired PDFs)")
        lines.append(f"- quotes checked: {len(page_audit)}")
        lines.append(f"- quotes with a verified page after the check: {verified_page_count(page_audit)}")
        lines.extend(f"- {outcome}: {page_counts.get(outcome, 0)}" for outcome in OUTCOMES)
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
            ingest_error = summary.get("ingest_error") or ""
            flag_text = " [visual_heavy]" if summary.get("visual_heavy") else ""
            if summary.get("printed_pdf"):
                flag_text += " [printed-to-pdf]"
            if ingest_error:
                flag_text += f" [ingest-failed: {ingest_error}]"
            scrambled = summary.get("scrambled_pages") or []
            if scrambled:
                flag_text += f" [scrambled pages: {', '.join(f'p.{n}' for n in scrambled)}]"
            ocr_pages = summary.get("ocr_pages") or []
            if ocr_pages:
                flag_text += f" [OCR pages: {', '.join(f'p.{n}' for n in ocr_pages)}]"
            if summary.get("ocr_note"):
                flag_text += f" [OCR note: {summary.get('ocr_note')}]"
            if summary.get("pdf_chunk_count"):
                flagged = ", ".join(f"p.{n}" for n in summary.get("flagged_pages") or [])
                flag_text += (
                    f" [paired PDF: {summary.get('pdf_chunk_count')} PDF chunks, "
                    f"flagged pages: {flagged}]"
                )
            pages = summary.get("page_count")
            chunk_count = summary.get("chunk_count", 0)
            size = f"{pages}p / {chunk_count} chunks" if pages else f"{chunk_count} chunks"
            lines.append(
                f"- {summary.get('source_id')} ({summary.get('source_type')}, {size}): "
                f"{summary.get('candidates', 0)} candidates emitted{flag_text}"
            )
        lines.append("")
    return "\n".join(lines)
