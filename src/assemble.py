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

# Client-readable failures file. Same rows, same order as failures.csv, but with
# a plain label + one-sentence explanation per reason code and no internal
# jargon. See CLIENT_FAILURE_LABELS.
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
    }
)
_RUN_REASON_CODES = frozenset({"json_parse_error", "engine_error", "checker_error"})
ALL_REASON_CODES = _CONFIDENCE_REASON_CODES | _ASSEMBLE_REASON_CODES | _RUN_REASON_CODES

# reason_code -> (What happened, Explanation), both written for a non-technical
# reader. `What happened` is a short label; `Explanation` says in one plain
# sentence what it means and what (if anything) the reader should do. This is a
# mapping layer only — the internal reason codes are never renamed.
CLIENT_FAILURE_LABELS: dict[str, tuple[str, str]] = {
    "duplicate_same_view": (
        "Duplicate — already covered",
        "The same view for this asset appears in another of this firm's "
        "documents; it was kept once. No action needed.",
    ),
    "duplicate_cross_leaf": (
        "Duplicate — same call, related asset",
        "This repeats a view already kept for a closely related asset from the "
        "same piece of text; it was kept once to avoid double-counting. No "
        "action needed.",
    ),
    "arbitrated_out": (
        "Conflicting views — other call kept",
        "Two documents disagreed; the more current or more specific call was "
        "kept. Review if the kept call looks wrong.",
    ),
    "unresolved_conflict": (
        "Conflicting views — none kept",
        "Two views for this asset disagreed and could not be reconciled "
        "automatically, so neither was kept. A human should decide which is "
        "correct.",
    ),
    "implied_challenges_stated": (
        "Suggestion — review recommended",
        "An implied reading challenges the firm's stated call; the stated call "
        "was kept and this row records the challenge for review.",
    ),
    "source_metadata_missing": (
        "Skipped — source details missing",
        "The document's identifying details were unavailable, so this call "
        "could not be attached to a source. Usually a processing issue; let us "
        "know if it recurs.",
    ),
    "taxonomy_no_match": (
        "Skipped — asset not on the list",
        "The asset named didn't match any item on the approved asset list, so "
        "it was left out. No action needed unless it should be on the list.",
    ),
    "quote_not_found": (
        "Evidence could not be verified",
        "The exact supporting text couldn't be found in the document, so the "
        "call was dropped rather than kept unverified. A human can check the "
        "cited page.",
    ),
    "visual_locator_missing": (
        "Evidence location missing",
        "A call from a chart or table didn't record where in the document it "
        "came from, so it couldn't be verified. A human can check the document.",
    ),
    "evidence_check_failed": (
        "Evidence could not be verified",
        "The supporting evidence didn't hold up on a second check, so the call "
        "was dropped rather than kept unverified. A human can check the cited "
        "page.",
    ),
    "delta_below_materiality": (
        "Change too small to call",
        "The forecast change behind this call was too small to justify a view, "
        "so it was left out. No action needed.",
    ),
    "checker_sign_mismatch": (
        "Evidence points the other way",
        "A second reader found the cited text doesn't support the direction of "
        "this call, so it was dropped. A human can check the cited page.",
    ),
    "checker_not_forward_looking": (
        "Not a forward-looking view",
        "A second reader found the cited text isn't a forward-looking view "
        "(e.g. it describes the past), so it was dropped. A human can check the "
        "cited page.",
    ),
    "checker_asset_mismatch": (
        "Evidence is about a different asset",
        "A second reader found the cited text is about a different asset than "
        "the call, so it was dropped. A human can check the cited page.",
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
    # Set when a same-leaf implied call challenged the kept stated call
    # (deterministic stated-beats-implied resolution). Rendered on the kept row
    # and forces a review flag so a human sees the recommendation.
    challenge_note: str = ""


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
) -> AssemblyResult:
    """page_counts maps source_id -> PDF page count (absent for HTML sources).

    scrambled_pages maps source_id -> the set of column-interleaved page numbers
    (see src/ingest.detect_scrambled_page); a prose call citing one of them uses
    the degraded key-token check, capping confidence and forcing review.
    ocr_pages maps source_id -> the set of image-only / low-text page numbers;
    a prose call citing one gets the same degraded cap and review flag, with an
    OCR-specific evidence message.
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
        source_ocr = frozenset((ocr_pages or {}).get(candidate.source_id, ()))
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
        challenge_note = ""
        if len(views) > 1:
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
                challenge_note=challenge_note,
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
            challenge_note=entry.challenge_note,
        )
        for entry in survivors
    ]

    return AssemblyResult(
        output_rows=output_rows,
        failures=failures,
        candidate_count=len(candidates),
    )


def _resolve_stated_vs_implied(
    group: list[ConfidenceResult],
) -> tuple[ConfidenceResult, list[tuple[ConfidenceResult, str, str]], str] | None:
    """Deterministic stated-beats-implied resolution for one conflicting
    (source/group, leaf) — the client's rule (ROADMAP decision 5).

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
    reasoning as a recommendation — the deliberate hook the v1.2 confidence-based
    override path will build on (a high-confidence implied call overriding a
    low-confidence stated view); v1 records the recommendation, nothing more.
    """
    stated = [item for item in group if item.candidate.basis == "stated"]
    inferred = [item for item in group if item.candidate.basis == "inferred"]
    if not stated or not inferred:
        return None
    if len(stated) + len(inferred) != len(group):
        return None  # a third basis (e.g. forecast_delta) is present — use the arbiter
    stated_views = {item.candidate.view for item in stated}
    if len(stated_views) != 1:
        return None  # the stated side disagrees with itself — that is arbiter work
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
    sources: dict[str, SourceInfo] | None = None,
    source_summaries: list[dict[str, object]] | None = None,
    chunk_failures: list[FailureRecord] | None = None,
    run_config: dict[str, object] | None = None,
    grouping: dict[str, object] | None = None,
) -> None:
    """Write the run's review files.

    chunk_failures are whole-chunk failures (e.g. unparseable model output) that
    produced no candidate; they are recorded in failures.csv and counted
    separately in the manifest so the candidate reconciliation stays exact.
    run_config (engine/model/effort) is recorded in the manifest so a frozen
    run states exactly what produced it. grouping is the resolved group-notes
    plan (groups + warnings) so the manifest shows exactly what was combined.
    sources maps source_id -> SourceInfo so the client-readable failures file can
    show each firm/source title (absent -> the source_id is shown instead).

    Alongside `failures.csv` (unchanged, internal) a `failures-client.csv` is
    written with the same rows in the same order but plain labels/explanations.
    """
    chunk_failures = chunk_failures or []
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    all_failures = [*result.failures, *chunk_failures]
    _write_csv(run_dir / "output.csv", OUTPUT_COLUMNS, result.output_rows)
    _write_csv(
        run_dir / "failures.csv",
        FAILURE_COLUMNS,
        [failure.to_row() for failure in all_failures],
    )
    _write_csv(
        run_dir / "failures-client.csv",
        CLIENT_FAILURE_COLUMNS,
        [_client_failure_row(failure, sources or {}) for failure in all_failures],
    )
    manifest = _manifest_text(result, source_summaries or [], chunk_failures, run_config, grouping)
    (run_dir / "manifest.md").write_text(manifest, encoding="utf-8")


def _client_failure_row(
    failure: FailureRecord, sources: dict[str, SourceInfo]
) -> dict[str, str]:
    """One failures-client.csv row: firm/source titles resolved from `sources`,
    a plain What-happened label and Explanation, and the row's own message (or
    reasoning) as human-readable evidence — no internal codes."""
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
    corroboration: str = "",
    sibling_note: str = "",
    challenge_note: str = "",
) -> dict[str, str]:
    candidate = scored.candidate
    lookup = taxonomy.output_fields_for(candidate.sub_asset_class)
    commentary = _commentary(candidate, locator_source=locator_source)
    if corroboration:
        commentary += f" {corroboration}"
    if scored.evidence_check.degraded:
        detail = scored.evidence_check.message or (
            "cited page used degraded evidence verification "
            "(confidence capped, review required)"
        )
        commentary += f" Evidence check: {detail}."
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
    if challenge_note:
        commentary += f" {challenge_note}"
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
            ocr_pages = summary.get("ocr_pages") or []
            if ocr_pages:
                flag_text += f" [OCR pages: {', '.join(f'p.{n}' for n in ocr_pages)}]"
            if summary.get("ocr_note"):
                flag_text += f" [OCR note: {summary.get('ocr_note')}]"
            pages = summary.get("page_count")
            chunk_count = summary.get("chunk_count", 0)
            size = f"{pages}p / {chunk_count} chunks" if pages else f"{chunk_count} chunks"
            lines.append(
                f"- {summary.get('source_id')} ({summary.get('source_type')}, {size}): "
                f"{summary.get('candidates', 0)} candidates emitted{flag_text}"
            )
        lines.append("")
    return "\n".join(lines)
